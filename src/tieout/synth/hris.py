"""Synthetic headcount (HRIS connector) — calibrated to the reported top line.

Anchors to the real reported revenue: headcount[y] = revenue[y] / revenue-per-employee,
with revenue/employee rising over time (operating leverage). Deterministic by CIK.
Tie-out: sum over segments of (heads x rpe) reconciles to reported revenue.
Calibrated demo data, not real employee data.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import numpy as np

from .constraints import CompanyConstraints
from .generate import SyntheticLedger


@dataclass
class HeadcountYear:
    fiscal_year: int
    headcount: int
    revenue_per_employee: float
    by_segment: dict


def synth_headcount(cons: CompanyConstraints, ledger: SyntheticLedger,
                    *, base_rpe: float = 420_000) -> list[HeadcountYear]:
    rng = np.random.default_rng(int(hashlib.sha256((cons.cik + "hris").encode()).hexdigest()[:12], 16))
    years = sorted(p.fiscal_year for p in cons.periods if p.revenue_total)
    by_year = cons.by_year()
    # scale rpe to the company size so headcount is plausible (big caps are capital-heavy)
    latest_rev = by_year[years[-1]].revenue_total if years else 0
    scale = 1.0 + min(latest_rev / 2e12, 1.1)        # bigger firms => modestly higher rev/employee
    out = []
    for i, y in enumerate(years):
        pc = by_year[y]
        rpe = base_rpe * scale * (1 + 0.045 * i)       # productivity rises over time
        heads = max(1, round(pc.revenue_total / rpe))
        segs = pc.segments or {"Total": pc.revenue_total}
        tot = sum(segs.values()) or pc.revenue_total
        by_seg, alloc, names = {}, 0, list(segs)
        for j, s in enumerate(names):
            h = (heads - alloc) if j == len(names) - 1 else round(heads * segs[s] / tot)
            by_seg[s] = max(h, 0); alloc += by_seg[s]
        out.append(HeadcountYear(y, heads, round(pc.revenue_total / heads), by_seg))
    return out
