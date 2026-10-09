"""Labelled offline stand-in that reads source text, never dataset annotations."""

from __future__ import annotations

import json
import re
from types import SimpleNamespace


class SmokeClient:
    def __init__(self):
        self.messages = SimpleNamespace(create=self.create)

    def create(self, *, messages, system='', **kwargs):
        content = messages[0]['content']
        payload = json.loads(content.split('User message: ', 1)[1] if not system else content)
        if 'memory' not in payload and 'source_text' not in payload:
            raise ValueError('unsupported smoke request')
        if 'extraction' in payload:
            extraction = payload['extraction']
            result = {'clause_type': extraction['clause_type'], 'risk_level': 'LOW',
                      'reasoning': 'The scripted smoke stand-in assigns LOW to every detected clause.',
                      'recommendation': 'Inspect this authored smoke example manually.',
                      'evidence_indices': [0]}
        elif 'procedure' in payload:
            target = payload['clause_type']
            cues = {'Governing Law': 'governed by', 'Termination for Convenience': 'terminate without cause',
                    'Uncapped Liability': 'unlimited liability', 'Cap on Liability': 'liability is capped',
                    'IP Ownership Assignment': 'intellectual property', 'Non-Compete': 'not compete',
                    'Change of Control': 'change of control', 'Indemnification': 'indemnify',
                    'Confidentiality Obligations': 'confidential information', 'Exclusivity': 'exclusively'}
            text = payload['source_text']
            match = re.search(re.escape(cues[target]), text, re.IGNORECASE)
            quote = ''
            if match:
                start, end = text.rfind('.', 0, match.start()) + 1, text.find('.', match.end())
                quote = text[start:end + 1] if end >= 0 else text[start:]
                quote = quote.strip()
                if len(quote) > 4000:
                    quote = match.group()
            result = {'clause_type': target, 'found': match is not None,
                      'spans': [{'text': quote, 'occurrence': 0}] if match else [],
                      'confidence': 1.0, 'rationale': 'A fixed source-text smoke rule produced this result.'}
        else:
            target = payload['clause_type']
            result = {'semantic': True, 'episodic': True, 'clause_type': target,
                      'query': target, 'reason': 'Exercise both permitted retrieval paths.'}
        return SimpleNamespace(stop_reason='end_turn', content=[SimpleNamespace(type='text', text=json.dumps(result))],
                               usage=SimpleNamespace(input_tokens=0, output_tokens=0))
