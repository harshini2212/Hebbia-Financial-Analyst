# Hebbia — research & adaptation plan

> Purpose of this doc: (Part I) a sourced briefing on Hebbia — product, architecture,
> the hard problems they say they care about, what their engineers build, and the
> sectors they serve; and (Part II) a concrete plan to adapt this repo's deterministic
> financial-verification harness (`tieout`) into a Hebbia-shaped project for an
> ML/software-engineering application.
>
> Everything in Part I is cited inline. Where a claim comes from secondary coverage or
> company marketing rather than Hebbia's own engineering writing, it's flagged. A
> consolidated **"What I'm not sure about"** list is at the end of Part I.

---

# Part I — Research

## 0. One-paragraph orientation

Hebbia is an enterprise AI company (founded 2020 by George Sivulka; backed by Andreessen
Horowitz and Peter Thiel; raised a $130M Series B in 2024) whose flagship product, **Matrix**,
is an agentic interface for doing analyst-grade work over large document sets — filings,
contracts, transcripts, models. Its pitch is "beyond chat": instead of a back-and-forth
chatbot, you build a **grid** where the system runs a swarm of agents to read every document
and answer every question, with citations. It sells primarily into finance, with expansion
into legal/consulting and government. ([a16z](https://a16z.com/announcement/investing-in-hebbia/),
[Bloomberg](https://www.bloomberg.com/news/articles/2024-07-08/hebbia-raises-130-million-for-ai-that-helps-firms-answer-complex-questions),
[Wikipedia](https://en.wikipedia.org/wiki/Hebbia))

## 1. The product: Matrix and its grid UI

Matrix presents AI work as a **spreadsheet-like grid** rather than a chat thread:

- **Documents are rows.** Each file you upload (a 10-K, a credit agreement, a transcript)
  becomes a row.
- **Questions/prompts are columns.** Each column is one thing you want extracted or
  computed from *every* document — "EBITDA definition," "change-of-control clause,"
  "FY revenue."
- **Agent outputs are cells.** Each cell is one agent's answer for one (document, question)
  pair, with citations back to the source.

a16z describes Matrix as letting users "build AI agents that complete end-to-end tasks,
instead of just chatting back and forth," processing "structured and unstructured data
across multiple files and formats" and returning "answers with citations" in a
"spreadsheet-like interface" that shows "sourcing and individual steps."
([a16z](https://a16z.com/announcement/investing-in-hebbia/)) The canonical demo framing is
"read these 500 credit agreements and extract the EBITDA definition for each" — i.e. AI as a
**parallel processor** filling a structured grid, not a single chat answer.
([skywork deep-dive](https://skywork.ai/skypage/en/hebbia-ai-deep-dive-guide/1976843429248823296),
[Dynamic Business](https://dynamicbusiness.com/ai-tools/hebbia-revolutionizes-ai-interface-meet-the-matrix.html))

**Trust / citations.** Every answer is meant to be traceable to its source. a16z's phrasing
is "answers with citations" showing "sourcing and individual steps"; Hebbia's own *Introducing
Matrix* post frames the core problem as trust — "even with citations to sources and the most
capable models, users couldn't trust generation" — which is the gap their decomposition +
sourcing is meant to close.
([Introducing Matrix](https://www.hebbia.com/blog/introducing-matrix-the-interface-to-agi))
The term **"Verifiable Fact Layer"** (clickable citations that jump to the exact sentence/table
in the source PDF) appears in *secondary* write-ups rather than Hebbia's own engineering posts —
treat it as descriptive, not an official component name.
([skywork](https://skywork.ai/skypage/en/hebbia-ai-deep-dive-guide/1976843429248823296))

> **The gap this project targets.** Hebbia's trust mechanism is *citation-level* ("this number
> is on page 47") plus *eval-time* grading. It is **not** *cross-figure semantic verification*
> ("do these extracted numbers actually reconcile against accounting identities?"). A cell can
> be individually cited and still be collectively wrong — a dropped segment in a roll-up, a
> margin computed off the wrong base. That's exactly what `tieout` checks. See Part II.

## 2. The architecture: an agent "swarm" + Iterative Source Decomposition

### 2.1 Why multi-agent

Hebbia moved Matrix from a single agent to a **multi-agent** design because the single-agent
approach suffered from overloaded tooling (tool misinterpretation), context-window saturation
(the "needle in the haystack" problem), and one monolithic prompt bundling all instructions.
([Multi-Agent Redesign](https://www.hebbia.com/blog/divide-and-conquer-hebbias-multi-agent-redesign))

### 2.2 The orchestrator pattern (this part is well-documented and precise)

- The **Orchestrator** receives the user request and dispatches work to specialized subagents.
- Crucially, **the orchestrator never directly invokes any tools** — it "simply passes a
  text-based 'detailed objective' to a chosen subagent," and is "only provided the subagent's
  name and a description of its capabilities. It is not aware of or responsible for the tools
  used internally by each subagent."
- Context is isolated via **"strongly typed hierarchical contexts"**: each message is tagged
  with the agent that produced it, so subagents can filter for the messages relevant to them.

([Multi-Agent Redesign](https://www.hebbia.com/blog/divide-and-conquer-hebbias-multi-agent-redesign))

### 2.3 The named agents — and a naming caveat

Different Hebbia posts name the subagents differently, and some names in circulation come from
product copy / secondary coverage rather than the engineering blog. Being precise:

| Role (as commonly listed) | What it does | Where the name actually appears |
|---|---|---|
| **Orchestrator** | Delegates a text "objective" to subagents; never calls tools itself | Hebbia blog (primary) |
| **Planning** | Decomposes a research goal into executable subtasks | Hebbia *Deeper* post (primary) |
| **Retrieval** | Surfaces the most relevant data from public & private sources via tailored indexing/search | Hebbia *Deeper* post (primary) |
| **Column Generation** | Decomposes a request into discrete fields and adds them as Matrix columns | Product copy / secondary summaries |
| **Information Synthesis** / **Output Agent** | Assembles cells into a well-formatted, cited output; writes section-by-section ("multi-hop") to stay under context limits | "Output Agent" in Hebbia blog (primary); "Information Synthesis" in secondary summaries |
| **Read Matrix subagent** | Selects which columns to read from the Matrix | Hebbia *Multi-Agent Redesign* post (primary) |
| **Context Distillation** | Compresses information to its core, reported as ">90%" context reduction while keeping recall | Hebbia *Deeper* post (primary) |

Sources: [Multi-Agent Redesign](https://www.hebbia.com/blog/divide-and-conquer-hebbias-multi-agent-redesign),
[A Look Inside Hebbia's "Deeper" Research Agent](https://www.hebbia.com/blog/inside-hebbias-deeper-research-agent).

> **Flag:** the tidy five-name taxonomy "Orchestrator / Planning / Retrieval / Column Generation /
> Information Synthesis" is a reasonable *synthesis* of how Hebbia describes the system, but it is
> not a verbatim list from a single Hebbia source. "Column Generation" and "Information Synthesis"
> in particular read as product-level descriptions; the engineering posts use "Read Matrix
> subagent," "Output Agent," and "Context Distillation."

### 2.4 Iterative Source Decomposition (ISD)

ISD is Hebbia's positioning *against* vanilla RAG. The consistent description across sources:
rather than chunk-and-embed, ISD **iteratively decomposes a complex query and reasons over
full documents** — preserving document context, structure, and formatting, analyzing line by
line, and leaving an **audit trail to the precise source** supporting each claim. It is
**model-agnostic** ("can run on any LLM — OpenAI, Anthropic, Google").
([Sacra — Danny Wheller interview](https://sacra.com/research/danny-wheller-hebbia-vertical-vs-horizontal-enterprise-ai/),
[Deeper Research Agent](https://www.hebbia.com/blog/inside-hebbias-deeper-research-agent))

> **Flag:** the *Introducing Matrix* post talks about "world-class decomposition" and breaking
> tasks into single steps but does not use the literal term "Iterative Source Decomposition";
> the named "ISD" term shows up mostly in Hebbia's resource pages and third-party coverage. The
> underlying idea (decompose-and-reason over full documents, beyond chunking, with citations) is
> consistent everywhere; the exact branded pipeline internals are not publicly detailed.

## 3. The hard problems Hebbia says it cares about

### 3.1 Measuring agent answer-quality at scale (hybrid evaluation) — the most relevant one

This is the problem this project leans into. Hebbia has written two detailed posts. The core
difficulty: **"when an agent generates a visualization, summarizes a document, or coordinates a
multi-step analysis, there's rarely a single 'correct' answer."**
([Hybrid eval framework](https://www.hebbia.com/blog/evaluating-ai-agents-a-hybrid-deterministic-and-rubric-based-framework))

Their methodology is explicitly **hybrid: deterministic checks + rubric-based LLM grading.**

- **Deterministic checks** handle "verifiable properties … such as schema validation, required
  field presence, data type correctness, and formatting compliance" — "fast, reliable, and
  consistent."
- **Rubric-based LLM grading** assesses "outcomes or processes for properties like coherence,
  completeness, and domain-appropriateness," distinguishing *outcome-focused* grading (simple
  agents) from **"process-focused criteria" that evaluate reasoning paths** for multi-step tools.
- **Rubric design rules:** atomicity (one diagnosable issue per criterion), **binary pass/fail**
  (improves LLM consistency), specificity, non-redundancy, multi-level standards (required = SLA;
  extra = advanced capability), authored with **in-house financial domain experts** and refined
  as new failure modes surface in production.
- **Variance control:** run multiple samples and **multiple grading passes**, aggregate, to
  "reduce noise from grader non-determinism and surface rubric ambiguity."

The companion post, *Who Evaluates the Evaluator*, adds the statistics:

- Each **criterion × question is a separate LLM call**, scored on a **Likert 1–5**, kept
  independent.
- They take the grader's **log-probabilities** for the score token and exponentiate to linear
  probabilities, then normalize/sum into weighted scores.
- They **repeat the whole evaluation N=50 times** ("running this just once is akin to flipping a
  coin and calling it science").
- Significance via a **two-sided permutation test, α = 0.05, 10,000 iterations**.
- Validated against **"human-labeled examples from former hedge fund analysts"** with "strong
  alignment."

([Who Evaluates the Evaluator](https://www.hebbia.com/blog/who-evaluates-the-evaluator-reaching-autonomous-consensus-on-agentic-outputs))

They also publish a **Financial AI Benchmark**: 600+ real finance questions across investment
banking, PE, credit, and public equities, grouped into **Extraction / Summarization / Reasoning**.
([Financial AI Benchmark](https://www.hebbia.com/blog/which-model-will-give-me-the-edge))

> **Why this matters for the project:** Hebbia's *own* framework says deterministic checks are
> one half of trustworthy evaluation — but their deterministic half is **schema/format-level**.
> A deterministic **accounting-identity** check (does this set of numbers reconcile?) is a
> *stronger* deterministic signal that their published framework doesn't cover. That's the wedge.

### 3.2 Context engineering across many agents

A single Matrix task "can use millions of tokens to process thousands of pages and coordinate
analysis from dozens of agents," so they must control "what each agent sees, shares, and
retains." Three named techniques:

1. **Role definition** — each agent gets only the context relevant to its bounded task.
2. **Selective communication** — agents pass *summarized* results downstream, not full outputs.
3. **Context distillation** — a dedicated agent compresses to principal components, reported as
   **">90%" context reduction** while maintaining recall.

Plus the orchestration system, **"Maximizer,"** which they say handles **"billions of tokens per
day."** ([Deeper Research Agent](https://www.hebbia.com/blog/inside-hebbias-deeper-research-agent))

### 3.3 Cost-effective serving of long-context, agentic workloads

Hebbia's *Hidden Economics of LLM Inference* is unusually concrete:

- **HBM bandwidth is the bottleneck** in the decode phase: each step "must read the full KV
  cache," and at 80k tokens "tens of gigabytes per step move through HBM" (vs. prefill, which is
  compute-bound and keeps tensor cores busy).
- **Continuous batching breaks down** for long context: moving from **1 to 3 concurrent requests
  inflated end-to-end latency by 13–40×**, because one 80k-context decode already pushes the HBM
  ceiling, "leaving almost no headroom to share."
- **Cost is driven by demand volatility, not throughput.** Via Erlang-C queueing, at 95%
  utilization wait times are ~**20×** the 50%-utilization baseline; **bursty demand can need 8×
  more GPUs** than smooth demand at the same average volume.
- **API providers win on demand pooling**: a provider serving 1,000 customers sees ~**1/30th**
  the relative standard deviation of a single customer, so self-hosting is "unviable for many
  workload profiles" unless the coefficient of variation is below ~0.3–0.4.

([Hidden Economics of LLM Inference](https://www.hebbia.com/blog/the-hidden-economics-of-llm-inference))

## 4. What Hebbia's ML & software engineers actually work on

From job postings and careers pages (so: aspirational/representative, not internal docs):

- **Document intelligence / ingestion & retrieval:** "a high-scale document build system"
  enabling "constant-time indexing regardless of data volume"; "elastically scaling data
  representation systems powering private data retrieval"; performance tuning "across millions
  of documents."
- **Agent platform:** "custom multi-agent frameworks powering research and copiloting
  interfaces"; **"a distributed, asynchronous DAG orchestrator managing LLM inference at scale
  with live graph mutations"** (this is the engineering substrate under the swarm).
- **Production agents for the domain:** agents "solving buy-side diligence and M&A analysis
  problems."
- **Stack:** Python/Java/Go; AWS; Kafka, ElasticSearch, PostgreSQL, Redis; workflow orchestration
  (Airflow/Temporal/Prefect); strong distributed-systems + performance focus. ML roles list
  Python + PyTorch/TensorFlow.

([Platform Engineer, Agents](https://builtin.com/job/platform-engineer-agents/6606050),
[Hebbia on Built In](https://builtin.com/company/hebbia-ai),
[ML Engineer (Wellfound)](https://wellfound.com/jobs/1114362-machine-learning-engineer-at-hebbia-ai))

In short, ML/SWE work clusters into: (a) retrieval/decomposition (ISD), (b) the multi-agent
orchestration platform, (c) **evaluation infrastructure** (§3.1), and (d) inference/serving
economics (§3.3). This project deliberately targets (c), with hooks into (a)/(b).

## 5. Sectors served

- **Finance (primary).** Company materials claim a large share of the top asset managers by AUM
  as customers and assets "managed with" the platform in the trillions; secondary coverage names
  blue-chip funds/banks. Treat specific share/AUM numbers as **company-stated**.
  ([a16z](https://a16z.com/announcement/investing-in-hebbia/),
  [Built In](https://builtin.com/company/hebbia-ai),
  [Sacra](https://sacra.com/c/hebbia/))
- **Legal & consulting.** Document-heavy review work (e.g. credit-agreement review time cut
  sharply). ([Sacra](https://sacra.com/c/hebbia/))
- **Government / military.** Expansion into the public sector, including the **US Air Force**
  (incl. an SBIR-style effort on aircraft-maintenance documentation).
  ([SBIR](https://www.sbir.gov/node/2088563))
- a16z's own list: "financial services, legal and consulting, military and government,
  manufacturing, pharmaceuticals, and beyond."
  ([a16z](https://a16z.com/announcement/investing-in-hebbia/))

## What I'm not sure about (flagged, not guessed)

1. **The exact agent taxonomy.** The five-name list (Orchestrator/Planning/Retrieval/Column
   Generation/Information Synthesis) is a synthesis. Hebbia's primary posts name Orchestrator,
   Planning, Retrieval, "Read Matrix subagent," "Output Agent," and "Context Distillation."
   "Column Generation"/"Information Synthesis" are product-copy/secondary terms.
2. **"ISD" as a branded pipeline.** The concept is consistent; the literal term is used more in
   marketing/resources than in the engineering posts, and internals aren't public.
3. **"Verifiable Fact Layer."** A secondary-source label, not (that I found) an official
   component name. Citations themselves are real and central.
4. **Scale/customer numbers** (% of asset managers, $-trillions of assets, named logos) are
   company marketing or press; I did not independently verify them.
5. **OpenAI customer page** (`openai.com/index/hebbia/`) was inaccessible (HTTP 403), so I did
   not rely on it for specifics.
6. **Financial AI Benchmark grading internals** — the public page describes the categories but
   points to a separate methodology doc for exact grading; I did not extract that doc.

### Source list

Hebbia (primary): [Introducing Matrix](https://www.hebbia.com/blog/introducing-matrix-the-interface-to-agi) ·
[Multi-Agent Redesign](https://www.hebbia.com/blog/divide-and-conquer-hebbias-multi-agent-redesign) ·
[Deeper Research Agent](https://www.hebbia.com/blog/inside-hebbias-deeper-research-agent) ·
[Hybrid eval framework](https://www.hebbia.com/blog/evaluating-ai-agents-a-hybrid-deterministic-and-rubric-based-framework) ·
[Who Evaluates the Evaluator](https://www.hebbia.com/blog/who-evaluates-the-evaluator-reaching-autonomous-consensus-on-agentic-outputs) ·
[Hidden Economics of LLM Inference](https://www.hebbia.com/blog/the-hidden-economics-of-llm-inference) ·
[Financial AI Benchmark](https://www.hebbia.com/blog/which-model-will-give-me-the-edge)

Third-party: [a16z](https://a16z.com/announcement/investing-in-hebbia/) ·
[Bloomberg](https://www.bloomberg.com/news/articles/2024-07-08/hebbia-raises-130-million-for-ai-that-helps-firms-answer-complex-questions) ·
[Sacra profile](https://sacra.com/c/hebbia/) ·
[Sacra interview](https://sacra.com/research/danny-wheller-hebbia-vertical-vs-horizontal-enterprise-ai/) ·
[Wikipedia](https://en.wikipedia.org/wiki/Hebbia) ·
[Built In](https://builtin.com/company/hebbia-ai) ·
[Platform Engineer, Agents](https://builtin.com/job/platform-engineer-agents/6606050) ·
[ML Engineer (Wellfound)](https://wellfound.com/jobs/1114362-machine-learning-engineer-at-hebbia-ai) ·
[SBIR](https://www.sbir.gov/node/2088563) ·
[skywork deep-dive](https://skywork.ai/skypage/en/hebbia-ai-deep-dive-guide/1976843429248823296) ·
[Dynamic Business](https://dynamicbusiness.com/ai-tools/hebbia-revolutionizes-ai-interface-meet-the-matrix.html)

---

# Part II — Adaptation plan: from `tieout` (Rogo-framed) to a Hebbia-shaped project

## The thesis in one sentence

Keep `tieout`'s deterministic accounting-identity engine exactly as it is, and put a **small
column-generation agent** on top that fills a Matrix-style **cell** (one question over one
filing) — whose differentiator is that it **verifies its own multi-step reasoning against
accounting identities**, producing a *verified cell*: the deterministic trust signal that
Hebbia's citation-level + rubric-level evaluation (§1, §3.1) does not provide.

## Why this is the right Hebbia story

- It speaks their language: **Matrix grid** (filings = rows, questions = columns, agent answers
  = cells), **column generation**, **hybrid deterministic + rubric evaluation**.
- It attacks their *stated* hard problem (§3.1) from an angle their published framework leaves
  open: a **semantic, cross-figure deterministic check** (accounting identities) rather than only
  schema/format checks.
- It's honest about scope: one strong agent + a real verifier + a small eval grid, not a
  re-implementation of their swarm/serving stack.

## What already exists in this repo (so the build is mostly reframing + one real upgrade)

- **Deterministic engine (keep intact):** `ingest/` → `ontology.py` → `facts.py` →
  `constraints.py` → `engine/{checker,propagating}.py` → `attribution/` → `report/`.
- **Agentic layer (exists, Rogo-framed):** `agent/felix.py` (a Claude tool-use agent that
  retrieves grounded figures and emits a Retrieval→Definition→Calculation trace) and
  `agent/verify.py` (checks cited numbers vs XBRL + a heuristic calc check).
- **Eval (exists, Rogo-framed):** `bench/{run,grade}.py` + `data/bench/questions.json` — rubric
  grading + an LLM-judge baseline + a "money metric" (judge said yes, answer was wrong).
- **Rogo-specific naming to reframe:** "Felix," "Big Finance Benchmark/BFB," and the "What Rogo
  builds" sections in the web UI.

## The three options for the agentic layer

### Option A — "Verified Cell" (single column agent + deep verification into the engine)
One tool-use agent fills a cell (answers a question over one filing), emits a structured
derivation, and then its cited figures + computed value are routed through the **actual
constraint engine** (`constraints.py` + `engine/propagating.py` + `attribution/`): instantiate
the relevant identities, check the answer reconciles, and attribute any violation
(`extraction_error` / `filing_inconsistency` / `constraint_model_error`). Output: a
`VerifiedCell{answer, citations, verdict, attribution}`.

- **Pros:** smallest delta from current code; makes the differentiator *real* (today's
  `verify.py` only does pairwise number-matching, not identity reconciliation); reuses the crown-
  jewel engine; trivially deterministic/cacheable.
- **Cons:** post-hoc verification only (it checks the finished answer, doesn't steer the agent);
  one agent, so it under-sells the "swarm."
- **Scope:** ~1 weekend.

### Option B — "Mini-swarm with a verifier in the loop" (self-correcting)
Mirror Hebbia's orchestrator pattern at small scale: an Orchestrator splits a question into
sub-fields (Planning), a Retrieval step pulls grounded figures, a Calculation step computes, and
the **constraint engine gates each step** — if an intermediate reconciliation fails, the
orchestrator re-plans/retries (bounded). Output: the same verified cell, plus a self-correction
trace.

- **Pros:** most faithful to the multi-agent + *process-focused* evaluation story (§2.2, §3.1);
  verification-in-the-loop is a genuinely strong, novel demo (deterministic identities as a
  *control signal*, not just a grader).
- **Cons:** more moving parts and more LLM calls (cost/latency — ironically bumping into §3.3);
  more ways to be flaky; the marginal story over A is "self-correction," which is nice-to-have,
  not the core thesis.
- **Scope:** ~2 weekends, and the riskiest to keep "working-first."

### Option C — "Matrix column eval harness" (grid + full hybrid scorecard)
Lean into the *evaluation* problem: build the Matrix grid (N filings × M questions), fill every
cell with the (simple) agent, then score with the **full hybrid methodology** — deterministic
verification (our engine) **+** audit-trail rubric **+** LLM-judge — and headline the result
that the deterministic layer **catches what the judge rubber-stamps** (the money metric),
reproducing Hebbia's "hybrid deterministic + rubric" framework (§3.1) on financial extraction.

- **Pros:** best showcases the *stated* hard problem and the Matrix grid; mostly already built in
  `bench/`.
- **Cons:** the agent itself stays simple, so on its own it under-sells the "agentic" half.
- **Scope:** ~1.5 weekends.

## Recommendation

**Build Option A as the spine, and present/evaluate it through the Option C framing. Defer B to
a clearly-labeled stretch.**

Concretely, the recommended deliverable is:

1. **Verified Cell (A):** reframe the agent as a Matrix **column-generation agent** that fills a
   cell, and **upgrade `verify.py` to use the real constraint engine** (this is the one
   substantive code upgrade, and it's the whole differentiator). 
2. **Hybrid eval grid (C):** reframe `bench/` as a **Hebbia-style hybrid answer-quality eval** —
   deterministic identity verification + audit-trail rubric + LLM-judge — over a small grid, and
   make the headline finding the **money metric** (judge-passed-but-wrong answers the
   deterministic layer catches).
3. **Stretch (B), labeled as such:** a bounded verify-in-the-loop self-correction mode, behind a
   flag, demonstrated on one deliberately hard filing.

Rationale: A+C is the weekend-or-two, working-first win that lands *exactly* on Hebbia's stated
problem (§3.1) while keeping the deterministic engine — the thing that's actually hard and
valuable — at the center. B is the highest-ceiling idea but the lowest-reliability per hour, so
it belongs as a flagged extension, not the core.

## Build phases (working-first; each phase runs end-to-end before the next)

- **P1 — Reframe the agent.** `agent/felix.py` → a Hebbia column agent (new name, prompts, no
  "Felix"); `AgentAnswer` → a cell result. Behaviour unchanged; engine untouched.
- **P2 — Deepen verification (the differentiator).** Route the cell's cited figures through
  `constraints.py` + `engine/propagating.py` + `attribution/`; emit a verdict + attribution.
  Keep the cheap number-match as a fast pre-check.
- **P3 — Hybrid eval grid.** Reframe `bench/` as the Matrix hybrid scorecard; keep rubric +
  judge + money metric; present results as a grid.
- **P4 — README + UI.** README opens with a Hebbia pitch (answer-quality evaluation for
  financial-document extraction; the verification layer Matrix column-generation needs).
  Reframe the web UI's "Ask Felix"/Rogo sections to Matrix/verified-cell language.
- **P5 — Verify + ship.** Tests + offline smoke tests green; commit; push.

## Explicit non-goals (to stay honest and in-scope)

- Not re-implementing ISD, the DAG orchestrator, "Maximizer," or their serving stack.
- Not claiming Hebbia *lacks* verification wholesale — they have citations and rubric/judge
  evaluation; the precise claim is that a **deterministic cross-figure accounting-identity check
  at answer-construction time** is a gap their published approach doesn't cover.
- Not touching the deterministic engine's logic — only *consuming* it from the agent layer.
