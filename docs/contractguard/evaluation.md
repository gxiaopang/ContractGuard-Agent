# ContractGuard evaluation

ContractGuard evaluates extraction against CUAD spans and evaluates risk against
separate authored examples. These tasks use different reference data. CUAD
clause presence never supplies a HIGH, MEDIUM or LOW risk label.

## Dataset and category coverage

The loader reads local SQuAD-style JSON with the standard library. It makes no
download or model request. It validates answer offsets against complete source
contexts, checks impossible-answer flags, and requires annotation coverage for
every scored target. Different contexts within one document are rejected;
repeated identical contexts can be merged without changing character offsets.

The [official category list](https://raw.githubusercontent.com/The-Atticus-Project/cuad/67faa0e6023b04fcaae6cc09497ab00e5d63a2a2/category_descriptions.csv)
and the published `test.json` support eight ContractGuard targets:

| ContractGuard target | CUAD question-id suffix |
|---|---|
| Termination for Convenience | Termination For Convenience |
| Uncapped Liability | Uncapped Liability |
| Cap on Liability | Cap On Liability |
| IP Ownership Assignment | Ip Ownership Assignment |
| Non-Compete | Non-Compete |
| Change of Control | Change Of Control |
| Governing Law | Governing Law |
| Exclusivity | Exclusivity |

CUAD has no standalone Indemnification or Confidentiality Obligations category.
The runner records these targets as unscored, rather than interpreting absent
annotations as negative clauses. The ten-target selector scores the eight
supported categories. A selection with no supported category is rejected.

The [official release archive](https://github.com/The-Atticus-Project/cuad/blob/67faa0e6023b04fcaae6cc09497ab00e5d63a2a2/data.zip)
contains a contract-level test split with 102 documents. CUAD carries
[CC BY 4.0 attribution requirements](https://huggingface.co/datasets/theatticusproject/cuad-qa/blob/main/README.md).
The loader identifies the pinned official test file by its byte hash. Other
valid SQuAD files are labelled custom inputs with a caller-declared split.
Generated artifacts and downloaded datasets do not belong in Git.

## Span matching version 1

The primary rule uses character intersection over union at a fixed threshold
of 0.5. Intervals use Python character offsets `[start, end)`. Every gold and
predicted quotation must agree with the source at those offsets.

Character IoU divides intersecting characters by the interval union. Token IoU
uses source token positions intersecting each span; identical words at another
location cannot create overlap. Exact matching requires equal offsets and text.
Normalized matching compares NFKC, case-folded word sequences and requires an
intersecting source interval. Python's Unicode `\w+` defines words, including
underscores; other punctuation and whitespace do not distinguish normalized
sequences. Exact, normalized and token-IoU rules are available as
explicit alternatives; an ablation uses one rule for every arm. Both IoU rules
keep the threshold at 0.5. There is no per-arm threshold override.

Duplicates at the same offsets count once in either span list. Repeated text at
different offsets remains distinct. The matcher computes maximum-cardinality
one-to-one matching, so one prediction cannot satisfy several gold spans and
greedy edge selection cannot lower the number of valid matches. Each matched
pair contributes one TP; unmatched predictions contribute FP; unmatched gold
spans contribute FN. Multiple responsive spans are scored separately.

Precision is `TP / (TP + FP)`, recall is `TP / (TP + FN)` and F1 is their harmonic
mean. A zero denominator produces zero. Micro scores pool counts across targets;
macro scores average each selected target's scores, including targets with zero
gold support. Clean negative pairs add no span TP. Failed extraction pairs are
reported separately and receive no negative credit; positive gold spans remain
FN. Valid extraction survives a later risk-stage failure and remains scoreable.

Critical-clause miss rate counts positive contract/category pairs with no
matched span for Termination for Convenience, Uncapped Liability and Cap on
Liability. This fixed subset describes this experiment's priorities, not a
general legal assessment. A partial match can avoid a presence miss while
still reducing span recall.

## Memory and leakage policy

The three arms hold bundled canonical procedures constant. Baseline receives
no semantic or episodic context. Semantic-only receives gated, fixed domain
seeds and accumulates no episodes. Full-memory receives gated domain seeds and
may retrieve previous contracts' validated prediction episodes. Semantic seeds
remain fixed: benchmark predictions never promote semantic facts.

Each arm owns a newly created SQLite home beneath its run directory. Existing
run directories are rejected; no runtime data or prior results are reset.
Current document ids are excluded before history top-k, and repeated source
documents are rejected by the loader. All requested extraction and risk stages
finish before a full-memory contract can contribute prediction episodes.
Partial reviews do not accumulate episodes.

The predictor accepts only a source id and complete contract text. The scoring
side retains annotations separately and never sends them to model calls or
memory writers. No gold-derived examples, seeds, confidence thresholds or
template snippets enter agent context. User-authored procedures and optional
remote stores or slot gates do not participate in the benchmark.

All arms use the same provider, main and small models, prompts, parsing rules,
targets, matcher and ordered contracts. The existing provider adapter leaves
temperature at the provider default; metadata records null temperature and
this policy explicitly. Input order is deterministic, with an optional recorded
shuffle seed. Predictions retain the production finding schema. No review
artifact cache is used.

The benchmark calls existing stage functions directly. It preserves complete
CUAD text, including documents longer than the interactive tool's 200,000-character
limit. Provider context limits still apply. Gate failures, failed model calls,
extraction errors and risk errors appear separately in artifacts; any such
failure marks the run partial. Valid extraction still contributes span metrics.

## Risk evaluation and interpretation

Authored examples specify expected risk levels and the commercial perspective
being tested. The risk runner first extracts evidence and then calls the same
risk scorer. It reports label agreement, per-level counts, failed cases and
source-grounding checks. These small examples measure agreement with stated
rubrics and do not establish general legal accuracy.

LLM-as-Judge can later assess reasoning, recommendations and readability through
the repo's separate judge tier. Such opinions cannot replace deterministic span
matching and cannot turn CUAD presence annotations into risk labels.

## Reproducibility and artifacts

Each run saves `config.json`, `prompts.json`, per-arm `predictions.jsonl` and
`metrics.json`, and a machine-readable plus Markdown comparison table. Metadata
records UTC timestamp, Git commit and dirty state, code and prompt hashes,
dataset byte hash and split, requested and scored targets, contract order and
shuffle seed, model configuration, memory policy, retrieval limits, matcher and
fixed threshold.
Telemetry records model calls, tokens, elapsed time, retrievals and retrieved
items. Estimated cost uses the existing price table when a provider or model has
a known rate. Unknown providers and calls with unavailable failure usage produce
null cost. SDK wire retries remain opaque; call counts represent logical calls.

Offline smoke runs use a labelled scripted stand-in that reads source text,
never annotations. Their scores verify artifact generation and experiment
wiring. A claim that memory improves extraction requires live results on the
same held-out contracts, stable matching rules, isolated arms, error accounting
and repeated runs that address model variability. A lower score must be reported
as a lower score. CUAD gold is a scoring dataset; agent memory contains domain
knowledge and prior predictions. Mixing them would test answer repetition.

## Commands

Run the authored fixtures without a provider key:

```bash
python scripts/run_eval.py --smoke --ablation --seed 42
python scripts/run_eval.py --smoke --risk
```

The CLI prints the new artifact directory. A repeated `--run-id` fails instead
of overwriting results. `--output` selects an artifact parent directory and
`--limit` selects contracts after the optional seeded shuffle.
`--workers` selects one to 32 concurrent clause stages within each contract;
the default is one. Contracts remain sequential, SQLite access stays on the
owner thread, and a complete contract contributes episodes only after all
clause stages finish. Every arm uses the same worker count, recorded as
`clause_workers` in its configuration. Parallel execution changes latency;
compare latency only between runs with the same execution configuration.
An HTTP 402 payment failure stops new model requests, later contracts and later
arms. The runner preserves the processed prefix and records `provider_unavailable`
plus the status code. Those artifacts do not establish a complete comparison;
restore provider access and start a fresh run id with new isolated homes.

Download the pinned [official release archive](https://github.com/The-Atticus-Project/cuad/raw/67faa0e6023b04fcaae6cc09497ab00e5d63a2a2/data.zip)
manually and extract its `test.json` to `artifacts/datasets/cuad/test.json`.
The archive SHA-256 is
`f8161d18bea4e9c05e78fa6dda61c19c846fb8087ea969c172753bc2f45b999a`;
the extracted test file SHA-256 is
`007b6a40b0c65247f881627375c3d2e9b6aeeb5dfa89957494feed0765a1a073`.
Retain the upstream attribution when sharing CUAD text or derived datasets.

Run an offline integration check on that local file:

```bash
python scripts/run_eval.py --smoke --ablation \
  --dataset artifacts/datasets/cuad/test.json --split test --seed 42
```

A live run uses Waku's configured provider and spends model calls:

```bash
python scripts/run_eval.py --ablation --clause-types 10 \
  --dataset artifacts/datasets/cuad/test.json --split test --seed 42
python scripts/run_eval.py --mode baseline \
  --clause-types "Governing Law" --dataset artifacts/datasets/cuad/test.json
python scripts/run_eval.py --risk
```

`--provider`, `--model` and `--small-model` override the existing provider settings.
`--mode semantic-only` and `--mode full-memory` select individual arms.
`--match-rule` selects a recorded alternative policy for an entire run.
`--split train` or `--split custom` labels caller-supplied files; the official
test byte hash cannot be relabelled as another split. Live quality measurements
must identify the chosen provider settings and run artifacts.

## Validated engineering behavior

The 2026-10-06 offline run loaded all 102 official test contracts and validated
525 distinct gold spans for the eight mapped categories. Its three arms completed
with separate homes and no stage failures. Baseline kept zero facts and episodes;
semantic-only kept ten fixed facts and zero episodes; full-memory kept ten fixed
facts and 166 predicted episodes. The authored risk path completed ten examples.
These stand-in runs verify implementation behavior and supply no live quality or
memory-improvement claim.

Deterministic evals cover source offsets, duplicate and repeated spans,
maximum-cardinality matching, error accounting, micro/macro metrics, isolated
homes, source-only model input, history timing, risk-reference exclusion and CLI
artifacts. Temporary mutations raised the IoU threshold and enabled episodic
writes in semantic-only mode; the corresponding evals failed. The repository
retains the original policies.
