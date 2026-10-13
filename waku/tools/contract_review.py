"""One plain-text review through Waku's existing tools, memory and model client."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile

from waku.memory.contractguard import CLAUSES, REVIEW_SCHEMA, validate_review
from waku.ops.tracing import metered
from waku.tools.clause_differ import compare_clause
from waku.tools.clause_extractor import extract_clause
from waku.tools.contract_parser import parse_contract
from waku.tools.contract_schema import (
    bounded_text,
    clause_name,
    load_json,
    object_fields,
    validate_context,
    validate_findings,
)
from waku.tools.registry import Tool
from waku.tools.report_generator import generate_report
from waku.tools.risk_scorer import score_risk

LEGACY_RESULT_SCHEMA = 'contractguard_result_v1'
RESULT_SCHEMA = 'contractguard_result_v2'
PROMPT_VERSION = '1'
MAX_CHARACTERS = 200_000
RESULT_FIELDS = {'schema', 'review_id', 'document_id', 'request', 'status', 'timestamp',
                 'findings', 'report', 'artifacts'}


def write_artifact(path: Path, text: str) -> None:
    """Replace one artifact atomically; a failed write leaves its predecessor intact."""
    temporary = None
    try:
        with NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as file:
            temporary = Path(file.name)
            file.write(text)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def encode(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, indent=2)


def review_episode(result: dict, finding: dict) -> dict:
    return {'schema': REVIEW_SCHEMA, 'contract_id': result['document_id'],
            'contract_name': result['request']['contract_name'], 'clause_type': finding['clause_type'],
            'risk_level': finding['risk']['risk_level'], 'finding_summary': finding['risk']['reasoning'],
            'evidence': finding['extraction']['spans'],
            'recommendation': finding['risk']['recommendation'], 'timestamp': result['timestamp'],
            'origin': result['request']['origin']}


def validate_result(result: dict, document: dict, request: dict, review_id: str, artifacts: dict) -> None:
    upgraded = 'review_context' in request
    object_fields(result, RESULT_FIELDS | ({'working_context'} if upgraded else set()), 'review result')
    if upgraded:
        from waku.memory.contractguard_context import validate_context

        validate_context(request['review_context'])
        if result['working_context'] != completed_working(document, request['clause_types'], result['findings']):
            raise ValueError('working context differs from current source and stage outcomes')
    if (result['schema'] not in (LEGACY_RESULT_SCHEMA, RESULT_SCHEMA) or result['review_id'] != review_id
            or result['document_id'] != document['document_id'] or result['request'] != request
            or result['artifacts'] != artifacts):
        raise ValueError('review artifact does not match the request')
    states = ('ready', 'completed', 'partial')
    if result['schema'] == RESULT_SCHEMA and request['origin'] == 'review':
        states = ('pending_review', 'partial')
    if result['status'] not in states:
        raise ValueError('invalid generation state')
    bounded_text(result['timestamp'], 'review timestamp', limit=100)
    timestamp = datetime.fromisoformat(result['timestamp'])
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError('review timestamp must include a timezone')
    validate_findings(result['findings'], document['text'], request['clause_types'])
    partial = any(f['status'] == 'error' for f in result['findings'])
    if (result['status'] == 'partial') != partial:
        raise ValueError('review status contradicts its findings')
    report = generate_report(document['text'], request['contract_name'], result['findings'],
                             request['clause_types'], compare_guidance=request['compare_guidance'])
    if result['report'] != report:
        raise ValueError('review report does not match structured findings')
    for finding in result['findings']:
        if finding['status'] == 'found':
            validate_review(review_episode(result, finding), document['text'])


def review_contract(memory, text: str, contract_name: str = 'Untitled contract',
                    clause_types: list[str] | None = None, compare_guidance: bool = True, *,
                    origin: str = 'review', notify=None, review_profile=None) -> dict:
    """Validate each stage and save ordinary reports for human review.

    Provenance is a trusted Python argument, never a model-facing tool argument.
    Partial reviews retry model stages; pending reports reuse immutable artifacts.
    """
    settings = memory.settings
    if not settings.contract_review:
        raise ValueError('review_contract requires WAKU_CONTRACT_REVIEW=1')
    bounded_text(text, 'contract text', limit=MAX_CHARACTERS)
    bounded_text(contract_name, 'contract name', limit=200)
    targets = [c.name for c in CLAUSES] if clause_types is None else clause_types
    if not isinstance(targets, list) or not 1 <= len(targets) <= len(CLAUSES):
        raise ValueError('request one to ten unique canonical clause types')
    for target in targets:
        clause_name(target)
    if len(set(targets)) != len(targets):
        raise ValueError('duplicate target clause type')
    if type(compare_guidance) is not bool or origin not in ('review', 'benchmark_prediction'):
        raise ValueError('invalid comparison option or review origin')
    emit = notify or (lambda kind, event: None)
    document = parse_contract(text, contract_name)
    request = {'contract_name': contract_name, 'clause_types': list(targets),
               'compare_guidance': compare_guidance, 'origin': origin,
               'provider': settings.provider, 'model': settings.model,
               'small_model': settings.small_model, 'prompt_version': PROMPT_VERSION}
    profile, working = None, None
    if settings.contract_memory_upgrade:
        from waku.memory.contractguard_context import review_context, scope_for, working_context
        from waku.memory.contractguard_store import identity as memory_identity

        profile = review_context(review_profile, scope=scope_for(settings.home))
        working = working_context(document, targets)
        request.update(review_context=profile, memory_policy_version='scoped-v1',
                       memory_snapshot=memory_identity(memory.domain.list()))
    elif review_profile is not None:
        raise ValueError('review profiles require WAKU_CONTRACT_MEMORY_UPGRADE=1')
    identity = {'document_id': document['document_id'], 'result_schema': RESULT_SCHEMA, **request}
    review_id = hashlib.sha256(encode(identity).encode('utf-8')).hexdigest()
    directory = settings.home / 'reviews' / review_id
    artifacts = {name: str(directory / filename) for name, filename in
                 (('document', 'document.json'), ('result', 'result.json'), ('report', 'report.md'))}
    result_path = Path(artifacts['result'])
    result = None
    if result_path.exists():
        result = load_json(result_path.read_text(encoding='utf-8'))
        validate_result(result, document, request, review_id, artifacts)
        if (load_json(Path(artifacts['document']).read_text(encoding='utf-8')) != document
                or Path(artifacts['report']).read_text(encoding='utf-8') != result['report']):
            raise ValueError('review artifacts are inconsistent')
        if result['status'] in ('completed', 'pending_review'):
            emit('review_cached', {'review_id': review_id})
            return {**result, 'cached': True}
    if result is None or result['status'] == 'partial':
        emit('contract_parsed', {'document_id': document['document_id'], **document['metadata']})
        memory.skills.refresh()
        procedures = {s.name: s.body for s in memory.skills.skills}
        skills = {c.name: c.skill for c in CLAUSES}
        findings = []
        for target in targets:
            item = {'clause_type': target, 'status': 'error', 'extraction': None, 'risk': None,
                    'comparison': None, 'memory_used': {'semantic': [], 'episodic': []}, 'error': None}
            stage = 'retrieval'
            try:
                message = encode({'task': 'Retrieve memory for this clause review only.',
                                  'clause_type': target, 'source_text': text})
                extra = {'review_profile': profile} if profile is not None else {}
                context = validate_context(memory.review_context(
                    message, clause_type=target, exclude_contract_id=document['document_id'], notify=emit, **extra))
                item['memory_used'] = context
                procedure = procedures.get(skills[target], '')
                if profile is not None:
                    from waku.memory.contractguard_context import procedure_context

                    procedure = procedure_context(procedure, profile, working)
                stage = 'extraction'
                item['extraction'] = extract_clause(
                    metered(memory.client, 'clause_extraction', emit), settings.model, text, target,
                    context=context, procedure=procedure, sections=document['sections'])
                emit('clause_extracted', {'clause_type': target, 'found': item['extraction']['found']})
                if not item['extraction']['found']:
                    item['status'] = 'not_found'
                else:
                    stage = 'risk'
                    item['risk'] = score_risk(metered(memory.client, 'risk_scoring', emit),
                                              settings.model, item['extraction'], text,
                                              context=context, procedure=procedure)
                    emit('risk_scored', {'clause_type': target, 'risk_level': item['risk']['risk_level']})
                    guidance = '\n'.join(context['semantic'])
                    if compare_guidance and guidance:
                        stage = 'comparison'
                        item['comparison'] = compare_clause(
                            metered(memory.client, 'clause_comparison', emit), settings.model,
                            item['extraction'], text, guidance)
                        emit('clause_compared', {'clause_type': target})
                    item['status'] = 'found'
            except Exception as exc:
                # Never echo arbitrary provider errors (which can include configuration).
                item['error'] = {'stage': stage, 'message':
                                 f'Stage failed to produce a validated result ({type(exc).__name__}).'}
                emit('review_failed', {'clause_type': target, 'stage': stage})
            findings.append(item)
        result = {'schema': RESULT_SCHEMA, 'review_id': review_id, 'document_id': document['document_id'],
                  'request': request, 'timestamp': datetime.now(UTC).isoformat(),
                  'status': 'partial' if any(f['status'] == 'error' for f in findings) else 'pending_review' if origin == 'review' else 'ready',
                  'findings': findings, 'artifacts': artifacts,
                  'report': generate_report(text, contract_name, findings, targets,
                                            compare_guidance=compare_guidance)}
        if profile is not None:
            result['working_context'] = completed_working(document, targets, findings)
        validate_result(result, document, request, review_id, artifacts)
        directory.mkdir(parents=True, exist_ok=True)
        write_artifact(Path(artifacts['document']), encode(document))
        write_artifact(Path(artifacts['report']), result['report'])
        write_artifact(result_path, encode(result))
        emit('report_generated', {'review_id': review_id, 'status': result['status']})
    if result['status'] == 'ready':
        for finding in result['findings']:
            if finding['status'] == 'found':
                memory.complete_review(review_episode(result, finding), text,
                                       candidates=[], recalled=encode(finding['memory_used']), notify=emit)
        result['status'] = 'completed'
        write_artifact(result_path, encode(result))
    return {**result, 'cached': False}


def completed_working(document, targets, findings):
    from waku.memory.contractguard_context import working_context

    working = working_context(document, targets)
    for item in findings:
        working['checks'][item['clause_type']] = {
            'extraction': 'failed' if item['extraction'] is None else 'completed',
            'assessment': 'completed' if item['risk'] is not None else 'not_applicable'
            if item['extraction'] is not None and not item['extraction']['found'] else 'failed'}
    return working


def make_tool(settings, memory) -> Tool:
    if memory is None or memory.settings is not settings:
        raise ValueError('review_contract requires the app memory facade')

    def run(text: str, contract_name: str = 'Untitled contract', clause_types=None,
            compare_guidance: bool = True, review_profile=None, _notify=None) -> str:
        return encode(review_contract(memory, text, contract_name, clause_types, compare_guidance,
                                      notify=_notify, review_profile=review_profile))

    profile_schema = {}
    if settings.contract_memory_upgrade:
        from waku.memory.contractguard_context import APPLICABILITY_FIELDS

        profile_schema = {'review_profile': {'type': 'object', 'properties': {
            key: {'type': 'string', 'minLength': 1, 'maxLength': 200}
            for key in (*APPLICABILITY_FIELDS, 'review_date')}, 'additionalProperties': False}}

    return Tool(
        name='review_contract',
        description='Review complete plain contract text for requested canonical clauses. Returns '
                    'grounded structured findings, a Markdown report and saved artifact paths. '
                    'Direct the person to Reviews to confirm or correct PENDING_REVIEW reports before '
                    'saving review memory. Report PARTIAL as incomplete; never treat failed stages as missing clauses.',
        input_schema={'type': 'object', 'properties': {
            'text': {'type': 'string', 'minLength': 1, 'maxLength': MAX_CHARACTERS},
            'contract_name': {'type': 'string', 'minLength': 1, 'maxLength': 200},
            'clause_types': {'type': 'array', 'minItems': 1, 'maxItems': len(CLAUSES),
                             'uniqueItems': True, 'items': {'type': 'string', 'enum': [c.name for c in CLAUSES]}},
            'compare_guidance': {'type': 'boolean'}, **profile_schema}, 'required': ['text'], 'additionalProperties': False},
        fn=run, wants_notify=True)
