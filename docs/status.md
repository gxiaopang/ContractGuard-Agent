# Status

**What is true right now.** This file is rewritten when behavior changes.

Read this before opening a PR or filing an issue; known failures appear below.

**Last updated:** 2026-10-13

---

## What works

The four pillars run: the loop, memory (semantic + episodic + procedural with
a retrieval gate), tools, and both eval tiers. `waku`, `waku dashboard`,
`waku voice`, `waku telegram`, `waku discord`, `waku brief` and
`waku connect google` all start.

**Deterministic evals run offline**, with no API key; live cases skip without
one. CI runs the offline tier on every PR along with
ruff, the skills validator, and a check that `.env.example` still matches the
integrations registry.

**ContractGuard reviews plain text through an opt-in tool.**
`WAKU_CONTRACT_REVIEW=1` enables `review_contract`, ten clause procedures, gated
SQLite memory, evidence-checked extraction, separate risk assessment and optional
guidance comparison. The tool saves structured findings and a Markdown report,
then waits for human confirmation or correction in Reviews before storing
approved positive findings. Rejection writes no review memory. Confirmation
calls no model and learns no semantic patterns.
[ContractGuard toolchain](contractguard/toolchain.md) explains usage, partial
failures and cache behavior. Offline evals verify scripted results; live model
quality remains unmeasured.

**ContractGuard evaluates local CUAD files with isolated memory ablations.**
`scripts/run_eval.py` supports baseline, semantic-only and full-memory arms,
frozen span matching, extraction metrics and separate authored risk rubrics.
CUAD covers eight of the ten review targets; the runner records the other two
as unscored. Offline smoke runs exercise all three arms without API calls.
[ContractGuard evaluation](contractguard/evaluation.md) explains commands,
leakage controls and artifacts. Live CUAD quality and memory improvement remain
unmeasured.

**ContractGuard's opt-in memory upgrade runs without manual evaluation labels.**
`WAKU_CONTRACT_MEMORY_UPGRADE=1` enables scoped memory, corrections and lifecycle decisions.
`scripts/run_eval.py --upgrade` generates cases for four isolated memory arms.
Frozen and sequential runs need no manual labels. Live judges remain uncalibrated;
risk and legal quality stay unverified. [Upgrade design](contractguard/memory-eval-design.md) explains limits.

**ContractGuard shows saved reviews and demo progress in the dashboard.**
`scripts/demo_review.py --contracts 5 --show-memory-growth --seed 42` reviews
authored fixtures offline in a fresh home and prints its dashboard command.
The Reviews page displays validated reports, progress, risk counts, memory
snapshots and generated extraction metrics. Ordinary reports support confirmation,
correction and rejection. Benchmark artifacts retain their evaluation-only
memory policy. Dashboard review actions start no model calls. [ContractGuard demo](contractguard/demo.md) explains the artifact
flow, live-run option and limits. Scripted scores establish no live quality or
memory advantage. [Measured results](contractguard/results.md) records offline
scores and overhead. [Interview notes](contractguard/interview-notes.md) provide
engineering resume bullets; performance bullets await finalized live measurements.

**0.1.8 is on PyPI and on GitHub Releases.** Pushing a `v*` tag publishes to
both, so the repo's "Latest" release always matches `pip install waku-agent`.

**The dashboard uses the Waku Memory design system**, and
`test_design_system.py` keeps it from drifting. See
[context/design-system.md](context/design-system.md).

## Known broken

These failures already have tracked issues.

| What | Where | Fix in flight |
|---|---|---|
| The model picker offers OpenAI models that 404 on use | #137 | #178 |
| GPT-5.6 tool calls fail on Chat Completions | — | #146 |
| OpenCode Zen fails with a rate-limit error | #112 | #113 |
| Google Calendar sign-in has no bundled OAuth client, so `waku connect google` needs your own `~/.waku/credentials.json` | — | — |

**Providers are the recurring theme.** Three of the items above are one
provider or another, and there is no single place that says which providers
are known-good today. Until there is, treat the model picker as a list of
things that *might* work.

## What is deliberately not built

Not a framework, not multi-agent, not production — true of `waku/`, see
[architecture.md](architecture.md). `hosted/` runs that same code as a
deployment instead; spec 001 designs it, and none of it exists in this repo
yet.

Additionally, and worth stating because people ask:

- **No Windows CI.** The Windows bugs so far (#140, #141, both fixed) were
  found by contributors, not by us. Every Windows claim in this repo is
  untested.
- **The judge evals are not in CI.** `make gate` runs deterministic evals at
  100% plus a judge threshold, and CI runs only the first half. The judge tier
  needs an API key, which CI does not have.
- **No provider smoke check.** Nothing verifies that a model in the picker
  resolves, which is why #137 reached a user.

## Open questions

1. **Where the memory pillar ends and Waku Memory begins.** This repo's memory
   is local, single-machine, and yours. Waku Memory is the same memory across
   several agents, and it is a paid hosted service. Both are true and the
   README does not yet say either plainly, so a reader has to work out the
   difference alone. Hosted waku (`hosted/`, spec 001) adds a third case: a
   tenant's memory lives on the operator's VM, not the person's own machine,
   but it is still that one tenant's own directory, not Waku Memory. What
   hosted waku keeps there is not written down yet; a later task in spec 001
   records it once `hosted/` exists.

## Not in the repo

Deliberately absent, so nobody goes looking:

- Filming and demo notes — production material, not product documentation
- Session handoffs — this file replaces them
- Plans and specs — they belong with the work, not in `docs/`
