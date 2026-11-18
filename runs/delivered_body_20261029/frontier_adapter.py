"""Six component arms; optional, budgeted post-review source-body acquisition.

The copied ladder/common adapters and product are immutable. The frontier is an
internal retrieval queue, never an output gate or an addition to the model prompt.
"""
from __future__ import annotations

import contextlib
import copy
import dataclasses
import json
import threading
from pathlib import Path

import ladder_adapter as la
import oar_product_adapter as ad

ARMS = ('diff', 'ast', 'legacy_lsp', 'body_lsp', 'reactive', 'proactive')
MAPPED_ARMS = dict(diff='diff', ast='ast', legacy_lsp='lsp', body_lsp='lsp',
                   reactive='search', proactive='search')
LOCATORS = frozenset(('go_to_definition', 'find_references', 'find_implementations',
                       'incoming_calls', 'outgoing_calls', 'symbol_search', 'document_symbols'))
_LOCK = threading.Lock()
_REQUEST_FIELDS = {'id', 'operation', 'reason', 'path', 'query', 'start', 'end', 'origin', 'rule_id'}


def response_schema(arm=None):
    if arm is not None and arm not in ARMS:
        raise ValueError(f'unknown frontier arm: {arm}')
    return la.response_schema()


def _head_spans(store):
    return [dict(file=ev.file, snapshot=ev.snapshot, lines=[n for n, _ in ev.lines],
                 runs=[list(r) for r in ev.runs()], admitted=True, evidence_id=ev.id)
            for ev in store.by_id.values() if ev.snapshot == 'head' and ev.lines]


def _locations(trace, allowed):
    """Read only location fields returned by executed tools, never source text."""
    out, by_position = [], {}
    keys = ('definitions', 'declarations', 'lsp_locations', 'scip_locations', 'locations',
            'references', 'symbols', 'incoming_calls', 'outgoing_calls', 'implementations')
    for raw in trace.get('raw', []):
        if raw.get('truncated'):
            continue
        try:
            payload = json.loads(raw.get('content', ''))
        except (ValueError, TypeError):
            continue
        if isinstance(payload, list):
            payload = {'locations': payload}
        if not isinstance(payload, dict):
            continue
        for key in keys:
            for row in payload.get(key, []) if isinstance(payload.get(key), list) else []:
                if not isinstance(row, dict):
                    continue
                path = row.get('path') or row.get('file')
                start = row.get('line_start', row.get('line'))
                end = row.get('line_end', start)
                if path not in allowed or type(start) is not int or type(end) is not int or not 1 <= start <= end:
                    continue
                identity = (path, start, end)
                location = by_position.get(identity)
                if location is None:
                    location = dict(path=path, start=start, end=end, line_start=start, line_end=end,
                                    name=row.get('name'), kind=row.get('kind'), source=row.get('source') or '',
                                    position_only=True, sources=[], ambiguous=False)
                    by_position[identity] = location
                    out.append(location)
                # Preserve a conservative ambiguity union across duplicate tool
                # rows; a later ambiguous hit must not inherit a certain binding.
                location['ambiguous'] = location['ambiguous'] or bool(row.get('ambiguity') or row.get('ambiguous'))
                sources = row.get('sources') or [row.get('source')]
                if isinstance(sources, str):
                    sources = [sources]
                if isinstance(sources, list):
                    location['sources'] = sorted(set(location['sources']) |
                                                 {s for s in sources if isinstance(s, str) and s})
                if type(row.get('column_start')) is int and row['column_start'] >= 0:
                    location.setdefault('column_start', row['column_start'])
    return out


class FrontierRuntime:
    def __init__(self, serialized, factory=None):
        self.serialized = copy.deepcopy(serialized)
        self.factory = factory
        self.frontier = None
        self.state = None
        self.decisions, self.observations = [], []
        self.automatic_ids = set()
        self.counters = dict(automatic_requests=0, automatic_body_read_queries=0,
                             automatic_locator_queries=0, runtime_source_read_queries=0,
                             runtime_native_locator_queries=0, admitted_body_chunks=0,
                             runtime_scoped_index_read_attempts=0, runtime_scoped_index_reads_succeeded=0)

    def initialize(self, state):
        self.state = state
        factory = self.factory
        if factory is None:
            from body_frontier import BodyFrontier
            factory = BodyFrontier.from_packet
        self.frontier = factory(self.serialized, state.inputs['data']['workspace_root'],
                                supplied_spans=_head_spans(state.account.store))

    def _release(self, request, reason):
        self.frontier.release(request, reason=reason)

    def plan(self, declaration, observation, model_requests, product):
        state = self.state
        remaining = max(0, state.accounting.limits['queries_total'] - state.accounting.total)
        record = dict(stage=ad.STAGES[state.calls - 1], remaining_queries=remaining,
                      selected=[], deferred=[], reason='selected')
        self.decisions.append(record)
        if state.calls >= 3 or remaining == 0:
            record['reason'] = 'model_or_query_budget_exhausted'
            return []
        if state.account.exposed_tokens() >= la.LIMITS['source_union_reference_tokens']:
            record['reason'] = 'source_budget_exhausted'
            return []
        if hasattr(self.frontier, 'prioritize_claims'):
            self.frontier.prioritize_claims(state.last_payload or {})
        capacity = min(3, remaining)
        selected, seen = [], set(state.accounting.seen_keys)
        # A locator consumes one query now and requires a later model round plus
        # an independently charged body read. Reserve that budget explicitly.
        reserved_bodies = 0
        for _ in range(capacity):
            raw = self.frontier.next_request(
                max_lines=200, remaining_queries=remaining - len(selected) - reserved_bodies,
                source_tokens_remaining=max(0, la.LIMITS['source_union_reference_tokens']
                                            - state.account.exposed_tokens()))
            if raw is None:
                break
            if not isinstance(raw, dict):
                raise ad.AdapterError('frontier returned a non-object request')
            raw = dict(raw)
            raw.setdefault('reason', 'Acquire missing source body linked to supplied code.')
            raw.setdefault('origin', 'body-frontier')
            raw.setdefault('rule_id', 'body-frontier')
            raw.setdefault('id', f'frontier-{state.calls}-{len(selected) + 1}')
            unknown = set(raw) - _REQUEST_FIELDS
            if unknown:
                self._release(raw, 'unsupported_request_fields')
                raise ad.AdapterError(f'frontier request has unsupported fields: {sorted(unknown)}')
            request = product.models.ContextRequest(**raw)
            reason = None
            if request.operation not in ad.PILOT_OPERATIONS:
                reason = 'unsupported_operation'
            elif request.path is not None and request.path not in state.inputs['data']['workspace_sources']:
                reason = 'outside_frozen_source_manifest'
            elif request.dedup_key in seen:
                reason = 'already_executed_or_selected'
            elif request.operation == 'read_lines' and (
                    type(request.start) is not int or type(request.end) is not int
                    or not 1 <= request.start <= request.end or request.end - request.start + 1 > 200):
                reason = 'invalid_body_chunk'
            elif request.operation in LOCATORS and (
                    state.calls >= 2 or remaining - len(selected) - reserved_bodies < 2):
                reason = 'no_following_body_slot_or_round'
            if reason:
                self._release(raw, reason)
                record['deferred'].append(dict(request=raw, reason=reason))
                break
            selected.append(request)
            seen.add(request.dedup_key)
            self.automatic_ids.add(request.id)
            if request.operation in LOCATORS:
                reserved_bodies += 1
            if len(selected) + reserved_bodies >= remaining:
                break
        # Keep original declaration -> observation -> model order after frontier.
        # Filter duplicates BEFORE the product's slice, including across stages.
        decl_paths = {r.path for r in declaration if r.path}
        original = [*declaration, *[r for r in observation if r.path not in decl_paths], *model_requests]
        for request in original:
            if request.dedup_key in seen:
                record['deferred'].append(dict(request=dataclasses.asdict(request), reason='duplicate'))
                continue
            if len(selected) >= capacity or len(selected) + reserved_bodies >= remaining:
                record['deferred'].append(dict(request=dataclasses.asdict(request), reason='batch_or_reserved_budget'))
                continue
            if request.operation in LOCATORS:
                if state.calls >= 2 or remaining - len(selected) - reserved_bodies < 2:
                    record['deferred'].append(dict(request=dataclasses.asdict(request),
                                                   reason='no_following_body_slot_or_round'))
                    continue
                reserved_bodies += 1
            selected.append(request)
            seen.add(request.dedup_key)
        record['selected'] = [dataclasses.asdict(r) for r in selected]
        record['reserved_body_queries'] = reserved_bodies
        if not selected:
            record['reason'] = 'no_eligible_frontier_or_original_request'
        return selected

    def observe_batch(self, registry, requests, batch, traces):
        by_id = {t.get('request', {}).get('id'): t for t in traces}
        events = {e.request_id: e for e in batch.events}
        allowed = set(self.state.inputs['data']['workspace_sources'])
        for request in requests:
            trace = by_id.get(request.id, {})
            event = events.get(request.id)
            status = event.status if event is not None else trace.get('product_event', {}).get('status', 'unknown')
            rows = [dict(row, snapshot='head') for row in trace.get('normalized', [])
                    if row.get('admitted') and row.get('runs')]
            locations = _locations(trace, allowed) if status == 'completed' else []
            automatic = request.id in self.automatic_ids
            self.counters['admitted_body_chunks'] += int(automatic and request.operation == 'read_lines' and bool(rows))
            self.frontier.observe(dataclasses.asdict(request), supplied_spans=rows,
                                  locator_locations=locations, status=status)
            self.observations.append(dict(request_id=request.id, operation=request.operation,
                                          automatic=automatic, status=status,
                                          admitted_source_spans=[{k: row[k] for k in ('file', 'runs', 'evidence_id') if k in row}
                                                                 for row in rows], locator_count=len(locations)))

    def summary(self):
        final = self.frontier.to_dict() if self.frontier is not None else None
        initial_reads = self.serialized.get('source_reads', [])
        # BodyFrontier copies the initial audit and appends only actual new index
        # file reads. A cache hit adds no event, and source/body queries are not a
        # proxy for these independent physical reads.
        runtime_reads = (final or {}).get('source_reads', [])[len(initial_reads):]
        self.counters['runtime_scoped_index_read_attempts'] = len(runtime_reads)
        self.counters['runtime_scoped_index_reads_succeeded'] = sum(
            row.get('read_succeeded') is True for row in runtime_reads)
        return dict(enabled=True, initial=self.serialized,
                    final=final,
                    decisions=self.decisions, observations=self.observations, counters=self.counters,
                    runtime_native_rpc_queries=None,
                    counter_semantics=_COUNTER_SEMANTICS,
                    note='Source coverage only; no finding/citation/output gate. Positions are not bodies.')


_COUNTER_SEMANTICS = {
    'runtime_native_locator_queries': 'Invocations of the native resolver API, including empty/unavailable/failed attempts; not physical JSON-RPC writes.',
    'runtime_native_rpc_queries': 'Unknown without a wire-write meter; null, never inferred from resolver status.requests.',
    'runtime_source_read_queries': 'Executed read_lines requests, including failed or blocked reads.',
    'admitted_body_chunks': 'Automatic read requests with at least one admitted source span; not complete functions.',
    'runtime_scoped_index_read_attempts': 'New physical scoped AST-index file-read events after the serialized initial source_reads prefix; independent of source body queries.',
    'runtime_scoped_index_reads_succeeded': 'New scoped-index file-read events whose read_succeeded is true; cached index reuse is not a new read.',
}


class _NativeMeter:
    METHODS = frozenset(('go_to_definition', 'find_references', 'implementations', 'incoming_calls',
                         'outgoing_calls', 'document_symbols', 'workspace_symbols'))

    def __init__(self, inner, counters):
        self.inner, self.counters = inner, counters

    def __getattr__(self, name):
        value = getattr(self.inner, name)
        if name not in self.METHODS or not callable(value):
            return value
        def counted(*args, **kwargs):
            self.counters['runtime_native_locator_queries'] += 1
            return value(*args, **kwargs)
        return counted


def _observed_registry(base, runtime, counters, product):
    class ObservedRegistry(base):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            if self.lsp_resolver is not None:
                self.lsp_resolver = _NativeMeter(self.lsp_resolver, counters)

        def execute(self, requests):
            before = len(self.trace)
            for request in requests:
                automatic = runtime is not None and request.id in runtime.automatic_ids
                counters['automatic_requests'] += int(automatic)
                counters['automatic_body_read_queries'] += int(automatic and request.operation == 'read_lines')
                counters['automatic_locator_queries'] += int(automatic and request.operation in LOCATORS)
                counters['runtime_source_read_queries'] += int(request.operation == 'read_lines')
            batch = super().execute(requests)
            if runtime is not None:
                runtime.observe_batch(self, requests, batch, self.trace[before:])
            return batch

        def _dispatch(self, request):
            if runtime is None or request.id not in runtime.automatic_ids or request.operation not in (
                    'go_to_definition', 'find_references'):
                return super()._dispatch(request)
            anchors = runtime.frontier.to_dict().get('locators', [])
            anchor = next((a for a in anchors if a['id'] == request.rule_id), None)
            if anchor is None:
                raise ad.AdapterError('automatic native lookup has no frozen exact-use-site anchor')
            if anchor['path'] not in runtime.state.inputs['data']['workspace_sources']:
                raise ad.AdapterError('automatic native anchor outside frozen source manifest')
            from packet_builder import _symbol
            symbol = _symbol(anchor)
            locations = (getattr(self.lsp_resolver, request.operation)(symbol)
                         if self.lsp_resolver is not None else [])
            rows = [dataclasses.asdict(x) if dataclasses.is_dataclass(x) else dict(x) for x in locations]
            sources = sorted({r.get('source', '') for r in rows if r.get('source')})
            payload = (dict(definitions=[], declarations=[], lsp_locations=rows, scip_locations=[])
                       if request.operation == 'go_to_definition' else rows)
            return product.models.ContextEvidence(
                id=f'evidence-{request.id}', request_id=request.id, operation=request.operation,
                source=f'{request.operation}:{anchor["name"]}', content=json.dumps(payload),
                metadata=dict(resolution_sources=sources, resolution_source=sources[0] if sources else 'native-empty',
                              fallback_used=False, ambiguity_reasons=[], exact_use_site=dict(anchor),
                              native_api_invoked=self.lsp_resolver is not None))
    return ObservedRegistry


@contextlib.contextmanager
def _install_hooks(runtime):
    original_hooks = la.product_hooks

    @contextlib.contextmanager
    def hooks(state, product):
        with original_hooks(state, product):
            runtime.initialize(state)
            orch = product.orchestrator
            originals = {name: getattr(orch, name) for name in (
                'run_llm_review_result', 'detect_declaration_evidence_gaps', 'detect_observation_path_gaps')}
            def run(*args, **kwargs):
                result = originals['run_llm_review_result'](*args, **kwargs)
                # The common wrapper has already preserved the model requests in
                # state.pending_requests. They enter the single merged plan below.
                return dataclasses.replace(result, context_requests=[])
            def declaration(notes, packet, code_index, additional_evidence=None):
                decl = originals['detect_declaration_evidence_gaps'](notes, packet, code_index, additional_evidence)
                obs = originals['detect_observation_path_gaps'](
                    notes, packet, state.registry.root, state.review_context, additional_evidence)
                return runtime.plan(decl, obs, state.pending_requests, product)
            try:
                orch.run_llm_review_result = run
                orch.detect_declaration_evidence_gaps = declaration
                orch.detect_observation_path_gaps = lambda *a, **k: []
                yield
            finally:
                for name, value in originals.items():
                    setattr(orch, name, value)
    la.product_hooks = hooks
    try:
        yield
    finally:
        la.product_hooks = original_hooks


def run_review(key, arm, inner_client, *, prepared_packet=None, expected_inputs=None,
               prepared_inputs=None, product=None, registry_class=None, frontier_factory=None):
    if arm not in ARMS:
        raise ValueError(f'unknown frontier arm: {arm}')
    if not _LOCK.acquire(blocking=False):
        raise ad.AdapterError('one frontier review per process; concurrent adapter mutation refused')
    try:
        product = product or ad.load_product()
        if prepared_inputs is None:
            import frontier_builder
            prepared_inputs = frontier_builder.build_inputs(key, arm, prepared_packet=prepared_packet, product=product)
        serialized = (prepared_packet or {}).get('frontier', prepared_inputs.get('component_frontier'))
        if arm == 'proactive' and not isinstance(serialized, dict):
            raise ad.AdapterError('proactive requires a frozen serialized body frontier')
        runtime = FrontierRuntime(serialized, frontier_factory) if arm == 'proactive' else None
        counters = runtime.counters if runtime is not None else FrontierRuntime({}, None).counters
        registry = registry_class
        if arm in ('reactive', 'proactive'):
            registry = _observed_registry(registry or ad.make_registry_class(product), runtime, counters, product)
        manager = _install_hooks(runtime) if runtime is not None else contextlib.nullcontext()
        with manager:
            result = la.run_review(key, MAPPED_ARMS[arm], inner_client, prepared_inputs=prepared_inputs,
                                   product=product, registry_class=registry, expected_inputs=expected_inputs)
        result.update(review_id=f'{key}_{arm}', arm=arm,
                      frontier_policy={'proactive_body_frontier': arm == 'proactive',
                                       'initial_packet': 'legacy' if arm in ('diff', 'ast', 'legacy_lsp') else 'enhanced',
                                       'R_ledger': False, 'C_output_gate': False},
                      body_frontier=runtime.summary() if runtime is not None else dict(
                          enabled=False, initial=copy.deepcopy(serialized), final=None, decisions=[], observations=[],
                          counters=counters, runtime_native_rpc_queries=None,
                          counter_semantics=_COUNTER_SEMANTICS))
        return result
    finally:
        _LOCK.release()
