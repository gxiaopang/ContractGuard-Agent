"""Explicit metric coverage and release verdicts independent of dataset adapters."""

from __future__ import annotations

import math
import random
from datetime import UTC, datetime

from evals.contractguard.matching import match_spans, policy
from evals.contractguard.metrics import scores


def rate(numerator, denominator):
    return {'value': numerator / denominator if denominator else None,
            'numerator': numerator, 'support': denominator}


def retrieval_audit(events, records, sources, scope):
    """Check recalled metadata independently of the production exclusion function."""
    indexed = {r['id']: r for r in records}
    inputs = {s.contract_id: s for s in sources}
    violations, selected = [], 0
    for event in events:
        source = inputs[event['contract_id']]
        context = source.context or {}
        day = context.get('review_date', datetime.now(UTC).date().isoformat())
        for record_id in event['selected_ids']:
            selected += 1
            row = indexed.get(record_id)
            reason = None
            if row is None or row['state'] != 'approved':
                reason = 'not_approved'
            elif row['applicability']['scope'] != scope:
                reason = 'scope_mismatch'
            elif row['clause_type'] != event['clause_type']:
                reason = 'clause_mismatch'
            elif row['source_id'] == source.contract_id:
                reason = 'current_source'
            elif row['kind'] in ('review', 'correction', 'experience') and (
                    row['applicability']['role'] is None or row['applicability']['policy_version'] is None):
                reason = 'unknown_required_context'
            else:
                constraints = row['applicability']
                for key in ('project', 'contract_type', 'represented_party', 'role', 'jurisdiction', 'policy_version'):
                    if constraints[key] is not None and constraints[key] != context.get(key):
                        reason = 'context_mismatch'
                        break
                if (constraints['valid_from'] and day < constraints['valid_from']) or (
                        constraints['valid_until'] and day > constraints['valid_until']):
                    reason = 'invalid_date'
            if reason:
                violations.append({'contract_id': source.contract_id, 'memory_id': record_id, 'reason': reason})
    return {'schema': 'contractguard_retrieval_audit_v1', 'violations': violations,
            'applicability_violation_rate': rate(len(violations), selected),
            'budget_violation_rate': rate(sum(e['characters'] > e['budget'] for e in events), len(events))}


def case_metrics(case, prediction, *, rule='character-iou'):
    from waku.tools.contract_schema import validate_findings

    if prediction['contract_id'] != case.review_input().contract_id:
        raise ValueError('prediction identity differs from scoring source')
    validate_findings(prediction['findings'], case.text, list(case.targets))
    rows = []
    for finding in prediction['findings']:
        target = finding['clause_type']
        reference = case.reference
        covered = target in reference['extraction']
        gold = reference['extraction'].get(target, [])
        extracted = finding['extraction']
        matched = match_spans(extracted['spans'] if extracted else [], gold, case.text, rule) if covered else None
        actual_risk = finding['risk']['risk_level'] if finding['risk'] else None
        expected_risk = reference['risk'].get(target)
        levels = {'LOW': 0, 'MEDIUM': 1, 'HIGH': 2}
        rows.append({'case_id': case.case_id, 'family_id': case.family_id, 'target': target,
                     'extraction_scored': covered, 'matching': matched,
                     'failed': finding['status'] == 'error',
                     'negative': covered and not gold,
                     'false_positive': covered and not gold and bool(extracted and extracted['found']),
                     'critical_positive': covered and bool(gold) and target in reference['critical_targets'],
                     'critical_miss': covered and bool(gold) and target in reference['critical_targets'] and matched['tp'] == 0,
                     'risk_scored': expected_risk is not None, 'expected_risk': expected_risk,
                     'actual_risk': actual_risk, 'risk_agreement': expected_risk is not None and actual_risk == expected_risk,
                     'serious_underestimate': expected_risk == 'HIGH' and (actual_risk is None or levels[actual_risk] < 2),
                     'severity_weight': reference['severity_weights'].get(target, 1)})
    return rows


def summarize(rows, *, rule='character-iou'):
    scored = [r for r in rows if r['extraction_scored']]
    pooled = {k: sum(r['matching'][k] for r in scored) for k in ('tp', 'fp', 'fn')}
    per_clause = {}
    for name in sorted({r['target'] for r in rows}):
        subset = [r for r in scored if r['target'] == name]
        totals = {k: sum(r['matching'][k] for r in subset) for k in ('tp', 'fp', 'fn')}
        per_clause[name] = {'support': len(subset), **totals,
                            **(scores(**totals) if subset else dict.fromkeys(('precision', 'recall', 'f1')))}
    covered = [v for v in per_clause.values() if v['support']]
    macro = {'support': len(covered), **{k: sum(v[k] for v in covered) / len(covered) if covered else None
                                       for k in ('precision', 'recall', 'f1')}}
    risks = [r for r in rows if r['risk_scored']]
    confusion = {level: dict.fromkeys(('HIGH', 'MEDIUM', 'LOW', 'ERROR'), 0) for level in ('HIGH', 'MEDIUM', 'LOW')}
    for r in risks:
        confusion[r['expected_risk']][r['actual_risk'] or 'ERROR'] += 1
    return {'schema': 'contractguard_quality_metrics_v1', 'matcher': policy(rule),
            'per_clause': per_clause, 'macro': macro,
            'extraction': {'support': len(scored), **pooled,
                           **(scores(**pooled) if scored else dict.fromkeys(('precision', 'recall', 'f1')))},
            'negative_false_positive_rate': rate(sum(r['false_positive'] for r in rows), sum(r['negative'] for r in rows)),
            'critical_miss_rate': rate(sum(r['critical_miss'] for r in rows), sum(r['critical_positive'] for r in rows)),
            'failure_rate': rate(sum(r['failed'] for r in rows), len(rows)),
            'risk_agreement': rate(sum(r['risk_agreement'] for r in risks), len(risks)),
            'serious_underestimate_rate': rate(sum(r['serious_underestimate'] for r in risks), sum(r['expected_risk'] == 'HIGH' for r in risks)),
            'risk_confusion': confusion,
            'evidence_support': {'value': None, 'support': 0, 'reason': 'requires calibrated assessment or judge'},
            'human_acceptance': {'value': None, 'support': 0, 'reason': 'no human labels required for this run'},
            'weighted_critical_miss_rate': rate(sum(r['severity_weight'] for r in rows if r['critical_miss']),
                                                sum(r['severity_weight'] for r in rows if r['critical_positive'])),
            'unscored_extraction_pairs': len(rows) - len(scored)}


def paired_comparison(base_rows, other_rows, *, seed=42, repeats=500, sequential=False):
    """Bootstrap groups rather than treating spans from the same contract as independent."""
    keys = lambda rows: {(r['case_id'], r['target']): r for r in rows}
    base, other = keys(base_rows), keys(other_rows)
    if base.keys() != other.keys() or not base:
        raise ValueError('paired comparisons require identical complete case coverage')
    differences = {}
    for key, b in base.items():
        o = other[key]
        if b['extraction_scored'] != o['extraction_scored']:
            raise ValueError('paired comparison coverage differs')
        group = 'whole-sequence' if sequential else b['family_id']
        differences.setdefault(group, []).append(int(b['failed'] or b['critical_miss'] or b['false_positive'])
                                                 - int(o['failed'] or o['critical_miss'] or o['false_positive']))
    groups = list(differences)
    randomizer = random.Random(seed)
    samples = []
    for _ in range(repeats):
        values = [v for _ in groups for v in differences[randomizer.choice(groups)]]
        samples.append(sum(values) / len(values))
    samples.sort()
    values = [v for group in groups for v in differences[group]]
    return {'metric': 'paired_error_rate_reduction', 'value': sum(values) / len(values),
            'interval_95': [samples[int(repeats * .025)], samples[min(repeats - 1, int(repeats * .975))]] if len(groups) > 1 else None,
            'independent_groups': len(groups), 'resampling': 'whole-sequence' if sequential else 'document-family',
            'regressions': sum(v < 0 for v in values), 'improvements': sum(v > 0 for v in values)}


def quality_verdict(metrics, profile, *, execution_kind, calibrated=False, complete=True):
    """A missing required score, calibration or threshold cannot produce quality pass."""
    if not profile:
        return {'status': 'not_evaluated', 'reasons': ['No quality profile was supplied.']}
    if (not isinstance(profile, dict) or profile.keys() != {'schema', 'version', 'criteria'}
            or profile.get('schema') != 'contractguard_quality_profile_v1'
            or not isinstance(profile.get('version'), str) or not profile['version'].strip()
            or not isinstance(profile.get('criteria'), dict) or not profile['criteria']):
        raise ValueError('quality profile requires a version and explicit criteria')
    failed, missing = [], []
    rates = {'critical_miss_rate', 'negative_false_positive_rate', 'serious_underestimate_rate',
             'weighted_critical_miss_rate', 'failure_rate', 'judge_failure_rate',
             'retrieval_applicability_violation_rate', 'unsupported_finding_rate'}
    rates.add('retrieval_context_budget_violation_rate')
    budgets = {'mean_latency_seconds', 'p95_latency_seconds', 'estimated_cost_usd', 'regression_rate'}
    for name, bounds in profile['criteria'].items():
        if name not in rates | budgets:
            raise ValueError('quality criterion is not supported')
        if (not isinstance(bounds, dict) or bounds.keys() != {'maximum', 'minimum_support'}
                or type(bounds['maximum']) not in (int, float) or not math.isfinite(bounds['maximum'])
                or bounds['maximum'] < 0 or (name in rates and bounds['maximum'] > 1)
                or type(bounds['minimum_support']) is not int
                or bounds['minimum_support'] < 1):
            raise ValueError('quality criterion requires explicit bounds and coverage')
        measurement = metrics.get(name)
        if not isinstance(measurement, dict) or measurement.get('value') is None or measurement.get('support', 0) < bounds['minimum_support']:
            missing.append(name)
        elif measurement['value'] > bounds['maximum']:
            failed.append(name)
    if execution_kind != 'live' or not calibrated or not complete:
        return {'status': 'not_evaluated', 'reasons': ['A complete live run and calibrated assessment are required.'],
                'observed_failures': failed, 'unscored_criteria': missing}
    return {'status': 'failed' if failed else 'not_evaluated' if missing else 'passed',
            'failed_criteria': failed, 'unscored_criteria': missing, 'profile_version': profile.get('version')}
