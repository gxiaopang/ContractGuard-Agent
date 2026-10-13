"""Offline human decisions, evidence correction, crash recovery and tenant boundaries."""

from __future__ import annotations

import copy
import io
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from evals.deterministic.test_contractguard_foundation import gate
from evals.deterministic.test_contractguard_tools import (
    CLAUSE,
    QUOTE,
    SOURCE,
    comparison,
    proposal,
    risk,
)
from evals.deterministic.test_contractguard_workflow import app as workflow_app
from evals.deterministic.test_contractguard_workflow import (
    episodes,
    run,
    set_model,
)
from waku.db import connect
from waku.memory.contractguard import store_review
from waku.memory.episodic.store import SqliteEpisodeStore
from waku.ops import contractguard, dashboard
from waku.tools.contract_review import encode, review_contract, review_episode
from waku.tools.human_review import (
    ReviewConflict,
    align_quotes,
    approved_episode,
    digest,
    submit_review,
)

app = workflow_app


def pending(app, *, absent=False):
    set_model(app, gate(), proposal(found=False, spans=[]) if absent else proposal(), *([] if absent else [risk()]))
    return run(app)


def submission(result, decision='confirm'):
    saved = json.loads(Path(result['artifacts']['result']).read_text())
    value = {'review_id': result['review_id'], 'revision': digest(saved), 'decision': decision,
             'reviewer': 'Human reviewer', 'note': 'Checked against source.'}
    if decision == 'correct':
        value['findings'] = [{'clause_type': f['clause_type'], 'found': f['status'] == 'found',
                              'evidence': [{'text': s['text'], 'occurrence': None}
                                           for s in f['extraction']['spans']],
                              'risk_level': f['risk']['risk_level'] if f['risk'] else None,
                              'reasoning': f['risk']['reasoning'] if f['risk'] else f['extraction']['rationale'],
                              'recommendation': f['risk']['recommendation'] if f['risk'] else None}
                             for f in saved['findings']]
    return value


def test_confirmation_is_model_free_preserves_originals_and_records_provenance(app):
    result = pending(app)
    before = {k: Path(v).read_bytes() for k, v in result['artifacts'].items()}
    payload = submission(result)
    app.memory.client = Mock(side_effect=AssertionError('no model'))
    journal = submit_review(replace(app.settings, api_key=''), payload)
    assert journal['status'] == 'completed' and len(episodes(app)) == 1
    record = episodes(app)[0]
    assert record['schema'] == 'contractguard_review_v2' and record['origin'] == 'review'
    assert record['approved_at'] == journal['timestamp'] == record['timestamp']
    assert record['decision_id'] == journal['decision_id'] and record['review_id'] == result['review_id']
    assert record['evidence'] == result['findings'][0]['extraction']['spans']
    assert {k: Path(v).read_bytes() for k, v in result['artifacts'].items()} == before
    assert len(app.memory.facts.list(limit=-1)) == 10
    assert 'contractguard_review_v2' in (app.settings.home / 'MEMORY.md').read_text()
    assert app.client.messages.create.call_count == 3
    assert submit_review(app.settings, payload) == journal and len(episodes(app)) == 1
    assert run(app)['cached'] and app.client.messages.create.call_count == 3
    data = contractguard.collect(app.settings.home, result['review_id'])
    assert data['reviews'][0]['status'] == 'completed'
    assert data['human_review']['source_text'] == SOURCE
    assert data['human_review']['approved_report'] and data['progress']['pending'] == 0


def test_corrected_risk_replaces_only_accepted_findings_and_drops_comparison(app):
    set_model(app, gate(semantic=True, query=CLAUSE, clause_type=CLAUSE), proposal(), risk(), comparison())
    result = run(app)
    payload = submission(result, 'correct')
    payload['findings'][0].update(risk_level='HIGH', reasoning='Human assessed wider exposure.',
                                  recommendation='Renegotiate this term.')
    journal = submit_review(app.settings, payload)
    assert episodes(app)[0]['risk_level'] == 'HIGH'
    assert episodes(app)[0]['finding_summary'] == 'Human assessed wider exposure.'
    assert journal['accepted_findings'][0]['comparison'] is None
    original = json.loads(Path(result['artifacts']['result']).read_text())
    assert original['findings'][0]['risk']['risk_level'] == 'LOW'
    assert original['findings'][0]['comparison'] == comparison()
    data = contractguard.collect(app.settings.home)
    assert data['risk_matrix'][CLAUSE]['HIGH'] == 1 and data['risk_matrix'][CLAUSE]['LOW'] == 0
    assert 'HIGH' in data['human_review']['approved_report']


@pytest.mark.parametrize('found', [True, False])
def test_human_can_correct_clause_presence_with_grounded_evidence(app, found):
    result = pending(app, absent=found)
    payload = submission(result, 'correct')
    payload['findings'][0].update(found=found, evidence=[{'text': QUOTE, 'occurrence': None}] if found else [],
                                  risk_level='MEDIUM' if found else None,
                                  reasoning='Reviewed exact wording.', recommendation='Check coverage.' if found else None)
    assert submit_review(app.settings, payload)['status'] == 'completed'
    assert len(episodes(app)) == int(found)
    if found:
        assert episodes(app)[0]['evidence'][0]['text'] == QUOTE


@pytest.mark.parametrize('decision', ['confirm', 'reject'])
def test_missing_and_rejected_reports_do_not_write_episodes_or_learned_facts(app, decision):
    result = pending(app, absent=decision == 'confirm')
    payload = submission(result, decision)
    journal = submit_review(app.settings, payload)
    assert journal['status'] == ('completed' if decision == 'confirm' else 'rejected')
    assert episodes(app) == [] and len(app.memory.facts.list(limit=-1)) == 10
    assert submit_review(app.settings, payload) == journal
    assert Path(result['artifacts']['report']).exists()


@pytest.mark.parametrize('mutation', [
    lambda p: p.update(origin='benchmark_prediction'), lambda p: p.update(revision='f' * 64),
    lambda p: p.update(reviewer=''), lambda p: p.update(note='x' * 4001),
    lambda p: p.update(decision='approve'), lambda p: p['findings'].append(p['findings'][0]),
    lambda p: p['findings'][0].update(clause_type='Indemnification'),
    lambda p: p['findings'][0].update(risk_level='CRITICAL'),
    lambda p: p['findings'][0].update(reasoning=''),
    lambda p: p['findings'][0]['evidence'][0].update(text='invented quote'),
    lambda p: p['findings'][0]['evidence'][0].update(occurrence=True),
    lambda p: p['findings'][0]['evidence'][0].update(start=0),
])
def test_invalid_or_stale_corrections_write_no_decision_or_memory(app, mutation):
    result = pending(app)
    payload = submission(result, 'correct')
    mutation(payload)
    with pytest.raises(ValueError):
        submit_review(app.settings, payload)
    assert episodes(app) == []
    assert not (Path(result['artifacts']['result']).parent / 'human_review.json').exists()


def test_partial_and_benchmark_reports_cannot_receive_human_decisions(app):
    set_model(app, gate(), {})
    partial = run(app)
    with pytest.raises(ReviewConflict):
        submit_review(app.settings, submission(partial))
    set_model(app, gate(), proposal(), risk())
    benchmark = run(app, origin='benchmark_prediction')
    with pytest.raises(ValueError, match='ordinary'):
        submit_review(app.settings, submission(benchmark))
    assert len(episodes(app)) == 1 and episodes(app)[0]['origin'] == 'benchmark_prediction'
    assert contractguard.collect(app.settings.home, benchmark['review_id'])['human_review'] is None


def test_quote_alignment_requires_explicit_occurrence_for_repeated_text():
    source = 'First quote; second quote.'
    with pytest.raises(ValueError, match='occurrence'):
        align_quotes([{'text': 'quote', 'occurrence': None}], source)
    spans = align_quotes([{'text': 'quote', 'occurrence': 1}], source)
    assert spans == [{'text': 'quote', 'start': 20, 'end': 25}]


def test_memory_facade_rejects_unapproved_and_forged_records(app):
    result = pending(app)
    saved = json.loads(Path(result['artifacts']['result']).read_text())
    record = review_episode(saved, saved['findings'][0])
    with pytest.raises(ValueError):
        app.memory.complete_review(record, SOURCE, candidates=[])
    with pytest.raises(ValueError):
        app.memory.complete_review({**record, 'approved': True}, SOURCE, candidates=[])
    journal = submit_review(app.settings, submission(result, 'reject'))
    forged = approved_episode(saved, saved['findings'][0], journal)
    with pytest.raises(ValueError, match='approval'):
        app.memory.complete_review(forged, SOURCE, candidates=[])
    assert episodes(app) == []


def test_memory_facade_rechecks_accepted_record_and_prevents_pattern_calls(app):
    result = pending(app)
    submit_review(app.settings, submission(result))
    record = episodes(app)[0]
    with pytest.raises(ValueError, match='differs'):
        app.memory.complete_review({**record, 'risk_level': 'HIGH'}, SOURCE, candidates=[])
    with pytest.raises(ValueError, match='pattern'):
        app.memory.complete_review(record, SOURCE)
    assert len(episodes(app)) == 1 and app.client.messages.create.call_count == 3


def test_concurrent_identical_and_conflicting_decisions_are_serialized(app):
    result = pending(app)
    payload = submission(result)
    with ThreadPoolExecutor(max_workers=4) as pool:
        journals = list(pool.map(lambda _: submit_review(app.settings, copy.deepcopy(payload)), range(4)))
    assert all(j == journals[0] for j in journals) and len(episodes(app)) == 1
    other = {**payload, 'decision': 'reject'}
    with pytest.raises(ReviewConflict):
        submit_review(app.settings, other)
    assert len(episodes(app)) == 1


def test_database_idempotency_also_holds_without_the_http_decision_lock(app):
    result = pending(app)
    submit_review(app.settings, submission(result))
    record = episodes(app)[0]
    def write(_):
        conn = connect(app.settings.home)
        try:
            return store_review(SqliteEpisodeStore(conn), record, SOURCE)[1]
        finally:
            conn.close()
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(write, range(8))) == [False] * 8
    assert len(episodes(app)) == 1


def test_persistence_failure_retains_approval_and_resumes_only_missing_writes(app, monkeypatch):
    targets = [CLAUSE, 'Indemnification']
    set_model(app, gate(), proposal(), risk(), gate(), proposal(clause_type=targets[1]), risk(clause_type=targets[1]))
    result = review_contract(app.memory, SOURCE, 'Agreement', targets)
    original = app.memory.complete_review
    count = 0
    def interrupted(*args, **kwargs):
        nonlocal count
        count += 1
        if count == 2:
            raise OSError('disk failure')
        return original(*args, **kwargs)
    monkeypatch.setattr(app.memory, 'complete_review', interrupted)
    payload = submission(result)
    with pytest.raises(OSError):
        submit_review(app.settings, payload, memory=app.memory)
    projected = contractguard.collect(app.settings.home)['human_review']
    assert projected['status'] == 'approved' and len(episodes(app)) == 1
    monkeypatch.setattr(app.memory, 'complete_review', original)
    completed = submit_review(app.settings, payload, memory=app.memory)
    assert completed['status'] == 'completed' and len(episodes(app)) == 2
    assert completed['timestamp'] == projected['timestamp']
    assert app.client.messages.create.call_count == 6


def test_legacy_ordinary_reports_remain_readable_without_fabricated_approval(app):
    result = pending(app)
    path = Path(result['artifacts']['result'])
    value = json.loads(path.read_text())
    value.update(schema='contractguard_result_v1', status='completed')
    path.write_text(encode(value))
    store_review(app.memory.episodes, review_episode(value, value['findings'][0]), SOURCE)
    data = contractguard.collect(app.settings.home)
    assert data['reviews'][0]['review_kind'] == 'legacy automatic review'
    assert data['human_review'] is None and 'approved_at' not in episodes(app)[0]
    with pytest.raises(ValueError):
        submit_review(app.settings, submission(result))
    assert len(episodes(app)) == 1


def handler_for(payload, headers=None):
    handler = object.__new__(dashboard.Handler)
    handler.path = '/api/contractguard/review'
    body = json.dumps(payload).encode()
    handler.headers = {'Content-Length': str(len(body)), 'Content-Type': 'application/json',
                       'Host': 'localhost:7777', **(headers or {})}
    handler.rfile = io.BytesIO(body)
    handler.sent = []
    handler._send = lambda data, kind, **kwargs: handler.sent.append(json.loads(data))
    handler.send_error = lambda status, message: handler.sent.append({'http_status': status})
    return handler


def test_http_decision_uses_only_configured_home_and_no_agent(app, monkeypatch):
    result = pending(app)
    monkeypatch.setattr(dashboard, 'load_settings', lambda: app.settings)
    monkeypatch.setattr(dashboard, 'get_agent', Mock(side_effect=AssertionError('no provider')))
    handler = handler_for(submission(result))
    handler.do_POST()
    assert handler.sent[0]['ok'] and len(episodes(app)) == 1
    handler = handler_for(submission(result, 'reject'))
    handler.do_POST()
    assert handler.sent[0]['code'] == 'conflict'
    handler = handler_for({'review_id': '../outside'})
    handler.do_POST()
    assert handler.sent[0]['code'] == 'validation'


@pytest.mark.parametrize('headers,status', [
    ({'Content-Length': '1000001'}, 413), ({'Content-Type': 'text/plain'}, 415),
    ({'Origin': 'https://outside.example'}, 403), ({'Sec-Fetch-Site': 'cross-site'}, 403),
])
def test_http_rejects_cross_origin_or_unbounded_requests_before_reading(headers, status):
    handler = handler_for({}, headers)
    handler.rfile = Mock(side_effect=AssertionError('must not read'))
    handler.do_POST()
    assert handler.sent == [{'http_status': status}]


def test_http_reports_persistence_failure_and_keeps_retryable_decision(app, monkeypatch):
    result = pending(app)
    monkeypatch.setattr(dashboard, 'load_settings', lambda: app.settings)
    monkeypatch.setattr('waku.tools.human_review._persist', Mock(side_effect=OSError('private disk detail')))
    handler = handler_for(submission(result))
    handler.do_POST()
    assert handler.sent[0]['code'] == 'persistence' and 'private' not in handler.sent[0]['error']
    assert contractguard.collect(app.settings.home)['human_review']['status'] == 'approved'


def test_competing_confirm_and_reject_keep_one_decision(app):
    result = pending(app)
    def decide(decision):
        try:
            return submit_review(app.settings, submission(result, decision))
        except ReviewConflict:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(decide, ['confirm', 'reject']))
    winners = [value for value in outcomes if value is not None]
    assert len(winners) == 1
    assert len(episodes(app)) == (1 if winners[0]['status'] == 'completed' else 0)
    assert contractguard.collect(app.settings.home)['human_review']['status'] == winners[0]['status']


@pytest.mark.parametrize('target', ['document.json', 'human_review.json', '.decisions.db', 'state.db'])
def test_human_endpoint_never_reads_or_writes_external_symlink_targets(app, tmp_path, target):
    result = pending(app)
    outside = tmp_path / 'outside.txt'
    outside.write_text('EXTERNAL_CANARY')
    folder = Path(result['artifacts']['result']).parent
    path = (app.settings.home / 'state.db' if target == 'state.db' else
            app.settings.home / 'reviews' / target if target == '.decisions.db' else folder / target)
    path.unlink(missing_ok=True)
    path.symlink_to(outside)
    with pytest.raises(ValueError):
        submit_review(app.settings, submission(result))
    assert outside.read_text() == 'EXTERNAL_CANARY'
    assert episodes(app) == []


def test_saved_journal_tampering_is_detected_before_retry(app):
    result = pending(app)
    payload = submission(result)
    submit_review(app.settings, payload)
    path = Path(result['artifacts']['result']).parent / 'human_review.json'
    saved = json.loads(path.read_text())
    saved['accepted_findings'][0]['risk']['risk_level'] = 'HIGH'
    path.write_text(encode(saved))
    with pytest.raises(ValueError, match='inconsistent'):
        submit_review(app.settings, payload)
    assert len(episodes(app)) == 1 and episodes(app)[0]['risk_level'] == 'LOW'
    assert contractguard.collect(app.settings.home)['reviews'] == []


def test_disabled_mode_and_unknown_home_do_not_create_decisions(app, tmp_path):
    result = pending(app)
    with pytest.raises(ValueError, match='WAKU_CONTRACT_REVIEW'):
        submit_review(replace(app.settings, contract_review=False), submission(result))
    missing = tmp_path / 'nonexistent-home'
    with pytest.raises(ValueError):
        submit_review(replace(app.settings, home=missing), submission(result))
    assert not missing.exists() and episodes(app) == []


def test_corrected_missing_clause_reasoning_is_kept_in_approved_report(app):
    result = pending(app, absent=True)
    payload = submission(result, 'correct')
    payload['findings'][0]['reasoning'] = 'Human checked the complete contract and found no governing law.'
    journal = submit_review(app.settings, payload)
    assert journal['accepted_findings'][0]['extraction']['rationale'] == payload['findings'][0]['reasoning']
    assert payload['findings'][0]['reasoning'] in contractguard.collect(app.settings.home)['human_review']['approved_report']
    assert episodes(app) == []
    assert result['findings'][0]['extraction']['rationale'] != payload['findings'][0]['reasoning']
