"""ColumnAgent — a Matrix-style column-generation agent (Claude tool-use).

In Hebbia's Matrix, documents are rows, questions are columns, and each *cell* is
one agent's answer for one (document, question) pair. This is a small, faithful
stand-in for that column-generation step: given a question about one filing, the
agent retrieves figures via a tool backed by the filing's XBRL ground truth,
reasons, and returns a `Cell` — a final answer plus a structured derivation trace
tagged Retrieval / Definition / Calculation.

What makes the cell trustworthy is the companion verifier (`verify.py`): it runs
the agent's *own* cited figures back through the deterministic accounting-identity
engine, so the cell carries a verdict — does the answer actually reconcile? — and
not just a citation. That deterministic, cross-figure check is the trust layer a
column-generation step needs and that citation/LLM-judge evaluation alone misses.

The whole run is content-addressed cached so re-runs are free and deterministic.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from ..extract.cache import DecodingParams, ResponseCache, request_key
from ..facts import FactStore, FiscalPeriod, Period, Source
from ..ontology import ONTOLOGY, DataType, concept as get_concept

PROMPT_VERSION = "matrix-cell-v1"
ADAPTER_VERSION = "matrix-cell/0"
_MAX_TURNS = 6


@dataclass
class Cell:
    """One Matrix cell: the agent's answer for one (filing, question) pair."""

    question: str
    fiscal_year: int
    answer: str = ""
    value: float | None = None
    unit: str = ""
    answer_concept: str | None = None  # canonical concept the answer maps to (if any)
    derivation: list = field(default_factory=list)   # [{type, text}]
    numbers_used: list = field(default_factory=list)  # [{label, concept, fiscal_year, value}]
    tool_calls: list = field(default_factory=list)    # [{concept, fiscal_year, result}]
    model_id: str = ""
    cache_hit: bool = False
    error: str = ""


def _catalogue() -> str:
    rows = []
    for c in ONTOLOGY.values():
        kind = "ratio" if c.data_type is DataType.RATIO else "USD"
        rows.append(f"- {c.id}: {c.label} ({kind})")
    return "\n".join(rows)


_TOOLS = [
    {
        "name": "get_financial_value",
        "description": "Look up a reported figure for THIS company by canonical "
                       "concept id and fiscal year. Returns the value (USD, or a "
                       "ratio) from the filing's official XBRL data, or null if the "
                       "filing does not report it (e.g. ratios like margins are "
                       "usually null — you must compute those yourself).",
        "input_schema": {
            "type": "object",
            "properties": {
                "concept": {"type": "string", "description": "a concept id from the catalogue"},
                "fiscal_year": {"type": "integer"},
            },
            "required": ["concept", "fiscal_year"],
        },
    },
]

_SYSTEM = """You are a meticulous financial-analyst agent filling one cell of a
Matrix: one question about a single company's filing. Answer using ONLY figures you
retrieve with get_financial_value (never invent numbers). Ratios/margins are not
stored — compute them from retrieved figures and state the formula.

Concept catalogue (use these exact ids):
{catalogue}

When done, end your reply with a single fenced ```json block:
{{"answer": "<one-sentence answer>", "value": <number or null>, "unit": "USD"|"ratio"|"%",
  "fiscal_year": <int>,
  "answer_concept": "<the catalogue concept id your answer equals, or null>",
  "derivation": [{{"type": "retrieval"|"definition"|"calculation", "text": "<step>"}}],
  "numbers_used": [{{"label": "<what>", "concept": "<id or null>", "fiscal_year": <int>, "value": <number>}}]}}
Set answer_concept to the catalogue id the answer corresponds to when one exists
(e.g. "gross_margin.ratio" for a gross-margin question, "net_income.parent" for a
net-income question); use null if no single concept matches. For a ratio answer,
put the decimal in value (e.g. 0.1284 for 12.84%)."""


class ColumnAgent:
    """Fills Matrix cells for one filing. (Named for Hebbia's column-generation step.)"""

    name = "matrix-column"

    def __init__(self, store: FactStore, *, model_id: str = "claude-opus-4-8",
                 cache: ResponseCache | None = None,
                 params: DecodingParams | None = None) -> None:
        self.store = store
        self.model_id = model_id
        self.cache = cache or ResponseCache(".cache/llm")
        self.params = params or DecodingParams()
        self._client = None

    def _get_client(self):
        if self._client is None:
            from anthropic import Anthropic
            self._client = Anthropic()
        return self._client

    # --- the grounded retrieval tool ---
    def _tool_get_value(self, concept: str, fiscal_year: int):
        if concept not in ONTOLOGY:
            return {"value": None, "note": f"unknown concept {concept!r}"}
        c = get_concept(concept)
        period = Period(c.period_type, int(fiscal_year), FiscalPeriod.FY)
        facts = self.store.query(concept, period, dimensions={}, source=Source.XBRL)
        if not facts:
            return {"value": None, "note": "not reported in this filing"}
        return {"value": float(facts[0].value), "unit": facts[0].unit}

    def fill(self, question: str, fiscal_year: int) -> Cell:
        """Fill one Matrix cell: answer `question` over this filing."""
        key = request_key(model_id=self.model_id, params=self.params,
                          prompt_version=PROMPT_VERSION, adapter_version=ADAPTER_VERSION,
                          rendered_prompt=f"{fiscal_year}\n{question}")
        cached = self.cache.get(key)
        if cached is not None:
            cell = Cell(question, fiscal_year, model_id=self.model_id, cache_hit=True)
            cell.__dict__.update(cached["answer"])
            cell.cache_hit = True
            return cell

        try:
            result = self._run(question, fiscal_year)
        except Exception as exc:  # surface, don't crash a batch
            return Cell(question, fiscal_year, model_id=self.model_id, error=str(exc))
        self.cache.put(key, {"model_id": self.model_id, "answer": result.__dict__})
        return result

    def _run(self, question: str, fiscal_year: int) -> Cell:
        client = self._get_client()
        system = _SYSTEM.format(catalogue=_catalogue())
        messages = [{"role": "user",
                     "content": f"Company filing fiscal year: {fiscal_year}.\n"
                                f"Question: {question}"}]
        out = Cell(question, fiscal_year, model_id=self.model_id)

        for _ in range(_MAX_TURNS):
            msg = client.messages.create(
                model=self.model_id, max_tokens=self.params.max_tokens,
                system=system, tools=_TOOLS, messages=messages)
            messages.append({"role": "assistant", "content": msg.content})

            tool_uses = [b for b in msg.content if getattr(b, "type", "") == "tool_use"]
            if not tool_uses:
                text = "".join(b.text for b in msg.content
                               if getattr(b, "type", "") == "text")
                self._parse_final(text, out)
                return out

            results = []
            for tu in tool_uses:
                concept = tu.input.get("concept", "")
                fy = tu.input.get("fiscal_year", fiscal_year)
                res = self._tool_get_value(concept, fy)
                out.tool_calls.append({"concept": concept, "fiscal_year": fy, "result": res})
                results.append({"type": "tool_result", "tool_use_id": tu.id,
                                "content": json.dumps(res)})
            messages.append({"role": "user", "content": results})

        out.error = "max turns reached without a final answer"
        return out

    @staticmethod
    def _parse_final(text: str, out: Cell) -> None:
        try:
            data = json.loads(_extract_obj(text))
        except Exception:
            out.answer = text.strip()[:500]
            out.error = "could not parse final JSON"
            return
        out.answer = str(data.get("answer", ""))
        v = data.get("value")
        out.value = float(v) if isinstance(v, (int, float)) else None
        out.unit = str(data.get("unit", ""))
        out.fiscal_year = int(data.get("fiscal_year", out.fiscal_year))
        ac = data.get("answer_concept")
        out.answer_concept = ac if (isinstance(ac, str) and ac in ONTOLOGY) else None
        out.derivation = data.get("derivation", []) or []
        out.numbers_used = data.get("numbers_used", []) or []


def _extract_obj(text: str) -> str:
    """Pull the JSON object out of a response that may have prose/fences."""
    t = text.strip()
    if "```" in t:
        after = t[t.find("```") + 3:]
        nl = after.find("\n")
        body = after[nl + 1:] if nl != -1 else after
        end = body.find("```")
        if end != -1:
            t = body[:end]
    i, j = t.find("{"), t.rfind("}")
    return t[i:j + 1] if (i != -1 and j > i) else t
