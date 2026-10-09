"""Normalize local CUAD SQuAD JSON while keeping annotations off the prediction path."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from waku.memory.contractguard import CLAUSES
from waku.tools.contract_schema import clause_name, load_json

CUAD_REVISION = '67faa0e6023b04fcaae6cc09497ab00e5d63a2a2'
OFFICIAL_TEST_SHA256 = '007b6a40b0c65247f881627375c3d2e9b6aeeb5dfa89957494feed0765a1a073'
CATEGORY_MAP = {
    'Termination for Convenience': 'Termination For Convenience',
    'Uncapped Liability': 'Uncapped Liability',
    'Cap on Liability': 'Cap On Liability',
    'IP Ownership Assignment': 'Ip Ownership Assignment',
    'Non-Compete': 'Non-Compete', 'Change of Control': 'Change Of Control',
    'Governing Law': 'Governing Law', 'Exclusivity': 'Exclusivity',
}


@dataclass(frozen=True)
class ReviewInput:
    contract_id: str
    text: str


@dataclass(frozen=True)
class Contract:
    contract_id: str
    title: str
    text: str
    annotations: dict[str, list[dict]]

    def review_input(self) -> ReviewInput:
        return ReviewInput(self.contract_id, self.text)


@dataclass(frozen=True)
class Dataset:
    contracts: tuple[Contract, ...]
    targets: tuple[str, ...]
    metadata: dict


def validate_span(span: dict, source: str) -> None:
    # Gold may contain more or longer spans than the production output budget.
    if (not isinstance(span, dict) or span.keys() != {'text', 'start', 'end'}
            or not isinstance(span['text'], str) or not span['text'].strip()
            or type(span['start']) is not int or type(span['end']) is not int
            or not 0 <= span['start'] < span['end'] <= len(source)
            or source[span['start']:span['end']] != span['text']):
        raise ValueError('annotation does not quote source at valid character offsets')


def unique_spans(spans: list[dict], source: str) -> list[dict]:
    if not isinstance(spans, list):
        raise TypeError('spans must be a list')
    found = {}
    for span in spans:
        validate_span(span, source)
        found[(span['start'], span['end'])] = dict(span)
    return [found[key] for key in sorted(found)]


def target_names(targets=None) -> tuple[str, ...]:
    names = tuple(c.name for c in CLAUSES) if targets is None else tuple(targets)
    if not 1 <= len(names) <= 10:
        raise ValueError('select one to ten canonical targets')
    for name in names:
        clause_name(name)
    if len(set(names)) != len(names):
        raise ValueError('duplicate target category')
    return names


def load_dataset(path: Path, targets=None, *, split: str = 'test') -> Dataset:
    requested = target_names(targets)
    scored = tuple(name for name in requested if name in CATEGORY_MAP)
    if not scored:
        raise ValueError('selected targets have no standalone CUAD annotation category')
    if split not in ('test', 'train', 'custom'):
        raise ValueError('split must be test, train or custom')
    raw = Path(path).read_bytes()
    checksum = hashlib.sha256(raw).hexdigest()
    if checksum == OFFICIAL_TEST_SHA256 and split != 'test':
        raise ValueError('official test file cannot be labelled another split')
    payload = load_json(raw.decode('utf-8-sig'))
    if not isinstance(payload, dict) or not isinstance(payload.get('data'), list) or not payload['data']:
        raise ValueError('dataset must contain nonempty SQuAD data')
    categories = {label.casefold(): name for name, label in CATEGORY_MAP.items() if name in scored}
    contracts, seen_titles, seen_ids = [], set(), set()
    for document in payload['data']:
        if (not isinstance(document, dict) or not isinstance(document.get('title'), str)
                or not document['title'].strip() or document['title'] in seen_titles
                or not isinstance(document.get('paragraphs'), list) or not document['paragraphs']):
            raise ValueError('each document needs a unique title and nonempty paragraphs')
        text, annotations, coverage, qa_ids = None, {name: [] for name in scored}, set(), set()
        for paragraph in document['paragraphs']:
            if (not isinstance(paragraph, dict) or not isinstance(paragraph.get('context'), str)
                    or not paragraph['context'].strip() or not isinstance(paragraph.get('qas'), list)):
                raise ValueError('each paragraph needs complete source text and questions')
            if text is not None and text != paragraph['context']:
                raise ValueError('different contexts within a document cannot preserve full-text offsets')
            text = paragraph['context']
            for qa in paragraph['qas']:
                if (not isinstance(qa, dict) or not isinstance(qa.get('id'), str)
                        or '__' not in qa['id'] or qa['id'] in qa_ids
                        or not isinstance(qa.get('question'), str)
                        or type(qa.get('is_impossible')) is not bool or not isinstance(qa.get('answers'), list)):
                    raise ValueError('invalid or duplicate CUAD question')
                qa_ids.add(qa['id'])
                label = qa['id'].rsplit('__', 1)[1].casefold()
                quoted = re.search(r'"([^"]+)"', qa['question'])
                if quoted is None or quoted.group(1).casefold() != label:
                    raise ValueError('question label disagrees with its id')
                if qa['is_impossible'] != (not qa['answers']):
                    raise ValueError('impossible flag contradicts annotation presence')
                spans = []
                for answer in qa['answers']:
                    if (not isinstance(answer, dict) or answer.keys() != {'text', 'answer_start'}
                            or not isinstance(answer['text'], str) or type(answer['answer_start']) is not int):
                        raise ValueError('invalid CUAD answer fields')
                    span = {'text': answer['text'], 'start': answer['answer_start'],
                            'end': answer['answer_start'] + len(answer['text'])}
                    validate_span(span, text)
                    spans.append(span)
                if label in categories:
                    name = categories[label]
                    coverage.add(name)
                    annotations[name].extend(spans)
        if coverage != set(scored):
            raise ValueError('document is missing a scored annotation category')
        contract_id = hashlib.sha256(text.encode('utf-8')).hexdigest()
        if contract_id in seen_ids:
            raise ValueError('duplicate source document could contaminate accumulated history')
        seen_ids.add(contract_id)
        seen_titles.add(document['title'])
        contracts.append(Contract(contract_id, document['title'], text,
                                  {name: unique_spans(spans, text) for name, spans in annotations.items()}))
    return Dataset(tuple(contracts), scored, {
        'path': str(Path(path).resolve()), 'sha256': checksum, 'split': split,
        'kind': 'cuad_official_test' if checksum == OFFICIAL_TEST_SHA256 else 'custom_squad',
        'upstream_revision': CUAD_REVISION if checksum == OFFICIAL_TEST_SHA256 else None,
        'version': payload.get('version'), 'contracts': len(contracts),
        'requested_targets': list(requested), 'scored_targets': list(scored),
        'unscored_targets': [name for name in requested if name not in CATEGORY_MAP],
    })
