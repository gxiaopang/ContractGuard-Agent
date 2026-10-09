# ContractGuard measured results

ContractGuard has completed offline engineering runs, but live model quality
and memory benefit remain unmeasured. Every run below used the scripted,
source-only `source-rules-v1` stand-in with provider `scripted` and execution kind
`offline_smoke`. No run below made a provider request.

## Official CUAD integration run

The 2026-10-06 run processed all 102 official test contracts in each of three
isolated arms. The loader validated 525 distinct gold spans across eight mapped
categories. Seed 42 fixed the shuffled order. Character IoU >= 0.5 and
maximum-cardinality one-to-one matching defined `contractguard_span_v1`.

| Arm | Final facts | Final episodes | Scripted logical calls | Retrieval events | Semantic items retrieved | Episodic items retrieved |
|---|---:|---:|---:|---:|---:|---:|
| baseline | 0 | 0 | 982 | 0 | 0 | 0 |
| semantic-only | 10 | 0 | 1,798 | 816 | 816 | 0 |
| full-memory | 10 | 166 | 1,798 | 816 | 816 | 1,316 |

All arms completed without extraction failures, risk failures, gate failures or
failed model calls. All arms retained identical canonical procedures; baseline
removed only semantic and episodic context. Full-memory wrote predicted review
episodes after each complete contract. Benchmark runs never changed semantic
seeds or used gold annotations as model context.

The stand-in produced the same extraction results in every arm:

| Scope | Precision | Recall | F1 |
|---|---:|---:|---:|
| Micro, scripted CUAD extraction | 0.337349 | 0.106667 | 0.162084 |
| Macro, scripted CUAD extraction | 0.145688 | 0.082629 | 0.099932 |

Each arm recorded 56 TP, 110 FP and 469 FN. All 86 positive critical-clause
pairs lacked a matched span, giving a critical-clause miss rate of 1.0.
These weak source-rule scores test matching and error accounting; they do not
measure LLM extraction quality. The stand-in does not adapt its extraction to
retrieved context, so equal scores cannot establish whether memory helps a
live model. Memory added 816 scripted gate calls and additional retrieved items.

Input and output tokens were zero because these calls used no provider.
Estimated cost was null. Recorded local mean latency includes scripted execution
and storage work; it does not estimate live provider latency or token savings.
CUAD risk accuracy remained null because CUAD has no reference risk labels.

## Five-contract dashboard demo

The 2026-10-06 full-memory demo used five authored fixtures, seed 42 and the same
scripted stand-in. The shuffled order was Technology, Services, Distribution,
Consulting and Supply. Its memory snapshots showed:

| Completed contracts | Semantic entries | Episodic entries | Procedural skills |
|---:|---:|---:|---:|
| 0 | 10 | 0 | 10 |
| 1 | 10 | 3 | 10 |
| 2 | 10 | 6 | 10 |
| 3 | 10 | 9 | 10 |
| 4 | 10 | 12 | 10 |
| 5 | 10 | 15 | 10 |

The demo recorded 95 scripted logical calls, 40 retrieval events and 69
retrieved items: 40 semantic items and 29 prior episodes. All five reviews
completed. The authored examples yielded 15 TP, zero FP and zero FN, with
micro and macro F1 of 1.0. These small, authored fixture scores verify demo
wiring and do not establish CUAD quality, legal accuracy or memory benefit.

The separate ten-example authored risk smoke run recorded 3/10 agreement
with its reference labels. The scripted scorer always chose LOW. This result
exercises risk evaluation and does not measure a live risk model.

## Artifact provenance and reproduction

The original local artifacts remain at these paths on the validation machine:

| Run | Local artifact directory |
|---|---|
| Official 102-contract ablation | `/tmp/waku-phase3-validation/official-102-final/` |
| Ten-example risk smoke | `/tmp/waku-phase3-validation/risk-ten-final/` |
| Five-contract dashboard demo | `/tmp/waku-phase4-validation/final-demo-visible/` |

These temporary directories are not bundled with the repository. Keep an
artifact archive when sharing a claim; a later checkout can reproduce the
commands below but cannot open another machine's temporary files.
Each run saves configuration and prompt snapshots, code and dataset hashes,
source order, predictions and generated metrics. The official ablation also
saves `comparison.json` and `comparison.md`. The demo saves native reports
and progress snapshots inside its isolated `home/`.

The official test source is pinned to upstream revision
`67faa0e6023b04fcaae6cc09497ab00e5d63a2a2`, with test-file SHA-256
`007b6a40b0c65247f881627375c3d2e9b6aeeb5dfa89957494feed0765a1a073`.
[Evaluation instructions](evaluation.md#commands) link the official release,
category mapping and dataset attribution. Obtain that release manually before
running the official-file command.

From an installed checkout, reproduce offline checks in new run directories:

```bash
python scripts/run_eval.py --smoke --ablation --clause-types 10 \
  --dataset artifacts/datasets/cuad/test.json --split test --seed 42
python scripts/run_eval.py --smoke --risk
python scripts/demo_review.py --contracts 5 --show-memory-growth --seed 42
```

Every command prints a fresh artifact directory and rejects an existing run id.
None resets an existing home. A live run requires configured provider settings
and omission of `--smoke`; the demo additionally requires `--live` and an
explicit dataset. [Evaluation instructions](evaluation.md) specify live commands
and telemetry limits.

## Claims that require further measurement

A memory-quality claim requires live baseline, semantic-only and full-memory
runs on the same held-out contracts, with fixed procedures, prompts, matcher
and source order. Preserve failure accounting and all artifacts. Repeat runs
to assess model variability, and report losses as well as gains. Latency and
token-cost claims require metered live calls; unavailable costs remain null.
Risk claims require independent risk rubrics or expert labels. Interview and
resume material must name offline results as offline until those measurements
exist.
