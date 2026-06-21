"""Pull the hard constraints for the synthetic generator from a filing's XBRL.

These are the marginals the generated ledger MUST sum back to: total revenue,
segment revenue, COGS / gross margin, and the working-capital balances
(AR / inventory / AP) that pin DSO / DIO / DPO. Everything the engine fabricates is
anchored to these real, reported numbers — that's what makes it calibrated demo
data rather than fiction.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..constraints import is_aggregable
from ..extract import XbrlDirectExtractor
from ..facts import FactStore, FiscalPeriod, Period, Source
from ..ingest import EdgarClient
from ..ingest.xbrl import periods_in
from ..ontology import PeriodType, concept as get_concept


@dataclass
class PeriodConstraints:
    fiscal_year: int
    revenue_total: float | None
    segments: dict[str, float] = field(default_factory=dict)
    cogs: float | None = None
    gross_margin: float | None = None
    accounts_receivable: float | None = None
    inventory: float | None = None
    accounts_payable: float | None = None

    def dso(self) -> float | None:
        if self.accounts_receivable and self.revenue_total:
            return self.accounts_receivable / self.revenue_total * 365
        return None

    def dio(self) -> float | None:
        if self.inventory and self.cogs:
            return self.inventory / self.cogs * 365
        return None

    def dpo(self) -> float | None:
        if self.accounts_payable and self.cogs:
            return self.accounts_payable / self.cogs * 365
        return None


@dataclass
class CompanyConstraints:
    ticker: str
    issuer: str
    cik: str
    periods: list[PeriodConstraints]  # newest first

    def by_year(self) -> dict[int, PeriodConstraints]:
        return {p.fiscal_year: p for p in self.periods}


def _clean_segment(member: str) -> str:
    name = member.split(":")[-1] if member else member
    if name.endswith("Member"):
        name = name[:-6]
    # split CamelCase into words
    out = []
    for i, ch in enumerate(name):
        if ch.isupper() and i and not name[i - 1].isupper():
            out.append(" ")
        out.append(ch)
    return "".join(out).strip() or member


def _val(store: FactStore, concept_id: str, fy: int) -> float | None:
    c = get_concept(concept_id)
    facts = store.query(concept_id, Period(c.period_type, fy, FiscalPeriod.FY),
                        dimensions={}, source=Source.XBRL)
    return float(facts[0].value) if facts else None


def _segments(store: FactStore, fy: int) -> dict[str, float]:
    per = Period(PeriodType.DURATION, fy, FiscalPeriod.FY)
    facts = [f for f in store.query("revenue.segment", per, source=Source.XBRL)
             if is_aggregable(f.dims_dict(), "segment")]
    best: dict[str, float] = {}
    for f in facts:
        name = _clean_segment(dict(f.dimensions).get("segment", ""))
        v = float(f.value)
        if name and (name not in best or v > best[name]):
            best[name] = v
    return best


def pull_constraints(ticker: str, *, store: FactStore | None = None,
                     issuer: str = "", cik: str = "", years: int = 3) -> CompanyConstraints:
    if store is None:
        filing = EdgarClient().find_10k(ticker)
        store = FactStore(); store.add_all(XbrlDirectExtractor().extract(filing))
        issuer = issuer or filing.issuer
        cik = cik or str(getattr(filing, "cik", "") or ticker)
    fiscal_years = sorted({p.fiscal_year for p in periods_in(store.all_facts())},
                          reverse=True)[:years]
    periods: list[PeriodConstraints] = []
    for fy in fiscal_years:
        rev = _val(store, "revenue.total", fy)
        cogs = _val(store, "cogs.total", fy)
        gp = _val(store, "gross_profit.total", fy)
        gm = None
        if gp is not None and rev:
            gm = gp / rev
        elif cogs is not None and rev:
            gm = (rev - cogs) / rev
        if cogs is None and gp is not None and rev is not None:
            cogs = rev - gp
        periods.append(PeriodConstraints(
            fiscal_year=fy, revenue_total=rev, segments=_segments(store, fy),
            cogs=cogs, gross_margin=gm,
            accounts_receivable=_val(store, "accounts_receivable.total", fy),
            inventory=_val(store, "inventory.total", fy),
            accounts_payable=_val(store, "accounts_payable.total", fy),
        ))
    return CompanyConstraints(ticker.upper(), issuer or ticker.upper(),
                              cik or ticker.upper(), periods)
