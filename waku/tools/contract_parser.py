"""Deterministic plain-text segmentation with unchanged source and character offsets."""

from __future__ import annotations

import hashlib
import re

_HEADING = re.compile(
    r'^(?:#{1,6}\s+\S.*|(?:\d+(?:\.\d+)*[.)]?|ARTICLE\s+[IVXLC\d]+[.:]?)\s+\S.*'
    r'|[A-Z][A-Z\d &/():-]{2,79})$'
)
_BLANK_LINE = re.compile(r'(?:\r\n|\r|\n)[ \t]*(?:\r\n|\r|\n)')


def parse_contract(text: str, contract_name: str = 'Untitled contract') -> dict:
    if not isinstance(text, str):
        raise TypeError('contract text must be a string')
    if not isinstance(contract_name, str) or not contract_name.strip() or len(contract_name) > 200:
        raise ValueError('contract name must contain 1 to 200 characters')
    document_id = hashlib.sha256(text.encode('utf-8')).hexdigest()
    boundaries = []
    offset = 0
    for line in text.splitlines(keepends=True):
        title = line.strip()
        if _HEADING.fullmatch(title):
            boundaries.append((offset, title))
        offset += len(line)
    if text.strip() and (not boundaries or boundaries[0][0] != 0):
        boundaries.insert(0, (0, 'Preamble' if boundaries else 'Contract'))
    sections, paragraphs = [], []
    for index, (start, title) in enumerate(boundaries):
        end = boundaries[index + 1][0] if index + 1 < len(boundaries) else len(text)
        section_id = f'section-{start}-{end}'
        sections.append({'section_id': section_id, 'title': title, 'start': start,
                         'end': end, 'text': text[start:end]})
        cursor = start
        separators = [(start + m.start(), start + m.end())
                      for m in _BLANK_LINE.finditer(text[start:end])]
        for boundary_start, boundary_end in [*separators, (end, end)]:
            raw = text[cursor:boundary_start]
            p_start = cursor + len(raw) - len(raw.lstrip())
            p_end = cursor + len(raw.rstrip())
            if p_start < p_end:
                paragraphs.append({'paragraph_id': f'paragraph-{p_start}-{p_end}',
                                   'section_id': section_id, 'start': p_start, 'end': p_end,
                                   'text': text[p_start:p_end]})
            cursor = boundary_end
    return {'schema': 'contractguard_document_v1', 'document_id': document_id, 'text': text,
            'metadata': {'name': contract_name, 'characters': len(text),
                         'sections': len(sections), 'paragraphs': len(paragraphs)},
            'sections': sections, 'paragraphs': paragraphs}
