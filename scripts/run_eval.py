"""Run original benchmarks or generated dataset-independent upgrade evaluations."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from evals.contractguard.dataset import load_dataset, target_names  # noqa: E402
from evals.contractguard.matching import RULES  # noqa: E402
from evals.contractguard.predictor import MODES  # noqa: E402
from evals.contractguard.runner import run_benchmark, run_risk  # noqa: E402
from evals.contractguard.smoke import SmokeClient  # noqa: E402
from waku.config import Settings  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, help='local CUAD SQuAD JSON; never downloaded automatically')
    parser.add_argument('--split', choices=('test', 'train', 'custom'), default='test')
    parser.add_argument('--mode', choices=MODES, default='baseline')
    parser.add_argument('--ablation', action='store_true', help='run all three isolated memory arms')
    parser.add_argument('--clause-types', nargs='+', default=['10'], help='10 or canonical category names')
    parser.add_argument('--match-rule', choices=RULES, default='character-iou')
    parser.add_argument('--limit', type=int)
    parser.add_argument('--workers', type=int, default=1, help='concurrent clause stages per contract (1–32); contracts remain sequential')
    parser.add_argument('--seed', type=int, help='shuffle before limiting; identical order in every arm')
    parser.add_argument('--output', type=Path, default=REPO / 'artifacts/eval')
    parser.add_argument('--run-id')
    parser.add_argument('--provider')
    parser.add_argument('--model')
    parser.add_argument('--small-model')
    parser.add_argument('--smoke', action='store_true', help='use a labelled source-only scripted client offline')
    parser.add_argument('--risk', action='store_true', help='score authored risk rubrics separately from CUAD')
    parser.add_argument('--risk-cases', type=Path, default=REPO / 'evals/contractguard/fixtures/risk_cases.jsonl')
    parser.add_argument('--upgrade', action='store_true', help='evaluate the scoped-memory upgrade without manual annotation')
    parser.add_argument('--cases', type=Path, help='local versioned generic cases; upgrade defaults to generated cases')
    parser.add_argument('--experiment', choices=('frozen', 'sequential'), default='frozen')
    parser.add_argument('--repetitions', type=int, default=1)
    parser.add_argument('--live', action='store_true', help='use real model calls for the upgrade evaluation')
    parser.add_argument('--judge', action='store_true', help='add contract-specific uncalibrated model opinions to a live upgrade run')
    parser.add_argument('--quality-profile', type=Path, help='explicit versioned quality bounds; scores without calibration remain unverified')
    args = parser.parse_args(argv)
    if args.upgrade:
        if args.dataset or args.risk or args.ablation or args.workers != 1:
            parser.error('--upgrade uses generic cases and its own four memory arms')
        if args.mode != 'baseline' or args.match_rule != 'character-iou' or args.clause_types != ['10']:
            parser.error('--upgrade takes targets from each case and uses frozen character-IoU matching')
        if (args.provider or args.model or args.small_model or args.judge) and not args.live:
            parser.error('provider overrides and judges require --live in upgrade mode')
        if args.live and args.smoke:
            parser.error('--live and --smoke cannot be combined')
        from evals.contractguard.cases import generated_cases, load_cases
        from evals.contractguard.upgrade_runner import run_upgrade
        from waku.tools.contract_schema import load_json

        try:
            cases, metadata = load_cases(args.cases) if args.cases else generated_cases(args.seed if args.seed is not None else 42)
            if args.limit is not None:
                if args.limit <= 0:
                    raise ValueError('case limit must be positive')
                cases = cases[:args.limit]
                metadata = {**metadata, 'limit': args.limit}
            settings = Settings()
            if args.provider:
                settings.provider = args.provider
                settings.model, settings.small_model = args.model or '', args.small_model or ''
            else:
                settings.model, settings.small_model = args.model or settings.model, args.small_model or settings.small_model
            profile = load_json(args.quality_profile.read_text()) if args.quality_profile else None
            result = run_upgrade(cases, metadata, settings, args.output, run_id=args.run_id,
                                 offline=not args.live, experiment=args.experiment, repetitions=args.repetitions,
                                 seed=args.seed if args.seed is not None else 42, judge=args.judge, profile=profile)
            print('Execution kind: ' + result['config']['execution_kind'])
            print('Status: ' + result['config']['status'])
            print('Quality: not evaluated; no manual annotation required')
            print('Artifacts: ' + result['directory'])
            return 0 if result['config']['status'] == 'completed' else 1
        except (ValueError, OSError) as exc:
            parser.exit(2, f'Upgrade evaluation could not complete ({type(exc).__name__}): {exc}\n')
    if args.cases or args.live or args.judge or args.quality_profile or args.repetitions != 1 or args.experiment != 'frozen':
        parser.error('generic cases, repetitions, experiments and judges require --upgrade')
    if args.risk and (args.ablation or args.dataset):
        parser.error('--risk is a separate evaluation and cannot use --ablation or --dataset')
    if args.risk and args.workers != 1:
        parser.error('--workers applies only to extraction benchmarks')
    if not args.risk and not args.dataset and not args.smoke:
        parser.error('--dataset is required for a live extraction run')
    if args.smoke and (args.provider or args.model or args.small_model):
        parser.error('--smoke supplies its own scripted model configuration')
    settings = Settings()
    if args.smoke:
        settings.provider, settings.model, settings.small_model = 'scripted', 'source-rules-v1', 'source-rules-v1'
    else:
        if args.provider:
            settings.provider = args.provider
            settings.model, settings.small_model = args.model or '', args.small_model or ''
        else:
            settings.model = args.model or settings.model
            settings.small_model = args.small_model or settings.small_model
    factory = (lambda settings, mode: SmokeClient()) if args.smoke else None
    kind = 'offline_smoke' if args.smoke else 'live'
    try:
        if args.risk:
            result = run_risk(args.risk_cases, settings, args.output, run_id=args.run_id,
                              client_factory=factory, execution_kind=kind)
        else:
            requested = target_names(None if args.clause_types == ['10'] else args.clause_types)
            dataset = load_dataset(args.dataset or REPO / 'evals/contractguard/fixtures/smoke.json',
                                   requested, split=args.split if args.dataset else 'custom')
            result = run_benchmark(dataset, settings, args.output,
                                   modes=MODES if args.ablation else (args.mode,), rule=args.match_rule,
                                   seed=args.seed, limit=args.limit, run_id=args.run_id,
                                   client_factory=factory, execution_kind=kind, workers=args.workers)
            if dataset.metadata['unscored_targets']:
                print('Unscored CUAD targets: ' + ', '.join(dataset.metadata['unscored_targets']))
        print('Execution kind: ' + kind)
        print('Status: ' + result['config']['status'])
        print('Artifacts: ' + result['directory'])
        return 0 if result['config']['status'] == 'completed' else 1
    except (ValueError, OSError) as exc:
        parser.exit(2, f'Evaluation could not complete ({type(exc).__name__}): {exc}\n')


if __name__ == '__main__':
    raise SystemExit(main())
