"""Offline segmentation, closed stage schemas and source-grounded report rendering."""

from __future__ import annotations

import copy
import json

import pytest

from evals.helpers import ScriptedClient, response, text_block
from waku.tools.clause_differ import compare_clause
from waku.tools.clause_extractor import extract_clause, locate_quote
from waku.tools.contract_parser import parse_contract
from waku.tools.contract_schema import (
    DIFFERENCE_FIELDS,
    call_json,
    load_json,
    validate_comparison,
    validate_extraction,
    validate_findings,
    validate_risk,
)
from waku.tools.report_generator import generate_report
from waku.tools.risk_scorer import score_risk

SOURCE = '前言\r\nThis agreement is governed by California law.\r\nEnd.'
QUOTE = 'This agreement is governed by California law.'
CLAUSE = 'Governing Law'


def extraction(**overrides):
    return {'clause_type': CLAUSE, 'found': True, 'spans': [locate_quote(SOURCE, QUOTE, 0)],
            'confidence': 0.91, 'rationale': 'The source explicitly selects California law.', **overrides}


def proposal(**overrides):
    return extraction(**{'spans': [{'text': QUOTE, 'occurrence': 0}], **overrides})


def risk(**overrides):
    return {'clause_type': CLAUSE, 'risk_level': 'LOW', 'reasoning': 'The choice is explicit.',
            'recommendation': 'Confirm the chosen jurisdiction suits both parties.',
            'evidence_indices': [0], **overrides}


def comparison(**overrides):
    return {'clause_type': CLAUSE, **{field: [] for field in DIFFERENCE_FIELDS}, **overrides}


def finding(**overrides):
    return {'clause_type': CLAUSE, 'status': 'found', 'extraction': extraction(), 'risk': risk(),
            'comparison': None, 'memory_used': {'semantic': [], 'episodic': []}, 'error': None,
            **overrides}


def model(*values):
    return ScriptedClient([response([text_block(json.dumps(v))]) for v in values])


@pytest.mark.parametrize('source', ['', ' \r\n\t', 'one paragraph', SOURCE,
                                   'Intro\n\n1. Liability\nA\n\n2. Law\nB',
                                   '# TERMS\r\nFirst\r\n\r\n# TERMS\r\nSecond',
                                   'ARTICLE IV: LIABILITY\rA\r\rCONFIDENTIALITY\rB'])
def test_parser_preserves_source_unique_ids_and_stable_character_offsets(source):
    document = parse_contract(source)
    assert document == parse_contract(source)
    assert document['text'] == source
    for key, id_key in [('sections', 'section_id'), ('paragraphs', 'paragraph_id')]:
        records = document[key]
        assert len({r[id_key] for r in records}) == len(records)
        for record in records:
            assert source[record['start']:record['end']] == record['text']
            assert record['start'] < record['end']
    assert ''.join(s['text'] for s in document['sections']) == (source if source.strip() else '')


def test_parser_handles_repeated_headings_and_document_identity():
    source = 'Intro\n\n# LAW\nA\n\n# LAW\nB'
    document = parse_contract(source, 'Agreement')
    assert [s['title'] for s in document['sections']] == ['Preamble', '# LAW', '# LAW']
    assert document['metadata']['paragraphs'] == 3
    assert parse_contract(source, 'Renamed')['document_id'] == document['document_id']
    assert parse_contract(source + ' ')['document_id'] != document['document_id']


@pytest.mark.parametrize('bad', [None, 1, [], {}])
def test_parser_rejects_non_text(bad):
    with pytest.raises(TypeError):
        parse_contract(bad)


def test_quote_locator_handles_repetition_overlaps_and_unicode():
    assert locate_quote('前言 aaa aaa', 'aaa', 1) == {'text': 'aaa', 'start': 7, 'end': 10}
    assert locate_quote('aaaa', 'aa', 2) == {'text': 'aa', 'start': 2, 'end': 4}


@pytest.mark.parametrize('quote,occurrence', [('invented', 0), (QUOTE, 1), (QUOTE, True),
                                           (QUOTE, -1), ('', 0), (QUOTE, '0')])
def test_quote_locator_rejects_ungrounded_or_invalid_proposals(quote, occurrence):
    with pytest.raises(ValueError):
        locate_quote(SOURCE, quote, occurrence)


def test_extractor_computes_real_offsets_and_returns_evaluator_ready_records():
    result = extract_clause(model(proposal()), 'offline', SOURCE, CLAUSE)
    assert result == extraction()
    assert result['spans'][0]['start'] == 4
    assert 'occurrence' not in result['spans'][0]
    absent = extract_clause(model(proposal(found=False, spans=[])), 'offline', SOURCE, CLAUSE)
    assert absent['found'] is False and absent['spans'] == []


@pytest.mark.parametrize('overrides', [
    {'found': True, 'spans': []}, {'found': False}, {'found': 1}, {'confidence': True},
    {'confidence': -0.01}, {'confidence': 1.01}, {'confidence': float('inf')},
    {'confidence': 10**500}, {'confidence': 'high'}, {'rationale': ''},
    {'clause_type': 'Law'}, {'surprise': 'field'},
    {'spans': [{'text': 'invented', 'start': 0, 'end': 8}]},
    {'spans': [{'text': QUOTE, 'start': True, 'end': len(QUOTE) + 1}]},
    {'spans': [locate_quote(SOURCE, QUOTE, 0)] * 2},
])
def test_extraction_schema_rejects_invalid_or_unbacked_findings(overrides):
    with pytest.raises(ValueError):
        validate_extraction(extraction(**overrides), SOURCE)


@pytest.mark.parametrize('payload', ['{"found":true,"found":false}', '{"x":NaN}',
                                   '{"x":Infinity}', '```json\n{}\n```'])
def test_json_rejects_ambiguous_and_non_json_answers(payload):
    with pytest.raises(ValueError):
        load_json(payload)


@pytest.mark.parametrize('blocks,stop', [([text_block('{}')], 'max_tokens'),
                                      ([text_block('[]')], 'end_turn'),
                                      ([text_block('invalid')], 'end_turn')])
def test_structured_call_rejects_truncated_or_non_object_output(blocks, stop):
    with pytest.raises((ValueError, TypeError)):
        call_json(ScriptedClient([response(blocks, stop)]), 'offline', 'JSON only', {})


@pytest.mark.parametrize('level', ['HIGH', 'MEDIUM', 'LOW'])
def test_risk_is_a_separate_assessment_not_derived_from_presence(level):
    assert score_risk(model(risk(risk_level=level)), 'offline', extraction(), SOURCE)['risk_level'] == level


@pytest.mark.parametrize('overrides', [{'risk_level': 'CRITICAL'}, {'risk_level': True},
                                      {'reasoning': ''}, {'recommendation': ''},
                                      {'evidence_indices': []}, {'evidence_indices': [1]},
                                      {'evidence_indices': [True]}, {'evidence_indices': [0, 0]},
                                      {'clause_type': 'Indemnification'}, {'label': 'present'}])
def test_risk_rejects_wrong_category_invalid_label_and_unbacked_references(overrides):
    with pytest.raises(ValueError):
        validate_risk(risk(**overrides), extraction(), SOURCE)


def test_negative_findings_cannot_be_assessed_or_compared():
    absent = extraction(found=False, spans=[])
    with pytest.raises(ValueError):
        score_risk(model(), 'offline', absent, SOURCE)
    with pytest.raises(ValueError):
        compare_clause(model(), 'offline', absent, SOURCE, 'Select applicable law.')


def test_comparison_requires_both_source_refs_and_actual_guidance_quotes():
    guidance = 'Select applicable law. Check jurisdiction suitability.'
    entry = {'description': 'The clause supplies a governing law but needs suitability review.',
             'evidence_indices': [0], 'guidance_quote': 'Check jurisdiction suitability.'}
    expected = comparison(risk_relevant_deviations=[entry])
    assert compare_clause(model(expected), 'offline', extraction(), SOURCE, guidance) == expected
    for overrides in [{'guidance_quote': 'Invented standard'}, {'evidence_indices': [2]},
                      {'description': ''}, {'extra': 'field'}]:
        bad = comparison(material_differences=[{**entry, **overrides}])
        with pytest.raises(ValueError):
            validate_comparison(bad, extraction(), SOURCE, guidance)


def test_report_aggregates_structured_positive_negative_and_failed_results():
    absent = finding(clause_type='Indemnification', status='not_found', risk=None,
                     extraction=extraction(clause_type='Indemnification', found=False, spans=[]))
    failed = finding(clause_type='Exclusivity', status='error', risk=None, extraction=None,
                     error={'stage': 'extraction', 'message': 'Invalid JSON.'})
    report = generate_report(SOURCE, 'Agreement', [finding(), absent, failed],
                             [CLAUSE, 'Indemnification', 'Exclusivity'])
    assert 'Review status: PARTIAL.' in report
    assert 'Detected: 1. Not found: 1. Failed: 1.' in report
    assert '| LOW | 1 |' in report and '| HIGH | 0 |' in report
    assert f'> {QUOTE}' in report and 'characters [4, 49)' in report
    assert report.count('### ') == 3
    assert 'Status: NOT FOUND.' in report and 'ERROR during extraction' in report


def test_report_escapes_source_and_model_prose_and_rejects_invented_evidence():
    source = '# Fake finding\n<script>alert(1)</script>'
    item = finding(extraction=extraction(spans=[locate_quote(source, source, 0)]),
                   risk=risk(reasoning='# Invented heading\n<script>bad</script>'))
    report = generate_report(source, '# Another finding', [item], [CLAUSE])
    assert '\n# Fake finding' not in report and '\n# Invented heading' not in report
    assert '<script>' not in report
    assert '> \\# Fake finding' in report
    corrupted = copy.deepcopy(item)
    corrupted['extraction']['spans'][0]['text'] = 'x' * len(source)
    with pytest.raises(ValueError):
        generate_report(source, 'Agreement', [corrupted], [CLAUSE])


@pytest.mark.parametrize('overrides', [{'status': 'not_found'}, {'risk': None},
                                      {'error': {'stage': 'risk', 'message': 'Failed'}},
                                      {'extra': True}, {'clause_type': 'Indemnification'}])
def test_aggregation_rejects_states_that_contradict_extraction(overrides):
    with pytest.raises(ValueError):
        validate_findings([finding(**overrides)], SOURCE, [CLAUSE])


def test_risk_failure_retains_evidence_without_assigning_a_label():
    item = finding(status='error', risk=None, error={'stage': 'risk', 'message': 'Failed'})
    report = generate_report(SOURCE, 'Agreement', [item], [CLAUSE])
    assert 'Risk: UNASSESSED.' in report and f'> {QUOTE}' in report
    assert 'Risk: LOW' not in report and '| LOW | 0 |' in report
    assert 'Detected: 1. Not found: 0. Failed: 1.' in report
