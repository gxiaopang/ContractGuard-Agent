# ContractGuard Agent

ContractGuard Agent extends [Waku](https://github.com/ShenSeanChen/waku-agent)
with contract clause extraction, risk assessment and reusable review memory.
It turns plain contract text into findings backed by source quotations and
saves structured results alongside a Markdown report.

## What we added

- **Contract review workflow.** The agent parses contracts, retrieves clause
  context, extracts evidence, assesses HIGH/MEDIUM/LOW risk and compares findings
  with available guidance through one `review_contract` tool.
- **Evidence validation.** Python verifies exact quotations and source offsets
  before findings enter reports or memory. Reports distinguish missing clauses
  from failed review stages.
- **Domain memory and skills.** Ten clause-review skills work with semantic
  definitions and guidance, plus episodic memory of completed findings.
  The retrieval gate selects relevant context and excludes the current document
  from review history.
- **Reproducible evaluation.** The CUAD runner compares baseline, semantic-only
  and full-memory configurations in isolated homes. It reports precision,
  recall and F1 with fixed span matching and keeps gold annotations out of
  model inputs and memory.
- **Review dashboard.** The Reviews page displays saved reports, risk counts,
  review progress, memory snapshots and extraction metrics.

These additions reuse Waku's agent loop, provider adapters, SQLite memory and
local dashboard. Contract review is enabled with `WAKU_CONTRACT_REVIEW=1`.

## Quickstart

Install this checkout to get the ContractGuard additions:

```bash
git clone https://github.com/gxiaopang/ContractGuard-Agent.git
cd ContractGuard-Agent
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
cp .env.example .env
```

Run the offline demo with five sample contracts:

```bash
python scripts/demo_review.py --contracts 5 --show-memory-growth --seed 42
```

The demo uses scripted responses and needs no API key. Run its printed
`WAKU_HOME=... python -m waku dashboard` command in another terminal, then open
`http://localhost:7777/#reviews` to inspect reports and memory growth.

For interactive review, configure your provider and API key in `.env`, then run:

```bash
export WAKU_CONTRACT_REVIEW=1
python -m waku
# Or use the dashboard:
python -m waku dashboard
```

Ask the agent to review contract text for selected clauses. The review tool
accepts plain text up to 200,000 characters.

## Documentation

- [Architecture](docs/contractguard/architecture.md) maps the additions to code.
- [Review toolchain](docs/contractguard/toolchain.md) explains stages and storage.
- [Evaluation](docs/contractguard/evaluation.md) documents CUAD and memory ablations.
- [Demo](docs/contractguard/demo.md) explains dashboard usage and saved artifacts.
- [Results](docs/contractguard/results.md) records offline runs and reproduction commands.

## Attribution and license

ContractGuard builds on Waku by [ShenSeanChen](https://github.com/ShenSeanChen).
Code uses the [MIT license](LICENSE), except `hosted/`, which uses
[Elastic License 2.0](hosted/LICENSE). The Waku name, mark and design system
remain covered by [LICENSE-BRAND](LICENSE-BRAND).
