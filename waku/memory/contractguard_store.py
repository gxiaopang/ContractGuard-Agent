"""Additive domain memory index with revisioned, transactional lifecycle decisions."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path

from waku.memory.contractguard_context import (
    APPLICABILITY_FIELDS,
    applicability,
    exclusion,
    validate_applicability,
    validate_context,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS contractguard_memory (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    clause_type TEXT NOT NULL,
    payload TEXT NOT NULL,
    applicability TEXT NOT NULL,
    source_id TEXT,
    state TEXT NOT NULL CHECK(state IN ('candidate','approved','superseded','revoked')),
    revision INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS contractguard_memory_decisions (
    id TEXT PRIMARY KEY,
    memory_id TEXT NOT NULL,
    expected_revision INTEGER NOT NULL,
    action TEXT NOT NULL,
    reviewer TEXT NOT NULL,
    reason TEXT NOT NULL,
    replacement_id TEXT,
    timestamp TEXT NOT NULL
);
"""


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def identity(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class DomainMemory:
    def __init__(self, conn, scope):
        self.conn, self.scope = conn, scope
        self.home = Path(conn.execute('PRAGMA database_list').fetchone()[2]).resolve().parent
        conn.executescript(SCHEMA)

    def put(self, kind, clause_type, payload, constraints, *, source_id=None,
            approved_by=None):
        from waku.memory.contractguard import CLAUSE_NAMES, validate_review

        if kind not in ('definition', 'policy', 'experience', 'review', 'correction') or clause_type not in CLAUSE_NAMES:
            raise ValueError('unknown domain memory kind or clause')
        validate_applicability(constraints)
        if constraints['scope'] != self.scope:
            raise ValueError('domain memory cannot leave its configured scope')
        if kind in ('review', 'correction'):
            validate_review(payload)
            if approved_by is not None:
                self._check_provenance({'kind': kind, 'payload': payload})
        elif not isinstance(payload, str) or not payload.strip() or len(payload) > 50000:
            raise ValueError('semantic memory must contain bounded guidance')
        value = {'kind': kind, 'clause_type': clause_type, 'payload': payload,
                 'applicability': constraints, 'source_id': source_id}
        record_id = identity(value)
        stamp = datetime.now(UTC).isoformat()
        with self.conn:
            self.conn.execute(
                'INSERT OR IGNORE INTO contractguard_memory '
                '(id,kind,clause_type,payload,applicability,source_id,state,created_at) VALUES (?,?,?,?,?,?,?,?)',
                (record_id, kind, clause_type, canonical(payload), canonical(constraints),
                 source_id, 'candidate', stamp))
        if approved_by is not None and self.get(record_id)['state'] == 'candidate':
            self.decide(record_id, 1, 'approve', reviewer=approved_by,
                        reason='Approved provenance supplied by the trusted writer.')
        return record_id

    def get(self, record_id):
        row = self.conn.execute('SELECT * FROM contractguard_memory WHERE id=?', (record_id,)).fetchone()
        if row is None:
            raise ValueError('domain memory record does not exist')
        return self._decode(row)

    @staticmethod
    def _decode(row):
        record = dict(row)
        record['payload'] = json.loads(record['payload'])
        record['applicability'] = json.loads(record['applicability'])
        validate_applicability(record['applicability'])
        if record['state'] not in ('candidate', 'approved', 'superseded', 'revoked'):
            raise ValueError('unknown stored memory state')
        expected = identity({k: record[k] for k in ('kind', 'clause_type', 'payload', 'applicability', 'source_id')})
        if record['id'] != expected:
            raise ValueError('stored memory identity differs from its immutable content')
        return record

    def list(self):
        return [self._decode(row) for row in self.conn.execute(
            'SELECT * FROM contractguard_memory ORDER BY created_at,id')]

    def decide(self, record_id, revision, action, *, reviewer, reason, replacement_id=None):
        """Persist a decision atomically; identical retries return current stored state."""
        if type(revision) is not int or revision < 1 or action not in ('approve', 'revoke', 'supersede'):
            raise ValueError('invalid memory decision')
        for value in (reviewer, reason):
            if not isinstance(value, str) or not value.strip() or len(value) > 4000:
                raise ValueError('memory decision requires bounded reviewer and reason')
        if (action == 'supersede') != (replacement_id is not None) or replacement_id == record_id:
            raise ValueError('supersession requires a distinct replacement')
        decision = {'memory_id': record_id, 'expected_revision': revision, 'action': action,
                    'reviewer': reviewer, 'reason': reason, 'replacement_id': replacement_id}
        decision_id = identity(decision)
        self.conn.execute('BEGIN IMMEDIATE')
        try:
            record = self.get(record_id)
            if record['applicability']['scope'] != self.scope:
                raise ValueError('memory decision crosses its configured scope')
            if self.conn.execute('SELECT 1 FROM contractguard_memory_decisions WHERE id=?', (decision_id,)).fetchone():
                self.conn.commit()
                return self.get(record_id)
            expected_state = 'candidate' if action == 'approve' else 'approved'
            if record['revision'] != revision or record['state'] != expected_state:
                raise ValueError('memory revision or state changed; reload before deciding')
            if action == 'approve':
                self._check_provenance(record)
            if replacement_id is not None:
                replacement = self.get(replacement_id)
                if (replacement['state'] != 'candidate' or replacement['kind'] != record['kind']
                        or replacement['clause_type'] != record['clause_type']
                        or replacement['applicability']['scope'] != self.scope):
                    raise ValueError('replacement must be an unapproved compatible candidate')
                self._check_provenance(replacement)
                self.conn.execute("UPDATE contractguard_memory SET state='approved',revision=revision+1 WHERE id=?",
                                  (replacement_id,))
            state = {'approve': 'approved', 'revoke': 'revoked', 'supersede': 'superseded'}[action]
            self.conn.execute('UPDATE contractguard_memory SET state=?,revision=revision+1 WHERE id=?', (state, record_id))
            self.conn.execute('INSERT INTO contractguard_memory_decisions VALUES (?,?,?,?,?,?,?,?)',
                              (decision_id, record_id, revision, action, reviewer, reason,
                               replacement_id, datetime.now(UTC).isoformat()))
            self.conn.commit()
        except BaseException:
            self.conn.rollback()
            raise
        return self.get(record_id)

    def _check_provenance(self, record):
        if record['kind'] in ('review', 'correction'):
            from waku.tools.human_review import load_review, validate_approved_record

            payload = record['payload']
            if payload.get('context', {}).get('scope') != self.scope:
                raise ValueError('approved review memory requires verified scope')
            document, _, _ = load_review(self.home, payload['review_id'])
            validate_approved_record(self.home, payload, document['text'])

    def seed(self, clauses, context):
        general = applicability(context)
        for key in APPLICABILITY_FIELDS:
            general[key] = None
        for clause in clauses:
            self.put('definition', clause.name, clause.text(), general, approved_by='bundled-definition-v1')

    def select(self, context, *, clause_type=None, query='', current_source=None,
               semantic=True, episodic=True, corrections=True, top_k=4, history_limit=3,
               budget=12000, latest=False):
        context = validate_context(context)
        if context['scope'] != self.scope:
            raise ValueError('retrieval context crosses its configured scope')
        if type(budget) is not int or budget < 0:
            raise ValueError('memory budget must be a nonnegative character count')
        words = set(re.findall(r'\w+', query.casefold()))
        eligible, decisions = [], []
        for record in self.list():
            reason = exclusion(record, context, current_source)
            if reason is None and clause_type and record['clause_type'] != clause_type:
                reason = 'clause_mismatch'
            is_semantic = record['kind'] in ('definition', 'policy', 'experience')
            if reason is None and ((is_semantic and not semantic) or (not is_semantic and not episodic)
                                   or (record['kind'] == 'correction' and not corrections)):
                reason = 'store_not_selected'
            decisions.append({'id': record['id'], 'kind': record['kind'], 'reason': reason or 'eligible'})
            if reason is None:
                relevance = len(words.intersection(re.findall(r'\w+', canonical(record['payload']).casefold())))
                matched = sum(record['applicability'][k] is not None for k in APPLICABILITY_FIELDS)
                eligible.append((record, relevance, matched))
        eligible.sort(key=lambda row: (row[0]['created_at'], row[0]['id']) if latest else
                      (row[1], row[2], row[0]['created_at'], row[0]['id']), reverse=True)
        policy_groups = {}
        for record, _, _ in eligible:
            if record['kind'] == 'policy':
                key = record['clause_type']
                policy_groups.setdefault(key, []).append(record['id'])
        conflicts = {rid for ids in policy_groups.values() if len(ids) > 1 for rid in ids}
        result, selected, spent = {'semantic': [], 'episodic': []}, [], 0
        if conflicts:
            notice = 'Applicable policies conflict. Request an explicit policy revision; do not resolve this conflict by recency.'
            if len(canonical(notice)) <= budget and top_k > 0:
                result['semantic'].append(notice)
                spent += len(canonical(notice))
        for record, _, _ in eligible:
            store = 'semantic' if record['kind'] in ('definition', 'policy', 'experience') else 'episodic'
            content = (f"[{record['id']} {record['kind']}] {record['payload']}" if store == 'semantic'
                       else record['payload'])
            size = len(canonical(content))
            reason = 'policy_conflict' if record['id'] in conflicts else 'limit' if len(result[store]) >= (top_k if store == 'semantic' else history_limit) else None
            if reason is None and spent + size > budget:
                reason = 'context_budget'
            for decision in decisions:
                if decision['id'] == record['id']:
                    decision['reason'] = reason or 'selected'
            if reason is None:
                result[store].append(content)
                selected.append(record['id'])
                spent += size
        return result, {'selected_ids': selected, 'decisions': decisions, 'characters': spent,
                        'budget': budget, 'policy_conflicts': sorted(conflicts)}
