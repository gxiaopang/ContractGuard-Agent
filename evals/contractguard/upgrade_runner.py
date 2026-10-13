"""Generated or local cases, isolated frozen/feedback arms and honest quality artifacts."""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import replace
from pathlib import Path

from evals.contractguard.cases import TEMPLATES
from evals.contractguard.predictor import Predictor
from evals.contractguard.quality import (
    case_metrics,
    paired_comparison,
    quality_verdict,
    rate,
    retrieval_audit,
    summarize,
)
from evals.contractguard.runner import manifest, new_run, save
from evals.contractguard.smoke import SmokeClient
from waku.loop.models import get_client, models_for
from waku.memory.contractguard import CLAUSES
from waku.memory.contractguard_store import identity
from waku.ops.contractguard_judge import RUBRIC_VERSION, judge_review
from waku.ops.tracing import metered
from waku.tools.contract_review import review_contract
from waku.tools.human_review import digest, submit_review

ARMS = ('baseline', 'semantic-only', 'approved-history', 'correction-history')


def decision_payload(result, decision='confirm'):
    saved = {k: v for k, v in result.items() if k != 'cached'}
    return {'review_id': result['review_id'], 'revision': digest(saved), 'decision': decision,
            'reviewer': 'scripted-evaluation-feedback', 'note': 'Synthetic feedback; no human quality claim.'}


def seed_history(predictor):
    """Exercise the real approval boundary using labelled synthetic fixture decisions."""
    memory = predictor.memory
    real_client = memory.client
    memory.client = SmokeClient()
    try:
        for index, clause in enumerate(CLAUSES):
            text = f'Historical fixture {index}.\n' + TEMPLATES[clause.name]
            result = review_contract(memory, text, 'Synthetic history', [clause.name], False,
                                     review_profile={'role': 'supplier', 'policy_version': 'generated-policy-v1',
                                                     'review_date': '2026-10-13'})
            payload = decision_payload(result, 'correct')
            finding = result['findings'][0]
            payload['findings'] = [{'clause_type': clause.name, 'found': True,
                                    'evidence': [{'text': s['text'], 'occurrence': 0} for s in finding['extraction']['spans']],
                                    'risk_level': 'MEDIUM', 'reasoning': 'Synthetic correction exercises scoped history.',
                                    'recommendation': 'Check the current source before reusing this example.'}]
            submit_review(memory.settings, payload, memory=memory)
    finally:
        memory.client = real_client


def feedback_after_score(predictor, source, prediction):
    """Persist source-only scripted corrections after scoring; scoring references stay separate."""
    if any(f['status'] == 'error' for f in prediction['findings']):
        return {'status': 'skipped', 'reason': 'partial_prediction'}
    memory, previous = predictor.memory, predictor.memory.client
    memory.client = SmokeClient()
    try:
        # Regeneration is a separate scripted fixture, not a second live review.
        result = review_contract(memory, source.text, 'Scripted feedback',
                                 [f['clause_type'] for f in prediction['findings']], False,
                                 review_profile=source.context)
        if result['status'] == 'partial':
            return {'status': 'skipped', 'reason': 'scripted_feedback_failure'}
        decision = submit_review(memory.settings, decision_payload(result), memory=memory)
        return {'status': 'stored', 'origin': 'scripted-source-only-feedback',
                'decision_id': decision['decision_id'], 'reference_access': False}
    finally:
        memory.client = previous


def contamination(cases):
    """Flag family overlap and near-duplicate source bodies without external services."""
    import re

    flagged = []
    for i, first in enumerate(cases):
        a = set(re.findall(r'\w+', first.text.casefold()))
        for second in cases[i + 1:]:
            b = set(re.findall(r'\w+', second.text.casefold()))
            similarity = len(a & b) / len(a | b) if a | b else 1
            if first.family_id == second.family_id or similarity >= .85:
                flagged.append({'first': first.case_id, 'second': second.case_id,
                                'reason': 'same_family' if first.family_id == second.family_id else 'near_duplicate',
                                'similarity': similarity})
    return {'version': 'word-set-jaccard-v1', 'threshold': .85, 'flagged': flagged,
            'held_out_claim': False}


def run_upgrade(cases, metadata, settings, root: Path, *, run_id=None, offline=True,
                arms=ARMS, experiment='frozen', repetitions=1, seed=42, judge=False,
                profile=None, client_factory=None):
    if not cases or len({c.case_id for c in cases}) != len(cases) or len({c.review_input().contract_id for c in cases}) != len(cases):
        raise ValueError('upgrade evaluation requires unique cases and source texts')
    if type(repetitions) is not int or not 1 <= repetitions <= 30 or type(seed) is not int:
        raise ValueError('repetitions must be 1 to 30 with an integer order seed')
    if experiment not in ('frozen', 'sequential') or not arms or len(set(arms)) != len(arms) or set(arms) - set(ARMS):
        raise ValueError('invalid experiment or arms')
    if offline and judge:
        raise ValueError('model judges require a live run')
    settings = replace(settings, contract_memory_upgrade=True, semantic_store='sqlite', episodic_store='sqlite')
    if offline:
        settings = replace(settings, provider='scripted', model='source-rules-v1', small_model='source-rules-v1')
    else:
        settings.model, settings.small_model = models_for(settings.provider, settings.model, settings.small_model)
    kind = 'offline_smoke' if offline else 'live'
    quality_verdict({}, profile, execution_kind=kind)
    config, prompts = manifest(settings, kind=kind, dataset=metadata,
                               order=[c.case_id for c in cases], modes=list(arms), seed=seed)
    config.update(schema='contractguard_upgrade_run_v1', experiment=experiment, repetitions=repetitions,
                  feedback_origin='scripted-source-only-feedback' if experiment == 'sequential' else None,
                  history_origin='scripted-fixture-decisions', held_out_claim=False,
                  quality_profile=profile, profile_sha256=identity(profile),
                  matcher={'rule': 'character-iou', 'threshold': .5}, judge_version=RUBRIC_VERSION if judge else None)
    config['policies'] = {arm: {'semantic': arm != 'baseline', 'episodic': arm in ('approved-history', 'correction-history'),
                               'corrections': arm == 'correction-history', 'episodic_writes': experiment == 'sequential'
                               and arm in ('approved-history', 'correction-history'),
                               'semantic_writes': False, 'procedures': 'bundled-fixed-all-arms',
                               'context_character_budget': 12000, 'cache': False} for arm in arms}
    from waku.ops.contractguard_judge import JUDGE_PROMPT

    prompts['contract_judge'] = JUDGE_PROMPT if judge else None
    config['prompt_sha256'] = identity(prompts)
    extra_files = ['waku/memory/contractguard_context.py', 'waku/memory/contractguard_records.py',
                   'waku/memory/contractguard_store.py', 'waku/tools/human_review.py',
                   'waku/tools/contract_review.py', 'waku/ops/contractguard_judge.py']
    for name in extra_files:
        config['code_hashes'][name] = hashlib.sha256((Path(__file__).resolve().parents[2] / name).read_bytes()).hexdigest()
    directory = new_run(root, run_id)
    save(directory / 'config.json', config)
    save(directory / 'prompts.json', prompts)
    save(directory / 'contamination.json', contamination(cases))
    rows, all_details, stop = [], {}, False
    try:
        for repetition in range(repetitions):
            ordered = list(cases)
            random.Random(seed + repetition).shuffle(ordered)
            for arm in arms:
                folder = directory / f'{arm}-{repetition}'
                folder.mkdir()
                arm_settings = replace(settings, home=folder / 'memory')
                client = client_factory(arm_settings, arm) if client_factory else SmokeClient() if offline else get_client(arm_settings)
                mode = arm if arm in ('baseline', 'semantic-only') else 'full-memory'
                predictor = Predictor(arm_settings, client, mode, accumulate=False,
                                      corrections=arm == 'correction-history')
                details, predictions, feedbacks, judges = [], [], [], []
                try:
                    if arm in ('approved-history', 'correction-history'):
                        seed_history(predictor)
                    before = predictor.memory.domain.list() if predictor.memory.domain else []
                    save(folder / 'memory-before.json', before)
                    with (folder / 'predictions.jsonl').open('x', encoding='utf-8') as output:
                        for case in ordered:
                            source = case.review_input()
                            prediction = predictor.predict(source, case.targets)
                            predictions.append(prediction)
                            details.extend(case_metrics(case, prediction))
                            output.write(json.dumps(prediction, ensure_ascii=False, allow_nan=False) + '\n')
                            output.flush()
                            if judge:
                                try:
                                    assessment = judge_review(metered(predictor.client, 'contract_judge', predictor.telemetry.event),
                                                              settings.small_model, source.text, source.context, prediction['findings'])
                                except Exception as exc:
                                    assessment = {'error': f'Judge failed ({type(exc).__name__}).', 'calibration': 'not_calibrated'}
                                judges.append({'case_id': case.case_id, 'assessment': assessment})
                            if predictor.telemetry.provider_status_code is not None:
                                stop = True
                                break
                            if experiment == 'sequential' and arm in ('approved-history', 'correction-history'):
                                feedbacks.append({'case_id': case.case_id, 'timing': 'after_scoring',
                                                  **feedback_after_score(predictor, source, prediction)})
                    metrics = summarize(details)
                    metrics['judge_opinions'] = {criterion: {
                        'mean': sum(values) / len(values) if values else None,
                        'support': len(values), 'calibration': 'not_calibrated'}
                        for criterion in ('evidence_support', 'exceptions', 'perspective', 'recommendations', 'memory_use', 'uncertainty')
                        for values in [[j['assessment']['criteria'][criterion]['score'] for j in judges if 'criteria' in j['assessment']]]}
                    telemetry = predictor.telemetry.snapshot()
                    latencies = sorted(p['diagnostics']['latency_seconds'] for p in predictions)
                    telemetry['mean_latency_seconds'] = sum(latencies) / len(latencies)
                    metrics['mean_latency_seconds'] = {'value': telemetry['mean_latency_seconds'], 'support': len(latencies)}
                    metrics['p95_latency_seconds'] = {'value': latencies[min(len(latencies) - 1, int(len(latencies) * .95))], 'support': len(latencies)}
                    metrics['estimated_cost_usd'] = {'value': telemetry['estimated_cost_usd'], 'support': len(predictions)}
                    metrics['judge_failure_rate'] = rate(sum('error' in j['assessment'] for j in judges), len(judges))
                    after = predictor.memory.domain.list() if predictor.memory.domain else []
                    audit = retrieval_audit(predictor.retrieval_events, after, [c.review_input() for c in ordered],
                                            predictor.memory.domain.scope if predictor.memory.domain else '')
                    metrics['retrieval_applicability_violation_rate'] = audit['applicability_violation_rate']
                    metrics['retrieval_context_budget_violation_rate'] = audit['budget_violation_rate']
                    row = {'arm': arm, 'repetition': repetition, 'metrics': metrics, 'telemetry': telemetry,
                           'status': 'provider_unavailable' if stop else 'partial' if any(r['failed'] for r in details)
                           or telemetry['gate_failures'] or telemetry['failed_model_calls']
                           or any('error' in j['assessment'] for j in judges) else 'completed',
                           'memory_snapshot_before': identity(before), 'memory_snapshot_after': identity(after),
                           'case_order': [c.case_id for c in ordered], 'processed_cases': len(predictions),
                           'quality': quality_verdict(metrics, profile, execution_kind=kind, calibrated=False,
                                                      complete=len(predictions) == len(cases))}
                    if experiment == 'frozen' and before != after:
                        raise ValueError('frozen experiment changed its memory')
                    save(folder / 'metrics.json', row)
                    save(folder / 'matching.json', details)
                    save(folder / 'retrieval.json', predictor.retrieval_events)
                    save(folder / 'retrieval-audit.json', audit)
                    save(folder / 'feedback.json', feedbacks)
                    save(folder / 'judges.json', {'rubric_version': RUBRIC_VERSION, 'calibration': 'not_calibrated', 'cases': judges})
                    save(folder / 'memory-after.json', after)
                    summary_dir = predictor.settings.home / 'contractguard'
                    summary_dir.mkdir(exist_ok=True)
                    save(summary_dir / 'quality.json', {'schema': 'contractguard_quality_report_v1',
                         'execution_kind': kind, 'engineering': 'not_run', 'execution_status': row['status'],
                         'quality': row['quality'], 'metrics': metrics, 'history_origin': config['history_origin'],
                         'profile': profile, 'calibration': 'not_calibrated',
                         'config_sha256': identity(config), 'profile_sha256': identity(profile),
                         'human_annotation_required': False})
                    rows.append(row)
                    all_details[(arm, repetition)] = details
                finally:
                    predictor.close()
                if stop:
                    break
            if stop:
                break
        comparisons = []
        for repetition in range(repetitions):
            for arm in arms:
                base, other = all_details.get(('baseline', repetition)), all_details.get((arm, repetition))
                if arm != 'baseline' and base is not None and other is not None and len(base) == len(other) and not stop:
                    comparisons.append({'arm': arm, 'repetition': repetition,
                                        **paired_comparison(base, other, seed=seed, sequential=experiment == 'sequential')})
        config['status'] = 'provider_unavailable' if stop else 'completed' if all(r['status'] == 'completed' for r in rows) else 'partial'
        variability = {}
        for arm in arms:
            f1s = [r['metrics']['extraction']['f1'] for r in rows if r['arm'] == arm and r['metrics']['extraction']['f1'] is not None]
            variability[arm] = {'repetitions': len(f1s), 'f1_mean': sum(f1s) / len(f1s) if f1s else None,
                                'f1_range': [min(f1s), max(f1s)] if f1s else None}
        sequence_intervals = {}
        if experiment == 'sequential':
            for arm in arms:
                values = [p['value'] for p in comparisons if p['arm'] == arm]
                randomizer = random.Random(seed)
                samples = sorted(sum(randomizer.choice(values) for _ in values) / len(values)
                                 for _ in range(500)) if len(values) > 1 else []
                sequence_intervals[arm] = {'independent_sequences': len(values),
                                          'interval_95': [samples[12], samples[487]] if samples else None}
        for row in rows:
            paired = next((p for p in comparisons if p['arm'] == row['arm'] and p['repetition'] == row['repetition']), None)
            row['metrics']['regression_rate'] = rate(paired['regressions'], len(all_details[(row['arm'], row['repetition'])])) if paired else rate(0, 0)
            row['quality'] = quality_verdict(row['metrics'], profile, execution_kind=kind, calibrated=False,
                                             complete=row['status'] == 'completed')
            folder = directory / f"{row['arm']}-{row['repetition']}"
            save(folder / 'metrics.json', row)
            quality_path = folder / 'memory/contractguard/quality.json'
            quality = json.loads(quality_path.read_text())
            quality['metrics'] = row['metrics']
            quality['quality'] = row['quality']
            save(quality_path, quality)
        report = {'schema': 'contractguard_upgrade_comparison_v1', 'execution_kind': kind,
                  'status': config['status'], 'quality': 'not_evaluated', 'arms': rows, 'paired': comparisons,
                  'variability': variability, 'sequence_intervals': sequence_intervals,
                  'human_annotation_required': False, 'legal_quality_verified': False}
        save(directory / 'comparison.json', report)
        lines = ['# ContractGuard upgrade evaluation', '', f'Execution kind: {kind}.',
                 'Quality: not evaluated. Human annotation is not required.',
                 'Synthetic history and scripted feedback do not establish legal quality.', '',
                 '| Arm | Repeat | Scored pairs | F1 | Critical miss rate | Failures | Calls |',
                 '|---|---:|---:|---:|---:|---:|---:|']
        for row in rows:
            m = row['metrics']
            lines.append(f"| {row['arm']} | {row['repetition']} | {m['extraction']['support']} | {m['extraction']['f1']} | "
                         f"{m['critical_miss_rate']['value']} | {m['failure_rate']['numerator']} | {row['telemetry']['llm_calls']} |")
        (directory / 'comparison.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    except BaseException:
        config['status'] = 'failed'
        save(directory / 'config.json', config)
        raise
    save(directory / 'config.json', config)
    for row in rows:
        quality_path = directory / f"{row['arm']}-{row['repetition']}" / 'memory/contractguard/quality.json'
        quality = json.loads(quality_path.read_text())
        quality['config_sha256'] = identity(config)
        quality['run_directory'] = str(directory)
        save(quality_path, quality)
    return {'directory': str(directory), 'config': config, 'arms': rows}
