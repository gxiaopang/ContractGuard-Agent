"""Generated references, unscored coverage, isolated experiments and quality gates."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from evals.contractguard.cases import decode_case, generated_cases
from evals.contractguard.quality import case_metrics, paired_comparison, quality_verdict, summarize
from evals.contractguard.smoke import SmokeClient
from evals.contractguard.upgrade_runner import run_upgrade
from waku.config import Settings
from waku.ops.contractguard_judge import CRITERIA, judge_review
from waku.ops.release_gate import contractguard_quality
from waku.tools.domain_review import submit_memory


def test_generator_is_repeatable_and_covers_presence_absence_and_unknown():
    first, metadata = generated_cases(42)
    second, again = generated_cases(42)
    assert first == second and metadata == again
    assert len(first) == 40
    assert set(first[0].review_input().__dict__) == {'contract_id', 'text', 'context'}
    assert first[0].reference['extraction'][first[0].targets[0]]
    assert first[1].reference['extraction'][first[1].targets[0]] == []
    assert first[2].reference['extraction'] == {}
    assert first[0].reference['risk'] == {}
    assert generated_cases(7)[1]['sha256'] != metadata['sha256']


def test_frozen_arms_keep_memory_fixed_and_never_receive_scoring_references(tmp_path):
    cases, metadata = generated_cases()
    calls = []

    class Spy(SmokeClient):
        def create(self, **kwargs):
            payload = kwargs['messages'][0]['content']
            assert 'severity_weights' not in payload and 'critical_targets' not in payload
            decoded = json.loads(payload.split('User message: ', 1)[-1])
            assert 'reference' not in decoded
            calls.append(payload)
            return super().create(**kwargs)

    result = run_upgrade(cases[:4], metadata, Settings(), tmp_path,
                         repetitions=2, client_factory=lambda settings, mode: Spy())
    assert result['config']['status'] == 'completed'
    assert len(result['arms']) == 8 and calls
    for arm in result['arms']:
        assert arm['memory_snapshot_before'] == arm['memory_snapshot_after']
        assert arm['metrics']['extraction']['support'] == 3
        assert arm['metrics']['unscored_extraction_pairs'] == 1
        assert arm['metrics']['risk_agreement']['value'] is None
        assert arm['quality']['status'] == 'not_evaluated'
    folder = Path(result['directory'])
    paired = json.loads((folder / 'comparison.json').read_text())['paired']
    assert len(paired) == 6
    assert json.loads((folder / 'contamination.json').read_text())['held_out_claim'] is False
    report = folder / 'baseline-0/memory/contractguard/quality.json'
    assert contractguard_quality(report)['status'] == 'not_evaluated'
    with pytest.raises(FileExistsError):
        run_upgrade(cases[:1], metadata, Settings(), tmp_path, run_id=folder.name)


def test_sequential_feedback_follows_scoring_and_uses_only_source_rules(tmp_path):
    cases, metadata = generated_cases()
    result = run_upgrade(cases[:3], metadata, Settings(), tmp_path,
                         arms=('baseline', 'approved-history', 'correction-history'), experiment='sequential')
    assert result['config']['status'] == 'completed'
    folder = Path(result['directory'])
    feedback = json.loads((folder / 'correction-history-0/feedback.json').read_text())
    assert len(feedback) == 3
    assert all(f['timing'] == 'after_scoring' and not f['reference_access'] for f in feedback)
    assert result['arms'][-1]['memory_snapshot_before'] != result['arms'][-1]['memory_snapshot_after']
    assert all(p['interval_95'] is None and p['resampling'] == 'whole-sequence'
               for p in json.loads((folder / 'comparison.json').read_text())['paired'])


def test_failure_is_not_a_successful_negative_and_unknown_remains_unscored():
    cases, _ = generated_cases()
    target = cases[1].targets[0]
    finding = {'clause_type': target, 'status': 'error', 'extraction': None, 'risk': None,
               'comparison': None, 'memory_used': {'semantic': [], 'episodic': []},
               'error': {'stage': 'extraction', 'message': 'fixture failure'}}
    predictions = lambda c: {'contract_id': c.review_input().contract_id, 'findings': [finding]}
    rows = case_metrics(cases[1], predictions(cases[1])) + case_metrics(cases[2], predictions(cases[2]))
    metrics = summarize(rows)
    assert metrics['failure_rate']['value'] == 1
    assert metrics['extraction']['support'] == 1
    assert metrics['unscored_extraction_pairs'] == 1
    assert metrics['critical_miss_rate']['value'] is None
    assert summarize(rows[1:])['extraction']['f1'] is None


def test_quality_cannot_pass_skipped_calibration_missing_coverage_or_critical_failure():
    profile = {'schema': 'contractguard_quality_profile_v1', 'version': 'fixture-v1',
               'criteria': {'critical_miss_rate': {'maximum': .05, 'minimum_support': 2}}}
    metrics = {'critical_miss_rate': {'value': 0, 'support': 2}}
    assert quality_verdict(metrics, profile, execution_kind='offline_smoke', calibrated=True)['status'] == 'not_evaluated'
    assert quality_verdict(metrics, profile, execution_kind='live')['status'] == 'not_evaluated'
    assert quality_verdict(metrics, profile, execution_kind='live', calibrated=True)['status'] == 'passed'
    metrics['critical_miss_rate'] = {'value': None, 'support': 0}
    assert quality_verdict(metrics, profile, execution_kind='live', calibrated=True)['status'] == 'not_evaluated'
    metrics['critical_miss_rate'] = {'value': .5, 'support': 2}
    assert quality_verdict(metrics, profile, execution_kind='live', calibrated=True)['status'] == 'failed'


def test_contract_judge_requires_per_criterion_reasons_and_current_source_citations():
    source = 'The liability limit is subject to Section 2.'
    result = {'schema': 'contractguard_judge_v1', 'criteria': {
        c: {'score': .8, 'rationale': 'The fixture tests the criterion contract.', 'citations': [source]} for c in CRITERIA}}
    client = SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: SimpleNamespace(
        stop_reason='end_turn', content=[SimpleNamespace(type='text', text=json.dumps(result))])))
    assert judge_review(client, 'offline', source, {}, [])['calibration'] == 'not_calibrated'
    result['criteria']['evidence_support']['citations'] = ['A quote from a previous contract.']
    with pytest.raises(ValueError, match='current source'):
        judge_review(client, 'offline', source, {}, [])


def test_experience_proposal_needs_regression_cases_and_explicit_approval(tmp_path):
    settings = Settings(home=tmp_path, contract_review=True, contract_memory_upgrade=True)
    payload = {'action': 'propose', 'kind': 'experience', 'clause_type': 'Governing Law',
               'guidance': 'Keep law and venue separate.', 'context': {'role': 'supplier'},
               'valid_from': None, 'valid_until': None, 'reviewer': 'reviewer',
               'reason': 'Reviewed correction', 'regression_cases': []}
    with pytest.raises(ValueError, match='regression'):
        submit_memory(settings, payload)
    payload['regression_cases'] = ['law-venue-separation']
    proposed = submit_memory(settings, payload)
    assert proposed['state'] == 'candidate'
    assert submit_memory(settings, {'action': 'approve', 'memory_id': proposed['id'], 'revision': 1,
                                    'reviewer': 'reviewer', 'reason': 'Reviewed proposal'})['state'] == 'approved'
    forged = copy.deepcopy(payload)
    forged['context']['scope'] = 'f' * 64
    with pytest.raises(ValueError, match='server-owned'):
        submit_memory(settings, forged)


def test_paired_metrics_report_regressions_and_reject_different_coverage():
    row = {'case_id': 'a', 'family_id': 'f', 'target': 'Governing Law', 'failed': False,
           'critical_miss': False, 'false_positive': False, 'extraction_scored': True}
    regression = {**row, 'false_positive': True}
    comparison = paired_comparison([row], [regression])
    assert comparison['value'] == -1 and comparison['regressions'] == 1
    with pytest.raises(ValueError, match='coverage differs'):
        paired_comparison([row], [{**row, 'extraction_scored': False}])


def test_generic_case_does_not_accept_risk_without_perspective_and_rubric():
    case = {'schema': 'contractguard_case_v1', 'id': 'risk', 'family_id': 'risk', 'text': 'Contract text.',
            'targets': ['Governing Law'], 'context': {},
            'reference': {'extraction': {}, 'risk': {'Governing Law': 'HIGH'}, 'rubrics': {},
                          'checks': {}, 'critical_targets': [], 'severity_weights': {}}}
    with pytest.raises(ValueError, match='role and explicit rubric'):
        decode_case(case)


@pytest.mark.parametrize('headers,status', [
    ({'Content-Length': '1000001'}, 413), ({'Content-Type': 'text/plain'}, 415),
    ({'Origin': 'https://outside.example'}, 403), ({'Sec-Fetch-Site': 'cross-site'}, 403),
])
def test_memory_http_endpoint_rejects_untrusted_or_unbounded_requests(headers, status):
    from unittest.mock import Mock

    from evals.deterministic.test_contractguard_human_review import handler_for

    handler = handler_for({}, headers)
    handler.path = '/api/contractguard/memory'
    handler.rfile = Mock(side_effect=AssertionError('must not read'))
    handler.do_POST()
    assert handler.sent == [{'http_status': status}]


def test_memory_http_endpoint_uses_server_scope_and_never_constructs_an_agent(tmp_path, monkeypatch):
    from unittest.mock import Mock

    from evals.deterministic.test_contractguard_human_review import handler_for
    from waku.memory.contractguard_context import scope_for
    from waku.ops import contractguard, dashboard

    settings = Settings(home=tmp_path, contract_review=True, contract_memory_upgrade=True)
    monkeypatch.setattr(dashboard, 'load_settings', lambda: settings)
    monkeypatch.setattr(dashboard, 'get_agent', Mock(side_effect=AssertionError('no agent or provider')))
    payload = {'action': 'propose', 'kind': 'policy', 'clause_type': 'Governing Law',
               'guidance': 'Use the selected jurisdiction.', 'context': {'role': 'supplier'},
               'valid_from': None, 'valid_until': None, 'reviewer': 'reviewer',
               'reason': 'Checked policy', 'regression_cases': []}
    handler = handler_for(payload)
    handler.path = '/api/contractguard/memory'
    handler.do_POST()
    assert handler.sent[0]['ok'] and handler.sent[0]['status'] == 'candidate'
    record = contractguard.collect(tmp_path)['domain_memory'][0]
    assert record['applicability']['scope'] == scope_for(tmp_path)
    payload['context']['scope'] = 'other-home'
    handler = handler_for(payload)
    handler.path = '/api/contractguard/memory'
    handler.do_POST()
    assert handler.sent[0]['code'] == 'validation'
    assert len(contractguard.collect(tmp_path)['domain_memory']) == 1


def test_strict_release_blocks_unverified_quality_after_engineering_passes(tmp_path, monkeypatch, capsys):
    import sys
    from unittest.mock import Mock

    from waku import config
    from waku.ops import release_gate

    report = tmp_path / 'quality.json'
    report.write_text(json.dumps({'schema': 'contractguard_quality_report_v1',
                                 'execution_kind': 'offline_smoke', 'metrics': {},
                                 'profile': None, 'calibration': 'not_calibrated',
                                 'execution_status': 'completed'}))
    monkeypatch.setattr(sys, 'argv', ['gate', '--contractguard-report', str(report), '--strict-quality'])
    monkeypatch.setattr(release_gate, 'run', lambda suite: (0, {'passed': 1, 'failed': 0}))
    monkeypatch.setattr(release_gate, 'report', Mock())
    monkeypatch.setattr(config, 'load_settings', lambda: Settings(home=tmp_path, api_key='', provider='anthropic'))
    monkeypatch.setenv('ANTHROPIC_API_KEY', '')
    with pytest.raises(SystemExit) as stopped:
        release_gate.main()
    assert stopped.value.code == 1
    assert 'ContractGuard quality: not_evaluated' in capsys.readouterr().out


def test_retrieval_audit_catches_revoked_and_wrong_role_records_without_reference_labels():
    from evals.contractguard.dataset import ReviewInput
    from evals.contractguard.quality import retrieval_audit
    from waku.memory.contractguard_context import applicability, review_context

    context = review_context({'role': 'supplier', 'policy_version': 'v1'}, scope='0' * 64)
    constraints = applicability(context)
    records = [{'id': 'revoked', 'state': 'revoked', 'kind': 'review', 'source_id': None,
                'clause_type': 'Governing Law', 'applicability': constraints},
               {'id': 'opposite-role', 'state': 'approved', 'kind': 'review', 'source_id': None,
                'clause_type': 'Governing Law', 'applicability': {**constraints, 'role': 'customer'}}]
    source = ReviewInput('current', 'Current contract.', {'role': 'supplier', 'policy_version': 'v1'})
    events = [{'contract_id': 'current', 'clause_type': 'Governing Law',
               'selected_ids': ['revoked', 'opposite-role'], 'characters': 10, 'budget': 5}]
    audit = retrieval_audit(events, records, [source], '0' * 64)
    assert audit['applicability_violation_rate']['value'] == 1
    assert audit['budget_violation_rate']['value'] == 1
