"""Frozen-prefix, fresh-final comparison. Never repeats initial/critic calls.

Primary: legacy debate vs repaired debate at the same saved critic/source/probe
prefix. Current review is an auxiliary fresh-final reference with its own prefix.
Original artifacts are read-only; new attempts are exclusive-create.
"""
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import copy
from datetime import datetime, timezone
import fcntl
import hashlib
import json
from pathlib import Path
import random
import re
import time

from bootstrap import ROOT, PARENT, WORKSPACE
import cli_transport as legacy
import reliable_transport as transport
import oar_core as oc

ARMS = ('current', 'legacy', 'repaired')
SEED = 2026103001
STATE = re.compile(r'<causal_review_state>(.*?)</causal_review_state>', re.S)

def load(path):
    return json.loads(Path(path).read_text())

def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(part)
    return h.hexdigest()

def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_new(path, value)

def source(group, arm):
    original_arm = 'current' if arm == 'current' else 'debate'
    directory = PARENT / 'results/reviews' / f'{group}_{original_arm}'
    paths = {f'{stage}_{kind}': directory / f'{stage}.{kind}.json'
             for stage in ('initial', 'update_1', 'update_2')
             for kind in ('input', 'result') if not (stage == 'update_2' and kind == 'result')}
    paths['review_result'] = directory / 'review.result.json'
    return original_arm, directory, paths

def prepare():
    import causal_adapter as adapter
    import critic_handoff
    adapter.validate_strict_schema(adapter.RESPONSE_SCHEMA)
    write(ROOT / 'prepared/response_schema.json', adapter.RESPONSE_SCHEMA)
    blocks = copy.deepcopy(load(PARENT / 'prepared/jobs.json')['blocks'])
    rng = random.Random(SEED)
    rng.shuffle(blocks)
    summaries = []
    for order, block in enumerate(blocks):
        group = block['group_id']
        block['arms'] = list(ARMS)
        rng.shuffle(block['arms'])
        block['order'] = order
        for arm in ARMS:
            original_arm, directory, refs = source(group, arm)
            original = load(refs['update_2_input'])
            review = load(refs['review_result'])
            raw = copy.deepcopy(original['raw'])
            handoff = None
            if arm == 'repaired':
                state_match = STATE.search(raw['suffix'])
                assert state_match and len(STATE.findall(raw['suffix'])) == 1
                prior_state = json.loads(state_match.group(1))
                initial_record = load(refs['initial_result'])
                critic_record = load(refs['update_1_result'])
                assert initial_record['status'] == critic_record['status'] == 'ok'
                causal = review['causal_audit']
                runtime = {k: v for k, v in review['causal_policy']['probe_runtime'].items() if k != 'python_executable'}
                built = critic_handoff.build_handoff_state(
                    role=prior_state['role'], instruction=prior_state['instruction'],
                    initial_payload=json.loads(initial_record['raw_response']),
                    critic_payload=json.loads(critic_record['raw_response']),
                    claims=causal['verification']['claims'],
                    probe_results=adapter.probe_digest(causal['probes']),
                    experimental_runtime=runtime,
                    source_provenance={
                        'initial': {'stage': 'initial', 'response_sha256': oc._sha(initial_record['raw_response']),
                                    'input_path': str(refs['initial_input']), 'input_sha256': sha(refs['initial_input']),
                                    'result_path': str(refs['initial_result']), 'result_sha256': sha(refs['initial_result'])},
                        'critic': {'stage': 'update_1', 'response_sha256': oc._sha(critic_record['raw_response']),
                                   'input_path': str(refs['update_1_input']), 'input_sha256': sha(refs['update_1_input']),
                                   'result_path': str(refs['update_1_result']), 'result_sha256': sha(refs['update_1_result'])},
                        'probes': {'result_path': str(refs['review_result']), 'result_sha256': sha(refs['review_result']),
                                   'json_pointer': '/causal_audit/probes'}},
                    cap=1600)
                state_text, handoff, tokens = built['state_text'], built['manifest'], built['tokens']
                assert tokens <= 1600
                assert not handoff['blocked'] and handoff['required_complete'], 'Required critic handoff does not fit; never dispatch a partial critical state'
                raw['suffix'] = raw['suffix'][:state_match.start()] + state_text + raw['suffix'][state_match.end():]
                assert STATE.sub('<STATE>', raw['suffix']) == STATE.sub('<STATE>', original['raw']['suffix'])
                assert all(raw[k] == original['raw'][k] for k in ('system', 'shared', 'prompt'))
            text, parts = legacy.compose_input(original['order_name'], **raw)
            if arm != 'repaired':
                assert text == original['composed_text'] and parts == original['parts']
            record = dict(review_id=f'{group}_{arm}', group_id=group, arm=arm,
                case=block['case_id'], direction=block['direction'], repeat=block['repeat'], dataset=block['dataset'],
                prefix_arm=original_arm, raw=raw, order=original['order_name'], composed_sha256=oc._sha(text),
                inherited_references={k: {'path': str(p), 'sha256': sha(p)} for k, p in refs.items()},
                inherited_source_exposure=review['source_exposure'], inherited_queries=review['queries'],
                provided_evidence_ids=review['stages'][-1]['provided_evidence_ids'],
                inherited_initial_draws=1, inherited_second_judgments=1, new_final_judgments=1,
                handoff_manifest=handoff,
                scope='Fixed historical prefix; only final judgment is newly sampled. Current prefix differs from debate prefix.')
            write(ROOT / 'prepared/inputs' / f'{group}_{arm}.json', record)
            summaries.append({'review_id': record['review_id'], 'input_sha256': record['composed_sha256']})
    write(ROOT / 'prepared/jobs.json', dict(seed=SEED, arms=ARMS, blocks=blocks, branches=len(summaries), scope='fresh_final_only'))
    write(ROOT / 'prepared/manifest.json', dict(branches=summaries, count=len(summaries)))
    print(json.dumps({'prepared': len(summaries), 'prefixes': len(blocks)}))

def freeze():
    assert (ROOT / 'verification/preflight.json').exists()
    check = load(ROOT / 'verification/preflight.json')
    assert check['passed'], check
    files = [p for p in ROOT.glob('*.py')] + [ROOT / 'PROTOCOL_KO.md', ROOT / 'lineage.json']
    for name in ('aggregate.py', 'test_aggregate.py', 'manual_grade.py', 'ANNOTATION_SCHEMA.md'):
        path = ROOT / 'parent_grading' / name
        assert path.exists(), name
        files.append(path)
    files += [p for p in (ROOT / 'prepared').rglob('*') if p.is_file()]
    files += [p for p in (ROOT / 'verification').rglob('*') if p.is_file()]
    write(ROOT / 'freeze.json', dict(created_at=datetime.now(timezone.utc).isoformat(),
        files={str(p.relative_to(ROOT)): sha(p) for p in sorted(files)},
        parent_seal_sha256=sha(PARENT / 'parent_grading/completed_result_seal.json'),
        model='gpt-5.6-sol', reasoning_effort='low', workers=4, min_start_interval=3,
        new_calls=432, stage='update_2', state_cap=1600, seed=SEED))
    print(json.dumps({'frozen_files': len(files), 'sha256': sha(ROOT / 'freeze.json')}))

def verify_freeze():
    frozen = load(ROOT / 'freeze.json')
    for path, expected in frozen['files'].items():
        assert sha(ROOT / path) == expected, path
    assert sha(PARENT / 'parent_grading/completed_result_seal.json') == frozen['parent_seal_sha256']

def execute_one(group, arm):
    rid = f'{group}_{arm}'
    folder = ROOT / 'results/reviews' / rid
    result_path = folder / 'review.result.json'
    if result_path.exists():
        return {'review_id': rid, 'preserved': True, 'status': load(result_path)['status']}
    prepared = load(ROOT / 'prepared/inputs' / f'{rid}.json')
    for ref in prepared['inherited_references'].values():
        assert sha(ref['path']) == ref['sha256'], ref['path']
    gate = transport.AdmissionGate(ROOT / 'results/gate', max_live_calls=4, min_interval=3)
    client = transport.CliCompletionClient(out_dir=ROOT / 'results/reviews', review_id=rid,
        schema=load(ROOT / 'prepared/response_schema.json'), order=prepared['order'],
        gate=gate, max_calls=1, timeout=300)
    if not (folder / 'prefix.reference.json').exists():
        write(folder / 'prefix.reference.json', {k: v for k, v in prepared.items() if k != 'raw'})
    # Only the final stage calls complete(). The first two are immutable references.
    for stage in legacy.STAGES:
        client.begin_stage(stage)
    stage_result = folder / 'update_2.result.json'
    if not stage_result.exists():
        try:
            client.complete(**prepared['raw'])
        except transport.CallFailed:
            pass
    record = load(stage_result)
    inp = load(folder / 'update_2.input.json')
    assert inp['raw'] == prepared['raw'] and inp['composed_sha256'] == prepared['composed_sha256']
    try:
        payload = json.loads(record['raw_response'] or '')
    except ValueError:
        payload = {}
    findings = payload.get('findings', []) if isinstance(payload, dict) else []
    if not isinstance(findings, list): findings = []
    result = {k: prepared[k] for k in ('review_id','group_id','case','direction','repeat','dataset','arm','prefix_arm')}
    result.update(status='completed' if record['status'] == 'ok' else 'output_failure',
        payload=payload, findings_model_raw=findings, freeze_sha256=sha(ROOT / 'freeze.json'),
        prepared_sha256=sha(ROOT / 'prepared/inputs' / f'{rid}.json'),
        final_result_sha256=sha(stage_result), final_input_sha256=sha(folder / 'update_2.input.json'),
        physical_attempt_count=record['physical_attempt_count'], retry_count=record['retry_count'],
        new_logical_calls=1, allocated_logical_judgments=3,
        inherited_references=prepared['inherited_references'],
        provided_evidence_ids=prepared['provided_evidence_ids'],
        invalid_citation_indices=[i for i,f in enumerate(findings)
            if any(e.get('evidence_id') not in prepared['provided_evidence_ids'] for e in f.get('evidence', []))])
    write(result_path, result)
    return {'review_id': rid, 'status': result['status'], 'attempts': record['physical_attempt_count']}

def execute_block(block):
    results = []
    for arm in block['arms']:
        if (ROOT / 'results/gate/STOP').exists(): break
        row = execute_one(block['group_id'], arm)
        results.append(row)
        print(json.dumps(row), flush=True)
    return results

def run():
    with (ROOT / 'scheduler.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        verify_freeze()
        jobs = load(ROOT / 'prepared/jobs.json')['blocks']
        gate = transport.AdmissionGate(ROOT / 'results/gate', max_live_calls=4, min_interval=3)
        assert not gate.is_stopped(), 'STOP exists; inspect before resuming'
        # Fixed first job is part of the experiment, not a quality-based pilot.
        first = execute_one(jobs[0]['group_id'], jobs[0]['arms'][0])
        print(json.dumps({'first': first}), flush=True)
        if first['status'] != 'completed':
            gate.stop('fixed_first_final_call_failed_before_fanout', first['review_id'])
            return
        with ProcessPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(execute_block, b): b['group_id'] for b in jobs}
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as exc:
                    gate.stop(f'harness_error:{type(exc).__name__}:{exc}', futures[future])
                    raise
        print(json.dumps(status()), flush=True)

def status():
    rows = [load(p) for p in (ROOT / 'results/reviews').glob('*/review.result.json')]
    return {'reviews': len(rows), 'expected': 432,
            'completed': sum(r['status'] == 'completed' for r in rows),
            'failed': sum(r['status'] != 'completed' for r in rows),
            'physical_attempts_finished': sum(r['physical_attempt_count'] for r in rows),
            'STOP': load(ROOT / 'results/gate/STOP') if (ROOT / 'results/gate/STOP').exists() else None}

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['prepare','freeze','run','status'])
    args = parser.parse_args()
    if args.command == 'status': print(json.dumps(status()))
    else: globals()[args.command]()
