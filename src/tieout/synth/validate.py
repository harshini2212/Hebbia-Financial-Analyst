"""Tie-out validation for the synthetic ledger.

The same idea as the rest of the project — does the granular detail reconcile to the
public total? — but pointed at *generated* data. If the synthetic ledger sums back
to the XBRL marginals within tolerance, it's calibrated; if not, it's rejected. One
trust layer, two jobs: it validates real extraction AND synthetic generation.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from .constraints import CompanyConstraints
from .generate import SyntheticLedger


@dataclass
class TieOut:
    marginal: str
    fiscal_year: int
    target: float
    synthetic: float
    variance: float
    pct: float
    ties_out: bool


def _rel(target: float, synth: float, tol: float = 0.01) -> tuple[float, float, bool]:
    var = synth - target
    pct = var / target if target else (0.0 if synth == 0 else 1.0)
    return var, pct, abs(pct) <= tol


def validate(ledger: SyntheticLedger, cons: CompanyConstraints) -> list[TieOut]:
    by_year = cons.by_year()
    cust = {c.id: c for c in ledger.customers}
    prod = {p.id: p for p in ledger.products}
    checks: list[TieOut] = []

    rev_by_year: dict[int, float] = defaultdict(float)
    rev_by_seg: dict[tuple[int, str], float] = defaultdict(float)
    cogs_by_year: dict[int, float] = defaultdict(float)
    for rl in ledger.revenue_lines:
        rev_by_year[rl.fiscal_year] += rl.amount
        rev_by_seg[(rl.fiscal_year, cust[rl.customer_id].segment)] += rl.amount
        cogs_by_year[rl.fiscal_year] += rl.amount * (1 - prod[rl.product_id].unit_margin)
    ar_by_year: dict[int, float] = defaultdict(float)
    for inv in ledger.ar_invoices:
        ar_by_year[inv.fiscal_year] += inv.amount

    for y in ledger.years:
        pc = by_year[y]
        if pc.revenue_total:
            var, pct, ok = _rel(pc.revenue_total, rev_by_year[y])
            checks.append(TieOut("total revenue", y, pc.revenue_total, rev_by_year[y], var, pct, ok))
        for s, target in (pc.segments or {}).items():
            syn = rev_by_seg[(y, s)]
            var, pct, ok = _rel(target, syn)
            checks.append(TieOut(f"segment revenue · {s}", y, target, syn, var, pct, ok))
        if pc.cogs:
            var, pct, ok = _rel(pc.cogs, cogs_by_year[y], tol=0.015)
            checks.append(TieOut("COGS (blended margin)", y, pc.cogs, cogs_by_year[y], var, pct, ok))
        if pc.accounts_receivable and ar_by_year.get(y):
            var, pct, ok = _rel(pc.accounts_receivable, ar_by_year[y], tol=0.02)
            checks.append(TieOut("accounts receivable", y, pc.accounts_receivable, ar_by_year[y], var, pct, ok))
    return checks


def all_tie_out(checks: list[TieOut]) -> bool:
    return all(c.ties_out for c in checks)
