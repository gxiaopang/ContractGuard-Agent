# ContractGuard architecture

ContractGuard adds clause-review tools and domain memory to Waku's existing
interactive loop. The benchmark and demo call the same extraction and risk
functions directly so experiments can control source order and memory policy.

The opt-in [memory upgrade](memory-eval-design.md) adds a versioned review profile
and source-bound working context. An additive SQLite domain index enforces
applicability and lifecycle before ranking recalled records. Saved human decisions
authorize scoped approvals and correction records. The Reviews page displays
provenance and exposes human-only memory lifecycle actions. The generic evaluator
generates reference spans locally and runs frozen or sequential memory experiments
without a manual annotation prerequisite. Quality remains explicitly unverified
when calibration or scored criteria are unavailable.

```mermaid
flowchart TD
    H[Harness: CLI or dashboard chat] --> S[Session: persona and context]
    S --> G[Retrieval Gate]
    G --> SM[Semantic Memory: SQLite clause knowledge]
    G --> EM[Episodic Memory: SQLite review history]
    SM --> S
    EM --> S
    PM[Procedural Memory: bundled clause skills] --> S
    S --> L[Loop: model and tool dispatch]
    L --> T[Tools: review_contract]
    T --> G
    SM --> R[Review stages: parse, extract, risk, optional comparison]
    EM --> R
    PM --> R
    T --> R
    R --> A[Validated findings and saved reports]
    A --> D[Dashboard: Reviews]
    D --> HR[Human confirmation or correction]
    HR --> C[Approved positive review findings]
    C --> EM
    E[Eval and demo: isolated arms, source-only input] --> R
    E --> BP[Benchmark prediction history]
    BP --> EM
    R --> E
    GOLD[Gold spans: scoring only] --> E
    E --> M[Metrics and demo progress snapshots]
    M --> D
    SM --> D
    EM --> D
    PM --> D
```

## Interactive review

The harness sends a chat message to session context assembly. Contract mode
adds the contract persona, gated facts and history, and matching skills before
the existing loop asks a model to respond. Tool dispatch calls `review_contract`
when the model requests a review.

The review tool parses complete plain text and reviews each requested canonical
clause. Its gate independently chooses semantic and episodic retrieval for that
clause. The history query excludes the current source id before taking up to
three episodes. The tool loads each clause's exact procedure independently of
the conversational loader's two-skill limit.

Extraction requests exact quotations and occurrence numbers. Python locates
those quotations in the source and validates half-open character offsets.
Risk assessment refers to extracted evidence indices. Optional comparison
requires semantic guidance and validates both source and guidance quotations.
The report renderer consumes validated findings; it never supplies metric input.

The tool saves ordinary documents, structured results and reports as pending
human review. Generation writes no episodes or learned facts. A person confirms,
corrects or rejects a complete report in Reviews. The decision journal preserves
the original findings and records accepted findings separately. The server
validates corrected quotations against the saved source before writing memory.
Partial reports cannot receive approval.

The memory facade checks saved approval provenance for each ordinary v2 episode.
Confirmation makes no model call and learns no semantic cues. SQLite serializes
competing decisions and duplicate checks; interrupted writes resume from the
saved decision. Existing v1 history remains readable as legacy automatic data.
Benchmark prediction history retains its explicit evaluation-only exception and
cannot use the human-decision endpoint. Evidence checks do not establish a
model's commercial or legal interpretation.

## Controlled evaluation and observation

The evaluator separates source-only prediction input from gold annotations.
Its predictor parses the complete source and calls extraction and risk stages
directly. It omits interactive guidance comparison, tool dispatch and review
caching. All arms retain the same bundled procedures. Baseline skips memory
retrieval; semantic-only gates fixed facts; full-memory also gates prior
prediction episodes. Every arm gets a fresh home, and all semantic seeds remain
fixed throughout benchmark runs.

The demo uses this predictor and generates native review artifacts from its
validated findings. It writes queue and memory snapshots before calls and after
reviews. The existing dashboard reads those artifacts, generated metrics and
local memory counts through `GET /api/contractguard`. That route starts no
agent or model call. Native interactive reviews expose their pending approval
queue, but have no demo evaluation snapshot. The selected ordinary report also
exposes its bounded source and human decision. `POST /api/contractguard/review`
stores the decision within the same configured home.

## Code map

| Component | Implementation |
|---|---|
| Harness and session | [runtime/session.py](../../waku/runtime/session.py), [ops/dashboard.py](../../waku/ops/dashboard.py) |
| Loop and tools | [loop/agent.py](../../waku/loop/agent.py), [tools/contract_review.py](../../waku/tools/contract_review.py) |
| Review stages | [contract_parser.py](../../waku/tools/contract_parser.py), [clause_extractor.py](../../waku/tools/clause_extractor.py), [risk_scorer.py](../../waku/tools/risk_scorer.py), [clause_differ.py](../../waku/tools/clause_differ.py), [report_generator.py](../../waku/tools/report_generator.py) |
| Human decisions | [tools/human_review.py](../../waku/tools/human_review.py) |
| Evidence and finding contracts | [contract_schema.py](../../waku/tools/contract_schema.py) |
| Memory and retrieval gate | [memory/__init__.py](../../waku/memory/__init__.py), [memory/contractguard.py](../../waku/memory/contractguard.py), [retrieval_gate.py](../../waku/memory/retrieval_gate.py) |
| Scoped memory | [contractguard_context.py](../../waku/memory/contractguard_context.py), [contractguard_store.py](../../waku/memory/contractguard_store.py), [contractguard_records.py](../../waku/memory/contractguard_records.py), [domain_review.py](../../waku/tools/domain_review.py) |
| Procedures | [skills/community/](../../skills/community/) |
| Consolidation | [memory/consolidation.py](../../waku/memory/consolidation.py) |
| Eval | [predictor.py](../../evals/contractguard/predictor.py), [runner.py](../../evals/contractguard/runner.py), [matching.py](../../evals/contractguard/matching.py), [metrics.py](../../evals/contractguard/metrics.py) |
| Upgrade eval | [cases.py](../../evals/contractguard/cases.py), [upgrade_runner.py](../../evals/contractguard/upgrade_runner.py), [quality.py](../../evals/contractguard/quality.py), [contractguard_judge.py](../../waku/ops/contractguard_judge.py) |
| Demo | [demo_review.py](../../scripts/demo_review.py), [evals/contractguard/demo.py](../../evals/contractguard/demo.py) |
| Dashboard reader and view | [ops/contractguard.py](../../waku/ops/contractguard.py), [js/contractguard.js](../../waku/ops/static/js/contractguard.js) |

[Toolchain](toolchain.md) specifies artifact and retry contracts.
[Evaluation](evaluation.md) specifies matching and leakage controls.
[Demo](demo.md) specifies dashboard data sources and limits.
