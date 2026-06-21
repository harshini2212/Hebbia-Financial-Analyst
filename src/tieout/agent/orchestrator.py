"""Eval-gate orchestrator — eval is a *gate* in the hot path, not a report.

Each cell runs a plan -> route -> generate -> verify -> GATE loop. The gate is
deterministic and has two teeth, both grounded in the filing's own XBRL:

  1. tie-out — does the answer match the XBRL ground truth for the asked concept?
  2. verify_cell — are the cited figures internally consistent (retrieval, the
     accounting identities they touch, and does the answer follow from them)?

A cell that fails the gate is not just flagged — the orchestrator ESCALATES (a
stronger model tier) and re-runs, up to the tier budget. A cell that never passes
is surfaced as *low-confidence*, never as a confident wrong answer. A cell with no
XBRL ground truth that the agent answers anyway is *unverifiable* (low-confidence);
a question with no ground truth that the agent declines is a *correct abstention*.

Every step is a structured trace event; every attempt records tokens + an
(illustrative) cost, so cheap cells stay on the cheap model and only hard/failed
cells pay for the strong one — adaptive model tiering with a cost ceiling.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..extract.cache import ResponseCache
from ..facts import FactStore
from .column import ColumnAgent
from .verify import verify_cell

# cheap -> strong. The gate escalates down this list on failure.
TIERS = ["claude-haiku-4-5", "claude-sonnet-4-6", "claude-opus-4-8"]

# Illustrative list prices ($ per 1M tokens, input/output) — for *relative* cost
# illustration in the UI, not an authoritative quote. Token counts are exact.
PRICE = {
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-opus-4-8": (15.0, 75.0),
}


def model_label(m: str) -> str:
    return (m.replace("claude-", "").replace("-4-8", " 4.8").replace("-4-6", " 4.6")
            .replace("-4-5", " 4.5").title())


def _est_cost(model: str, tin: int, tout: int) -> float:
    pin, pout = PRICE.get(model, (0.0, 0.0))
    return round(tin / 1e6 * pin + tout / 1e6 * pout, 6)


def _within(val, gold, unit) -> bool:
    if val is None or gold is None:
        return False
    if unit == "ratio":
        return abs(val - gold) <= 0.005
    return abs(val - gold) <= max(abs(gold) * 0.01, 1_000_000)


def _route(question: str) -> str:
    q = question.lower()
    if any(w in q for w in ("margin", "ratio", "rate", "return on", "roe", "roa")):
        return "ratio"
    if any(w in q for w in ("grow", "growth", "increase", "change", "year over year", "yoy")):
        return "growth"
    return "absolute_figure"


@dataclass
class Attempt:
    tier: str
    model_label: str
    cache_hit: bool
    input_tokens: int
    output_tokens: int
    est_cost_usd: float
    trusted: bool
    retrieval_ok: bool
    calc_status: str
    tie_out: str = ""            # ok | off | no_gold
    violations: list = field(default_factory=list)   # [{id, label}]
    note: str = ""


@dataclass
class TraceEvent:
    step: str            # plan | route | generate | verify | gate
    detail: str
    tier: str | None = None


@dataclass
class GatedCell:
    ticker: str | None
    question: str
    fiscal_year: int
    status: str          # verified | abstained | low_confidence | error
    answer: str = ""
    value: float | None = None
    unit: str = ""
    answer_concept: str | None = None
    gold: float | None = None
    tie_out: str = ""    # ok | off | no_gold | abstained | unverified
    confidence: float = 0.0
    final_tier: str = ""
    route: str = ""
    attempts: list = field(default_factory=list)     # [Attempt]
    trace: list = field(default_factory=list)        # [TraceEvent]
    cost_usd: float = 0.0
    tokens: int = 0
    checks: list = field(default_factory=list)       # cited figures vs XBRL
    identities: list = field(default_factory=list)   # identities the figures touch
    derivation: list = field(default_factory=list)
    tool_calls: int = 0
    error: str = ""


_CONF_VERIFIED = [0.98, 0.92, 0.85, 0.80]   # by tier index it passed at


def run_gated(question: str, fiscal_year: int, gt_store: FactStore, *,
              cache: ResponseCache | None = None, tiers: list[str] | None = None,
              ticker: str | None = None, gold: float | None = None,
              unit: str = "USD") -> GatedCell:
    cache = cache or ResponseCache(".cache/llm")
    tiers = tiers or TIERS
    out = GatedCell(ticker, question, fiscal_year, status="low_confidence",
                    unit=unit, gold=gold)

    route = _route(question)
    out.route = route
    out.trace.append(TraceEvent("plan", _plan_text(route)))
    out.trace.append(TraceEvent("route",
        f"{route} → grounded XBRL structured retrieval"
        + ("; XBRL ground truth available for tie-out" if gold is not None
           else "; no XBRL ground truth for this concept")))

    best = None  # (cell, verdict, score) — strongest partial, for low-confidence
    for idx, tier in enumerate(tiers):
        cell = ColumnAgent(gt_store, model_id=tier, cache=cache).fill(question, fiscal_year)
        out.trace.append(TraceEvent("generate",
            f"{model_label(tier)} filled the cell"
            + (" (cache hit)" if cell.cache_hit else "")
            + (f" — {len(cell.tool_calls)} grounded retrieval(s)" if cell.tool_calls else ""),
            tier=tier))

        if cell.error:
            out.attempts.append(Attempt(tier, model_label(tier), cell.cache_hit,
                cell.input_tokens, cell.output_tokens,
                _est_cost(tier, cell.input_tokens, cell.output_tokens),
                False, False, "error", note=cell.error))
            out.trace.append(TraceEvent("gate", f"agent error → escalate ({cell.error})", tier))
            continue

        v = verify_cell(cell, gt_store)
        tie = ("no_gold" if gold is None
               else "ok" if _within(cell.value, gold, unit) else "off")
        viols = [{"id": ic.template_id, "label": ic.label}
                 for ic in v.identities if ic.status == "violated"]
        out.attempts.append(Attempt(
            tier, model_label(tier), cell.cache_hit, cell.input_tokens, cell.output_tokens,
            _est_cost(tier, cell.input_tokens, cell.output_tokens),
            v.trusted, v.retrieval_ok, v.calc_status, tie, viols))
        out.trace.append(TraceEvent("verify", _verify_text(v, tie, gold, cell.value, unit, viols), tier))

        # --- gate decision ---
        if cell.value is None:
            if gold is None:
                _finalize(out, cell, v, tier, "abstained", 0.85, "abstained",
                          "no XBRL ground truth and the agent produced no figure → correct abstention")
                return out
            nxt = tiers[idx + 1] if idx + 1 < len(tiers) else None
            out.trace.append(TraceEvent("gate",
                f"no figure produced, but ground truth exists → escalate to {model_label(nxt)}"
                if nxt else "no figure produced at the top tier → flag", tier))
            best = best or (cell, v, -1)
            continue

        if gold is None:
            _finalize(out, cell, v, tier, "low_confidence", 0.40, "no_gold",
                      "answered, but no XBRL ground truth to certify against → low-confidence (unverifiable)")
            return out

        if v.trusted and tie == "ok":
            conf = _CONF_VERIFIED[min(idx, len(_CONF_VERIFIED) - 1)]
            _finalize(out, cell, v, tier, "verified", conf, "ok",
                      f"ties out to XBRL and is self-consistent → accept on {model_label(tier)}")
            return out

        reasons = []
        if tie == "off":
            reasons.append(f"does not tie out to XBRL ({_fmt(cell.value, unit)} vs {_fmt(gold, unit)})")
        if not v.trusted:
            reasons.append("verify_cell rejected (grounding / identity / self-consistency)")
        nxt = tiers[idx + 1] if idx + 1 < len(tiers) else None
        out.trace.append(TraceEvent("gate",
            f"failed the gate — {'; '.join(reasons)} → "
            + (f"escalate to {model_label(nxt)}" if nxt else "mark low-confidence"), tier))
        score = (2 if v.retrieval_ok else 0) + (1 if tie != "off" else 0)
        if best is None or score > best[2]:
            best = (cell, v, score)

    # never passed the gate → surface honestly, never as a confident answer
    if best is None or best[0] is None:
        out.status, out.error = "error", "all tiers errored"
        return out
    cell, v = best[0], best[1]
    if cell.value is None:
        _finalize(out, cell, v, out.attempts[-1].tier, "abstained", 0.50, "abstained",
                  "no figure produced across all tiers, though ground truth exists → flagged abstention")
    else:
        conf = 0.45 if v.retrieval_ok else 0.25
        _finalize(out, cell, v, out.attempts[-1].tier, "low_confidence", conf, "off",
                  "never tied out / never passed across all tiers → low-confidence, not shipped as confident")
    return out


def _finalize(out: GatedCell, cell, v, tier, status, confidence, tie_out, gate: str) -> None:
    out.status = status
    out.confidence = confidence
    out.final_tier = tier
    out.tie_out = tie_out
    out.answer = cell.answer
    out.value = cell.value
    out.unit = cell.unit or out.unit
    out.answer_concept = cell.answer_concept
    out.derivation = cell.derivation
    out.tool_calls = len(cell.tool_calls)
    if v is not None:
        out.checks = [{"label": c.label, "concept": c.concept, "stated": c.stated,
                       "truth": c.truth, "ok": c.ok, "note": c.note} for c in v.checks]
        out.identities = [{"id": ic.template_id, "description": ic.description,
                           "status": ic.status, "residual": ic.residual,
                           "label": ic.label, "evidence": ic.evidence} for ic in v.identities]
    out.tokens = sum(a.input_tokens + a.output_tokens for a in out.attempts)
    out.cost_usd = round(sum(a.est_cost_usd for a in out.attempts), 6)
    out.trace.append(TraceEvent("gate", gate, tier))


def _fmt(v, unit) -> str:
    if v is None:
        return "—"
    return f"{v * 100:.2f}%" if unit == "ratio" else f"{v:,.0f}"


def _plan_text(route: str) -> str:
    if route == "ratio":
        return "ratio question → decompose: retrieve the operands, compute the ratio, verify it derives from them"
    if route == "growth":
        return "growth question → decompose: retrieve the figure for both periods, compute the change"
    return "single-figure question → retrieve the reported value (no decomposition needed)"


def _verify_text(v, tie, gold, val, unit, viols) -> str:
    bits = [f"retrieval {'ok' if v.retrieval_ok else 'MISMATCH'}",
            f"self-consistency {v.calc_status}"]
    if gold is not None:
        bits.append(f"tie-out {tie.upper()} (vs XBRL {_fmt(gold, unit)})")
    if viols:
        bits.append(f"{len(viols)} identity violation(s)")
    ok = v.trusted and tie in ("ok", "no_gold")
    return "verify_cell → " + "; ".join(bits) + (" → PASS" if ok else " → REJECT")
