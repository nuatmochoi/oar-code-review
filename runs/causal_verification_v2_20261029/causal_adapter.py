"""Three matched logical judgments over the unchanged delivered-body pipeline.

All arms share the first prompt/schema and source/query budgets. Later roles
differ; neither verification nor a synthetic probe is a finding acceptance gate.
The parent may replay a freshly shared first completion after checking its exact
input hash. This module does not replay responses or call a model itself.
"""
from __future__ import annotations

import contextlib
import copy
import json
import threading
from types import SimpleNamespace

import causal_verification as cv
import delivery_adapter as da
import delivery_rendering as dr
import ladder_adapter as la
import oar_core as oc
import oar_product_adapter as ad

ARMS = ('current', 'verify', 'debate', 'consumer')
_LOCK = threading.Lock()
RESPONSE_SCHEMA = copy.deepcopy(la.RESPONSE_SCHEMA)
RESPONSE_SCHEMA['properties']['semantic_probe'] = {'anyOf': [{'type': 'null'}, cv.probe_schema()]}
RESPONSE_SCHEMA['required'].append('semantic_probe')
INITIAL_POLICY = ('This is the common initial review in a three-judgment experiment. '
    'Review supplied evidence normally. Do not propose a semantic_probe in this first response; '
    'the semantic_probe field is required and must be null when no probe is proposed. Set it to null in this first response. '
    'One lookup slot is reserved for the second review in every condition. '
    'A final answer will follow two common post-review stages even when no extra evidence is available.')
PROBE_GUIDE = ('The semantic_probe field is required; use null when no proposal is needed. '
    'Only when a minimal semantic hypothesis needs testing, return a semantic_probe object with exactly: '
    '{id,claim_id,code,expected_stdout,required_python,source_refs:[{evidence_id,snapshot,start,end}]}. '
    'Use a supplied claim ID; source_refs must name actually delivered contiguous numbered lines in the explicit head/base snapshot. '
    'required_python must match the declared experimental CPython version. expected_stdout is your hypothesis, not an observed result. '
    'Use small synthetic pure code and allowed stdlib operations, for example functools.partial/wraps, copy.copy/deepcopy, '
    'collections, operator, math, decimal, fractions, or ast constructors. No repository imports, files, network, eval, or exec. '
    'compile accepts an AST only and returns validation metadata, never executable code. '
    'Probe IDs are not source evidence IDs; cite the delivered source for any final finding. '
    'Do not propose a probe when source inspection is sufficient; unsupported probes remain inconclusive.')
ROLES = {
    ('current', 2): ('neutral_reviewer', 'Reconsider the previous review against the supplied source. Correct any mistaken defect or safety conclusion. Do not propose a semantic_probe.'),
    ('current', 3): ('final_reviewer', 'Make the final review from the supplied evidence and previous review. Keep or revise conclusions as justified. Do not propose a semantic_probe.'),
    ('verify', 2): ('self_verifier', 'Verify your initial causal explanation and safe conclusions. Identify the exact failing operation, consumer, and a concrete refutation. Record the reasoning in review.observations.'),
    ('verify', 3): ('verified_final_reviewer', 'Make the final review after checking the causal explanation. A missing or inconclusive probe is not grounds by itself to remove a finding. Do not propose a semantic_probe.'),
    ('consumer', 2): ('self_verifier', 'Verify your initial causal explanation and safe conclusions. Identify the exact failing operation, consumer, and a concrete refutation. Record the reasoning in review.observations.'),
    ('consumer', 3): ('verified_final_reviewer', 'Make the final review after checking the causal explanation. A missing or inconclusive probe is not grounds by itself to remove a finding. Do not propose a semantic_probe.'),
    ('debate', 2): ('independent_critic', 'Act as a critic of the initial reviewer, not as its defender. Independently check defect and safety claims against supplied evidence, identify the exact operation and consumer, and propose concrete refutations in review.observations. Disagreement alone is not evidence.'),
    ('debate', 3): ('adjudicator', 'Adjudicate the initial review and critic using actual supplied source and any advisory probe. Do not vote or count agreement. Retain only justified final findings, explaining corrections in review.observations. Do not propose a semantic_probe.'),
}


def validate_strict_schema(schema):
    """Local provider-shape preflight, including objects inside nullable unions.

    Strict structured outputs require every property in required and forbid
    extra object properties. Optional values must use a required nullable field.
    This catches the observed provider rejection before any CLI/model call.
    """
    def visit(node, path):
        if isinstance(node, list):
            for index, item in enumerate(node):
                visit(item, f'{path}[{index}]')
        elif isinstance(node, dict):
            kind = node.get('type')
            if kind == 'object' or isinstance(kind, list) and 'object' in kind or 'properties' in node:
                properties = node.get('properties', {})
                required = node.get('required')
                if (not isinstance(required, list) or len(required) != len(set(required))
                        or set(required) != set(properties)):
                    raise ValueError(f'{path}: strict schema requires every property exactly once in required')
                if node.get('additionalProperties') is not False:
                    raise ValueError(f'{path}: strict schema requires additionalProperties=false')
            for name, value in node.items():
                visit(value, f'{path}.{name}')
    visit(schema, '$')


def response_schema(arm=None):
    if arm is not None and arm not in ARMS:
        raise ValueError(f'unknown causal arm: {arm}')
    value = copy.deepcopy(RESPONSE_SCHEMA)
    validate_strict_schema(value)
    return value


def actual_sources(state):
    """Exact numbered lines observed in actual completion parts, never all store rows."""
    by_key = {}
    for item in state.actual_inputs:
        observed = dr.observe_delivery(item['parts'], state.account.store)
        for span in observed['spans']:
            ev = state.account.store.by_id[span['evidence_id']]
            if ev.content_kind != 'source_lines':
                continue
            key = (ev.id, ev.snapshot)
            row = by_key.setdefault(key, dict(evidence_id=ev.id, file=ev.file, snapshot=ev.snapshot,
                                             delivered=True, content_kind='source_lines', lines={}))
            exact = dict(ev.lines)
            row['lines'].update((n, exact[n]) for n in span['lines'])
    return [{**row, 'lines': sorted(row['lines'].items())} for row in by_key.values()]


def bounded_state(role, instruction, sections, cap=1600):
    """One JSON block with explicit section truncation under the shared state cap."""
    chunks = {k: v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, separators=(',', ':'))
              for k, v in sections.items()}
    omitted = {k: False for k in chunks}
    def render():
        return json.dumps(dict(role=role, instruction=instruction, sections=chunks,
                               section_truncated=omitted,
                               note='Excerpts are prior model claims or advisory checks, not new source evidence.'),
                          ensure_ascii=False, separators=(',', ':'))
    text = render()
    while oc.count_reference_tokens(text) > cap:
        key = max(chunks, key=lambda k: len(chunks[k]))
        if len(chunks[key]) < 16:
            raise ad.StateBlocked('fixed causal role instruction exceeds state cap')
        chunks[key] = chunks[key][:max(0, int(len(chunks[key])*0.8))]
        omitted[key] = True
        text = render()
    return text, oc.count_reference_tokens(text), omitted


def probe_digest(rows):
    """Put measured outcome before verbose proposal/provenance in bounded state."""
    out = []
    for row in rows:
        result = row.get('result', {})
        item = dict(status=row['status'], id=row.get('proposal', {}).get('id'),
                    claim_id=row.get('proposal', {}).get('claim_id'), logical_queries=row['logical_queries'],
                    runtime_scope=row.get('runtime_scope'), source_output_gate=False,
                    validation_reasons=row.get('validation', {}).get('reasons', []))
        for key in ('stdout', 'stderr', 'exception_type', 'exception_message', 'expected_stdout_matches',
                    'matches_expected', 'model_hypothesis_expected_stdout', 'model_hypothesis_output_matched',
                    'interpretation', 'actual_python', 'runtime', 'cost', 'memory_control'):
            if key in result:
                item[key] = result[key]
        if row.get('error'):
            item['error'] = row['error']
        out.append(item)
    return out


class RunContext:
    def __init__(self, arm, probe_runtime):
        self.arm, self.probe_runtime = arm, copy.deepcopy(probe_runtime)
        self.state = None
        self.first_payload = None
        self.first_raw = None
        self.verification = {}
        self.probes, self.rounds, self.roles, self.ignored_proposals = [], [], [], []
        self.source_queries = 0

    def probe(self):
        state = self.state
        proposal = (state.last_payload or {}).get('semantic_probe')
        if proposal is None:
            return 0
        row = dict(stage='update_1', proposal=copy.deepcopy(proposal), logical_queries=0,
                   source_output_gate=False, runtime_scope=(self.probe_runtime or {}).get('scope'))
        self.probes.append(row)
        if self.arm == 'current':
            row['status'] = 'ignored_current_condition'
            return 0
        if state.accounting.total >= state.accounting.limits['queries_total']:
            row['status'] = 'budget_denied'
            return 0
        request = SimpleNamespace(id=f'semantic-probe-{len(self.probes)}', operation='semantic_probe',
                                  dedup_key=('semantic_probe', len(self.probes)))
        state.accounting.record('round_2', [request])
        row['logical_queries'] = 1
        supplied = actual_sources(state)
        claims = [*self.verification.get('claims', []), *cv.extract_claims(state.last_payload or {}, supplied)]
        validated = cv.validate_probe(proposal, claims, supplied,
                                       declared_runtime=(self.probe_runtime or {}).get('version'))
        row['validation'] = validated
        if not validated['allowed']:
            row['status'] = 'unsupported'
            return 1
        executable = (self.probe_runtime or {}).get('python_executable')
        if not executable:
            row.update(status='unsupported', reason='experimental_python_executable_missing')
            return 1
        try:
            result = cv.execute_probe(validated, python_executable=executable)
        except Exception as exc:
            row.update(status='execution_error', error=f'{type(exc).__name__}: {exc}',
                       note='Inconclusive advisory check; no finding/output gate.')
        else:
            row.update(status=result['status'], result=result)
        return 1


def _state_class(context):
    base = la.LadderState
    class CausalState(base):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            context.state = self

        def capture_raw(self, raw, *, failure=False):
            value = super().capture_raw(raw, failure=failure)
            if self.calls == 1:
                context.first_payload, context.first_raw = copy.deepcopy(value), raw
            return value

        def suffix_extra(self):
            remaining = max(0, self.accounting.limits['queries_total'] - self.accounting.total)
            allowance = min(3, max(0, remaining - 1)) if self.calls == 1 else min(3, remaining) if self.calls == 2 else 0
            budget = (f'<lookup_budget>initial preparation queries allocated: {self.initial_allocated}; '
                      f'actual runtime queries including semantic probes: {self.accounting.total}; '
                      f'allocated total used: {self.initial_allocated + self.accounting.total} of 6; '
                      f'lookups allowed after this response: {allowance}; '
                      f'logical judgments remaining after this response: {3-self.calls}. '
                      'No query can run after the final judgment.</lookup_budget>')
            parts = ['\n'+la.LADDER_CONTRACT, budget]
            if self.calls == 1:
                initial_block = '<causal_initial>'+INITIAL_POLICY+'</causal_initial>'
                parts.append(initial_block)
                tokens = oc.count_reference_tokens(initial_block)
                if not self.account.check_state_tokens(tokens, 'stage_0'):
                    raise ad.StateBlocked('initial common policy exceeds state cap')
                self.state_block = dict(kind='common_initial', tokens=tokens)
                context.roles.append(dict(stage='initial', role='initial_reviewer', tokens=tokens,
                                          state_sha256=oc._sha(INITIAL_POLICY)))
            else:
                role, instruction = ROLES[(context.arm, self.calls)]
                if self.calls == 2 and context.arm != 'current':
                    instruction += (' '+context.verification.get('prompt', '')+' '+PROBE_GUIDE+
                        ' This tests the declared experimental runtime, not the original repository execution.')
                sections = {'initial_raw_response': context.first_raw or '',
                            'previous_response_digest': ad.digest_previous(self.prev_payload or {}, 600)[0]}
                if context.arm != 'current':
                    sections.update(claims=context.verification.get('claims', []),
                                    semantic_probe_results=probe_digest(context.probes),
                                    experimental_runtime={k:v for k,v in (context.probe_runtime or {}).items()
                                                          if k != 'python_executable'})
                opening, closing = '<causal_review_state>', '</causal_review_state>'
                overhead = oc.count_reference_tokens(opening+closing) + 4
                text, _, omitted = bounded_state(role, instruction, sections, cap=1600-overhead)
                state_text = opening + text + closing
                tokens = oc.count_reference_tokens(state_text)
                if not self.account.check_state_tokens(tokens, f'stage_{self.calls-1}'):
                    raise ad.StateBlocked('causal state exceeds cap')
                self.state_block = dict(kind='causal_review_state', tokens=tokens, omitted=omitted, role=role)
                parts.append(state_text)
                context.roles.append(dict(stage=ad.STAGES[self.calls-1], role=role, tokens=tokens,
                                          omitted=omitted, state_sha256=oc._sha(text)))
            return '\n'.join(parts)+'\n'
    return CausalState


def _loop(context, product):
    orch = product.orchestrator
    def run(diff, packet, client, tool_registry, config, on_event=None, review_context=None,
            system_prompt=None, sdk_workflow_context=None):
        evidence, requests, events, seen_evidence = [], [], [], set()
        tokens, latest, format_errors = 0, None, []
        for index in range(3):
            latest = orch.run_llm_review_result(diff, packet, client, additional_evidence=evidence,
                review_context=review_context, system_prompt=system_prompt, sdk_workflow_context=sdk_workflow_context)
            if latest.format_error:
                format_errors.append(latest.format_error)
                break
            state = context.state
            if index != 1 and (state.last_payload or {}).get('semantic_probe') is not None:
                context.ignored_proposals.append(dict(stage=ad.STAGES[index],
                    reason='semantic_probes_are_only_allowed_after_the_second_judgment',
                    proposal=copy.deepcopy(state.last_payload['semantic_probe']), logical_queries=0))
            if index == 0:
                context.verification = cv.build_verification(context.first_payload or {}, actual_sources(state))
            if index == 2:
                break  # neither detector nor registry gets a post-final opportunity
            probe_charge = context.probe() if index == 1 else 0
            remaining = max(0, state.accounting.limits['queries_total'] - state.accounting.total)
            allowed = min(3, max(0, remaining - 1)) if index == 0 else min(3-probe_charge, remaining)
            # Limit the planner itself before it reserves units; do not plan a
            # larger batch and silently abandon body-frontier reservations.
            old_limits = state.accounting.limits
            state.accounting.limits = {**old_limits, 'queries_total': state.accounting.total + allowed}
            try:
                wanted = orch.detect_declaration_evidence_gaps(latest.review_notes, packet,
                                                               tool_registry.code_index, evidence)
                wanted += orch.detect_observation_path_gaps(latest.review_notes, packet,
                    tool_registry.root, review_context, evidence)
            finally:
                state.accounting.limits = old_limits
            chosen, seen = [], set(state.accounting.seen_keys)
            for request in wanted:
                if request.dedup_key not in seen and len(chosen) < allowed:
                    chosen.append(request); seen.add(request.dedup_key)
            row = dict(stage=ad.STAGES[index], source_allowance=allowed, probe_queries=probe_charge,
                       source_requests=[r.id for r in chosen], continued_without_query=not chosen and not probe_charge)
            context.rounds.append(row)
            if chosen:
                requests.extend(chosen)
                batch = tool_registry.execute(chosen)
                context.source_queries += len(chosen)
                events.extend(batch.events)
                added, tokens, budget = orch._append_evidence(batch.evidence, packet, evidence,
                    seen_evidence, tokens, config.agentic_context_token_budget)
                row.update(new_evidence=added, product_token_budget_reached=budget)
        assert latest is not None
        return orch.AgenticReviewResult(findings=latest.findings, review_notes=latest.review_notes,
            trace=product.models.AgenticReviewTrace(packet_id=packet.id, enabled=True,
                iterations=index+1, termination_reason='response_format_error' if format_errors else 'three_judgments_completed',
                requests=requests, evidence=evidence, tool_events=events, evidence_tokens=tokens,
                response_format_retries=0, response_format_errors=format_errors,
                context_reflections=0, context_reflection_errors=[]))
    return run


@contextlib.contextmanager
def _patches(context, product):
    old_state, old_schema = la.LadderState, la.RESPONSE_SCHEMA
    old_loop = product.orchestrator.run_agentic_review
    try:
        la.LadderState, la.RESPONSE_SCHEMA = _state_class(context), RESPONSE_SCHEMA
        product.orchestrator.run_agentic_review = _loop(context, product)
        yield
    finally:
        la.LadderState, la.RESPONSE_SCHEMA = old_state, old_schema
        product.orchestrator.run_agentic_review = old_loop


def run_review(key, arm, inner_client, *, prepared_packet=None, prepared_inputs=None,
               expected_inputs=None, product=None, registry_class=None, frontier_factory=None, probe_runtime=None):
    if arm not in ARMS:
        raise ValueError(f'unknown causal arm: {arm}')
    if not _LOCK.acquire(blocking=False):
        raise ad.AdapterError('one causal review per process')
    try:
        product = product or ad.load_product()
        context = RunContext(arm, probe_runtime)
        if arm == 'consumer' and frontier_factory is None:
            from consumer_search import ConsumerFrontier
            frontier_factory = ConsumerFrontier.from_packet
        with _patches(context, product):
            result = da.run_review(key, 'delivered_auto', inner_client, prepared_packet=prepared_packet,
                prepared_inputs=prepared_inputs, expected_inputs=expected_inputs, product=product,
                registry_class=registry_class, frontier_factory=frontier_factory)
        probe_queries = sum(p['logical_queries'] for p in context.probes)
        result.update(review_id=f'{key}_{arm}', arm=arm,
            runtime_source_queries=result['runtime_tool_queries'] - probe_queries,
            runtime_probe_queries=probe_queries,
            causal_policy=dict(three_logical_judgments=True, initial_prompt_common=True,
                current_is_matched_compute_control_not_historical_adaptive=True,
                stage_roles=[r['role'] for r in context.roles], probe_enabled=arm!='current',
                consumer_frontier=arm=='consumer', initial_query_reservation=1,
                same_model=True, debate_rounds=1 if arm=='debate' else 0, voting=False,
                source_output_gate=False, probe_runtime=copy.deepcopy(probe_runtime)),
            causal_audit=dict(roles=context.roles, rounds=context.rounds, probes=context.probes,
                ignored_proposals=context.ignored_proposals,
                initial_response_sha256=oc._sha(context.first_raw) if context.first_raw is not None else None,
                verification= context.verification if arm!='current' else None,
                logical_judgments=len(context.roles), source_output_gate=False))
        if result['status']=='completed' and result['model_calls'] != 3:
            result.update(status='incomplete_three_stage_protocol', raw_findings_are_final_success=False,
                          reportable_finding_indices=[])
        return result
    finally:
        _LOCK.release()
