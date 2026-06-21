"""The analysis catalog — deterministic analyses unlocked by connected sources.

Each analysis is a pure function over the company's real XBRL constraints + the
calibrated synthetic ledger (+ headcount/holdings when those connectors are on). EDGAR
is always available; ERP/CRM/HRIS/investor each unlock more. No LLM calls here — these
are exact computations, so they're free and reproducible. The one formula rule:
`unit_margin` is the gross-margin fraction, so segment gross profit = Σ amount·unit_margin.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from ..synth.constraints import CompanyConstraints
from ..synth.generate import SyntheticLedger
from ..synth.hris import synth_headcount
from ..synth.holdings import synth_holdings, holdings_summary
from .qoe import run_qoe


@dataclass
class Analysis:
    id: str
    name: str
    requires: list      # connectors needed beyond EDGAR
    blurb: str
    fn: object


def _round(v, n=0):
    return None if v is None else round(v, n)


def _precompute(cons: CompanyConstraints, ledger: SyntheticLedger) -> dict:
    fy = ledger.years[-1]
    prior = ledger.years[-2] if len(ledger.years) >= 2 else None
    cust = {c.id: c for c in ledger.customers}
    prod = {p.id: p for p in ledger.products}
    seg_rev, seg_gp, ar_seg, total = defaultdict(float), defaultdict(float), defaultdict(float), 0.0
    for rl in ledger.revenue_lines:
        if rl.fiscal_year != fy:
            continue
        s = cust[rl.customer_id].segment
        seg_rev[s] += rl.amount
        seg_gp[s] += rl.amount * prod[rl.product_id].unit_margin   # one formula rule
        total += rl.amount
    for inv in ledger.ar_invoices:
        if inv.fiscal_year == fy:
            ar_seg[cust[inv.customer_id].segment] += inv.amount
    return {"fy": fy, "prior": prior, "cust": cust, "prod": prod, "total": total,
            "seg_rev": dict(seg_rev), "seg_gp": dict(seg_gp), "ar_seg": dict(ar_seg)}


# ---- analyses (each returns a small, rounded, source-tagged dict) --------------

def a_concentration(cons, ledger, rep, ex):
    rows = rep.top_customers
    top10 = sum(r["pct_of_total"] for r in rows[:10])
    hhi = round(sum((r["pct_of_total"] * 100) ** 2 for r in rows), 0)
    return {"source": "ERP ledger (synthetic, tied out to XBRL)",
            "top5_pct": rep.top5_concentration, "top10_pct": round(top10, 4),
            "hhi": hhi, "largest_customer": rep.largest_customer, "customers": rows[:8],
            "note": f"Top 5 = {rep.top5_concentration*100:.0f}% of revenue, undisclosed in the filing."}


def a_revenue(cons, ledger, rep, ex):
    return {"source": "10-K XBRL + ERP ledger",
            "reported_growth": rep.reported_growth, "underlying_growth": rep.underlying_growth,
            "recurring_growth": rep.recurring_growth, "one_time_pct": rep.one_time_pct,
            "by_segment": {s: _round(v) for s, v in ex["seg_rev"].items()},
            "note": f"Reported growth {(_pct(rep.reported_growth))} vs underlying {_pct(rep.underlying_growth)} ex-largest."}


def a_net_profit(cons, ledger, rep, ex):
    p = cons.by_year()[ex["fy"]]
    pp = cons.by_year().get(ex["prior"])
    seg_margin = {s: (ex["seg_gp"][s] / ex["seg_rev"][s] if ex["seg_rev"].get(s) else None)
                  for s in ex["seg_rev"]}
    return {"source": "10-K XBRL (+ ERP for the segment split)",
            "net_income": _round(p.net_income), "operating_income": _round(p.operating_income),
            "gross_margin": _r4(p.gross_margin), "operating_margin": _r4(p.operating_margin()),
            "net_margin": _r4(p.net_margin()),
            "net_margin_prior": _r4(pp.net_margin()) if pp else None,
            "segment_gross_margin": {s: _r4(m) for s, m in seg_margin.items()},
            "note": f"Net margin {_pct(p.net_margin())}" + (f" vs {_pct(pp.net_margin())} prior." if pp and pp.net_margin() else ".")}


def a_cash_flow(cons, ledger, rep, ex):
    p = cons.by_year()[ex["fy"]]
    fcf = p.free_cash_flow()
    ccc = None
    if p.dso() is not None and p.dio() is not None and p.dpo() is not None:
        ccc = round(p.dso() + p.dio() - p.dpo(), 1)
    return {"source": "10-K XBRL (cash-flow + balance sheet)",
            "operating_cash_flow": _round(p.cfo), "capex": _round(p.capex),
            "free_cash_flow": _round(fcf),
            "fcf_margin": _r4(fcf / p.revenue_total) if (fcf is not None and p.revenue_total) else None,
            "cash_conversion_cycle_days": ccc, "dso": p.dso() and round(p.dso(), 1),
            "dio": p.dio() and round(p.dio(), 1), "dpo": p.dpo() and round(p.dpo(), 1),
            "note": f"FCF {_money(fcf)} on OCF {_money(p.cfo)} less capex {_money(p.capex)}."}


def a_burn(cons, ledger, rep, ex):
    p = cons.by_year()[ex["fy"]]
    fcf = p.free_cash_flow()
    generating = fcf is not None and fcf > 0
    return {"source": "10-K XBRL",
            "annual_free_cash_flow": _round(fcf), "operating_cash_flow": _round(p.cfo),
            "capex_intensity": _r4(p.capex / p.revenue_total) if (p.capex and p.revenue_total) else None,
            "status": "cash-generating" if generating else ("cash-burning" if fcf is not None else "n/a"),
            "note": (f"Self-funding: {_money(fcf)} of free cash flow."
                     if generating else f"Burning: free cash flow is {_money(fcf)}.")}


def a_roi_segment(cons, ledger, rep, ex):
    p = cons.by_year()[ex["fy"]]
    cogs_tot = sum(ex["seg_rev"][s] - ex["seg_gp"][s] for s in ex["seg_rev"]) or 1
    rows = []
    for s, rev in sorted(ex["seg_rev"].items(), key=lambda x: -x[1]):
        gp = ex["seg_gp"][s]
        cogs_s = rev - gp
        ar_s = ex["ar_seg"].get(s, 0.0)
        inv_s = (p.inventory or 0) * (cogs_s / cogs_tot)
        ap_s = (p.accounts_payable or 0) * (cogs_s / cogs_tot)
        ic = ar_s + inv_s - ap_s
        roic = (gp / ic) if ic > 0 else None
        rows.append({"segment": s, "revenue": _round(rev), "gross_profit": _round(gp),
                     "proxy_invested_capital": _round(ic), "gp_roic_proxy": _r4(roic)})
    return {"source": "ERP ledger + balance sheet (proxy invested capital)", "segments": rows,
            "note": "Return on a proxy invested capital (AR + inventory − AP) by segment."}


def a_retention(cons, ledger, rep, ex):
    return {"source": "CRM cohorts (synthetic)", "net_retention": rep.net_retention,
            "cohorts": [{"cohort_year": c.cohort_year, "n_customers": c.n_customers,
                         "net_retention": c.net_retention} for c in ledger.cohorts],
            "note": f"Net revenue retention {_pct(rep.net_retention)}."}


def a_pipeline(cons, ledger, rep, ex):
    stages = defaultdict(lambda: [0, 0.0])
    for o in ledger.pipeline:
        stages[o.stage][0] += 1
        stages[o.stage][1] += o.amount
    funnel = [{"stage": s, "count": v[0], "amount": _round(v[1])} for s, v in stages.items()]
    return {"source": "CRM pipeline (synthetic)", **rep.pipeline, "funnel": funnel,
            "note": f"Win-rate-weighted pipeline covers {_pct((rep.pipeline or {}).get('coverage'))} of next-year growth."}


def a_headcount(cons, ledger, rep, ex):
    hc = synth_headcount(cons, ledger)
    return {"source": "HRIS (synthetic, calibrated to revenue)",
            "years": [{"fiscal_year": h.fiscal_year, "headcount": h.headcount,
                       "revenue_per_employee": _round(h.revenue_per_employee),
                       "by_segment": h.by_segment} for h in hc],
            "note": (f"~{hc[-1].headcount:,} employees, {_money(hc[-1].revenue_per_employee)}/employee."
                     if hc else "")}


def a_investors(cons, ledger, rep, ex):
    summ = holdings_summary(synth_holdings(cons, ledger))
    return {"source": "13F-style holdings (synthetic, calibrated demo data)", **summ,
            "note": f"Hedge funds hold ~{_pct(summ['hedge_fund_pct'])}; institutions ~{_pct(summ['institutional_pct'])}."}


def _pct(v):
    return "—" if v is None else f"{v*100:.0f}%"


def _money(v):
    if v is None:
        return "—"
    a = abs(v); s = "-" if v < 0 else ""
    if a >= 1e9: return f"{s}${a/1e9:.1f}B"
    if a >= 1e6: return f"{s}${a/1e6:.0f}M"
    return f"{s}${a:,.0f}"


def _r4(v):
    return None if v is None else round(v, 4)


ANALYSES = {a.id: a for a in [
    Analysis("concentration", "Customer concentration", ["erp"],
             "Top-customer share + HHI the filing never breaks out.", a_concentration),
    Analysis("revenue_analysis", "Revenue quality", ["erp"],
             "Reported vs underlying vs recurring growth, by segment.", a_revenue),
    Analysis("net_profit", "Profitability & margins", ["erp"],
             "Gross/operating/net margin + the segment-level split.", a_net_profit),
    Analysis("annual_cash_flow", "Cash flow & conversion", ["erp"],
             "OCF, capex, FCF and the cash-conversion cycle.", a_cash_flow),
    Analysis("burn_rate", "Burn / self-funding", ["erp"],
             "Is the business generating or burning cash?", a_burn),
    Analysis("roi_by_segment", "ROI by segment", ["erp"],
             "Return on a proxy invested capital, per segment.", a_roi_segment),
    Analysis("retention", "Net revenue retention", ["crm"],
             "Cohort expansion net of churn.", a_retention),
    Analysis("pipeline_to_revenue", "Pipeline → revenue", ["crm"],
             "Win-rate-weighted pipeline vs next-year growth.", a_pipeline),
    Analysis("headcount", "Headcount & efficiency", ["hris"],
             "Employees + revenue/employee, by segment.", a_headcount),
    Analysis("investor_analysis", "Investor base & flows", ["investor"],
             "Top holders, hedge-fund ownership, QoQ flow.", a_investors),
]}


def catalog(connected: set) -> list[dict]:
    have = {"edgar"} | set(connected)
    out = []
    for a in ANALYSES.values():
        out.append({"id": a.id, "name": a.name, "requires": a.requires, "blurb": a.blurb,
                    "unlocked": set(a.requires) <= have,
                    "needs": [r for r in a.requires if r not in have]})
    return out


def run_analysis(cons: CompanyConstraints, ledger: SyntheticLedger, analysis_id: str) -> dict:
    a = ANALYSES.get(analysis_id)
    if a is None:
        raise KeyError(f"unknown analysis {analysis_id!r}")
    rep = run_qoe(cons, ledger)
    ex = _precompute(cons, ledger)
    return {"id": a.id, "name": a.name, "requires": a.requires, "blurb": a.blurb,
            "result": a.fn(cons, ledger, rep, ex)}
