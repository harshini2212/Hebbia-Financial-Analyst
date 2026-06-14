"""Launch the tieout web UI:  python -m tieout.web   ->  http://localhost:8000

  --precompute   analyze the demo filings and cache them, then exit
  --port N       serve on a different port
"""

from __future__ import annotations

import sys

from . import service


def main() -> None:
    if "--precompute" in sys.argv:
        for f in service.filings_index():
            print(f"analyzing {f['ticker']} ...", flush=True)
            service.analyze(f["ticker"], force=True)
        print("done.")
        return

    port = 8000
    if "--port" in sys.argv:
        port = int(sys.argv[sys.argv.index("--port") + 1])

    import uvicorn
    print(f"tieout UI -> http://localhost:{port}")
    uvicorn.run("tieout.web.app:app", host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
