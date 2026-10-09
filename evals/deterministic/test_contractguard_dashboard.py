"""Read-only home boundaries, validated artifacts and dashboard projections."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from unittest.mock import Mock

import pytest

from evals.contractguard.dataset import load_dataset
from evals.contractguard.demo import run_demo
from evals.contractguard.smoke import SmokeClient
from hosted.core import policy
from waku.config import Settings
from waku.ops import contractguard, dashboard

FIXTURE = Path(__file__).resolve().parents[1] / 'contractguard/fixtures/demo.json'


@pytest.fixture
def demo(tmp_path):
    settings = Settings(home=tmp_path / 'unused', provider='scripted', model='source-rules-v1',
                        small_model='source-rules-v1', api_key='', base_url=None, otel_endpoint='')
    return run_demo(load_dataset(FIXTURE), settings, tmp_path / 'runs', contracts=2,
                    client_factory=lambda s, m: SmokeClient(), emit=lambda line: None)


def test_empty_home_is_not_created_and_has_no_fabricated_metrics(tmp_path):
    home = tmp_path / 'missing'
    result = contractguard.collect(home)
    assert not home.exists()
    assert result['progress']['reviewed'] == 0 and result['progress']['pending'] is None
    assert result['evaluation'] is None and result['report'] is None and result['memory_growth'] == []


def test_selection_reads_one_validated_report_and_no_source_or_paths(demo):
    home = Path(demo['home'])
    listing = contractguard.collect(home)
    selected = listing['reviews'][-1]['review_id']
    result = contractguard.collect(home, selected)
    assert result['selected_review_id'] == selected and result['report']
    assert 'source_text' not in result and 'artifacts' not in result
    assert demo['home'] not in json.dumps(result)
    assert result['evaluation']['telemetry']['llm_calls'] == demo['metrics']['telemetry']['llm_calls']
    missing = contractguard.collect(home, 'f' * 64)
    assert missing['selection_missing'] and missing['report'] is None


@pytest.mark.parametrize('identity', ['../outside', '/tmp/outside', 'a' * 65, '', 42])
def test_review_selection_never_accepts_a_file_path(tmp_path, identity):
    assert contractguard.collect(tmp_path, identity) == {'error': 'Select a saved review id.'}


@pytest.mark.parametrize('change', ['report', 'source', 'offset', 'artifact', 'document'])
def test_corrupt_reviews_are_omitted_instead_of_becoming_missing_clauses(demo, change):
    home = Path(demo['home'])
    row = demo['progress']['contracts'][0]
    folder = home / 'reviews' / row['review_id']
    result = json.loads((folder / 'result.json').read_text())
    if change == 'report':
        (folder / 'report.md').write_text('fabricated report')
    elif change == 'document':
        document = json.loads((folder / 'document.json').read_text())
        document['text'] += 'added text'
        (folder / 'document.json').write_text(json.dumps(document))
    else:
        found = next(f for f in result['findings'] if f['status'] == 'found')
        if change == 'source':
            found['extraction']['spans'][0]['text'] = 'fabricated evidence'
        elif change == 'offset':
            found['extraction']['spans'][0]['start'] = True
        else:
            result['artifacts']['report'] = '/tmp/outside'
        (folder / 'result.json').write_text(json.dumps(result))
    data = contractguard.collect(home)
    assert len(data['reviews']) == 1 and data['progress']['reviewed'] == 1
    assert data['progress']['failed'] == 1 and data['errors']
    assert data['evaluation'] is None


def test_external_symlinks_cannot_supply_reviews_snapshots_or_memory(tmp_path):
    home, outside = tmp_path / 'home', tmp_path / 'outside'
    home.mkdir()
    outside.mkdir()
    (outside / 'canary.txt').write_text('EXTERNAL_CANARY')
    (home / 'reviews').symlink_to(outside, target_is_directory=True)
    (home / 'contractguard').mkdir()
    (home / 'contractguard/progress.json').symlink_to(outside / 'canary.txt')
    (home / 'state.db').symlink_to(outside / 'canary.txt')
    result = contractguard.collect(home)
    assert result['reviews'] == [] and result['evaluation'] is None
    assert 'EXTERNAL_CANARY' not in json.dumps(result)


def test_a_valid_review_cannot_be_loaded_through_an_external_folder_symlink(demo, tmp_path):
    home = Path(demo['home'])
    row = demo['progress']['contracts'][0]
    folder = home / 'reviews' / row['review_id']
    outside = tmp_path / 'outside-review'
    shutil.move(str(folder), outside)
    folder.symlink_to(outside, target_is_directory=True)
    result = contractguard.collect(home)
    assert len(result['reviews']) == 1
    assert all(r['review_id'] != row['review_id'] for r in result['reviews'])
    assert result['errors']


@pytest.mark.parametrize('level', ['HIGH', 'MEDIUM', 'LOW'])
def test_risk_matrix_uses_saved_labels_instead_of_the_smoke_default(tmp_path, level):
    client = SmokeClient()
    original = client.messages.create
    def create(**kwargs):
        response = original(**kwargs)
        value = json.loads(response.content[0].text)
        if 'risk_level' in value:
            value['risk_level'] = level
            response.content[0].text = json.dumps(value)
        return response
    client.messages.create = create
    settings = Settings(home=tmp_path / 'unused', provider='scripted', model='source-rules-v1',
                        small_model='source-rules-v1', api_key='', base_url=None, otel_endpoint='')
    result = run_demo(load_dataset(FIXTURE), settings, tmp_path / 'runs', contracts=1,
                      client_factory=lambda s, m: client, emit=lambda line: None)
    values = contractguard.collect(Path(result['home']))['risk_matrix']['Governing Law']
    assert values[level] == 1 and sum(values.values()) == 1


def test_native_reviews_survive_relative_artifact_paths_without_demo_metadata(demo, monkeypatch):
    home = Path(demo['home'])
    monkeypatch.chdir(home.parent)
    for folder in (home / 'reviews').iterdir():
        path = folder / 'result.json'
        value = json.loads(path.read_text())
        value['artifacts'] = {k: str(Path(p).relative_to(home.parent)) for k, p in value['artifacts'].items()}
        path.write_text(json.dumps(value))
    (home / 'contractguard/progress.json').unlink()
    data = contractguard.collect(Path('home'))
    assert len(data['reviews']) == 2 and data['errors'] == []
    assert data['progress']['pending'] is None and data['evaluation'] is None


@pytest.mark.parametrize('change', [lambda v: v.update(schema='bad'), lambda v: v.update(mode='gold-memory'),
    lambda v: v.update(current_contract_id='bad'), lambda v: v['memory_growth'][0].update(reviewed=1),
    lambda v: v['memory_growth'][0].update(semantic_entries=True)])
def test_malformed_progress_does_not_break_valid_native_reports(demo, change):
    home = Path(demo['home'])
    path = home / 'contractguard/progress.json'
    value = json.loads(path.read_text())
    change(value)
    path.write_text(json.dumps(value))
    result = contractguard.collect(home)
    assert len(result['reviews']) == 2 and result['evaluation'] is None and result['errors']


@pytest.mark.parametrize('change', [lambda v: v.update(contracts=999), lambda v: v['micro'].update(f1=2),
    lambda v: v['telemetry'].update(llm_calls=True), lambda v: v['matcher'].update(threshold=-1)])
def test_invalid_metrics_do_not_display_fake_scores(demo, change):
    home = Path(demo['home'])
    path = home / 'contractguard/metrics.json'
    value = json.loads(path.read_text())
    change(value)
    path.write_text(json.dumps(value))
    result = contractguard.collect(home)
    assert result['evaluation'] is None and result['errors']


def test_ready_journals_are_not_counted_as_completed_reviews(demo):
    home = Path(demo['home'])
    (home / 'contractguard/progress.json').unlink()
    folder = next((home / 'reviews').iterdir())
    path = folder / 'result.json'
    value = json.loads(path.read_text())
    value['status'] = 'ready'
    path.write_text(json.dumps(value))
    result = contractguard.collect(home)
    assert result['progress']['total'] == 2 and result['progress']['reviewed'] == 1
    assert result['errors'] == []


def test_get_route_projects_the_configured_home_and_has_tenant_policy(demo, monkeypatch):
    home = Path(demo['home'])
    monkeypatch.setattr(dashboard, 'load_settings', lambda: Settings(home=home))
    monkeypatch.setattr(dashboard, 'get_agent', Mock(side_effect=AssertionError('no model')))
    handler = object.__new__(dashboard.Handler)
    handler.path = '/api/contractguard'
    sent = []
    handler._send = lambda data, kind, **kwargs: sent.append((json.loads(data), kind, kwargs))
    handler.do_GET()
    assert sent[0][0]['progress']['reviewed'] == 2 and sent[0][1] == 'application/json'
    assert sent[0][2]['no_cache'] is True
    handler.path = '/api/contractguard?review_id=..%2Foutside'
    handler.do_GET()
    assert sent[-1][0] == {'error': 'Select a saved review id.'}
    assert policy.DECISIONS['/api/contractguard'] == policy.PASS
