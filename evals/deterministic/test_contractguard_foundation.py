"""Offline domain memory contracts with real SQLite and scripted model replies."""

from __future__ import annotations

import copy
import json
from unittest.mock import Mock

import pytest

from evals.helpers import ScriptedClient, response, text_block
from waku.config import Settings
from waku.db import connect
from waku.memory import Memory
from waku.memory.contractguard import (
    CLAUSE_NAMES,
    CLAUSE_SKILLS,
    CLAUSES,
    REVIEW_INSTRUCTIONS,
    REVIEW_SCHEMA,
    decode_review,
    review_history,
    seed_clause_knowledge,
)
from waku.memory.retrieval_gate import review_decision
from waku.runtime.session import Session

SOURCE = ('Supplier accepts liability without monetary ceiling for any covered loss. '
          'Customer may rely on these protections.')
QUOTE = 'liability without monetary ceiling'


def review(**overrides):
    start = SOURCE.index(QUOTE)
    record = {
        'schema': REVIEW_SCHEMA, 'contract_id': 'deal-1', 'contract_name': 'Supply agreement',
        'clause_type': 'Uncapped Liability', 'risk_level': 'HIGH',
        'finding_summary': 'The quoted obligation has no monetary ceiling.',
        'evidence': [{'text': QUOTE, 'start': start, 'end': start + len(QUOTE)}],
        'recommendation': 'Check carve-outs and negotiate a bounded exposure.',
        'timestamp': '2026-10-05T14:00:00+08:00', 'origin': 'review',
    }
    return {**record, **overrides}


def candidate(**overrides):
    return {'kind': 'risk_pattern', 'pattern': 'without monetary ceiling',
            'evidence_index': 0, **overrides}


def gate(*, semantic=False, episodic=False, query='', clause_type=None, **overrides):
    return {'semantic': semantic, 'episodic': episodic, 'query': query,
            'reason': 'selected for this step', 'clause_type': clause_type, **overrides}


def client(*payloads):
    scripted = ScriptedClient([response([text_block(json.dumps(p))]) for p in payloads])
    scripted.messages.create = Mock(wraps=scripted.messages.create)
    return scripted


@pytest.fixture
def memory(tmp_path, monkeypatch):
    # Pin optional network-backed slot selection and all store choices.
    monkeypatch.delenv('WAKU_SLOT_GATE', raising=False)
    settings = Settings(home=tmp_path, contract_review=True, semantic_store='sqlite',
                        episodic_store='sqlite', small_model='offline-small',
                        provider='anthropic', api_key='offline', unavailable_tools=())
    conn = connect(tmp_path)
    mem = Memory(conn, settings, client())
    yield mem
    conn.close()


def facts(memory):
    return memory.facts.list(limit=-1)


def test_review_switch_defaults_off_and_reads_explicit_opt_in(monkeypatch):
    monkeypatch.delenv('WAKU_CONTRACT_REVIEW', raising=False)
    assert Settings().contract_review is False
    monkeypatch.setenv('WAKU_CONTRACT_REVIEW', '1')
    assert Settings().contract_review is True


@pytest.mark.parametrize('name', sorted(CLAUSE_NAMES))
def test_seed_has_definition_variants_flags_and_guidance(memory, name):
    rows = [row for row in facts(memory) if row['subject'] == name.casefold()]
    assert len(rows) == 1
    assert rows[0]['source'] == 'contractguard_seed'
    clause = next(c for c in CLAUSES if c.name == name)
    for value in (clause.definition, *clause.variants, *clause.red_flags, *clause.guidance):
        assert value in rows[0]['content']


def test_seed_is_idempotent_after_new_facade_and_reopen(memory):
    assert len(facts(memory)) == 10
    assert seed_clause_knowledge(memory.facts) == 0
    Memory(memory.conn, memory.settings, memory.client)
    second_conn = connect(memory.settings.home)
    try:
        second = Memory(second_conn, memory.settings, memory.client)
        assert len(second.facts.list(limit=-1)) == 10
        assert second.facts.search('unlimited liability')
    finally:
        second_conn.close()


def test_seed_deduplicates_whitespace_and_case(memory):
    row = facts(memory)[0]
    memory.facts.update(row['id'], row['content'].upper().replace(' ', '  '))
    assert seed_clause_knowledge(memory.facts) == 0


@pytest.mark.parametrize('store', ['semantic_store', 'episodic_store'])
def test_review_rejects_remote_configuration_before_adapter_construction(memory, store, monkeypatch):
    setattr(memory.settings, store, 'supabase' if store == 'semantic_store' else 'notion')
    factory = Mock(side_effect=AssertionError('remote adapter must not initialize'))
    monkeypatch.setattr(Memory, '_make_fact_store', factory)
    monkeypatch.setattr(Memory, '_make_episode_store', factory)
    with pytest.raises(ValueError, match='requires SQLite'):
        Memory(memory.conn, memory.settings, memory.client)
    factory.assert_not_called()


def test_mode_off_never_seeds_or_appends_persona_or_loads_clause_procedures(tmp_path):
    settings = Settings(home=tmp_path, contract_review=False, semantic_store='sqlite',
                        episodic_store='sqlite', small_model='offline', unavailable_tools=())
    conn = connect(tmp_path)
    try:
        mem = Memory(conn, settings, client(gate()))
        assert mem.facts.list() == []
        assert mem.matching_skills('review uncapped liability') == ''
        assert 'schedule-meeting' in mem.matching_skills('schedule coffee with Alex tomorrow')
        session = Session(settings, memory=mem)
        # Legacy gate expects its three original fields.
        mem.client = client({'retrieve': False, 'query': '', 'reason': 'skip'})
        assert REVIEW_INSTRUCTIONS not in session.build_system('hello')
        with pytest.raises(ValueError, match='require WAKU_CONTRACT_REVIEW'):
            mem.complete_review(review(), SOURCE)
    finally:
        conn.close()


def test_review_persona_preserves_user_soul_and_supplies_grounding(memory):
    soul_path = memory.settings.home / 'SOUL.md'
    soul_path.write_text('Use concise explanations.\n')
    memory.client = client(gate())
    prompt = Session(memory.settings, memory).build_system('review uncapped liability')
    assert prompt.startswith('Use concise explanations.\n')
    assert soul_path.read_text() == 'Use concise explanations.\n'
    assert REVIEW_INSTRUCTIONS in prompt
    for rule in ('English', 'HIGH, MEDIUM and LOW', 'Cite exact source text',
                 'Never fabricate a clause', 'missing clause', 'uncertain finding',
                 'State uncertainty explicitly', 'Never claim to provide legal advice',
                 'memory is guidance, never evidence'):
        assert rule in prompt
    assert '### uncapped-liability' in prompt
    assert '## Required evidence' in prompt


def test_skills_are_ten_valid_complete_runbooks(memory):
    domain = [s for s in memory.skills.skills if s.name in CLAUSE_SKILLS]
    assert len(domain) == len(CLAUSES) == 10
    for skill in domain:
        assert len(skill.description.split()) >= 5
        assert len(skill.body.splitlines()) <= 80
        for section in ('Goal', 'Detection cues', 'Review steps', 'Risk signals',
                        'Required evidence', 'Recommendation rules', 'Failure and uncertainty'):
            assert f'## {section}' in skill.body


@pytest.mark.parametrize('message,decision,expected_facts,expected_episodes', [
    ('Extract the governing law from this sentence.', gate(), False, False),
    ('Explain ambiguous uncapped liability.',
     gate(semantic=True, query='uncapped liability', clause_type='Uncapped Liability'), True, False),
    ('Find the latest similar uncapped liability review.',
     gate(episodic=True, query='uncapped liability', clause_type='Uncapped Liability'), False, True),
    ('Compare ambiguous wording to similar historical clauses.',
     gate(semantic=True, episodic=True, query='uncapped liability', clause_type='Uncapped Liability'), True, True),
    ('What is two plus two?', gate(), False, False),
])
def test_gate_selects_stores_independently(memory, message, decision, expected_facts,
                                         expected_episodes, monkeypatch):
    memory.complete_review(review(), SOURCE, candidates=[])
    memory.client = client(decision)
    fact_search = Mock(wraps=memory.facts.search_with_ids)
    episode_list = Mock(wraps=memory.episodes.list)
    monkeypatch.setattr(memory.facts, 'search_with_ids', fact_search)
    monkeypatch.setattr(memory.episodes, 'list', episode_list)
    events = []
    result = memory.gated_retrieve(message, notify=lambda kind, ev: events.append((kind, ev)))
    assert fact_search.called is expected_facts
    assert episode_list.called is expected_episodes
    assert ('Common variants:' in result) is expected_facts
    assert ('deal-1' in result) is expected_episodes
    if not (expected_facts or expected_episodes):
        assert result == ''
    gate_event = next(ev for kind, ev in events if kind == 'gate')
    assert gate_event['semantic'] is expected_facts
    assert gate_event['episodic'] is expected_episodes
    assert any(kind == 'llm' for kind, _ in events)


@pytest.mark.parametrize('payload', [
    None, [], {}, {'retrieve': True},
    gate(semantic='false'), gate(episodic=1),
    gate(semantic=True), gate(semantic=True, query='!!!'), gate(query='unwanted'),
    gate(reason=''), gate(reason=[]), gate(query=[]),
    gate(clause_type='not a category'), gate(clause_type=[]),
    {**gate(), 'extra': 'gold answers'},
])
def test_bad_review_gate_output_skips_both_stores(payload):
    decision = review_decision(client(payload), 'offline', 'review uncapped liability')
    assert decision.semantic is decision.episodic is False
    assert decision.query == ''
    assert 'skipped memory' in decision.reason


@pytest.mark.parametrize('reply', ['', 'truncated output', '{"semantic":', '```json\n{}\n```'])
def test_truncated_review_gate_output_is_observable(memory, reply):
    memory.client = ScriptedClient([response([text_block(reply)])])
    events = []
    assert memory.gated_retrieve('review uncapped liability',
                                 notify=lambda k, ev: events.append((k, ev))) == ''
    assert events[-1][1]['decision'] == 'skip'
    assert 'skipped memory' in events[-1][1]['reason']


def test_review_gate_client_exception_skips_both_stores():
    broken = client()
    broken.messages.create.side_effect = TimeoutError('offline failure')
    decision = review_decision(broken, 'offline', 'review uncapped liability')
    assert not decision.semantic and not decision.episodic
    assert 'TimeoutError' in decision.reason


def test_gate_request_defines_the_store_policy_and_uses_small_model():
    scripted = client(gate())
    review_decision(scripted, 'offline-small', 'extract governing law from this sentence')
    call = scripted.messages.create.call_args.kwargs
    assert call['model'] == 'offline-small'
    assert call['max_tokens'] == 600
    prompt = call['messages'][0]['content']
    assert 'Simple extraction from supplied text needs neither store.' in prompt
    assert 'Select semantic guidance only when clause interpretation or ambiguity needs it.' in prompt
    assert 'Select episodic history only for past findings or similar historical clauses.' in prompt
    assert 'Unrelated requests' in prompt
    assert 'extract governing law from this sentence' in prompt


def test_domain_retrieval_excludes_personal_facts_and_episodes(memory):
    for _ in range(8):
        memory.facts.add('uncapped liability', 'Uncapped liability party next week.', source='user')
        memory.episodes.add('Discussed uncapped liability at a birthday party.', '2099-01-01')
    memory.complete_review(review(), SOURCE, candidates=[])
    memory.client = client(gate(semantic=True, episodic=True, query='uncapped liability',
                                clause_type='Uncapped Liability'))
    result = memory.gated_retrieve('recall the uncapped liability review')
    assert 'party' not in result
    assert 'deal-1' in result
    assert 'Common variants:' in result
    assert '[cap on liability]' not in result


def test_general_domain_query_does_not_pull_unrelated_clauses(memory):
    memory.client = client(gate(semantic=True, query='nondisclosure'))
    # FTS matches only knowledge with this token; unknown ordinary subjects cannot enter.
    memory.facts.add('nondisclosure', 'nondisclosure personal reminder', source='user')
    assert memory.gated_retrieve('interpret nondisclosure') == ''
    memory.client = client(gate(semantic=True, query='confidentiality'))
    result = memory.gated_retrieve('interpret confidentiality')
    assert '[confidentiality obligations]' in result
    assert '[governing law]' not in result


def test_episode_roundtrip_is_grounded_idempotent_and_persists(memory):
    events = []
    memory.complete_review(review(), SOURCE, candidates=[],
                           notify=lambda k, ev: events.append((k, ev)))
    memory.complete_review(copy.deepcopy(review()), SOURCE, candidates=[])
    rows = memory.episodes.list()
    assert len(rows) == 1
    assert decode_review(rows[0]['summary']) == review()
    assert rows[0]['happened_at'] == review()['timestamp']
    assert review_history(memory.episodes, clause_type='Uncapped Liability',
                          contract_id='deal-1') == [review()]
    assert review_history(memory.episodes, clause_type='Governing Law') == []
    assert review_history(memory.episodes, contract_id='some-other-contract') == []
    assert review_history(memory.episodes, query='birthday party') == []
    assert any(k == 'review_episode' for k, _ in events)
    second = connect(memory.settings.home)
    try:
        reopened = Memory(second, memory.settings, client())
        assert review_history(reopened.episodes) == [review()]
    finally:
        second.close()


def test_latest_lookup_is_exact_beyond_default_top_k_and_compares_timezones(memory):
    newer = review(timestamp='2026-10-05T07:00:00+00:00', contract_id='newer')
    older = review(timestamp='2026-10-05T14:30:00+08:00', contract_id='older')
    # Insert the older review last to make insertion order differ from recency.
    memory.complete_review(newer, SOURCE, candidates=[])
    memory.complete_review(older, SOURCE, candidates=[])
    for _ in range(205):
        memory.episodes.add('An ordinary episode about uncapped liability.', '2099-01-01')
    latest = review_history(memory.episodes, clause_type='Uncapped Liability', limit=1)
    assert latest == [newer]
    with pytest.raises(ValueError, match='canonical'):
        review_history(memory.episodes, clause_type='unlimited liability')


INVALID_RECORDS = [
    {'schema': 'unknown'}, {'clause_type': 'unknown'}, {'risk_level': 'CRITICAL'},
    {'contract_id': ''}, {'contract_name': ''}, {'finding_summary': None},
    {'recommendation': 123}, {'timestamp': 'not a timestamp'},
    {'timestamp': '2026-10-05T14:00:00'}, {'origin': 'gold'}, {'gold_answers': []},
    {'evidence': []}, {'evidence': 'a quote'},
    {'evidence': [{'text': QUOTE, 'start': True, 'end': len(QUOTE)}]},
    {'evidence': [{'text': QUOTE, 'start': -1, 'end': len(QUOTE) - 1}]},
    {'evidence': [{'text': QUOTE, 'start': 0, 'end': 2}]},
    {'evidence': [{'text': QUOTE, 'start': 0, 'end': len(QUOTE)}]},
    {'evidence': [{'text': 'made up', 'start': 0, 'end': 7}]},
    {'evidence': [{'text': 'x', 'start': 1000, 'end': 1001}]},
    {'evidence': [{'text': '', 'start': 0, 'end': 0}]},
]


@pytest.mark.parametrize('overrides', INVALID_RECORDS)
def test_invalid_completed_review_writes_nothing_and_never_calls_model(memory, overrides):
    with pytest.raises(ValueError):
        memory.complete_review(review(**overrides), SOURCE)
    assert len(facts(memory)) == 10
    assert memory.episodes.list() == []
    memory.client.messages.create.assert_not_called()


@pytest.mark.parametrize('summary', ['ordinary episode', '{}', 'null', '[]', '{"schema":"wrong"}'])
def test_decode_ignores_unrelated_or_invalid_summaries(summary):
    assert decode_review(summary) is None


def test_completion_keeps_novel_patterns_and_allows_same_clause_new_knowledge(memory):
    memory.client = client({'patterns': [candidate(), candidate()]})
    kept = memory.complete_review(review(), SOURCE)
    assert len(kept) == 1
    assert kept[0]['subject'] == 'Uncapped Liability'
    assert 'without monetary ceiling' in kept[0]['content']
    assert 'finding_summary' not in kept[0]['content']
    assert SOURCE not in kept[0]['content']
    assert len(facts(memory)) == 11
    assert memory.complete_review(review(), SOURCE, candidates=[candidate()]) == []
    other = candidate(kind='variant', pattern='accepts liability')
    # Extend quoted evidence so the second, distinct pattern is grounded.
    record = review(evidence=[{'text': SOURCE, 'start': 0, 'end': len(SOURCE)}])
    assert len(memory.complete_review(record, SOURCE, candidates=[other])) == 1
    assert len(facts(memory)) == 12
    call = memory.client.messages.create.call_args.kwargs
    assert call['model'] == 'offline-small'
    assert QUOTE in call['messages'][0]['content']
    assert review()['finding_summary'] not in call['messages'][0]['content']


@pytest.mark.parametrize('bad', [
    {}, {'subject': 'Uncapped Liability', 'content': SOURCE},
    candidate(kind='report'), candidate(kind=[]), candidate(pattern=SOURCE),
    candidate(pattern='not in evidence'), candidate(pattern='Without monetary ceiling'),
    candidate(pattern='liability 2026'), candidate(pattern='no'), candidate(pattern=[]),
    candidate(evidence_index=True), candidate(evidence_index=-1), candidate(evidence_index=4),
    {**candidate(), 'gold_answer': True},
])
def test_rejected_patterns_never_become_semantic_facts(memory, bad):
    assert memory.complete_review(review(), SOURCE, candidates=[bad]) == []
    assert len(facts(memory)) == 10
    assert len(memory.episodes.list()) == 1


def test_recalled_pattern_is_not_written_even_without_numbers(memory):
    recalled = 'Earlier review cue: WITHOUT MONETARY CEILING.'
    assert memory.complete_review(review(), SOURCE, candidates=[candidate()], recalled=recalled) == []
    assert len(facts(memory)) == 10


def test_seeded_variant_is_not_written_as_a_new_fact(memory):
    source = 'The agreement states unlimited liability.'
    quote = 'unlimited liability'
    record = review(evidence=[{'text': quote, 'start': source.index(quote),
                              'end': source.index(quote) + len(quote)}])
    assert memory.complete_review(record, source, candidates=[candidate(pattern=quote)]) == []


def test_contract_identity_is_not_reusable_knowledge(memory):
    record = review(contract_id='monetary ceiling')
    assert memory.complete_review(record, SOURCE, candidates=[candidate()]) == []


def test_benchmark_prediction_can_be_history_but_never_teaches_semantic_facts(memory):
    record = review(origin='benchmark_prediction')
    assert memory.complete_review(record, SOURCE, candidates=[candidate()]) == []
    assert memory.complete_review(record, SOURCE) == []
    assert review_history(memory.episodes) == [record]
    assert len(facts(memory)) == 10
    memory.client.messages.create.assert_not_called()


@pytest.mark.parametrize('payload', [{'facts': [{'subject': 'clause', 'content': SOURCE}]},
                                     {'patterns': None}, [], None])
def test_malformed_model_consolidation_does_not_lose_review_episode(memory, payload):
    memory.client = client(payload)
    assert memory.complete_review(review(), SOURCE) == []
    assert review_history(memory.episodes) == [review()]
    assert len(facts(memory)) == 10


def test_failed_pattern_model_retains_episode_and_can_retry(memory):
    memory.client.messages.create.side_effect = TimeoutError('offline failure')
    assert memory.complete_review(review(), SOURCE) == []
    assert len(memory.episodes.list()) == 1
    memory.client = client({'patterns': [candidate()]})
    assert len(memory.complete_review(review(), SOURCE)) == 1
    assert len(memory.episodes.list()) == 1


def test_review_mode_never_consolidates_arbitrary_chat_or_benchmark_labels(memory):
    memory.settings.consolidate_every = 1
    memory.log_chat('Gold benchmark answer: unlimited liability.', 'A raw contract report.')
    memory.maybe_consolidate()
    assert len(facts(memory)) == 10
    assert memory.episodes.list() == []
    memory.client.messages.create.assert_not_called()
    assert memory.conn.execute('SELECT SUM(consolidated) FROM chat_log').fetchone()[0] == 0
