"""Verify a Matrix cell against the deterministic accounting-identity engine.

This is the differentiator. A column-generation agent (`column.py`) produces a
cell: an answer plus the figures it cited and a derivation. Citation checking and
LLM-judge grading can confirm a number is *on the page* and *looks plausible* — but
not that the cell's figures are *mutually consistent*, nor that the stated answer
actually *follows* from them. So a confident, individually-cited, plausible cell can
still be collectively wrong (a dropped segment in a roll-up, a margin off the wrong
base).

`verify_cell` runs the cell's OWN cited figures back through the real engine that
the rest of `tieout` is built on:

  1. Retrieval — does each cited figure match the filing's official XBRL? (cheap)
  2. Reconciliation — do the cited figures satisfy the hard accounting identities
     they touch (Assets = L + E, segments sum to total, …)? Violations are labelled
     three ways (extraction_error / filing_inconsistency / constraint_model_error)
     by the existing attribution layer, using XBRL as the disambiguator.
  3. Self-consistency — propagate the cited figures through the identity graph and
     check the agent's stated answer equals what the identities *derive* from those
     same figures. This is "the agent verifies its own multi-step reasoning."

No LLM judge is involved; the verdict is a pure, deterministic function of the cell
and the filing's ground truth.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from ..attribution.attribute import Label, attribute_run
from ..constraints import Status
from ..engine import CheckerEngine, PropagatingEngine
from ..facts import Fact, FactStore, FiscalPeriod, Period, Source, TextProv
from ..ontology import ONTOLOGY, DataType, PeriodType, concept as get_concept
from ..registry import REGISTRY

_TMPL = {t.template_id: t for t in REGISTRY}


@dataclass
class NumberCheck:
    label: str
    concept: str | None
    fiscal_year: int | None
    stated: float | None
    truth: float | None
    ok: bool | None      # True/False, or None when not verifiable (e.g. ratios)
    note: str = ""


@dataclass
class IdentityCheck:
    """One accounting identity the cell's cited figures touch."""
    template_id: str
    description: str
    status: str               # "satisfied" | "violated"
    residual: float | None
    label: str | None = None  # attribution label, when violated
    evidence: str = ""


@dataclass
class CellVerdict:
    retrieval_ok: bool
    calc_status: str          # "corroborated" | "contradicted" | "not_derivable"
    derived_value: float | None   # what the identities derive for the answer concept
    trusted: bool
    checks: list = field(default_factory=list)        # [NumberCheck]
    identities: list = field(default_factory=list)    # [IdentityCheck]
    notes: list = field(default_factory=list)

    # back-compat alias: older callers read `.calculation_ok`
    @property
    def calculation_ok(self) -> bool | None:
        if self.calc_status == "corroborated":
            return True
        if self.calc_status == "contradicted":
            return False
        return None


def _money_match(a: float, b: float) -> bool:
    return abs(a - b) <= max(abs(b) * 0.01, 1_000_000)


def _ratio_match(a: float, b: float) -> bool:
    return abs(a - b) <= 0.005


def _truth_value(store: FactStore, concept: str, fy: int) -> float | None:
    if concept not in ONTOLOGY:
        return None
    c = get_concept(concept)
    facts = store.query(concept, Period(c.period_type, int(fy), FiscalPeriod.FY),
                        dimensions={}, source=Source.XBRL)
    return float(facts[0].value) if facts else None


def _text_store(cell) -> tuple[FactStore, list[Period]]:
    """The cell's cited figures, as a TEXT-source fact store (+ the periods touched)."""
    store = FactStore()
    fys: set[int] = set()
    for n in cell.numbers_used:
        concept = n.get("concept")
        val = n.get("value")
        if not concept or concept not in ONTOLOGY or not isinstance(val, (int, float)):
            continue
        fy = int(n.get("fiscal_year", cell.fiscal_year))
        c = get_concept(concept)
        period = Period(c.period_type, fy, FiscalPeriod.FY)
        store.add(Fact(
            concept=concept, value=Decimal(str(val)), period=period,
            source=Source.TEXT,
            provenance=TextProv(doc_id=f"cell:{cell.fiscal_year}", model=cell.model_id,
                                prompt_version="matrix-cell-v1",
                                snippet=str(n.get("label", ""))),
            unit="ratio" if c.data_type is DataType.RATIO else "USD",
        ))
        fys.add(fy)
    fys.add(int(cell.fiscal_year))
    periods: list[Period] = []
    for fy in sorted(fys):
        periods.append(Period(PeriodType.DURATION, fy, FiscalPeriod.FY))
        periods.append(Period(PeriodType.INSTANT, fy, FiscalPeriod.FY))
    return store, periods


def verify_cell(cell, gt_store: FactStore) -> CellVerdict:
    notes: list[str] = []

    # 1) Retrieval: each cited figure vs the filing's official XBRL.
    checks: list[NumberCheck] = []
    for n in cell.numbers_used:
        concept = n.get("concept")
        fy = n.get("fiscal_year", cell.fiscal_year)
        stated = n.get("value")
        stated = float(stated) if isinstance(stated, (int, float)) else None
        truth = _truth_value(gt_store, concept, fy) if concept else None
        if not concept:
            ok, note = None, "no source concept cited"
        elif truth is None:
            ok, note = None, "not in official data (derived/untagged)"
        elif stated is None:
            ok, note = False, "no value stated"
        else:
            ok = _money_match(stated, truth)
            note = "" if ok else f"stated {stated:,.0f} vs official {truth:,.0f}"
        checks.append(NumberCheck(str(n.get("label", "")), concept, fy, stated, truth, ok, note))

    graded = [c for c in checks if c.ok is not None]
    retrieval_ok = bool(graded) and all(c.ok for c in graded)
    if not graded:
        notes.append("No cited figure could be matched to official data.")

    text_store, periods = _text_store(cell)

    # 2) Reconciliation: which hard identities do the cited figures satisfy/violate?
    #    CheckerEngine binds directly (no propagation), so a satisfied identity means
    #    the agent's *own cited* numbers reconcile — not a fact we derived for it.
    identities: list[IdentityCheck] = []
    for r in CheckerEngine(REGISTRY).run(text_store, periods):
        if r.status is Status.INDETERMINATE:
            continue
        identities.append(IdentityCheck(
            r.template_id, _TMPL[r.template_id].description, r.status.value,
            float(r.residual) if r.residual is not None else None))

    # Label any violation three ways, using XBRL as the disambiguator.
    for a in attribute_run(REGISTRY, periods, text_store, gt_store):
        for ic in identities:
            if ic.template_id == a.template_id and ic.status == "violated":
                ic.label = a.label.value
                ic.evidence = a.evidence
    hard_violation = any(ic.status == "violated" for ic in identities)

    # 3) Self-consistency: propagate the cited figures and check the stated answer
    #    equals what the identities DERIVE from those same figures.
    calc_status, derived_value = "not_derivable", None
    if cell.answer_concept and cell.value is not None:
        eng = PropagatingEngine(REGISTRY, ground_truth=False)
        eng.run(text_store, periods)
        c = get_concept(cell.answer_concept)
        period = Period(c.period_type, int(cell.fiscal_year), FiscalPeriod.FY)
        derived = [f for f in (eng.store.query(cell.answer_concept, period) if eng.store else [])
                   if f.source is Source.DERIVED]
        if derived:
            derived_value = float(derived[0].value)
            ok = (_ratio_match(cell.value, derived_value)
                  if c.data_type is DataType.RATIO
                  else _money_match(cell.value, derived_value))
            calc_status = "corroborated" if ok else "contradicted"
            if not ok:
                notes.append(f"stated answer {cell.value} does not match {derived_value} "
                             f"derived from the cited figures via {cell.answer_concept}")

    trusted = retrieval_ok and calc_status != "contradicted" and not hard_violation
    return CellVerdict(retrieval_ok, calc_status, derived_value, trusted,
                       checks, identities, notes)
