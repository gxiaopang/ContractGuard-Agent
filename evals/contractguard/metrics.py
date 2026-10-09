"""Deterministic extraction counts; errors never become successful negatives."""

from __future__ import annotations

from evals.contractguard.matching import match_spans, policy
from waku.tools.contract_schema import validate_findings

CRITICAL = ('Termination for Convenience', 'Uncapped Liability', 'Cap on Liability')


def scores(tp: int, fp: int, fn: int) -> dict:
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    return {'precision': precision, 'recall': recall,
            'f1': 2 * precision * recall / (precision + recall) if precision + recall else 0.0}


def extraction_metrics(contracts, predictions: list[dict], targets, *, rule='character-iou') -> dict:
    targets = list(targets)
    if not contracts or len(predictions) != len(contracts):
        raise ValueError('metrics require one prediction per contract in the same order')
    totals = {name: {'tp': 0, 'fp': 0, 'fn': 0, 'gold_spans': 0, 'predicted_spans': 0,
                     'positive_pairs': 0, 'negative_pairs': 0, 'failed_pairs': 0,
                     'critical_misses': 0} for name in targets}
    critical_positive = critical_misses = risk_failures = 0
    details = []
    for contract, prediction in zip(contracts, predictions, strict=True):
        if prediction['contract_id'] != contract.contract_id or set(contract.annotations) != set(targets):
            raise ValueError('prediction identity or annotation coverage differs from scoring contract')
        validate_findings(prediction['findings'], contract.text, targets)
        for item in prediction['findings']:
            name, extracted = item['clause_type'], item['extraction']
            failed = extracted is None
            predicted = extracted['spans'] if extracted is not None else []
            gold = contract.annotations[name]
            matched = match_spans(predicted, gold, contract.text, rule)
            row = totals[name]
            for key in ('tp', 'fp', 'fn'):
                row[key] += matched[key]
            row['gold_spans'] += matched['gold_count']
            row['predicted_spans'] += matched['predicted_count']
            row['positive_pairs'] += bool(gold)
            row['negative_pairs'] += not gold and not predicted and not failed
            row['failed_pairs'] += failed
            if item['error'] is not None and item['error']['stage'] == 'risk':
                risk_failures += 1
            if name in CRITICAL and gold:
                critical_positive += 1
                miss = matched['tp'] == 0
                critical_misses += miss
                row['critical_misses'] += miss
            details.append({'contract_id': contract.contract_id, 'clause_type': name,
                            'extraction_failed': failed, **matched})
    for row in totals.values():
        row.update(scores(row['tp'], row['fp'], row['fn']))
    pooled = {key: sum(row[key] for row in totals.values()) for key in ('tp', 'fp', 'fn')}
    macro = {key: sum(row[key] for row in totals.values()) / len(targets)
             for key in ('precision', 'recall', 'f1')}
    return {'schema': 'contractguard_metrics_v1', 'matcher': policy(rule), 'contracts': len(contracts),
            'per_clause': totals, 'micro': {**pooled, **scores(**pooled)}, 'macro': macro,
            'extraction_failed_pairs': sum(r['failed_pairs'] for r in totals.values()),
            'risk_failed_pairs': risk_failures, 'risk_accuracy': None,
            'critical': {'targets': [t for t in CRITICAL if t in targets], 'positive_pairs': critical_positive,
                         'missed_pairs': critical_misses,
                         'miss_rate': critical_misses / critical_positive if critical_positive else 0.0},
            'matching_details': details}
