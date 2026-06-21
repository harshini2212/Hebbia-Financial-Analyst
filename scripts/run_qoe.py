"""Headless Quality-of-Earnings: drain the workflow generator -> report -> print + cache.

Shares one definition with the live stream (`/api/stream/qoe`) and the cached recompute.

  python scripts/run_qoe.py            # default AMZN
  python scripts/run_qoe.py MSFT
"""
from __future__ import annotations
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from tieout.workflows.qoe import materialize_qoe  # noqa: E402

ticker = (sys.argv[1] if len(sys.argv) > 1 else "AMZN").upper()


def money(v):
    if v is None:
        return "—"
    a, s = abs(v), ("-" if v < 0 else "")
    if a >= 1e9: return f"{s}${a/1e9:.2f}B"
    if a >= 1e6: return f"{s}${a/1e6:.1f}M"
    return f"{s}${a:,.0f}"


def pct(v):
    return "—" if v is None else f"{v*100:.1f}%"


print(f"Running QoE for {ticker} (EDGAR public + synthetic private)…", flush=True)
rep = materialize_qoe(ticker)
fy = rep["fiscal_year"]
print(f"\n=== Quality of Earnings — {rep['issuer']} ({rep['ticker']}) FY{fy} ===")
print("Synthetic ERP/CRM calibrated to EDGAR/XBRL · CALIBRATED DEMO DATA\n")

print("Tie-out — synthetic ledger rolls up to reported XBRL:")
for c in rep["reconciliation"]:
    if c["fiscal_year"] != fy:
        continue
    print(f"  [{'OK' if c['ties_out'] else 'XX'}] {c['marginal']:32} "
          f"target {money(c['target']):>10}  synthetic {money(c['synthetic']):>10}  ({c['pct']*100:+.2f}%)")
print(f"  => {'ALL TIE OUT' if rep['tied_out'] else 'HAS VARIANCE'}  ({len(rep['reconciliation'])} checks)")
ls = rep["ledger_summary"]
print(f"  ledger: {ls['customers']} customers · {ls['revenue_lines']} revenue lines · "
      f"{ls['ar_invoices']} AR invoices · {ls['pipeline_opps']} pipeline opps")

print("\nFindings the filing hides:")
for i in rep["insights"]:
    print(f"  [{i['severity']:6}] {i['headline']}")

print(f"\nConcentration — top 5 = {pct(rep['top5_concentration'])}:")
for r in rep["top_customers"][:6]:
    print(f"  {r['name']:24} {money(r['revenue']):>9}  {pct(r['pct_of_total']):>6}")
print(f"\nReported growth {pct(rep['reported_growth'])} vs underlying {pct(rep['underlying_growth'])} · "
      f"NRR {pct(rep['net_retention'])} · pipeline coverage "
      f"{pct(rep['pipeline'].get('coverage')) if rep.get('pipeline') else '—'}")

Path("data/web").mkdir(parents=True, exist_ok=True)
Path(f"data/web/qoe_{ticker}.json").write_text(json.dumps(rep, indent=2), encoding="utf-8")
print(f"\nWROTE data/web/qoe_{ticker}.json")
