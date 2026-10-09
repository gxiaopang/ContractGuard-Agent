<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/brand/waku-mark-on-dark.svg">
  <img src="docs/brand/waku-mark-on-light.svg" alt="Waku" width="76" align="right">
</picture>

# waku-agent

**Your own AI assistant. On your laptop. In code you can read in an afternoon.**

Meet **Waku** — a local-first personal assistant that shows the four pillars behind every
serious agent: **Harness · Loop · Memory · Eval/LLM-Ops**. No frameworks hiding the good parts.
Built by [seanchen.io](https://seanchen.io).

This checkout adds **ContractGuard Agent**, an opt-in contract reviewer built on Waku.
[Try the offline demo](#contractguard-agent) or read its [architecture](docs/contractguard/architecture.md).

- **Local-first.** Your memory is one SQLite file. Open it. Read it. It's yours.
- **Memory is the hero.** Semantic + episodic + procedural — with a gate that decides *whether*
  to remember, and a pass that decides *what* to keep.
- **The loop is ~95 lines** of plain Python. Step through it.
- **Watch it think.** A local dashboard lights up every message as it flows through the harness.
- **Eval built in.** Deterministic tests *and* LLM-as-judge, side by side, with a release gate.

![waku-agent architecture — the whiteboard](docs/architecture-whiteboard.png)

> The system-design whiteboard from the series.
> Every box maps to a file — see [the architecture](docs/architecture.md).

**▶ [Watch the 20-min code walkthrough](https://www.youtube.com/watch?v=rvRyBhILrls&list=PLE9hy4A7ZTmpGq7GHf5tgGFWh2277AeDR&index=42)** — the loop, the memory pillars, the evals, the Telegram gateway and the "Waku Waku" wake word, live.

**[Waku Memory](https://www.waku.one)** — the same memory in Claude Code, Codex, Grok Bot and this agent: [waku.one](https://www.waku.one) · [docs](https://www.waku.one/docs)

[YouTube](https://www.youtube.com/@SeanAIStories) · [X](https://x.com/ShenSeanChen) · [LinkedIn](https://linkedin.com/in/shen-sean-chen) · [Instagram](https://www.instagram.com/sean_ai_stories) · [TikTok](https://www.tiktok.com/@sean_ai_stories) · [Discord](https://discord.gg/ebbdvSCXqu) ·
[哔哩哔哩](https://space.bilibili.com/479332937) · [小红书](https://www.xiaohongshu.com/user/profile/5cf02cfb0000000005014371) · [抖音](https://www.douyin.com/user/MS4wLjABAAAAWCkd62_e8q4n-S34LIL04HsYN3m03l8MFdVYZToojP8)

### [Buy me a coffee](https://buy.stripe.com/5kA176bA895ggog4gh) — it keeps this repo (and the videos) coming

## Quickstart

Just want to run it:

```bash
pip install waku-agent
waku                                    # talk to your Waku in the terminal
waku dashboard                          # …or the browser cockpit → localhost:7777
```

It will tell you which key to set the first time. Want to **read the code** (the
point of this repo) or contribute — clone it instead:

```bash
git clone https://github.com/ShenSeanChen/waku-agent && cd waku-agent
uv venv && uv pip install -e .          # create the env + install the `waku` command
cp .env.example .env                    # pick a provider, paste ONE key
uv run waku                             # talk to your Waku in the terminal
uv run waku dashboard                   # …or the browser cockpit → localhost:7777
```

**Now try it.** *"Remember that Alex prefers morning meetings."* Quit. Restart.
*"Book a catch-up with Alex on Friday."* → it remembers, and books 9am. Your memory is one
file: `~/.waku/state.db`, the same from every folder.

**Use the model you already pay for.** Anthropic (default), OpenAI, Gemini, DeepSeek, MiniMax,
Kimi, GLM, OpenRouter (one key, hundreds of hosted models), OpenCode Zen, or OpenCode Go —
set `WAKU_PROVIDER=`, paste the key, done. One dialect in the loop;
a [~60-line adapter](waku/loop/models.py) handles the rest.

New to it? **[Getting started](docs/getting-started.md)** walks the whole setup, with a check
at the end of every step.

## Connect Waku Memory

Waku's own memory is local. **[Waku Memory](https://www.waku.one)** is the hosted memory you
share across agents: save something in Claude Code, recall it here.

```bash
pip install 'waku-agent[mcp]'           # in a checkout: uv pip install -e '.[mcp]'
waku connect waku-memory                # or /connect waku-memory in the dashboard chat
waku skill export --to claude,codex     # carry Waku's skills to Claude Code and Codex too
```

Your browser opens once to sign in. To connect Claude Code, Codex, Hermes or Grok Bot to the
same memory, see [integrations](docs/integrations.md#share-one-memory-with-your-other-agents-waku-memory).
For live data when it researches, `waku connect treg` (or `/connect treg`) signs in to your own
[treg](https://treg.to) account the same way; see [integrations](docs/integrations.md#live-data-for-research-treg).

## What's inside

| Pillar | In one line | Read more |
|---|---|---|
| **Harness** | gateways (terminal, dashboard, voice, Telegram, Discord, WhatsApp) and tools around one loop | [architecture](docs/architecture.md) |
| **Loop** | ~95 lines of plain Python: reason, act, repeat, with two ways to stop | [the tour](docs/tour.md#the-loop) |
| **Memory** | semantic, episodic and procedural (skills); a gate decides *whether* to remember, consolidation decides *what* to keep | [the tour](docs/tour.md#the-retrieval-gate) |
| **Eval / LLM-Ops** | deterministic tests and LLM-as-judge side by side, a release gate, a trace for every turn | [evals](docs/evals.md) |

**How is this different from ChatGPT or Claude Desktop?** Those are products you *use*. This is a
codebase you *own*: the loop, the memory schema, the gate and the eval harness are all yours to
read and change. Versus the big open-source assistants (OpenClaw, Hermes)? Same architecture,
1/100th the code.

## ContractGuard Agent

ContractGuard turns plain contract text into source-backed clause findings,
risk assessments and a saved report. The project tests whether reusable clause
knowledge and prior review episodes can help later reviews. Live model quality
and memory improvement remain unmeasured.

**Why Waku?** Its visible Harness, Loop, Memory and Eval boundaries let this
project extend tool dispatch, context assembly and persistence without adding
a second agent framework. Its [diagram](docs/contractguard/architecture.md) maps each boundary to code.

**Review workflow.** Set `WAKU_CONTRACT_REVIEW=1` for interactive review.
The existing loop dispatches `review_contract`: parse plain text, retrieve
clause context, extract exact source quotations, assess HIGH/MEDIUM/LOW risk,
optionally compare semantic guidance, and save structured findings plus Markdown.
Python validates evidence offsets before a finding enters a report or memory.
The tool distinguishes missing clauses from failed stages.

| Memory | ContractGuard stores |
|---|---|
| Semantic: what is known | ten seeded clause definitions and guidance; normal reviews may add validated lexical cues |
| Episodic: what happened | completed positive findings with source evidence, recommendations and review provenance |
| Procedural: how to act | ten bundled clause-review skills, loaded independently of database facts |

The retrieval gate selects semantic and episodic context independently.
History excludes the current document before taking its top three matches.
Gating aims to reduce irrelevant context, but gate calls add overhead;
token savings and latency benefits require live measurements.

**CUAD and memory ablation.** The evaluator scores eight mapped CUAD categories
with fixed character IoU >= 0.5 and one-to-one span matching. Indemnification and
Confidentiality Obligations remain unscored because CUAD lacks those categories.
Baseline, semantic-only and full-memory use isolated homes, identical procedures
and the same contract order. Gold annotations stay on the scoring side;
benchmark predictions never become semantic facts. CUAD provides no risk labels.

**Measured results, offline only (2026-10-06).** All three arms completed 102
official test contracts with no stage failures. Full-memory retained ten fixed
facts and 166 prediction episodes. The five-contract authored demo retained
15 episodes and retrieved 69 memory items. These scripted runs verify engineering
behavior; they do not measure LLM extraction quality or demonstrate a memory gain.
The [results record](docs/contractguard/results.md) preserves scores, overhead,
artifact provenance and reproduction commands.

From an installed checkout, run the demo without an API key:

```bash
python scripts/demo_review.py --contracts 5 --show-memory-growth --seed 42
```

Run the printed `WAKU_HOME=... python -m waku dashboard` command in another
terminal, then open `http://localhost:7777/#reviews`. The Reviews page reads saved
progress, risk counts, memory snapshots, metrics and reports without model calls.
[Demo instructions](docs/contractguard/demo.md) cover the live option and artifacts.

**Limits.** Interactive input accepts plain text up to 200,000 characters;
PDF ingestion is absent. Benchmark stages preserve full text, subject to provider
context limits. Evidence checks establish quotation validity, not legal correctness.
Atomic files and SQLite writes do not form one transaction. Live CUAD quality,
general risk accuracy and memory benefit require additional experiments.
[Interview notes and verified resume bullets](docs/contractguard/interview-notes.md)
explain design choices, regressions and the claims current evidence supports.

## Docs

| Read | For |
|---|---|
| [Getting started](docs/getting-started.md) | installing, the first run, connecting Waku Memory |
| [The tour](docs/tour.md) | the dashboard, things to try, the loop, graph workflows, skills |
| [Architecture](docs/architecture.md) | every box on the whiteboard, and the file behind it |
| [Integrations](docs/integrations.md) | voice, Telegram, calendars, MCP servers, Waku Memory |
| [Commands](docs/commands.md) | every `waku` and `make` command |
| [Evals & tracing](docs/evals.md) | the two kinds of eval, the Docker tier, the release gate, traces and spend |
| [Roadmap](docs/roadmap.md) | what is live, what is still a skeleton, upgrade paths |
| [Whiteboards](docs/README.md#whiteboards) | the editable system-design charts from the videos |
| [lab/](lab/README.md) | Waku meets other agents and models: the video experiments |
| [AGENTS.md](AGENTS.md) · [CONTRIBUTING.md](CONTRIBUTING.md) | the rules, and how to send a PR |

## Community

Star the repo, join the [Discord](https://discord.gg/ebbdvSCXqu), and grab a
[good first issue](https://github.com/ShenSeanChen/waku-agent/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22)
— that link is the live list, so it's always current. Gateways, memory backends and
community skills are all shaped to be first PRs; the easiest needs no Python at all
(see [contributing a skill](CONTRIBUTING.md)).

**Comment on an issue before you start** and it gets assigned to you, so two people
never build the same thing.

## Also from me

- **[launch-mvp-stripe-nextjs-supabase](https://github.com/ShenSeanChen/launch-mvp-stripe-nextjs-supabase)** — NextJS + Supabase + Stripe, everything you need to ship a SaaS.
- **[AutoManus.io](https://automanus.io)** — my AI startup: a sales lead manager for made-to-order products. It embeds where conversations already happen (WhatsApp, email, web chat) to capture inbound, automate follow-ups and kill CRM busywork. Pre-seed backed by Character VC. ([AutoManus Discord](https://discord.gg/SxXATg9rSK))

Code is MIT — see [LICENSE](LICENSE) — **except `hosted/`, which is Elastic
License 2.0**: run it yourself, including commercially, but not as a service for
others. See [hosted/LICENSE](hosted/LICENSE). `waku/`, the harness itself and
everything PyPI ships, is MIT. The Waku name, mark and design system belong to
AutoManus Technologies, Inc. and are not MIT — see [LICENSE-BRAND](LICENSE-BRAND). Built by [@ShenSeanChen](https://github.com/ShenSeanChen)
([YouTube](https://www.youtube.com/@SeanAIStories) · [X](https://x.com/ShenSeanChen)).
