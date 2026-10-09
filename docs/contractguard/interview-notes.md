# ContractGuard interview notes

ContractGuard adapts Waku into a source-grounded contract reviewer with three
memory types, controlled ablations and saved-review observability. Completed
offline runs verify engineering behavior. Live extraction quality, risk quality
and memory benefit remain unmeasured.

## Explain the project in one minute

I extended Waku's existing tool and memory boundaries for ten contract clause
types. The interactive loop dispatches a review tool that extracts exact source
quotations, assesses risk, optionally compares clause guidance and saves a
structured result and report. Python validates source offsets and stage schemas.
Completed positive findings become review history; normal reviews can propose
small source-grounded lexical cues for semantic memory.

I built a separate controlled evaluator that reuses the same extraction and
risk functions. It isolates baseline, semantic-only and full-memory homes,
keeps procedures constant and excludes gold annotations from prediction input.
CUAD supports eight of the ten targets. An offline run processed its 102 test
contracts in each arm, and an authored demo shows persisted history in the
dashboard. Those scripted runs establish no live quality or memory advantage.

## Why Waku?

Waku exposes context assembly, tool dispatch, model adapters, memory stores and
evals as readable code. Adapting those boundaries requires explicit decisions
about evidence validation, memory writes, retries and measurement. A single API
call would leave those responsibilities unspecified.

The implementation preserves Waku's Python package, loop and provider adapter.
Contract capability uses an opt-in persona, ordinary tools and bundled skills.
The project adds no agent framework, vector database or default dependency.
[Architecture](architecture.md) maps each responsibility to its implementation.

## Why three memory types?

| Type | Question it answers | ContractGuard example | Update rule |
|---|---|---|---|
| Semantic | What is known? | Cap-on-liability definition, wording and red flags | Seed once; normal reviews may add validated lexical cues |
| Episodic | What happened? | A prior completed liability finding with quoted evidence and a recommendation | Persist after all requested model stages complete successfully |
| Procedural | How should the reviewer act? | Search for caps, inspect carve-outs and report evidence | Load the bundled clause skill; never turn a prediction into a procedure |

These stores give knowledge, fallible historical predictions and instructions
different provenance and update rules. A previous HIGH assessment remains a
predicted episode, not an authoritative legal fact. Benchmark runs freeze
semantic seeds and disable semantic promotion so predicted answers cannot
rewrite benchmark knowledge.

## Why a retrieval gate?

The gate chooses semantic and episodic retrieval independently for each clause.
It aims to keep irrelevant definitions and unrelated review history out of model
context. Clause-specific retrieval and current-source exclusion further reduce
noise. Invalid gate output skips contract memory and records the failure.

A gate also costs a model call. It may reduce downstream input tokens and
latency when it excludes unnecessary context, but it may add more overhead than
it saves. The official offline run recorded 982 scripted baseline calls versus
1,798 in either memory arm. Full-memory retrieved 2,132 items, including 1,316
episodes. These figures demonstrate overhead and retrieval behavior, not live
token or latency savings. [Measured results](results.md) records their provenance.

## Why CUAD?

CUAD gives extraction a public source of contract text and annotated spans.
It lets the evaluator compare predictions with reference evidence using a
fixed matching policy instead of judging fluent report prose. The loader checks
full source text and answer offsets before scoring any model output.

The agent supports ten review targets, but CUAD supplies standalone annotations
for only eight. Indemnification and Confidentiality Obligations remain unscored.
CUAD also provides no HIGH/MEDIUM/LOW reference labels. Separate authored cases
test agreement with explicit risk rubrics; they do not establish broad legal
accuracy. [Evaluation](evaluation.md) specifies categories and scoring rules.

## Why memory ablation?

Persisting more history does not prove that history improves an answer.
Baseline removes semantic and episodic retrieval, semantic-only uses fixed
domain knowledge, and full-memory additionally retrieves prior prediction
episodes. All three arms keep canonical procedures, sources, order, prompts,
models and matching policy constant. Each arm starts in a fresh local home.

The primary matcher uses character IoU >= 0.5 with maximum-cardinality
one-to-one assignment. Exact duplicate offsets collapse, and repeated text at
different locations remains distinct. Micro scores pool span counts; macro
scores expose category performance, including selected categories with no
positive support. Extraction failures retain false negatives, and risk errors
do not erase a valid extraction.

The scripted official run produced identical scores in all three arms because
its source rules ignore retrieved context. It confirms isolated memory behavior
and scoring, not memory usefulness. A live comparison must preserve these
controls, repeat runs to assess variability and report any quality regression.
Gold spans remain available only to scoring; titles and reference answers never
enter prediction input or memory writers.

## What did not work?

The implementation and validation records contain concrete corrections:

| Failed assumption or regression | Evidence and correction | Engineering lesson |
|---|---|---|
| The ten review targets all map to CUAD categories. | Inspection of the official category list found two missing categories. The runner records them as unscored instead of inventing negative labels. | A dataset's task and label coverage constrain claims. |
| Interactive input limits also fit the official benchmark. | The longest official source contains 300,768 characters, exceeding the tool's 200,000-character limit. The evaluator calls stages directly and preserves complete sources. | Truncation would silently change offsets and evaluation input. |
| Generic clause-skill trigger wording stays confined to contract reviews. | Three ordinary-message trigger cases failed during Phase 1. Removing generic triggers and filtering domain skills before conversational limits fixed the collisions. | Domain instructions must not crowd out ordinary assistant skills. |
| Temporary Git metadata can safely apply to every test. | The Phase 1 check initially interfered with tests that create their own repositories. Scoping it to the two skill-tracking guards restored isolation. | A validation harness must isolate its own state. |

Deliberate mutations also verified regression guards. Bypassing source equality
made forged-quotation cases fail. Raising IoU to 0.6 made the boundary case fail.
Enabling episodes in semantic-only mode made its policy eval fail. Removing the
dashboard artifact boundary admitted an external-home symlink and failed its
guard. Reclassifying a risk error as NOT FOUND failed the provider-error case.
These were temporary validation mutations, not observed production incidents;
the repository retains the guarded behavior.

## What trade-offs remain?

Plain-text input avoids adding a PDF dependency, but it leaves ingestion to the
caller. Complete benchmark sources preserve evidence offsets, but large sources
can exceed a provider's context budget. Source validation proves a quotation
exists; it cannot prove a legal interpretation or recommendation is correct.

Atomic artifact replacement and a ready journal allow sequential persistence
retry. SQLite episode deduplication prevents repeat writes. These mechanisms
do not provide one transaction across all files and memory, or coordination
between concurrent review processes. Dashboard snapshots may temporarily lag
reports. The reader validates artifacts and exposes unavailable metrics rather
than generating replacement numbers.

The dashboard reads saved outputs without a model key or agent initialization.
Interactive traces describe actual loop turns; direct benchmark/demo runs save
experiment artifacts instead of fabricating loop traces. Temperature stays at
the provider default, SDK wire retries remain opaque, and unknown costs remain
null. A live study must report these limitations beside its measurements.

## Resume bullets supported by completed validation

The finalized offline measurements in [results](results.md) support the bullets
below. Retain the offline qualification when using the measured contract count.

**ContractGuard Agent — contract-review extension of Waku**

- Adapted Waku's existing Harness, Loop, Memory and Eval architecture into an opt-in reviewer for ten clause types, with source-validated extraction, risk assessment and structured reports.
- Integrated seeded semantic knowledge, completed review episodes and ten procedural skills with independent retrieval gating, current-document exclusion and guarded consolidation.
- Built an isolated three-arm memory-ablation evaluator and completed an offline scripted integration run on all 102 CUAD test contracts across eight mapped categories, with fixed span matching and gold excluded from prediction context.
- Added reproducible prediction artifacts, deterministic regression evals and a read-only dashboard showing saved reports, review progress, risk counts, memory growth and generated metrics.

Performance bullets remain deferred until finalized live artifacts exist.
Do not describe the authored demo's F1 of 1.0 as CUAD accuracy, describe
3/10 scripted risk agreement as live risk performance, or claim that 166 stored
episodes improved extraction. A measured live claim must name its provider,
models, dataset coverage, matcher, run artifacts and observed comparison.

## Interview walkthrough

1. Run the [five-contract offline demo](demo.md#run-the-demo) and open its printed dashboard home at `#reviews`.
2. Select a report and trace a quotation from its finding back to the saved source offsets.
3. Show the semantic, episodic and procedural counts, then explain why episode growth is not a quality metric.
4. Walk through the [architecture](architecture.md), including direct benchmark stages and read-only dashboard access.
5. Show the [official offline results](results.md#official-cuad-integration-run), including equal scores and added gate calls.
6. Explain the eight-category mapping, source-only prediction boundary and one trade-off that still needs live measurement.
