"""Source-adapter pattern — the bridge between demo and production.

Every data source exposes the same contract, so the workflows never know (or care)
whether the data came from EDGAR, an EDGAR-anchored synthetic generator, or a real
ERP/CRM via a unified connector. You demo on `SyntheticAdapter`; a real customer
connects `MergeAdapter` and the *exact same* workflows run on their live data. That
swap — not the connector logos — is the point.

  EdgarAdapter     -> real public data (XBRL constraints)         [built]
  SyntheticAdapter -> EDGAR-anchored calibrated ledger (demo)     [built]
  MergeAdapter     -> real ERP/CRM via Merge.dev (production)      [roadmap]
"""

from __future__ import annotations

from dataclasses import dataclass

from .constraints import CompanyConstraints, pull_constraints
from .generate import SyntheticLedger, generate
from .validate import all_tie_out, validate


class EdgarAdapter:
    """Public source: pulls the real XBRL constraint set for a company."""
    kind = "public"
    name = "EDGAR"

    def __init__(self, ticker: str):
        self.ticker = ticker

    def constraints(self) -> CompanyConstraints:
        return pull_constraints(self.ticker)


class SyntheticAdapter:
    """Private source (demo): an EDGAR-anchored synthetic ERP/CRM ledger that ties
    out to the public constraints. Drop-in stand-in for a real connector."""
    kind = "synthetic"
    name = "Synthetic ERP/CRM"

    def __init__(self, constraints: CompanyConstraints, *, n_customers: int = 140):
        self.constraints = constraints
        self.n_customers = n_customers

    def ledger(self) -> SyntheticLedger:
        return generate(self.constraints, n_customers=self.n_customers)


class MergeAdapter:
    """Private source (production): real ERP/CRM via a unified connector (Merge.dev).
    Intentionally not implemented — the production story is a few hundred lines behind
    the same interface; the demo runs on SyntheticAdapter."""
    kind = "live"
    name = "Merge (live ERP/CRM)"

    def __init__(self, *_, **__):
        raise NotImplementedError(
            "MergeAdapter is the production path; the demo uses SyntheticAdapter "
            "behind this same interface.")


@dataclass
class Workspace:
    ticker: str
    issuer: str
    constraints: CompanyConstraints
    ledger: SyntheticLedger
    tied_out: bool


def build_workspace(ticker: str, *, n_customers: int = 140) -> Workspace:
    """Wire EDGAR (public) + Synthetic (private) through the adapters into one
    workspace, validating that the private ledger ties out to the public truth."""
    cons = EdgarAdapter(ticker).constraints()
    ledger = SyntheticAdapter(cons, n_customers=n_customers).ledger()
    tied = all_tie_out(validate(ledger, cons))
    return Workspace(cons.ticker, cons.issuer, cons, ledger, tied)
