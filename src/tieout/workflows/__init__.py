"""Agentic workflows that fuse public + private data.

A workflow is a saved analysis template; running it reconciles public (XBRL) and
private (ledger) facts and surfaces findings with evidence. Quality-of-Earnings is
the hero; Coverage Ramp / Covenant Headroom reuse the same reconciliation spine.
"""

from .qoe import QoEReport, run_qoe

__all__ = ["run_qoe", "QoEReport"]
