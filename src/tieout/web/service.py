"""Analysis service: turns the full tieout pipeline into JSON for the web UI.

Reuses every layer we built (ingest -> extract -> engine -> attribution) and
serialises the result. Results are cached to .cache/web/{ticker}.json so the UI
is instant; `force=True` recomputes live (EDGAR + Claude).
"""

from __future__ import annotations

import json
import os
from decimal import Decimal
from pathlib import Path

from ..attribution import attribute_run
from ..constraints import Status, is_aggregable
from ..engine import CheckerEngine, PropagatingEngine
from ..engine.propagating import _expr_str
from ..extract import (BaselineExtractor, CachedModel, LlmTextExtractor,
                       ResponseCache, XbrlDirectExtractor, claude_model)
from ..facts import Fact, FactStore, Source
from ..ingest import EdgarClient
from ..ingest.text import edgar_text_provider
from ..ingest.xbrl import periods_in
from ..registry import REGISTRY
from ..report import build_scorecard

FILINGS = [
    {"ticker": "COST", "issuer": "Costco Wholesale", "note": "clean baseline"},
    {"ticker": "AMZN", "issuer": "Amazon", "note": "multi-segment + equity-method"},
    {"ticker": "KHC", "issuer": "Kraft Heinz", "note": "mezzanine equity"},
]
_DESC = {t.template_id: t for t in REGISTRY}
# Committed so a fresh clone serves the UI instantly, offline, with no API key.
# "Run live" overwrites these when a key + network are available.
_CACHE_DIR = Path("data/web")


def load_api_key() -> bool:
    """Best-effort: load ANTHROPIC_API_KEY from .env or the out-of-repo
    credentials file so live runs work without manual env setup. Runs on import
    so it applies no matter how the server is started. Returns True if a key is set."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return True
    home = Path(os.path.expanduser("~"))
    candidates = [
        Path(".env"),
        Path(os.environ.get("LOCALAPPDATA", "")) / "tieout" / "credentials.env",
        home / "AppData" / "Local" / "tieout" / "credentials.env",
        home / ".tieout" / "credentials.env",
    ]
    for p in candidates:
        try:
            if str(p) and p.exists():
                for line in p.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    if line.lower().startswith("export "):
                        line = line[7:]
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
        except Exception:
            pass
        if os.environ.get("ANTHROPIC_API_KEY"):
            return True
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


load_api_key()


def _f(x) -> float | None:
    return None if x is None else float(x)


def _band(gt: Decimal) -> Decimal:
    return max(abs(gt) * Decimal("0.002"), Decimal("1000000"))


def registry_json() -> list[dict]:
    out = []
    for t in REGISTRY:
        out.append({
            "id": t.template_id,
            "kind": t.kind.value,
            "description": t.description,
            "severity": t.severity.value,
            "formula": f"{t.target.concept} = {_expr_str(t.expr)}",
            "tolerance": {"abs": _f(t.tolerance.abs), "rel": _f(t.tolerance.rel),
                          "rel_ground_truth": _f(t.tolerance.rel_ground_truth)},
        })
    return out


def _store(facts) -> FactStore:
    s = FactStore(); s.add_all(facts); return s


def _constraint_rows(gt_results, claude_results, fy) -> list[dict]:
    gt = {r.template_id: r for r in gt_results if r.period.fiscal_year == fy}
    cl = {r.template_id: r for r in claude_results if r.period.fiscal_year == fy}
    rows = []
    for t in REGISTRY:
        g, c = gt.get(t.template_id), cl.get(t.template_id)
        rows.append({
            "id": t.template_id,
            "description": t.description,
            "severity": t.severity.value,
            "formula": f"{t.target.concept} = {_expr_str(t.expr)}",
            "gt_status": g.status.value if g else "n/a",
            "gt_residual": _f(g.residual) if g else None,
            "gt_band": _f(g.band) if g else None,
            "claude_status": c.status.value if c else "n/a",
            "claude_residual": _f(c.residual) if c else None,
            "claude_band": _f(c.band) if c else None,
        })
    return rows


def _fact_comparison(claude_store, gt, fy) -> list[dict]:
    out = []
    for f in claude_store.all_facts():
        if f.period.fiscal_year != fy or f.dimensions or f.unit != "USD":
            continue
        g = gt.query(f.concept, f.period, dimensions={}, source=Source.XBRL)
        if not g:
            out.append({"concept": f.concept, "text": _f(f.value),
                        "xbrl": None, "agree": None})
            continue
        gv = g[0].value
        out.append({"concept": f.concept, "text": _f(f.value), "xbrl": _f(gv),
                    "agree": abs(f.value - gv) <= _band(gv)})
    return sorted(out, key=lambda r: r["concept"])


def _propagation(gt, periods, fy) -> list[dict]:
    eng = PropagatingEngine(REGISTRY)
    eng.run(gt, periods)
    out = []
    for f in eng.derived_facts:
        if f.period.fiscal_year != fy:
            continue
        out.append({"concept": f.concept, "value": _f(f.value),
                    "unit": f.unit, "op": f.provenance.op,
                    "inputs": len(f.provenance.input_fact_ids)})
    return out


def _localization(gt_facts, periods, fy) -> dict | None:
    seg = [f for f in gt_facts if f.concept == "revenue.segment"
           and f.period.fiscal_year == fy]
    if not seg:
        return None
    victim = max(seg, key=lambda f: f.value)
    corrupted = Fact(victim.concept, victim.value + Decimal("5000000000"),
                     victim.period, Source.XBRL, victim.provenance,
                     decimals=victim.decimals, dimensions=victim.dimensions)
    store2 = _store([f for f in gt_facts if f.fact_id != victim.fact_id])
    store2.add(corrupted)
    eng = PropagatingEngine(REGISTRY)
    results = eng.run(store2, periods)
    r = next((x for x in results if x.template_id == "rev.segments_sum"
              and x.period.fiscal_year == fy), None)
    if r is None or r.status is not Status.VIOLATED:
        return None
    suspects = []
    for fid, score in eng.localizations.get(r.inst_id, []):
        f = eng.store.get(fid)
        suspects.append({"concept": f.concept,
                         "segment": dict(f.dimensions).get("segment", "(total)"),
                         "value": _f(f.value), "score": score})
    return {"injected_segment": dict(victim.dimensions).get("segment", "?"),
            "injected_amount": 5_000_000_000,
            "residual": _f(r.residual), "suspects": suspects}


def _segments(gt, periods, fy) -> dict | None:
    per = next((p for p in periods if p.fiscal_year == fy
                and p.type.value == "duration"), None)
    if per is None:
        return None
    seg = [f for f in gt.query("revenue.segment", per, source=Source.XBRL)
           if is_aggregable(f.dims_dict(), "segment")]
    if not seg:
        return None
    total_f = gt.query("revenue.total", per, dimensions={}, source=Source.XBRL)
    total = _f(total_f[0].value) if total_f else None
    # Some filers tag a segment at several granularities (segment, segment x
    # category, duplicates across contexts). Keep one value per segment — the
    # largest, which is the segment total rather than a sub-row.
    best: dict[str, float] = {}
    for f in seg:
        name = dict(f.dimensions).get("segment", "?").replace("Member", "")
        v = _f(f.value)
        if name not in best or v > best[name]:
            best[name] = v
    members = sorted(({"name": k, "value": v} for k, v in best.items()),
                     key=lambda m: -m["value"])
    ssum = sum(m["value"] for m in members)
    return {"members": members, "total": total, "sum": ssum,
            "reconciles": total is not None
            and abs(ssum - total) <= max(abs(total) * 0.005, 1e6)}


def _bn(v) -> str:
    if v is None:
        return "n/a"
    a = abs(v)
    if a >= 1e9:
        return f"${v/1e9:.1f}B"
    if a >= 1e6:
        return f"${v/1e6:.1f}M"
    return f"${v:,.0f}"


def _narrative(issuer, fy, snap) -> str | None:
    """A 2-3 sentence plain-English brief from the verified numbers (1 cached
    Claude call). Returns None with no API key, so it degrades gracefully."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    lines = [f"{issuer} — fiscal year {fy}. Figures (verified against SEC XBRL):"]
    for name, pts in snap["series"].items():
        lines.append(f"  {name}: " + ", ".join(f"FY{p['year']} {_bn(p['value'])}" for p in pts))
    m = snap["margins"]
    lines.append("  margins: " + ", ".join(
        f"{k} {v*100:.1f}%" for k, v in m.items() if v is not None))
    if snap["notable"]:
        lines.append("  notable: " + "; ".join(snap["notable"]))
    prompt = ("You are a financial analyst writing for a non-expert. In 2-3 plain "
              "sentences, summarize this company's fiscal year: overall performance, "
              "the profitability trend, and any notable change. Be factual and "
              "concise, use the numbers given, and do not invent anything.\n\n"
              + "\n".join(lines))
    try:
        cm = CachedModel(claude_model("claude-opus-4-8"), ResponseCache(".cache/llm"),
                         prompt_version="narrative-v1", adapter_version="narrative/0")
        text, _, _ = cm.complete(prompt)
        return text.strip()
    except Exception:
        return None


def _snapshot(gt, fy, issuer) -> dict:
    facts = [f for f in gt.all_facts() if f.source is Source.XBRL and not f.dimensions]
    by = {(f.concept, f.period.fiscal_year): _f(f.value) for f in facts}
    years = sorted({yr for (c, yr) in by if c == "revenue.total"})[-3:]

    def g(concept, year):
        return by.get((concept, year))

    series = {}
    for name, concept in [("revenue", "revenue.total"), ("net income", "net_income.parent"),
                          ("operating income", "operating_income.total"),
                          ("total assets", "assets.total")]:
        pts = [{"year": y, "value": g(concept, y)} for y in years if g(concept, y) is not None]
        if len(pts) >= 2:
            series[name] = pts

    rev = g("revenue.total", fy)
    gp = g("gross_profit.total", fy)
    if gp is None and rev is not None and g("cogs.total", fy) is not None:
        gp = rev - g("cogs.total", fy)

    def ratio(a, b):
        return (a / b) if (a is not None and b not in (None, 0)) else None

    margins = {
        "gross": ratio(gp, rev),
        "operating": ratio(g("operating_income.total", fy), rev),
        "net": ratio(g("net_income.parent", fy), rev),
        "tax": ratio(g("income_tax.total", fy), g("pretax_income.total", fy)),
    }

    def yoy(c):
        return ratio(_sub(g(c, fy), g(c, fy - 1)), g(c, fy - 1))

    kpis = [{"label": lbl, "value": g(c, fy), "yoy": yoy(c)} for lbl, c in
            [("Revenue", "revenue.total"), ("Net income", "net_income.parent"),
             ("Total assets", "assets.total"), ("Operating cash flow", "cfo.total")]]

    notable = []
    ry = yoy("revenue.total")
    if ry is not None:
        notable.append(f"Revenue {'grew' if ry >= 0 else 'fell'} {abs(ry)*100:.0f}% "
                       f"year-over-year to {_bn(rev)}")
    ni = yoy("net_income.parent")
    if ni is not None:
        notable.append(f"Net income {'rose' if ni >= 0 else 'declined'} {abs(ni)*100:.0f}% YoY")
    if margins["operating"] is not None:
        notable.append(f"Operating margin {margins['operating']*100:.1f}%")
    n_seg = len({dict(f.dimensions).get("segment") for f in gt.all_facts()
                 if f.concept == "revenue.segment"})
    if n_seg:
        notable.append(f"{n_seg} reportable segment(s)")
    if any(c == "income.discontinued" for (c, _) in by):
        notable.append("Reports discontinued operations (often a divestiture or spin-off)")
    if any(c == "equity.temporary" for (c, _) in by):
        notable.append("Carries redeemable / mezzanine equity on the balance sheet")

    snap = {"fy": fy, "years": years, "series": series, "margins": margins,
            "kpis": kpis, "notable": notable}
    snap["narrative"] = _narrative(issuer, fy, snap)
    return snap


def _sub(a, b):
    return (a - b) if (a is not None and b is not None) else None


def _scorecard_json(sc) -> dict:
    return {"name": sc.name, "facts": sc.extracted, "agree": sc.agree,
            "disagree": sc.disagree, "satisfied": sc.satisfied,
            "violated": sc.violated, "soft": sc.soft_violated,
            "indeterminate": sc.indeterminate, "judge_invisible": sc.judge_invisible,
            "attributions": [{"id": a.template_id, "label": a.label.value,
                              "evidence": a.evidence} for a in sc.attributions]}


def analyze(ticker: str, *, force: bool = False) -> dict:
    ticker = ticker.upper()
    cache = _CACHE_DIR / f"{ticker}.json"
    if cache.exists() and not force:
        return json.loads(cache.read_text(encoding="utf-8"))

    client = EdgarClient()
    filing = client.find_10k(ticker)
    tp = edgar_text_provider(client)

    gt_facts = XbrlDirectExtractor().extract(filing)
    gt = _store(gt_facts)
    periods = periods_in(gt_facts)
    fy = max(p.fiscal_year for p in periods)

    claude_facts = LlmTextExtractor(claude_model("claude-opus-4-8"),
                                    ResponseCache(".cache/llm"),
                                    text_provider=tp).extract(filing)
    claude_store = _store(claude_facts)
    baseline_store = _store(BaselineExtractor(tp).extract(filing))

    scorecards = [
        _scorecard_json(build_scorecard("Claude (text)", claude_store, gt,
                                        REGISTRY, periods, fy)),
        _scorecard_json(build_scorecard("Baseline (regex)", baseline_store, gt,
                                        REGISTRY, periods, fy)),
    ]
    gt_results = CheckerEngine(REGISTRY, source=Source.XBRL).run(gt, periods)
    claude_results = CheckerEngine(REGISTRY).run(claude_store, periods)

    result = {
        "ticker": ticker,
        "issuer": filing.issuer,
        "fiscal_year": fy,
        "filing_date": filing.filing_date,
        "url": filing.url,
        "scorecards": scorecards,
        "constraints": _constraint_rows(gt_results, claude_results, fy),
        "facts": _fact_comparison(claude_store, gt, fy),
        "propagation": _propagation(gt, periods, fy),
        "segments": _segments(gt, periods, fy),
        "snapshot": _snapshot(gt, fy, filing.issuer),
        "attribution": [{"id": a.template_id, "label": a.label.value,
                         "evidence": a.evidence}
                        for a in attribute_run(REGISTRY, periods, claude_store, gt)
                        if a.period.fiscal_year == fy],
        "localization": _localization(gt_facts, periods, fy),
    }
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


_agent_stores: dict = {}


def _agent_store(ticker: str):
    t = ticker.upper()
    if t not in _agent_stores:
        filing = EdgarClient().find_10k(t)
        s = FactStore(); s.add_all(XbrlDirectExtractor().extract(filing))
        fy = max(p.fiscal_year for p in periods_in(s.all_facts()))
        _agent_stores[t] = (s, fy, filing.issuer)
    return _agent_stores[t]


def ask(ticker: str, question: str) -> dict:
    """Fill one Matrix cell live over a filing, then verify it against the
    accounting-identity engine (no LLM judge involved)."""
    from ..agent import ColumnAgent, verify_cell
    store, fy, issuer = _agent_store(ticker)
    cell = ColumnAgent(store, model_id="claude-opus-4-8",
                       cache=ResponseCache(".cache/llm")).fill(question, fy)
    v = verify_cell(cell, store)
    return {
        "issuer": issuer, "fiscal_year": fy, "question": question,
        "answer": cell.answer, "value": cell.value, "unit": cell.unit,
        "answer_concept": cell.answer_concept,
        "derivation": cell.derivation, "numbers_used": cell.numbers_used,
        "tool_calls": len(cell.tool_calls), "error": cell.error,
        "verdict": {
            "trusted": v.trusted, "retrieval_ok": v.retrieval_ok,
            "calc_status": v.calc_status, "derived_value": v.derived_value,
            "checks": [{"label": c.label, "concept": c.concept, "stated": c.stated,
                        "truth": c.truth, "ok": c.ok, "note": c.note} for c in v.checks],
            "identities": [{"id": ic.template_id, "description": ic.description,
                            "status": ic.status, "residual": ic.residual,
                            "label": ic.label, "evidence": ic.evidence}
                           for ic in v.identities],
        },
    }


def benchmark() -> dict:
    p = Path("data/bench/results.json")
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"results": {}}


def grid() -> dict:
    """The gated Query Grid (documents x questions), precomputed to data/web/grid.json."""
    p = _CACHE_DIR / "grid.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"rows": [], "columns": [], "cells": {}, "metrics": {}, "gold": {}}


def grid_run() -> dict:
    """Recompute the grid live (needs an API key) and cache it."""
    from ..evals import run_grid
    res = run_grid()
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (_CACHE_DIR / "grid.json").write_text(json.dumps(res, indent=2), encoding="utf-8")
    return res


def eval_metrics() -> dict:
    return grid().get("metrics", {})


# --- Quality-of-Earnings workflow + connectors ---------------------------------

def qoe(ticker: str) -> dict:
    p = _CACHE_DIR / f"qoe_{ticker.upper()}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def qoe_companies() -> list[dict]:
    out = []
    for p in sorted(_CACHE_DIR.glob("qoe_*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        out.append({"ticker": d.get("ticker"), "issuer": d.get("issuer"),
                    "fiscal_year": d.get("fiscal_year"), "tied_out": d.get("tied_out")})
    return out


def qoe_run(ticker: str) -> dict:
    """Live recompute: drain the QoE workflow generator into the report dict + cache it.
    Shares one definition with the live stream (`/api/stream/qoe`)."""
    from ..workflows.qoe import materialize_qoe
    out = materialize_qoe(ticker)
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (_CACHE_DIR / f"qoe_{ticker.upper()}.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    return out


# --- user-controlled connectors + the progressive analysis catalog ------------
# Connection state is per-process, per-ticker (resets on restart — a session, not a DB).
_CONNECTIONS: dict[str, set] = {}
_LEDGER_CACHE: dict[str, tuple] = {}

_SOURCE_DEFS = [
    {"id": "edgar", "name": "SEC EDGAR", "vendor": "Public filings", "kind": "public",
     "security": "Public XBRL — no PII; the ground-truth marginals.",
     "provides": "10-K/Q XBRL marginals", "unlocks": []},
    {"id": "erp", "name": "NetSuite", "vendor": "ERP", "kind": "erp",
     "security": "Most sensitive financial system — connect in a clean room.",
     "provides": "GL · revenue by customer/SKU · AR aging · inventory",
     "unlocks": ["concentration", "revenue_analysis", "net_profit", "annual_cash_flow", "burn_rate", "roi_by_segment"]},
    {"id": "crm", "name": "Salesforce", "vendor": "CRM", "kind": "crm",
     "security": "Customer-identifying — governed by your DPA.",
     "provides": "pipeline · bookings · cohort retention",
     "unlocks": ["retention", "pipeline_to_revenue"]},
    {"id": "hris", "name": "Workday", "vendor": "HRIS", "kind": "hris",
     "security": "Employee PII — highest sensitivity; connect last.",
     "provides": "headcount · org · efficiency", "unlocks": ["headcount"]},
    {"id": "investor", "name": "13F holdings", "vendor": "Capital markets", "kind": "investor",
     "security": "Ownership data — commercially sensitive.",
     "provides": "institutional + hedge-fund ownership", "unlocks": ["investor_analysis"]},
    {"id": "merge", "name": "Merge.dev", "vendor": "Live ERP/CRM (production)", "kind": "live",
     "security": "Swap in real production data via one connector — same interface.",
     "provides": "real ERP/CRM in production", "unlocks": []},
]
_TOGGLEABLE = {"erp", "crm", "hris", "investor"}


def _connected(ticker: str) -> set:
    return _CONNECTIONS.setdefault(ticker.upper(), set())


def _ledger_for(ticker: str):
    """Cache (ledger, constraints) per ticker — reuses the EDGAR pull from qoe."""
    t = ticker.upper()
    if t not in _LEDGER_CACHE:
        from ..workflows.qoe import _CONS_CACHE
        from ..synth.constraints import pull_constraints
        from ..synth.generate import generate
        cons = _CONS_CACHE.get(t) or pull_constraints(t)
        _CONS_CACHE[t] = cons
        _LEDGER_CACHE[t] = (generate(cons), cons)
    return _LEDGER_CACHE[t]


def sources(ticker: str) -> list[dict]:
    """Connector cards reflecting the user's current connections (EDGAR always on)."""
    t = ticker.upper()
    conn = _connected(t)
    d = qoe(ticker)
    counts = {"edgar": f"{len(d.get('constraints', []))} periods of XBRL"}
    if conn:
        try:
            ledger, _ = _ledger_for(t)
            counts.update({
                "erp": f"{len(ledger.customers)} customers · {len(ledger.revenue_lines)} lines · {len(ledger.ar_invoices)} AR invoices",
                "crm": f"{len(ledger.pipeline)} open opps · {len(ledger.cohorts)} cohorts",
                "hris": "headcount by segment", "investor": "institutional + hedge-fund holders"})
        except Exception:
            pass
    out = []
    for s in _SOURCE_DEFS:
        on = (s["id"] == "edgar") or (s["id"] in conn)
        out.append({**{k: s[k] for k in ("id", "name", "vendor", "kind", "security", "provides", "unlocks")},
                    "status": "connected" if on else ("locked" if s["id"] == "edgar" else "available"),
                    "toggleable": s["id"] in _TOGGLEABLE,
                    "tied_out": (True if (on and s["id"] not in ("edgar", "merge")) else None),
                    "detail": counts.get(s["id"]) if on else None})
    return out


def toggle_source(ticker: str, source: str, connect: bool) -> dict:
    t, source = ticker.upper(), source.lower()
    if source not in _TOGGLEABLE:
        raise ValueError("only erp / crm / hris / investor can be connected")
    from ..workflows.catalog import catalog
    conn = _connected(t)
    before = {a["id"] for a in catalog(conn) if a["unlocked"]}
    if connect:
        conn.add(source); _ledger_for(t)
    else:
        conn.discard(source)
    after = {a["id"] for a in catalog(conn) if a["unlocked"]}
    return {"sources": sources(t), "connected": sorted(conn),
            "unlocked": sorted(after - before)}


def analyses(ticker: str) -> dict:
    from ..workflows.catalog import catalog
    conn = _connected(ticker)
    return {"ticker": ticker.upper(), "connected": sorted(conn), "catalog": catalog(conn)}


def run_analysis(ticker: str, analysis_id: str) -> dict:
    from ..workflows.catalog import ANALYSES, run_analysis as _run
    a = ANALYSES.get(analysis_id)
    if a is None:
        raise KeyError(f"unknown analysis {analysis_id!r}")
    if not set(a.requires) <= ({"edgar"} | _connected(ticker)):
        raise PermissionError(f"connect {', '.join(a.requires)} to run {analysis_id}")
    ledger, cons = _ledger_for(ticker)
    return _run(cons, ledger, analysis_id)


_search_client: EdgarClient | None = None


def search_companies(query: str, limit: int = 8) -> list[dict]:
    global _search_client
    if _search_client is None:
        _search_client = EdgarClient()
    return _search_client.search(query, limit)


def filings_index() -> list[dict]:
    out = []
    for f in FILINGS:
        out.append({**f, "cached": (_CACHE_DIR / f"{f['ticker']}.json").exists()})
    return out
