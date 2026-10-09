"""Review a seeded sample and save reports for the existing dashboard."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from evals.contractguard.dataset import load_dataset, target_names  # noqa: E402
from evals.contractguard.demo import run_demo  # noqa: E402
from evals.contractguard.predictor import MODES  # noqa: E402
from evals.contractguard.smoke import SmokeClient  # noqa: E402
from waku.config import Settings  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--contracts', type=int, default=5)
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--show-memory-growth', action='store_true')
    parser.add_argument('--dataset', type=Path, help='local CUAD-style annotated JSON')
    parser.add_argument('--split', choices=('test', 'train', 'custom'), default='test')
    parser.add_argument('--clause-types', nargs='+', default=['10'])
    parser.add_argument('--mode', choices=MODES, default='full-memory')
    parser.add_argument('--output', type=Path, default=REPO / 'artifacts/demo')
    parser.add_argument('--run-id')
    execution = parser.add_mutually_exclusive_group()
    execution.add_argument('--smoke', action='store_true', help='use the offline stand-in (default)')
    execution.add_argument('--live', action='store_true', help='use the configured provider and spend model calls')
    if (args := parser.parse_args(argv)).live and args.dataset is None:
        parser.error('--live requires an explicit --dataset')
    settings = Settings()
    if not args.live:
        settings.provider, settings.model, settings.small_model = 'scripted', 'source-rules-v1', 'source-rules-v1'
    try:
        targets = target_names(None if args.clause_types == ['10'] else args.clause_types)
        dataset = load_dataset(args.dataset or REPO / 'evals/contractguard/fixtures/demo.json', targets,
                               split=args.split if args.dataset else 'custom')
        if dataset.metadata['unscored_targets']:
            print('Unscored targets: ' + ', '.join(dataset.metadata['unscored_targets']))
        result = run_demo(dataset, settings, args.output, contracts=args.contracts, seed=args.seed,
                          mode=args.mode, run_id=args.run_id, show_memory_growth=args.show_memory_growth,
                          execution_kind='live' if args.live else 'offline_smoke',
                          client_factory=None if args.live else lambda s, m: SmokeClient(),
                          emit=lambda line: print(line, flush=True))
        print('Artifacts: ' + result['directory'])
        print('Status: ' + result['config']['status'])
        return 0 if result['config']['status'] == 'completed' else 1
    except (OSError, ValueError) as exc:
        parser.exit(2, f'Demo could not complete ({type(exc).__name__}): {exc}\n')


if __name__ == '__main__':
    raise SystemExit(main())
