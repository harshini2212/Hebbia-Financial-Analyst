"""FastAPI app: JSON API over the pipeline + serves the single-page UI."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import service

_STATIC = Path(__file__).parent / "static"

app = FastAPI(title="tieout", docs_url="/api/docs")


@app.get("/api/filings")
def filings():
    return service.filings_index()


@app.get("/api/registry")
def registry():
    return service.registry_json()


@app.get("/api/search")
def search(q: str = ""):
    return service.search_companies(q)


@app.get("/api/analysis/{ticker}")
def analysis(ticker: str):
    try:
        return service.analyze(ticker)
    except Exception as exc:  # surface a clean error to the UI
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/analysis/{ticker}/run")
def analysis_live(ticker: str):
    try:
        return service.analyze(ticker, force=True)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/")
def index():
    return FileResponse(_STATIC / "index.html")


app.mount("/", StaticFiles(directory=_STATIC), name="static")
