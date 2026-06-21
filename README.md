# tieout

**Public + private financial intelligence, reconciled and tied out.** A workspace that fuses a company's real SEC filings (EDGAR/XBRL) with a *calibrated synthetic* ERP/CRM ledger, then reconciles the consolidated public numbers against the granular private ones to surface what the filing hides — customer concentration, reported-vs-underlying growth, one-time revenue, DSO drift. Every figure rolls up to the public total, validated by a deterministic tie-out.

> Built as an ML/software-engineering application project targeting **Hebbia** — auditable, accurate answers over financial documents where one wrong number kills a deal. Design rationale in **[HEBBIA_RESEARCH.md](HEBBIA_RESEARCH.md)**.

## The two ideas that make it work

1. **The synthetic engine is constrained generation, not fabrication.** It fits a private ledger (customers, SKUs, AR aging, CRM pipeline, cohorts) to the company's real XBRL marginals via **iterative proportional fitting** — so it *sums back to the reported numbers*, deterministic by CIK. Always framed as **calibrated demo data**, never real. The same tie-out harness that checks real extraction validates the synthetic generation — one trust layer, two jobs.
2. **The reconciliation join is the IP.** Rolling the granular ledger up to each public figure and exposing the variance is what produces *"top-5 = 30% of revenue (undisclosed); underlying growth 10% not 12%"* — the analysis that only exists when you join public + private.

**Connectors:** EDGAR (public) · Synthetic ERP/CRM (demo) · Merge (production) all sit behind one `SourceAdapter` interface — demo on synthetic, a real customer connects Merge, the workflows never change.

### Proven on real Amazon XBRL
```
Tie-out (synthetic ledger → reported XBRL):  17/17 across 3 years —
  revenue $716.9B, the 3 real segments, COGS, AR $67.7B all reconcile exactly.
QoE findings:  top-5 = 30% · reported 12% vs underlying 10% · $22.9B one-time ·
  DSO 31.7→34.5d · DPO 124.8 (Amazon's real ratios) · NRR 111% · pipeline cov 112%.
```

**Run it live** — opening the workspace *streams the run*: the trace ticks step-by-step, tie-out checks turn green one at a time, reconciliation rows + KPI cards fill as they compute, findings land last.

```bash
python serve.py                  # -> http://localhost:8050 · open the QoE workspace
RUN_PACE=0.3 python serve.py     # paced for a live demo (drips the stream)
python scripts/run_qoe.py AMZN   # headless: the same workflow, printed
```

Opening a workflow hits an SSE endpoint (`/api/stream/qoe`); the *same generator* materializes the cached `/api/qoe/{ticker}` and the static export — one workflow definition, no fork. **Deploy:** `render.yaml` (Render) or the `Dockerfile` (Cloud Run) — a single FastAPI origin serves UI + streaming API. Tabs: Workflows · QoE workspace · Companies · Sources/connectors · Runs · Artifacts · Evals.

---

The **verification/trust layer** underneath — the deterministic accounting-identity engine, the eval-gate that does the same job for *extraction* — is documented below.

![tieout — workspace](docs/img/ui-overview.png)

---

## The thesis (three non-obvious bets)

1. **Eval is a gate, not a report.** Every cell passes a deterministic gate *before* it reaches the grid: does the answer **tie out to XBRL**, and do its cited figures **reconcile** against accounting identities? Failures are re-run with an escalated model — the eval layer is in the hot path, not a dashboard at the end.
2. **XBRL is free ground truth.** SEC filings ship machine-readable XBRL facts alongside the prose. That gives an exact answer key for a huge class of numeric questions — and it's what makes a *credible* eval engine (and measurable abstention) possible. Most "chat with your PDF" projects have no ground truth at all.
3. **A cell is an agent.** Each `(document × question)` cell is an independent, tiered agent run — cheap model first, escalate on failure — which turns the grid into a fan-out / cost-control problem, not a spreadsheet.

The deterministic engine that scores all this — `tieout`'s accounting-identity verifier — is the spine, not a side tab.

## Architecture — the eval gate in the loop

```
EDGAR + XBRL ─▶ Plan · Route ─▶ Column agent ─▶  ┌── EVAL GATE ──┐ ─▶ Grid + metrics
(ground truth)  (decompose,     (tiered tool-use, │ tie-out to    │    (verified cells,
                 route numeric)  grounded figures) │ XBRL + verify │     scored vs XBRL)
                                                   │ identities    │
                                                   └──────┬────────┘
                                       ✓ accept · ↑ re-run on a stronger tier · ! hold low-confidence
```

- **`agent/orchestrator.py`** — the gate: `plan → route → generate → verify → gate`, with model tiering (Haiku → Sonnet → Opus), per-attempt cost, and a structured trace.
- **`agent/verify.py`** — the deterministic verifier (retrieval vs XBRL, accounting-identity reconciliation with three-way attribution, self-consistency).
- **`evals/grid.py` · `evals/metrics.py`** — the gated Query Grid (bounded in-process fan-out) + XBRL-grounded metrics.
- **constraint engine** (`constraints.py`, `engine/`, `attribution/`, `registry.py`) — 16 accounting identities, propagation, attribution. Unchanged; consumed by the gate.

## Real results (the gated grid, `data/web/grid.json`, generated live)

3 filings (Costco, Amazon, Kraft Heinz) × 6 analyst questions, every cell gated against XBRL:

| Metric | Result |
|---|--:|
| **Tie-out accuracy** (answers matching XBRL) | **18/18 = 100%** |
| **Hallucination rate** (unsupported/incorrect figures) | **0%** |
| **Grounding rate** (cited figures tracing to XBRL) | **100%** |
| **Resolved on the cheap tier** (Haiku) | **17/18** |
| **Escalations** (re-run on a stronger model) | **1** |
| **Total grid cost** | **≈ $0.13** |

The one escalation is the honest headline: on **Kraft Heinz's operating margin** (a negative margin — an operating *loss*), Haiku's first answer didn't tie out to the XBRL-derived −18.7%. The gate caught it, escalated to Sonnet, which got it right — *verified*, not shipped wrong. That's the loop doing its job on real data, not a fabricated demo. (And on a deliberately-wrong ground-truth value, a cell escalates Haiku→Sonnet→Opus and ends **low-confidence** — never a confident wrong answer.)

## The UI — an audit console, not a chat box

A light, data-dense single-page app (FastAPI + one self-contained page), built to read as an eval/ops tool:

- **Query Grid** — filings × questions; each cell shows the value, a status chip (verified / low-confidence / abstained), the model tier it resolved on, and an escalation marker. Click a cell →
- **Cell inspector** — the full **reasoning trace** (plan → route → generate → verify → gate), the **gate & tiering** attempts (per-model cost/tokens, the escalation), the **grounding** (cited figures vs official XBRL), and the **identities** the figures touch.
- **Eval** — tie-out / hallucination / grounding / escalation, gate outcomes + cost, and a model-comparison leaderboard (the hybrid deterministic + rubric + LLM-judge eval, with the *money metric*: answers the judge accepts that are actually wrong).
- **Rulebook** — the accounting-identity registry. **Architecture** — the layered system with an honest *built vs. roadmap* table.

Ships with precomputed results, so it runs instantly, offline, **no API key**:

```bash
pip install fastapi "uvicorn[standard]"
python serve.py                                   # -> http://localhost:8000
```

## What's built vs. roadmap (honest scope)

**Built:** EDGAR + XBRL ground-truth store · the eval gate (tie-out + verify, re-run-on-fail, tiering, cost) · the Query Grid with bounded in-process fan-out · XBRL-grounded metrics · the constraint engine + attribution.

**Roadmap (labeled as such, not stubbed):** distributed fan-out (Celery/Redis — same interface, swap the executor) · hybrid prose retrieval (BM25 + embeddings + cross-encoder rerank) with span-level prose citations (today retrieval is structured-XBRL) · a Neo4j knowledge graph for multi-hop / cross-filing questions · multimodal table/chart extraction.

## Run it

```bash
pip install -e ".[xbrl,llm,dev]"                  # arelle + anthropic + pytest
export ANTHROPIC_API_KEY=sk-ant-...               # for live runs (cached results need no key)
PYTHONPATH=src python scripts/run_grid.py         # gated grid -> data/web/grid.json
PYTHONPATH=src python scripts/run_bench.py         # model leaderboard -> data/bench/results.json
python serve.py                                    # the UI
pytest                                             # engine/attribution/extraction suite (offline)
```

## Why this maps to what Hebbia works on

Hebbia's published engineering centers on retrieval/decomposition, a multi-agent orchestration platform, **evaluation infrastructure**, and inference economics. This project targets the eval problem head-on — *measuring agent answer-quality at scale, with ground truth* — and frames it the way they do: accuracy and auditability in regulated finance, with the eval as a gate rather than an afterthought. See [HEBBIA_RESEARCH.md](HEBBIA_RESEARCH.md).

## Engine internals

Design decisions (details in [ARCHITECTURE.md](ARCHITECTURE.md)): Decimal everywhere, interval-arithmetic tolerance (never `==`); every LLM response content-addressed cached (reproducible, free re-runs); real-world XBRL handling (52/53-week fiscal years, segment de-duplication, mezzanine equity / NCI / equity-method / discontinued-ops optional-zero terms); honest attribution (a "filing inconsistency" is only declared after ruling out an incomplete rule).

## Status

| Phase | What | State |
|---|---|---|
| 0–5 | XBRL ingest · 16-identity registry · propagating engine · extraction adapters · attribution · scorecard | ✅ |
| A | Column agent → engine-backed cell verification (`verify_cell`) | ✅ |
| B | Hybrid answer-quality eval (deterministic + rubric + judge, money metric) | ✅ |
| C | **Eval-gate orchestrator** (tie-out + verify, re-run-on-fail, model tiering, cost) | ✅ |
| D | **Gated Query Grid + XBRL-grounded metrics + audit-console UI** | ✅ |
| — | Roadmap: distributed fan-out · hybrid prose retrieval · graph · multimodal | planned |

Engine/attribution/extraction unit tests pass offline; grid + gate verified live on 3 real FY2025 filings.
