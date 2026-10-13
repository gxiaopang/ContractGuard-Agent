"""Dataset-independent cases with explicit coverage and source-only projections."""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass
from pathlib import Path

from evals.contractguard.dataset import ReviewInput, target_names, unique_spans
from waku.memory.contractguard import CLAUSES
from waku.memory.contractguard_context import review_context
from waku.tools.contract_schema import load_json, object_fields

CASE_SCHEMA = 'contractguard_case_v1'
GENERATOR_VERSION = 'contractguard_generated_v1'

# These source templates define extraction truth by construction. They do not
# define commercial risk truth, which needs an independently stated rubric.
TEMPLATES = {
    'Termination for Convenience': 'The Customer may terminate without cause on thirty days notice.',
    'Uncapped Liability': 'The Supplier has unlimited liability for fraud.',
    'Cap on Liability': 'Aggregate liability is capped at the fees paid during twelve months.',
    'IP Ownership Assignment': 'The Supplier assigns intellectual property in the deliverables to the Customer.',
    'Non-Compete': 'The Supplier shall not compete with the Customer for one year.',
    'Change of Control': 'A change of control requires prior written consent.',
    'Governing Law': 'This agreement is governed by the laws of England and Wales.',
    'Indemnification': 'The Supplier shall indemnify the Customer against third party patent claims.',
    'Confidentiality Obligations': 'Each party shall protect confidential information from unauthorized disclosure.',
    'Exclusivity': 'The Customer shall purchase the services exclusively from the Supplier.',
}


@dataclass(frozen=True)
class EvaluationCase:
    case_id: str
    family_id: str
    text: str
    targets: tuple[str, ...]
    context: dict
    reference: dict

    def review_input(self):
        return ReviewInput(hashlib.sha256(self.text.encode()).hexdigest(), self.text,
                           json.loads(json.dumps(self.context)))


def decode_case(value):
    object_fields(value, {'schema', 'id', 'family_id', 'text', 'targets', 'context', 'reference'}, 'evaluation case')
    if value['schema'] != CASE_SCHEMA:
        raise ValueError('unknown evaluation case version')
    for key in ('id', 'family_id', 'text'):
        if not isinstance(value[key], str) or not value[key].strip():
            raise ValueError('cases require explicit identity, family and source')
    targets = target_names(value['targets'])
    review_context(value['context'], scope='0' * 64)
    reference = value['reference']
    object_fields(reference, {'extraction', 'risk', 'rubrics', 'checks', 'critical_targets', 'severity_weights'}, 'reference')
    for key in ('extraction', 'risk', 'rubrics', 'checks', 'severity_weights'):
        if not isinstance(reference[key], dict) or reference[key].keys() - set(targets):
            raise ValueError('reference coverage names unsupported targets')
    for name, spans in reference['extraction'].items():
        reference['extraction'][name] = unique_spans(spans, value['text'])
    for name, level in reference['risk'].items():
        if level not in ('HIGH', 'MEDIUM', 'LOW') or not reference['rubrics'].get(name) or not value['context'].get('role'):
            raise ValueError('risk references require a role and explicit rubric')
    if not isinstance(reference['critical_targets'], list) or set(reference['critical_targets']) - set(targets):
        raise ValueError('critical targets must belong to the requested targets')
    for name, weight in reference['severity_weights'].items():
        if type(weight) not in (int, float) or not 0 < weight <= 100:
            raise ValueError('severity weights must be positive and bounded')
    for checks in reference['checks'].values():
        if not isinstance(checks, list) or any(not isinstance(c, str) or not c.strip() for c in checks):
            raise ValueError('required checks must be explicit text')
    return EvaluationCase(value['id'], value['family_id'], value['text'], targets,
                          json.loads(json.dumps(value['context'])), json.loads(json.dumps(reference)))


def load_cases(path: Path):
    raw = path.read_bytes()
    values = load_json(raw.decode('utf-8'))
    if not isinstance(values, list) or not values:
        raise ValueError('case file must contain a nonempty case array')
    cases = [decode_case(v) for v in values]
    if len({c.case_id for c in cases}) != len(cases) or len({c.review_input().contract_id for c in cases}) != len(cases):
        raise ValueError('cases require unique ids and source texts')
    return cases, {'kind': 'local-cases', 'sha256': hashlib.sha256(raw).hexdigest(), 'path': str(path.resolve())}


def generated_cases(seed=42):
    if type(seed) is not int:
        raise ValueError('generator seed must be an integer')
    randomizer = random.Random(seed)
    cases = []
    for index, clause in enumerate(CLAUSES):
        for variant in ('positive', 'negative', 'unscored', 'formatting'):
            quote = TEMPLATES[clause.name]
            body = quote if variant in ('positive', 'formatting') else 'The parties shall exchange monthly progress reports.'
            prefix = f'Agreement {randomizer.randrange(10**8)} between Company {index} and its counterparty.\n'
            text = prefix + ('\n\n' if variant == 'formatting' else '\n') + body
            spans = [{'text': quote, 'start': text.index(quote), 'end': text.index(quote) + len(quote)}] if quote in text else []
            extraction = {} if variant == 'unscored' else {clause.name: spans}
            value = {'schema': CASE_SCHEMA, 'id': f'{index}-{variant}',
                     'family_id': f'generated-{index}', 'text': text, 'targets': [clause.name],
                     'context': {'role': 'supplier', 'policy_version': 'generated-policy-v1', 'review_date': '2026-10-13'},
                     'reference': {'extraction': extraction, 'risk': {}, 'rubrics': {}, 'checks': {},
                                   'critical_targets': [clause.name], 'severity_weights': {clause.name: 1}}}
            cases.append(decode_case(value))
    checksum = hashlib.sha256(json.dumps([c.__dict__ for c in cases], sort_keys=True).encode()).hexdigest()
    return cases, {'kind': 'generated', 'generator': GENERATOR_VERSION, 'seed': seed,
                   'sha256': checksum, 'reference_origin': 'source-template-construction',
                   'risk_reference': 'unscored', 'held_out_claim': False}
