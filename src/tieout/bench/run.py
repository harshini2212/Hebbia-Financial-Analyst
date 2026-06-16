"""Run the hybrid answer-quality evaluation across models -> a leaderboard.

This mirrors Hebbia's stated evaluation philosophy — *hybrid: deterministic checks
+ rubric/LLM grading* — applied to financial-document extraction. For each
(model, question) the column agent fills a Matrix cell, and the cell is scored three
ways:

  - deterministic verification — the cell's own figures, run back through the
    accounting-identity engine (`verify_cell`): retrieval-correct? identities
    reconcile? answer self-consistent? -> the `trusted` rate.
  - audit-trail rubric — line items tagged retrieval/definition/calculation,
    auto-graded by the fact store + the agent's computed value -> the rubric score.
  - LLM judge — a naive plausibility check with no answer key, the baseline that
    rubber-stamps confident-but-wrong answers.

The headline is the **money metric**: answers the LLM judge accepts that are
actually wrong — exactly what the deterministic identity layer catches and a
citation/plausibility check does not.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..agent import ColumnAgent, verify_cell
from ..extract import ResponseCache, XbrlDirectExtractor
from ..facts import FactStore
from ..ingest import EdgarClient
from .grade import grade_one, llm_judge

DEFAULT_MODELS = ["claude-opus-4-8", "claude-sonnet-4-6", "claude-haiku-4-5"]
JUDGE_MODEL = "claude-sonnet-4-6"
QUESTIONS = Path("data/bench/questions.json")


def _model_label(m: str) -> str:
    return (m.replace("claude-", "").replace("-4-8", " 4.8").replace("-4-6", " 4.6")
            .replace("-4-5", " 4.5").title())


def run_benchmark(models=None, questions_path=QUESTIONS, judge_model=JUDGE_MODEL,
                  progress=lambda *_: None) -> dict:
    models = models or DEFAULT_MODELS
    qs = json.loads(Path(questions_path).read_text(encoding="utf-8"))["questions"]
    cache = ResponseCache(".cache/llm")
    client = EdgarClient()
    stores: dict[str, FactStore] = {}

    def store_for(ticker: str) -> FactStore:
        if ticker not in stores:
            filing = client.find_10k(ticker)
            s = FactStore(); s.add_all(XbrlDirectExtractor().extract(filing))
            stores[ticker] = s
        return stores[ticker]

    results = {}
    for model in models:
        per = []
        for q in qs:
            store = store_for(q["ticker"])
            cell = ColumnAgent(store, model_id=model, cache=cache).fill(
                q["question"], q["fiscal_year"])
            g = grade_one(q, cell)
            v = verify_cell(cell, store)
            judged = llm_judge(q, cell, judge_model, cache)
            unanswerable = q["gold"].get("answerable") is False
            trusted = (cell.value is None) if unanswerable else v.trusted
            per.append({
                "id": q["id"], "skill": q["skill"], "question": q["question"],
                "gold": q["gold"].get("value"), "unit": q["gold"].get("unit"),
                "value": cell.value, "answer": cell.answer,
                "rubric_score": round(g["rubric_score"], 3), "final_ok": g["final_ok"],
                "trusted": trusted, "retrieval_ok": v.retrieval_ok,
                "judge_yes": judged, "items": g["items"], "error": cell.error,
            })
            progress(model, q["id"])
        n = len(per) or 1
        results[model] = {
            "label": _model_label(model),
            "per_question": per,
            "rubric_score": round(sum(p["rubric_score"] for p in per) / n, 3),
            "final_accuracy": round(sum(p["final_ok"] for p in per) / n, 3),
            "trusted_rate": round(sum(p["trusted"] for p in per) / n, 3),
            "judge_accuracy": round(sum(p["judge_yes"] for p in per) / n, 3),
            "money_metric": sum(1 for p in per if p["judge_yes"] and not p["final_ok"]),
        }
        results[model]["gap"] = round(results[model]["rubric_score"]
                                      - results[model]["final_accuracy"], 3)

    return {"models": models, "judge_model": judge_model,
            "question_count": len(qs), "results": results}
