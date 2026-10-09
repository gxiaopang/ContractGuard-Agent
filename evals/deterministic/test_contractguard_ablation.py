"""Offline arm isolation, source-only requests, history timing and benchmark artifacts."""

from __future__ import annotations

import copy
import json
import threading
from dataclasses import asdict
from pathlib import Path
from unittest.mock import Mock

import pytest

from evals.contractguard.dataset import Contract, load_dataset
from evals.contractguard.predictor import MODES, Predictor, Telemetry
from evals.contractguard.risk import evaluate_risk, load_cases
from evals.contractguard.runner import new_run, run_benchmark, run_risk
from evals.contractguard.smoke import SmokeClient
from evals.helpers import ScriptedClient, response, text_block
from scripts.run_eval import main
from waku.config import Settings
from waku.memory.contractguard import decode_review

FIXTURES = Path(__file__).resolve().parents[1] / 'contractguard/fixtures'


def without_timestamps(value):
    if isinstance(value, dict):
        return {k: without_timestamps(v) for k, v in value.items() if k != 'timestamp'}
    if isinstance(value, list):
        return [without_timestamps(v) for v in value]
    return value


@pytest.mark.parametrize('mode', MODES)
def test_concurrent_clauses_preserve_findings_history_order_and_accounting(tmp_path, mode):
    targets = ['Governing Law', 'Cap on Liability']
    dataset = load_dataset(FIXTURES / 'smoke.json', targets)
    runs = []
    for workers in (1, 2):
        predictor = Predictor(settings(tmp_path / f'{mode}-{workers}'), SmokeClient(), mode, workers=workers)
        try:
            predictions = [predictor.predict(c.review_input(), targets) for c in dataset.contracts]
            for prediction in predictions:
                prediction['diagnostics'].pop('latency_seconds')
            runs.append((without_timestamps(predictions), predictor.telemetry.snapshot(),
                         len(predictor.memory.episodes.list(limit=-1))))
        finally:
            predictor.close()
    assert runs[0] == runs[1]


def test_concurrent_network_stages_keep_sqlite_on_owner_thread(tmp_path, monkeypatch):
    targets = ['Governing Law', 'Cap on Liability']
    barrier = threading.Barrier(2)
    client = SmokeClient()
    original = client.messages.create
    threads = set()
    owner = threading.get_ident()

    def concurrent_create(**kwargs):
        content = kwargs['messages'][0]['content']
        payload = json.loads(content.split('User message: ', 1)[1] if not kwargs.get('system') else content)
        if 'extraction' not in payload:
            threads.add(threading.get_ident())
            barrier.wait(timeout=5)
        return original(**kwargs)

    client.messages.create = concurrent_create
    predictor = Predictor(settings(tmp_path / 'parallel'), client, 'full-memory', workers=2)
    for store, name in ((predictor.memory.facts, 'search_with_ids'),
                        (predictor.memory.episodes, 'list'), (predictor.memory, 'complete_review')):
        method = getattr(store, name)

        def on_owner_thread(*args, _method=method, **kwargs):
            assert threading.get_ident() == owner
            return _method(*args, **kwargs)

        monkeypatch.setattr(store, name, on_owner_thread)
    try:
        source = load_dataset(FIXTURES / 'smoke.json', targets).contracts[0].review_input()
        result = predictor.predict(source, targets)
        assert [f['clause_type'] for f in result['findings']] == targets
        assert all(f['status'] != 'error' for f in result['findings'])
        assert len(threads) == 2 and owner not in threads
        assert result['diagnostics']['failed_model_calls'] == 0
    finally:
        predictor.close()


def test_concurrent_partial_contract_cannot_write_successful_sibling(tmp_path):
    client = SmokeClient()
    original = client.messages.create

    def failing_create(**kwargs):
        if kwargs.get('system'):
            payload = json.loads(kwargs['messages'][0]['content'])
            if payload.get('clause_type') == 'Cap on Liability':
                raise RuntimeError('authored extraction failure')
        return original(**kwargs)

    client.messages.create = failing_create
    predictor = Predictor(settings(tmp_path / 'partial'), client, 'full-memory', workers=2)
    try:
        source = load_dataset(FIXTURES / 'smoke.json', ['Governing Law']).contracts[0].review_input()
        result = predictor.predict(source, ['Governing Law', 'Cap on Liability'])
        assert [f['status'] for f in result['findings']] == ['found', 'error']
        assert result['findings'][1]['error']['stage'] == 'extraction'
        assert result['diagnostics']['failed_model_calls'] == 1
        assert result['diagnostics']['episode_writes'] == 0
        assert predictor.memory.episodes.list() == []
    finally:
        predictor.close()


@pytest.mark.parametrize('workers', [0, 33, True, '2'])
def test_invalid_worker_counts_cannot_create_a_run(tmp_path, workers):
    dataset = load_dataset(FIXTURES / 'smoke.json', ['Governing Law'])
    with pytest.raises(ValueError, match='clause workers'):
        run_benchmark(dataset, settings(tmp_path / 'unused'), tmp_path / 'runs', workers=workers)
    assert not (tmp_path / 'runs').exists()


def test_cli_records_concurrent_execution_without_changing_arm_isolation(tmp_path):
    assert main(['--smoke', '--ablation', '--workers', '2', '--seed', '42',
                 '--output', str(tmp_path), '--run-id', 'parallel']) == 0
    root = tmp_path / 'parallel'
    config = json.loads((root / 'config.json').read_text())
    rows = json.loads((root / 'comparison.json').read_text())['arms']
    assert config['clause_workers'] == 2
    assert config['status'] == 'completed'
    assert len({r['memory_home'] for r in rows}) == 3
    assert [r['metrics']['micro']['f1'] for r in rows] == [1.0, 1.0, 1.0]


@pytest.mark.parametrize('provider,model,cost', [('scripted', 'unknown', None),
                                               ('openai', 'gpt-5.6-sol', 0.000065),
                                               ('scripted', 'sample:free', 0.0)])
def test_telemetry_distinguishes_unknown_prices_from_known_free_calls(provider, model, cost):
    telemetry = Telemetry(provider)
    telemetry.event('llm', {'model': model, 'usage': {'in': 1, 'out': 2}})
    result = telemetry.snapshot()
    assert result['input_tokens'] == 1 and result['output_tokens'] == 2
    if cost is None:
        assert result['estimated_cost_usd'] is None
    else:
        assert result['estimated_cost_usd'] == pytest.approx(cost)


def settings(home):
    return Settings(home=home, provider='scripted', model='source-rules-v1', small_model='source-rules-v1',
                    api_key='', base_url=None, contract_review=False, semantic_store='sqlite',
                    episodic_store='sqlite', apple_tools=False, experimental=False,
                    gh_tool=False, otel_endpoint='', retrieval_top_k=4)


def spy_client():
    client = SmokeClient()
    client.messages.create = Mock(wraps=client.messages.create)
    return client


def calls(client, *, extraction_only=False):
    result = []
    for call in client.messages.create.call_args_list:
        kwargs = call.kwargs
        content = kwargs['messages'][0]['content']
        payload = json.loads(content.split('User message: ', 1)[1] if not kwargs.get('system') else content)
        if not extraction_only or 'procedure' in payload and 'extraction' not in payload:
            result.append(payload)
    return result


@pytest.mark.parametrize('mode,facts,episodes', [('baseline', 0, 0), ('semantic-only', 10, 0), ('full-memory', 10, 2)])
def test_each_mode_has_explicit_context_and_write_policy(tmp_path, mode, facts, episodes):
    client = spy_client()
    predictor = Predictor(settings(tmp_path / mode), client, mode)
    contracts = load_dataset(FIXTURES / 'smoke.json', ['Governing Law']).contracts
    try:
        first = predictor.predict(contracts[0].review_input(), ['Governing Law'])
        second = predictor.predict(contracts[1].review_input(), ['Governing Law'])
        a, b = first['findings'][0]['memory_used'], second['findings'][0]['memory_used']
        assert a['episodic'] == []
        assert bool(a['semantic']) == (mode != 'baseline')
        assert bool(b['episodic']) == (mode == 'full-memory')
        if b['episodic']:
            assert b['episodic'][0]['contract_id'] == contracts[0].contract_id
            assert b['episodic'][0]['origin'] == 'benchmark_prediction'
        assert len(predictor.memory.facts.list(limit=-1)) == facts
        assert len(predictor.memory.episodes.list(limit=-1)) == episodes
        assert first['findings'][0]['status'] == second['findings'][0]['status'] == 'found'
        assert first['diagnostics']['llm_calls'] == (2 if mode == 'baseline' else 3)
        payloads = calls(client, extraction_only=True)
        assert 'Identify the law selected to govern the agreement.' in payloads[0]['procedure']
        assert payloads[0]['source_text'] == contracts[0].text
    finally:
        predictor.close()


def test_baseline_does_not_query_memory_or_call_the_gate(tmp_path, monkeypatch):
    client = spy_client()
    predictor = Predictor(settings(tmp_path / 'baseline'), client, 'baseline')
    monkeypatch.setattr(predictor.memory.facts, 'search_with_ids', Mock(side_effect=AssertionError('no query')))
    monkeypatch.setattr(predictor.memory.episodes, 'list', Mock(side_effect=AssertionError('no history')))
    source = load_dataset(FIXTURES / 'smoke.json', ['Governing Law']).contracts[0].review_input()
    try:
        result = predictor.predict(source, ['Governing Law'])
        assert result['findings'][0]['status'] == 'found'
        assert len(calls(client)) == 2 and all('procedure' in c for c in calls(client))
    finally:
        predictor.close()


def test_benchmark_pins_local_stores_and_excludes_user_slot_gate(tmp_path, monkeypatch):
    monkeypatch.setenv('WAKU_SLOT_GATE', 'jev')
    monkeypatch.setattr('waku.memory.slot_gate.select', Mock(side_effect=AssertionError('no optional adapter')))
    configured = settings(tmp_path / 'fresh')
    configured.semantic_store, configured.episodic_store = 'mem0', 'notion'
    predictor = Predictor(configured, spy_client(), 'semantic-only')
    try:
        source = load_dataset(FIXTURES / 'smoke.json', ['Governing Law']).contracts[0].review_input()
        result = predictor.predict(source, ['Governing Law'])
        assert result['findings'][0]['status'] == 'found'
        assert predictor.settings.semantic_store == predictor.settings.episodic_store == 'sqlite'
    finally:
        predictor.close()


def test_predictor_rejects_annotation_objects_repeated_documents_and_existing_homes(tmp_path):
    client = spy_client()
    predictor = Predictor(settings(tmp_path / 'fresh'), client, 'baseline')
    contract = load_dataset(FIXTURES / 'smoke.json', ['Governing Law']).contracts[0]
    try:
        with pytest.raises(ValueError):
            predictor.predict(contract, ['Governing Law'])
        assert client.messages.create.call_count == 0
        predictor.predict(contract.review_input(), ['Governing Law'])
        with pytest.raises(ValueError):
            predictor.predict(contract.review_input(), ['Governing Law'])
        assert client.messages.create.call_count == 2
    finally:
        predictor.close()
    with pytest.raises(ValueError):
        Predictor(settings(tmp_path / 'fresh'), spy_client(), 'baseline')


def test_current_contract_is_excluded_before_history_limit(tmp_path):
    predictor = Predictor(settings(tmp_path / 'fresh'), spy_client(), 'full-memory')
    contracts = load_dataset(FIXTURES / 'smoke.json', ['Governing Law']).contracts
    try:
        prediction = predictor.predict(contracts[0].review_input(), ['Governing Law'])
        record = decode_review(predictor.memory.episodes.list()[0]['summary'])
        for i in range(5):
            quote = contracts[1].annotations['Governing Law']
            newer = {**record, 'contract_id': contracts[1].contract_id, 'evidence': quote,
                     'timestamp': f'2026-10-06T1{i}:00:00+00:00'}
            predictor.memory.complete_review(newer, contracts[1].text, candidates=[])
        context = predictor.context(contracts[1].review_input(), 'Governing Law')
        assert len(context['episodic']) == 1
        assert context['episodic'][0]['contract_id'] == prediction['contract_id']
    finally:
        predictor.close()


def test_failed_contract_does_not_accumulate_other_valid_findings(tmp_path):
    gate = {'semantic': False, 'episodic': False, 'clause_type': None, 'query': '', 'reason': 'self-contained'}
    source = load_dataset(FIXTURES / 'smoke.json', ['Governing Law']).contracts[0]
    quote = source.annotations['Governing Law'][0]['text']
    extract = {'clause_type': 'Governing Law', 'found': True, 'spans': [{'text': quote, 'occurrence': 0}],
               'confidence': 1.0, 'rationale': 'Actual source.'}
    risk = {'clause_type': 'Governing Law', 'risk_level': 'LOW', 'reasoning': 'Named law.',
            'recommendation': 'Confirm suitability.', 'evidence_indices': [0]}
    client = ScriptedClient([response([text_block(json.dumps(p))]) for p in (gate, extract, risk, gate, {})])
    predictor = Predictor(settings(tmp_path / 'fresh'), client, 'full-memory')
    try:
        result = predictor.predict(source.review_input(), ['Governing Law', 'Indemnification'])
        assert [f['status'] for f in result['findings']] == ['found', 'error']
        assert predictor.memory.episodes.list() == []
        assert len(predictor.memory.facts.list(limit=-1)) == 10
    finally:
        predictor.close()


def test_three_arms_share_procedures_order_prompts_and_never_receive_gold_metadata(tmp_path):
    source = load_dataset(FIXTURES / 'smoke.json', ['Governing Law'])
    contracts = tuple(Contract(c.contract_id, 'GOLD_ONLY_CANARY_TITLE', c.text, c.annotations) for c in source.contracts)
    dataset = type(source)(contracts, source.targets, source.metadata)
    clients = {}

    def factory(configured, mode):
        clients[mode] = spy_client()
        return clients[mode]

    result = run_benchmark(dataset, settings(tmp_path / 'unused-runtime'), tmp_path / 'runs',
                           seed=7, client_factory=factory, execution_kind='offline_test')
    assert result['config']['status'] == 'completed'
    assert len({row['memory_home'] for row in result['arms']}) == 3
    procedures = []
    for mode in MODES:
        payloads = calls(clients[mode], extraction_only=True)
        procedures.append([p['procedure'] for p in payloads])
        assert [p['source_text'] for p in payloads] == [contracts[i].text for i in [0, 1]]
        assert 'GOLD_ONLY_CANARY_TITLE' not in json.dumps(calls(clients[mode]))
        assert all(set(p['memory']) == {'semantic', 'episodic'} for p in payloads)
        assert all('annotations' not in p for p in payloads)
    assert procedures[0] == procedures[1] == procedures[2]
    assert [r['memory_counts'] for r in result['arms']] == [
        {'facts': 0, 'episodes': 0}, {'facts': 10, 'episodes': 0}, {'facts': 10, 'episodes': 2}]
    config = result['config']
    assert config['temperature'] is None and config['decoding_policy'] == 'provider-default-not-overridden'
    assert config['matcher']['threshold'] == 0.5 and config['shuffle_seed'] == 7
    assert config['retrieval_top_k'] == 4 and config['episodic_history_limit'] == 3
    assert config['provider'] == 'scripted' and config['prompt_sha256']
    assert config['git_commit'] and config['code_hashes'] and config['dataset']['sha256']
    assert 'api_key' not in json.dumps(config)
    directory = Path(result['directory'])
    assert (directory / 'comparison.md').exists() and (directory / 'comparison.json').exists()
    assert json.loads((directory / 'prompts.json').read_text())['extraction']
    for row in result['arms']:
        assert row['metrics']['micro']['f1'] == 1.0
        assert row['telemetry']['estimated_cost_usd'] is None
        assert (Path(row['memory_home']).parent / 'predictions.jsonl').exists()


def test_runner_does_not_change_annotations_or_reuse_state_and_records_limits(tmp_path):
    dataset = load_dataset(FIXTURES / 'smoke.json', ['Governing Law'])
    before = copy.deepcopy(asdict(dataset))
    runtime = tmp_path / 'runtime'
    runtime.mkdir()
    (runtime / 'keep.txt').write_text('personal data')
    options = {'client_factory': lambda s, m: SmokeClient(), 'execution_kind': 'offline_test',
               'modes': ['baseline'], 'seed': 1, 'limit': 1, 'run_id': 'fixed'}
    result = run_benchmark(dataset, settings(runtime), tmp_path / 'runs', **options)
    assert result['config']['number_of_contracts'] == 1
    assert result['config']['contract_order'] == [dataset.contracts[1].contract_id]
    assert asdict(dataset) == before
    with pytest.raises(FileExistsError):
        run_benchmark(dataset, settings(runtime), tmp_path / 'runs', **options)
    assert (runtime / 'keep.txt').read_text() == 'personal data'


@pytest.mark.parametrize('kwargs', [{'modes': []}, {'modes': ['baseline', 'baseline']}, {'modes': ['gold-memory']},
                                    {'rule': 'tuned'}, {'seed': True}, {'limit': 0}, {'limit': True},
                                    {'execution_kind': 'pretend-live'}])
def test_runner_rejects_invalid_experiment_settings_before_model_work(tmp_path, kwargs):
    factory = Mock(side_effect=AssertionError('must not run'))
    with pytest.raises(ValueError):
        options = {'client_factory': factory, 'execution_kind': 'offline_test', **kwargs}
        run_benchmark(load_dataset(FIXTURES / 'smoke.json'), settings(tmp_path / 'unused'), tmp_path / 'runs', **options)
    assert factory.call_count == 0


@pytest.mark.parametrize('identity', ['../runtime', '', '/tmp/escape', 'a.b', 'a' * 81])
def test_run_identity_cannot_escape_the_artifact_root(tmp_path, identity):
    with pytest.raises(ValueError):
        new_run(tmp_path, identity)


def test_failed_provider_records_attempts_without_exposing_exception_text(tmp_path):
    client = SmokeClient()
    client.messages.create = Mock(side_effect=RuntimeError('secret provider configuration'))
    result = run_benchmark(load_dataset(FIXTURES / 'smoke.json', ['Governing Law']),
                           settings(tmp_path / 'unused'), tmp_path / 'runs', modes=['baseline'],
                           client_factory=lambda s, m: client, execution_kind='offline_test')
    row = result['arms'][0]
    assert result['config']['status'] == 'partial'
    assert row['telemetry']['llm_calls'] == row['telemetry']['failed_model_calls'] == 2
    assert row['metrics']['extraction_failed_pairs'] == 2 and row['metrics']['micro']['fn'] == 2
    assert 'secret provider' not in (Path(result['directory']) / 'baseline/predictions.jsonl').read_text()


@pytest.mark.parametrize('mode', ['baseline', 'full-memory'])
def test_payment_failure_stops_later_contracts_and_arms_without_repeating_requests(tmp_path, mode):
    class BalanceError(RuntimeError):
        status_code = 402

    client = SmokeClient()
    client.messages.create = Mock(side_effect=BalanceError('secret account configuration'))
    factory = Mock(return_value=client)
    dataset = load_dataset(FIXTURES / 'smoke.json', ['Governing Law', 'Cap on Liability'])
    result = run_benchmark(dataset, settings(tmp_path / 'unused'), tmp_path / 'runs',
                           modes=[mode, 'semantic-only'], client_factory=factory, execution_kind='offline_test')
    assert result['config']['status'] == 'provider_unavailable'
    assert result['config']['provider_error']['status_code'] == 402
    assert result['config']['processed_modes'] == [mode]
    assert factory.call_count == client.messages.create.call_count == 1
    assert len(result['arms']) == 1
    row = result['arms'][0]
    assert row['metrics']['contracts'] == 1
    assert row['metrics']['extraction_failed_pairs'] == 2
    assert row['telemetry']['llm_calls'] == row['telemetry']['failed_model_calls'] == 1
    assert row['telemetry']['provider_status_code'] == 402
    assert row['memory_counts']['episodes'] == 0
    directory = Path(result['directory'])
    assert not (directory / 'semantic-only').exists()
    assert 'secret account' not in (directory / mode / 'predictions.jsonl').read_text()
    assert 'HTTP 402 interrupted this run' in (directory / 'comparison.md').read_text()


def test_manifest_records_failed_setup_and_offline_labels_require_an_offline_factory(tmp_path):
    dataset = load_dataset(FIXTURES / 'smoke.json', ['Governing Law'])
    with pytest.raises(ValueError):
        run_benchmark(dataset, settings(tmp_path / 'unused'), tmp_path / 'runs', execution_kind='offline_smoke')
    with pytest.raises(RuntimeError):
        run_benchmark(dataset, settings(tmp_path / 'unused'), tmp_path / 'runs', run_id='failure',
                       client_factory=Mock(side_effect=RuntimeError('setup failed')), execution_kind='offline_test')
    assert json.loads((tmp_path / 'runs/failure/config.json').read_text())['status'] == 'failed'


def test_risk_examples_are_separate_gold_and_are_never_sent_to_a_model(tmp_path):
    cases, checksum = load_cases(FIXTURES / 'risk_cases.jsonl')
    assert len(cases) == 10 and {c['expected_risk'] for c in cases} == {'HIGH', 'MEDIUM', 'LOW'}
    assert checksum and len({c['clause_type'] for c in cases}) == 10
    case = copy.deepcopy(next(c for c in cases if c['clause_type'] == 'Governing Law'))
    case['rubric'] = 'GOLD_ONLY_CANARY_RUBRIC'
    client = spy_client()
    predictions, metrics = evaluate_risk([case], settings(tmp_path / 'unused'), client)
    assert metrics['label_agreement'] == 1.0 and metrics['reference'] == 'authored-risk-rubrics'
    assert 'GOLD_ONLY_CANARY_RUBRIC' not in json.dumps(calls(client))
    assert all('expected_risk' not in p and 'rubric' not in p for p in calls(client))
    assert predictions[0]['rubric'] == 'GOLD_ONLY_CANARY_RUBRIC'
    result = run_risk(FIXTURES / 'risk_cases.jsonl', settings(tmp_path / 'unused'), tmp_path / 'runs',
                      client_factory=lambda s, m: SmokeClient(), execution_kind='offline_test')
    assert result['config']['status'] == 'completed'
    assert result['metrics']['label_agreement'] == 0.3
    assert result['metrics']['failed_cases'] == 0


def test_risk_failures_remain_in_the_denominator_and_cuad_never_supplies_labels(tmp_path):
    cases, _ = load_cases(FIXTURES / 'risk_cases.jsonl')
    client = SmokeClient()
    client.messages.create = Mock(side_effect=RuntimeError('failure'))
    _, metrics = evaluate_risk(cases[:2], settings(tmp_path / 'unused'), client)
    assert metrics['label_agreement'] == 0.0 and metrics['failed_cases'] == 2
    assert metrics['confusion']['HIGH']['ERROR'] == 2
    assert metrics['telemetry']['failed_model_calls'] == 2
    with pytest.raises(ValueError):
        load_cases(FIXTURES / 'smoke.json')


def test_cli_smoke_ablation_and_separate_risk_produce_real_artifact_paths(tmp_path, capsys):
    assert main(['--smoke', '--ablation', '--output', str(tmp_path), '--run-id', 'ablation']) == 0
    assert main(['--smoke', '--risk', '--output', str(tmp_path), '--run-id', 'risk']) == 0
    output = capsys.readouterr().out
    assert 'Execution kind: offline_smoke' in output
    assert 'Unscored CUAD targets: Indemnification, Confidentiality Obligations' in output
    assert (tmp_path / 'ablation/comparison.md').exists()
    assert (tmp_path / 'risk/metrics.json').exists()


@pytest.mark.parametrize('args', [[], ['--risk', '--ablation'], ['--risk', '--dataset', 'x'],
                                  ['--smoke', '--provider', 'deepseek'], ['--smoke', '--model', 'live']])
def test_cli_rejects_ambiguous_or_missing_inputs(args):
    with pytest.raises(SystemExit) as exc:
        main(args)
    assert exc.value.code == 2
