"""Closed schemas between review stages; report prose never becomes evaluator input."""

from __future__ import annotations

import json
import math

from waku.memory.contractguard import CLAUSE_NAMES, validate_evidence, validate_review

DIFFERENCE_FIELDS = ('material_differences', 'missing_protections',
                     'additional_obligations', 'risk_relevant_deviations')
FINDING_FIELDS = {'clause_type', 'status', 'extraction', 'risk', 'comparison', 'memory_used', 'error'}


def object_fields(value, fields: set, label: str) -> None:
    if not isinstance(value, dict) or value.keys() != fields:
        raise ValueError(f'{label} fields must be exactly {", ".join(sorted(fields))}')


def bounded_text(value, label: str, limit: int = 4000) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f'{label} must be a nonempty string of at most {limit} characters')


def clause_name(value, expected: str | None = None) -> None:
    if not isinstance(value, str) or value not in CLAUSE_NAMES:
        raise ValueError('unknown canonical clause type')
    if expected is not None and value != expected:
        raise ValueError('result names a different clause type')


def load_json(text: str):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate JSON field')
            result[key] = value
        return result

    def constant(value):
        raise ValueError(f'non-finite JSON value: {value}')

    return json.loads(text, object_pairs_hook=unique, parse_constant=constant)


def call_json(client, model: str, system: str, payload: dict, *, max_tokens: int = 4096) -> dict:
    """One provider-compatible call; malformed or truncated replies are stage failures."""
    try:
        response = client.messages.create(model=model, max_tokens=max_tokens, system=system,
                                          messages=[{'role': 'user', 'content': json.dumps(
                                              payload, ensure_ascii=False, allow_nan=False)}])
    except Exception as exc:
        raise ValueError(f'model request failed ({type(exc).__name__})') from exc
    if response.stop_reason != 'end_turn' or any(b.type == 'tool_use' for b in response.content):
        raise ValueError('model did not finish a structured answer')
    text = ''.join(b.text for b in response.content if b.type == 'text')
    try:
        data = load_json(text)
    except (ValueError, TypeError) as exc:
        raise ValueError('model returned invalid JSON') from exc
    if not isinstance(data, dict):
        raise TypeError('model answer must be a JSON object')
    return data


def validate_extraction(value: dict, source_text: str, expected_clause: str | None = None) -> dict:
    object_fields(value, {'clause_type', 'found', 'spans', 'confidence', 'rationale'}, 'extraction')
    clause_name(value['clause_type'], expected_clause)
    if type(value['found']) is not bool:
        raise ValueError('found must be a boolean')
    confidence = value['confidence']
    if type(confidence) not in (int, float) or not 0 <= confidence <= 1 or not math.isfinite(confidence):
        raise ValueError('confidence must be a finite number from 0 to 1')
    bounded_text(value['rationale'], 'rationale')
    if not isinstance(source_text, str):
        raise TypeError('source text must be a string')
    if value['found']:
        validate_evidence(value['spans'], source_text)
        offsets = {(s['start'], s['end']) for s in value['spans']}
        if len(offsets) != len(value['spans']):
            raise ValueError('duplicate evidence span')
    elif value['spans'] != []:
        raise ValueError('a negative finding must contain an empty span list')
    return value


def evidence_indices(value, extraction: dict) -> None:
    if (not isinstance(value, list) or not value
            or any(type(i) is not int or not 0 <= i < len(extraction['spans']) for i in value)
            or len(set(value)) != len(value)):
        raise ValueError('evidence indices must uniquely reference detected source spans')


def validate_risk(value: dict, extraction: dict, source_text: str) -> dict:
    validate_extraction(extraction, source_text)
    if not extraction['found']:
        raise ValueError('risk assessment requires a detected clause')
    object_fields(value, {'clause_type', 'risk_level', 'reasoning', 'recommendation',
                          'evidence_indices'}, 'risk')
    clause_name(value['clause_type'], extraction['clause_type'])
    if not isinstance(value['risk_level'], str) or value['risk_level'] not in {'HIGH', 'MEDIUM', 'LOW'}:
        raise ValueError('risk level must be HIGH, MEDIUM or LOW')
    bounded_text(value['reasoning'], 'risk reasoning')
    bounded_text(value['recommendation'], 'recommendation')
    evidence_indices(value['evidence_indices'], extraction)
    return value


def validate_comparison(value: dict, extraction: dict, source_text: str, guidance: str) -> dict:
    validate_extraction(extraction, source_text)
    if not extraction['found']:
        raise ValueError('comparison requires a detected clause')
    bounded_text(guidance, 'standard guidance', limit=50000)
    object_fields(value, {'clause_type', *DIFFERENCE_FIELDS}, 'comparison')
    clause_name(value['clause_type'], extraction['clause_type'])
    for field in DIFFERENCE_FIELDS:
        if not isinstance(value[field], list) or len(value[field]) > 20:
            raise ValueError('comparison categories must be lists with at most 20 entries')
        for entry in value[field]:
            object_fields(entry, {'description', 'evidence_indices', 'guidance_quote'}, 'difference')
            bounded_text(entry['description'], 'difference description')
            bounded_text(entry['guidance_quote'], 'guidance quote')
            if entry['guidance_quote'] not in guidance:
                raise ValueError('comparison quote does not occur in standard guidance')
            evidence_indices(entry['evidence_indices'], extraction)
    return value


def validate_context(value: dict) -> dict:
    object_fields(value, {'semantic', 'episodic'}, 'memory context')
    if not isinstance(value['semantic'], list) or not isinstance(value['episodic'], list):
        raise TypeError('memory stores must return lists')
    for fact in value['semantic']:
        bounded_text(fact, 'semantic context', limit=50000)
    for episode in value['episodic']:
        validate_review(episode)
    return value


def validate_findings(findings: list, source_text: str, targets: list[str]) -> None:
    if not isinstance(targets, list) or not 1 <= len(targets) <= len(CLAUSE_NAMES):
        raise ValueError('report requires one to ten canonical target clauses')
    for target in targets:
        clause_name(target)
    if len(set(targets)) != len(targets):
        raise ValueError('duplicate report target clause')
    if not isinstance(findings, list) or len(findings) != len(targets):
        raise ValueError('report must contain one result per requested clause')
    for item, target in zip(findings, targets, strict=True):
        object_fields(item, FINDING_FIELDS, 'finding')
        clause_name(item['clause_type'], target)
        context = validate_context(item['memory_used'])
        extraction, risk, comparison = item['extraction'], item['risk'], item['comparison']
        if extraction is not None:
            validate_extraction(extraction, source_text, target)
        if risk is not None:
            if extraction is None:
                raise ValueError('risk has no source extraction')
            validate_risk(risk, extraction, source_text)
        if comparison is not None:
            if risk is None:
                raise ValueError('comparison has no assessed finding')
            validate_comparison(comparison, extraction, source_text, '\n'.join(context['semantic']))
        if item['status'] == 'found':
            if extraction is None or not extraction['found'] or risk is None or item['error'] is not None:
                raise ValueError('detected finding lacks validated evidence or assessment')
        elif item['status'] == 'not_found':
            if (extraction is None or extraction['found'] or risk is not None
                    or comparison is not None or item['error'] is not None):
                raise ValueError('missing-clause result contradicts its extraction')
        elif item['status'] == 'error':
            object_fields(item['error'], {'stage', 'message'}, 'stage error')
            bounded_text(item['error']['message'], 'stage error')
            stage = item['error']['stage']
            state = {'retrieval': (False, False), 'extraction': (False, False),
                     'risk': (True, False), 'comparison': (True, True)}
            if (not isinstance(stage, str) or stage not in state or comparison is not None
                    or (extraction is not None, risk is not None) != state[stage]
                    or (extraction is not None and not extraction['found'])):
                raise ValueError('failed stage contradicts its retained results')
        else:
            raise ValueError('unknown finding status')
