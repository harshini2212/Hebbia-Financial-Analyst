"""Run the Query Grid through the eval gate -> data/web/grid.json.

  python scripts/run_grid.py        # 3 filings x 6 questions, gated + scored vs XBRL
"""
from __future__ import annotations
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from tieout.web.service import load_api_key  # noqa: E402

load_api_key()
from tieout.evals import run_grid  # noqa: E402

res = run_grid(progress=lambda kind, what: print(" ", kind, what, flush=True))

Path("data/web").mkdir(parents=True, exist_ok=True)
Path("data/web/grid.json").write_text(json.dumps(res, indent=2), encoding="utf-8")

m = res["metrics"]
print("\n=== eval summary ===")
print(f"tie-out {m['tie_out_accuracy']} | halluc {m['hallucination_rate']} | "
      f"abstention-recall {m['abstention_recall']} | grounding {m['grounding_rate']} | "
      f"cost ${m['cost_usd']}")
print("gate:", m["gate"])
print("WROTE data/web/grid.json")
