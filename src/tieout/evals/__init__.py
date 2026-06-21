"""The eval engine as a platform layer: a gated Query Grid + XBRL-grounded metrics.

`run_grid` runs documents x questions through the eval gate (see
`agent.orchestrator`); `compute_metrics` scores the result against the filings' own
XBRL ground truth (tie-out accuracy, hallucination rate, abstention calibration,
grounding rate) — the regression-suite numbers the README reports.
"""

from .grid import GRID_FILINGS, GRID_QUESTIONS, run_grid, xbrl_gold
from .metrics import compute_metrics

__all__ = ["run_grid", "xbrl_gold", "compute_metrics",
           "GRID_FILINGS", "GRID_QUESTIONS"]
