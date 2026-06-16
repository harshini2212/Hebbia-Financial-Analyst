"""A small hybrid answer-quality evaluation for financial-document extraction.

Runs the column agent over a question set and scores each filled cell three ways —
deterministic identity verification, an audit-trail rubric (line items tagged
Retrieval / Definition / Calculation, auto-graded by the fact store + the constraint
layer), and a naive LLM judge. This mirrors Hebbia's stated "hybrid deterministic +
rubric" evaluation philosophy and surfaces the money metric: answers an LLM judge
rubber-stamps that the deterministic identity layer catches as wrong.
"""

from .grade import grade_one, llm_judge
from .run import run_benchmark

__all__ = ["grade_one", "llm_judge", "run_benchmark"]
