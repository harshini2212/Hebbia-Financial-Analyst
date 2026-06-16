"""A Matrix-style column-generation agent + a deterministic cell verifier.

`ColumnAgent` fills one Matrix cell — answers a question over a single filing and
emits a Retrieval -> Definition -> Calculation derivation trace. `verify_cell` then
runs the cell's own cited figures back through the accounting-identity engine, so
the cell carries a deterministic trust verdict (and a three-way attribution when it
doesn't reconcile), not just a citation.
"""

from .column import Cell, ColumnAgent
from .verify import CellVerdict, IdentityCheck, NumberCheck, verify_cell

__all__ = ["ColumnAgent", "Cell", "verify_cell", "CellVerdict",
           "IdentityCheck", "NumberCheck"]
