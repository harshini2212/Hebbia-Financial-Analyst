"""FastAPI app: JSON API over the pipeline + serves the single-page UI."""

from __future__ import annotations

import collections
import json
import os
import time
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import service


def sse(event: str, data: dict) -> str:
    """One Server-Sent-Events frame: `event: <type>` + `data: <json>` + blank line."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


# Headers that keep SSE flushing incrementally (no proxy/CDN buffering).
_SSE_HEADERS = {"Cache-Control": "no-cache", "Connection": "keep-alive",
                "X-Accel-Buffering": "no"}

_STATIC = Path(__file__).parent / "static"

app = FastAPI(title="tieout", docs_url="/api/docs")

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


@app.get("/api/stream/qoe")
def stream_qoe(ticker: str, period: str = "FY2025"):
    """Stream a Quality-of-Earnings run as it computes (Server-Sent Events).
    Phase 1: a stub that proves the transport before the workflow is wired in."""
    def gen():
        yield sse("run_started", {"run_id": "r_stub", "workflow": "qoe",
                                  "company": ticker, "period": period})
        yield sse("step", {"id": "pull_public", "label": "Pull filed marginals from XBRL",
                           "status": "running"})
        yield sse("step", {"id": "pull_public", "label": "Pull filed marginals from XBRL",
                           "status": "done"})
        yield sse("tie_out", {"check": "us-gaap:Revenues", "value": 716900000000,
                              "variance": 0.0, "passed": True})
        yield sse("done", {"run_id": "r_stub", "checks_passed": 1, "elapsed_ms": 10})
    return StreamingResponse(gen(), media_type="text/event-stream", headers=_SSE_HEADERS)


@app.get("/")
def index():
    return FileResponse(_STATIC / "index.html")


app.mount("/", StaticFiles(directory=_STATIC), name="static")
