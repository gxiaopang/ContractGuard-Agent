"""Human-only domain-memory decisions; this module registers no model tool."""

from __future__ import annotations

from contextlib import closing

from waku.db import connect
from waku.memory.contractguard_context import applicability, review_context, scope_for
from waku.memory.contractguard_store import DomainMemory
from waku.tools.contract_schema import bounded_text, object_fields


def submit_memory(settings, payload):
    if not settings.contract_review or not settings.contract_memory_upgrade:
        raise ValueError('Memory decisions require the upgraded contract policy.')
    if not isinstance(payload, dict):
        raise TypeError('memory submission must be an object')
    action = payload.get('action')
    scope = scope_for(settings.home)
    if action == 'propose':
        object_fields(payload, {'action', 'kind', 'clause_type', 'guidance', 'context',
                                'valid_from', 'valid_until', 'reviewer', 'reason', 'regression_cases'}, 'memory proposal')
        if payload['kind'] not in ('policy', 'experience'):
            raise ValueError('Only policies and experience rules can be proposed here.')
        bounded_text(payload['reviewer'], 'reviewer', 200)
        bounded_text(payload['reason'], 'proposal reason')
        regressions = payload['regression_cases']
        if not isinstance(regressions, list) or any(not isinstance(r, str) or not r.strip() or len(r) > 200 for r in regressions):
            raise ValueError('regression cases must contain case identities')
        if payload['kind'] == 'experience' and not regressions:
            raise ValueError('Experience promotion requires explicit regression case identities.')
        context = review_context(payload['context'], scope=scope)
        constraints = applicability(context, valid_from=payload['valid_from'], valid_until=payload['valid_until'])
        bounded_text(payload['guidance'], 'guidance', 40000)
        guidance = (payload['guidance'] + '\nProposed by: ' + payload['reviewer']
                    + '\nReason: ' + payload['reason'] + '\nRegression cases: ' + ', '.join(regressions))
    else:
        fields = {'action', 'memory_id', 'revision', 'reviewer', 'reason'}
        if action == 'supersede':
            fields.add('replacement_id')
        object_fields(payload, fields, 'memory decision')
        if action not in ('approve', 'revoke', 'supersede'):
            raise ValueError('unknown memory lifecycle action')
    if (settings.home / 'state.db').is_symlink():
        raise ValueError('domain memory cannot use a symlinked database')
    with closing(connect(settings.home)) as conn:
        domain = DomainMemory(conn, scope)
        if action == 'propose':
            record_id = domain.put(payload['kind'], payload['clause_type'], guidance, constraints)
            return domain.get(record_id)
        return domain.decide(payload['memory_id'], payload['revision'], action,
                             reviewer=payload['reviewer'], reason=payload['reason'],
                             replacement_id=payload.get('replacement_id'))
