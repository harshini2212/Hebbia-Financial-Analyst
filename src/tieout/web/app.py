"""FastAPI app: JSON API over the pipeline + serves the single-page UI."""

from __future__ import annotations

import collections
import json
import os
import time
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import runlog, service


def sse(event: str, data: dict) -> str:
    """One Server-Sent-Events frame: `event: <type>` + `data: <json>` + blank line."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


# Headers that keep SSE flushing incrementally (no proxy/CDN buffering).
_SSE_HEADERS = {"Cache-Control": "no-cache", "Connection": "keep-alive",
                "X-Accel-Buffering": "no"}

_STATIC = Path(__file__).parent / "static"

app = FastAPI(title="tieout", docs_url="/api/docs")

# Public read-only demo API: allow any origin so a separately-hosted UI (or the
# static export) can stream from it. (No GZipMiddleware — it would buffer SSE.)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                   allow_headers=["*"])

# Protect a public "Run live" button from running up the Anthropic bill: cap live
# runs per rolling hour. Tune via the TIEOUT_RUN_LIMIT env var (0 = unlimited).
_RUN_LIMIT = int(os.environ.get("TIEOUT_RUN_LIMIT", "40"))
_RUN_TIMES: collections.deque = collections.deque()


def _ratelimit():
    if not _RUN_LIMIT:
        return
    now = time.time()
    while _RUN_TIMES and now - _RUN_TIMES[0] > 3600:
        _RUN_TIMES.popleft()
    if len(_RUN_TIMES) >= _RUN_LIMIT:
        raise HTTPException(status_code=429,
                            detail="Hourly live-run limit reached (protects the demo's "
                                   "API budget). Try again later.")
    _RUN_TIMES.append(now)


@app.get("/api/filings")
def filings():
    return service.filings_index()


@app.get("/api/registry")
def registry():
    return service.registry_json()


@app.get("/api/search")
def search(q: str = ""):
    return service.search_companies(q)


@app.get("/api/health")
def health():
    import os
    return {"api_key": bool(os.environ.get("ANTHROPIC_API_KEY"))}


@app.get("/api/analysis/{ticker}")
def analysis(ticker: str):
    try:
        return service.analyze(ticker)
    except Exception as exc:  # surface a clean error to the UI
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/analysis/{ticker}/run")
def analysis_live(ticker: str):
    _ratelimit()
    try:
        return service.analyze(ticker, force=True)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/ask/{ticker}")
def ask(ticker: str, payload: dict = Body(...)):
    question = (payload or {}).get("question", "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="question is required")
    _ratelimit()
    try:
        return service.ask(ticker, question)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/benchmark")
def benchmark():
    return service.benchmark()


@app.get("/api/grid")
def grid():
    return service.grid()


@app.post("/api/grid/run")
def grid_run():
    _ratelimit()
    try:
        return service.grid_run()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/eval/metrics")
def eval_metrics():
    return service.eval_metrics()


@app.get("/api/qoe/companies")
def qoe_companies():
    return service.qoe_companies()


@app.get("/api/qoe/{ticker}")
def qoe(ticker: str):
    return service.qoe(ticker)


@app.post("/api/qoe/{ticker}/run")
def qoe_run(ticker: str):
    _ratelimit()
    try:
        return service.qoe_run(ticker)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/sources/{ticker}")
def sources(ticker: str):
    return service.sources(ticker)


@app.post("/api/sources/{ticker}/{source}/toggle")
def toggle_source(ticker: str, source: str, payload: dict = Body(default={})):
    try:
        return service.toggle_source(ticker, source, bool((payload or {}).get("connect", True)))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/analyses/{ticker}")
def analyses(ticker: str):
    return service.analyses(ticker)


@app.post("/api/analyses/{ticker}/{analysis_id}/run")
def analysis_run(ticker: str, analysis_id: str):
    try:
        res = service.run_analysis(ticker, analysis_id)
        # analyses are computed off the ledger that is validated to tie out to XBRL
        runlog.record("analysis", ticker.upper(), res.get("name", analysis_id),
                      tied_out=True, note=(res.get("result") or {}).get("note", ""))
        return res
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/runs")
def runs():
    return runlog.list_runs()


@app.get("/api/runs/{run_id}")
def run_detail(run_id: str):
    r = runlog.get_run(run_id)
    if r is None:
        raise HTTPException(status_code=404, detail="run not found")
    return r


@app.get("/api/stream/qoe")
def stream_qoe(ticker: str, period: str = "FY2025"):
    """Stream a Quality-of-Earnings run as it computes (Server-Sent Events).
    The same generator that `materialize_qoe`/`/api/qoe/.../run` drain — one workflow
    definition, streamed live here and materialized there."""
    from ..workflows.qoe import qoe_events

    def gen():
        summary = {}
        for event, payload in qoe_events(ticker, period):
            if event == "result":
                summary = payload or {}
            elif event == "done":
                t5 = summary.get("top5_concentration") or 0
                ug = summary.get("underlying_growth") or 0
                runlog.record("qoe", ticker.upper(), f"Quality of Earnings · {period}",
                              tied_out=summary.get("tied_out"),
                              checks_passed=summary.get("checks_passed"),
                              checks_total=summary.get("checks_total"),
                              note=f"top-5 {round(t5*100)}% · underlying {round(ug*100)}%")
            elif event == "failed":
                runlog.record("qoe", ticker.upper(), f"Quality of Earnings · {period}",
                              status="failed", note=(payload or {}).get("message", ""))
            yield sse(event, payload)
    return StreamingResponse(gen(), media_type="text/event-stream", headers=_SSE_HEADERS)


@app.get("/api/stream/ask")
def stream_ask(ticker: str, q: str, connectors: str = "edgar,erp,crm"):
    """Stream a freeform AI answer about the company (Claude tool-use over XBRL + ledger)."""
    from ..workflows.ask import ask_events
    if not (q or "").strip():
        raise HTTPException(status_code=400, detail="a question is required")
    _ratelimit()  # this one calls the Claude API
    conn = {x for x in connectors.split(",") if x} or None

    def gen():
        tied = None
        for event, payload in ask_events(ticker, q, conn):
            if event == "tie_out":
                tied = (payload or {}).get("passed")
            elif event == "done":
                runlog.record("ask", ticker.upper(), q[:70], tied_out=tied, note="AI answer")
            elif event == "failed":
                runlog.record("ask", ticker.upper(), q[:70], status="failed",
                              note=(payload or {}).get("message", ""))
            yield sse(event, payload)
    return StreamingResponse(gen(), media_type="text/event-stream", headers=_SSE_HEADERS)


@app.get("/api/ask/deck")
def ask_deck():
    from ..workflows.ask import QUESTION_DECK
    return QUESTION_DECK


@app.get("/")
def index():
    # never cache the shell — clients must always get the latest JS
    return FileResponse(_STATIC / "index.html",
                        headers={"Cache-Control": "no-store, must-revalidate"})


app.mount("/", StaticFiles(directory=_STATIC), name="static")
