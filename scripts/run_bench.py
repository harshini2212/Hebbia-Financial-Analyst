"""Run the hybrid answer-quality eval and save the leaderboard to data/bench/results.json.

  python scripts/run_bench.py                         # all 3 Claude tiers
  python scripts/run_bench.py claude-opus-4-8         # one model
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

from tieout.bench import run_benchmark

models = sys.argv[1].split(",") if len(sys.argv) > 1 else None
res = run_benchmark(models=models, progress=lambda m, q: print(" ", m, q, flush=True))

Path("data/bench").mkdir(parents=True, exist_ok=True)
Path("data/bench/results.json").write_text(json.dumps(res, indent=2), encoding="utf-8")

print("\n=== leaderboard ===")
for m, r in res["results"].items():
    print(f"{r['label']:16} rubric {r['rubric_score']:.2f} | final {r['final_accuracy']:.2f} "
          f"| gap {r['gap']:+.2f} | trusted {r['trusted_rate']:.2f} "
          f"| judge {r['judge_accuracy']:.2f} | money {r['money_metric']}")
