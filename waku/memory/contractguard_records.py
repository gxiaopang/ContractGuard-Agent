"""Versioned scoped approvals and corrections backed by saved human decisions."""

from __future__ import annotations

import json
import re
from datetime import datetime

from waku.memory.contractguard_context import validate_context

SCOPED_REVIEW = 'contractguard_review_v3'
CORRECTION = 'contractguard_correction_v1'
CORRECTION_FIELDS = {'schema', 'contract_id', 'contract_name', 'clause_type', 'context',
                     'review_id', 'decision_id', 'approved_at', 'timestamp', 'origin',
                     'original', 'replacement', 'reason'}


def project(finding):
    return {'found': finding['extraction']['found'], 'evidence': finding['extraction']['spans'],
            'risk_level': finding['risk']['risk_level'] if finding['risk'] else None,
            'reasoning': finding['risk']['reasoning'] if finding['risk'] else finding['extraction']['rationale'],
            'recommendation': finding['risk']['recommendation'] if finding['risk'] else None}


def correction_record(result, original, replacement, journal):
    return {'schema': CORRECTION, 'contract_id': result['document_id'],
            'contract_name': result['request']['contract_name'], 'clause_type': original['clause_type'],
            'context': result['request']['review_context'], 'review_id': result['review_id'],
            'decision_id': journal['decision_id'], 'approved_at': journal['timestamp'],
            'timestamp': journal['timestamp'], 'origin': 'review',
            'original': project(original), 'replacement': project(replacement),
            'reason': journal['submission']['note'] or project(replacement)['reasoning']}


def validate_record(record, source=None):
    from waku.memory.contractguard import APPROVED_REVIEW_SCHEMA, CLAUSE_NAMES, validate_review
    from waku.tools.contract_schema import bounded_text, validate_extraction, validate_risk

    if record['schema'] == SCOPED_REVIEW:
        if 'context' not in record:
            raise ValueError('scoped reviews require context')
        validate_context(record['context'])
        validate_review({**{k: v for k, v in record.items() if k != 'context'},
                         'schema': APPROVED_REVIEW_SCHEMA}, source)
    elif record['schema'] == CORRECTION:
        if record.keys() != CORRECTION_FIELDS or record['clause_type'] not in CLAUSE_NAMES:
            raise ValueError('invalid correction fields')
        validate_context(record['context'])
        for key in ('contract_id', 'contract_name', 'reason', 'timestamp', 'approved_at'):
            bounded_text(record[key], key)
        for key in ('review_id', 'decision_id'):
            if not isinstance(record[key], str) or re.fullmatch(r'[0-9a-f]{64}', record[key]) is None:
                raise ValueError('invalid correction approval identity')
        stamp = datetime.fromisoformat(record['timestamp'])
        if stamp.utcoffset() is None or record['approved_at'] != record['timestamp'] or record['origin'] != 'review':
            raise ValueError('invalid correction approval timestamp or origin')
        for value in (record['original'], record['replacement']):
            if not isinstance(value, dict) or value.keys() != {'found', 'evidence', 'risk_level', 'reasoning', 'recommendation'}:
                raise ValueError('invalid corrected finding')
            extraction = {'clause_type': record['clause_type'], 'found': value['found'],
                          'spans': value['evidence'], 'confidence': 1.0, 'rationale': value['reasoning']}
            # Stored records validate span structure; source-aware writers recheck quotations.
            if source is not None:
                validate_extraction(extraction, source)
            else:
                if type(value['found']) is not bool:
                    raise ValueError('corrected presence must be boolean')
                bounded_text(value['reasoning'], 'corrected reasoning')
                if value['found']:
                    from waku.memory.contractguard import validate_evidence

                    validate_evidence(value['evidence'])
                elif value['evidence'] != []:
                    raise ValueError('negative correction must have no positive evidence')
            if value['found']:
                if value['risk_level'] not in ('HIGH', 'MEDIUM', 'LOW'):
                    raise ValueError('invalid corrected risk')
                bounded_text(value['recommendation'], 'corrected recommendation')
                if source is not None:
                    validate_risk({'clause_type': record['clause_type'], 'risk_level': value['risk_level'],
                                   'reasoning': value['reasoning'], 'recommendation': value['recommendation'],
                                   'evidence_indices': list(range(len(value['evidence'])))}, extraction, source)
            elif value['risk_level'] is not None or value['recommendation'] is not None:
                raise ValueError('negative correction cannot assess detected-clause risk')
        if record['original'] == record['replacement']:
            raise ValueError('a correction must change the finding')
    else:
        raise ValueError('unknown scoped record version')
    return json.loads(json.dumps(record))
