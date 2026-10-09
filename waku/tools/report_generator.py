"""Render validated findings deterministically; never ask a model to invent a report."""

from __future__ import annotations

import html
import re
from collections import Counter

from waku.tools.contract_schema import DIFFERENCE_FIELDS, bounded_text, validate_findings


def plain(text: str) -> str:
    return re.sub(r'([\\`*_{}\[\]#!|])', r'\\\1', html.escape(' '.join(text.split()), quote=False))


def generate_report(source_text: str, contract_name: str, findings: list, targets: list[str], *,
                    compare_guidance: bool = True) -> str:
    bounded_text(contract_name, 'contract name', limit=200)
    validate_findings(findings, source_text, targets)
    counts = Counter(f['status'] for f in findings)
    detected = sum(f['extraction'] is not None and f['extraction']['found'] for f in findings)
    risks = Counter(f['risk']['risk_level'] for f in findings if f['risk'] is not None)
    complete = counts['error'] == 0
    lines = ['# Contract Review Report', '', f'Contract: {plain(contract_name)}', '',
             '## Executive Summary', '',
             f"Review status: {'COMPLETED' if complete else 'PARTIAL'}.",
             f"Detected: {detected}. Not found: {counts['not_found']}. Failed: {counts['error']}.",
             '', '## Risk Summary', '', '| Risk | Assessed findings |', '|---|---:|']
    lines.extend(f'| {label} | {risks[label]} |' for label in ('HIGH', 'MEDIUM', 'LOW'))
    lines.extend(['', '## Findings'])
    for item in findings:
        lines.extend(['', f"### {item['clause_type']}", ''])
        extraction, risk = item['extraction'], item['risk']
        if item['status'] == 'error':
            error = item['error']
            lines.append(f"Status: ERROR during {plain(error['stage'])}: {plain(error['message'])}")
        elif item['status'] == 'not_found':
            lines.extend(['Status: NOT FOUND.', '', plain(extraction['rationale'])])
        else:
            lines.append('Status: DETECTED.')
        if extraction is not None and extraction['found']:
            lines.extend(['', 'Evidence:'])
            for index, span in enumerate(extraction['spans']):
                lines.extend(['', f"Evidence {index}: characters [{span['start']}, {span['end']})."])
                # Prefix every line: source text cannot escape its quotation block.
                lines.extend('> ' + plain(line) for line in span['text'].splitlines())
            lines.extend(['', 'Extraction rationale:', plain(extraction['rationale'])])
        if risk is not None:
            refs = ', '.join(str(i) for i in risk['evidence_indices'])
            lines.extend(['', f"Risk: {risk['risk_level']}", f'Risk evidence: {refs}.',
                          '', 'Analysis:', plain(risk['reasoning']), '',
                          'Recommendation:', plain(risk['recommendation'])])
        elif extraction is not None and extraction['found']:
            lines.extend(['', 'Risk: UNASSESSED.'])
        if item['comparison'] is not None:
            lines.extend(['', 'Comparison against retrieved guidance:'])
            if not any(item['comparison'][field] for field in DIFFERENCE_FIELDS):
                lines.append('No supported differences were reported.')
            for field in DIFFERENCE_FIELDS:
                for entry in item['comparison'][field]:
                    refs = ', '.join(str(i) for i in entry['evidence_indices'])
                    lines.append(f"- {field.replace('_', ' ').capitalize()}: {plain(entry['description'])} "
                                 f"(evidence {refs}; guidance: {plain(entry['guidance_quote'])}).")
        elif compare_guidance and risk is not None and item['status'] != 'error':
            lines.extend(['', 'Comparison skipped because no standard guidance was retrieved.'])
        context = item['memory_used']
        lines.extend(['', 'Memory used:'])
        lines.extend(f'- Semantic guidance: {plain(fact)}' for fact in context['semantic'])
        for episode in context['episodic']:
            lines.append(f"- Historical review: {plain(episode['contract_name'])} "
                         f"({plain(episode['contract_id'])}, {plain(episode['timestamp'])}).")
        if not context['semantic'] and not context['episodic']:
            lines.append('No long-term memory was retrieved for this clause.')
    lines.extend(['', '## Recommended Actions', ''])
    actions = [f"- {item['clause_type']}: {plain(item['risk']['recommendation'])}"
               for item in findings if item['risk'] is not None]
    lines.extend(actions or ['No assessed finding produced a recommendation.'])
    if not complete:
        lines.append('- Resolve failed stages before relying on this review.')
    lines.extend(['', '## Limitations', '',
                  'This report provides contract-review assistance, not legal advice.',
                  'Quotes are checked against source text; model interpretation remains unverified.',
                  'NOT FOUND means the extractor reported no evidence in the supplied text.',
                  'A failed stage is not a missing clause. No extraction or risk-quality metric is claimed.'])
    return '\n'.join(lines) + '\n'
