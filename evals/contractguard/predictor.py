"""Source-only benchmark prediction with explicit memory policy and no runtime cache."""

from __future__ import annotations

import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from threading import Lock
from types import SimpleNamespace

from evals.contractguard.dataset import ReviewInput, target_names
from waku.db import connect
from waku.memory import Memory, bundled_skill_dirs, retrieval_gate
from waku.memory.contractguard import CLAUSES, REVIEW_SCHEMA, clause_facts, review_history
from waku.memory.procedural.loader import SkillLoader
from waku.ops.pricing import MODEL_PRICING, PRICING, price_for
from waku.ops.tracing import metered
from waku.tools.clause_extractor import extract_clause
from waku.tools.contract_parser import parse_contract
from waku.tools.contract_schema import validate_findings
from waku.tools.risk_scorer import score_risk

MODES = ('baseline', 'semantic-only', 'full-memory')
PREDICTOR_VERSION = 'contractguard_ablation_v1'


class ProviderUnavailable(RuntimeError):
    """Stop model work after the provider reports a payment failure."""
    def __init__(self, status_code):
        self.status_code = status_code
        super().__init__(f'provider unavailable (HTTP {status_code})')


def memory_policy(mode: str) -> dict:
    if mode not in MODES:
        raise ValueError('unknown memory mode')
    return {'semantic': mode != 'baseline', 'episodic': mode == 'full-memory',
            'procedures': 'bundled-fixed-all-arms', 'semantic_writes': False,
            'episodic_writes': mode == 'full-memory', 'cache': False, 'slot_gate': False}


class Telemetry:
    def __init__(self, provider: str):
        self.provider = provider
        self.counts = dict.fromkeys(('llm_calls', 'failed_model_calls', 'input_tokens', 'output_tokens',
                                     'retrieval_count', 'semantic_items', 'episodic_items',
                                     'memory_items_retrieved', 'episode_writes', 'gate_failures'), 0)
        self.cost = 0.0
        self.cost_known = True
        self.provider_status_code = None
        self.lock = Lock()

    def event(self, kind: str, data: dict) -> None:
        with self.lock:
            self._event(kind, data)

    def _event(self, kind: str, data: dict) -> None:
        if kind == 'llm':
            usage = data.get('usage', {})
            incoming, outgoing = usage.get('in', 0), usage.get('out', 0)
            self.counts['input_tokens'] += incoming
            self.counts['output_tokens'] += outgoing
            model = data.get('model', '')
            prices = price_for(self.provider, model)
            if self.provider not in PRICING and model not in MODEL_PRICING and not model.endswith(':free'):
                self.cost_known = False
            self.cost += (incoming * prices[0] + outgoing * prices[1]) / 1_000_000
        elif kind == 'retrieval':
            self.counts['retrieval_count'] += 1
            self.counts['semantic_items'] += data['facts']
            self.counts['episodic_items'] += data['episodes']
            self.counts['memory_items_retrieved'] += data['facts'] + data['episodes']
        elif kind == 'review_episode':
            self.counts['episode_writes'] += 1

    def snapshot(self) -> dict:
        with self.lock:
            return {**self.counts, 'estimated_cost_usd': self.cost if self.cost_known else None,
                    'provider_status_code': self.provider_status_code}


class CountingClient:
    """Count attempted logical calls, including failures; SDK wire retries remain opaque."""
    def __init__(self, client, telemetry: Telemetry):
        self.client, self.telemetry = client, telemetry
        self.messages = SimpleNamespace(create=self.create)

    def create(self, **kwargs):
        with self.telemetry.lock:
            if self.telemetry.provider_status_code is not None:
                raise ProviderUnavailable(self.telemetry.provider_status_code)
            self.telemetry.counts['llm_calls'] += 1
        try:
            return self.client.messages.create(**kwargs)
        except Exception as exc:
            with self.telemetry.lock:
                self.telemetry.counts['failed_model_calls'] += 1
                self.telemetry.cost_known = False
                if getattr(exc, 'status_code', None) == 402:
                    self.telemetry.provider_status_code = 402
            raise


class Predictor:
    def __init__(self, settings, client, mode: str, *, workers: int = 1):
        if type(workers) is not int or not 1 <= workers <= 32:
            raise ValueError('clause workers must be an integer from 1 to 32')
        self.workers = workers
        self.policy = memory_policy(mode)
        self.mode = mode
        self.settings = replace(settings, contract_review=self.policy['semantic'],
                                semantic_store='sqlite', episodic_store='sqlite', otel_endpoint='')
        # The runner owns a new home. A predictor cannot attach to someone's memory.
        if self.settings.home.exists():
            raise ValueError('benchmark memory home must be new')
        self.settings.ensure_home()
        self.telemetry = Telemetry(settings.provider)
        self.client = CountingClient(client, self.telemetry)
        self.conn = connect(self.settings.home)
        self.memory = Memory(self.conn, self.settings, self.client)
        loader = SkillLoader(bundled_skill_dirs())
        bodies = {s.name: s.body for s in loader.skills}
        self.procedures = {c.name: bodies[c.skill] for c in CLAUSES}
        self.seen = set()

    def close(self):
        self.conn.close()

    def decision(self, source: ReviewInput, target: str):
        message = json.dumps({'task': 'Retrieve memory for this clause review only.',
                              'clause_type': target, 'source_text': source.text}, ensure_ascii=False)
        return retrieval_gate.review_decision(
            metered(self.client, 'gate', self.telemetry.event), self.settings.small_model, message)

    def context(self, source: ReviewInput, target: str, *, decision=None) -> dict:
        if not self.policy['semantic']:
            return {'semantic': [], 'episodic': []}
        decision = decision if decision is not None else self.decision(source, target)
        if decision.reason.startswith('review gate skipped memory ('):
            self.telemetry.counts['gate_failures'] += 1
        semantic = decision.semantic and decision.clause_type in (None, target)
        episodic = (self.policy['episodic'] and decision.episodic
                    and decision.clause_type in (None, target))
        facts = clause_facts(self.memory.facts, decision.query, target,
                             self.settings.retrieval_top_k) if semantic else []
        episodes = review_history(self.memory.episodes, clause_type=target,
                                  exclude_contract_id=source.contract_id) if episodic else []
        if semantic or episodic:
            self.telemetry.event('retrieval', {'facts': len(facts), 'episodes': len(episodes)})
        return {'semantic': facts, 'episodic': episodes}

    def finding(self, source, target, sections, *, context=None, retrieval_error=None):
        item = {'clause_type': target, 'status': 'error', 'extraction': None, 'risk': None,
                'comparison': None, 'memory_used': {'semantic': [], 'episodic': []}, 'error': None}
        stage = 'retrieval'
        try:
            if retrieval_error is not None:
                raise retrieval_error
            item['memory_used'] = context if context is not None else self.context(source, target)
            stage = 'extraction'
            item['extraction'] = extract_clause(
                metered(self.client, 'clause_extraction', self.telemetry.event), self.settings.model,
                source.text, target, context=item['memory_used'], procedure=self.procedures[target],
                sections=sections)
            if item['extraction']['found']:
                stage = 'risk'
                item['risk'] = score_risk(
                    metered(self.client, 'risk_scoring', self.telemetry.event), self.settings.model,
                    item['extraction'], source.text, context=item['memory_used'],
                    procedure=self.procedures[target])
                item['status'] = 'found'
            else:
                item['status'] = 'not_found'
        except Exception as exc:
            item['error'] = {'stage': stage, 'message':
                             f'Stage failed to produce a validated result ({type(exc).__name__}).'}
        return item

    def parallel_findings(self, source, targets, sections):
        # Only network stages use threads. SQLite reads and writes stay on the
        # owner thread, and every clause sees history from earlier contracts.
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            decisions = ({target: pool.submit(self.decision, source, target) for target in targets}
                         if self.policy['semantic'] else {})
            prepared = {}
            for target in targets:
                try:
                    context = self.context(source, target, decision=decisions[target].result()) if decisions else {
                        'semantic': [], 'episodic': []}
                    prepared[target] = (context, None)
                except Exception as exc:
                    prepared[target] = (None, exc)
            pending = [pool.submit(self.finding, source, target, sections,
                                   context=prepared[target][0], retrieval_error=prepared[target][1])
                       for target in targets]
            return [future.result() for future in pending]

    def predict(self, source: ReviewInput, targets) -> dict:
        if (type(source) is not ReviewInput or not isinstance(source.text, str) or not source.text.strip()
                or source.contract_id != hashlib.sha256(source.text.encode('utf-8')).hexdigest()):
            raise ValueError('predictor accepts only a source id and complete text')
        if source.contract_id in self.seen:
            raise ValueError('a benchmark predictor cannot review the same document twice')
        self.seen.add(source.contract_id)
        targets = list(target_names(targets))
        before, started = self.telemetry.snapshot(), time.perf_counter()
        document = parse_contract(source.text, 'Contract ' + source.contract_id[:12])
        findings = (self.parallel_findings(source, targets, document['sections']) if self.workers > 1 else
                    [self.finding(source, target, document['sections']) for target in targets])
        validate_findings(findings, source.text, targets)
        timestamp = datetime.now(UTC).isoformat()
        if self.policy['episodic_writes'] and all(f['status'] != 'error' for f in findings):
            for item in findings:
                if item['status'] == 'found':
                    record = {'schema': REVIEW_SCHEMA, 'contract_id': source.contract_id,
                              'contract_name': document['metadata']['name'], 'clause_type': item['clause_type'],
                              'risk_level': item['risk']['risk_level'], 'finding_summary': item['risk']['reasoning'],
                              'evidence': item['extraction']['spans'], 'recommendation': item['risk']['recommendation'],
                              'timestamp': timestamp, 'origin': 'benchmark_prediction'}
                    self.memory.complete_review(record, source.text, candidates=[], notify=self.telemetry.event)
        after = self.telemetry.snapshot()
        diagnostics = {key: after[key] - before[key] for key in self.telemetry.counts}
        diagnostics['estimated_cost_usd'] = (after['estimated_cost_usd'] - before['estimated_cost_usd']
                                           if after['estimated_cost_usd'] is not None
                                           and before['estimated_cost_usd'] is not None else None)
        diagnostics['latency_seconds'] = time.perf_counter() - started
        return {'schema': 'contractguard_prediction_v1', 'contract_id': source.contract_id,
                'timestamp': timestamp, 'findings': findings, 'diagnostics': diagnostics}
