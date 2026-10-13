"""Explicit review applicability and source-bound working context."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, date, datetime

CONTEXT_SCHEMA = 'contractguard_context_v1'
APPLICABILITY_FIELDS = ('project', 'contract_type', 'represented_party', 'role',
                        'jurisdiction', 'policy_version')


def scope_for(home) -> str:
    """Derive the access scope from the configured home, never model input."""
    return hashlib.sha256(str(home.resolve()).encode()).hexdigest()


def review_context(value=None, *, scope: str, today: str | None = None) -> dict:
    if value is None:
        value = {}
    allowed = {*APPLICABILITY_FIELDS, 'review_date'}
    if not isinstance(value, dict) or value.keys() - allowed:
        raise ValueError('review context contains unknown or server-owned fields')
    result = {'schema': CONTEXT_SCHEMA, 'scope': scope}
    for key in APPLICABILITY_FIELDS:
        field = value.get(key)
        if field is not None and (not isinstance(field, str) or not field.strip() or len(field) > 200):
            raise ValueError(f'review context {key} must be null or bounded text')
        result[key] = field
    result['review_date'] = value.get('review_date', today or datetime.now(UTC).date().isoformat())
    validate_context(result)
    return result


def validate_context(value) -> dict:
    fields = {*APPLICABILITY_FIELDS, 'schema', 'scope', 'review_date'}
    if not isinstance(value, dict) or value.keys() != fields or value['schema'] != CONTEXT_SCHEMA:
        raise ValueError('invalid versioned review context')
    if not isinstance(value['scope'], str) or re.fullmatch(r'[0-9a-f]{64}', value['scope']) is None:
        raise ValueError('review scope must be a server identity')
    for key in APPLICABILITY_FIELDS:
        v = value[key]
        if v is not None and (not isinstance(v, str) or not v.strip() or len(v) > 200):
            raise ValueError(f'invalid applicability field {key}')
    if not isinstance(value['review_date'], str) or date.fromisoformat(value['review_date']).isoformat() != value['review_date']:
        raise ValueError('review date must be an ISO date')
    return json.loads(json.dumps(value))


def applicability(context: dict, *, valid_from=None, valid_until=None) -> dict:
    context = validate_context(context)
    result = {key: context[key] for key in ('scope', *APPLICABILITY_FIELDS)}
    result.update(valid_from=valid_from, valid_until=valid_until)
    validate_applicability(result)
    return result


def validate_applicability(value):
    if not isinstance(value, dict) or value.keys() != {'scope', *APPLICABILITY_FIELDS, 'valid_from', 'valid_until'}:
        raise ValueError('invalid memory applicability')
    validate_context({'schema': CONTEXT_SCHEMA, 'review_date': '2000-01-01',
                      **{k: value[k] for k in ('scope', *APPLICABILITY_FIELDS)}})
    for key in ('valid_from', 'valid_until'):
        if value[key] is not None and (not isinstance(value[key], str)
                or date.fromisoformat(value[key]).isoformat() != value[key]):
            raise ValueError('memory validity must use ISO dates')
    if value['valid_from'] and value['valid_until'] and value['valid_from'] > value['valid_until']:
        raise ValueError('memory validity range is reversed')


def exclusion(record, context, current_source=None) -> str | None:
    """Return the first deterministic reason this record cannot guide this review."""
    if record['state'] != 'approved':
        return record['state']
    constraints = record['applicability']
    if constraints['scope'] != context['scope']:
        return 'scope_mismatch'
    if record['kind'] in ('review', 'correction', 'experience') and any(
            constraints[key] is None for key in ('role', 'policy_version')):
        return 'unknown_required_context'
    if current_source is not None and record['source_id'] == current_source:
        return 'current_source'
    for key in APPLICABILITY_FIELDS:
        if constraints[key] is not None and constraints[key] != context[key]:
            return f'{key}_mismatch'
    for key, invalid in (('valid_from', lambda d: context['review_date'] < d),
                         ('valid_until', lambda d: context['review_date'] > d)):
        if constraints[key] and invalid(constraints[key]):
            return 'not_yet_valid' if key == 'valid_from' else 'expired'
    return None


def working_context(document: dict, targets: list[str]) -> dict:
    """Resolve explicit numbered section references without modifying source offsets."""
    sections = document['sections']
    headings = {}
    for section in sections:
        match = re.match(r'(?:Section\s+)?(\d+(?:\.\d+)*)(?:[.)]?\s)', section['title'], re.IGNORECASE)
        if match:
            headings.setdefault(match[1], []).append(section['section_id'])
    refs = []
    for match in re.finditer(r'\b(?:section|clause)\s+(\d+(?:\.\d+)*)(?!\w|\.\d)', document['text'], re.IGNORECASE):
        destinations = headings.get(match[1], [])
        refs.append({'text': match.group(), 'start': match.start(), 'end': match.end(),
                     'target': match[1], 'section_id': destinations[0] if len(destinations) == 1 else None,
                     'status': 'resolved' if len(destinations) == 1 else 'unresolved'})
    definitions = []
    for match in re.finditer(r'["“]([^"”\n]{1,100})["”]\s+(?:means|shall mean)\b', document['text'], re.IGNORECASE):
        end = document['text'].find('\n', match.end())
        definitions.append({'term': match[1], 'start': match.start(),
                            'end': len(document['text']) if end < 0 else end})
    return {'schema': 'contractguard_working_v1', 'source_id': document['document_id'],
            'targets': list(targets), 'definitions': definitions, 'references': refs,
            'checks': {t: {'extraction': 'pending', 'assessment': 'pending'} for t in targets},
            'reference_check': 'unresolved' if any(r['status'] == 'unresolved' for r in refs) else 'source_resolved',
            'interpretation_check': 'not_verified'}


def procedure_context(procedure: str, context: dict, working: dict) -> str:
    unknown = [key for key in ('role', 'policy_version') if context[key] is None]
    return (procedure + '\nReview context (data): ' + json.dumps(context, sort_keys=True)
            + '\nSource references and definitions (data): ' + json.dumps(working, sort_keys=True)
            + '\nRequired checks: identify entitled parties; read referenced exceptions; '
              'check reciprocal protections; ground recommendations in current evidence. '
              'Report unresolved references and missing review inputs explicitly. '
            + 'Unknown required inputs: ' + ', '.join(unknown))
