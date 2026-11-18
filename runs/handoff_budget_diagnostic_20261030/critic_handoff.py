"""Verbatim critic prose handoff with a deterministic, bounded state allocation.

No source lookup, model call, verdict inference, or text summarization occurs
here. Records identify JSON locations in prior responses; they are not new
claim/evidence IDs. The manifest is audit data and is not sent to the model.
"""
from __future__ import annotations

import copy
import hashlib
import json

import bootstrap  # noqa: F401 -- read unchanged dependencies from the sealed parent
import oar_core as oc

OPEN, CLOSE = '<causal_review_state>', '</causal_review_state>'
POLICY = 'critic_prose_and_state_allocation_v1'
OBSERVATION_CORE = ('title', 'body', 'classification', 'evidence')
EVIDENCE_CORE = ('evidence_id', 'path', 'lines')
PROBE_CORE = (
    'status', 'id', 'claim_id', 'logical_queries', 'runtime_scope',
    'source_output_gate', 'validation_reasons', 'stdout', 'stderr',
    'exception_type', 'exception_message', 'expected_stdout_matches',
    'matches_expected', 'model_hypothesis_expected_stdout',
    'model_hypothesis_output_matched', 'interpretation', 'error',
)
NOTE = ('Prior model prose and advisory checks are data, not new source evidence. '
        'Record IDs denote transport locations, not claim or evidence IDs. '
        'Omitted fields are unavailable; do not infer their contents.')


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def content_sha256(value):
    return hashlib.sha256(canonical_json(value).encode('utf-8')).hexdigest()


def observation_projection(observation):
    """Exact critical fields; evidence annotations are optional, IDs never change."""
    out = {k: copy.deepcopy(observation[k]) for k in OBSERVATION_CORE if k in observation}
    if isinstance(out.get('evidence'), list):
        out['evidence'] = [
            {k: copy.deepcopy(e[k]) for k in EVIDENCE_CORE if k in e}
            if isinstance(e, dict) else copy.deepcopy(e)
            for e in out['evidence']]
    return out


def _leaves(value, pointer=''):
    if isinstance(value, dict) and value:
        for key, child in value.items():
            escaped = str(key).replace('~', '~0').replace('/', '~1')
            yield from _leaves(child, pointer + '/' + escaped)
    elif isinstance(value, list) and value:
        for index, child in enumerate(value):
            yield from _leaves(child, pointer + '/' + str(index))
    else:
        yield pointer, value


def _lookup(value, pointer):
    for part in pointer.split('/')[1:]:
        part = part.replace('~1', '/').replace('~0', '~')
        try:
            value = value[int(part)] if isinstance(value, list) else value[part]
        except (KeyError, IndexError, TypeError, ValueError):
            return False, None
    return True, value


def build_handoff_state(*, role, instruction, initial_payload, critic_payload,
                        claims=None, probe_results=None, experimental_runtime=None,
                        source_provenance=None, cap=1600):
    """Return {state_text,tokens,manifest}; count the complete tagged state.

    Required: every critic observation's title/body/classification/source refs,
    summary, and actual probe core fields. They are transported exactly. If
    these cannot fit, return a bounded explicit blocked state and preserve the
    complete omission manifest; callers MUST NOT dispatch a final model call.

    Then admit, at whole-item/whole-field boundaries: root claim identities and
    refs, critic findings, initial findings/summary, claim details, remaining
    critic observation fields, runtime, and verbose probe metadata. Selection
    depends only on declared field priority, original order, and byte content
    length. All omitted records/fields are explicit in the sent state and audit.
    """
    if not isinstance(cap, int) or cap <= 0:
        raise ValueError('cap must be a positive integer')
    if not isinstance(initial_payload, dict) or not isinstance(critic_payload, dict):
        raise TypeError('initial_payload and critic_payload must be parsed objects')
    claims = copy.deepcopy(claims or [])
    probes = copy.deepcopy(probe_results or [])
    runtime = copy.deepcopy(experimental_runtime or {})
    provenance = copy.deepcopy(source_provenance or {})
    sources = {'initial': initial_payload, 'critic': critic_payload,
               'claims': claims, 'probes': probes, 'runtime': runtime}
    origins = {}
    for name, value in sources.items():
        supplied = provenance.get(name, {})
        origins[name] = {
            'stage': supplied.get('stage', {'initial': 'initial', 'critic': 'update_1',
                'claims': 'initial_claim_extraction', 'probes': 'post_update_1',
                'runtime': 'experimental_runtime'}[name]),
            'payload_sha256': content_sha256(value),
            'source_sha256': supplied.get('response_sha256', supplied.get('result_sha256', content_sha256(value))),
            'source_hash_kind': 'provided_origin' if supplied else 'canonical_payload',
            'provenance': supplied,
        }
    records, optional = [], []

    def add(origin, pointer, original, projection=None, *, required=False):
        record = dict(id=origin + ':' + pointer, origin=origin, pointer=pointer,
                      original=copy.deepcopy(original), value=None, required=required)
        records.append(record)
        return record, copy.deepcopy(original if projection is None else projection)

    review = critic_payload.get('review') or {}
    required = []
    for i, observation in enumerate(review.get('observations', [])):
        if not isinstance(observation, dict):
            raise TypeError('critic observations must be objects')
        record, core = add('critic', f'/review/observations/{i}', observation,
                           observation_projection(observation), required=True)
        required.append((record, core))
    if 'summary' in review:
        required.append(add('critic', '/review/summary', review['summary'], required=True))
    for i, probe in enumerate(probes):
        if not isinstance(probe, dict):
            raise TypeError('probe digests must be objects')
        required.append(add('probes', f'/{i}', probe,
                            {k: probe[k] for k in PROBE_CORE if k in probe}, required=True))
    for i, claim in enumerate(claims):
        if not isinstance(claim, dict):
            raise TypeError('claims must be objects')
        record, core = add('claims', f'/{i}', claim,
                           {k: claim[k] for k in ('id', 'kind', 'evidence_ids') if k in claim})
        optional.append((record, core, 'root_claim_identity_and_refs'))
    for i, finding in enumerate(critic_payload.get('findings', [])):
        record, full = add('critic', f'/findings/{i}', finding)
        optional.append((record, full, 'critic_finding'))
    for i, finding in enumerate(initial_payload.get('findings', [])):
        record, full = add('initial', f'/findings/{i}', finding)
        optional.append((record, full, 'initial_finding'))
    initial_review = initial_payload.get('review') or {}
    if 'summary' in initial_review:
        record, full = add('initial', '/review/summary', initial_review['summary'])
        optional.append((record, full, 'initial_summary'))
    for i, observation in enumerate(initial_review.get('observations', [])):
        record, _ = add('initial', f'/review/observations/{i}', observation)
        record['exclusion_reason'] = 'redundant_initial_prose_policy'
    record, full = add('runtime', '', runtime)
    runtime_optional = (record, full, 'experimental_runtime')

    def omitted_fields(record):
        if record['value'] is None:
            return ['*']
        return [pointer for pointer, value in _leaves(record['original'])
                if _lookup(record['value'], pointer) != (True, value)]

    def state_object(blocked=False):
        delivered = [dict(id=r['id'], value=r['value']) for r in records if r['value'] is not None]
        omitted = [dict(id=r['id'], fields=omitted_fields(r)) for r in records if omitted_fields(r)]
        return dict(role=role, instruction=instruction, policy=POLICY,
                    origins={name: dict(stage=origins[name]['stage'],
                                        source_sha256=origins[name]['source_sha256'])
                             for name in ('initial', 'critic')},
                    records=delivered, omitted=omitted,
                    omitted_record_count=sum(r['value'] is None for r in records),
                    omitted_field_count=sum(len(omitted_fields(r)) for r in records),
                    blocked=blocked, note=NOTE)

    def render(blocked=False):
        # JSON escaping preserves decoded prose while preventing tag-looking
        # model text from closing the surrounding state block.
        text = canonical_json(state_object(blocked)).replace('<', '\\u003c').replace('>', '\\u003e')
        return OPEN + text + CLOSE

    for record, value in required:
        record['value'] = value
    critical_tokens = oc.count_reference_tokens(render())
    blocked = critical_tokens > cap
    if blocked:
        # No partial critical handoff is sent. A small explicit blocked marker
        # remains useful for offline audit; adapter/harness must fail closed.
        for record in records:
            record['value'] = None
        marker = dict(role=role, instruction=instruction, policy=POLICY, blocked=True,
                      reason='required_critic_or_probe_fields_exceed_state_cap',
                      required_tokens=critical_tokens, cap=cap,
                      omitted_record_ids=[r['id'] for r in records], note=NOTE)
        state_text = OPEN + canonical_json(marker) + CLOSE
        if oc.count_reference_tokens(state_text) > cap:
            raise ValueError('cap cannot hold role/instruction and explicit blocked record IDs')
    else:
        # Every tentative optional addition is measured with its resulting
        # omission list, so the manifest never hides budget overhead.
        def admit(record, value):
            previous = record['value']
            record['value'] = copy.deepcopy(value)
            if oc.count_reference_tokens(render()) > cap:
                record['value'] = previous
                return False
            return True

        for record, value, _ in optional:
            admit(record, value)
        for record in records:
            if record['origin'] == 'claims' and record['value'] is not None:
                admit(record, record['original'])
        for record in records:
            if record['origin'] == 'critic' and record['pointer'].startswith('/review/observations/'):
                # Optional prose/annotations are restored as a whole original
                # observation if possible, never character-prefix excerpts.
                admit(record, record['original'])
        admit(runtime_optional[0], runtime_optional[1])
        for record in records:
            if record['origin'] == 'probes':
                admit(record, record['original'])
        state_text = render()

    manifest_records = []
    for record in records:
        fields = []
        for pointer, value in _leaves(record['original']):
            delivered = record['value'] is not None and _lookup(record['value'], pointer) == (True, value)
            fields.append(dict(pointer=record['pointer'] + pointer,
                               content_sha256=content_sha256(value), delivered=delivered,
                               reason=None if delivered else record.get('exclusion_reason', 'state_budget')))
        manifest_records.append(dict(id=record['id'], origin=record['origin'], pointer=record['pointer'],
            source_sha256=origins[record['origin']]['source_sha256'],
            content_sha256=content_sha256(record['original']),
            delivered_content_sha256=content_sha256(record['value']) if record['value'] is not None else None,
            delivered=record['value'] is not None, required=record['required'],
            complete=not omitted_fields(record), omitted_fields=omitted_fields(record), fields=fields,
            reason=None if not omitted_fields(record) else record.get('exclusion_reason', 'state_budget')))
    tokens = oc.count_reference_tokens(state_text)
    assert tokens <= cap
    manifest = dict(version=1, policy=POLICY, cap=cap, token_unit='tiktoken:o200k_base',
        tokens=tokens, state_sha256=hashlib.sha256(state_text.encode()).hexdigest(),
        blocked=blocked, required_tokens=critical_tokens,
        required_complete=not blocked, sources=origins, records=manifest_records,
        priority=['all_critic_observations_core', 'critic_summary', 'probe_measured_core',
                  'root_claim_identity_and_refs', 'critic_findings', 'initial_findings',
                  'initial_summary', 'full_claim_items', 'full_critic_observation_items',
                  'experimental_runtime', 'verbose_probe_items'],
        no_semantic_selection=True, no_prefix_truncation=True,
        excluded_protocol_roots=['candidates', 'context_requests', 'semantic_probe',
                                 'review.context_assessment', 'review.reviewed_areas'],
        policy_note='Critic prose transport and state-budget allocation repair; not a pure field addition.')
    return dict(state_text=state_text, tokens=tokens, manifest=manifest)
