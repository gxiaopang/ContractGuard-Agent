# ContractGuard memory and evaluation upgrade

ContractGuard will make review memory conditional, correctable and measurable.
The upgrade will preserve Waku's semantic, episodic and procedural memory,
offline deterministic evals, model judges and trace artifacts.

Status: the user authorized implementation on 2026-10-13 with no manual annotation
prerequisite. The opt-in implementation supplies scoped memory, correction records,
generated evaluation cases and separate quality verdicts. Model opinions remain
uncalibrated until independent review is available. Memory and tool-contract
changes follow [conventions section 2](../context/conventions.md#2-how-much-process-a-change-needs).

## Running without manual annotation

`python scripts/run_eval.py --upgrade` generates source-template extraction
references and runs four frozen memory arms offline. `--experiment sequential`
applies labelled source-only scripted feedback after scoring. `--repetitions 2`
repeats each arm with recorded order seeds. Local versioned case files can replace
generated cases through `--cases`; no external dataset is required.

Generated references cover positive, negative, formatting and unscored examples
for all ten targets. Generated cases provide no risk labels. Synthetic history
uses the real approval journal with an explicit scripted-reviewer label in fresh
experiment homes. These decisions exercise the boundary and represent no actual
human review. Unscored risk and human acceptance remain null.

`--live` enables real model calls. `--judge` adds criterion-level opinions with
current-source citations. Those opinions report `not_calibrated` and do not
establish legal quality. The no-annotation implementation exposes quality
thresholds and strict release checks while reporting quality as `not_evaluated`.
Human calibration and empirical quality claims remain optional later work.

`WAKU_CONTRACT_REVIEW=1 WAKU_CONTRACT_MEMORY_UPGRADE=1` enables production
applicability filters, versioned approvals and explicit corrections. Ordinary
memory still requires confirmation; that product decision is independent of
preparing evaluation labels. The existing Reviews page exposes memory provenance,
guidance proposals and approve, revoke and replace actions. These actions call
no model. Waku code remains MIT.

The hosted route policy remains Elastic License 2.0.

## Current behavior and scope

The [memory foundation](foundation.md) seeds ten clause definitions and review
cues. Clause procedures describe review steps. A domain gate selects semantic
and episodic retrieval independently. History retrieval filters clause types
and source identities, then primarily orders results by recency.

The current working tree includes the [human-review workflow](human-review-design.md).
Ordinary generation saves pending reports. Confirmation or correction stores
approved positive findings without model calls or semantic learning. Historical
v1 episodes remain readable without acquiring approval metadata.

The [evaluation runner](evaluation.md) already separates prediction inputs from
references, isolates experiment homes and measures extraction, risk agreement
and overhead. Its full-memory experiment accumulates benchmark predictions.
Those predictions do not represent human-approved experience. Offline scripted
results establish engineering behavior, not live review quality.

The upgrade will support complete plain-text contracts and the existing clause
targets. It will introduce no default dependency, remote memory service, graph
database, autonomous rule promotion or new core model tool. Dataset selection,
collection and downloads remain outside this proposal. The evaluator will accept
versioned cases through adapters without requiring a particular dataset.

## Review context and memory contracts

Every new review will carry a versioned context alongside its source identity.
The context will record the workspace or project scope, contract type, represented
party, commercial role, applicable review-policy version, review date and any
explicitly supplied jurisdiction. Unknown fields will remain unknown. The model
will not invent a role, jurisdiction or policy to satisfy a retrieval filter.

Context and policy revisions will participate in review cache identity. Changing
the represented party must not return a cached assessment for the opposite party.
Server-side configuration will establish access boundaries. Model-supplied scope
labels will never grant access to another workspace or tenant.

| Memory component | Proposed responsibility |
|---|---|
| Working context | The review will retain definitions, source-section references, cross-references, checked targets and unresolved questions for the current source revision. |
| Semantic memory | Records will distinguish clause definitions, approved organizational policies and approved experience rules. Each record will declare provenance, scope and validity. |
| Episodic memory | Records will preserve approved findings and explicit corrections with their original source, review context and human decision. |
| Procedural memory | Versioned clause procedures will declare required inputs, checks and completion conditions. Missing information will produce an explicit incomplete check. |

Working context will use lightweight source references within review artifacts.
It will not copy complete contracts into long-term facts. Evidence offsets will
continue to address the exact source text. Cross-reference extraction failures
will leave an unresolved reference rather than fabricate a linked section.

### Applicability and retrieval

Retrieval will apply these steps in order:

1. The existing domain gate will select stores and record its decision.
2. Deterministic filters will enforce access scope, approval state, effective
   dates, policy compatibility and applicable role or jurisdiction constraints.
3. Retrieval will exclude the current source and rank eligible records by clause
   relevance, context match and recency. Latest-history requests will retain an
   explicit chronological mode.
4. A bounded context budget will select records and preserve their identifiers
   and provenance. The trace will record selection and exclusion reasons.

An unknown required applicability field will make a scoped record ineligible.
Explicitly general clause definitions may still be retrieved. Missing scope
will never act as a wildcard. Conflicting policies will require an explicit
policy selection or an unresolved finding; recency alone will not resolve them.
Gate failures will preserve the current observable skip behavior.

Recalled records will supply guidance only. Each current finding must cite the
current contract. Quote validation will prove that text exists at its offsets;
a separate quality check will assess whether that text supports the conclusion.

### Corrections and lifecycle

Domain memory will support candidate, approved, superseded and revoked states.
Only approved, applicable records will enter ordinary review context. Superseding
and revoking will preserve prior records and an auditable decision history.

A correction will record the original finding, accepted replacement, reason,
source revision and approval decision. A false-positive correction may say that
the earlier evidence did not support a finding. It will use a distinct correction
schema rather than fabricate a positive finding or prove absence with a quotation.
Rejection alone will create no reusable correction rule.

Approved corrections will remain scoped episodes. Promoting them into a semantic
rule will require a separate human decision, explicit applicability and regression
cases. Automatic candidate generation and rule promotion are deferred.

Lifecycle updates will use expected revisions and transactional writes. Concurrent
or retried decisions must not duplicate records or leave two active replacements.
The existing approval journal will remain the authority for approval provenance.
Approval and lifecycle actions will make no model calls.

### Compatibility and storage

The implementation will keep generic Waku store interfaces stable where possible.
Additive ContractGuard metadata and lifecycle tables in the existing SQLite file
will index domain records without rewriting generic chat episodes. A new domain
record version will carry review context and correction references.

Existing v1 and v2 records will remain inspectable. Records without verified scope
or required applicability metadata will be excluded from the upgraded review
retrieval until a person explicitly assigns and approves that metadata. The
migration will not invent approval, roles or effective dates.

Migration tests will use isolated homes and cover repeated initialization,
interruption and old-record decoding. Disabling the upgraded retrieval policy
will preserve stored data. Arbitrary older binaries are not promised compatibility
with new records. Runtime data will never be cleared to migrate or roll back.

## Evaluation contracts and measurements

The evaluator will separate three versioned objects:

| Object | Fields and access |
|---|---|
| Review input | The predictor receives the source identity, complete text, requested targets and review context. |
| Evaluation reference | Only scoring receives annotation coverage, expected findings, risk rubrics, required checks, acceptable uncertainty and severity weights. |
| Experiment configuration | The runner receives model and prompt versions, procedure and policy versions, memory snapshot, mode, order, repetition settings and scoring policy. |

Adapters will map local cases into these objects. Missing annotations will mean
unscored, while an explicitly annotated absence will mean negative. Coverage
will be declared per target and metric. Risk references will require a stated
perspective and rubric; clause presence will not imply a risk label.

Existing matching rules will remain versioned and available through compatibility
adapters. A new rule will require a new scoring-policy version. Every comparison
will use the same matching rule and declared coverage across its arms.

| Evaluation family | Measurements |
|---|---|
| Engineering | Offline tests will verify evidence offsets, closed schemas, approval boundaries, scope filters, lifecycle behavior, retries and reference isolation. |
| Extraction | Scores will report span precision, recall and F1, annotated negative false positives, failures and critical-target misses. |
| Assessment | Scores will report risk confusion matrices, serious underestimates, evidence support, exception handling, perspective consistency and recommendation quality. |
| Memory | Scores will report gate decisions, applicable retrieval precision and recall where candidates are labelled, stale or out-of-scope use, correction transfer and regressions. |
| Operations | Traces will report stage failures, model calls, tokens, latency, cost availability and retrieval overhead. Human feedback will report acceptance and substantive corrections with sample counts. |

Critical targets and severity weights will belong to the versioned evaluation
policy instead of a universal hard-coded clause subset. Metrics with no eligible
examples will report null with zero support. Stage failures will remain in the
denominator where applicable and will never earn successful-negative credit.

Model judges will grade separate criteria with anchored rubrics and cited evidence.
They will grade justified memory rejection as well as justified memory use.
Evaluation artifacts will preserve judge identity, rubric version, rationale,
failure state and repeated-run variability. Human reviewers will calibrate a
sample and record disagreements. Automated scores will not claim legal correctness.

## Memory experiments

The first comparison will freeze each arm's memory before evaluation. All arms
will use identical source cases, procedures, models, scoring and runtime limits.
The initial arms will use no retrieved memory, fixed semantic memory, semantic
plus approved history, and semantic plus approved history and corrections.
Procedure content will stay constant; a separate paired comparison can evaluate
procedure upgrades without attributing their effect to episodic memory.

A second experiment will evaluate ordered reviews with feedback between reviews.
The runner will score each prediction before applying the permitted feedback.
Only explicitly supplied feedback will affect future memory. The experiment will
record feedback timing and distinguish real human decisions from scripted feedback.
The existing prediction-accumulation mode will remain separately labelled.

Targeted cases will exercise opposite commercial roles, stale policy, revoked
history, misleading historical findings, unrelated memories and unresolved source
references. Metamorphic checks will verify declared relationships: formatting
changes preserve meaning, while role changes invalidate role-specific retrieval.
They will not assume that every role change must change a risk label.

Each arm will use a new isolated home. Group and source identities will prevent
contract versions and near duplicates from crossing memory/evaluation boundaries.
Adapters will supply document-family ids where known; a versioned local text
similarity check will flag remaining suspected duplicates for review. Unresolved
duplicate groups will be excluded from a claimed held-out comparison.

Artifacts will record source hashes, memory snapshot and lineage, code and prompt
hashes, model settings, judge configuration, policy versions and processed cases.
Paired score differences will include contract-level uncertainty estimates and
repeated model runs. Sequential runs will repeat declared order seeds and treat
whole sequences as dependent observations. Partial runs will not establish a
complete comparison. Memory regressions will be reported alongside improvements.

## Release decisions

Waku's offline gate will remain available without credentials. ContractGuard will
add an explicit quality profile with separate engineering and quality verdicts.
Quality will report passed, failed or not evaluated. Missing credentials, skipped
required criteria and insufficient scored coverage will never become quality passed.
A strict ContractGuard release invocation will require both verdicts to pass.

The quality profile will declare separate bounds for critical misses, unsupported
findings, serious risk underestimates and operational failures. It will also declare
minimum coverage, acceptable regression margins and cost or latency budgets.
An average report score will not override a failed critical criterion.

Numeric quality thresholds remain unset until P1 establishes a baseline and the
maintainer records acceptable tradeoffs. The profile must be frozen before the
final held-out comparison. Offline invariants require every case to pass.

## Delivery sequence and acceptance

Each phase will ship with deterministic regression cases and updated affected
documentation. These phases express dependencies rather than calendar estimates.

| Phase | Deliverable and likely files | Acceptance condition |
|---|---|---|
| P0: Freeze contracts | Review this proposal and specify context, record, reference and metric schemas beside existing ContractGuard modules. | Reviewers can trace each proposed field, state transition and compatibility rule to a concrete example. Memory and tool-contract design receives maintainer approval before implementation. |
| P1: Establish evaluation baseline | Extend `evals/contractguard/dataset.py`, `runner.py`, `metrics.py` and `scripts/run_eval.py` with generic case adapters, explicit coverage and versioned experiment manifests. | Authored offline cases exercise both annotated absence and unscored targets. Existing adapters preserve their results. Reference leakage and failed-stage accounting tests pass. A baseline artifact can be produced without a selected external dataset. |
| P2: Govern memory | Extend `waku/memory/contractguard.py`, the facade, SQLite initialization, `contract_schema.py` and `human_review.py` with context, lifecycle and correction records. | Tests cover role-aware cache identity, unknown scope, expiry, revocation, supersession, legacy records, approval provenance, concurrent updates and retries. Generation still writes no approved memory. |
| P3: Improve review context | Extend retrieval, `contract_review.py` and clause procedures with applicability filters, bounded context and explicit cross-reference checks. | Tests reject opposite-role and stale guidance, preserve exact current-source evidence and expose unresolved references. Retrieval traces explain selected and excluded records. |
| P4: Measure quality and learning | Extend the predictor and runner; add ContractGuard judges under `evals/judge/` and focused deterministic cases. | Frozen and sequential experiments stay isolated. Feedback enters only after scoring. Judge rubric calibration, paired differences, failures, uncertainty and overhead appear in artifacts. Offline smoke scores remain labelled as scripted. |
| P5: Integrate release and visibility | Extend `waku/ops/release_gate.py`, ContractGuard trace summaries and the existing dashboard views. | Offline success cannot imply unmeasured quality success. Reviews expose memory provenance and lifecycle state. A strict quality profile produces a reproducible verdict with coverage and threshold evidence. |

P1 precedes memory behavior changes so later phases have a comparable baseline.
P2 precedes P3, and P4 validates their combined effect. P5 consumes the metrics
and states established by P1 through P4. Initial rollout will keep the upgrade
opt-in until its required engineering checks and declared quality profile pass.

Focused tests will accompany each phase. Temporary isolated mutations will prove
that critical guards fail when approval is bypassed, scope filtering is removed,
revoked memory is recalled or annotations reach prediction input. The full offline
suite and lint will run before integration. Live judge runs will record unavailable
credentials or limits explicitly. UI changes will receive browser verification;
new routes will receive deterministic route coverage and hosted policy entries.

Implementation will update the foundation, toolchain, evaluation, architecture
and status docs when behavior changes. Results will change only when measured
artifacts support new claims. Waku code will remain MIT.

Hosted policy changes will remain under `hosted/`'s Elastic License 2.0.
