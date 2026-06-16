"""Smoke-test the column agent + the engine-backed cell verifier on one filing."""
from __future__ import annotations
import sys

from tieout.agent import ColumnAgent, verify_cell
from tieout.extract import XbrlDirectExtractor
from tieout.facts import FactStore
from tieout.ingest import EdgarClient

ticker = sys.argv[1] if len(sys.argv) > 1 else "COST"
model = sys.argv[2] if len(sys.argv) > 2 else "claude-opus-4-8"

filing = EdgarClient().find_10k(ticker)
store = FactStore(); store.add_all(XbrlDirectExtractor().extract(filing))
agent = ColumnAgent(store, model_id=model)

qs = [
    f"What was {filing.issuer}'s gross margin in FY{filing.fiscal_year}?",
    f"What was net income attributable to the company in FY{filing.fiscal_year}?",
    f"What was the operating margin in FY{filing.fiscal_year}?",
]
for q in qs:
    cell = agent.fill(q, filing.fiscal_year)
    v = verify_cell(cell, store)
    print(f"\nQ: {q}")
    if cell.error:
        print("  ERROR:", cell.error); continue
    print(f"  A: {cell.answer}")
    print(f"  value={cell.value} {cell.unit} | concept={cell.answer_concept} | "
          f"tool_calls={len(cell.tool_calls)} | hit={cell.cache_hit}")
    print(f"  TRUSTED={v.trusted}  retrieval_ok={v.retrieval_ok}  calc={v.calc_status}"
          + (f" (identities imply {v.derived_value})" if v.derived_value is not None else ""))
    for c in v.checks:
        print(f"     {c.concept}={c.stated} vs truth={c.truth} ok={c.ok} {c.note}")
    for ic in v.identities:
        tag = f" [{ic.label}]" if ic.label else ""
        print(f"     identity {ic.template_id}: {ic.status}{tag}")
