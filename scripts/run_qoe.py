"""Headless Quality-of-Earnings proof.

Pull a real company's XBRL constraints -> generate an EDGAR-calibrated synthetic
ERP/CRM ledger -> validate it ties out -> run the QoE reconciliation -> print the
findings the consolidated filing hides.

  python scripts/run_qoe.py            # default AMZN
  python scripts/run_qoe.py SNOW
"""
from __future__ import annotations
import dataclasses
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from tieout.synth import build_workspace  # noqa: E402
from tieout.synth.validate import validate  # noqa: E402
from tieout.workflows import run_qoe  # noqa: E402

ticker = (sys.argv[1] if len(sys.argv) > 1 else "AMZN").upper()


def money(v):
    if v is None:
        return "—"
    a = abs(v); s = "-" if v < 0 else ""
    if a >= 1e9: return f"{s}${a/1e9:.2f}B"
    if a >= 1e6: return f"{s}${a/1e6:.1f}M"
    return f"{s}${a:,.0f}"


print(f"Building workspace for {ticker} (EDGAR public + synthetic private)…", flush=True)
ws = build_workspace(ticker)
print(f"\n=== Quality of Earnings — {ws.issuer} ({ws.ticker}) ===")
print("Synthetic ERP/CRM calibrated to EDGAR/XBRL · deterministic by CIK · CALIBRATED DEMO DATA\n")

print("Tie-out — synthetic ledger rolls up to the reported XBRL:")
checks = validate(ws.ledger, ws.constraints)
fy = ws.ledger.years[-1]
for c in checks:
    if c.fiscal_year != fy:
        continue
    mark = "OK " if c.ties_out else "XX "
    print(f"  [{mark}] {c.marginal:32} target {money(c.target):>10}  synthetic {money(c.synthetic):>10}  ({c.pct*100:+.2f}%)")
print(f"  => {'ALL TIE OUT' if all(c.ties_out for c in checks) else 'FAILED'}  ({len(checks)} checks across {len(ws.ledger.years)} years)")
print(f"  ledger: {len(ws.ledger.customers)} customers · {len(ws.ledger.products)} products · "
      f"{len(ws.ledger.revenue_lines)} revenue lines · {len(ws.ledger.ar_invoices)} AR invoices")

rep = run_qoe(ws.constraints, ws.ledger)
print(f"\nFindings the filing hides (FY{rep.fiscal_year}):")
for ins in rep.insights:
    print(f"  [{ins['severity']:6}] {ins['headline']}")
    print(f"            {ins['detail']}  ·  {ins['evidence']}")

print(f"\nCustomer concentration (FY{rep.fiscal_year}) — top 5 = {rep.top5_concentration*100:.0f}% of revenue:")
for r in rep.top_customers[:6]:
    yoy = f"{r['yoy']*100:+.0f}%" if r['yoy'] is not None else "  —"
    flag = "  <- whale" if r['name'] == rep.largest_customer else ""
    print(f"  {r['name']:24} {money(r['revenue']):>9}  {r['pct_of_total']*100:5.1f}%  {yoy:>6}{flag}")

print(f"\nGrowth bridge: reported {rep.reported_growth*100:.0f}% vs underlying (ex-largest) "
      f"{rep.underlying_growth*100:.0f}%" if rep.reported_growth is not None and rep.underlying_growth is not None else "")
mb = rep.margin_bridge
if mb.get("share_prior") is not None:
    print(f"Margin/mix: {mb['lowest_margin_line']} (margin {mb['lowest_margin']*100:.0f}%) "
          f"share {mb['share_prior']*100:.0f}% -> {mb['share_now']*100:.0f}%")
wc = rep.working_capital
if wc.get("dso"):
    print(f"Working capital: DSO {wc.get('dso_prior') or '—'} -> {wc['dso']} days  ·  "
          f"DIO {wc.get('dio') or '—'}  ·  DPO {wc.get('dpo') or '—'}")

# persist the artifact so the UI can serve it offline
out = dataclasses.asdict(rep)
out["tied_out"] = ws.tied_out
out["constraints"] = [dataclasses.asdict(p) for p in ws.constraints.periods]
out["ledger_summary"] = {"customers": len(ws.ledger.customers),
                         "products": [dataclasses.asdict(p) for p in ws.ledger.products],
                         "revenue_lines": len(ws.ledger.revenue_lines),
                         "ar_invoices": len(ws.ledger.ar_invoices),
                         "anomalies": ws.ledger.anomalies}
Path("data/web").mkdir(parents=True, exist_ok=True)
Path(f"data/web/qoe_{ticker}.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
print(f"\nWROTE data/web/qoe_{ticker}.json")
