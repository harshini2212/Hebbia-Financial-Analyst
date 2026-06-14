# tieout

**A deterministic trust layer for LLM-extracted financials: it verifies figures pulled from SEC 10-K filings against accounting identities, catching errors an LLM-as-judge rubber-stamps.**

> *"Tie-out"* is the analyst's term for proving reported numbers reconcile. This automates it.

---

## The pitch

An AI analyst that extracts numbers from filings has one catastrophic failure mode: a **plausible, individually-correct-looking figure that is collectively wrong** — a segment dropped from a roll-up, a balance that omits mezzanine equity, a margin off the wrong base. An LLM-as-judge checks each number for plausibility and waves these through. `tieout` doesn't: it runs the extracted figures through a graph of **deterministic accounting identities** (assets = liabilities + equity, segments sum to total, net income bridges from pretax), and when something doesn't reconcile it says *what*, *by how much*, and *whose fault it is* — extraction error vs. filing inconsistency vs. an incomplete rule — using the filing's own XBRL as ground truth. For an autonomous analyst like Rogo's Felix, this is the guardrail that makes the numbers trustworthy.

## What it does

- **Ingests** real filings from SEC EDGAR: structured XBRL (via `arelle`, the ground truth) *and* the raw filing text (what an LLM reads).
- **Verifies** any extractor's figures against 16 hand-authored identities with **rounding-aware interval tolerance** (driven by XBRL `decimals`), source-aware (tight for ground truth, looser for prose).
- **Propagates**: solves any single missing slot in an identity and emits a derived fact *with a provenance chain* — so it fills gaps too (it computes Costco's unreported gross margin from revenue − COGS).
- **Attributes** every violation three ways, disambiguated by ground truth: `extraction_error` · `filing_inconsistency` · `constraint_model_error`.
- **Reports** a discrimination table (Claude vs. a regex baseline vs. ground truth) with the attribution breakdown.

## Real results (`out/report.md`, generated live)

Across three real FY2025 10-Ks — Costco, Amazon, Kraft Heinz:

| Filing | Extractor | Agree vs XBRL | Disagree | Hard violations |
|---|---|--:|--:|--:|
| Costco | Claude (text) | 13 | 0 | 0 |
| Amazon | Claude (text) | 13 | 0 | 0 |
| Kraft Heinz | Claude (text) | 18 | 0 | 1 (undetermined) |
| Kraft Heinz | Baseline (regex) | 0 | 11 | 1 → **extraction_error** |

Two things this demonstrates:

1. **Verification with zero false positives** — Claude's figures reconcile cleanly across all three, *including* structures a naive checker would false-flag: Kraft Heinz's **$12M redeemable (mezzanine) equity**, Amazon's **equity-method income below the tax line**, 3M's **discontinued operations** and **segment×product double-counting**. Not crying wolf on real-world complexity is most of the battle.
2. **Discrimination + attribution** — the regex baseline's scale-broken numbers are caught and labeled `extraction_error` automatically, while a strong extractor passes.

**The catch, demonstrated** (`scripts/phase3_real.py`, limited 60k-char context): under a constrained context window, Claude extracted Costco's US ($200.0B) and Canada ($36.9B) segments but **silently dropped Other International ($38.3B)**. Every number it returned was individually correct — an LLM judge passes it — yet `rev.segments_sum` fails by **exactly $38.27B**, attributed to `extraction_error` because the XBRL reconciles. With a full-statements window Claude gets all three, so in practice the layer is a deterministic verifier that *also* catches real omissions when context or the model is weaker.

## How it works

```
EDGAR ingest ─▶ Concept Normalizer ─▶ Fact Store ─▶ Constraint Engine ─▶ Attribution ─▶ Report
 (arelle XBRL +   (canonical concept   (provenanced,   (propagating graph:   (3-way label
  filing text)     ontology + tags)     Decimal snap)   derive + localize)    via ground truth)
        ▲                                    ▲
   Extractors ──────────────────────────────┘
   (xbrl_direct = truth · llm_text = under test · baseline = floor)
```

Design decisions worth noting (details in [ARCHITECTURE.md](ARCHITECTURE.md)):
- **Decimal everywhere; tolerance is interval arithmetic, never `==`.**
- **Reproducible**: every LLM response is content-addressed cached (SHA-256 of the *fully-rendered* request) — re-runs are free and bit-identical.
- **Real-world XBRL handling**: 52/53-week **fiscal-year anchoring** (Amazon's FY2025 ends 2026-01-01), dimensional **segment-total de-duplication**, optional-zero terms for mezzanine equity / NCI / equity-method / discontinued ops, and a **soft** severity for the inherently-fragile pretax→net bridge.
- **Honest attribution**: a ground-truth violation is only called a filing inconsistency after ruling out an incomplete rule — most "inconsistencies" in audited filings are really modeling gaps, and the system is built to say so.

## Interactive UI

A zero-build web UI (FastAPI + a single self-contained page) lets you explore every
layer — discrimination table, per-identity reconciliation, the propagation cascade,
attribution, localization, and the constraint registry — across the three filings.
Ships with precomputed results, so it runs instantly, offline, **no API key**:

```bash
pip install fastapi "uvicorn[standard]"
python serve.py                             # -> http://localhost:8000
```
Tabs: **Overview · Filing explorer · Propagation · Attribution · Localization · Registry · About.**
A "Run live" button re-runs a filing against EDGAR + Claude (needs a key).

## Quickstart (3 commands)

```bash
pip install -e ".[xbrl,llm,dev]"           # arelle + anthropic + pytest
export ANTHROPIC_API_KEY=sk-ant-...         # for the llm_text adapter
PYTHONPATH=src python scripts/demo.py       # -> out/report.md  (COST AMZN KHC)
```

More: `python scripts/phase0_real.py COST` (ground-truth reconciliation) · `python scripts/phase2_real.py COST` (propagation + localization) · `python scripts/phase3_real.py COST` (the limited-context catch) · `pytest` (10 tests, all offline).

## Honest limitations & what I'd do next

- **Frontier models are accurate on large-cap headline figures with full context**, so the live "money metric" is most visible on weaker extractors / constrained context. Next: a systematic eval across cheaper models and harder, deeper line items to quantify catch-rate.
- **Gold set**: ~30–50 hand-verified Q&A (loader already gates answers as unverified until confirmed) + an LLM-judge baseline to measure caught-vs-passed directly. (Costco drafts in `data/gold/`.)
- **Segment roll-ups** need explicit corporate/unallocated/eliminations terms to handle conglomerates like 3M (currently declines to evaluate rather than false-flag).
- **Stretch**: XBRL calculation-linkbase harvester (auto-derive filing-specific constraints) and a probabilistic factor-graph engine for per-figure confidence.

## Status

| Phase | What | State |
|---|---|---|
| 0 | Skeleton + arelle/EDGAR XBRL ingest | ✅ |
| 1 | 16-identity registry, source-aware tolerance, interval ÷ | ✅ |
| 2 | Propagating engine (derive + localize) | ✅ |
| 3 | Extraction adapters (Claude / baseline) + response cache | ✅ |
| 4 | Three-way violation attribution | ✅ |
| 5 | Scorecard + discrimination report | ✅ |
| — | Stretch: factor-graph engine, linkbase harvester | planned |

10 unit tests pass; verified live on 3 real FY2025 filings.
