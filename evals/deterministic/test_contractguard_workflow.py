"""Scripted reviews through the real registry, loop, SQLite memory and artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from evals.deterministic.test_contractguard_foundation import client, gate, review
from evals.deterministic.test_contractguard_tools import (
    CLAUSE,
    QUOTE,
    SOURCE,
    comparison,
    proposal,
    risk,
)
from evals.helpers import make_waku, response, text_block, tool_block
from waku.memory.contractguard import CLAUSES, decode_review, review_history
from waku.tools.contract_review import make_tool, review_contract, write_artifact


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.delenv('WAKU_SLOT_GATE', raising=False)
    agent = make_waku(tmp_path / 'home', client=client(), contract_review=True,
                      provider='anthropic', api_key='offline', model='offline-main', small_model='offline-small',
                      semantic_store='sqlite', episodic_store='sqlite', experimental=False,
                      gh_tool=False, otel_endpoint='', consolidate_every=1000,
                      unavailable_tools=())
    yield agent
    agent.close()
    agent.conn.close()


def set_model(app, *values):
    app.client = app.memory.client = client(*values)
    return app.client


def episodes(app):
    return [decode_review(r['summary']) for r in app.memory.episodes.list(limit=-1)]


def run(app, **kwargs):
    return review_contract(app.memory, SOURCE, 'Agreement', [CLAUSE], **kwargs)


def test_tool_is_opt_in_with_closed_schema_and_no_model_controlled_provenance(app, tmp_path):
    tool = app.tools.get('review_contract')
    assert tool is not None and tool.wants_notify
    schema = tool.to_api()['input_schema']
    assert schema['required'] == ['text'] and schema['additionalProperties'] is False
    assert set(schema['properties']) == {'text', 'contract_name', 'clause_types', 'compare_guidance'}
    assert len(schema['properties']['clause_types']['items']['enum']) == 10
    ordinary = make_waku(tmp_path / 'ordinary', client=client(), contract_review=False,
                         provider='anthropic', api_key='offline', experimental=False, gh_tool=False)
    try:
        assert ordinary.tools.get('review_contract') is None
        with pytest.raises(ValueError):
            review_contract(ordinary.memory, SOURCE)
    finally:
        ordinary.close()
        ordinary.conn.close()
    with pytest.raises(ValueError):
        make_tool(app.settings, None)
    output = app.tools.execute('review_contract', {'text': SOURCE, 'origin': 'benchmark_prediction'})
    assert output.startswith('Error running review_contract:')


@pytest.mark.parametrize('kwargs', [{'text': ''}, {'text': ' '}, {'text': 'x' * 200001},
                                    {'text': 1}, {'contract_name': ''}, {'contract_name': 'x' * 201},
                                    {'clause_types': []}, {'clause_types': [CLAUSE, CLAUSE]},
                                    {'clause_types': ['Law']}, {'clause_types': CLAUSE},
                                    {'compare_guidance': 1}, {'origin': 'gold'}])
def test_invalid_requests_fail_before_model_or_disk_work(app, kwargs):
    with pytest.raises(ValueError):
        review_contract(app.memory, **{'text': SOURCE, 'clause_types': [CLAUSE], **kwargs})
    assert app.client.messages.create.call_count == 0
    assert not (app.settings.home / 'reviews').exists()


def test_review_writes_grounded_artifacts_episode_and_reuses_cache(app):
    set_model(app, gate(), proposal(), risk(), {'patterns': []})
    events = []
    result = run(app, notify=lambda kind, event: events.append((kind, event)))
    assert result['status'] == 'completed' and result['cached'] is False
    assert result['findings'][0]['extraction']['spans'][0]['text'] == QUOTE
    assert result['findings'][0]['risk']['risk_level'] == 'LOW'
    for path in result['artifacts'].values():
        assert Path(path).is_file()
    assert json.loads(Path(result['artifacts']['document']).read_text())['text'] == SOURCE
    saved = json.loads(Path(result['artifacts']['result']).read_text())
    assert saved['findings'] == result['findings'] and 'cached' not in saved
    assert Path(result['artifacts']['report']).read_text() == result['report']
    record = episodes(app)[0]
    assert record['contract_id'] == result['document_id'] and record['evidence'] == result['findings'][0]['extraction']['spans']
    assert record['origin'] == 'review' and record['timestamp'] == result['timestamp']
    assert [event['kind'] for kind, event in events if kind == 'llm'] == [
        'gate', 'clause_extraction', 'risk_scoring', 'consolidation']
    cached = run(app)
    assert cached == {**result, 'cached': True}
    assert len(episodes(app)) == 1 and app.client.messages.create.call_count == 4


def test_registry_tool_runs_through_loop_and_serializes_results_back_to_model(app):
    replies = [gate(), None, gate(), proposal(), risk(), {'patterns': []}, None]
    scripted = client(*replies)
    scripted._script[1] = response([tool_block('review_contract', {
        'text': SOURCE, 'contract_name': 'Agreement', 'clause_types': [CLAUSE]}, 'review-1')], 'tool_use')
    scripted._script[-1] = response([text_block('The report is saved.')])
    app.client = app.memory.client = scripted
    result = app.respond('Review this agreement for Governing Law: ' + SOURCE)
    assert [call['tool'] for call in result.tool_calls] == ['review_contract']
    payload = json.loads(result.tool_calls[0]['output'])
    assert payload['status'] == 'completed' and Path(payload['artifacts']['report']).exists()
    messages = scripted.messages.create.call_args_list[-1].kwargs['messages']
    observed = [block for msg in messages if isinstance(msg['content'], list)
                for block in msg['content'] if isinstance(block, dict) and block.get('type') == 'tool_result']
    assert observed[-1]['tool_use_id'] == 'review-1'
    assert json.loads(observed[-1]['content'])['findings'] == payload['findings']
    assert len(episodes(app)) == 1
    usage = [json.loads(line) for line in (app.settings.home / 'usage.jsonl').read_text().splitlines()]
    assert {'clause_extraction', 'risk_scoring', 'consolidation'} <= {row['kind'] for row in usage}
    assert all(row['turn_id'] == usage[0]['turn_id'] for row in usage)


def test_retrieved_guidance_reaches_stages_and_drives_optional_comparison(app):
    set_model(app, gate(semantic=True, query=CLAUSE, clause_type=CLAUSE), proposal(), risk(), comparison(), {'patterns': []})
    result = run(app)
    assert result['findings'][0]['memory_used']['semantic']
    assert result['findings'][0]['comparison'] == comparison()
    calls = app.client.messages.create.call_args_list
    extract_payload = json.loads(calls[1].kwargs['messages'][0]['content'])
    assert extract_payload['source_text'] == SOURCE
    assert 'Identify the law selected to govern the agreement.' in extract_payload['procedure']
    assert extract_payload['memory'] == result['findings'][0]['memory_used']
    diff_payload = json.loads(calls[3].kwargs['messages'][0]['content'])
    assert diff_payload['standard_guidance'] == '\n'.join(extract_payload['memory']['semantic'])


def test_comparison_can_be_disabled_even_when_guidance_is_retrieved(app):
    set_model(app, gate(semantic=True, query=CLAUSE, clause_type=CLAUSE), proposal(), risk(), {'patterns': []})
    result = run(app, compare_guidance=False)
    assert result['findings'][0]['comparison'] is None
    assert 'Comparison skipped' not in result['report']
    assert app.client.messages.create.call_count == 4


def test_negative_review_is_complete_but_does_not_invent_a_risk_or_episode(app):
    set_model(app, gate(), proposal(found=False, spans=[]))
    result = run(app)
    assert result['status'] == 'completed'
    item = result['findings'][0]
    assert item['status'] == 'not_found' and item['risk'] is None
    assert episodes(app) == [] and app.client.messages.create.call_count == 2


@pytest.mark.parametrize('stage', ['retrieval', 'extraction', 'risk', 'comparison'])
def test_stage_failures_are_partial_and_never_teach_memory(app, stage):
    if stage == 'retrieval':
        app.memory.review_context = Mock(side_effect=RuntimeError('private provider configuration'))
        script = []
    elif stage == 'extraction':
        script = [gate(), proposal(spans=[{'text': 'invented clause', 'occurrence': 0}])]
    elif stage == 'risk':
        script = [gate(), proposal(), risk(evidence_indices=[99])]
    else:
        script = [gate(semantic=True, query=CLAUSE, clause_type=CLAUSE), proposal(), risk(), {'not': 'a comparison'}]
    set_model(app, *script)
    result = run(app)
    item = result['findings'][0]
    assert result['status'] == 'partial' and item['status'] == 'error'
    assert item['error']['stage'] == stage and 'private' not in item['error']['message']
    assert (item['extraction'] is not None) == (stage in ('risk', 'comparison'))
    assert (item['risk'] is not None) == (stage == 'comparison')
    assert episodes(app) == []
    assert len(app.memory.facts.list(limit=-1)) == 10
    assert 'Review status: PARTIAL.' in result['report']


def test_one_failed_clause_prevents_learning_from_other_positive_findings(app):
    targets = [CLAUSE, 'Indemnification']
    set_model(app, gate(), proposal(), risk(), gate(), {})
    result = review_contract(app.memory, SOURCE, clause_types=targets)
    assert [f['status'] for f in result['findings']] == ['found', 'error']
    assert result['status'] == 'partial' and episodes(app) == []


def test_partial_artifact_retries_model_stages_then_becomes_cached(app):
    set_model(app, gate(), {})
    first = run(app)
    assert first['status'] == 'partial'
    set_model(app, gate(), proposal(), risk(), {'patterns': []})
    second = run(app)
    assert second['review_id'] == first['review_id'] and second['status'] == 'completed'
    assert len(episodes(app)) == 1 and run(app)['cached'] is True


def test_ready_journal_retries_persistence_with_fixed_timestamp_without_duplicate_episode(app):
    set_model(app, gate(), proposal(), risk(), {'patterns': []}, {'patterns': []})
    original = app.memory.complete_review

    def fail_after_write(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError('disk unavailable')

    app.memory.complete_review = fail_after_write
    with pytest.raises(OSError):
        run(app)
    journal = next((app.settings.home / 'reviews').glob('*/result.json'))
    ready = json.loads(journal.read_text())
    assert ready['status'] == 'ready' and len(episodes(app)) == 1
    app.memory.complete_review = original
    result = run(app)
    assert result['status'] == 'completed' and result['timestamp'] == ready['timestamp']
    assert len(episodes(app)) == 1
    # Retry called only consolidation, never extraction or risk.
    assert app.client.messages.create.call_count == 5


@pytest.mark.parametrize('artifact', ['result', 'document', 'report'])
def test_cache_refuses_inconsistent_or_ungrounded_artifacts(app, artifact):
    set_model(app, gate(), proposal(), risk(), {'patterns': []})
    result = run(app)
    path = Path(result['artifacts'][artifact])
    if artifact == 'result':
        data = json.loads(path.read_text())
        data['findings'][0]['extraction']['spans'][0]['text'] = 'Fabricated evidence'
        path.write_text(json.dumps(data))
    else:
        path.write_text('{}' if artifact == 'document' else 'Invented report')
    with pytest.raises(ValueError):
        run(app)
    assert app.client.messages.create.call_count == 4 and len(episodes(app)) == 1


def test_benchmark_provenance_stores_prediction_but_never_promotes_facts(app):
    set_model(app, gate(), proposal(), risk())
    result = run(app, origin='benchmark_prediction')
    assert result['status'] == 'completed' and episodes(app)[0]['origin'] == 'benchmark_prediction'
    assert len(app.memory.facts.list(limit=-1)) == 10
    assert app.client.messages.create.call_count == 3


def test_history_excludes_current_document_before_limit_and_preserves_category(app):
    for i in range(5):
        app.memory.complete_review(review(contract_id='current', timestamp=f'2026-10-05T1{i}:00:00+00:00'),
                                   'Supplier accepts liability without monetary ceiling for any covered loss. '
                                   'Customer may rely on these protections.', candidates=[])
    app.memory.complete_review(review(contract_id='old', timestamp='2026-10-04T10:00:00+00:00'),
                               'Supplier accepts liability without monetary ceiling for any covered loss. '
                               'Customer may rely on these protections.', candidates=[])
    history = review_history(app.memory.episodes, clause_type='Uncapped Liability', exclude_contract_id='current')
    assert [r['contract_id'] for r in history] == ['old']
    set_model(app, gate(semantic=True, episodic=True, query='Uncapped Liability', clause_type='Uncapped Liability'))
    context = app.memory.review_context('Review Governing Law', clause_type=CLAUSE)
    assert context == {'semantic': [], 'episodic': []}


def test_workflow_excludes_its_document_from_historical_examples(app):
    set_model(app, gate(), proposal(), risk(), {'patterns': []})
    first = run(app)
    set_model(app, gate(episodic=True, query=CLAUSE, clause_type=CLAUSE), proposal(), risk(), {'patterns': []})
    changed_request = review_contract(app.memory, SOURCE, 'Renamed contract', [CLAUSE])
    assert changed_request['review_id'] != first['review_id']
    assert changed_request['findings'][0]['memory_used']['episodic'] == []


def test_all_ten_tasks_receive_their_procedure_without_conversational_skill_cap(app):
    script = []
    for clause in CLAUSES:
        script.extend([gate(), proposal(clause_type=clause.name, found=False, spans=[])])
    set_model(app, *script)
    result = review_contract(app.memory, SOURCE)
    assert result['status'] == 'completed' and len(result['findings']) == 10
    calls = app.client.messages.create.call_args_list
    goals = {
        'Termination for Convenience': 'Determine who can terminate without proving breach.',
        'Uncapped Liability': 'Determine whether a specified exposure lacks a monetary ceiling.',
        'Cap on Liability': 'Identify the maximum monetary exposure and its coverage.',
        'IP Ownership Assignment': 'Determine ownership of deliverables and retained intellectual property.',
        'Non-Compete': 'Identify restrictions on competing commercial activities.',
        'Change of Control': 'Identify rights and duties triggered by a control change.',
        'Governing Law': 'Identify the law selected to govern the agreement.',
        'Indemnification': 'Determine which claims and losses one party must cover for another.',
        'Confidentiality Obligations': 'Determine permitted uses and disclosures of protected information.',
        'Exclusivity': 'Determine which commercial dealings or rights are exclusive.',
    }
    for index, clause in enumerate(CLAUSES):
        payload = json.loads(calls[index * 2 + 1].kwargs['messages'][0]['content'])
        assert payload['clause_type'] == clause.name and payload['source_text'] == SOURCE
        assert goals[clause.name] in payload['procedure']
    assert episodes(app) == []


def test_consolidation_learns_only_source_backed_lexical_patterns(app):
    set_model(app, gate(), proposal(), risk(), {'patterns': [
        {'kind': 'variant', 'pattern': 'is governed by', 'evidence_index': 0},
        {'kind': 'variant', 'pattern': 'invented phrase', 'evidence_index': 0}]})
    run(app)
    learned = [row for row in app.memory.facts.list(limit=-1) if row['source'] == 'contractguard_consolidation']
    assert len(learned) == 1 and 'is governed by' in learned[0]['content']


@pytest.mark.parametrize('operation', ['fsync', 'replace'])
def test_failed_atomic_write_preserves_old_artifact_and_removes_temporary_file(tmp_path, monkeypatch, operation):
    path = tmp_path / 'result.json'
    path.write_text('previous valid result', encoding='utf-8')
    monkeypatch.setattr(f'waku.tools.contract_review.os.{operation}', Mock(side_effect=OSError('disk failure')))
    with pytest.raises(OSError):
        write_artifact(path, 'new result')
    assert path.read_text() == 'previous valid result'
    assert list(tmp_path.iterdir()) == [path]
