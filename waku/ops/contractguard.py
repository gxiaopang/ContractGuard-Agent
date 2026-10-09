"""Read saved reviews and demonstration snapshots from one local home only."""

from __future__ import annotations

import math
import re
import sqlite3
from contextlib import closing
from pathlib import Path

from waku.memory import bundled_skill_dirs
from waku.memory.contractguard import CLAUSES, DOMAIN_SOURCES, decode_review
from waku.memory.procedural.loader import SkillLoader
from waku.tools.contract_parser import parse_contract
from waku.tools.contract_review import validate_result
from waku.tools.contract_schema import load_json

IDENTITY = re.compile(r'[0-9a-f]{64}')
MAX_REVIEWS = 200
MAX_BYTES = 8_000_000
GROWTH_KEYS = ('reviewed', 'semantic_entries', 'episodic_entries', 'procedural_skills',
               'retrieval_events', 'retrieved_items')


def read_text(home: Path, path: Path) -> str:
    target = path.resolve()
    if home not in target.parents or not target.is_file() or target.stat().st_size > MAX_BYTES:
        raise ValueError('artifact is not a bounded file inside the configured home')
    return target.read_text(encoding='utf-8')


def count(value) -> bool:
    return type(value) is int and value >= 0


def number(value) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def progress_data(value: dict) -> dict:
    if (value['schema'] != 'contractguard_demo_v1' or value['mode'] not in
            ('baseline', 'semantic-only', 'full-memory')
            or value['execution_kind'] not in ('offline_smoke', 'live')
            or value['status'] not in ('running', 'completed', 'partial', 'failed')
            or not isinstance(value['contracts'], list) or not 1 <= len(value['contracts']) <= MAX_REVIEWS):
        raise ValueError('invalid demonstration snapshot')
    rows, seen = [], set()
    for row in value['contracts']:
        identity = row['contract_id']
        if (not isinstance(identity, str) or IDENTITY.fullmatch(identity) is None or identity in seen
                or not isinstance(row['name'], str) or not 1 <= len(row['name']) <= 200
                or row['status'] not in ('pending', 'running', 'completed', 'partial', 'failed')
                or (row['review_id'] is not None and (not isinstance(row['review_id'], str)
                                                     or IDENTITY.fullmatch(row['review_id']) is None))):
            raise ValueError('invalid demonstration contract')
        seen.add(identity)
        rows.append({key: row[key] for key in ('contract_id', 'name', 'status', 'review_id')})
    current = value['current_contract_id']
    if current is not None and current not in seen:
        raise ValueError('current contract is outside the sample')
    growth = value['memory_growth']
    if (not isinstance(growth, list) or len(growth) > len(rows) + 1 or any(
            not isinstance(row, dict) or set(row) != set(GROWTH_KEYS)
            or not all(count(row[key]) for key in GROWTH_KEYS)
            or row['reviewed'] != i for i, row in enumerate(growth))):
        raise ValueError('invalid memory snapshots')
    return {'execution_kind': value['execution_kind'], 'mode': value['mode'], 'status': value['status'],
            'contracts': rows, 'current_contract_id': current, 'memory_growth': growth}


def evaluation_data(value: dict, reviewed: int) -> dict:
    if value['schema'] != 'contractguard_metrics_v1' or value['contracts'] != reviewed:
        raise ValueError('evaluation does not cover the completed snapshot')
    scores = {section: {key: value[section][key] for key in ('precision', 'recall', 'f1')}
              for section in ('micro', 'macro')}
    if any(not number(v) or v > 1 for row in scores.values() for v in row.values()):
        raise ValueError('invalid extraction score')
    telemetry = value['telemetry']
    usage = {key: telemetry[key] for key in ('llm_calls', 'input_tokens', 'output_tokens',
                                            'retrieval_count', 'memory_items_retrieved')}
    if not all(count(v) for v in usage.values()) or not number(telemetry['mean_latency_seconds']):
        raise ValueError('invalid usage snapshot')
    cost = telemetry['estimated_cost_usd']
    if cost is not None and not number(cost):
        raise ValueError('invalid cost estimate')
    targets = list(value['per_clause'])
    if not targets or any(t not in {c.name for c in CLAUSES} for t in targets):
        raise ValueError('invalid scored targets')
    matcher = {key: value['matcher'][key] for key in ('version', 'rule', 'threshold')}
    if (matcher['rule'] not in ('character-iou', 'token-iou', 'exact', 'normalized')
            or not isinstance(matcher['version'], str) or len(matcher['version']) > 100
            or not number(matcher['threshold']) or matcher['threshold'] > 1
            or not all(count(value[key]) for key in ('extraction_failed_pairs', 'risk_failed_pairs'))):
        raise ValueError('invalid matching metadata')
    return {'contracts': reviewed, **scores, 'scored_targets': targets, 'matcher': matcher,
            'telemetry': {**usage, 'mean_latency_seconds': telemetry['mean_latency_seconds'],
                          'estimated_cost_usd': cost},
            'extraction_failed_pairs': value['extraction_failed_pairs'],
            'risk_failed_pairs': value['risk_failed_pairs']}


def memory_counts(home: Path) -> dict:
    skills = {s.name for s in SkillLoader(bundled_skill_dirs()).skills}
    result = {'semantic_entries': 0, 'episodic_entries': 0,
              'procedural_skills': sum(c.skill in skills for c in CLAUSES)}
    path = (home / 'state.db').resolve()
    if home not in path.parents or not path.is_file():
        return result
    # URI mode=ro cannot initialize a DB, seed facts or call remote stores.
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as conn:
        result['semantic_entries'] = conn.execute(
            'SELECT COUNT(*) FROM facts WHERE source IN (?, ?)', tuple(sorted(DOMAIN_SOURCES))).fetchone()[0]
        result['episodic_entries'] = sum(decode_review(row[0]) is not None
                                         for row in conn.execute('SELECT summary FROM episodes'))
    return result


def collect(home: Path, review_id: str | None = None) -> dict:
    """Return projections, never source documents, raw memory, keys or caller paths."""
    home = Path(home).resolve()
    if review_id is not None and (not isinstance(review_id, str) or IDENTITY.fullmatch(review_id) is None):
        return {'error': 'Select a saved review id.'}
    errors, reviews, findings_by_id = [], [], {}
    root = home / 'reviews'
    candidates = sorted(root.iterdir()) if root.is_dir() and home in root.resolve().parents else []
    folders = [p for p in candidates if IDENTITY.fullmatch(p.name) and p.is_dir()]
    truncated = len(folders) > MAX_REVIEWS
    for folder in folders[:MAX_REVIEWS]:
        try:
            document = load_json(read_text(home, folder / 'document.json'))
            result = load_json(read_text(home, folder / 'result.json'))
            request = result['request']
            if document != parse_contract(document['text'], request['contract_name']):
                raise ValueError('document does not preserve its source structure')
            artifacts = {key: str(folder / filename) for key, filename in
                         (('document', 'document.json'), ('result', 'result.json'), ('report', 'report.md'))}
            declared = result['artifacts']
            if (not isinstance(declared, dict) or set(declared) != set(artifacts)
                    or any(not isinstance(declared[key], str)
                           or Path(declared[key]).resolve() != Path(path).resolve()
                           for key, path in artifacts.items())):
                raise ValueError('review artifact paths differ from its directory')
            validate_result(result, document, request, folder.name, declared)
            if read_text(home, folder / 'report.md') != result['report']:
                raise ValueError('report differs from validated findings')
            reviews.append({'review_id': folder.name, 'contract_id': result['document_id'],
                            'name': request['contract_name'], 'status': result['status'],
                            'timestamp': result['timestamp'], 'origin': request['origin']})
            findings_by_id[folder.name] = result
        except (OSError, ValueError, TypeError, KeyError, OverflowError):
            errors.append('A saved review is incomplete or invalid and was omitted.')
    reviews.sort(key=lambda r: (r['timestamp'], r['review_id']), reverse=True)
    state = None
    snapshot = home / 'contractguard' / 'progress.json'
    if snapshot.exists():
        try:
            state = progress_data(load_json(read_text(home, snapshot)))
        except (OSError, ValueError, TypeError, KeyError):
            errors.append('The demonstration progress snapshot is invalid.')
    by_id = {r['review_id']: r for r in reviews}
    if state is not None:
        for row in state['contracts']:
            if row['status'] in ('completed', 'partial'):
                saved = by_id.get(row['review_id'])
                if saved is None or saved['contract_id'] != row['contract_id'] or saved['status'] != row['status']:
                    row['status'] = 'invalid'
                    errors.append('A finished contract has no matching validated review.')
        rows = state['contracts']
        reviewed = sum(r['status'] in ('completed', 'partial') for r in rows)
        progress = {'total': len(rows), 'reviewed': reviewed,
                    'pending': sum(r['status'] in ('pending', 'running') for r in rows),
                    'failed': sum(r['status'] in ('partial', 'invalid', 'failed') for r in rows),
                    'current': next((r['name'] for r in rows if r['contract_id'] == state['current_contract_id']), None),
                    'status': state['status'], 'contracts': rows}
    else:
        reviewed = sum(r['status'] in ('completed', 'partial') for r in reviews)
        progress = {'total': len(reviews), 'reviewed': reviewed, 'pending': None, 'current': None,
                    'failed': sum(r['status'] == 'partial' for r in reviews),
                    'status': 'saved-reviews', 'contracts': reviews}
    matrix = {c.name: dict.fromkeys(('HIGH', 'MEDIUM', 'LOW', 'NOT_FOUND', 'ERROR'), 0) for c in CLAUSES}
    for result in findings_by_id.values():
        for finding in result['findings']:
            label = ('ERROR' if finding['status'] == 'error' else 'NOT_FOUND'
                     if finding['status'] == 'not_found' else finding['risk']['risk_level'])
            matrix[finding['clause_type']][label] += 1
    evaluation = None
    if state is not None and reviewed:
        try:
            evaluation = evaluation_data(load_json(read_text(home, home / 'contractguard' / 'metrics.json')), reviewed)
        except (OSError, ValueError, TypeError, KeyError):
            errors.append('The extraction metrics are unavailable or updating.')
    try:
        counts = memory_counts(home)
    except (sqlite3.Error, OSError, ValueError):
        counts = None
        errors.append('Local memory counts are unavailable.')
    selected = review_id or (reviews[0]['review_id'] if reviews else None)
    chosen = findings_by_id.get(selected)
    return {'schema': 'contractguard_dashboard_v1', 'reviews': reviews, 'progress': progress,
            'risk_matrix': matrix, 'memory_growth': state['memory_growth'] if state else [],
            'memory_counts': counts, 'evaluation': evaluation, 'execution_kind': state['execution_kind'] if state else None,
            'mode': state['mode'] if state else None, 'selected_review_id': selected,
            'report': chosen['report'] if chosen else None,
            'errors': list(dict.fromkeys(errors)), 'truncated': truncated,
            'selection_missing': review_id is not None and chosen is None}
