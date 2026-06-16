# tieout

**Answer-quality evaluation for financial-document extraction — the deterministic verification layer a Matrix-style column-generation agent needs and that citations + LLM-judge grading miss.**

A small agent fills a question's **cell** over a SEC 10-K, then `tieout` runs the cell's *own* cited figures back through a graph of **accounting identities** (assets = liabilities + equity, segments sum to total, net income bridges from pretax) — catching the plausible, individually-cited, **collectively-wrong** answers that a citation layer and an LLM-as-judge wave through.

> Built as an ML/software-engineering application project targeting **Hebbia**. The full research and the reframing rationale are in **[HEBBIA_RESEARCH.md](HEBBIA_RESEARCH.md)**.
>
> *"Tie-out"* is the analyst's term for proving reported numbers reconcile. This automates it.

![tieout UI — overview](docs/img/ui-overview.png)

---

## The pitch, in Hebbia's terms

Hebbia's **Matrix** turns documents into rows, questions into columns, and fills each **cell** with an agent's cited answer ([Hebbia: *Introducing Matrix*](https://www.hebbia.com/blog/introducing-matrix-the-interface-to-agi), [*Multi-Agent Redesign*](https://www.hebbia.com/blog/divide-and-conquer-hebbias-multi-agent-redesign)). Citations prove a number is *on the page*; an LLM judge says it *looks plausible*. Neither proves the cell's numbers are **mutually consistent**, nor that the stated answer actually **follows** from them — so a confident, individually-cited cell can still be collectively wrong (a segment dropped from a roll-up, a balance that omits mezzanine equity, a margin off the wrong base).

Hebbia itself argues good evaluation is **hybrid — deterministic checks + rubric/LLM grading** ([*Evaluating AI Agents*](https://www.hebbia.com/blog/evaluating-ai-agents-a-hybrid-deterministic-and-rubric-based-framework)) — but their deterministic half is schema/format-level. `tieout` adds the missing leg: a **semantic, cross-figure deterministic check** — a *verified cell* — that says *what* fails to reconcile, *by how much*, and *whose fault it is* (extraction error vs. filing inconsistency vs. an incomplete rule), using the filing's own XBRL as ground truth.

## Two layers

**1 · The deterministic verification engine (the core — unchanged).**
Ingest a filing's structured XBRL (via `arelle`, the ground truth) and its raw text; verify any extractor's figures against ~16 hand-authored identities with **rounding-aware interval tolerance**; **propagate** to solve any single missing slot and emit a derived fact *with a provenance chain*; **attribute** every violation three ways.

**2 · The agentic layer (the Hebbia adaptation).**
- **`ColumnAgent`** ([`agent/column.py`](src/tieout/agent/column.py)) — a Claude tool-use agent that fills one Matrix cell: it answers a question over a filing using a grounded retrieval tool, and emits a Retrieval → Definition → Calculation derivation.
- **`verify_cell`** ([`agent/verify.py`](src/tieout/agent/verify.py)) — runs the cell's cited figures back through the real engine: are they retrieval-correct (vs. XBRL)? do the identities they touch **reconcile**? and does the stated answer equal what the identities **derive** from those same figures? No LLM judge involved — the verdict is a pure function of the cell and the ground truth.
- **Hybrid eval** ([`bench/`](src/tieout/bench)) — scores each model's cells three ways (deterministic verification · audit-trail rubric · naive LLM judge) and surfaces the **money metric**: answers the judge accepts that are actually wrong, which the deterministic layer catches.

```
                         ┌───────────────  the agentic layer  ───────────────┐
question + filing ─▶ ColumnAgent ─▶ Cell (answer + cited figures + derivation) ─▶ verify_cell ─▶ Verified cell
                     (Claude tool-use)                                              │   (trusted? + 3-way attribution)
                                                                                    ▼
EDGAR ingest ─▶ Concept Normalizer ─▶ Fact Store ─▶ Constraint Engine ─▶ Attribution ─▶ Report
 (arelle XBRL + text)  (ontology)      (provenanced)  (propagating graph)   (3-way label)     ← the deterministic core
```

## The differentiator, demonstrated

Run a cell's cited figures back through the identities and the answer verifies *itself*:

- **Self-consistency.** Ask for a gross margin; the agent cites revenue and COGS and states 12.84%. `verify_cell` propagates revenue − COGS → gross profit → gross margin **through the identity graph** and confirms the stated answer follows from the cited numbers. Change the stated answer without changing the inputs and it is flagged **`contradicted`** — the multi-step reasoning no longer reconciles.
- **Cross-figure reconciliation + attribution.** If a cell cites a set of balance-sheet figures that don't balance — e.g. it drops $100B of equity — `bs.balance` fails and the engine labels it **`extraction_error`** (XBRL reconciles, the cited figure disagrees) with the exact delta. A filing that breaks the identity *itself* is labelled **`filing_inconsistency`** instead.

This is the verification a column-generation step can't get from a second model grading the first.

## Real results — the deterministic engine (`out/report.md`, generated live)

Across three real FY2025 10-Ks — Costco, Amazon, Kraft Heinz:

| Filing | Extractor | Agree vs XBRL | Disagree | Hard violations |
|---|---|--:|--:|--:|
| Costco | Claude (text) | 13 | 0 | 0 |
| Amazon | Claude (text) | 13 | 0 | 0 |
| Kraft Heinz | Claude (text) | 18 | 0 | 1 (undetermined) |
| Kraft Heinz | Baseline (regex) | 0 | 11 | 1 → **extraction_error** |

1. **Verification with zero false positives** — Claude's figures reconcile cleanly across all three, *including* structures a naive checker would false-flag: Kraft Heinz's **$12M redeemable (mezzanine) equity**, Amazon's **equity-method income below the tax line**, 3M's **discontinued operations** and **segment×product double-counting**. Not crying wolf on real-world complexity is most of the battle.
2. **Discrimination + attribution** — the regex baseline's scale-broken numbers are caught and labeled `extraction_error` automatically, while a strong extractor passes.

**The catch, demonstrated** (`scripts/phase3_real.py`, limited 60k-char context): under a constrained context window, Claude extracted Costco's US ($200.0B) and Canada ($36.9B) segments but **silently dropped Other International ($38.3B)**. Every number it returned was individually correct — an LLM judge passes it — yet `rev.segments_sum` fails by **exactly $38.27B**, attributed to `extraction_error` because the XBRL reconciles. The same mechanism is what `verify_cell` applies to an agent's cell.

## Why this maps to what Hebbia's engineers work on

Hebbia's published engineering centers on (a) retrieval/decomposition (ISD), (b) a multi-agent orchestration platform, (c) **evaluation infrastructure**, and (d) inference/serving economics ([careers](https://builtin.com/company/hebbia-ai), and the blog posts cited above). This project deliberately targets **(c)** — measuring agent answer-quality at scale — with a column-generation agent (b) sitting on top of a deterministic engine. See [HEBBIA_RESEARCH.md](HEBBIA_RESEARCH.md) for the mapping and the options considered.

## Interactive UI

A zero-build web UI (FastAPI + a single self-contained page) lets you explore every layer. Ships with precomputed results, so it runs instantly, offline, **no API key**:

```bash
pip install fastapi "uvicorn[standard]"
python serve.py                             # -> http://localhost:8000
```
Tabs: **Overview · Snapshot · Verified cell · Hybrid eval · Filing explorer · Propagation · Attribution · Localization · Rulebook · Approach.** The **Verified cell** tab runs the column agent live and shows the engine-backed verdict (cited-figure checks, the identities the figures touch, and the self-consistency result); the **Hybrid eval** tab is the deterministic-vs-rubric-vs-judge leaderboard with the money metric. `run.ps1` (Windows) loads your API key so live runs work out of the box.

## Quickstart

```bash
pip install -e ".[xbrl,llm,dev]"                 # arelle + anthropic + pytest
export ANTHROPIC_API_KEY=sk-ant-...               # for the agent + llm_text adapter
PYTHONPATH=src python scripts/demo.py             # -> out/report.md  (COST AMZN KHC)
PYTHONPATH=src python scripts/test_agent.py COST  # fill + verify cells on one filing
PYTHONPATH=src python scripts/run_bench.py        # hybrid-eval leaderboard -> data/bench/results.json
```

`pytest` runs the engine/attribution/extraction suite offline.

## Honest limitations & what I'd do next

- **The verified-cell check binds where the cited figures overlap an identity.** A pure single-figure retrieval (e.g. "net income") has no cross-figure identity to test, so trust there rests on the retrieval check; the identity + self-consistency checks add their value on multi-figure and ratio answers. The richest signal is on derived ratios and roll-ups.
- **Frontier models are accurate on large-cap headline figures with full context**, so the live money metric is most visible on weaker extractors / constrained context. Next: a systematic sweep across cheaper models and deeper line items to quantify catch-rate.
- **Self-correction (stretch).** The recommended build is post-hoc verification; a bounded *verify-in-the-loop* mode (the engine as a control signal that triggers a re-plan) is the natural next step — scoped and deferred in [HEBBIA_RESEARCH.md](HEBBIA_RESEARCH.md).
- **Segment roll-ups** need explicit corporate/unallocated/eliminations terms for conglomerates like 3M (currently declines to evaluate rather than false-flag).

## How it works (engine internals)

Design decisions (details in [ARCHITECTURE.md](ARCHITECTURE.md)):
- **Decimal everywhere; tolerance is interval arithmetic, never `==`.**
- **Reproducible**: every LLM response (extractor *and* agent) is content-addressed cached (SHA-256 of the fully-rendered request) — re-runs are free and bit-identical.
- **Real-world XBRL handling**: 52/53-week **fiscal-year anchoring** (Amazon's FY2025 ends 2026-01-01), dimensional **segment-total de-duplication**, optional-zero terms for mezzanine equity / NCI / equity-method / discontinued ops, and a **soft** severity for the inherently-fragile pretax→net bridge.
- **Honest attribution**: a ground-truth violation is only called a filing inconsistency after ruling out an incomplete rule — most "inconsistencies" in audited filings are really modeling gaps, and the system says so.

## Status

| Phase | What | State |
|---|---|---|
| 0 | Skeleton + arelle/EDGAR XBRL ingest | ✅ |
| 1 | 16-identity registry, source-aware tolerance, interval ÷ | ✅ |
| 2 | Propagating engine (derive + localize) | ✅ |
| 3 | Extraction adapters (Claude / baseline) + response cache | ✅ |
| 4 | Three-way violation attribution | ✅ |
| 5 | Scorecard + discrimination report | ✅ |
| A | Matrix column agent → verified cell (engine-backed `verify_cell`) | ✅ |
| B | Hybrid answer-quality eval (deterministic + rubric + judge, money metric) | ✅ |
| — | Stretch: verify-in-the-loop self-correction; factor-graph engine | planned |

Verified live on 3 real FY2025 filings; engine/attribution/extraction unit tests pass offline.
