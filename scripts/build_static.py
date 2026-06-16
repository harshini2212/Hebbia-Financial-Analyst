"""Build a static export of the cached demo into ./public for Vercel / any static host.

The cached filings are plain JSON, so the whole UI (all tabs, 3 companies) works
with no server. Live runs / arbitrary-company search are disabled in this export
(they need Python + arelle + an API key, which shouldn't sit on a public site).

Run locally, then deploy ./public:  python scripts/build_static.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from tieout.web import service  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "public"
DATA = ROOT / "data" / "web"


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "api" / "analysis").mkdir(parents=True)

    shutil.copy(ROOT / "src" / "tieout" / "web" / "static" / "index.html",
                OUT / "index.html")

    filings = [{**f, "cached": True} for f in service.FILINGS
               if (DATA / f"{f['ticker']}.json").exists()]
    (OUT / "api" / "filings.json").write_text(json.dumps(filings), encoding="utf-8")
    (OUT / "api" / "registry.json").write_text(
        json.dumps(service.registry_json()), encoding="utf-8")
    # signal "static" so the UI hides live-run / search and shows a hosted-demo note
    (OUT / "api" / "health.json").write_text(
        json.dumps({"api_key": False, "static": True}), encoding="utf-8")

    # the hybrid-eval leaderboard, if it's been generated
    bench = ROOT / "data" / "bench" / "results.json"
    if bench.exists():
        shutil.copy(bench, OUT / "api" / "benchmark.json")

    for f in filings:
        shutil.copy(DATA / f"{f['ticker']}.json",
                    OUT / "api" / "analysis" / f"{f['ticker']}.json")

    print("static export ->", OUT)
    print("filings:", [f["ticker"] for f in filings])


if __name__ == "__main__":
    main()
