# ContractGuard demo and saved reviews

The ContractGuard demo reviews a deterministic sample in a new local home and
shows its saved artifacts in Waku's existing dashboard. Offline runs use the
labelled source-only stand-in from Phase 3. Live model quality remains unmeasured.

## Implementation design

The Phase 4 request authorizes this dashboard view under
[conventions section 2](../context/conventions.md#2-how-much-process-a-change-needs).
The implementation follows these decisions:

1. The demo reuses the evaluation predictor, matching policy and report renderer.
2. A seeded sample runs sequentially with isolated memory and fixed procedures.
3. Atomic progress snapshots record pending, running and finished contracts.
4. Saved reviews use the existing document, result and Markdown report contracts.
5. A read-only route reads only the configured home's artifacts and local counts.
6. The Reviews page uses existing UI primitives and the safe Markdown renderer.
7. Hosted policy permits this tenant-local read; no route starts a model request.
8. The provider setup gate allows saved reviews while keeping chat unavailable.

The dashboard derives risk counts from validated findings. It reads extraction
metrics from generated evaluation artifacts. Neither memory growth nor scripted
scores establish that later reviews improve quality.

## Run the demo

Run five authored contracts without an API key:

```bash
python scripts/demo_review.py --contracts 5 --show-memory-growth --seed 42
```

The command defaults to `offline_smoke` and prints its artifact directory and
isolated dashboard home. It creates a new run beneath `artifacts/demo/`; it
never resets an existing home. `--run-id` chooses a unique name and fails if
that name already exists. `--output` chooses a different artifact parent.
The command prints and flushes its dashboard command before the first review,
so a second terminal can observe progress during a live run.

Start the existing dashboard with the home printed by the demo:

```bash
WAKU_HOME=/absolute/path/to/artifacts/demo/RUN_ID/home python -m waku dashboard
```

Open `http://localhost:7777/#reviews`. Waku also links saved reviews from its
provider setup screen. This read-only page requires no model key and hides chat
when the configured provider cannot serve a turn.

`--mode baseline`, `--mode semantic-only` and `--mode full-memory` select the
Phase 3 memory policies. Full-memory is the default. `--clause-types` accepts
`10` or quoted canonical target names. The authored fixtures and CUAD score
eight mapped targets; the command prints the two unsupported targets explicitly.
`--dataset` selects a local SQuAD-style file and `--split` labels its source.
The seed shuffles contracts before selecting the requested sample. The command
rejects a count larger than the dataset or the dashboard's 200-review limit.

A live demonstration requires an explicit local dataset and uses Waku's existing
provider settings:

```bash
python scripts/demo_review.py --live --dataset artifacts/datasets/cuad/test.json \
  --contracts 5 --show-memory-growth --seed 42
```

This command spends provider calls. Live sample scores describe the selected
contracts and do not establish general legal accuracy or a memory advantage.
The demo does not rerun all three ablation arms; `scripts/run_eval.py --ablation`
remains the comparison path. The demo never supplies annotations to a model or
promotes benchmark predictions into semantic facts.

## Artifacts and dashboard data

Each run saves configuration, prompt snapshots, predictions and cumulative
extraction metrics beside its new `home/`. Reviews under
`home/reviews/<review_id>/` use the existing `document.json`, `result.json` and
`report.md` contracts. The demo generates reports from validated structured
findings; report prose never supplies extraction metrics.

`home/contractguard/progress.json` records the queue, current contract, completion
status and memory snapshots. The demo replaces it before a model call and after
each finished review. `home/contractguard/metrics.json` records cumulative
generated extraction metrics. Separate file replacements are atomic; they do
not form a transaction across reports, memory, progress and metrics. An
interrupted run keeps its home, marks progress failed and clears its current
contract. The dashboard can temporarily report metrics as updating.

`GET /api/contractguard` reads only the configured home. Its optional
`review_id` query selects a 64-character saved id, never a file path. The reader
checks document structure, exact source evidence, stage schemas, artifact paths
and report equality before exposing a report. Files must remain inside the home
after symlink resolution and must fit an eight-megabyte read budget. The view
reads at most 200 reviews and reports omitted or invalid artifacts.

The frontend polls this route through the existing refresh mechanism and tags
timer-driven reads with `X-Waku-Background`. User selections remain ordinary
reads. Hosted policy permits the route because it reads only the tenant's own
home and starts no model request. The policy implementation remains under
Elastic License 2.0 and is not MIT. The Waku reader and frontend remain under MIT. No code moves
between those license boundaries and no copied design asset changes.

Review Progress derives completed, pending and incomplete counts from queue
snapshots and validated artifacts. Native reviews outside a demo have no saved
queue, so the view shows unknown pending work. A ready persistence journal does
not count as a completed review. Risk Matrix computes HIGH, MEDIUM, LOW,
NOT FOUND and ERROR counts from validated findings. Unreviewed categories have
zero counts; failed stages never count as missing clauses.

Memory Growth reads persisted after-review snapshots. Current semantic and review
episode counts come from read-only local SQLite queries; procedure counts come
from bundled clause skills. Evaluation Summary projects generated micro/macro
precision, recall, F1, model calls, tokens, latency, mode and available cost.
The view labels scripted runs explicitly and makes no CUAD risk-quality claim.
Review Report uses the existing safe Markdown renderer and shared typography.

## Learning checkpoint

Dashboard state originates in saved reviews and demo snapshots, rather than
frontend example constants. Review starts and finishes become observable when
the demo replaces its progress file. Polling makes those changes visible without
adding a second event transport or changing Waku's loop.

Runtime traces describe interactive harness turns and their gate, tool, model and
memory events. Benchmark/demo artifacts describe a controlled experiment's
sources, configuration, predictions and deterministic scores. The demo calls
existing review stages directly and does not fabricate interactive turn traces.
Risk counts and progress totals are computed by the reader. Memory progression,
token telemetry and extraction metrics are persisted by the runner. Current
memory counts are read from local storage. These sources explain every number
the dashboard displays.
