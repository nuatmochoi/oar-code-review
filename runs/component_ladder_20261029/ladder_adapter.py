"""Fresh component ladder over the immutable product loop; no A/R/C/B intervention.

Packet construction lives in packet_builder. This module owns only review-time
permissions, accounting, the common response contract, and auditable output capture.
The copied common adapter and product snapshot are imported without editing them.
"""
from __future__ import annotations

import contextlib
import copy
import dataclasses
import json
import time
from dataclasses import replace

import jsonschema

import oar_core as oc
import oar_product_adapter as ad

ARMS = ('diff', 'ast', 'lsp', 'search')
LIMITS = dict(ad.LIMITS)
POLICY = dict(A_scheduling=False, R_ledger=False, C_controller=False, B_body_acquisition=False)

# Identical schema in every arm. Lookup permission is conveyed by the budget,
# not by a schema difference; an unused request cannot erase a valid finding.
RESPONSE_SCHEMA = copy.deepcopy(ad.B0_RESPONSE_SCHEMA)
RESPONSE_SCHEMA['properties']['context_requests']['maxItems'] = 3

LADDER_CONTRACT = '\n'.join(
    '- Review the public contract, DIFF, and supplied code evidence. No persistent candidate ledger is available. '
    'Return candidates: []. The lookup_budget below states whether read-only context requests can run.'
    if line.startswith('- Only the public contract') else
    '- Cite only evidence ids actually shown in this prompt (diff:, context:, or agentic:). '
    'Diff hunks are valid code evidence; do not invent external code.'
    if line.startswith('- Cite only diff:') else
    '- Return at most 3 context_requests. Allowed operations: ' + ', '.join(ad.OPERATIONS) + '. '
    'Keep candidate_id, obligation_id, and challenge_id empty in requests. '
    'If no lookup allowance remains, answer from supplied evidence and return context_requests: [].'
    if line.startswith('- Do not request tools') else line
    for line in ad.B0_CONTRACT.splitlines())


def response_schema(arm=None):
    if arm is not None and arm not in ARMS:
        raise ValueError(f'unknown ladder arm: {arm}')
    return copy.deepcopy(RESPONSE_SCHEMA)


class OutputValidationError(ad.AdapterError):
    pass


class LadderState(ad.ReviewState):
    def __init__(self, *, component_arm, initial_allocated, **kwargs):
        super().__init__(arm='P', **kwargs)
        self.component_arm = component_arm
        self.initial_allocated = initial_allocated
        self.initial_metadata = []
        self.raw_attempts = []
        self.actual_inputs = []

    def suffix_extra(self):
        used = self.initial_allocated + self.accounting.total
        rounds = max(0, 3 - self.calls) if self.component_arm == 'search' else 0
        allowance = min(3, max(0, 6 - used)) if rounds else 0
        budget = (
            f'<lookup_budget>initial preparation queries allocated: {self.initial_allocated}; '
            f'actual runtime queries: {self.accounting.total}; allocated total used: {used} of 6; '
            f'rounds left after this response: {rounds}; '
            f'lookups that can still run after this response: {allowance}. '
            'Initial preparation allocation is not a count of physical queries repeated in this review.'
            + (' This response is final; no further lookup can run.' if allowance == 0 else '')
            + '</lookup_budget>')
        parts = ['\n' + LADDER_CONTRACT, budget]
        self.state_block = {'kind': 'none', 'tokens': 0}
        if self.component_arm == 'search' and self.prev_payload is not None and self.calls > 1:
            # A normal previous-review digest, not a checked or persistent ledger.
            prior = {**self.prev_payload, 'candidates': []}
            text, tokens, omitted = ad.digest_previous(prior, LIMITS['state_tokens_per_call'])
            if not self.account.check_state_tokens(tokens, f'stage_{self.calls - 1}'):
                raise ad.StateBlocked('previous review digest exceeds the 1600-token cap')
            self.state_block = {'kind': 'previous_response_digest', 'tokens': tokens, 'omitted': omitted}
            parts.append('<previous_response>\n' + text + '\n</previous_response>')
        return '\n'.join(parts) + '\n'

    def capture_raw(self, raw, *, failure=False):
        payload = None
        if isinstance(raw, str):
            try:
                value = json.loads(raw)
                if isinstance(value, dict):
                    payload = value
            except (ValueError, TypeError):
                pass
        self.raw_attempts.append({'stage': ad.STAGES[self.calls - 1], 'raw': raw,
                                  'payload': payload, 'failure': failure})
        return payload


class LadderClient:
    def __init__(self, inner, state):
        self.inner, self.state = inner, state

    def complete(self, *, system, prompt, suffix='', shared=''):
        state = self.state
        if getattr(self.inner, '_stage_calls', 0) >= 1:
            state.refused_retries.append({'stage': ad.STAGES[state.calls - 1],
                                          'error': 'RetryRefused'})
            raise OutputValidationError('a second completion in the same stage is prohibited')
        parts = dict(system=system, shared=shared, prompt=prompt, suffix=suffix + state.suffix_extra())
        state.rendered_seed_ids = ad.check_rendered_seed_ids(state.inputs, '\n'.join(parts.values()))
        state.actual_inputs.append({'stage': ad.STAGES[state.calls - 1],
                                    'parts_sha256': {k: oc._sha(v) for k, v in parts.items()},
                                    'parts': parts})
        try:
            raw = self.inner.complete(**parts)
        except Exception as exc:
            record = getattr(exc, 'record', None)
            state.capture_raw(record.get('raw_response') if isinstance(record, dict) else None, failure=True)
            raise
        payload = state.capture_raw(raw)
        try:
            jsonschema.validate(payload, RESPONSE_SCHEMA)
        except jsonschema.ValidationError as exc:
            state.raw_attempts[-1]['failure'] = True
            raise OutputValidationError(f'response schema: {exc.message[:400]}') from exc
        return raw


@contextlib.contextmanager
def product_hooks(state, product):
    """Keep the product's post-review ordering and dedup; suppress only pre-gap work.

The common P wrappers add stage accounting, ID capture, and disable hidden
format-reflection calls. All four intervention flags remain false. Its suffix
and previous digest are replaced by LadderState.suffix_extra above.
"""
    orch = product.orchestrator
    names = ('detect_public_reference_gaps', 'detect_related_test_gaps', 'detect_terraform_graph_gaps')
    originals = {name: getattr(orch, name) for name in names}
    original_run = orch.run_llm_review_result

    def with_initial(diff, packet, client, additional_evidence=None, **kwargs):
        evidence = [*state.initial_metadata, *(additional_evidence or [])]
        return original_run(diff, packet, client, additional_evidence=evidence, **kwargs)

    try:
        for name in names:
            setattr(orch, name, lambda *a, **kw: [])
        orch.run_llm_review_result = with_initial
        with ad.scoped_patches(state, product):
            yield
    finally:
        orch.run_llm_review_result = original_run
        for name, fn in originals.items():
            setattr(orch, name, fn)


def _initial_metadata(inputs, product, account):
    items, audit, used = [], [], 0
    for row in inputs.get('component_initial_metadata', []):
        ev = oc.Evidence(row['id'], row.get('snapshot', 'head'), row['path'], text=row['content'],
                         provenance=row['provenance'], backend=row.get('backend'),
                         content_kind=row['content_kind'])
        if ev.id in account.store:
            raise ad.AdapterError(f'initial metadata duplicates an evidence id: {ev.id}')
        tokens = ad.ref_tokens(ev.text)
        used += tokens
        if used > account.budgets.metadata_tokens_per_call:
            raise ad.BudgetViolation('frozen initial metadata exceeds cumulative metadata cap')
        account.store.add(ev)
        items.append(product.models.ContextEvidence(
            id=ev.id, request_id='initial-preparation', operation=row.get('operation', 'initial_locator'),
            source=row.get('source', row['path']), content=ev.text,
            metadata={'initial_preparation': True, 'provenance': ev.provenance, 'backend': ev.backend}))
        audit.append({'id': ev.id, 'path': ev.file, 'content_sha256': oc._sha(ev.text),
                      'reference_tokens': tokens, 'provenance': ev.provenance,
                      'backend': ev.backend, 'content_kind': ev.content_kind})
    return items, audit, used


def _source_audit(inputs, metadata):
    source = [{'id': ev.id, 'path': ev.file, 'snapshot': ev.snapshot,
               'lines': [n for n, _ in ev.lines], 'content_sha256': oc._sha(ev.lines),
               'provenance': ev.provenance, 'backend': ev.backend}
              for ev in inputs['seed_evidence']]
    return {'source': source, 'metadata': metadata, 'source_sha256': oc._sha(source),
            'metadata_sha256': oc._sha(metadata), 'evidence_sha256': oc._sha([source, metadata]),
            'packet_audit': inputs.get('component_packet_audit', {})}


def _initial_native_summary(inputs, initial_queries):
    locators = [r for r in initial_queries.get('requests', [])
                if r.get('operation') in ('go_to_definition', 'find_references')]
    return {
        'allocated_locator_attempts': len(locators),
        'locators_with_native_results': sum(bool(r.get('native_result_count', 0)) for r in locators),
        'native_returned_locations': sum(r.get('native_result_count', 0) for r in locators),
        'native_source_items_supplied': sum(ev.provenance == 'native_lsp' and ad.is_native(ev.backend)
                                             for ev in inputs['seed_evidence']),
        'metadata_audit_items_supplied': len(inputs.get('component_initial_metadata', [])),
        'note': ('Preparation is shared, not rerun here. Returned locations may be omitted by metadata caps '
                 'or rejected as body targets. An empty/failed locator audit is not a resolved native item.')}


def _final_validation(findings, provided):
    # A failed transport may contain JSON that never passed the schema. Audit
    # that raw array without crashing, dropping entries, or shifting indices.
    audit = []
    for index, finding in enumerate(findings):
        if not isinstance(finding, dict):
            audit.append({'index': index, 'cited': [], 'valid': [], 'invalid': [],
                          'has_trigger_mechanism_impact': False, 'has_location': False,
                          'ledger_verdict': None,
                          'final_validity': {'ok': False, 'reasons': ['malformed_finding']}})
            continue
        evidence = finding.get('evidence', [])
        malformed = not isinstance(evidence, list)
        evidence = evidence if isinstance(evidence, list) else []
        cited = [e.get('evidence_id') for e in evidence if isinstance(e, dict)]
        malformed = malformed or any(not isinstance(e, dict) for e in evidence)
        valid = [c for c in cited if isinstance(c, str) and c in provided]
        invalid = [c for c in cited if not isinstance(c, str) or c not in provided]
        row = {'index': index, 'cited': cited, 'valid': valid, 'invalid': invalid,
               'has_trigger_mechanism_impact': all(isinstance(finding.get(k), str) and finding[k].strip()
                                                  for k in ('trigger', 'mechanism', 'impact')),
               'has_location': bool(finding.get('path')) and type(finding.get('line_start')) is int}
        reasons = []
        if malformed:
            reasons.append('malformed_evidence')
        if not row['cited']:
            reasons.append('no_citation')
        if row['invalid']:
            reasons.append('invalid_citation')
        if not row['has_trigger_mechanism_impact']:
            reasons.append('missing_trigger_mechanism_impact')
        if not row['has_location']:
            reasons.append('missing_location')
        row['ledger_verdict'] = None
        row['final_validity'] = {'ok': not reasons, 'reasons': reasons}
        audit.append(row)
    return audit


def run_review(key, arm, inner_client, *, prepared_inputs=None, prepared_packet=None,
               product=None, registry_class=None, expected_inputs=None):
    if arm not in ARMS:
        raise ValueError(f'unknown ladder arm: {arm}')
    product = product or ad.load_product()
    started = time.time()
    if prepared_inputs is None:
        import packet_builder
        inputs = packet_builder.build_inputs(key, arm, prepared_packet=prepared_packet, product=product)
    else:
        inputs = dict(prepared_inputs)
    if inputs['key'] != key:
        raise ad.AdapterError('prepared packet key does not match review key')
    if expected_inputs is not None:
        for name, value in inputs['data']['authoritative'].items():
            if expected_inputs.get(name) != value:
                raise ad.AdapterError(f'{key}: authoritative input changed: {name}')
    initial_queries = inputs.get('component_initial_queries', {'count': 0, 'requests': [], 'batches': []})
    initial_count = initial_queries.get('count', 0)
    if type(initial_count) is not int or not 0 <= initial_count <= 3:
        raise ad.AdapterError('initial query allocation must be an integer from 0 to 3')
    if arm in ('diff', 'ast') and initial_count:
        raise ad.AdapterError('diff/AST cannot carry an LSP preparation query allocation')
    if arm == 'diff' and (inputs['seed_evidence'] or inputs['packet'].snippets
                          or inputs.get('component_initial_metadata') or inputs['packet'].seed_symbols
                          or inputs['packet'].seed_literals or inputs['packet'].risk_tags):
        raise ad.AdapterError('diff arm contains structural/source evidence')
    runtime_cap = 6 - initial_count if arm == 'search' else 0
    runtime_limits = {**LIMITS, 'queries_total': runtime_cap,
                      'rounds_after_initial': 2 if arm == 'search' else 0}
    accounting = ad.QueryAccounting(runtime_limits)
    account = oc.EvidenceAccount(oc.Budgets())
    masked = ad.MaskedSources(inputs['data']['workspace_root'])
    state = LadderState(component_arm=arm, initial_allocated=initial_count, inputs=inputs, account=account,
                        masked=masked, accounting=accounting, client_inner=inner_client, product=product)
    account.add_seed(inputs['seed_evidence'])
    for ev in ad.diff_core_evidence(inputs['diff'], product):
        account.store.add(ev)
    metadata, metadata_audit, metadata_tokens = _initial_metadata(inputs, product, account)
    state.initial_metadata = metadata
    inputs['provided_base_ids'] = ([e.id for e in inputs['seed_evidence']]
                                  + [e.id for e in ad.diff_core_evidence(inputs['diff'], product)]
                                  + [e.id for e in metadata])
    if len(inputs['provided_base_ids']) != len(set(inputs['provided_base_ids'])):
        raise ad.AdapterError('duplicate prepared evidence IDs')
    source_audit = _source_audit(inputs, metadata_audit)
    seed_tokens = account.exposed_tokens()
    if seed_tokens > LIMITS['source_union_reference_tokens']:
        raise ad.BudgetViolation('initial packet exceeds source union cap')
    verify = ad.verify_workspace(inputs['data']['workspace_root'], inputs['data']['workspace_sources'])
    if expected_inputs is not None and expected_inputs.get('workspace_tree_sha256') != verify['tree_sha256']:
        raise ad.AdapterError('workspace tree differs from frozen input')
    agreement = ad.seed_agreement(inputs, masked)
    if agreement['mismatched']:
        raise ad.AdapterError('prepared source disagrees with masked workspace')
    config = replace(ad.pilot_config(product), agentic_exploration_enabled=arm == 'search',
                     agentic_gap_detection_enabled=arm == 'search', agentic_max_requests=runtime_cap,
                     agentic_max_iterations=2 if arm == 'search' else 0, lsp_enabled=arm == 'search')
    if arm == 'search':
        cls = registry_class or ad.make_registry_class(product)
        registry = cls(inputs['data']['workspace_root'], packet_id=key, account=account, masked=masked,
                       accounting=accounting, state=state, config=config)
    else:
        registry = ad.NoLookupRegistry(inputs['data']['workspace_root'])
    registry.meta_used = metadata_tokens
    registry.meta_log.extend({'initial_preparation': True, **row} for row in metadata_audit)
    state.registry = registry
    result, error = None, None
    try:
        try:
            with ad.seed_rendering(inputs, product), product_hooks(state, product):
                result = product.orchestrator.run_agentic_review(
                    inputs['diff'], inputs['packet'], LadderClient(inner_client, state), registry, config,
                    review_context=inputs['review_context'])
        except Exception as exc:
            error = exc
        output = ad.finalize(key, 'P', inputs, state, registry, account, accounting, masked, result, error,
                             verify, agreement, seed_tokens, started, time.time(), product)
        last_parseable = next((x for x in reversed(state.raw_attempts) if isinstance(x['payload'], dict)), None)
        last_attempt = state.raw_attempts[-1] if state.raw_attempts else None
        final_stage = ad.STAGES[state.calls - 1] if state.calls else None
        parsed = (last_attempt if last_attempt and last_attempt['stage'] == final_stage
                  and isinstance(last_attempt['payload'], dict) else None)
        raw_findings = (parsed['payload'].get('findings', []) if parsed else [])
        raw_findings = raw_findings if isinstance(raw_findings, list) else []
        audit = _final_validation(raw_findings, set(state.provided_ids))
        invalid = [row['index'] for row in audit if not row['final_validity']['ok']]
        if output['status'] == 'completed' and invalid:
            output['status'] = 'invalid_final_report'
        final_ok = result is not None and output['status'] == 'completed'
        output.update(review_id=f'{key}_{arm}', arm=arm, policy=dict(POLICY),
                      limits={**LIMITS, 'model_calls': 3 if arm == 'search' else 1,
                              'runtime_queries_total': runtime_cap,
                              'rounds_after_initial': 2 if arm == 'search' else 0},
                      findings_model_raw=raw_findings,
                      findings_model_raw_stage=parsed['stage'] if parsed else None,
                      raw_findings_are_final_success=final_ok,
                      citation_audit=audit, invalid_final_finding_indices=invalid,
                      reportable_finding_indices=[r['index'] for r in audit if r['final_validity']['ok']]
                      if final_ok else [],
                      initial_preparation_queries_allocated=initial_count,
                      runtime_tool_queries=accounting.total,
                      component_initial_queries=initial_queries,
                      initial_native_summary=_initial_native_summary(inputs, initial_queries),
                      runtime_native_summary=copy.deepcopy(output['native_summary']),
                      native_summary_scope='runtime only; initial preparation is in initial_native_summary',
                      initial_source=source_audit,
                      initial_metadata=[dataclasses.asdict(x) for x in metadata],
                      ignored_requests=[{'stage': x['stage'], 'requests': x['payload'].get('context_requests', []),
                                         'reason': 'static_arm_runtime_queries_disabled'}
                                        for x in state.raw_attempts if arm != 'search' and x['payload']
                                        and x['payload'].get('context_requests')],
                      raw_attempts=[{k: v for k, v in x.items() if k != 'raw'} for x in state.raw_attempts],
                      last_parseable_payload=last_parseable['payload'] if last_parseable else None,
                      last_parseable_stage=last_parseable['stage'] if last_parseable else None,
                      initial_input={'model_called': bool(state.actual_inputs), 'pre_gap_queries': 0,
                                     'pre_gap_evidence_ids': [], 'differs_from_pure_seed': False,
                                     'initial_evidence_ids': inputs['provided_base_ids'],
                                     'initial_evidence_sha256': source_audit['evidence_sha256'],
                                     'parts_sha256': state.actual_inputs[0]['parts_sha256'] if state.actual_inputs else {},
                                     'note': 'frozen source/metadata only; product pre-gap lookup suppressed'},
                      input_parts_sha256=[{'stage': x['stage'], **x['parts_sha256']} for x in state.actual_inputs])
        output['queries'] = {**accounting.summary(), 'total': initial_count + accounting.total,
                             'initial_allocated': initial_count, 'runtime_actual': accounting.total,
                             'runtime_limit': runtime_cap, 'budget_limit': 6,
                             'accounting_basis': 'allocated_initial_plus_actual_runtime; not physical total'}
        output['metadata_allocation']['initial_tokens'] = metadata_tokens
        output['metadata_allocation']['rule'] = 'initial plus runtime metadata retained cumulatively, at most 800 tokens'
        return output
    finally:
        ad.lsp_shutdown(registry)
