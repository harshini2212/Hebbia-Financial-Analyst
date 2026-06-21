"""Synthetic 13F-style institutional holdings (investor connector).

A plausible ownership register — top institutions + hedge funds, % held, QoQ flow —
deterministic by CIK. Not real 13F data: calibrated demo data. Tie-out: percentages
sum to 100%.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import numpy as np

from .constraints import CompanyConstraints
from .generate import SyntheticLedger
from .ipf import rake_1d

_INSTITUTIONS = ["BlackRock", "Vanguard", "State Street", "FMR (Fidelity)",
                 "T. Rowe Price", "Capital Group", "Geode Capital"]
_HEDGE = ["Citadel", "Renaissance Technologies", "Two Sigma", "Point72",
          "Millennium", "Coatue", "Tiger Global"]


@dataclass
class Holder:
    name: str
    pct_held: float
    is_hedge_fund: bool
    qoq_change_pct: float


def synth_holdings(cons: CompanyConstraints, ledger: SyntheticLedger,
                   *, n_holders: int = 14, institutional_pct: float = 0.72) -> list[Holder]:
    rng = np.random.default_rng(int(hashlib.sha256((cons.cik + "13f").encode()).hexdigest()[:12], 16))
    names = _INSTITUTIONS + _HEDGE
    rng.shuffle(names)
    names = names[:n_holders]
    w = rng.lognormal(mean=0.0, sigma=1.1, size=len(names))
    pcts = rake_1d(w, institutional_pct)
    holders = []
    for nm, p in zip(names, pcts):
        hf = nm in _HEDGE
        holders.append(Holder(nm, round(float(p), 4), hf,
                              round(float(rng.normal(0, 0.9 if hf else 0.35)), 3)))
    holders.sort(key=lambda h: -h.pct_held)
    holders.append(Holder("Retail & other", round(1 - institutional_pct, 4), False, 0.0))
    return holders


def holdings_summary(holders: list[Holder]) -> dict:
    hf = [h for h in holders if h.is_hedge_fund]
    return {"top_holders": [{"name": h.name, "pct_held": h.pct_held,
                             "is_hedge_fund": h.is_hedge_fund, "qoq_change_pct": h.qoq_change_pct}
                            for h in holders[:10]],
            "hedge_fund_pct": round(sum(h.pct_held for h in hf), 4),
            "institutional_pct": round(sum(h.pct_held for h in holders if h.name != "Retail & other"), 4),
            "n_hedge_funds": len(hf)}
