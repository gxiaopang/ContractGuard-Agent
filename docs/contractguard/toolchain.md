# ContractGuard review toolchain

ContractGuard reviews plain text through one opt-in `review_contract` tool.
`WAKU_CONTRACT_REVIEW=1` enables the tool and the Phase 1 local memory foundation.
The five review stages remain ordinary Python functions in `waku/tools/`.
The implementation request authorizes this tool and memory-context design under
[conventions section 2](../context/conventions.md#2-how-much-process-a-change-needs).

## Execution and storage design

`build_registry()` registers a `Tool` with a name, argument schema and function.
A provider's `tool_use` block reaches `run_loop()`, which calls
`ToolRegistry.execute()`. The workflow returns a JSON string. The loop appends
that string as a `tool_result` block for the next provider call. This reuses
Waku's client, observer and loop without adding another graph engine.

The workflow parses the original text, assembles per-clause memory and one
procedure, extracts source spans, assesses detected clauses and optionally
compares them against retrieved semantic guidance. Each stage uses the same
configured main-model client. Gate and consolidation calls use the existing
small-model setting. Calls use the provider adapter's supported Messages shape;
no separate DeepSeek client or new dependency is introduced.

`Memory.review_context()` supplies separate semantic and episodic values.
Retrieval remains gated. The workflow passes the current document id as an
exclusion before history is limited. A gate that names another clause supplies
no context for the current task. Procedures are selected by canonical skill
name, one clause at a time, so a full review can use all ten runbooks despite
the conversational loader's two-skill limit.

`parse_contract()` preserves source text and assigns SHA-256 document identity.
Sections use numbered, Markdown and uppercase heading cues; paragraphs split
at blank lines inside each section. Offset-based ids remain unique when
headings repeat. Empty text parses to empty collections; the review workflow
rejects empty input before spending model calls. Segmentation is a heuristic,
not a legal interpretation. Every extraction sees the entire supplied text.

Extraction records carry canonical clause type, found flag, source spans,
confidence and rationale. Positive findings require nonempty exact quotations
at Python character offsets. Negative findings require no spans. Validators
reject extra fields, duplicate spans, boolean offsets and invalid confidence.
The extractor asks the model for each quotation and its zero-based occurrence
in the complete source. Python locates those quotations, including repeated
wording, and computes offsets locally. The model never counts characters.
Risk records carry HIGH/MEDIUM/LOW, reasoning, a recommendation and references
to validated extraction spans. Clause presence does not determine risk.
Comparison records carry four lists of differences; every entry references
source evidence and quotes retrieved guidance. Missing guidance skips comparison
instead of inventing a template.

`generate_report()` validates structured findings again and renders Markdown
without a model call. It distinguishes detected clauses, missing clauses and
failed stages. It escapes variable text so contract quotations and model prose
cannot inject additional report headings or HTML. The benchmark evaluator reads
structured extraction records, never prose headings or risk summaries.

Successful and partial results are local artifacts in
`<WAKU_HOME>/reviews/<review_id>/document.json`, `result.json` and `report.md`.
The review id hashes source identity, name, ordered targets, comparison option,
origin, main and small models, provider and prompt version. Names never become filesystem paths.
A complete cached result is revalidated against its source before reuse.
Sequential repeated successful calls therefore spend no model calls and add no episodes.
Partial reviews can be retried and do not teach memory.

Before memory persistence, a ready artifact preserves validated findings and a
single timestamp. A persistence failure leaves that artifact ready for retry.
The retry reuses those findings and timestamp, so the Phase 1 episode writer
can suppress duplicate summaries. A completed result is cached only after all
positive findings have been handed to `Memory.complete_review()`. That writer
stores episodes and separately considers reusable lexical patterns. A review
with only missing clauses stores a report but no positive review episode.

The Python workflow accepts caller-controlled `benchmark_prediction` provenance;
the model-facing tool always uses ordinary review provenance. Benchmark-origin
reviews cannot teach semantic patterns. The [evaluation runner](evaluation.md)
isolates memory homes, controls cache/retrieval/procedure policies and keeps
annotations outside inputs.

## Usage and result contract

Start Waku with `WAKU_CONTRACT_REVIEW=1` and ask it to review the complete plain
text for specified clauses. The model calls `review_contract` with `text`, an
optional `contract_name`, optional `clause_types` and optional `compare_guidance`.
The tool reviews all ten canonical clauses when `clause_types` is omitted and
attempts comparison by default when the gate supplies semantic guidance.

Python callers can reuse an existing Waku instance:

```python
from waku.tools.contract_review import review_contract

result = review_contract(
    app.memory,
    text="This agreement is governed by California law.",
    contract_name="Sample Agreement",
    clause_types=["Governing Law"],
    compare_guidance=False,
)
print(result["status"])
print(result["artifacts"]["report"])
```

Each result contains schema `contractguard_result_v1`, document and review ids,
request metadata, a timezone-aware timestamp, status, findings, report and
artifact paths. The returned `cached` flag describes reuse and is absent from
saved JSON. Status is `completed` or `partial`; an internal `ready` journal
means model stages finished and memory persistence still needs a retry.

Each finding contains `clause_type`, `status`, `extraction`, `risk`, `comparison`,
`memory_used` and `error`. Finding status is `found`, `not_found` or `error`.
Failed stages retain already validated outputs and supply a stage name with a
sanitized failure message. Evaluators can read `finding["extraction"]` directly
and must preserve errors as failures. Report formatting cannot change these
predictions. Plain-text extraction has no gold-label or CUAD input field.

Artifact validation rejects inconsistent cached documents, reports and findings.
Writes replace individual files atomically; SQLite episode and fact writers
remain separate operations. A ready journal supports retry after persistence
failure; it does not provide a transaction across every file and memory write.
The cache does not coordinate simultaneous writers in different processes.

## Scope and limits

The tool accepts plain text up to 200,000 characters and one to ten unique
canonical clause types. It never silently truncates a source document. It
supports SQLite stores and the existing provider registry. The evaluation runner
loads CUAD and scores extraction through direct stage calls with complete text.
The [saved-review dashboard](demo.md) displays these validated native artifacts.
PDF ingestion remains later work.
Live extraction, risk reasoning and recommendations remain
unmeasured; offline evals verify schemas, routing and scripted results.
Provider context and output limits can still cause failures for accepted input.

The tool returns partial results when a model stage fails. An error is not a
negative finding, and a failed risk call never receives a fabricated risk label.
The workflow does not persist review knowledge until every requested stage has
succeeded or been explicitly skipped. Evidence validation proves that a quote
exists in the source, not that a live model interpreted it correctly.
