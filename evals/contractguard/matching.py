"""Frozen span policies and maximum-cardinality one-to-one matching."""

from __future__ import annotations

import re
import unicodedata

from evals.contractguard.dataset import unique_spans

MATCH_VERSION = 'contractguard_span_v1'
THRESHOLD = 0.5
RULES = ('character-iou', 'token-iou', 'exact', 'normalized')


def normalized(text: str) -> str:
    return ' '.join(re.findall(r'\w+', unicodedata.normalize('NFKC', text).casefold()))


def character_iou(left: dict, right: dict) -> float:
    intersection = max(0, min(left['end'], right['end']) - max(left['start'], right['start']))
    union = left['end'] - left['start'] + right['end'] - right['start'] - intersection
    return intersection / union


def token_iou(left: dict, right: dict, tokens: list[tuple[int, int]]) -> float:
    def covered(span):
        return {i for i, (start, end) in enumerate(tokens) if start < span['end'] and end > span['start']}
    a, b = covered(left), covered(right)
    return len(a & b) / len(a | b) if a or b else 0.0


def match_spans(predicted: list[dict], gold: list[dict], source: str,
                rule: str = 'character-iou') -> dict:
    if rule not in RULES:
        raise ValueError('unknown frozen span rule')
    predicted, gold = unique_spans(predicted, source), unique_spans(gold, source)
    tokens = [(m.start(), m.end()) for m in re.finditer(r'\w+', source)]

    def score(left, right):
        if rule == 'exact':
            return float(left == right)
        if rule == 'normalized':
            text = normalized(left['text'])
            return float(bool(text) and text == normalized(right['text']) and character_iou(left, right) > 0)
        return token_iou(left, right, tokens) if rule == 'token-iou' else character_iou(left, right)

    scores = [[score(p, g) for g in gold] for p in predicted]
    cutoff = THRESHOLD if rule.endswith('iou') else 1.0
    edges = [sorted((j for j, value in enumerate(row) if value >= cutoff),
                    key=lambda j: (-row[j], j)) for row in scores]
    assigned = {}

    def augment(i, visited):
        for j in edges[i]:
            if j in visited:
                continue
            visited.add(j)
            if j not in assigned or augment(assigned[j], visited):
                assigned[j] = i
                return True
        return False

    for i in range(len(predicted)):
        augment(i, set())
    pairs = [{'predicted': i, 'gold': j, 'score': scores[i][j]}
             for j, i in sorted(assigned.items(), key=lambda item: item[1])]
    return {'tp': len(pairs), 'fp': len(predicted) - len(pairs), 'fn': len(gold) - len(pairs),
            'pairs': pairs, 'predicted_count': len(predicted), 'gold_count': len(gold)}


def policy(rule: str = 'character-iou') -> dict:
    if rule not in RULES:
        raise ValueError('unknown frozen span rule')
    return {'version': MATCH_VERSION, 'rule': rule, 'threshold': THRESHOLD if rule.endswith('iou') else 1.0,
            'normalization': 'NFKC-casefold-word-sequence', 'duplicates': 'identical-offsets',
            'assignment': 'maximum-cardinality-one-to-one', 'span_order': 'start-end',
            'offset_unit': 'python-character'}
