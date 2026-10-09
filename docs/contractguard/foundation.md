# ContractGuard memory foundation

ContractGuard adds opt-in contract context to Waku's existing session and memory
facade. Set `WAKU_CONTRACT_REVIEW=1` before starting Waku. Phase 1 supplies memory
and procedures. The [review toolchain](toolchain.md) connects extraction, risk
scoring and reports to this foundation.

## Memory and prompt design

The Phase 1 implementation request authorizes this design under conventions
section 2. Review mode defaults off and preserves the user's runtime `SOUL.md`.
`Session.build_system()` appends contract instructions only in review mode.
Those instructions require English findings, source quotations, uncertainty,
HIGH/MEDIUM/LOW risk labels and a distinction between missing and detected
clauses. The agent provides review assistance and cannot claim legal advice.

Review mode initially requires both stores to use SQLite. The facade rejects
other configurations before constructing remote adapters. This keeps seeding,
complete history lookup and duplicate checks local without changing store
interfaces or promising remote consistency that Phase 1 does not test.

`Memory` seeds ten canonical clause facts when review mode initializes. Each
fact contains a definition, lexical variants, red flags and review guidance.
Repeated initialization compares normalized content and does not add another
copy. Definitions describe review cues rather than jurisdiction-specific legal
rules. Seeds carry `contractguard_seed` provenance.

The review gate uses the existing small-model client with a separate prompt.
It returns independent semantic and episodic booleans, a nonempty search query
when either store is selected, a reason and an optional canonical clause type.
Strict JSON validation rejects strings masquerading as booleans and unknown
fields or clause names. Failure selects neither store and emits an observable
skip reason. The legacy three-value gate and its fail-open behavior remain
available when review mode is off. Simple source extraction should skip history;
ambiguous wording can request guidance; a historical comparison can request
review episodes. An unrelated request should skip contract memory.

Exact clause history scans decoded review summaries and sorts their timestamps,
so a latest-review request does not depend on a small FTS top-k result. Domain
retrieval excludes ordinary chat episodes. Clause-specific semantic retrieval
filters matches by canonical subject. A general domain query uses only facts
with ContractGuard provenance. Existing slot selection still filters facts.

Ten community `SKILL.md` directories supply procedures through the existing
keyword loader. The facade excludes bundled clause procedures when review mode
is off. The conversational loader selects at most two skills. The review tool
selects one canonical procedure for each clause task when it reviews all ten.
Each procedure describes
its own detection cues, steps, risk signals, evidence and recommendation rules.
Shared grounding rules live in the opt-in persona.

## Completed reviews and consolidation

`Memory.complete_review(record, source_text, ...)` is the explicit completion
event. It validates the entire record before writing any review memory. A
versioned JSON summary preserves contract id, contract name, canonical clause,
risk, finding summary, evidence spans, recommendation, timestamp and origin.
Evidence uses Python character offsets and must quote the supplied source text
exactly. Timestamps must include a timezone. Duplicate summaries are not added
again. SQLite's existing `episodes` table and FTS index store these summaries;
no migration or new store is needed. Source documents are not copied into facts.

Completion persists the episode immediately and then requests optional reusable
patterns from the small model through `consolidation.consolidate_review()`.
Callers can supply candidate patterns explicitly for offline use. Candidates
have a kind (`variant` or `risk_pattern`), a short lowercase lexical pattern and
an evidence index. The writer checks the exact schema, evidence grounding,
contract identity, novelty and recalled content before constructing a fact.
It stores a short pattern with fixed review guidance, never model-written
reports or free-form fact bodies. Model or JSON failure leaves the episode
stored and returns no new facts. Repeating completion can retry consolidation.

Review mode disables generic chat-log consolidation. A chat response alone
cannot prove that a grounded review finished, and a chat may contain benchmark
annotations. Default Waku still consolidates every configured number of
exchanges. Completed-review consolidation has its own immediate trigger and
uses existing gate, consolidation and metering events.

A record's origin is `review` or `benchmark_prediction`. Benchmark predictions
may become prior-review episodes for a later full-memory experiment, but they
never generate semantic patterns. Gold annotations have no accepted input field
and must never be supplied as source text, evidence or model context. Callers
must keep provenance honest: validation cannot detect a caller relabelling gold
answers as a genuine review. The [benchmark runner](evaluation.md) uses isolated
homes and controls retrieval, skills and writes separately for each ablation arm.

## Learning checkpoint

Semantic facts describe reusable clause wording and review cues. Episodic
summaries describe a particular reviewed contract and its conclusion. Procedure
bodies reach the system prompt after the user's message matches their skill
metadata. Session assembly causes one retrieval decision before each loop run;
only selected stores contribute memory. The completion API causes immediate
review persistence and optional semantic consolidation.

SQLite stores facts, dated review summaries and ordinary chat rows in their
existing tables. It does not store procedure bodies there. Raw reports, whole
contracts, current benchmark answers and recalled facts must not become new
semantic knowledge. Leaking test answers into retrieval would let the agent
repeat annotations rather than extract clauses, invalidating measured results.

Offline evals verify these interfaces and scripted model requests. They do not
measure live clause extraction, legal correctness or memory improvement. The
review tool connects validated findings to the completion event. The benchmark
uses [verified CUAD categories and a fixed ablation policy](evaluation.md).
