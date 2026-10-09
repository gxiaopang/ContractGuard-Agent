"""Sequential review demonstration with isolated memory and persisted progress."""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import replace
from pathlib import Path

from evals.contractguard.matching import policy
from evals.contractguard.metrics import extraction_metrics
from evals.contractguard.predictor import MODES, Predictor
from evals.contractguard.runner import manifest, new_run, save
from waku.loop.models import get_client, models_for
from waku.ops.contractguard import MAX_REVIEWS
from waku.tools.contract_parser import parse_contract
from waku.tools.contract_review import (
    PROMPT_VERSION,
    RESULT_SCHEMA,
    encode,
    validate_result,
    write_artifact,
)
from waku.tools.report_generator import generate_report


def save_review(home: Path, contract, prediction: dict, settings, targets) -> str:
    # Dataset titles stay out of model inputs; they label saved reports only.
    name = contract.title[:200]
    document = parse_contract(contract.text, name)
    request = {'contract_name': name, 'clause_types': list(targets), 'compare_guidance': False,
               'origin': 'benchmark_prediction', 'provider': settings.provider, 'model': settings.model,
               'small_model': settings.small_model, 'prompt_version': PROMPT_VERSION}
    identity = {'document_id': contract.contract_id, **request}
    review_id = hashlib.sha256(encode(identity).encode()).hexdigest()
    folder = home / 'reviews' / review_id
    folder.mkdir(parents=True, exist_ok=False)
    artifacts = {key: str(folder / filename) for key, filename in
                 (('document', 'document.json'), ('result', 'result.json'), ('report', 'report.md'))}
    status = 'partial' if any(f['status'] == 'error' for f in prediction['findings']) else 'completed'
    report = generate_report(contract.text, name, prediction['findings'], list(targets), compare_guidance=False)
    result = {'schema': RESULT_SCHEMA, 'review_id': review_id, 'document_id': contract.contract_id,
              'request': request, 'status': status, 'timestamp': prediction['timestamp'],
              'findings': prediction['findings'], 'report': report, 'artifacts': artifacts}
    validate_result(result, document, request, review_id, artifacts)
    save(Path(artifacts['document']), document)
    write_artifact(Path(artifacts['report']), report)
    save(Path(artifacts['result']), result)
    return review_id


def run_demo(dataset, settings, root: Path, *, contracts=5, seed=42, mode='full-memory', run_id=None,
             client_factory=None, execution_kind='offline_smoke', show_memory_growth=False, emit=print) -> dict:
    if type(contracts) is not int or not 1 <= contracts <= min(len(dataset.contracts), MAX_REVIEWS):
        raise ValueError('contract count must fit the input dataset and the 200-review dashboard limit')
    if type(seed) is not int or mode not in MODES:
        raise ValueError('select an integer seed and a supported memory mode')
    if execution_kind not in ('offline_smoke', 'live') or (execution_kind != 'live' and client_factory is None):
        raise ValueError('offline demonstrations require an explicit offline client')
    selected = list(dataset.contracts)
    random.Random(seed).shuffle(selected)
    selected = selected[:contracts]
    settings = replace(settings)
    settings.model, settings.small_model = models_for(settings.provider, settings.model, settings.small_model)
    config, prompts = manifest(settings, kind=execution_kind, dataset=dataset.metadata,
                               order=[c.contract_id for c in selected], modes=[mode], seed=seed)
    config.update(evaluation_task='demo-span-extraction', matcher=policy(), contract_limit=contracts)
    directory = new_run(root, run_id)
    settings = replace(settings, home=directory / 'home')
    save(directory / 'config.json', config)
    save(directory / 'prompts.json', prompts)
    state = {'schema': 'contractguard_demo_v1', 'execution_kind': execution_kind, 'mode': mode,
             'status': 'running', 'contracts': [{'contract_id': c.contract_id, 'name': c.title[:200],
                                                'status': 'pending', 'review_id': None} for c in selected],
             'current_contract_id': None, 'memory_growth': [], 'telemetry': None}
    predictor = None
    predictions = []
    progress = settings.home / 'contractguard' / 'progress.json'
    try:
        client = client_factory(settings, mode) if client_factory else get_client(settings)
        predictor = Predictor(settings, client, mode)
        progress.parent.mkdir()

        def snapshot(completed):
            state['memory_growth'].append({
                'reviewed': completed, 'semantic_entries': len(predictor.memory.facts.list(limit=-1)),
                'episodic_entries': len(predictor.memory.episodes.list(limit=-1)),
                'procedural_skills': len(predictor.procedures),
                'retrieval_events': predictor.telemetry.counts['retrieval_count'],
                'retrieved_items': predictor.telemetry.counts['memory_items_retrieved']})
            state['telemetry'] = predictor.telemetry.snapshot()
            save(progress, state)

        snapshot(0)
        emit(f'Execution kind: {execution_kind}. Memory mode: {mode}.')
        emit('Scripted scores verify wiring; live quality is unmeasured.' if execution_kind == 'offline_smoke'
             else 'This live demonstration spends model calls; results describe this sample only.')
        emit(f"Dashboard: WAKU_HOME={settings.home} python -m waku dashboard; open #reviews.")
        with (directory / 'predictions.jsonl').open('x', encoding='utf-8') as output:
            for i, contract in enumerate(selected):
                row = state['contracts'][i]
                row['status'], state['current_contract_id'] = 'running', contract.contract_id
                save(progress, state)
                prediction = predictor.predict(contract.review_input(), dataset.targets)
                predictions.append(prediction)
                output.write(json.dumps(prediction, ensure_ascii=False, allow_nan=False) + '\n')
                output.flush()
                row['review_id'] = save_review(settings.home, contract, prediction, settings, dataset.targets)
                row['status'] = ('partial' if any(f['status'] == 'error' for f in prediction['findings'])
                                 else 'completed')
                state['current_contract_id'] = None
                metrics = extraction_metrics(selected[:i + 1], predictions, dataset.targets)
                metrics['execution_kind'], metrics['mode'] = execution_kind, mode
                metrics['telemetry'] = predictor.telemetry.snapshot()
                metrics['telemetry']['mean_latency_seconds'] = sum(
                    p['diagnostics']['latency_seconds'] for p in predictions) / len(predictions)
                save(progress.parent / 'metrics.json', metrics)
                save(directory / 'metrics.json', metrics)
                snapshot(i + 1)
                emit(f"[{i + 1}/{contracts}] {contract.title[:200]}: {row['status']}; "
                     f"retrieval events {prediction['diagnostics']['retrieval_count']}, "
                     f"retrieved items {prediction['diagnostics']['memory_items_retrieved']}.")
                if show_memory_growth:
                    growth = state['memory_growth'][-1]
                    emit(f"Memory: {growth['semantic_entries']} semantic, {growth['episodic_entries']} episodic, "
                         f"{growth['procedural_skills']} procedures.")
        counters = predictor.telemetry.counts
        state['status'] = ('partial' if any(r['status'] == 'partial' for r in state['contracts'])
                           or counters['gate_failures'] or counters['failed_model_calls'] else 'completed')
        save(progress, state)
        config['status'] = state['status']
        save(directory / 'config.json', config)
        micro = metrics['micro']
        emit(f"Extraction on {contracts} annotated contracts: P={micro['precision']:.4f}, "
             f"R={micro['recall']:.4f}, F1={micro['f1']:.4f}; macro F1={metrics['macro']['f1']:.4f}.")
        return {'directory': str(directory), 'home': str(settings.home), 'config': config,
                'progress': state, 'metrics': metrics}
    except BaseException:
        state['status'] = config['status'] = 'failed'
        state['current_contract_id'] = None
        for row in state['contracts']:
            if row['status'] == 'running':
                row['status'] = 'failed'
        if progress.parent.exists():
            save(progress, state)
        save(directory / 'config.json', config)
        raise
    finally:
        if predictor is not None:
            predictor.close()
