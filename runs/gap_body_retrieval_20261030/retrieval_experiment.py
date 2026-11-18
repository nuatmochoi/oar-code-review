"""Freeze and execute 90 fresh final judgments on prepared retrieval variants.

Preparation and native retrieval are separate. This module never replays the
initial/critic model stages, performs retrieval, changes a prompt, repairs an
answer, or automatically retries an incomplete logical claim. Only the shared
reliable transport may retry one response-less transient physical attempt.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import copy
from datetime import datetime, timezone
import fcntl
import hashlib
import json
from pathlib import Path
import re

from bootstrap import ROOT
import cli_transport as legacy
import reliable_transport as transport
import oar_core as oc
from source_context import SourceAccount, SOURCE_CAP

ARMS = ('replay', 'frontier', 'gap')
EXPECTED_GROUPS, EXPECTED_REVIEWS = 30, 90
MODEL, EFFORT, WORKERS, INTERVAL = 'gpt-5.6-sol', 'low', 4, 3
CONFIGURATION_ERROR = re.compile(r'invalid_json_schema|invalid schema for response_format|'
                                  r'unsupported (?:model|reasoning_effort)|model_not_found', re.I)


def load(path):
    return json.loads(Path(path).read_text())


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            digest.update(block)
    return digest.hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_new(path, value)


def write_once_equal(path, value):
    path = Path(path)
    if path.exists():
        require(load(path) == value, f'preserved artifact differs: {path}')
    else:
        write(path, value)


def _inside(root, rel):
    path = (Path(root)/rel).resolve()
    require(not Path(rel).is_absolute() and path.is_relative_to(Path(root).resolve()), f'unsafe relative path: {rel}')
    return path


def _reference(ref):
    require(isinstance(ref, dict) and isinstance(ref.get('path'), str) and isinstance(ref.get('sha256'), str), 'invalid reference')
    path = Path(ref['path'])
    require(path.is_absolute() and path.is_file(), f'missing absolute reference: {path}')
    require(sha(path) == ref['sha256'], f'reference SHA changed: {path}')
    return load(path)


def validate_jobs(jobs):
    require(jobs.get('branches') == EXPECTED_REVIEWS, 'expected 90 prepared branches')
    require(set(jobs.get('arms', [])) == set(ARMS), 'prepared arms changed')
    blocks = jobs.get('blocks', [])
    require(len(blocks) == EXPECTED_GROUPS, 'expected 30 fixed prefix groups')
    expected = {}
    for block in blocks:
        group = block['group_id']
        require(re.fullmatch(r'[A-Za-z0-9_]+', group) is not None, 'invalid group ID')
        require(len(block['arms']) == 3 and set(block['arms']) == set(ARMS), f'invalid group arms: {group}')
        for arm in block['arms']:
            rid = f'{group}_{arm}'
            require(rid not in expected, f'duplicate prepared group: {group}')
            expected[rid] = dict(review_id=rid, group_id=group, arm=arm,
                case=block['case_id'], direction=block['direction'], repeat=block['repeat'], dataset=block['dataset'])
    require(len(expected) == EXPECTED_REVIEWS, 'prepared coverage mismatch')
    return expected


def _policy_check(root):
    policy = load(root/'policy_freeze.json')
    require(policy.get('query_cap') == 6 and policy.get('source_cap') == SOURCE_CAP and
            policy.get('state_cap') == 5120 and policy.get('chunk_lines') == 200, 'policy budgets changed')
    require(set(policy.get('arms', [])) == set(ARMS), 'policy arms changed')
    require(policy.get('groups') == EXPECTED_GROUPS or
            isinstance(policy.get('groups'), list) and len(policy['groups']) == EXPECTED_GROUPS, 'policy group count changed')
    require(bool(policy.get('files')), 'empty policy file manifest')
    for rel, expected in policy['files'].items():
        require(sha(_inside(root, rel)) == expected, f'policy source changed: {rel}')
    return policy


def _seal_references(root):
    lineage = load(root/'lineage.json')
    refs = []
    for key in ('parent', 'repair', 'budget'):
        require(key in lineage and key+'_seal_sha256' in lineage, f'missing lineage {key} seal')
        refs.append({'name': key, 'path': str(Path(lineage[key])/'parent_grading/completed_result_seal.json'),
                     'sha256': lineage[key+'_seal_sha256']})
    return refs


def verify_parent_seals(refs):
    """Full preservation check, once at startup/freeze, never per review."""
    checked = {}
    for ref in refs:
        seal = _reference(ref)
        workspace = Path(seal['workspace']).resolve()
        require(isinstance(seal.get('files'), dict) and bool(seal['files']), 'empty parent seal')
        if 'file_count' in seal:
            require(seal['file_count'] == len(seal['files']), 'parent seal file_count')
        for rel, expected in seal['files'].items():
            path = _inside(workspace, rel)
            if path in checked:
                require(checked[path] == expected, f'inconsistent inherited seals: {path}')
            else:
                require(sha(path) == expected, f'parent sealed file changed: {path}')
                checked[path] = expected
    return {'seals': len(refs), 'unique_sealed_files_verified': len(checked)}


def _sources_by_id(rows):
    out = {}
    for row in rows:
        require(isinstance(row, dict) and isinstance(row.get('evidence_id'), str), 'source row missing evidence ID')
        eid = row['evidence_id']
        if eid in out:
            require(out[eid] == row, f'conflicting source evidence: {eid}')
        out[eid] = row
    return out


def validate_prepared(prepared, *, root=ROOT, expected=None):
    """Read-only integrity and composition checks; no model/native calls."""
    root = Path(root)
    rid = prepared['review_id']
    require(prepared['arm'] in ARMS and rid == prepared['group_id']+'_'+prepared['arm'], 'prepared identity')
    if expected is not None:
        require(all(prepared.get(k) == v for k, v in expected.items()), f'prepared job identity: {rid}')
    require(prepared.get('prefix_arm') == 'debate', f'inherited prefix changed: {rid}')
    require(set(prepared['raw']) == {'system','shared','prompt','suffix'} and
            all(isinstance(v, str) for v in prepared['raw'].values()), f'raw prompt fields: {rid}')
    text, parts = legacy.compose_input(prepared['order'], **prepared['raw'])
    require(oc._sha(text) == prepared['composed_sha256'], f'prepared composition SHA: {rid}')
    for ref in prepared['inherited_references'].values():
        _reference(ref)
    trace_path = Path(prepared['retrieval_reference']['path']).resolve()
    require(trace_path.is_relative_to((root/'retrieval').resolve()), f'retrieval reference outside frozen tree: {rid}')
    trace = _reference(prepared['retrieval_reference'])
    require(trace['arm'] == prepared['arm'], f'retrieval arm mismatch: {rid}')
    require(trace['new_query_cap'] == 6 and type(trace['new_logical_queries']) is int and
            0 <= trace['new_logical_queries'] <= 6, f'retrieval query budget: {rid}')
    require(trace['source_cap'] == SOURCE_CAP, f'retrieval source budget: {rid}')
    require(trace.get('visibility_stage') == 'prepared_composition_not_yet_a_model_call', f'acquisition mislabeled as actual delivery: {rid}')
    require(trace.get('source_output_gate') is False, f'unsupported source output gate: {rid}')
    events = trace['events']
    require(len(events) == trace['new_logical_queries'], f'retrieval event count: {rid}')
    obtained = []
    for index, event in enumerate(events, 1):
        require(event['sequence'] == index and event['result']['logical_queries'] == 1, f'atomic query accounting: {rid}')
        for source in event['admitted_sources']:
            require(event['request']['operation'] == 'read_lines', f'position admitted as source: {rid}')
            require(type(event['request'].get('start')) is int and type(event['request'].get('end')) is int and
                    1 <= event['request']['start'] <= event['request']['end'] and
                    event['request']['end']-event['request']['start']+1 <= 200, f'body chunk budget: {rid}')
            returned = {n: t for n, t in event['result'].get('lines', [])}
            require(source['file'] == event['request']['path'] and source['snapshot'] == 'head', f'admitted source identity: {rid}')
            require(all(returned.get(n) == t and event['request']['start'] <= n <= event['request']['end'] for n,t in source['lines']),
                    f'admission not executed source: {rid}')
            obtained.append(source)
    protected = prepared['protected_sources']
    extra = prepared['extra_sources']
    require(_sources_by_id(extra) == _sources_by_id(obtained), f'extra source does not match acquisition: {rid}')
    require(set(_sources_by_id(protected)) <= set(prepared['provided_evidence_ids']) and
            set(_sources_by_id(extra)) <= set(prepared['provided_evidence_ids']), f'provided source ID list: {rid}')
    account = SourceAccount(protected + extra)
    initial_account = SourceAccount(protected)
    require(account.tokens <= SOURCE_CAP and account.tokens == trace['final_source_tokens'] and
            initial_account.tokens == trace['initial_source_tokens'], f'source union accounting: {rid}')
    cleanup = trace['native_executor']['cleanup']
    if prepared['arm'] == 'replay':
        require(not extra and trace['new_logical_queries'] == 0 and cleanup.get('not_started') is True, f'replay executed retrieval: {rid}')
    else:
        require(cleanup.get('passed') is True, f'native source cleanup incomplete: {rid}')
    observed = account.verify_delivery(prepared['raw'], allowed_prior_ids=prepared['provided_evidence_ids'])
    require(observed == prepared['composed_visibility'], f'composition visibility mismatch: {rid}')
    return {'trace': trace, 'account': account, 'observed': observed, 'parts': parts}


def _freeze_files(root):
    files = set(root.glob('*.py'))
    for name in ('PROTOCOL_KO.md', 'lineage.json', 'policy_freeze.json'):
        require((root/name).is_file(), f'missing freeze input: {name}')
        files.add(root/name)
    for folder in ('prepared', 'retrieval', 'verification', 'parent_grading'):
        for path in (root/folder).rglob('*'):
            if path.is_file() and not any(p in ('__pycache__','.pytest_cache') for p in path.parts) and path.suffix not in ('.pyc','.pyo'):
                files.add(path)
    return sorted(files)


def freeze(root=ROOT):
    root = Path(root).resolve()
    require(not (root/'freeze.json').exists(), 'run already frozen; never replace a freeze')
    require(not list((root/'results').glob('**/*.claim.json')), 'model claims exist before freeze')
    _policy_check(root)
    check = load(root/'verification/preflight.json')
    require(check.get('passed') is True and check.get('source_integrity_passed') is True and
            check.get('cleanup_passed') is True, 'preflight source/cleanup checks failed')
    require(check.get('prepared_inputs') == EXPECTED_REVIEWS and check.get('prefixes') == EXPECTED_GROUPS,
            'preflight coverage mismatch')
    require(load(root/'verification/aggregate_tests.json').get('passed') is True, 'aggregate tests did not pass')
    if (root/'verification/aggregate_tests_v2.json').exists():
        require(load(root/'verification/aggregate_tests_v2.json').get('passed') is True, 'aggregate regression tests did not pass')
    from causal_adapter import validate_strict_schema
    validate_strict_schema(load(root/'prepared/response_schema.json'))
    jobs = load(root/'prepared/jobs.json')
    expected = validate_jobs(jobs)
    require({p.stem for p in (root/'prepared/inputs').glob('*.json')} == set(expected), 'prepared file set mismatch')
    for rid, identity in expected.items():
        validate_prepared(load(root/'prepared/inputs'/f'{rid}.json'), root=root, expected=identity)
    refs = _seal_references(root)
    preservation = verify_parent_seals(refs)
    files = _freeze_files(root)
    frozen = dict(created_at=datetime.now(timezone.utc).isoformat(),
        files={str(p.relative_to(root)): sha(p) for p in files}, parent_seals=refs,
        parent_preservation=preservation, policy_freeze_sha256=sha(root/'policy_freeze.json'),
        model=MODEL, reasoning_effort=EFFORT, workers=WORKERS, min_start_interval=INTERVAL,
        new_calls=EXPECTED_REVIEWS, max_new_physical_attempts=2*EXPECTED_REVIEWS, stage='update_2',
        arms=list(ARMS), groups=EXPECTED_GROUPS, seed=jobs.get('seed'),
        source_cap=SOURCE_CAP, new_query_cap=6, state_cap=5120,
        runtime_note='Final judgments only. Retrieval occurred during preparation; inherited stages/probes are references.')
    write(root/'freeze.json', frozen)
    return {'frozen_files': len(files), 'sha256': sha(root/'freeze.json'), 'parent_preservation': preservation}


def verify_freeze(root=ROOT, *, verify_parents=True):
    root = Path(root).resolve()
    frozen = load(root/'freeze.json')
    require(frozen['model'] == MODEL and frozen['reasoning_effort'] == EFFORT and
            frozen['workers'] == WORKERS and frozen['min_start_interval'] == INTERVAL and
            frozen['new_calls'] == EXPECTED_REVIEWS and frozen['stage'] == 'update_2', 'execution policy changed')
    for rel, expected in frozen['files'].items():
        require(sha(_inside(root, rel)) == expected, f'frozen file changed: {rel}')
    # Newly inserted runtime/prepared files may affect imports/selection even if
    # all old files still match. Analysis outputs after the freeze are allowed.
    immutable_now = {str(p.relative_to(root)) for p in root.glob('*.py')}
    for folder in ('prepared', 'retrieval'):
        immutable_now |= {str(p.relative_to(root)) for p in (root/folder).rglob('*') if p.is_file()}
    immutable_frozen = {p for p in frozen['files'] if '/' not in p and p.endswith('.py') or p.startswith(('prepared/','retrieval/'))}
    require(immutable_now == immutable_frozen, 'immutable runtime/prepared tree changed')
    require(sha(root/'policy_freeze.json') == frozen['policy_freeze_sha256'], 'policy freeze changed')
    _policy_check(root)
    require(_seal_references(root) == frozen['parent_seals'], 'lineage changed')
    if verify_parents:
        return verify_parent_seals(frozen['parent_seals'])
    # Still check the seal documents in per-artifact callers, without rehashing
    # all ~27k inherited files. Official run() always requests full verification.
    for ref in frozen['parent_seals']: _reference(ref)
    return {'seals': len(frozen['parent_seals']), 'full_parent_verification': False}


def _invalid_citations(findings, provided):
    return [i for i, f in enumerate(findings) if not isinstance(f, dict) or
            not isinstance(f.get('evidence', []), list) or
            any(not isinstance(e, dict) or e.get('evidence_id') not in provided for e in f.get('evidence', []))]


def _delivery_audit(folder, prepared, inp, observed):
    protected_ids = {s['evidence_id'] for s in prepared['protected_sources']}
    extra_ids = {s['evidence_id'] for s in prepared['extra_sources']}
    return dict(schema_version=1, stage='explicitly_actual_model_input', review_id=prepared['review_id'],
        logical_stage='update_2', input_reference={'path': str(folder/'update_2.input.json'), 'sha256': sha(folder/'update_2.input.json')},
        composed_sha256=inp['composed_sha256'], raw_part_sha256={k: oc._sha(v) for k,v in inp['raw'].items()},
        retrieval_reference=copy.deepcopy(prepared['retrieval_reference']),
        protected_source_evidence_ids=sorted(protected_ids), additional_source_evidence_ids=sorted(extra_ids),
        protected_sources_sha256=oc._sha(prepared['protected_sources']), extra_sources_sha256=oc._sha(prepared['extra_sources']),
        observed=observed, acquisition_and_composition_are_separate=True,
        evidence_complete=not observed['incomplete_source_evidence_ids'],
        note='Measured only from saved actual completion input. SourceAccount was reconstructed with protected+extra sources for comparison; its initial_tokens is the total verification store, not new retrieval or inherited cost.')


def execute_one(group, arm, *, root=ROOT):
    root = Path(root).resolve()
    require(arm in ARMS and re.fullmatch(r'[A-Za-z0-9_]+', group) is not None, 'invalid review identity')
    rid = f'{group}_{arm}'
    folder = root/'results/reviews'/rid
    result_path = folder/'review.result.json'
    if result_path.exists():
        previous = load(result_path)
        require(previous['review_id'] == rid and previous['freeze_sha256'] == sha(root/'freeze.json'), 'preserved result identity/freeze')
        for key, name in (('prepared_sha256', root/'prepared/inputs'/f'{rid}.json'),
                          ('final_result_sha256', folder/'update_2.result.json'),
                          ('final_input_sha256', folder/'update_2.input.json')):
            require(previous[key] == sha(name), f'preserved result reference changed: {name}')
        _reference(previous['final_actual_delivery_audit'])
        _reference(previous['retrieval_reference'])
        return {'review_id': rid, 'preserved': True, 'status': previous['status']}
    gate = transport.AdmissionGate(root/'results/gate', max_live_calls=WORKERS, min_interval=INTERVAL)
    require(not gate.is_stopped(), 'STOP active; no new review claims allowed')
    prepared_path = root/'prepared/inputs'/f'{rid}.json'
    prepared = load(prepared_path)
    require(prepared['review_id'] == rid, 'prepared review identity')
    integrity = validate_prepared(prepared, root=root)
    write_once_equal(folder/'prefix.reference.json', {k:v for k,v in prepared.items() if k != 'raw'})
    client = transport.CliCompletionClient(out_dir=root/'results/reviews', review_id=rid,
        schema=load(root/'prepared/response_schema.json'), model=MODEL, order=prepared['order'],
        gate=gate, max_calls=1, timeout=300)
    for stage in legacy.STAGES:
        client.begin_stage(stage)
    stage_result = folder/'update_2.result.json'
    if not stage_result.exists():
        if (folder/'update_2.claim.json').exists():
            raise transport.ClaimedIncomplete(f'{rid}: existing incomplete claim will not be re-issued')
        try:
            client.complete(**prepared['raw'])
        except transport.CallFailed:
            pass  # last canonical attempt is preserved; never promote a prior answer
    record = load(stage_result)
    inp = load(folder/'update_2.input.json')
    require(inp['raw'] == prepared['raw'] and inp['composed_sha256'] == prepared['composed_sha256'], 'actual input differs from prepared input')
    text, parts = legacy.compose_input(prepared['order'], **inp['raw'])
    require(inp['composed_text'] == text and inp['parts'] == parts and record['input_sha256'] == oc._sha(text), 'actual composed input audit mismatch')
    require(record['review_id'] == rid and record['stage'] == 'update_2' and record['status'] in ('ok','error'), 'canonical final identity/status')
    require(1 <= record['physical_attempt_count'] <= 2 and record['retry_count'] == record['physical_attempt_count']-1, 'physical retry budget')
    observed = integrity['account'].verify_delivery(inp['raw'], allowed_prior_ids=prepared['provided_evidence_ids'])
    require(observed == prepared['composed_visibility'], 'actual delivery differs from frozen composition')
    audit_path = folder/'final_actual_delivery_audit.json'
    write_once_equal(audit_path, _delivery_audit(folder, prepared, inp, observed))
    try:
        payload = json.loads(record['raw_response']) if isinstance(record.get('raw_response'), str) else {}
    except ValueError:
        payload = {}
    findings = payload.get('findings', []) if isinstance(payload, dict) else []
    if not isinstance(findings, list): findings = []
    result = {k: prepared[k] for k in ('review_id','group_id','case','direction','repeat','dataset','arm','prefix_arm')}
    result.update(status='completed' if record['status'] == 'ok' else 'output_failure',
        payload=payload, findings_model_raw=findings, freeze_sha256=sha(root/'freeze.json'),
        prepared_sha256=sha(prepared_path), final_result_sha256=sha(stage_result),
        final_input_sha256=sha(folder/'update_2.input.json'), physical_attempt_count=record['physical_attempt_count'],
        retry_count=record['retry_count'], new_logical_calls=1, allocated_logical_judgments=3,
        inherited_references=prepared['inherited_references'],
        inherited_source_exposure=prepared['inherited_source_exposure'], inherited_queries=prepared['inherited_queries'],
        provided_evidence_ids=prepared['provided_evidence_ids'], invalid_citation_indices=_invalid_citations(findings, prepared['provided_evidence_ids']),
        retrieval_reference=prepared['retrieval_reference'],
        final_actual_delivery_audit={'path': str(audit_path), 'sha256': sha(audit_path)},
        new_retrieval_queries=integrity['trace']['new_logical_queries'],
        judgments_note='Two inherited judgments plus one fresh final; physical attempts include the common response-less retry, at most two.')
    write(result_path, result)
    trace_path = folder/'update_2.trace.json'
    if trace_path.exists():
        trace = load(trace_path)
        # Configuration errors are transport diagnostics, never searched inside
        # model prose. A bad schema/model stops all subsequent physical starts.
        errors = str(trace.get('stderr', '')) + '\n' + '\n'.join(
            json.dumps(event, ensure_ascii=False) for event in transport._events(trace.get('stdout',''))
            if event.get('type') in legacy.ERROR_EVENT_TYPES)
        if CONFIGURATION_ERROR.search(errors):
            gate.stop('provider_configuration_error', rid)
    return {'review_id': rid, 'status': result['status'], 'attempts': record['physical_attempt_count']}


def execute_block(block, *, root=ROOT):
    root = Path(root)
    results = []
    for arm in block['arms']:
        if (root/'results/gate/STOP').exists(): break
        row = execute_one(block['group_id'], arm, root=root)
        results.append(row)
        print(json.dumps(row), flush=True)
    return results


def run(root=ROOT):
    root = Path(root).resolve()
    with (root/'scheduler.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        preservation = verify_freeze(root)  # full three-parent scan, once per startup
        jobs = load(root/'prepared/jobs.json')['blocks']
        validate_jobs(load(root/'prepared/jobs.json'))
        gate = transport.AdmissionGate(root/'results/gate', max_live_calls=WORKERS, min_interval=INTERVAL)
        require(not gate.is_stopped(), 'STOP exists; inspect/archive it explicitly before resuming')
        print(json.dumps({'event':'scheduler_start','parent_preservation':preservation,'workers':WORKERS}), flush=True)
        # The predetermined first branch is an ordinary member of the frozen
        # jobs, never chosen/replaced according to the content of its answer.
        try:
            first = execute_one(jobs[0]['group_id'], jobs[0]['arms'][0], root=root)
        except Exception as exc:
            gate.stop(f'harness_error_before_fanout:{type(exc).__name__}:{exc}', jobs[0]['group_id'])
            raise
        print(json.dumps({'first': first}), flush=True)
        if first['status'] != 'completed':
            gate.stop('fixed_first_final_call_failed_before_fanout', first['review_id'])
            return status(root)
        with ProcessPoolExecutor(max_workers=WORKERS) as pool:
            futures = {pool.submit(execute_block, block, root=root): block['group_id'] for block in jobs}
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as exc:
                    gate.stop(f'harness_error:{type(exc).__name__}:{exc}', futures[future])
                    raise
        result = status(root)
        print(json.dumps({'event':'scheduler_end', **result}), flush=True)
        return result


def status(root=ROOT):
    root = Path(root)
    rows = [load(p) for p in (root/'results/reviews').glob('*/review.result.json')]
    attempts = [load(p) for p in (root/'results/reviews').glob('*/attempts/update_2/attempt_*/result.json')]
    claims = list((root/'results/reviews').glob('*/update_2.claim.json'))
    unfinished = [str(p.parent.name) for p in claims if not (p.parent/'update_2.result.json').exists()]
    return {'reviews': len(rows), 'expected': EXPECTED_REVIEWS,
        'completed': sum(r['status'] == 'completed' for r in rows),
        'failed': sum(r['status'] != 'completed' for r in rows),
        'physical_attempts_finished': len(attempts),
        'physical_attempts_in_final_records': sum(r['physical_attempt_count'] for r in rows),
        'retry_attempts_finished': sum(a.get('attempt', 1) > 1 for a in attempts),
        'incomplete_claims_not_reissued': sorted(unfinished),
        'STOP': load(root/'results/gate/STOP') if (root/'results/gate/STOP').exists() else None}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['freeze','verify-freeze','run','status'])
    parser.add_argument('--root', type=Path, default=ROOT)
    args = parser.parse_args()
    if args.command == 'verify-freeze': answer = verify_freeze(args.root)
    else: answer = globals()[args.command](args.root)
    print(json.dumps(answer, ensure_ascii=False))
