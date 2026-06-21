"""The Query Grid — documents (rows) x questions (columns), each cell run through
the eval gate. This is the "Matrix" frame, but the cells are gated, verified, and
costed; the grid is plumbing and the eval is the point.

Fan-out is bounded in-process concurrency (a ThreadPoolExecutor with a worker cap +
backpressure) — the honest single-machine stand-in for the Celery/Redis fan-out in
the architecture doc. Ground truth for the metrics is the filings' own XBRL.
"""

from __future__ import annotations

import dataclasses
from concurrent.futures import ThreadPoolExecutor

from ..engine import PropagatingEngine
from ..extract import ResponseCache, XbrlDirectExtractor
from ..facts import FactStore, FiscalPeriod, Period
from ..ingest import EdgarClient
from ..ingest.xbrl import periods_in
from ..ontology import concept as get_concept
from ..registry import REGISTRY
from ..agent.orchestrator import run_gated

GRID_FILINGS = [
    {"ticker": "COST", "issuer": "Costco Wholesale", "note": "clean baseline"},
    {"ticker": "AMZN", "issuer": "Amazon", "note": "multi-segment + equity-method"},
    {"ticker": "KHC", "issuer": "Kraft Heinz", "note": "messy revenue tagging"},
]

# Each column is an analyst question + the canonical concept it targets. The concept
# is what lets XBRL stand in as free ground truth for scoring.
GRID_QUESTIONS = [
    {"id": "net_income", "label": "Net income", "concept": "net_income.parent",
     "unit": "USD", "q": "What was {co}'s net income attributable to the company in fiscal year {fy}?"},
    {"id": "assets", "label": "Total assets", "concept": "assets.total",
     "unit": "USD", "q": "What were {co}'s total assets at the end of fiscal year {fy}?"},
    {"id": "gross_margin", "label": "Gross margin", "concept": "gross_margin.ratio",
     "unit": "ratio", "q": "What was {co}'s gross margin in fiscal year {fy}?"},
    {"id": "operating_margin", "label": "Operating margin", "concept": "operating_margin.ratio",
     "unit": "ratio", "q": "What was {co}'s operating margin in fiscal year {fy}?"},
    {"id": "net_margin", "label": "Net margin", "concept": "net_margin.ratio",
     "unit": "ratio", "q": "What was {co}'s net profit margin in fiscal year {fy}?"},
    {"id": "effective_tax_rate", "label": "Effective tax rate", "concept": "effective_tax_rate.ratio",
     "unit": "ratio", "q": "What was {co}'s effective income tax rate in fiscal year {fy}?"},
]

_GOLD_CONCEPTS = [q["concept"] for q in GRID_QUESTIONS]


def xbrl_gold(store: FactStore, fy: int) -> dict[str, float | None]:
    """Ground truth from the filing's own XBRL: reported facts, plus ratios the
    propagating engine derives from them. A concept that doesn't resolve is None —
    i.e. the question is *not answerable* from structured ground truth."""
    periods = periods_in(store.all_facts())
    eng = PropagatingEngine(REGISTRY)
    eng.run(store, periods)
    aug = eng.store or store
    gold: dict[str, float | None] = {}
    for cid in _GOLD_CONCEPTS:
        c = get_concept(cid)
        # consolidated only (dimensions={}) so a segment/dimensional fact can't be
        # mistaken for the company total — the bug that made AMZN "revenue" a segment.
        facts = aug.query(cid, Period(c.period_type, fy, FiscalPeriod.FY), dimensions={})
        gold[cid] = float(facts[0].value) if facts else None
    return gold


def _cell_dict(g) -> dict:
    return dataclasses.asdict(g)


def run_grid(*, cache: ResponseCache | None = None, concurrency: int = 6,
             progress=lambda *_: None) -> dict:
    cache = cache or ResponseCache(".cache/llm")
    client = EdgarClient()

    rows = []
    stores: dict[str, tuple[FactStore, int, dict]] = {}
    for f in GRID_FILINGS:
        filing = client.find_10k(f["ticker"])
        store = FactStore(); store.add_all(XbrlDirectExtractor().extract(filing))
        fy = max(p.fiscal_year for p in periods_in(store.all_facts()))
        gold = xbrl_gold(store, fy)
        stores[f["ticker"]] = (store, fy, gold)
        rows.append({**f, "issuer": filing.issuer, "fiscal_year": fy})
        progress("ingest", f["ticker"])

    # fan out every (filing, question) cell with a bounded worker pool (backpressure)
    work = []
    for f in GRID_FILINGS:
        store, fy, gold = stores[f["ticker"]]
        co = f["issuer"]
        for q in GRID_QUESTIONS:
            text = q["q"].format(co=co, fy=fy)
            work.append((f["ticker"], q["id"], text, fy, store,
                         gold.get(q["concept"]), q["unit"]))

    results: dict[str, dict] = {f["ticker"]: {} for f in GRID_FILINGS}

    def _run(item):
        ticker, qid, text, fy, store, gold_v, unit = item
        g = run_gated(text, fy, store, cache=cache, ticker=ticker, gold=gold_v, unit=unit)
        progress("cell", f"{ticker}/{qid}")
        return ticker, qid, g

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        for ticker, qid, g in pool.map(_run, work):
            results[ticker][qid] = g

    # metrics use XBRL gold (lazy import avoids a cycle)
    from .metrics import compute_metrics
    golds = {t: stores[t][2] for t in stores}
    metrics = compute_metrics(results, golds, GRID_QUESTIONS)

    cells_json = {t: {qid: _cell_dict(g) for qid, g in row.items()}
                  for t, row in results.items()}
    total_cost = round(sum(g.cost_usd for row in results.values() for g in row.values()), 6)
    return {
        "rows": rows,
        "columns": GRID_QUESTIONS,
        "cells": cells_json,
        "metrics": metrics,
        "gold": {t: golds[t] for t in golds},
        "total_cost_usd": total_cost,
        "concurrency": concurrency,
    }
