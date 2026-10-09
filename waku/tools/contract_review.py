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

RESULT_SCHEMA = 'contractguard_result_v1'
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
    object_fields(result, RESULT_FIELDS, 'review result')
    if (result['schema'] != RESULT_SCHEMA or result['review_id'] != review_id
            or result['document_id'] != document['document_id'] or result['request'] != request
            or result['artifacts'] != artifacts or result['status'] not in ('ready', 'completed', 'partial')):
        raise ValueError('review artifact does not match the request')
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
                    origin: str = 'review', notify=None) -> dict:
    """Validate each stage, render a report, then hand completed positives to memory.

    Provenance is a trusted Python argument, never a model-facing tool argument.
    Partial reviews retry model stages; ready journals retry only persistence.
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
    identity = {'document_id': document['document_id'], **request}
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
        if result['status'] == 'completed':
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
                context = validate_context(memory.review_context(
                    message, clause_type=target, exclude_contract_id=document['document_id'], notify=emit))
                item['memory_used'] = context
                procedure = procedures.get(skills[target], '')
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
                  'status': 'partial' if any(f['status'] == 'error' for f in findings) else 'ready',
                  'findings': findings, 'artifacts': artifacts,
                  'report': generate_report(text, contract_name, findings, targets,
                                            compare_guidance=compare_guidance)}
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
                                       recalled=encode(finding['memory_used']), notify=emit)
        result['status'] = 'completed'
        write_artifact(result_path, encode(result))
    return {**result, 'cached': False}


def make_tool(settings, memory) -> Tool:
    if memory is None or memory.settings is not settings:
        raise ValueError('review_contract requires the app memory facade')

    def run(text: str, contract_name: str = 'Untitled contract', clause_types=None,
            compare_guidance: bool = True, _notify=None) -> str:
        return encode(review_contract(memory, text, contract_name, clause_types, compare_guidance,
                                      notify=_notify))

    return Tool(
        name='review_contract',
        description='Review complete plain contract text for requested canonical clauses. Returns '
                    'grounded structured findings, a Markdown report and saved artifact paths. '
                    'Report PARTIAL as incomplete; never treat failed stages as missing clauses.',
        input_schema={'type': 'object', 'properties': {
            'text': {'type': 'string', 'minLength': 1, 'maxLength': MAX_CHARACTERS},
            'contract_name': {'type': 'string', 'minLength': 1, 'maxLength': 200},
            'clause_types': {'type': 'array', 'minItems': 1, 'maxItems': len(CLAUSES),
                             'uniqueItems': True, 'items': {'type': 'string', 'enum': [c.name for c in CLAUSES]}},
            'compare_guidance': {'type': 'boolean'}}, 'required': ['text'], 'additionalProperties': False},
        fn=run, wants_notify=True)
