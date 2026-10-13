"""Human decisions over saved reports; no model-facing tool or provider calls."""

from __future__ import annotations

import hashlib
import re
import sqlite3
from contextlib import closing, contextmanager
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

from waku.db import connect
from waku.memory import Memory
from waku.memory.contractguard import APPROVED_REVIEW_SCHEMA, encode_review
from waku.tools.contract_parser import parse_contract
from waku.tools.contract_review import (
    RESULT_SCHEMA,
    encode,
    review_episode,
    validate_result,
    write_artifact,
)
from waku.tools.contract_schema import bounded_text, load_json, object_fields, validate_findings
from waku.tools.report_generator import generate_report

IDENTITY = re.compile(r'[0-9a-f]{64}')
MAX_BYTES = 8_000_000
HUMAN_SCHEMA = 'contractguard_human_review_v1'
JOURNAL_FIELDS = {'schema', 'review_id', 'revision', 'decision_id', 'timestamp', 'submission',
                  'accepted_findings', 'status'}


class ReviewConflict(ValueError):
    """A saved decision or source revision differs from this submission."""


def identity(value, label):
    if not isinstance(value, str) or IDENTITY.fullmatch(value) is None:
        raise ValueError(f'{label} must be a saved 64-character identity')


def read_artifact(home: Path, path: Path) -> str:
    target = path.resolve()
    if home not in target.parents or not target.is_file() or target.stat().st_size > MAX_BYTES:
        raise ValueError('artifact must be a bounded file inside the configured home')
    return target.read_text(encoding='utf-8')


def digest(value: dict) -> str:
    return hashlib.sha256(encode(value).encode('utf-8')).hexdigest()


def load_review(home: Path, review_id: str) -> tuple[dict, dict, Path]:
    identity(review_id, 'review id')
    home = Path(home).resolve()
    folder = home / 'reviews' / review_id
    document = load_json(read_artifact(home, folder / 'document.json'))
    result = load_json(read_artifact(home, folder / 'result.json'))
    request = result['request']
    if 'review_context' in request:
        from waku.memory.contractguard_context import scope_for, validate_context

        profile = validate_context(request['review_context'])
        if profile['scope'] != scope_for(home):
            raise ValueError('review context differs from the configured home')
    bounded_text(document['text'], 'source text', limit=200_000)
    if document != parse_contract(document['text'], request['contract_name']):
        raise ValueError('document does not preserve its source structure')
    artifacts = {key: folder / filename for key, filename in
                 (('document', 'document.json'), ('result', 'result.json'), ('report', 'report.md'))}
    declared = result['artifacts']
    if (not isinstance(declared, dict) or set(declared) != set(artifacts)
            or any(not isinstance(declared[key], str) or Path(declared[key]).resolve() != path
                   for key, path in artifacts.items())):
        raise ValueError('review artifact paths differ from its directory')
    validate_result(result, document, request, review_id, declared)
    if read_artifact(home, folder / 'report.md') != result['report']:
        raise ValueError('report differs from validated findings')
    return document, result, folder


def align_quotes(evidence, source):
    if not isinstance(evidence, list) or not 1 <= len(evidence) <= 20:
        raise ValueError('positive findings require one to twenty source quotations')
    spans = []
    for quote in evidence:
        object_fields(quote, {'text', 'occurrence'}, 'source quotation')
        bounded_text(quote['text'], 'source quotation')
        starts, offset = [], 0
        while (start := source.find(quote['text'], offset)) >= 0:
            starts.append(start)
            offset = start + 1
        occurrence = quote['occurrence']
        if occurrence is None and len(starts) == 1:
            occurrence = 0
        if type(occurrence) is not int or not 0 <= occurrence < len(starts):
            raise ValueError('quote must match source; repeated quotes require a zero-based occurrence')
        start = starts[occurrence]
        spans.append({'text': quote['text'], 'start': start, 'end': start + len(quote['text'])})
    return spans


def accepted_findings(payload, result, source):
    fields = {'review_id', 'revision', 'decision', 'reviewer', 'note'}
    if isinstance(payload, dict) and payload.get('decision') == 'correct':
        fields.add('findings')
    object_fields(payload, fields, 'human review submission')
    identity(payload['review_id'], 'review id')
    identity(payload['revision'], 'source revision')
    if payload['decision'] not in ('confirm', 'correct', 'reject'):
        raise ValueError('decision must be confirm, correct or reject')
    bounded_text(payload['reviewer'], 'reviewer label', limit=200)
    if not isinstance(payload['note'], str) or len(payload['note']) > 4000:
        raise ValueError('note must be a string of at most 4000 characters')
    if payload['review_id'] != result['review_id'] or payload['revision'] != digest(result):
        raise ReviewConflict('The generated report changed. Reload it before submitting.')
    if payload['decision'] == 'reject':
        return []
    findings = deepcopy(result['findings'])
    if payload['decision'] == 'correct':
        edits = payload['findings']
        if not isinstance(edits, list) or len(edits) != len(findings):
            raise ValueError('correction requires one finding per requested clause')
        for edit, item in zip(edits, findings, strict=True):
            object_fields(edit, {'clause_type', 'found', 'evidence', 'risk_level',
                                 'reasoning', 'recommendation'}, 'corrected finding')
            if edit['clause_type'] != item['clause_type'] or type(edit['found']) is not bool:
                raise ValueError('corrected clauses must preserve the requested order and presence boolean')
            bounded_text(edit['reasoning'], 'human reasoning')
            original = deepcopy(item)
            extraction = item['extraction']
            extraction['found'] = edit['found']
            extraction['spans'] = align_quotes(edit['evidence'], source) if edit['found'] else []
            if not edit['found']:
                if edit['evidence'] != [] or edit['risk_level'] is not None or edit['recommendation'] is not None:
                    raise ValueError('missing clauses cannot carry evidence, risk or recommendation')
                item['risk'] = None
                extraction['rationale'] = edit['reasoning']
            else:
                item['risk'] = {'clause_type': item['clause_type'], 'risk_level': edit['risk_level'],
                                'reasoning': edit['reasoning'], 'recommendation': edit['recommendation'],
                                'evidence_indices': list(range(len(extraction['spans'])))}
            item['status'] = 'found' if edit['found'] else 'not_found'
            if item != original:
                extraction['rationale'] = edit['reasoning']
                item['comparison'] = None
    validate_findings(findings, source, result['request']['clause_types'])
    return findings


def decision_identity(journal):
    return digest({k: v for k, v in journal.items() if k not in ('status', 'decision_id')})


def read_decision(home, result, document, folder):
    path = folder / 'human_review.json'
    if not path.exists() and not path.is_symlink():
        return None
    journal = load_json(read_artifact(home, path))
    object_fields(journal, JOURNAL_FIELDS, 'saved human decision')
    expected = accepted_findings(journal['submission'], result, document['text'])
    rejected = journal['submission']['decision'] == 'reject'
    if (journal['schema'] != HUMAN_SCHEMA or journal['review_id'] != result['review_id']
            or journal['revision'] != digest(result) or journal['accepted_findings'] != expected
            or journal['decision_id'] != decision_identity(journal)
            or journal['status'] not in (('rejected',) if rejected else ('approved', 'completed'))):
        raise ValueError('saved human decision is inconsistent')
    bounded_text(journal['timestamp'], 'approval timestamp', limit=100)
    stamp = datetime.fromisoformat(journal['timestamp'])
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError('approval timestamp must include a timezone')
    if result['schema'] != RESULT_SCHEMA or result['request']['origin'] != 'review' or result['status'] != 'pending_review':
        raise ValueError('human decisions require a complete ordinary v2 report')
    return journal


def approved_episode(result, finding, journal):
    record = {**review_episode(result, finding), 'schema': APPROVED_REVIEW_SCHEMA,
              'timestamp': journal['timestamp'], 'approved_at': journal['timestamp'],
              'review_id': result['review_id'], 'decision_id': journal['decision_id']}
    if 'review_context' in result['request']:
        record.update(schema='contractguard_review_v3', context=result['request']['review_context'])
    return record


def validate_approved_record(home, record, source):
    identity(record.get('review_id'), 'approval review id')
    home = Path(home).resolve()
    document, result, folder = load_review(home, record['review_id'])
    journal = read_decision(home, result, document, folder)
    if document['text'] != source or journal is None or journal['status'] == 'rejected':
        raise ValueError('ordinary memory writes require a saved human approval')
    if record.get('schema') == 'contractguard_correction_v1':
        from waku.memory.contractguard_records import correction_record, project

        if (journal['submission']['decision'] == 'correct' and 'review_context' in result['request']
                and any(project(a) != project(b) and correction_record(result, a, b, journal) == record
                        for a, b in zip(result['findings'], journal['accepted_findings'], strict=True))):
            return
        raise ValueError('correction differs from the saved human decision')
    if not any(f['status'] == 'found' and approved_episode(result, f, journal) == record
               for f in journal['accepted_findings']):
        raise ValueError('memory record differs from the saved human approval')


@contextmanager
def decision_lock(home):
    """Serialize decisions across threads and processes, including crash recovery.

    A separate SQLite transaction avoids holding state.db while writing episodes.
    It stores no review data and the OS releases its lock after a crashed process.
    """
    path = home / 'reviews' / '.decisions.db'
    if path.is_symlink():
        raise ValueError('decision lock cannot be a symlink')
    with closing(sqlite3.connect(path, timeout=30)) as conn:
        conn.execute('BEGIN IMMEDIATE')
        try:
            yield
        finally:
            conn.rollback()


def submit_review(settings, payload, *, memory=None):
    """Validate, journal, and persist one human decision without a model call."""
    if not settings.contract_review:
        raise ValueError('Human review requires WAKU_CONTRACT_REVIEW=1.')
    if not isinstance(payload, dict):
        raise TypeError('human review submission must be an object')
    home = Path(settings.home).resolve()
    document, result, folder = load_review(home, payload.get('review_id'))
    if result['schema'] != RESULT_SCHEMA or result['request']['origin'] != 'review':
        raise ValueError('Only ordinary v2 reports support human decisions.')
    if result['status'] != 'pending_review':
        raise ReviewConflict('Regenerate a complete report before reviewing it.')
    if 'review_context' in result['request'] and not settings.contract_memory_upgrade:
        raise ValueError('Enable the upgraded memory policy before approving this review.')
    # The loader has checked every source path before the lock creates a file.
    with decision_lock(home):
        document, result, folder = load_review(home, payload['review_id'])
        accepted = accepted_findings(payload, result, document['text'])
        journal = read_decision(home, result, document, folder)
        if journal is not None and journal['submission'] != payload:
            raise ReviewConflict('This report already has a different human decision.')
        if journal is None:
            journal = {'schema': HUMAN_SCHEMA, 'review_id': result['review_id'], 'revision': digest(result),
                       'timestamp': datetime.now(UTC).isoformat(), 'submission': deepcopy(payload),
                       'accepted_findings': accepted,
                       'status': 'rejected' if payload['decision'] == 'reject' else 'approved'}
            journal['decision_id'] = decision_identity(journal)
            write_artifact(folder / 'human_review.json', encode(journal))
        if journal['status'] in ('rejected', 'completed'):
            return journal
        write_artifact(folder / 'approved_report.md', generate_report(
            document['text'], result['request']['contract_name'], accepted, result['request']['clause_types'],
            compare_guidance=result['request']['compare_guidance']))
        if memory is None:
            # Construct only local SQLite memory; provider credentials are unnecessary.
            if (home / 'state.db').is_symlink() or (home / 'memory').is_symlink() or (home / 'MEMORY.md').is_symlink():
                raise ValueError('memory output paths cannot be symlinks')
            if (home / 'memory').exists() and any(p.is_symlink() for p in (home / 'memory').glob('*.md')):
                raise ValueError('memory export files cannot be symlinks')
            with closing(connect(home)) as conn:
                _persist(Memory(conn, settings, None), result, document, journal)
        else:
            if Path(memory.settings.home).resolve() != home:
                raise ValueError('memory must belong to the configured home')
            _persist(memory, result, document, journal)
        journal['status'] = 'completed'
        write_artifact(folder / 'human_review.json', encode(journal))
        return journal


def _persist(memory, result, document, journal):
    existing = {row['summary'] for row in memory.episodes.list(limit=-1)}
    for finding in journal['accepted_findings']:
        if finding['status'] == 'found':
            record = approved_episode(result, finding, journal)
            if memory.domain is not None or encode_review(record, document['text']) not in existing:
                memory.complete_review(record, document['text'], candidates=[])
    if 'review_context' in result['request'] and journal['submission']['decision'] == 'correct':
        from waku.memory.contractguard_records import correction_record, project

        for original, replacement in zip(result['findings'], journal['accepted_findings'], strict=True):
            if project(original) != project(replacement):
                memory.complete_review(correction_record(result, original, replacement, journal),
                                       document['text'], candidates=[])
    memory.export_markdown()
