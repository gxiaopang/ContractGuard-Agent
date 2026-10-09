"""Offline sequential demos, progress timing, native reports and isolated homes."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from evals.contractguard.dataset import load_dataset
from evals.contractguard.demo import run_demo
from evals.contractguard.smoke import SmokeClient
from scripts.demo_review import main
from waku.config import Settings
from waku.ops.contractguard import collect

FIXTURE = Path(__file__).resolve().parents[1] / 'contractguard/fixtures/demo.json'


def configured(home):
    return Settings(home=home, provider='scripted', model='source-rules-v1', small_model='source-rules-v1',
                    api_key='', base_url=None, otel_endpoint='', retrieval_top_k=4)


@pytest.mark.parametrize('mode,facts,episodes', [('baseline', 0, 0), ('semantic-only', 10, 0), ('full-memory', 10, 15)])
def test_seeded_demo_saves_valid_reports_metrics_and_per_review_growth(tmp_path, mode, facts, episodes):
    runtime = tmp_path / 'personal'
    runtime.mkdir()
    (runtime / 'keep.txt').write_text('keep this data')
    dataset = load_dataset(FIXTURE, split='custom')
    output = []
    result = run_demo(dataset, configured(runtime), tmp_path / 'runs', mode=mode,
                      client_factory=lambda s, m: SmokeClient(), show_memory_growth=True, emit=output.append)
    data = collect(Path(result['home']))
    assert data['errors'] == [] and data['progress']['reviewed'] == 5 and data['progress']['pending'] == 0
    assert data['mode'] == mode and data['execution_kind'] == 'offline_smoke'
    assert len(data['reviews']) == 5 and '# Contract Review Report' in data['report']
    assert data['evaluation']['micro']['f1'] == result['metrics']['micro']['f1'] == 1.0
    assert data['memory_counts'] == {'semantic_entries': facts, 'episodic_entries': episodes, 'procedural_skills': 10}
    assert [s['reviewed'] for s in data['memory_growth']] == [0, 1, 2, 3, 4, 5]
    assert data['memory_growth'][-1]['retrieval_events'] == (0 if mode == 'baseline' else 40)
    assert data['risk_matrix']['Governing Law']['LOW'] == 5
    assert data['risk_matrix']['Uncapped Liability']['NOT_FOUND'] == 4
    assert data['risk_matrix']['Indemnification'] == dict.fromkeys(('HIGH', 'MEDIUM', 'LOW', 'NOT_FOUND', 'ERROR'), 0)
    assert any('live quality is unmeasured' in line for line in output)
    assert sum(line.startswith('Memory:') for line in output) == 5
    assert (runtime / 'keep.txt').read_text() == 'keep this data'
    assert not (runtime / 'state.db').exists()
    repeat = run_demo(dataset, configured(runtime), tmp_path / 'runs', mode=mode,
                      client_factory=lambda s, m: SmokeClient(), emit=lambda line: None)
    assert result['config']['contract_order'] == repeat['config']['contract_order']
    assert result['home'] != repeat['home']


def test_progress_is_visible_before_and_during_a_model_call_without_gold_or_titles(tmp_path):
    client = SmokeClient()
    original = client.messages.create
    snapshots, wire, notices = [], [], []
    home = tmp_path / 'runs/timing/home'

    def create(**kwargs):
        assert any(line.startswith('Dashboard:') for line in notices)
        snapshots.append(collect(home)['progress'])
        wire.append(kwargs)
        return original(**kwargs)

    client.messages.create = create
    dataset = load_dataset(FIXTURE)
    run_demo(dataset, configured(tmp_path / 'unused'), tmp_path / 'runs', contracts=2, run_id='timing',
             client_factory=lambda s, m: client, emit=notices.append)
    assert snapshots[0]['reviewed'] == 0 and snapshots[0]['pending'] == 2
    assert snapshots[0]['current'] is not None and snapshots[0]['status'] == 'running'
    assert any(s['reviewed'] == 1 and s['pending'] == 1 for s in snapshots)
    for call in wire:
        assert 'annotations' not in call['messages'][0]['content']
    assert all(c.title not in call['system'] for c in dataset.contracts for call in wire if 'system' in call)


def test_provider_failures_remain_partial_with_reports_and_no_episodes(tmp_path):
    client = SmokeClient()
    client.messages.create = Mock(side_effect=RuntimeError('private configuration'))
    result = run_demo(load_dataset(FIXTURE), configured(tmp_path / 'unused'), tmp_path / 'runs', contracts=1,
                      client_factory=lambda s, m: client, emit=lambda line: None)
    data = collect(Path(result['home']))
    assert result['config']['status'] == 'partial' and data['progress']['failed'] == 1
    assert data['memory_counts']['episodic_entries'] == 0
    assert data['risk_matrix']['Governing Law']['ERROR'] == 1
    assert data['risk_matrix']['Governing Law']['NOT_FOUND'] == 0
    assert data['evaluation']['extraction_failed_pairs'] == 8
    assert 'private configuration' not in json.dumps(data)


def test_setup_failure_is_recorded_without_erasing_an_existing_run(tmp_path):
    options = {'run_id': 'failure', 'client_factory': Mock(side_effect=RuntimeError('setup failed'))}
    with pytest.raises(RuntimeError):
        run_demo(load_dataset(FIXTURE), configured(tmp_path / 'unused'), tmp_path, **options)
    assert json.loads((tmp_path / 'failure/config.json').read_text())['status'] == 'failed'
    with pytest.raises(FileExistsError):
        run_demo(load_dataset(FIXTURE), configured(tmp_path / 'unused'), tmp_path, **options)


def test_report_write_failure_preserves_the_home_and_stops_current_progress(tmp_path, monkeypatch):
    monkeypatch.setattr('evals.contractguard.demo.save_review', Mock(side_effect=OSError('disk unavailable')))
    with pytest.raises(OSError):
        run_demo(load_dataset(FIXTURE), configured(tmp_path / 'unused'), tmp_path, contracts=2,
                 run_id='interrupted', client_factory=lambda s, m: SmokeClient(), emit=lambda line: None)
    home = tmp_path / 'interrupted/home'
    data = collect(home)
    assert data['progress']['status'] == 'failed' and data['progress']['current'] is None
    assert data['progress']['pending'] == 1 and data['progress']['failed'] == 1
    assert (home / 'state.db').exists()


@pytest.mark.parametrize('options', [{'contracts': 0}, {'contracts': 6}, {'contracts': True}, {'seed': True},
                                     {'mode': 'gold-memory'}, {'execution_kind': 'hidden-live'}])
def test_invalid_demo_options_make_no_model_calls(tmp_path, options):
    client = Mock(side_effect=AssertionError('no work'))
    with pytest.raises(ValueError):
        run_demo(load_dataset(FIXTURE), configured(tmp_path / 'unused'), tmp_path,
                 client_factory=client, **options)
    client.assert_not_called()


def test_cli_is_offline_by_default_and_prints_the_dashboard_home(tmp_path, capsys):
    assert main(['--output', str(tmp_path), '--run-id', 'cli', '--show-memory-growth']) == 0
    text = capsys.readouterr().out
    assert 'Execution kind: offline_smoke' in text and '[5/5]' in text
    assert f'WAKU_HOME={tmp_path}/cli/home' in text and 'Memory:' in text
    assert main(['--output', str(tmp_path), '--run-id', 'quiet', '--contracts', '1']) == 0
    assert 'Memory:' not in capsys.readouterr().out
    with pytest.raises(SystemExit) as error:
        main(['--live'])
    assert error.value.code == 2
