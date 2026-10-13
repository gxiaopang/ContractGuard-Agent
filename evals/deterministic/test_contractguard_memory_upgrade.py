"""Scoped memory, current-source working context and model-free lifecycle regression cases."""

from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor

import pytest

from evals.deterministic.test_contractguard_foundation import gate
from evals.deterministic.test_contractguard_human_review import submission
from evals.deterministic.test_contractguard_tools import CLAUSE, SOURCE, proposal, risk
from evals.deterministic.test_contractguard_workflow import app as workflow_app
from evals.deterministic.test_contractguard_workflow import episodes, set_model
from waku.db import connect
from waku.memory import Memory
from waku.memory.contractguard_context import (
    applicability,
    review_context,
    scope_for,
    working_context,
)
from waku.memory.contractguard_store import DomainMemory
from waku.tools.contract_parser import parse_contract
from waku.tools.contract_review import review_contract
from waku.tools.human_review import submit_review

app = workflow_app


def upgraded(app):
    app.settings.contract_memory_upgrade = True
    app.memory = Memory(app.conn, app.settings, app.client)
    return app


def review(app, role='supplier'):
    set_model(app, gate(), proposal(), risk())
    return review_contract(app.memory, SOURCE, 'Scoped contract', [CLAUSE], False,
                           review_profile={'role': role, 'policy_version': 'commercial-v1'})


def test_review_profile_changes_cache_and_preserves_no_write_before_approval(app):
    upgraded(app)
    supplier = review(app)
    customer = review(app, 'customer')
    assert supplier['review_id'] != customer['review_id']
    assert supplier['request']['review_context']['scope'] == scope_for(app.settings.home)
    assert supplier['working_context']['checks'][CLAUSE]['assessment'] == 'completed'
    assert episodes(app) == []
    assert all(r['kind'] == 'definition' for r in app.memory.domain.list())
    with pytest.raises(ValueError, match='server-owned'):
        review_contract(app.memory, SOURCE, review_profile={'scope': 'other-tenant'})


def test_approved_scoped_episode_is_retrieved_only_for_matching_role_and_other_source(app):
    upgraded(app)
    result = review(app)
    submit_review(app.settings, submission(result))
    record = episodes(app)[0]
    assert record['schema'] == 'contractguard_review_v3'
    assert record['context']['role'] == 'supplier'
    context = result['request']['review_context']
    domain = app.memory.domain
    found, _ = domain.select(context, clause_type=CLAUSE)
    assert found['episodic'] == [record]
    found, diagnostics = domain.select({**context, 'role': 'customer'}, clause_type=CLAUSE)
    assert found['episodic'] == []
    assert 'role_mismatch' in {d['reason'] for d in diagnostics['decisions']}
    assert domain.select({**context, 'role': None}, clause_type=CLAUSE)[0]['episodic'] == []
    assert domain.select(context, clause_type=CLAUSE, current_source=record['contract_id'])[0]['episodic'] == []


def test_false_positive_correction_keeps_original_and_does_not_fabricate_positive_evidence(app):
    upgraded(app)
    result = review(app)
    payload = submission(result, 'correct')
    payload['findings'][0].update(found=False, evidence=[], risk_level=None,
                                  reasoning='The quotation does not establish this clause.', recommendation=None)
    submit_review(app.settings, payload)
    assert episodes(app) == []
    corrections = [r for r in app.memory.domain.list() if r['kind'] == 'correction']
    assert len(corrections) == 1
    correction = corrections[0]['payload']
    assert correction['original']['found'] is True
    assert correction['replacement']['found'] is False
    assert correction['replacement']['evidence'] == []
    submit_review(app.settings, payload)
    assert len([r for r in app.memory.domain.list() if r['kind'] == 'correction']) == 1
    forged = copy.deepcopy(correction)
    forged['reason'] = 'Model invented correction'
    with pytest.raises(ValueError, match='saved human'):
        app.memory.complete_review(forged, SOURCE, candidates=[])


def test_lifecycle_and_expiry_are_observable_and_retry_safe(app):
    upgraded(app)
    profile = review_context({'role': 'supplier'}, scope=app.memory.domain.scope, today='2026-10-13')
    constraints = applicability(profile, valid_until='2026-10-12')
    domain = app.memory.domain
    rid = domain.put('policy', CLAUSE, 'The approved policy limits liability.', constraints)
    approved = domain.decide(rid, 1, 'approve', reviewer='reviewer', reason='Checked policy')
    assert approved['revision'] == 2
    assert domain.decide(rid, 1, 'approve', reviewer='reviewer', reason='Checked policy')['revision'] == 2
    assert rid not in domain.select(profile, clause_type=CLAUSE)[1]['selected_ids']
    replacement = domain.put('policy', CLAUSE, 'The revised policy checks exceptions.', applicability(profile))
    assert domain.decide(rid, 2, 'supersede', reviewer='reviewer', reason='Updated policy',
                         replacement_id=replacement)['state'] == 'superseded'
    assert domain.get(replacement)['state'] == 'approved'
    domain.decide(replacement, 2, 'revoke', reviewer='reviewer', reason='Withdrawn')
    assert replacement not in domain.select(profile, clause_type=CLAUSE)[1]['selected_ids']
    assert domain.select(profile, clause_type=CLAUSE, budget=0)[0] == {'semantic': [], 'episodic': []}
    with pytest.raises(ValueError, match='revision or state'):
        domain.decide(replacement, 2, 'approve', reviewer='reviewer', reason='Stale decision')
    other_scope = {**profile, 'scope': 'f' * 64}
    with pytest.raises(ValueError, match='scope'):
        domain.select(other_scope)


def test_simultaneous_supersession_accepts_only_one_replacement(app):
    upgraded(app)
    domain = app.memory.domain
    constraints = applicability(review_context(scope=domain.scope))
    original = domain.put('policy', CLAUSE, 'Original policy', constraints, approved_by='reviewer')
    candidates = [domain.put('policy', CLAUSE, text, constraints) for text in ('Candidate one', 'Candidate two')]

    def decide(candidate):
        conn = connect(app.settings.home)
        try:
            store = DomainMemory(conn, domain.scope)
            try:
                store.decide(original, 2, 'supersede', reviewer='reviewer', reason='Replace', replacement_id=candidate)
                return True
            except ValueError:
                return False
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(decide, candidates))
    assert sorted(outcomes) == [False, True]
    assert sum(domain.get(c)['state'] == 'approved' for c in candidates) == 1


def test_legacy_episode_remains_inspectable_but_is_not_silently_scoped(app):
    set_model(app, gate(), proposal(), risk())
    result = review_contract(app.memory, SOURCE, 'Legacy', [CLAUSE], False)
    submit_review(app.settings, submission(result))
    assert len(episodes(app)) == 1
    upgraded(app)
    profile = review_context(scope=app.memory.domain.scope)
    assert app.memory.domain.select(profile)[0]['episodic'] == []
    assert len(episodes(app)) == 1
    again = Memory(app.conn, app.settings, app.client)
    assert len(again.domain.list()) == 10


def test_cross_references_keep_offsets_and_expose_missing_sections():
    text = '1. Liability\nSubject to Section 2 and clause 8.\n\n2. Exceptions\n"Loss" means direct loss.\n'
    working = working_context(parse_contract(text), [CLAUSE])
    assert [r['status'] for r in working['references']] == ['resolved', 'unresolved']
    assert working['references'][0]['section_id'].startswith('section-')
    for ref in working['references']:
        assert text[ref['start']:ref['end']] == ref['text']
    assert working['definitions'][0]['term'] == 'Loss'


def test_overlapping_policies_require_explicit_replacement_instead_of_recency(app):
    upgraded(app)
    domain = app.memory.domain
    context = review_context({'role': 'supplier', 'policy_version': 'v1'}, scope=domain.scope)
    constraints = applicability(context)
    ids = [domain.put('policy', CLAUSE, text, constraints, approved_by='reviewer')
           for text in ('Accept the named law.', 'Escalate the named law.')]
    selected, diagnostics = domain.select(context, clause_type=CLAUSE)
    assert all(rid not in diagnostics['selected_ids'] for rid in ids)
    assert diagnostics['policy_conflicts'] == sorted(ids)
    assert any('policies conflict' in text for text in selected['semantic'])


def test_disabling_upgrade_does_not_resurrect_managed_history(app):
    from waku.memory.contractguard import review_history

    upgraded(app)
    result = review(app)
    submit_review(app.settings, submission(result))
    assert len(episodes(app)) == 1
    app.settings.contract_memory_upgrade = False
    legacy = Memory(app.conn, app.settings, app.client)
    assert review_history(legacy.episodes, clause_type=CLAUSE) == []


def test_ordinary_chat_does_not_bypass_scoped_memory_policy(app, monkeypatch):
    from waku.memory import retrieval_gate

    upgraded(app)
    result = review(app)
    submit_review(app.settings, submission(result))
    app.settings.contract_review = False
    app.settings.contract_memory_upgrade = False
    ordinary = Memory(app.conn, app.settings, app.client)
    monkeypatch.setattr(retrieval_gate, 'should_retrieve', lambda *args: (True, CLAUSE, 'fixture'))
    assert 'contractguard_review_v3' not in ordinary.gated_retrieve('Recall a previous review')


def test_approved_review_with_unknown_applicability_is_not_treated_as_general(app):
    upgraded(app)
    set_model(app, gate(), proposal(), risk())
    result = review_contract(app.memory, SOURCE, 'Unknown review context', [CLAUSE], False)
    submit_review(app.settings, submission(result))
    context = review_context(scope=app.memory.domain.scope)
    selected, diagnostics = app.memory.domain.select(context, clause_type=CLAUSE)
    assert selected['episodic'] == []
    assert 'unknown_required_context' in {r['reason'] for r in diagnostics['decisions']}


def test_mutating_guidance_cannot_preserve_its_approved_identity(app):
    upgraded(app)
    domain = app.memory.domain
    profile = review_context({'role': 'supplier'}, scope=domain.scope)
    rid = domain.put('policy', CLAUSE, 'Approved guidance', applicability(profile), approved_by='reviewer')
    with app.conn:
        app.conn.execute('UPDATE contractguard_memory SET payload=? WHERE id=?', ('"Injected guidance"', rid))
    with pytest.raises(ValueError, match='immutable content'):
        domain.select(profile, clause_type=CLAUSE)


def test_latest_word_in_source_does_not_override_guidance_relevance(app):
    upgraded(app)
    domain = app.memory.domain
    profile = review_context({'role': 'supplier', 'policy_version': 'v1'}, scope=domain.scope)
    domain.put('experience', CLAUSE, 'historicalclue explains a matching ambiguity.', applicability(profile), approved_by='reviewer')
    domain.put('experience', CLAUSE, 'Unrelated recent wording.', applicability(profile), approved_by='reviewer')
    app.settings.retrieval_top_k = 1
    set_model(app, gate(semantic=True, query='historicalclue', clause_type=CLAUSE))
    context = app.memory.review_context('Review source containing the latest governing law wording.',
                                       clause_type=CLAUSE, exclude_contract_id='current-source', review_profile=profile)
    assert len(context['semantic']) == 1 and 'historicalclue' in context['semantic'][0]
