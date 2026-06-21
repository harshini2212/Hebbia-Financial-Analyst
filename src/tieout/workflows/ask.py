"""Ask-anything agent — the freeform AI query system.

A Claude tool-use agent answers any question about the active company using grounded
tools over BOTH its real XBRL filings and the calibrated synthetic ERP/CRM ledger. It
streams over the same SSE spine: `step` per tool call (the work shows live), `token`
for the answer text as it's generated, `citation` for each source it pulls, and a
`tie_out` badge proving the figures reconcile. Tools are gated by which connectors are
"connected", so the agent can only use data the user has actually wired up.
"""

from __future__ import annotations

import time
import uuid

from ..synth import build_workspace
from .qoe import _CONS_CACHE, run_qoe  # reuse the per-process EDGAR cache
from ..synth.generate import generate

ASK_MODEL = "claude-sonnet-4-6"
_MAX_TURNS = 6

# tool -> the connector that must be connected to expose it
_TOOL_SOURCE = {
    "get_financials": "edgar", "get_segments": "edgar", "get_working_capital": "edgar",
    "get_growth_quality": "erp", "get_customer_concentration": "erp",
    "get_retention_and_pipeline": "crm",
}

_SCHEMAS = {
    "get_financials": {
        "name": "get_financials",
        "description": "Reported financials from the company's 10-K XBRL for a fiscal year: "
                       "revenue, net_income, operating_income, gross_profit, cogs, cfo "
                       "(operating cash flow), capex, free_cash_flow, accounts_receivable, "
                       "inventory, accounts_payable, gross_margin, operating_margin, net_margin.",
        "input_schema": {"type": "object", "properties": {
            "fiscal_year": {"type": "integer"}}, "required": ["fiscal_year"]},
    },
    "get_segments": {
        "name": "get_segments",
        "description": "Reported segment revenue (10-K segment disclosure) for a fiscal year.",
        "input_schema": {"type": "object", "properties": {
            "fiscal_year": {"type": "integer"}}, "required": ["fiscal_year"]},
    },
    "get_working_capital": {
        "name": "get_working_capital",
        "description": "Working-capital metrics derived from the balance sheet: DSO, DIO, DPO, "
                       "and the year-over-year change, for a fiscal year.",
        "input_schema": {"type": "object", "properties": {
            "fiscal_year": {"type": "integer"}}, "required": ["fiscal_year"]},
    },
    "get_customer_concentration": {
        "name": "get_customer_concentration",
        "description": "From the ERP ledger: the top customers, each customer's share of "
                       "revenue, and the top-5 concentration. (Calibrated demo data.)",
        "input_schema": {"type": "object", "properties": {}},
    },
    "get_growth_quality": {
        "name": "get_growth_quality",
        "description": "Reported revenue growth vs underlying growth excluding the largest "
                       "customer, plus one-time vs recurring revenue, from the ERP ledger.",
        "input_schema": {"type": "object", "properties": {}},
    },
    "get_retention_and_pipeline": {
        "name": "get_retention_and_pipeline",
        "description": "From the CRM: net revenue retention and the win-rate-weighted pipeline "
                       "coverage of next year's growth need.",
        "input_schema": {"type": "object", "properties": {}},
    },
}

_SYSTEM = """You are a buy-side financial analyst answering a question about {issuer} ({ticker}).
Use the tools to ground EVERY figure — never invent numbers. Public figures come from the
company's 10-K XBRL; granular figures come from a *calibrated synthetic* ERP/CRM ledger that
ties out to those filings (say so when you use it — it is demo data, not real customer data).
Be concise and concrete: lead with the answer, cite the source of each number inline (e.g.
"(10-K XBRL)" or "(ERP ledger, synthetic)"), and surface the non-obvious insight a diligence
analyst would care about. Today only these sources are connected: {connected}."""


def _money(v):
    if v is None:
        return None
    return round(v)


def _financials(cons, fy):
    p = cons.by_year().get(fy) or cons.periods[0]
    return {"fiscal_year": p.fiscal_year, "source": f"FY{p.fiscal_year} 10-K XBRL",
            "revenue": _money(p.revenue_total), "net_income": _money(p.net_income),
            "operating_income": _money(p.operating_income), "cogs": _money(p.cogs),
            "cfo": _money(p.cfo), "capex": _money(p.capex),
            "free_cash_flow": _money(p.free_cash_flow()),
            "accounts_receivable": _money(p.accounts_receivable),
            "inventory": _money(p.inventory), "accounts_payable": _money(p.accounts_payable),
            "gross_margin": round(p.gross_margin, 4) if p.gross_margin else None,
            "operating_margin": round(p.operating_margin(), 4) if p.operating_margin() else None,
            "net_margin": round(p.net_margin(), 4) if p.net_margin() else None}


def _run_tool(name, args, cons, ledger, report):
    fy = args.get("fiscal_year", cons.periods[0].fiscal_year)
    if name == "get_financials":
        return _financials(cons, fy)
    if name == "get_segments":
        p = cons.by_year().get(fy) or cons.periods[0]
        return {"fiscal_year": p.fiscal_year, "source": f"FY{p.fiscal_year} 10-K segment disclosure",
                "segments": {k: _money(v) for k, v in p.segments.items()}}
    if name == "get_working_capital":
        return {"source": "balance sheet (XBRL)", **report["working_capital"]}
    if name == "get_customer_concentration":
        return {"source": "ERP ledger (synthetic, tied out to XBRL)",
                "top5_concentration": report["top5_concentration"],
                "largest_customer": report["largest_customer"],
                "top_customers": report["top_customers"][:6]}
    if name == "get_growth_quality":
        return {"source": "ERP ledger (synthetic)",
                "reported_growth": report["reported_growth"],
                "underlying_growth_ex_largest": report["underlying_growth"],
                "one_time_revenue": report["one_time_revenue"],
                "one_time_pct": report["one_time_pct"]}
    if name == "get_retention_and_pipeline":
        return {"source": "CRM (synthetic)", "net_retention": report["net_retention"],
                "pipeline": report["pipeline"]}
    return {"error": f"unknown tool {name}"}


def ask_events(ticker: str, question: str, connectors=None):
    """Answer `question` about `ticker`, streaming (event, payload) tuples."""
    ticker = ticker.upper()
    connected = set(connectors or {"edgar", "erp", "crm"})
    run_id = "a_" + uuid.uuid4().hex[:6]
    t0 = time.time()
    yield "run_started", {"run_id": run_id, "workflow": "ask", "company": ticker, "question": question}
    try:
        yield "step", {"id": "load", "label": "Load company filings + ledger", "status": "running"}
        # reuse cached constraints; (re)build the ledger + report (fast, deterministic)
        from ..synth.constraints import pull_constraints
        cons = _CONS_CACHE.get(ticker) or pull_constraints(ticker)
        _CONS_CACHE[ticker] = cons
        ledger = generate(cons)
        import dataclasses
        from .qoe import _report_dict
        rep_obj = run_qoe(cons, ledger)
        report = _report_dict(cons, ledger, rep_obj)
        yield "step", {"id": "load", "label": f"Loaded {cons.issuer} ({len(cons.periods)} periods) + ledger",
                       "status": "done"}

        tools = [_SCHEMAS[t] for t, src in _TOOL_SOURCE.items() if src in connected and t in _SCHEMAS]
        from anthropic import Anthropic
        client = Anthropic()
        system = _SYSTEM.format(issuer=cons.issuer, ticker=ticker,
                                connected=", ".join(sorted(connected)))
        messages = [{"role": "user", "content": question}]
        tie_seen = False

        for _ in range(_MAX_TURNS):
            with client.messages.stream(model=ASK_MODEL, max_tokens=900, system=system,
                                        tools=tools, messages=messages) as stream:
                for ev in stream:
                    if (ev.type == "content_block_delta"
                            and getattr(ev.delta, "type", "") == "text_delta"):
                        yield "token", {"text": ev.delta.text}
                final = stream.get_final_message()
            messages.append({"role": "assistant", "content": final.content})
            tool_uses = [b for b in final.content if getattr(b, "type", "") == "tool_use"]
            if not tool_uses:
                break
            results = []
            for tu in tool_uses:
                yield "step", {"id": tu.id, "label": f"tool · {tu.name}({tu.input or ''})",
                               "status": "running"}
                out = _run_tool(tu.name, tu.input or {}, cons, ledger, report)
                src = out.get("source")
                if src:
                    yield "citation", {"tool": tu.name, "source": src}
                if not tie_seen and report.get("tied_out"):
                    yield "tie_out", {"check": "ledger ↔ XBRL reconciliation",
                                      "value": None, "passed": True}
                    tie_seen = True
                yield "step", {"id": tu.id, "label": f"tool · {tu.name}", "status": "done"}
                import json as _json
                results.append({"type": "tool_result", "tool_use_id": tu.id,
                                "content": _json.dumps(out, default=str)})
            messages.append({"role": "user", "content": results})

        yield "done", {"run_id": run_id, "elapsed_ms": int((time.time() - t0) * 1000)}
    except Exception as exc:
        yield "failed", {"message": str(exc)}


# A deck of example questions so a user knows what they can ask, grouped by lens.
QUESTION_DECK = [
    {"group": "Revenue & growth", "qs": [
        "How fast is revenue growing, and is the growth high quality?",
        "How much of revenue is recurring vs one-time?"]},
    {"group": "Concentration / QoE", "qs": [
        "How concentrated is revenue — and is it getting worse?",
        "Ex the largest customer, what is underlying growth?"]},
    {"group": "Margins & cash", "qs": [
        "What are the margins and how much free cash flow does it generate?",
        "Is capex eating the operating cash flow?"]},
    {"group": "Working capital", "qs": [
        "Is DSO deteriorating — are collections slowing?",
        "How does the cash conversion cycle look?"]},
    {"group": "Retention / pipeline", "qs": [
        "What's net revenue retention, and does the pipeline cover next year's growth?"]},
]
