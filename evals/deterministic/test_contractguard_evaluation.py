"""Offline dataset integrity, frozen span matching and extraction metric accounting."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from evals.contractguard.dataset import CATEGORY_MAP, Contract, load_dataset
from evals.contractguard.matching import match_spans, normalized, policy
from evals.contractguard.metrics import extraction_metrics, scores

FIXTURES = Path(__file__).resolve().parents[1] / 'contractguard/fixtures'


def span(source, start, end):
    return {'text': source[start:end], 'start': start, 'end': end}


def write_fixture(tmp_path, mutate=None):
    data = json.loads((FIXTURES / 'smoke.json').read_text())
    if mutate:
        mutate(data)
    path = tmp_path / 'test.json'
    path.write_text(json.dumps(data))
    return path


def test_loader_scores_eight_targets_without_fabricating_two_missing_categories():
    dataset = load_dataset(FIXTURES / 'smoke.json', split='custom')
    assert len(dataset.contracts) == 2 and len(dataset.targets) == 8
    assert dataset.metadata['unscored_targets'] == ['Indemnification', 'Confidentiality Obligations']
    assert dataset.metadata['kind'] == 'custom_squad'
    assert dataset.metadata['sha256'] == hashlib.sha256((FIXTURES / 'smoke.json').read_bytes()).hexdigest()
    contract = dataset.contracts[0]
    assert contract.contract_id == hashlib.sha256(contract.text.encode()).hexdigest()
    assert set(contract.annotations) == set(CATEGORY_MAP)
    assert contract.annotations['Governing Law'][0]['text'] == 'This agreement is governed by California law.'
    assert contract.annotations['Uncapped Liability'] == []
    assert not hasattr(contract.review_input(), 'annotations')


@pytest.mark.parametrize('target', list(CATEGORY_MAP))
def test_loader_canonicalizes_actual_cuad_question_suffixes(target):
    dataset = load_dataset(FIXTURES / 'smoke.json', [target])
    assert dataset.targets == (target,)
    assert set(dataset.contracts[0].annotations) == {target}


def test_loader_keeps_offsets_for_crlf_unicode_and_repeated_text(tmp_path):
    data = {'data': [{'title': 'Unicode', 'paragraphs': [{
        'context': '前言\r\nlaw law', 'qas': [{'id': 'Unicode__Governing Law',
            'question': 'Review "Governing Law".', 'is_impossible': False,
            'answers': [{'text': 'law', 'answer_start': 8}, {'text': 'law', 'answer_start': 8}]}]}]}]}
    path = tmp_path / 'unicode.json'
    path.write_text(json.dumps(data))
    dataset = load_dataset(path, ['Governing Law'])
    assert dataset.contracts[0].text == '前言\r\nlaw law'
    assert dataset.contracts[0].annotations['Governing Law'] == [{'text': 'law', 'start': 8, 'end': 11}]


@pytest.mark.parametrize('change', [
    lambda d: d.update(data=[]),
    lambda d: d['data'][0]['paragraphs'][0]['qas'].pop(),
    lambda d: d['data'][0]['paragraphs'][0].update(context=''),
    lambda d: d['data'][0]['paragraphs'][0]['qas'][0].update(is_impossible='true'),
    lambda d: d['data'][0]['paragraphs'][0]['qas'][0].update(is_impossible=False),
    lambda d: d['data'][0]['paragraphs'][0]['qas'][0].update(question='Wrong "Governing Law" label'),
    lambda d: d['data'][0]['paragraphs'][0]['qas'].append(copy.deepcopy(d['data'][0]['paragraphs'][0]['qas'][0])),
    lambda d: d['data'].append(copy.deepcopy(d['data'][0])),
    lambda d: d['data'][1].update(paragraphs=copy.deepcopy(d['data'][0]['paragraphs'])),
    lambda d: d['data'][0]['paragraphs'].append(copy.deepcopy(d['data'][1]['paragraphs'][0])),
])
def test_loader_rejects_missing_coverage_bad_flags_and_duplicate_or_fragmented_contracts(tmp_path, change):
    with pytest.raises(ValueError):
        load_dataset(write_fixture(tmp_path, change))


@pytest.mark.parametrize('change', [
    lambda a: a.update(text='invented evidence'), lambda a: a.update(answer_start=True),
    lambda a: a.update(answer_start=-1), lambda a: a.update(answer_start=1),
    lambda a: a.update(extra='field'), lambda a: a.update(text=''),
])
def test_loader_rejects_ungrounded_gold_instead_of_repairing_offsets(tmp_path, change):
    def modify(data):
        qa = next(q for q in data['data'][0]['paragraphs'][0]['qas'] if q['answers'])
        change(qa['answers'][0])
    with pytest.raises(ValueError):
        load_dataset(write_fixture(tmp_path, modify))


@pytest.mark.parametrize('targets', [[], ['Indemnification'], ['Confidentiality Obligations'],
                                      ['Governing Law', 'Governing Law'], ['Unknown']])
def test_loader_rejects_unscorable_or_invalid_selections(targets):
    with pytest.raises(ValueError):
        load_dataset(FIXTURES / 'smoke.json', targets)


def test_loader_can_merge_identical_context_paragraphs_without_offset_rewriting(tmp_path):
    def modify(data):
        paragraph = data['data'][0]['paragraphs'][0]
        first, rest = paragraph['qas'][:3], paragraph['qas'][3:]
        paragraph['qas'] = first
        data['data'][0]['paragraphs'].append({'context': paragraph['context'], 'qas': rest})
    assert len(load_dataset(write_fixture(tmp_path, modify)).targets) == 8


def test_character_iou_uses_the_fixed_boundary_and_one_to_one_counts():
    source = 'abcdefghijabcdefghij'
    gold = [span(source, 0, 10)]
    assert match_spans([span(source, 0, 5)], gold, source)['tp'] == 1
    result = match_spans([span(source, 0, 4)], gold, source)
    assert (result['tp'], result['fp'], result['fn']) == (0, 1, 1)
    assert match_spans([span(source, 10, 20)], gold, source)['tp'] == 0
    assert policy()['threshold'] == 0.5


def test_maximum_matching_avoids_a_greedy_miss():
    source = 'abcdefghij'
    result = match_spans([span(source, 0, 10), span(source, 5, 10)],
                         [span(source, 0, 10), span(source, 0, 5)], source)
    assert (result['tp'], result['fp'], result['fn']) == (2, 0, 0)
    assert len({p['predicted'] for p in result['pairs']}) == 2
    assert len({p['gold'] for p in result['pairs']}) == 2


def test_duplicates_count_once_but_repeated_locations_remain_distinct():
    source = 'law law'
    left, right = span(source, 0, 3), span(source, 4, 7)
    result = match_spans([left, left], [left, right, right], source)
    assert (result['tp'], result['fp'], result['fn']) == (1, 0, 1)
    assert match_spans([right, left], [left, right], source) == match_spans([left, right], [right, left], source)


@pytest.mark.parametrize('rule', ['exact', 'normalized', 'token-iou', 'character-iou'])
def test_same_words_at_another_location_do_not_match(rule):
    source = 'law law'
    assert match_spans([span(source, 0, 3)], [span(source, 4, 7)], source, rule)['tp'] == 0


def test_normalized_and_token_policies_are_explicit_alternatives():
    source = '  CALIFORNIA, law. '
    outer, inner = span(source, 0, len(source)), span(source, 2, len(source) - 2)
    assert normalized('Ｋ Law—choice') == 'k law choice'
    assert match_spans([outer], [inner], source, 'exact')['tp'] == 0
    assert match_spans([outer], [inner], source, 'normalized')['tp'] == 1
    assert match_spans([outer], [inner], source, 'token-iou')['tp'] == 1
    assert match_spans([span('!!!', 0, 3)], [span('!!!', 0, 3)], '!!!', 'token-iou')['tp'] == 0


@pytest.mark.parametrize('bad', [{'text': 'invented', 'start': 0, 'end': 8},
                                 {'text': 'a', 'start': True, 'end': 2},
                                 {'text': '', 'start': 0, 'end': 0}])
def test_matcher_requires_grounded_spans(bad):
    with pytest.raises(ValueError):
        match_spans([bad], [], 'abcdefghij')


def item(source, target, spans=(), *, failed=False, risk_failed=False):
    extraction = {'clause_type': target, 'found': bool(spans), 'spans': list(spans),
                  'confidence': 0.9, 'rationale': 'Scripted current source result.'}
    risk = {'clause_type': target, 'risk_level': 'LOW', 'reasoning': 'Scripted reasoning.',
            'recommendation': 'Read source carefully.', 'evidence_indices': [0]} if spans else None
    error = {'stage': 'extraction' if failed else 'risk', 'message': 'Failed stage.'} if failed or risk_failed else None
    return {'clause_type': target, 'status': 'error' if error else 'found' if spans else 'not_found',
            'extraction': None if failed else extraction, 'risk': None if error else risk,
            'comparison': None, 'memory_used': {'semantic': [], 'episodic': []}, 'error': error}


def test_micro_macro_multiple_spans_and_critical_misses_have_distinct_counts():
    targets = ['Governing Law', 'Uncapped Liability']
    a, b = 'abcdefghij', 'klmnopqrst'
    contracts = [Contract('a', 'A', a, {targets[0]: [span(a, 0, 4)], targets[1]: [span(a, 5, 9)]}),
                 Contract('b', 'B', b, {targets[0]: [span(b, 0, 4)], targets[1]: []})]
    predictions = [{'contract_id': 'a', 'findings': [item(a, targets[0], [span(a, 0, 4)]), item(a, targets[1])]},
                   {'contract_id': 'b', 'findings': [item(b, targets[0], [span(b, 0, 4), span(b, 5, 9)]), item(b, targets[1])]}]
    result = extraction_metrics(contracts, predictions, targets)
    assert {k: result['micro'][k] for k in ('tp', 'fp', 'fn')} == {'tp': 2, 'fp': 1, 'fn': 1}
    assert result['micro']['f1'] == pytest.approx(2 / 3)
    assert result['macro']['f1'] == pytest.approx(0.4)
    assert result['per_clause']['Governing Law']['f1'] == pytest.approx(0.8)
    assert result['critical']['miss_rate'] == 1.0
    assert result['risk_accuracy'] is None


def test_extraction_failures_are_not_negatives_and_risk_failures_keep_valid_extraction():
    source, target = 'abcdefghij', 'Uncapped Liability'
    contracts = [Contract('a', 'A', source, {target: []})]
    result = extraction_metrics(contracts, [{'contract_id': 'a', 'findings': [item(source, target, failed=True)]}], [target])
    assert result['extraction_failed_pairs'] == 1 and result['per_clause'][target]['negative_pairs'] == 0
    contracts = [Contract('a', 'A', source, {target: [span(source, 0, 10)]})]
    result = extraction_metrics(contracts, [{'contract_id': 'a', 'findings': [item(source, target, [span(source, 0, 10)], risk_failed=True)]}], [target])
    assert result['micro']['f1'] == 1.0 and result['risk_failed_pairs'] == 1
    assert result['extraction_failed_pairs'] == 0


def test_zero_support_is_zero_and_predictions_must_align_with_contracts():
    assert scores(0, 0, 0) == {'precision': 0.0, 'recall': 0.0, 'f1': 0.0}
    source, target = 'abcdefghij', 'Governing Law'
    contracts = [Contract('a', 'A', source, {target: []})]
    prediction = {'contract_id': 'wrong', 'findings': [item(source, target)]}
    with pytest.raises(ValueError):
        extraction_metrics(contracts, [prediction], [target])
    with pytest.raises(ValueError):
        extraction_metrics(contracts, [], [target])
    with pytest.raises(ValueError):
        match_spans([], [], source, 'tuned-threshold')
