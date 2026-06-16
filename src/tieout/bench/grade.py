"""Rubric grading (auto) + an LLM-judge baseline.

Retrieval items pass if the agent actually retrieved that concept/year (the tool
returns ground truth, so a correct retrieval = a correct figure). Definition items
pass if the agent used the right input concepts. Calculation items pass if the
final value is within tolerance of the gold answer.
"""

from __future__ import annotations

from ..agent.verify import _money_match


def _within(value, gold) -> bool:
    if gold.get("answerable") is False:
        return value is None   # correct iff the agent declined to fabricate
    if value is None:
        return False
    if gold.get("unit") == "ratio":
        tol = gold.get("tolerance", 0.004)
        # accept either decimal (0.1238) or percent-as-number (12.38) encoding
        return (abs(value - gold["value"]) <= tol
                or abs(value / 100 - gold["value"]) <= tol)
    return _money_match(value, gold["value"])


# Concepts that are interchangeable for a filer without noncontrolling interest.
_EQUIV = {
    "equity.total": {"equity.parent"}, "equity.parent": {"equity.total"},
    "net_income.parent": {"net_income.consolidated"},
    "net_income.consolidated": {"net_income.parent"},
}


def _equiv(concept: str) -> set:
    return {concept} | _EQUIV.get(concept, set())


def _retrieved(ans, concept, fy) -> bool:
    fy = int(fy)
    targets = _equiv(concept)
    return any(tc.get("concept") in targets
              and int(tc.get("fiscal_year", fy)) == fy
              and (tc.get("result") or {}).get("value") is not None
              for tc in ans.tool_calls)


def _used_concepts(ans) -> set:
    s = {tc["concept"] for tc in ans.tool_calls if tc.get("concept")}
    s |= {n["concept"] for n in ans.numbers_used if n.get("concept")}
    return s


def grade_one(q: dict, ans) -> dict:
    gold = q["gold"]
    items = []
    used = _used_concepts(ans)
    for r in q["rubric"]:
        t = r["type"]
        if t == "retrieval":
            passed = _retrieved(ans, r["concept"], r.get("fiscal_year", q["fiscal_year"]))
        elif t == "definition":
            req = q.get("required_concepts", [])
            passed = all(_equiv(c) & used for c in req)
        elif t == "calculation":
            passed = _within(ans.value, gold)
        else:
            passed = False
        items.append({"type": t, "text": r["text"], "weight": r["weight"], "passed": passed})

    total = sum(r["weight"] for r in q["rubric"]) or 1
    got = sum(i["weight"] for i in items if i["passed"])
    return {"rubric_score": got / total, "final_ok": _within(ans.value, gold), "items": items}


def llm_judge(q: dict, ans, judge_model: str, cache) -> bool:
    """The naive LLM-as-judge baseline: it grades the FINAL ANSWER for plausibility,
    with no answer key and no derivation to inspect — so a confident,
    plausible-but-wrong answer gets rubber-stamped. The deterministic identity layer
    catches exactly these (the money metric), which is why Hebbia's own evaluation
    philosophy pairs LLM grading with deterministic checks."""
    from ..extract import CachedModel, claude_model
    prompt = ("You are a lightweight automated answer-checker for a finance assistant. "
              "Decide whether the answer is a plausible, reasonable answer to the "
              "question for a major public company. Accept it unless it is clearly "
              "wrong, malformed, or a non-answer.\n\n"
              f"Question: {q['question']}\n"
              f"Answer: {ans.answer}\n\n"
              "Respond with exactly one word: 'yes' (acceptable) or 'no' (clearly wrong).")
    cm = CachedModel(claude_model(judge_model), cache,
                     prompt_version="judge-v4", adapter_version="judge/0")
    try:
        text, _, _ = cm.complete(prompt)
        t = text.strip().lower()
        return t.startswith("y") or t.startswith("**y") or "yes" in t[:6]
    except Exception:
        return False
