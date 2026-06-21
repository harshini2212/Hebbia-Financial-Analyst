"""EDGAR-calibrated synthetic data engine.

Generates a realistic private ledger (ERP/CRM) for a company, constrained so it sums
back to that company's real reported XBRL figures — *calibrated demo data*, validated
by the same tie-out harness that checks real extraction. See `generate` for the
mechanics, `validate` for the tie-out, `adapters` for the source-adapter pattern that
lets the demo (SyntheticAdapter) and production (MergeAdapter) share one interface.
"""

from .adapters import EdgarAdapter, MergeAdapter, SyntheticAdapter, build_workspace
from .constraints import CompanyConstraints, PeriodConstraints, pull_constraints
from .generate import SyntheticLedger, generate
from .validate import TieOut, all_tie_out, validate

__all__ = ["pull_constraints", "CompanyConstraints", "PeriodConstraints",
           "generate", "SyntheticLedger", "validate", "TieOut", "all_tie_out",
           "EdgarAdapter", "SyntheticAdapter", "MergeAdapter", "build_workspace"]
