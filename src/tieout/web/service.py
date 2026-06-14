"""Analysis service: turns the full tieout pipeline into JSON for the web UI.

Reuses every layer we built (ingest -> extract -> engine -> attribution) and
serialises the result. Results are cached to .cache/web/{ticker}.json so the UI
is instant; `force=True` recomputes live (EDGAR + Claude).
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from ..attribution import attribute_run
from ..constraints import Status, is_aggregable
from ..engine import CheckerEngine, PropagatingEngine
from ..engine.propagating import _expr_str
from ..extract import (BaselineExtractor, LlmTextExtractor, ResponseCache,
                       XbrlDirectExtractor, claude_model)
from ..facts import Fact, FactStore, Source
from ..ingest import EdgarClient
from ..ingest.text import edgar_text_provider
from ..ingest.xbrl import periods_in
from ..registry import REGISTRY
from ..report import build_scorecard

FILINGS = [
    {"ticker": "COST", "issuer": "Costco Wholesale", "note": "clean baseline"},
    {"ticker": "AMZN", "issuer": "Amazon", "note": "multi-segment + equity-method"},
    {"ticker": "KHC", "issuer": "Kraft Heinz", "note": "mezzanine equity"},
]
_DESC = {t.template_id: t for t in REGISTRY}
# Committed so a fresh clone serves the UI instantly, offline, with no API key.
# "Run live" overwrites these when a key + network are available.
_CACHE_DIR = Path("data/web")


def _f(x) -> float | None:
    return None if x is None else float(x)


def _band(gt: Decimal) -> Decimal:
    return max(abs(gt) * Decimal("0.002"), Decimal("1000000"))


def registry_json() -> list[dict]:
    out = []
    for t in REGISTRY:
        out.append({
            "id": t.template_id,
            "kind": t.kind.value,
            "description": t.description,
            "severity": t.severity.value,
            "formula": f"{t.target.concept} = {_expr_str(t.expr)}",
            "tolerance": {"abs": _f(t.tolerance.abs), "rel": _f(t.tolerance.rel),
                          "rel_ground_truth": _f(t.tolerance.rel_ground_truth)},
        })
    return out


def _store(facts) -> FactStore:
    s = FactStore(); s.add_all(facts); return s


def _constraint_rows(gt_results, claude_results, fy) -> list[dict]:
    gt = {r.template_id: r for r in gt_results if r.period.fiscal_year == fy}
    cl = {r.template_id: r for r in claude_results if r.period.fiscal_year == fy}
    rows = []
    for t in REGISTRY:
        g, c = gt.get(t.template_id), cl.get(t.template_id)
        rows.append({
            "id": t.template_id,
            "description": t.description,
            "severity": t.severity.value,
            "formula": f"{t.target.concept} = {_expr_str(t.expr)}",
            "gt_status": g.status.value if g else "n/a",
            "gt_residual": _f(g.residual) if g else None,
            "gt_band": _f(g.band) if g else None,
            "claude_status": c.status.value if c else "n/a",
            "claude_residual": _f(c.residual) if c else None,
            "claude_band": _f(c.band) if c else None,
        })
    return rows


def _fact_comparison(claude_store, gt, fy) -> list[dict]:
    out = []
    for f in claude_store.all_facts():
        if f.period.fiscal_year != fy or f.dimensions or f.unit != "USD":
            continue
        g = gt.query(f.concept, f.period, dimensions={}, source=Source.XBRL)
        if not g:
            out.append({"concept": f.concept, "text": _f(f.value),
                        "xbrl": None, "agree": None})
            continue
        gv = g[0].value
        out.append({"concept": f.concept, "text": _f(f.value), "xbrl": _f(gv),
                    "agree": abs(f.value - gv) <= _band(gv)})
    return sorted(out, key=lambda r: r["concept"])


def _propagation(gt, periods, fy) -> list[dict]:
    eng = PropagatingEngine(REGISTRY)
    eng.run(gt, periods)
    out = []
    for f in eng.derived_facts:
        if f.period.fiscal_year != fy:
            continue
        out.append({"concept": f.concept, "value": _f(f.value),
                    "unit": f.unit, "op": f.provenance.op,
                    "inputs": len(f.provenance.input_fact_ids)})
    return out


def _localization(gt_facts, periods, fy) -> dict | None:
    seg = [f for f in gt_facts if f.concept == "revenue.segment"
           and f.period.fiscal_year == fy]
    if not seg:
        return None
    victim = max(seg, key=lambda f: f.value)
    corrupted = Fact(victim.concept, victim.value + Decimal("5000000000"),
                     victim.period, Source.XBRL, victim.provenance,
                     decimals=victim.decimals, dimensions=victim.dimensions)
    store2 = _store([f for f in gt_facts if f.fact_id != victim.fact_id])
    store2.add(corrupted)
    eng = PropagatingEngine(REGISTRY)
    results = eng.run(store2, periods)
    r = next((x for x in results if x.template_id == "rev.segments_sum"
              and x.period.fiscal_year == fy), None)
    if r is None or r.status is not Status.VIOLATED:
        return None
    suspects = []
    for fid, score in eng.localizations.get(r.inst_id, []):
        f = eng.store.get(fid)
        suspects.append({"concept": f.concept,
                         "segment": dict(f.dimensions).get("segment", "(total)"),
                         "value": _f(f.value), "score": score})
    return {"injected_segment": dict(victim.dimensions).get("segment", "?"),
            "injected_amount": 5_000_000_000,
            "residual": _f(r.residual), "suspects": suspects}


def _segments(gt, periods, fy) -> dict | None:
    per = next((p for p in periods if p.fiscal_year == fy
                and p.type.value == "duration"), None)
    if per is None:
        return None
    seg = [f for f in gt.query("revenue.segment", per, source=Source.XBRL)
           if is_aggregable(f.dims_dict(), "segment")]
    if not seg:
        return None
    total_f = gt.query("revenue.total", per, dimensions={}, source=Source.XBRL)
    total = _f(total_f[0].value) if total_f else None
    # Some filers tag a segment at several granularities (segment, segment x
    # category, duplicates across contexts). Keep one value per segment — the
    # largest, which is the segment total rather than a sub-row.
    best: dict[str, float] = {}
    for f in seg:
        name = dict(f.dimensions).get("segment", "?").replace("Member", "")
        v = _f(f.value)
        if name not in best or v > best[name]:
            best[name] = v
    members = sorted(({"name": k, "value": v} for k, v in best.items()),
                     key=lambda m: -m["value"])
    ssum = sum(m["value"] for m in members)
    return {"members": members, "total": total, "sum": ssum,
            "reconciles": total is not None
            and abs(ssum - total) <= max(abs(total) * 0.005, 1e6)}


def _scorecard_json(sc) -> dict:
    return {"name": sc.name, "facts": sc.extracted, "agree": sc.agree,
            "disagree": sc.disagree, "satisfied": sc.satisfied,
            "violated": sc.violated, "soft": sc.soft_violated,
            "indeterminate": sc.indeterminate, "judge_invisible": sc.judge_invisible,
            "attributions": [{"id": a.template_id, "label": a.label.value,
                              "evidence": a.evidence} for a in sc.attributions]}


def analyze(ticker: str, *, force: bool = False) -> dict:
    ticker = ticker.upper()
    cache = _CACHE_DIR / f"{ticker}.json"
    if cache.exists() and not force:
        return json.loads(cache.read_text(encoding="utf-8"))

    client = EdgarClient()
    filing = client.find_10k(ticker)
    tp = edgar_text_provider(client)

    gt_facts = XbrlDirectExtractor().extract(filing)
    gt = _store(gt_facts)
    periods = periods_in(gt_facts)
    fy = max(p.fiscal_year for p in periods)

    claude_facts = LlmTextExtractor(claude_model("claude-opus-4-8"),
                                    ResponseCache(".cache/llm"),
                                    text_provider=tp).extract(filing)
    claude_store = _store(claude_facts)
    baseline_store = _store(BaselineExtractor(tp).extract(filing))

    scorecards = [
        _scorecard_json(build_scorecard("Claude (text)", claude_store, gt,
                                        REGISTRY, periods, fy)),
        _scorecard_json(build_scorecard("Baseline (regex)", baseline_store, gt,
                                        REGISTRY, periods, fy)),
    ]
    gt_results = CheckerEngine(REGISTRY, source=Source.XBRL).run(gt, periods)
    claude_results = CheckerEngine(REGISTRY).run(claude_store, periods)

    result = {
        "ticker": ticker,
        "issuer": filing.issuer,
        "fiscal_year": fy,
        "filing_date": filing.filing_date,
        "url": filing.url,
        "scorecards": scorecards,
        "constraints": _constraint_rows(gt_results, claude_results, fy),
        "facts": _fact_comparison(claude_store, gt, fy),
        "propagation": _propagation(gt, periods, fy),
        "segments": _segments(gt, periods, fy),
        "attribution": [{"id": a.template_id, "label": a.label.value,
                         "evidence": a.evidence}
                        for a in attribute_run(REGISTRY, periods, claude_store, gt)
                        if a.period.fiscal_year == fy],
        "localization": _localization(gt_facts, periods, fy),
    }
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


_search_client: EdgarClient | None = None


def search_companies(query: str, limit: int = 8) -> list[dict]:
    global _search_client
    if _search_client is None:
        _search_client = EdgarClient()
    return _search_client.search(query, limit)


def filings_index() -> list[dict]:
    out = []
    for f in FILINGS:
        out.append({**f, "cached": (_CACHE_DIR / f"{f['ticker']}.json").exists()})
    return out
