"""Risk-label agreement on authored rubrics, separate from CUAD extraction gold."""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

from evals.contractguard.predictor import CountingClient, Telemetry
from waku.memory import bundled_skill_dirs
from waku.memory.contractguard import CLAUSES
from waku.memory.procedural.loader import SkillLoader
from waku.ops.tracing import metered
from waku.tools.clause_extractor import extract_clause
from waku.tools.contract_schema import bounded_text, clause_name, load_json, object_fields
from waku.tools.risk_scorer import score_risk

RISK_VERSION = 'contractguard_curated_risk_v1'
LEVELS = ('HIGH', 'MEDIUM', 'LOW')


def load_cases(path: Path) -> tuple[list[dict], str]:
    raw = Path(path).read_bytes()
    cases, identities = [], set()
    for line in raw.decode('utf-8').splitlines():
        if not line.strip():
            continue
        case = load_json(line)
        object_fields(case, {'id', 'clause_type', 'text', 'perspective', 'expected_risk', 'rubric'}, 'risk case')
        for key in ('id', 'text', 'perspective', 'rubric'):
            bounded_text(case[key], key)
        clause_name(case['clause_type'])
        if case['id'] in identities or case['expected_risk'] not in LEVELS:
            raise ValueError('risk examples require unique ids and explicit expected labels')
        identities.add(case['id'])
        cases.append(case)
    if not cases:
        raise ValueError('risk set must contain examples')
    return cases, hashlib.sha256(raw).hexdigest()


def evaluate_risk(cases: list[dict], settings, client) -> tuple[list[dict], dict]:
    telemetry = Telemetry(settings.provider)
    counted = CountingClient(client, telemetry)
    bodies = {s.name: s.body for s in SkillLoader(bundled_skill_dirs()).skills}
    procedures = {c.name: bodies[c.skill] for c in CLAUSES}
    predictions, started = [], time.perf_counter()
    for case in cases:
        stage, extraction, risk, error = 'extraction', None, None, None
        # Neither expected label nor scoring rubric is supplied to a model.
        procedure = procedures[case['clause_type']] + '\nReview perspective: ' + case['perspective']
        try:
            extraction = extract_clause(metered(counted, 'clause_extraction', telemetry.event),
                                        settings.model, case['text'], case['clause_type'], procedure=procedure)
            if not extraction['found']:
                raise ValueError('authored risk case produced no detected evidence')
            stage = 'risk'
            risk = score_risk(metered(counted, 'risk_scoring', telemetry.event), settings.model,
                              extraction, case['text'], procedure=procedure)
        except Exception as exc:
            error = {'stage': stage, 'message': f'No validated risk result ({type(exc).__name__}).'}
        predictions.append({'id': case['id'], 'clause_type': case['clause_type'], 'extraction': extraction,
                            'risk': risk, 'error': error, 'expected_risk': case['expected_risk'],
                            'rubric': case['rubric']})
    matrix = {expected: {actual: 0 for actual in (*LEVELS, 'ERROR')} for expected in LEVELS}
    for prediction in predictions:
        actual = prediction['risk']['risk_level'] if prediction['risk'] is not None else 'ERROR'
        matrix[prediction['expected_risk']][actual] += 1
    correct = sum(matrix[level][level] for level in LEVELS)
    errors = sum(row['ERROR'] for row in matrix.values())
    metrics = {'schema': RISK_VERSION, 'reference': 'authored-risk-rubrics', 'cases': len(cases),
               'correct_labels': correct, 'failed_cases': errors,
               'label_agreement': correct / len(cases), 'confusion': matrix,
               'interpretation': 'Agreement with authored examples; general legal accuracy is unmeasured.',
               'telemetry': {**telemetry.snapshot(), 'latency_seconds': time.perf_counter() - started}}
    return predictions, metrics
