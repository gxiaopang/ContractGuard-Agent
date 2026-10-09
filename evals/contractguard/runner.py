"""Isolated benchmark arms, reproducible manifests and machine-readable results."""

from __future__ import annotations

import hashlib
import json
import platform
import random
import re
import subprocess
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from evals.contractguard.matching import policy
from evals.contractguard.metrics import extraction_metrics
from evals.contractguard.predictor import MODES, PREDICTOR_VERSION, Predictor, memory_policy
from evals.contractguard.risk import RISK_VERSION, evaluate_risk, load_cases
from waku.loop.models import get_client, models_for
from waku.memory import bundled_skill_dirs, retrieval_gate
from waku.memory.contractguard import CLAUSES
from waku.memory.procedural.loader import SkillLoader
from waku.tools.clause_extractor import EXTRACTION_PROMPT
from waku.tools.contract_review import write_artifact
from waku.tools.risk_scorer import RISK_PROMPT

REPO = Path(__file__).resolve().parents[2]


def save(path: Path, value) -> None:
    write_artifact(path, json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, indent=2) + '\n')


def manifest(settings, *, kind: str, dataset: dict, order: list[str], modes: list[str], seed=None) -> tuple[dict, dict]:
    def git(*args):
        result = subprocess.run(['git', *args], cwd=REPO, capture_output=True, text=True, check=False)
        return result.stdout.strip() if result.returncode == 0 else None
    files = [*sorted((REPO / 'evals/contractguard').glob('*.py')),
             *(REPO / 'waku/tools' / name for name in ('clause_extractor.py', 'risk_scorer.py',
                                                     'contract_parser.py', 'contract_schema.py')),
             REPO / 'waku/memory/contractguard.py', REPO / 'waku/memory/retrieval_gate.py',
             REPO / 'waku/memory/__init__.py', REPO / 'waku/loop/models.py']
    file_hashes = {str(p.relative_to(REPO)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    bodies = {s.name: s.body for s in SkillLoader(bundled_skill_dirs()).skills}
    prompts = {'extraction': EXTRACTION_PROMPT, 'risk': RISK_PROMPT, 'gate': retrieval_gate.REVIEW_GATE_PROMPT,
               'procedures': {c.name: bodies[c.skill] for c in CLAUSES}}
    prompt_hash = hashlib.sha256(json.dumps(prompts, sort_keys=True).encode()).hexdigest()
    dirty = git('status', '--porcelain')
    config = {'schema': 'contractguard_run_v1', 'timestamp': datetime.now(UTC).isoformat(),
              'status': 'running', 'execution_kind': kind, 'git_commit': git('rev-parse', 'HEAD'),
              'git_dirty': bool(dirty) if dirty is not None else None, 'code_hashes': file_hashes,
              'python': platform.python_version(), 'prompt_version': PREDICTOR_VERSION,
              'prompt_sha256': prompt_hash, 'provider': settings.provider, 'model': settings.model,
              'small_model': settings.small_model, 'temperature': None,
              'decoding_policy': 'provider-default-not-overridden',
              'max_output_tokens': {'extraction': 4096, 'risk': 4096, 'gate': 600},
              'custom_endpoint': bool(settings.base_url), 'dataset': dataset,
              'retrieval_top_k': settings.retrieval_top_k, 'episodic_history_limit': 3,
              'number_of_contracts': len(order), 'contract_order': order, 'shuffle_seed': seed,
              'modes': modes, 'policies': {mode: memory_policy(mode) for mode in modes if mode in MODES}}
    return config, prompts


def new_run(root: Path, run_id: str | None = None) -> Path:
    identity = (datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ_') + uuid.uuid4().hex[:8]
                if run_id is None else run_id)
    if not isinstance(identity, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', identity):
        raise ValueError('run id must contain one to eighty letters, digits, underscores or hyphens')
    directory = Path(root).resolve() / identity
    directory.mkdir(parents=True, exist_ok=False)
    return directory


def comparison_table(rows: list[dict], kind: str) -> str:
    lines = ['# ContractGuard Extraction Comparison', '', f'Execution kind: {kind}.', '',
             '| Mode | Contracts | Micro P | Micro R | Micro F1 | Macro F1 | Extraction failures | Critical miss rate | Calls | Retrieved items | Mean seconds |',
             '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for row in rows:
        metrics, telemetry = row['metrics'], row['telemetry']
        micro = metrics['micro']
        lines.append(f"| {row['mode']} | {metrics['contracts']} | {micro['precision']:.4f} | "
                     f"{micro['recall']:.4f} | {micro['f1']:.4f} | {metrics['macro']['f1']:.4f} | "
                     f"{metrics['extraction_failed_pairs']} | {metrics['critical']['miss_rate']:.4f} | "
                     f"{telemetry['llm_calls']} | {telemetry['memory_items_retrieved']} | "
                     f"{telemetry['mean_latency_seconds']:.4f} |")
    if any(row['status'] == 'provider_unavailable' for row in rows):
        lines.extend(['', 'HTTP 402 interrupted this run. Scores cover only processed contracts;',
                      'these artifacts do not establish a complete ablation comparison.'])
    lines.extend(['', 'Risk accuracy is not scored by CUAD.',
                  'Offline smoke scores describe a scripted stand-in.', ''])
    return '\n'.join(lines)


def run_benchmark(dataset, settings, root: Path, *, modes=MODES, rule='character-iou',
                  seed: int | None = None, limit: int | None = None, run_id=None,
                  client_factory=None, execution_kind='live', workers: int = 1) -> dict:
    if type(workers) is not int or not 1 <= workers <= 32:
        raise ValueError('clause workers must be an integer from 1 to 32')
    modes = list(modes)
    if not modes or len(set(modes)) != len(modes) or any(mode not in MODES for mode in modes):
        raise ValueError('select unique valid memory modes')
    if execution_kind not in ('live', 'offline_smoke', 'offline_test'):
        raise ValueError('invalid execution kind')
    if execution_kind != 'live' and client_factory is None:
        raise ValueError('offline runs require an explicit offline client factory')
    if type(limit) not in (int, type(None)) or (limit is not None and limit <= 0):
        raise ValueError('contract limit must be a positive integer')
    if seed is not None and type(seed) is not int:
        raise ValueError('shuffle seed must be an integer')
    matcher = policy(rule)
    contracts = list(dataset.contracts)
    if not contracts:
        raise ValueError('benchmark dataset is empty')
    if seed is not None:
        random.Random(seed).shuffle(contracts)
    if limit is not None:
        contracts = contracts[:limit]
    settings = replace(settings)
    settings.model, settings.small_model = models_for(settings.provider, settings.model, settings.small_model)
    config, prompts = manifest(settings, kind=execution_kind, dataset=dataset.metadata,
                               order=[c.contract_id for c in contracts], modes=modes, seed=seed)
    config['matcher'] = matcher
    config['contract_limit'] = limit
    config['evaluation_task'] = 'span-extraction'
    config['clause_workers'] = workers
    directory = new_run(root, run_id)
    save(directory / 'prompts.json', prompts)
    save(directory / 'config.json', config)
    rows = []
    try:
        for mode in modes:
            arm = directory / mode
            arm.mkdir()
            arm_settings = replace(settings, home=arm / 'memory')
            client = client_factory(arm_settings, mode) if client_factory else get_client(arm_settings)
            predictor = Predictor(arm_settings, client, mode, workers=workers)
            predictions = []
            try:
                with (arm / 'predictions.jsonl').open('x', encoding='utf-8') as output:
                    for contract in contracts:
                        # Projection is the only input to prediction: annotations stay here.
                        prediction = predictor.predict(contract.review_input(), dataset.targets)
                        predictions.append(prediction)
                        output.write(json.dumps(prediction, ensure_ascii=False, allow_nan=False) + '\n')
                        output.flush()
                        if predictor.telemetry.provider_status_code is not None:
                            break
                metrics = extraction_metrics(contracts[:len(predictions)], predictions, dataset.targets, rule=rule)
                telemetry = predictor.telemetry.snapshot()
                telemetry['mean_latency_seconds'] = sum(p['diagnostics']['latency_seconds'] for p in predictions) / len(predictions)
                status = ('partial' if metrics['extraction_failed_pairs'] or metrics['risk_failed_pairs']
                          or telemetry['gate_failures'] or telemetry['failed_model_calls'] else 'completed')
                if telemetry['provider_status_code'] is not None:
                    status = 'provider_unavailable'
                    config['provider_error'] = {'status_code': telemetry['provider_status_code'],
                                                'reason': 'Payment required; check provider account balance.'}
                row = {'mode': mode, 'status': status, 'memory_home': str(arm_settings.home),
                       'metrics': metrics, 'telemetry': telemetry,
                       'memory_counts': {'facts': len(predictor.memory.facts.list(limit=-1)),
                                         'episodes': len(predictor.memory.episodes.list(limit=-1))}}
                save(arm / 'metrics.json', row)
                rows.append(row)
            finally:
                predictor.close()
            if row['status'] == 'provider_unavailable':
                break
        config['status'] = ('provider_unavailable' if 'provider_error' in config else
                            'completed' if all(row['status'] == 'completed' for row in rows) else 'partial')
        config['processed_modes'] = [row['mode'] for row in rows]
        save(directory / 'comparison.json', {'schema': 'contractguard_comparison_v1',
                                              'execution_kind': execution_kind, 'matcher': matcher, 'arms': rows})
        write_artifact(directory / 'comparison.md', comparison_table(rows, execution_kind))
    except BaseException:
        config['status'] = 'failed'
        save(directory / 'config.json', config)
        raise
    save(directory / 'config.json', config)
    return {'directory': str(directory), 'config': config, 'arms': rows}


def run_risk(path: Path, settings, root: Path, *, run_id=None, client_factory=None,
             execution_kind='live') -> dict:
    if execution_kind not in ('live', 'offline_smoke', 'offline_test'):
        raise ValueError('invalid execution kind')
    if execution_kind != 'live' and client_factory is None:
        raise ValueError('offline runs require an explicit offline client factory')
    cases, checksum = load_cases(path)
    settings = replace(settings)
    settings.model, settings.small_model = models_for(settings.provider, settings.model, settings.small_model)
    config, prompts = manifest(settings, kind=execution_kind,
                               dataset={'kind': 'authored-risk-rubrics', 'sha256': checksum, 'path': str(path)},
                               order=[case['id'] for case in cases], modes=['risk-curated'])
    config['prompt_version'] = RISK_VERSION
    config['evaluation_task'] = 'curated-risk-label-agreement'
    config['target_clause_types'] = list(dict.fromkeys(case['clause_type'] for case in cases))
    directory = new_run(root, run_id)
    save(directory / 'prompts.json', prompts)
    save(directory / 'config.json', config)
    try:
        client = client_factory(settings, 'risk-curated') if client_factory else get_client(settings)
        predictions, metrics = evaluate_risk(cases, settings, client)
        save(directory / 'metrics.json', metrics)
        write_artifact(directory / 'predictions.jsonl', ''.join(json.dumps(p, ensure_ascii=False) + '\n' for p in predictions))
        config['status'] = 'partial' if metrics['failed_cases'] else 'completed'
    except BaseException:
        config['status'] = 'failed'
        save(directory / 'config.json', config)
        raise
    save(directory / 'config.json', config)
    return {'directory': str(directory), 'config': config, 'metrics': metrics}
