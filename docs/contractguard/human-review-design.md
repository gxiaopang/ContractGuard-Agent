# ContractGuard human review

This design requires a person to confirm or correct an ordinary contract
report before ContractGuard stores its findings in review memory.

Status: implemented; the maintainer approved this design on 2026-10-13 under
[conventions section 2](../context/conventions.md#2-how-much-process-a-change-needs).

## Behavior change

Before this change, `review_contract()` generated validated artifacts and immediately
called `Memory.complete_review()` for positive findings. The Reviews page displayed
saved reports through a read-only route. Neither step recorded a human decision.

For example, a generated report can assess a liability clause as HIGH. The
proposed workflow saves that report as `pending_review`. A person can confirm
HIGH, correct the assessment to MEDIUM with revised reasoning, or reject the
report. Only confirmation or correction permits review-memory writes. Rejection
preserves artifacts and writes no review episode or learned semantic fact.

The implementation will extend the existing ContractGuard workflow and Reviews
page. It will add no dependency, core model tool, graph workflow or agent-loop
approval system.

## States and decisions

| State | Meaning and permitted action |
|---|---|
| `partial` | A model stage failed. The person must regenerate a complete report before approving it. |
| `pending_review` | Generation succeeded. The person can confirm, correct and confirm, or reject the report. |
| `approved` | The server saved a validated human decision. Persistence can start or retry with that decision. |
| `completed` | The server persisted all approved positive findings. Repeated submissions return the saved outcome. |
| `rejected` | The person rejected the report. The server preserves the rejection and writes no review memory. |

The server will distinguish generation completion from human approval and memory
persistence. It will save an approval journal before writing memory. A failure
after approval will leave the decision available for retry without another model
extraction or another human decision. A rejected or completed review will require
a new review revision before its findings can change.

The tool will return `pending_review` after successful ordinary generation. Its
description and session instructions will tell the model to direct the person to
Reviews. The model-facing schema will expose no approval flag or approval tool.
Repeated generation requests will reuse valid pending artifacts without writing
memory or spending another extraction call.

## Artifacts and provenance

The implementation will introduce a versioned result format for new ordinary
reviews and include its version in cache identity. It will preserve the generated
`document.json`, `result.json` and `report.md` as model output.

A separate versioned `human_review.json` journal will record the review id,
source-result digest, decision, reviewer label, server timestamp, accepted
structured findings and a bounded optional note. A corrected decision will
preserve original findings and store corrected findings separately. The server
will render a separate approved report from accepted findings without a model
call. The UI will let the person inspect generated and approved reports.

The server will compute identities, timestamps, artifact paths and source
digests. Requests will supply a saved review id and expected revision, never a
home directory, artifact path, origin or memory record. A local reviewer label
will document who submitted the decision; it will not claim verified identity.
Hosted access will continue to rely on the existing tenant authentication.

The server will validate the complete submitted correction before saving any
decision or memory. It will enforce existing canonical clause names, risk
levels, text bounds, evidence offsets and exact source quotations. It will
reject unknown fields, duplicate clauses, inconsistent findings and stale
source revisions. Comparison data that no longer matches corrected evidence
will be removed from accepted findings rather than presented as revalidated.

## Memory boundary

Ordinary review generation will stop calling `Memory.complete_review()`.
An approval function will load and validate the saved artifacts and journal,
then write only accepted positive findings through the existing memory facade.
The facade will require validated approval provenance for ordinary writes so
another ordinary Python caller cannot silently retain the previous behavior.
An approval boolean supplied by the model will never satisfy that requirement.

Approved review episodes will preserve the human decision reference and approval
timestamp in a versioned record. Retrieval will decode both existing records
and new approved records. Existing records will remain readable as historical
records without acquiring fabricated approval metadata.

The approval action will pass `candidates=[]` when persisting episodes so a
confirmation click can finish without provider credentials or model calls.
It will not learn new semantic patterns during confirmation. Seeded clause
guidance and procedure skills will remain available. A later separately designed
step can propose patterns from approved episodes if semantic learning is needed.

Persistence will use a stable decision identity and timestamp. It will serialize
competing decisions for one review and enforce episode idempotency at the
database boundary. A repeated identical decision will succeed without duplicate
episodes. A conflicting decision or stale correction will fail without writing
memory. A failure after some episode writes will retry only missing writes;
the journal will reach `completed` only after every accepted positive is stored.
An approved report containing no positive findings will complete without an
episode. The existing memory export will reflect successfully persisted data.

## Dashboard and HTTP contract

`GET /api/contractguard` will retain its saved-review list and expose human-review
state, accepted findings and approval metadata for the selected review. The
selected-review projection will include bounded source text for checking and
correcting evidence. Queue views will distinguish reports awaiting review from
reports whose approved findings were stored.

`POST /api/contractguard/review` will accept exactly a saved review id, expected
revision, decision, reviewer label, note and corrected findings when needed.
The handler will validate all input before mutations and return structured
validation, conflict and persistence errors. It will reject benchmark artifacts,
partial reviews and unsafe paths. It will use the existing dashboard request
security checks and apply a bounded JSON request size.

The Reviews page will show `Confirm and save`, `Correct and confirm`, and
`Reject` for pending ordinary reports. The correction form will allow the person
to revise clause presence, evidence quotations, risk level, reasoning and
recommendation. The server will align supplied quotations to the source and
require explicit selection when a quotation occurs more than once. Changes to
missing clauses will require source evidence before creating a positive finding.
The page will preserve unsaved edits across background refreshes, disable actions
during submission and display errors without discarding the correction.

The UI will use existing cards, dialogs, buttons, selects and form controls.
It will use English copy and existing design tokens. The new route will receive
a deterministic route-contract entry and a hosted `PASS` policy for the
authenticated tenant dashboard. The narrower embedded chat session will not
receive permission to submit review decisions. Waku code will remain MIT;
the policy change will remain in Elastic License 2.0 `hosted/`.

## Historical reviews and evaluation artifacts

Existing v1 reports will remain readable. The UI will label ordinary v1 records
as legacy automatic reviews, rather than calling them human approved. The
implementation will neither delete their existing memory nor automatically
rewrite it. Human approval will apply to new ordinary review revisions.

The isolated benchmark predictor currently writes `benchmark_prediction`
episodes to measure full-memory ablations. It will retain that explicit,
evaluation-only behavior so existing experiments keep their stated meaning.
Benchmark records will remain excluded from semantic learning. Their dashboard
labels will identify benchmark predictions and offer no human approval action.
Ordinary review tools will retain no model-controlled benchmark bypass.

## Implementation and validation

The implementation will change `tools/contract_review.py`, the ContractGuard
record validators, the memory facade, `ops/contractguard.py`, the dashboard
handler and `static/js/contractguard.js`. It will update hosted route policy,
route-contract evals and the affected ContractGuard workflow evals. It will
update status, architecture, foundation, toolchain, demo and frontend reference
docs so each describes the implemented approval boundary and benchmark exception.

Deterministic offline evals will cover these behaviors:

- Successful generation saves a pending report and writes no review episodes
  or learned semantic facts; cache reuse also writes nothing.
- Confirmation stores approved findings; correction stores revised findings
  and preserves the original report and source evidence.
- Rejection, partial results, stale submissions and invalid evidence write
  no review memory.
- Repeated and concurrent approvals cannot duplicate episodes; conflicting
  decisions cannot overwrite an accepted decision.
- An interrupted persistence attempt resumes from its saved decision and
  completes without duplicate episodes or new extraction calls.
- Ordinary memory writes require approval provenance; the model-facing tool
  cannot forge approval or select benchmark provenance.
- Legacy reports remain readable; benchmark predictions retain isolated
  evaluation behavior and cannot use the human decision endpoint.
- Dashboard routes preserve tenant boundaries, bounded paths, closed schemas
  and safe rendering of source text, notes and corrected reports.

Verification will run focused ContractGuard and route-policy evals, the full
offline deterministic suite and lint. A deliberate regression will remove the
generation barrier to prove that the no-memory-before-approval eval fails.
Browser verification will exercise confirmation, correction, rejection, errors
and repeated submission in light and dark themes. Any unavailable checks will
be reported with their concrete limitation.
