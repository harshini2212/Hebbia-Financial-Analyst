"""XBRL-grounded eval metrics over a grid run.

The non-obvious move: the filings' own XBRL is the answer key, so these are real,
machine-checked metrics — not LLM-judge vibes. Answerability itself is defined by
ground truth (a concept that doesn't resolve from XBRL is *unanswerable*), which is
what makes abstention calibration measurable.
"""

from __future__ import annotations


def _within(val, gold, unit) -> bool:
    if val is None or gold is None:
        return False
    if unit == "ratio":
        return abs(val - gold) <= 0.005
    return abs(val - gold) <= max(abs(gold) * 0.01, 1_000_000)


def _rate(n, d):
    return round(n / d, 4) if d else None


def compute_metrics(results: dict, golds: dict, questions: list) -> dict:
    qmap = {q["id"]: q for q in questions}
    total = answerable = unanswerable = 0
    correct = wrong = under_abstain = correct_abstention = false_answer = 0
    grounded_ok = grounded_checkable = 0
    by_status: dict[str, int] = {}
    escalated = verified_first_tier = 0
    cost = 0.0

    for ticker, row in results.items():
        for qid, g in row.items():
            total += 1
            q = qmap[qid]
            gold = golds.get(ticker, {}).get(q["concept"])
            val = g.value
            cost += g.cost_usd
            by_status[g.status] = by_status.get(g.status, 0) + 1
            if len(g.attempts) > 1:
                escalated += 1
            if g.status == "verified" and len(g.attempts) == 1:
                verified_first_tier += 1

            for c in g.checks:                       # grounding: cited figures vs XBRL
                if c.get("ok") is not None:
                    grounded_checkable += 1
                    grounded_ok += 1 if c.get("ok") else 0

            if gold is not None:                     # answerable from ground truth
                answerable += 1
                if val is None:
                    under_abstain += 1               # declined a knowable answer
                elif _within(val, gold, q["unit"]):
                    correct += 1
                else:
                    wrong += 1                       # asserted a wrong figure
            else:                                    # unanswerable from ground truth
                unanswerable += 1
                if val is None:
                    correct_abstention += 1
                else:
                    false_answer += 1                # asserted an unsupported figure

    return {
        "total_cells": total,
        "answerable": answerable,
        "unanswerable": unanswerable,
        "tie_out_accuracy": _rate(correct, answerable),
        "tie_out_correct": correct,
        "wrong_figures": wrong,
        "under_abstentions": under_abstain,
        "hallucination_rate": _rate(wrong + false_answer, total),
        "hallucinations": wrong + false_answer,
        "abstention_recall": _rate(correct_abstention, unanswerable),
        "false_answer_rate": _rate(false_answer, unanswerable),
        "correct_abstentions": correct_abstention,
        "grounding_rate": _rate(grounded_ok, grounded_checkable),
        "grounded_ok": grounded_ok,
        "grounded_checkable": grounded_checkable,
        "gate": {
            "by_status": by_status,
            "escalated": escalated,
            "verified_first_tier": verified_first_tier,
            "escalation_rate": _rate(escalated, total),
        },
        "cost_usd": round(cost, 6),
    }
