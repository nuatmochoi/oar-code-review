"""Actual-prompt delivery audit and an optional delivery-driven body frontier.

The copied adapters remain immutable. All source/citation/output rules and the
product's review loop are shared. A cache replay still consumes one logical
read_lines request; it does not invoke a source-reading tool.
"""
from __future__ import annotations

import contextlib
import copy
import dataclasses
import threading

import delivery_rendering as dr
import frontier_adapter as fa
import ladder_adapter as la
import oar_core as oc
import oar_product_adapter as ad

ARMS = ('diff', 'legacy_auto', 'render_auto', 'delivered_auto')
MODES = dict(diff='observe_only_diff', legacy_auto='observe_only_legacy',
             render_auto='fixed_render_only', delivered_auto='delivered_coverage')
_LOCK = threading.Lock()


def response_schema(arm=None):
    if arm is not None and arm not in ARMS:
        raise ValueError(f'unknown delivery arm: {arm}')
    return la.response_schema()


def _line_keys(spans):
    return {(s['file'], s['snapshot'], n) for s in spans for n in s.get('lines', [])}


def _source_keys(store):
    return {(e.file, e.snapshot, n) for e in store.by_id.values()
            if e.content_kind == 'source_lines' for n, _ in e.lines}


def _cached_lines(store, request):
    """Only already-admitted HEAD source, never metadata, diff, or filesystem."""
    if request.operation != 'read_lines' or type(request.start) is not int or type(request.end) is not int:
        return None
    lines = {}
    for ev in store.by_id.values():
        if ev.content_kind == 'source_lines' and ev.snapshot == 'head' and ev.file == request.path:
            for n, text in ev.lines:
                if request.start <= n <= request.end:
                    if n in lines and lines[n] != text:
                        raise ad.AdapterError('conflicting admitted cache source')
                    lines[n] = text
    return lines if set(lines) == set(range(request.start, request.end + 1)) else None


class DeliveryAudit:
    def __init__(self, arm):
        self.arm, self.state, self.runtime = arm, None, None
        self.stages, self.errors, self.executions = [], [], []
        self.delivered = set()
        self.counters = dict(cache_replay_queries=0, actual_source_tool_read_queries=0,
                             delivery_reconciliations=0)

    def observe(self, parts, state):
        # Called by the transport proxy, after LadderClient has built and logged
        # its exact final parts, immediately before the real completion method.
        self.state = state
        observed = dr.observe_delivery(parts, state.account.store)
        visible = _line_keys(observed['spans'])
        prepared = {(e.file, e.snapshot, n) for e in state.inputs['seed_evidence']
                    if e.content_kind == 'source_lines' for n, _ in e.lines}
        errors = []
        for row in observed['evidence']:
            if row.get('mismatched_lines') or row.get('unexpected_lines') or row.get('path_or_snapshot_mismatches'):
                errors.append(dict(kind='source_render_mismatch', evidence_id=row['evidence_id'],
                                   mismatched_lines=row.get('mismatched_lines', []),
                                   unexpected_lines=row.get('unexpected_lines', []),
                                   path_or_snapshot_mismatches=row.get('path_or_snapshot_mismatches', 0)))
        if observed.get('unknown_evidence_ids'):
            errors.append(dict(kind='unknown_rendered_evidence', ids=observed['unknown_evidence_ids']))
        stage = dict(stage=ad.STAGES[state.calls - 1],
                     parts_sha256={k: oc._sha(v) for k, v in parts.items()},
                     spans=observed['spans'], evidence=observed['evidence'],
                     visible_source_lines=len(visible),
                     prepared_missing_source_lines=len(prepared - visible),
                     errors=errors)
        self.stages.append(stage)
        self.errors.extend(dict(stage=stage['stage'], **e) for e in errors)
        self.delivered.update(visible)
        if self.runtime is not None:
            self.runtime.receive_delivery(observed['spans'])
            self.counters['delivery_reconciliations'] += 1

    def finish(self, result):
        store = self.state.account.store if self.state is not None else None
        collected = _source_keys(store) if store is not None else set()
        frontier = result.get('body_frontier', {}).get('final') or {}
        units = frontier.get('units', [])
        complete, partial, wrong = 0, 0, 0
        unit_audit = []
        for unit in units:
            needed = {(unit['path'], unit.get('snapshot', 'head'), n)
                      for n in range(unit['start'], unit['end'] + 1)}
            missing = needed - self.delivered
            full = bool(needed) and not missing
            complete += int(full)
            partial += int(bool(needed & self.delivered) and not full)
            wrong += int(unit.get('state') == 'body_supplied' and bool(missing))
            unit_audit.append(dict(unit_id=unit['id'], path=unit['path'],
                                   snapshot=unit.get('snapshot', 'head'), start=unit['start'], end=unit['end'],
                                   frontier_state=unit.get('state'), actual_complete=full,
                                   missing_lines=sorted(n for _, _, n in missing)))
        errors = list(self.errors)
        if self.arm == 'delivered_auto' and wrong:
            errors.append(dict(kind='completed_with_missing_lines', count=wrong))
        first = self.stages[0] if self.stages else None
        return dict(version=1, mode=MODES[self.arm], stages=self.stages, units=unit_audit,
                    summary=dict(stage_count=len(self.stages),
                                 initial_prepared_complete=(first['prepared_missing_source_lines'] == 0) if first else None,
                                 initial_prepared_missing_source_lines=first['prepared_missing_source_lines'] if first else None,
                                 collected_source_lines=len(collected), delivered_source_lines=len(self.delivered),
                                 undelivered_source_lines=len(collected - self.delivered),
                                 delivered_complete_units=complete, delivered_partial_units=partial,
                                 completed_with_missing_lines=wrong),
                    counters=dict(self.counters), source_dispatches=self.executions, errors=errors,
                    semantics={
                        'source_lines': 'Unique (file, snapshot, line) from source_lines evidence; diff and positions excluded.',
                        'delivered_source_lines': 'Union of exact verified lines in actual completion parts across stages, including failed completions.',
                        'completion': 'Source-body coverage only, never semantic correctness or a finding/output gate.',
                        'cache_replay_queries': 'Charged logical read_lines requests served from already-admitted HEAD source; zero source-tool dispatches.',
                        'actual_source_tool_read_queries': 'Invocations of read_lines tool dispatch, including failures; not physical filesystem syscalls.',
                    })


class DeliveredRuntime(fa.FrontierRuntime):
    def __init__(self, serialized, factory, audit):
        super().__init__(serialized, factory)
        self.audit = audit
        self.initial_flags = {u['id']: (u.get('expanded', False), u.get('source_expanded', False))
                              for u in serialized.get('units', [])}
        self.reconciliations = []
        audit.runtime = self

    def initialize(self, state):
        self.state = state
        from body_frontier import BodyFrontier
        reset = copy.deepcopy(self.serialized)
        reset['coverage'] = {}
        # Initial preparation acquired these bodies; it did not deliver them to
        # this review. Preserve location attempts, permit one charged replay of
        # an acquired-but-undelivered initial body, then normal dedup applies.
        reset['attempted'] = [k for k in reset.get('attempted', []) if k[0] != 'read_lines']
        for unit in reset.get('units', []):
            if unit.get('state') != 'blocked':
                unit['state'] = 'unread'
            unit['expanded'], unit['source_expanded'] = False, False
        self.frontier = (self.factory or BodyFrontier.from_packet)(
            reset, state.inputs['data']['workspace_root'], supplied_spans=[])

    def receive_delivery(self, spans):
        self.frontier.supply(spans)
        # Reuse initial expansions only once their actual full body is visible.
        # Otherwise a later completed body goes through the unchanged generic
        # frontier expansion, including its separately metered AST index reads.
        for unit in list(self.frontier.data['units']):
            if unit['state'] == 'body_supplied':
                flags = self.initial_flags.pop(unit['id'], None)
                if flags is not None:
                    unit['expanded'], unit['source_expanded'] = flags
                if not unit.get('expanded') or not unit.get('source_expanded'):
                    try:
                        self.frontier.expand(unit)
                    except (ValueError, OSError, SyntaxError) as exc:
                        self.frontier.data['events'].append(dict(kind='expansion_unavailable',
                            unit_id=unit['id'], error=type(exc).__name__))
            else:
                # A serialized expanded flag is not evidence of delivery.
                self.initial_flags.pop(unit['id'], None)
        self.reconciliations.append(dict(stage=ad.STAGES[self.state.calls - 1],
            source='actual_completion_parts', spans=copy.deepcopy(spans),
            complete_units=sum(u['state'] == 'body_supplied' for u in self.frontier.data['units'])))

    def _block(self, request, reason):
        self._release(request, reason)
        for unit in self.frontier.data['units']:
            if unit['id'] == request.get('rule_id') and unit['state'] != 'body_supplied':
                unit['state'] = 'blocked'
                unit['delivery_blocked_reason'] = reason

    def _mark_unrecoverable_duplicates(self):
        # If the exact missing interval already ran and stayed invisible, the
        # product dedup forbids its repeat. Mark that explicit limitation; do
        # not manufacture another call or loop forever on a new request ID.
        attempted = self.frontier.data.get('attempted', [])
        for unit in self.frontier.data['units']:
            if unit['state'] in ('body_supplied', 'blocked'):
                continue
            chunks = self.frontier._missing_chunks(unit, 200)
            if chunks and all(['read_lines', unit['path'], a, b] in attempted for a, b in chunks):
                unit['state'] = 'blocked'
                unit['delivery_blocked_reason'] = 'same_missing_chunk_already_attempted_not_delivered'

    def plan(self, declaration, observation, model_requests, product):
        state = self.state
        self._mark_unrecoverable_duplicates()
        remaining = max(0, state.accounting.limits['queries_total'] - state.accounting.total)
        record = dict(stage=ad.STAGES[state.calls - 1], remaining_queries=remaining,
                      selected=[], deferred=[], reason='selected')
        self.decisions.append(record)
        if state.calls >= 3 or remaining == 0:
            record['reason'] = 'model_or_query_budget_exhausted'
            return []
        self.frontier.prioritize_claims(state.last_payload or {})
        selected, seen, reserved = [], set(state.accounting.seen_keys), 0
        capacity = min(3, remaining)
        # Selection relations/order remain the frozen frontier's. Only delivery
        # coverage and whether a request adds any source differ. At a full store
        # a zero-marginal cache replay remains eligible; new source is denied.
        for _ in range(len(self.frontier.data['units']) + len(self.frontier.data['locators']) + capacity):
            if len(selected) >= capacity or len(selected) + reserved >= remaining:
                break
            raw = self.frontier.next_request(max_lines=200,
                    remaining_queries=remaining - len(selected) - reserved,
                    source_tokens_remaining=la.LIMITS['source_union_reference_tokens'])
            if raw is None:
                break
            if not isinstance(raw, dict) or set(raw) - fa._REQUEST_FIELDS:
                raise ad.AdapterError('frontier returned unsupported request fields')
            request = product.models.ContextRequest(**raw)
            reason = None
            cached = _cached_lines(state.account.store, request) is not None
            if request.operation not in ad.PILOT_OPERATIONS:
                reason = 'unsupported_operation'
            elif request.path is not None and request.path not in state.inputs['data']['workspace_sources']:
                reason = 'outside_frozen_source_manifest'
            elif request.dedup_key in seen:
                reason = 'already_executed_or_selected'
            elif request.operation == 'read_lines' and (type(request.start) is not int or type(request.end) is not int
                    or not 1 <= request.start <= request.end or request.end - request.start + 1 > 200):
                reason = 'invalid_body_chunk'
            elif state.account.exposed_tokens() >= la.LIMITS['source_union_reference_tokens'] and not cached:
                reason = 'source_budget_exhausted_new_source_blocked'
            elif request.operation in fa.LOCATORS and (state.calls >= 2 or remaining - len(selected) - reserved < 2):
                reason = 'no_following_body_slot_or_round'
            if reason:
                self._block(raw, reason)
                record['deferred'].append(dict(request=raw, reason=reason))
                if request.operation in fa.LOCATORS:
                    break
                continue
            selected.append(request)
            seen.add(request.dedup_key)
            self.automatic_ids.add(request.id)
            reserved += int(request.operation in fa.LOCATORS)
        decl_paths = {r.path for r in declaration if r.path}
        original = [*declaration, *[r for r in observation if r.path not in decl_paths], *model_requests]
        for request in original:
            reason = None
            if request.dedup_key in seen:
                reason = 'duplicate'
            elif len(selected) >= capacity or len(selected) + reserved >= remaining:
                reason = 'batch_or_reserved_budget'
            elif state.account.exposed_tokens() >= la.LIMITS['source_union_reference_tokens'] and _cached_lines(state.account.store, request) is None:
                reason = 'source_budget_exhausted_new_source_blocked'
            elif request.operation in fa.LOCATORS and (state.calls >= 2 or remaining - len(selected) - reserved < 2):
                reason = 'no_following_body_slot_or_round'
            if reason:
                record['deferred'].append(dict(request=dataclasses.asdict(request), reason=reason))
                continue
            selected.append(request)
            seen.add(request.dedup_key)
            reserved += int(request.operation in fa.LOCATORS)
        record['selected'] = [dataclasses.asdict(r) for r in selected]
        record['reserved_body_queries'] = reserved
        if not selected:
            record['reason'] = 'no_eligible_frontier_or_original_request'
        return selected

    def observe_batch(self, registry, requests, batch, traces):
        by_id = {t.get('request', {}).get('id'): t for t in traces}
        events = {e.request_id: e for e in batch.events}
        allowed = set(self.state.inputs['data']['workspace_sources'])
        for request in requests:
            trace, event = by_id.get(request.id, {}), events.get(request.id)
            status = event.status if event is not None else trace.get('product_event', {}).get('status', 'unknown')
            rows = [dict(row, snapshot='head') for row in trace.get('normalized', [])
                    if row.get('admitted') and row.get('runs')]
            locations = fa._locations(trace, allowed) if status == 'completed' else []
            automatic = request.id in self.automatic_ids
            self.counters['admitted_body_chunks'] += int(automatic and request.operation == 'read_lines' and bool(rows))
            # Acquisition is recorded, but only the next actual prompt may
            # promote a unit to body_supplied. Locator positions remain usable.
            self.frontier.observe(dataclasses.asdict(request), supplied_spans=[],
                                  locator_locations=locations, status=status)
            self.observations.append(dict(request_id=request.id, operation=request.operation,
                automatic=automatic, status=status, admitted_source_spans=[{k: row[k] for k in ('file', 'runs', 'evidence_id') if k in row}
                     for row in rows], locator_count=len(locations), coverage_granted_at_acquisition=False))

    def summary(self):
        # The review is over: preserve partial coverage but make its inability
        # to complete in this run explicit, including budget and output failure
        # terminations where the product never asks for another plan.
        if self.frontier is not None:
            for unit in self.frontier.data['units']:
                if unit['state'] not in ('body_supplied', 'blocked'):
                    unit['state'] = 'blocked'
                    unit['delivery_blocked_reason'] = 'review_ended_before_delivery_completion'
        result = super().summary()
        result.update(coverage_semantics='Cumulative verified HEAD source lines from actual completion parts only.',
                      delivery_reconciliations=self.reconciliations)
        return result


def _registry(base, audit, product, allow_replay):
    class DeliveryRegistry(base):
        def _dispatch(self, request):
            if request.operation == 'read_lines':
                cached = _cached_lines(self.account.store, request) if allow_replay else None
                entry = dict(request_id=request.id, path=request.path, start=request.start, end=request.end,
                             logical_queries=1, cache_replay=cached is not None,
                             source_tool_dispatches=0 if cached is not None else 1)
                audit.executions.append(entry)
                if cached is not None:
                    audit.counters['cache_replay_queries'] += 1
                    return product.models.ContextEvidence(id=f'evidence-{request.id}', request_id=request.id,
                        operation='read_lines', source=f'{request.path}:{request.start}-{request.end}',
                        content=ad.render_lines(cached), metadata={'delivery_cache_replay': True})
                audit.counters['actual_source_tool_read_queries'] += 1
            return super()._dispatch(request)

        def _n_read_lines(self, request, raw):
            if raw and raw[0].metadata.get('delivery_cache_replay'):
                lines = _cached_lines(self.account.store, request)
                if lines is None:
                    raise ad.AdapterError('admitted cache changed before normalization')
                item, ev = self._source_item(request, '', raw[0].source, request.path, lines, 'read_lines', None)
                item = dataclasses.replace(item, metadata={**item.metadata, 'start': request.start,
                    'end': request.end, 'delivery_cache_replay': True})
                return ad.Normalized([item], [ev], ['source'], ['admitted-cache replay; no source tool read'], {})
            return super()._n_read_lines(request, raw)
    return DeliveryRegistry


@contextlib.contextmanager
def _scoped_delivery(audit):
    original_client, original_runtime = la.LadderClient, fa.FrontierRuntime

    class Proxy:
        def __init__(self, inner, state):
            self.inner, self.state = inner, state

        def __getattr__(self, name):
            return getattr(self.inner, name)

        def complete(self, **parts):
            audit.observe(parts, self.state)
            return self.inner.complete(**parts)

    class Client(original_client):
        def __init__(self, inner, state):
            super().__init__(Proxy(inner, state), state)

    class Runtime(DeliveredRuntime):
        def __init__(self, serialized, factory=None):
            super().__init__(serialized, factory, audit)

    try:
        la.LadderClient = Client
        if audit.arm == 'delivered_auto':
            fa.FrontierRuntime = Runtime
        yield
    finally:
        la.LadderClient, fa.FrontierRuntime = original_client, original_runtime


def run_review(key, arm, inner_client, *, prepared_packet=None, expected_inputs=None,
               prepared_inputs=None, product=None, registry_class=None, frontier_factory=None):
    if arm not in ARMS:
        raise ValueError(f'unknown delivery arm: {arm}')
    if not _LOCK.acquire(blocking=False):
        raise ad.AdapterError('one delivery review per process; concurrent patching refused')
    try:
        product = product or ad.load_product()
        audit = DeliveryAudit(arm)
        base = registry_class
        if arm != 'diff':
            base = _registry(base or ad.make_registry_class(product), audit, product, arm == 'delivered_auto')
        rendering = dr.fixed_rendering(product) if arm in ('render_auto', 'delivered_auto') else contextlib.nullcontext()
        with rendering, _scoped_delivery(audit):
            result = fa.run_review(key, 'diff' if arm == 'diff' else 'proactive', inner_client,
                prepared_packet=prepared_packet, expected_inputs=expected_inputs, prepared_inputs=prepared_inputs,
                product=product, registry_class=base, frontier_factory=frontier_factory)
        result.update(review_id=f'{key}_{arm}', arm=arm,
            delivery_policy=dict(fixed_renderer=arm in ('render_auto', 'delivered_auto'),
                actual_delivery_frontier=arm == 'delivered_auto', source_output_gate=False,
                cache_replay_logical_query_charge=1, underlying_arm='diff' if arm == 'diff' else 'proactive'),
            delivery_audit=audit.finish(result))
        return result
    finally:
        _LOCK.release()
