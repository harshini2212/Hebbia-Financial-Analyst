"""Quality-of-Earnings reconciliation — the hero workflow.

Bridges the filed *consolidated* numbers (public XBRL) to *granular* reality (the
private ledger) and surfaces what the filing hides: customer concentration,
reported-vs-underlying growth, one-time revenue, product-mix margin pressure, and
working-capital drift. Every figure is reconciled (rolls up to the public total) and
carries its evidence — the analysis that only exists when you join public + private.
"""

from __future__ import annotations

import os
import time
import uuid
from collections import defaultdict
from dataclasses import asdict, dataclass, field

from ..synth.constraints import CompanyConstraints, pull_constraints
from ..synth.generate import SyntheticLedger, generate
from ..synth.validate import TieOut, validate


@dataclass
class Insight:
    headline: str
    severity: str            # high | medium | low
    detail: str
    evidence: str


@dataclass
class CustomerRow:
    id: str
    name: str
    segment: str
    revenue: float
    pct_of_total: float
    yoy: float | None


@dataclass
class QoEReport:
    ticker: str
    issuer: str
    fiscal_year: int
    prior_year: int | None
    reconciliation: list           # [TieOut as dict]
    tied_out: bool
    top_customers: list            # [CustomerRow as dict]
    top5_concentration: float
    reported_growth: float | None
    underlying_growth: float | None   # ex-largest customer
    largest_customer: str
    one_time_revenue: float
    one_time_pct: float
    recurring_growth: float | None
    margin_bridge: dict
    working_capital: dict
    insights: list                 # [Insight as dict]
    net_retention: float | None = None
    pipeline: dict = field(default_factory=dict)


def _pct(a, b):
    return (a / b) if b else None


def run_qoe(cons: CompanyConstraints, ledger: SyntheticLedger) -> QoEReport:
    years = ledger.years
    fy = years[-1]
    prior = years[-2] if len(years) >= 2 else None
    cust = {c.id: c for c in ledger.customers}
    prod = {p.id: p for p in ledger.products}

    rev_cy: dict[tuple[str, int], float] = defaultdict(float)   # (customer, year)
    rev_py: dict[tuple[str, int], float] = defaultdict(float)   # (product, year)
    onetime: dict[int, float] = defaultdict(float)
    total: dict[int, float] = defaultdict(float)
    for rl in ledger.revenue_lines:
        rev_cy[(rl.customer_id, rl.fiscal_year)] += rl.amount
        rev_py[(rl.product_id, rl.fiscal_year)] += rl.amount
        total[rl.fiscal_year] += rl.amount
        if not rl.recurring:
            onetime[rl.fiscal_year] += rl.amount

    # --- reconciliation (public vs private) ---
    checks = validate(ledger, cons)
    tied = all(c.ties_out for c in checks)

    # --- concentration ---
    latest = sorted(((cid, rev_cy[(cid, fy)]) for cid in cust if rev_cy[(cid, fy)] > 0),
                    key=lambda x: -x[1])
    tot = total[fy] or 1.0
    top_rows = []
    for cid, rev in latest[:8]:
        yoy = _pct(rev - rev_cy[(cid, prior)], rev_cy[(cid, prior)]) if prior and rev_cy[(cid, prior)] else None
        top_rows.append(CustomerRow(cid, cust[cid].name, cust[cid].segment,
                                    round(rev), round(rev / tot, 4),
                                    round(yoy, 4) if yoy is not None else None))
    top5 = sum(r for _, r in latest[:5]) / tot

    # --- reported vs underlying growth (ex-largest) ---
    whale = next((c for c in ledger.customers if c.is_whale), None)
    reported_growth = underlying_growth = None
    if prior and total[prior]:
        reported_growth = total[fy] / total[prior] - 1
        if whale:
            ex_now = total[fy] - rev_cy[(whale.id, fy)]
            ex_prior = total[prior] - rev_cy[(whale.id, prior)]
            underlying_growth = ex_now / ex_prior - 1 if ex_prior else None

    # --- recurring vs one-time ---
    ot = onetime[fy]
    rec_growth = None
    if prior and (total[prior] - onetime[prior]):
        rec_growth = (total[fy] - ot) / (total[prior] - onetime[prior]) - 1

    # --- margin / mix bridge ---
    def shares(y):
        t = total[y] or 1.0
        return {pid: rev_py[(pid, y)] / t for pid in prod}
    s_now = shares(fy)
    s_prior = shares(prior) if prior else {}
    erode = min(prod.values(), key=lambda p: p.unit_margin)
    margin_bridge = {
        "blended_margin": round(cons.by_year()[fy].gross_margin, 4) if cons.by_year()[fy].gross_margin else None,
        "lowest_margin_line": erode.name,
        "lowest_margin": round(erode.unit_margin, 3),
        "share_now": round(s_now.get(erode.id, 0), 4),
        "share_prior": round(s_prior.get(erode.id, 0), 4) if prior else None,
        "products": [{"name": p.name, "margin": round(p.unit_margin, 3),
                      "share": round(s_now.get(p.id, 0), 4)} for p in prod.values()],
    }

    # --- working capital (from balance-sheet-derived ratios) ---
    pc, pcp = cons.by_year()[fy], (cons.by_year().get(prior) if prior else None)
    working_capital = {
        "dso": round(pc.dso(), 1) if pc.dso() else None,
        "dso_prior": round(pcp.dso(), 1) if pcp and pcp.dso() else None,
        "dio": round(pc.dio(), 1) if pc.dio() else None,
        "dpo": round(pc.dpo(), 1) if pc.dpo() else None,
    }

    insights = _insights(ledger, cust, top5, latest, fy, reported_growth,
                         underlying_growth, whale, ot, tot, margin_bridge, working_capital)

    # --- CRM: cohort net retention + win-rate-weighted pipeline coverage ---
    coh = [c for c in ledger.cohorts if c.starting_revenue > 0]
    nrr = (sum(c.current_revenue for c in coh) / sum(c.starting_revenue for c in coh)
           if coh else None)
    pl_weighted = sum(o.amount * o.win_prob for o in ledger.pipeline)
    growth_dollars = (total[fy] - total[prior]) if prior and total.get(prior) else None
    coverage = round(pl_weighted / growth_dollars, 3) if growth_dollars else None
    pipeline = {"weighted": round(pl_weighted), "count": len(ledger.pipeline),
                "coverage": coverage, "open_value": round(sum(o.amount for o in ledger.pipeline))}
    if nrr is not None:
        insights.append(Insight(
            f"Net revenue retention {nrr*100:.0f}%", "medium" if nrr < 0.97 else "low",
            "Cohort expansion net of churn, from the CRM/ERP customer base.",
            f"Σ cohort current / starting across {len(coh)} cohorts"))
    if coverage is not None:
        insights.append(Insight(
            f"Pipeline covers {coverage*100:.0f}% of next-year growth need",
            "low" if coverage >= 1 else "medium",
            "Win-rate-weighted CRM pipeline vs the dollars to repeat this year's growth.",
            f"${pl_weighted/1e9:.2f}B weighted across {len(ledger.pipeline)} open opps"))

    return QoEReport(
        cons.ticker, cons.issuer, fy, prior,
        [asdict(c) for c in checks], tied,
        [asdict(r) for r in top_rows], round(top5, 4),
        round(reported_growth, 4) if reported_growth is not None else None,
        round(underlying_growth, 4) if underlying_growth is not None else None,
        whale.name if whale else "", round(ot), round(ot / tot, 4),
        round(rec_growth, 4) if rec_growth is not None else None,
        margin_bridge, working_capital, [asdict(i) for i in insights],
        net_retention=round(nrr, 3) if nrr is not None else None, pipeline=pipeline)


def _insights(ledger, cust, top5, latest, fy, reported, underlying, whale, ot, tot,
              mb, wc) -> list[Insight]:
    out = []
    if top5 >= 0.25:
        names = ", ".join(cust[cid].name for cid, _ in latest[:3])
        out.append(Insight(
            f"Top 5 customers = {top5*100:.0f}% of revenue — undisclosed in the filing",
            "high", f"Customer concentration the consolidated 10-K never breaks out.",
            f"Largest: {names}"))
    if reported is not None and underlying is not None and abs(reported - underlying) > 0.02:
        out.append(Insight(
            f"Ex-largest customer, underlying growth is {underlying*100:.0f}%, not the reported {reported*100:.0f}%",
            "high", f"{whale.name}'s share is rising YoY, so the headline growth overstates the base.",
            f"Reported {reported*100:.1f}% vs underlying {underlying*100:.1f}%"))
    if ot and ot / tot > 0.01:
        out.append(Insight(
            f"${ot/1e6:,.0f}M of revenue is one-time — {ot/tot*100:.0f}% of the top line",
            "medium", "Non-recurring revenue inflating the latest period; the recurring base is softer.",
            f"{ot/tot*100:.1f}% of FY{fy} revenue tagged one-time"))
    if mb["share_prior"] is not None and mb["share_now"] > mb["share_prior"] + 0.01:
        out.append(Insight(
            f"{mb['lowest_margin_line']} (lowest margin, {mb['lowest_margin']*100:.0f}%) gaining share",
            "medium", "Mix shift toward the lowest-margin line — gross-margin pressure ahead.",
            f"Share {mb['share_prior']*100:.0f}% → {mb['share_now']*100:.0f}%"))
    if wc["dso"] and wc["dso_prior"] and wc["dso"] - wc["dso_prior"] > 3:
        out.append(Insight(
            f"DSO up {wc['dso']-wc['dso_prior']:.0f} days YoY — collections slowing",
            "medium", "Receivables growing faster than revenue; cash conversion deteriorating ahead of the cash-flow statement.",
            f"DSO {wc['dso_prior']:.0f} → {wc['dso']:.0f} days"))
    return out


# --- Streaming: the workflow as an event generator -----------------------------
# A workflow is a generator that emits typed events as it computes. The live SSE
# stream forwards them; `materialize_qoe` drains them into the report dict. One
# definition of the workflow, no duplicated logic.

_CONS_CACHE: dict[str, CompanyConstraints] = {}   # cache the EDGAR/XBRL pull per process
PACE = float(os.getenv("RUN_PACE", "0.0"))         # >0 drips events for demo pacing


def _pace() -> None:
    if PACE:
        time.sleep(PACE)


def _ledger_summary(ledger: SyntheticLedger) -> dict:
    return {"customers": len(ledger.customers),
            "products": [asdict(p) for p in ledger.products],
            "revenue_lines": len(ledger.revenue_lines), "ar_invoices": len(ledger.ar_invoices),
            "pipeline_opps": len(ledger.pipeline), "cohorts": len(ledger.cohorts),
            "anomalies": ledger.anomalies}


def _report_dict(cons: CompanyConstraints, ledger: SyntheticLedger, rep: QoEReport) -> dict:
    out = asdict(rep)
    recon = out.get("reconciliation") or []
    out["tied_out"] = bool(recon) and all(c["ties_out"] for c in recon)
    out["constraints"] = [asdict(p) for p in cons.periods]
    out["ledger_summary"] = _ledger_summary(ledger)
    return out


def qoe_events(ticker: str, period: str = "FY2025"):
    """Run a Quality-of-Earnings analysis, yielding (event, payload) as it computes."""
    ticker = ticker.upper()
    run_id = "r_" + uuid.uuid4().hex[:6]
    t0 = time.time()
    yield "run_started", {"run_id": run_id, "workflow": "qoe", "company": ticker, "period": period}
    try:
        # 1 — public marginals from XBRL (EDGAR pull, cached per process)
        yield "step", {"id": "pull_public", "label": "Pull filed marginals from SEC XBRL",
                       "status": "running"}
        _pace()
        cons = _CONS_CACHE.get(ticker)
        if cons is None:
            cons = pull_constraints(ticker)
            _CONS_CACHE[ticker] = cons
        fy = cons.periods[0].fiscal_year if cons.periods else None
        yield "count", {"label": "Reporting periods", "n": len(cons.periods)}
        yield "step", {"id": "pull_public",
                       "label": f"Pulled {cons.issuer} XBRL — {len(cons.periods)} periods",
                       "status": "done"}

        # 2 — private ledger, IPF-fit to the filings
        yield "step", {"id": "pull_private",
                       "label": "Generate ERP/CRM ledger, IPF-fit to the filings",
                       "status": "running"}
        _pace()
        ledger = generate(cons)
        for lbl, n in (("Customers", len(ledger.customers)),
                       ("Revenue lines", len(ledger.revenue_lines)),
                       ("AR invoices", len(ledger.ar_invoices)),
                       ("Pipeline opps", len(ledger.pipeline))):
            yield "count", {"label": lbl, "n": n}
        yield "step", {"id": "pull_private",
                       "label": f"Generated {len(ledger.customers)} customers · "
                                f"{len(ledger.revenue_lines)} revenue lines",
                       "status": "done"}

        # 3 — reconcile + tie-out, one event per check
        yield "step", {"id": "reconcile", "label": "Reconcile ledger to filed figures (tie-out)",
                       "status": "running"}
        passed = 0
        for chk in validate(ledger, cons):
            if chk.fiscal_year != fy:
                continue
            passed += int(chk.ties_out)
            if chk.marginal.startswith("segment revenue"):
                seg = chk.marginal.split("·", 1)[-1].strip()
                yield "cell", {"segment": seg, "filed": chk.target, "rollup": chk.synthetic,
                               "variance": round(chk.pct, 6), "ties": chk.ties_out}
            yield "tie_out", {"check": chk.marginal, "value": chk.target,
                              "variance": round(chk.pct, 6), "passed": chk.ties_out}
            _pace()
        yield "step", {"id": "reconcile", "label": f"Reconciled — {passed} checks tie out",
                       "status": "done"}

        # 4 — decompose → metrics + findings
        yield "step", {"id": "decompose", "label": "Decompose: concentration, growth, retention",
                       "status": "running"}
        rep = run_qoe(cons, ledger)
        _pace()
        yield "step", {"id": "decompose", "label": "Decomposed the granular ledger", "status": "done"}

        if rep.reported_growth is not None:
            yield "metric", {"key": "reported_growth", "label": "Reported growth",
                             "value": round(rep.reported_growth * 100, 1), "unit": "%"}
        if rep.underlying_growth is not None:
            flag = ("danger" if rep.reported_growth is not None
                    and rep.underlying_growth < rep.reported_growth - 0.02 else None)
            yield "metric", {"key": "underlying_growth", "label": "Underlying growth (ex-largest)",
                             "value": round(rep.underlying_growth * 100, 1), "unit": "%", "flag": flag}
        yield "metric", {"key": "top5_concentration", "label": "Top-5 concentration",
                         "value": round(rep.top5_concentration * 100, 0), "unit": "%",
                         "flag": "danger" if rep.top5_concentration >= 0.3 else None}
        if rep.net_retention is not None:
            yield "metric", {"key": "net_retention", "label": "Net retention",
                             "value": round(rep.net_retention * 100, 0), "unit": "%"}
        if rep.pipeline and rep.pipeline.get("coverage") is not None:
            yield "metric", {"key": "pipeline_coverage", "label": "Pipeline coverage",
                             "value": round(rep.pipeline["coverage"] * 100, 0), "unit": "%"}

        for ins in rep.insights:
            conf = {"high": 0.97, "medium": 0.92, "low": 0.88}.get(ins.get("severity"), 0.9)
            yield "finding", {"severity": ins["severity"], "text": ins["headline"],
                              "detail": ins.get("detail", ""), "evidence": ins.get("evidence", ""),
                              "confidence": conf}
            _pace()

        # 5 — the full report (for the concentration table, panels, inspector)
        yield "result", _report_dict(cons, ledger, rep)
        yield "done", {"run_id": run_id, "checks_passed": passed,
                       "elapsed_ms": int((time.time() - t0) * 1000)}
    except Exception as exc:  # surface as a stream event, never a 500 mid-stream
        yield "failed", {"message": str(exc)}


def materialize_qoe(ticker: str, period: str = "FY2025") -> dict:
    """Drain qoe_events into the full report dict — the single definition shared by the
    cached recompute (`/api/qoe/{ticker}/run`) and the headless CLI."""
    report: dict = {}
    for event, payload in qoe_events(ticker, period):
        if event == "result":
            report = payload
        elif event == "failed":
            raise RuntimeError(payload["message"])
    return report
