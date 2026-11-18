"""Predeclared paired four-arm, three-repeat experiment; append-only artifacts.

Initial responses are freshly sampled once per direction/repeat and shared only
within that block. Every branch verifies the exact same input before replay.
No historical model output is used. Physical and allocated costs stay separate.
"""
from __future__ import annotations

import argparse
import contextlib
import copy
import hashlib
import json
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed

HERE = Path(__file__).resolve().parent
PARENT = HERE.parent / 'delivered_body_20261029'
ARMS = ('current', 'verify', 'debate', 'consumer')
REPEATS = 3
WORKERS = 4
SEED = 2026102907
import oar_product_adapter as ad
import oar_core as oc
import cli_transport as legacy
import reliable_transport as transport


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_new(path, value)


def validate_strict_schema(schema):
    """Provider-required object constraints, beyond generic JSON Schema validity."""
    def visit(value, path):
        if isinstance(value, dict):
            if value.get('type') == 'object' or 'properties' in value:
                if set(value.get('properties', {})) != set(value.get('required', [])):
                    raise ValueError(f'{path}: strict schema requires every property')
                if value.get('additionalProperties') is not False:
                    raise ValueError(f'{path}: strict schema needs additionalProperties=false')
            for key, child in value.items():
                visit(child, f'{path}/{key}')
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, f'{path}/{index}')
    visit(schema, '$')


def provider_schema_rejection(initial_dir):
    """Inspect only typed CLI errors, never model-authored prose."""
    path = Path(initial_dir) / 'initial.trace.json'
    if not path.exists():
        return False
    trace = read(path)
    for line in trace.get('stdout', '').splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get('type') not in ('error', 'turn.failed'):
            continue
        detail = json.dumps(event, ensure_ascii=False)
        if 'invalid_json_schema' in detail:
            return True
    return False


@contextlib.contextmanager
def case_loader():
    original = ad.case_files
    def paths(case_id, world):
        if case_id.startswith('H'):
            key = f'{case_id}_{world}'
            public = HERE / 'holdout/public'
            return {'case_sha256': public / 'cases' / f'{key}.json',
                    'workspace_manifest_sha256': public / 'workspace_manifests' / f'{key}.json',
                    'seed_sha256': public / 'seeds' / f'{key}.json'}
        return original(case_id, world)
    ad.case_files = paths
    try:
        yield
    finally:
        ad.case_files = original


def blocks():
    result = []
    for repeat in range(1, REPEATS + 1):
        for dataset, prefix in [('existing', 'N'), ('unused', 'H')]:
            for case in range(1, 13):
                for direction in ('a', 'b'):
                    key = f'{prefix}{case:02d}_{direction}'
                    result.append(dict(group_id=f'{key}_r{repeat}', key=key,
                        case_id=key.split('_')[0], direction=direction,
                        repeat=repeat, dataset=dataset))
    rng = random.Random(SEED)
    rng.shuffle(result)
    for order, row in enumerate(result):
        arms = list(ARMS)
        rng.shuffle(arms)
        row.update(order=order, arms=arms)
    return result


class SharedInitialClient:
    """A new block's one producer call plus two private branch calls.

    Initial inputs/results are copied as references with zero physical cost.
    Missing/incomplete initial claims never cause automatic resampling.
    """
    def __init__(self, branch, initial):
        self.branch, self.initial = branch, initial
        self.dir = branch.dir

    def __getattr__(self, name):
        return getattr(self.branch, name)

    def begin_stage(self, stage):
        self.branch.begin_stage(stage)

    def complete(self, **parts):
        if self.branch._stage != 'initial':
            return self.branch.complete(**parts)
        if self.branch._stage_calls:
            raise legacy.RetryRefused('initial response cannot be replayed twice in one branch')
        self.branch._stage_calls += 1
        text, normalized_parts = legacy.compose_input(self.branch.order, parts['system'],
            parts.get('shared', ''), parts['prompt'], parts.get('suffix', ''))
        canonical = self.initial._paths('initial')
        if not canonical['claim.json'].exists():
            self.initial.begin_stage('initial')
            try:
                self.initial.complete(**parts)
            except legacy.CallFailed:
                pass  # All branches inherit this same failed initial draw.
        if not canonical['result.json'].exists():
            raise legacy.ClaimedIncomplete('shared initial claimed without a result; never resampled')
        recorded_input = read(canonical['input.json'])
        expected_raw = {key: parts.get(key, '') for key in ('system', 'shared', 'prompt', 'suffix')}
        if (recorded_input['composed_sha256'] != oc._sha(text)
                or recorded_input.get('raw') != expected_raw
                or recorded_input.get('parts') != normalized_parts):
            raise ad.AdapterError('shared initial input differs between branches')
        record = read(canonical['result.json'])
        output = copy.deepcopy(record)
        output.update(review_id=self.branch.review_id,
            shared_initial_reference=str(canonical['result.json']),
            shared_initial_result_sha256=sha(canonical['result.json']),
            allocated_shared_usage=record.get('actual_usage'),
            allocated_shared_physical_attempts=record.get('physical_attempt_count', 0),
            physical_attempt_count=0, physical_spawn_count=0, retry_count=0,
            actual_usage={'per_turn': [], 'total': {}},
            reference_tokens_o200k_input_all_attempts=0,
            usage_semantics='Physical cost belongs to shared producer; allocated_shared_usage is charged once per arm for fair per-arm comparison.')
        output['attempts'] = [{**attempt,
            'path': str(self.initial.dir / attempt['path']),
            'shared_physical_attempt': True} for attempt in record.get('attempts', [])]
        paths = self.branch._paths('initial')
        write(paths['claim.json'], {**read(canonical['claim.json']),
            'review_id': self.branch.review_id, 'shared_initial_reference': str(canonical['claim.json']),
            'physical_spawn_count': 0})
        for name in ('input.json', 'trace.json', 'raw.txt'):
            if canonical[name].exists():
                write(paths[name], canonical[name].read_text())
        write(paths['result.json'], output)
        if output['status'] != 'ok':
            raise legacy.CallFailed(output)
        return output['raw_response']


def prepare_direction(key):
    target = HERE / 'prepared/inputs' / f'{key}.json'
    if target.exists():
        return {'key': key, 'path': str(target), 'sha256': sha(target), 'preserved': True}
    if key.startswith('N'):
        source = PARENT / 'prepared/inputs' / f'{key}.json'
        write(target, source.read_text())
        assert sha(target) == sha(source)
        return {'key': key, 'path': str(target), 'sha256': sha(target), 'mode': 'parent_packet_byte_replay', 'fresh_model_output': False}
    import frontier_builder as builder
    with case_loader():
        product = ad.load_product()
        packet = builder.prepare_packet(key, product=product)
        data = ad.load_case(*key.split('_'))
        workspace = ad.verify_workspace(data['workspace_root'], data['workspace_sources'])
        value = {'key': key, 'authoritative': {**data['authoritative'],
                    'workspace_tree_sha256': data['workspace_tree_sha256']},
                 'workspace': workspace, 'body_packet': packet,
                 'packet_builder_sha256': sha(HERE / 'frontier_builder.py'), 'prepared_at': time.time()}
        write(target, value)
    return {'key': key, 'path': str(target), 'sha256': sha(target), 'mode': 'fresh_native_preparation'}


def prepare():
    jobs = blocks()
    keys = sorted({row['key'] for row in jobs})
    results = []
    with ProcessPoolExecutor(max_workers=WORKERS) as pool:
        pending = {pool.submit(prepare_direction, key): key for key in keys}
        for future in as_completed(pending):
            result = future.result()
            results.append(result)
            print(json.dumps(result), flush=True)
    write(HERE / 'prepared/jobs.json', {'seed': SEED, 'repeats': REPEATS, 'arms': ARMS,
        'blocks': jobs, 'review_branches': len(jobs) * len(ARMS), 'initial_producers': len(jobs)})
    write(HERE / 'prepared/manifest.json', {'results': sorted(results, key=lambda r: r['key']),
        'directions': len(keys), 'all_ok': len(results) == 48})


def freeze_files():
    paths = list(HERE.glob('*.py')) + [HERE / 'PROTOCOL_KO.md']
    if (HERE / 'amendment.json').exists():
        paths.append(HERE / 'amendment.json')
    for folder in ('prepared', 'holdout/public', 'holdout/manifests', 'holdout/private'):
        paths += [p for p in (HERE / folder).rglob('*') if p.is_file()]
    # Implementation dependencies are measured separately from historical data.
    paths += [p for p in (HERE / 'product_snapshot_20261026').rglob('*.py')]
    return {str(p.relative_to(HERE)): sha(p) for p in sorted(set(paths))}


def verification_code_hashes():
    paths = list(HERE.glob('*.py')) + list((HERE / 'product_snapshot_20261026').rglob('*.py'))
    import scheduler_support
    return {'local': {str(p.relative_to(HERE)): sha(p) for p in sorted(paths)},
            'ace_dependencies': scheduler_support.ace_dependency_files()}


def freeze():
    manifest = read(HERE / 'prepared/manifest.json')
    if not manifest['all_ok'] or manifest['directions'] != 48:
        raise RuntimeError('all 48 directions must be prepared')
    checks = read(HERE / 'verification/final/checks.json')
    if checks.get('exit_code') != 0:
        raise RuntimeError('synthetic integration tests must pass before freeze')
    if checks.get('code_hashes') != verification_code_hashes():
        raise RuntimeError('code changed since the final verification run')
    integration = read(HERE / 'verification/final/integration.json')
    if not integration.get('passed') or integration.get('initial_directions') != 48:
        raise RuntimeError('all four initial inputs and delivery checks must pass on 48 directions')
    private_seal = HERE / 'holdout/manifests/private_grading_seal.json'
    if not private_seal.is_file():
        raise RuntimeError('independent holdout grading boundaries must be sealed before model calls')
    if list((HERE / 'results').glob('**/*.claim.json')):
        raise RuntimeError('cannot create initial freeze after model claims')
    import causal_adapter
    validate_strict_schema(causal_adapter.response_schema())
    import scheduler_support
    write(HERE / 'freeze.json', {'created_at': time.time(), 'files': freeze_files(),
        'ace_dependencies': scheduler_support.ace_dependency_files(),
        'model': legacy.MODEL, 'reasoning_effort': 'low', 'workers': WORKERS,
        'min_start_interval': 3, 'repeats': REPEATS, 'seed': SEED,
        'tests_sha256': sha(HERE / 'verification/final/checks.json'),
        'integration_sha256': sha(HERE / 'verification/final/integration.json'),
        'private_grading_seal_sha256': sha(private_seal),
        'probe_runtime': {'implementation': 'cpython', 'version': sys.version.split()[0],
                          'python_executable': str(HERE / '.venv/bin/python'),
                          'scope': 'declared_experimental_runtime_not_repository_target'}})


def verify_freeze():
    frozen = read(HERE / 'freeze.json')
    bad = [name for name, digest in frozen['files'].items()
           if not (HERE / name).is_file() or sha(HERE / name) != digest]
    import scheduler_support
    if scheduler_support.ace_dependency_files() != frozen['ace_dependencies']:
        bad.append('ace_dependencies')
    if bad:
        raise RuntimeError(f'frozen inputs or policy changed: {bad[:10]}')
    return frozen


def run_block(job):
    import causal_adapter as adapter
    import frontier_builder as builder
    frozen = verify_freeze()
    gate = transport.AdmissionGate(HERE / 'results/gate', max_live_calls=WORKERS, min_interval=3)
    if gate.is_stopped():
        return {'group_id': job['group_id'], 'status': 'global_stop'}
    prepared = read(HERE / 'prepared/inputs' / f"{job['key']}.json")
    schema = adapter.response_schema()
    validate_strict_schema(schema)
    initial = transport.CliCompletionClient(out_dir=HERE / 'results/initial',
        review_id=job['group_id'], schema=schema, gate=gate, timeout=300)
    statuses = []
    with case_loader():
        product = ad.load_product()
        for arm in job['arms']:
            review_id = f"{job['group_id']}_{arm}"
            folder = HERE / 'results/reviews' / review_id
            if (folder / 'review.result.json').exists():
                statuses.append({'review_id': review_id, 'preserved': True})
                continue
            if list(folder.glob('*.claim.json')):
                statuses.append({'review_id': review_id, 'status': 'incomplete_preserved'})
                continue
            if gate.is_stopped():
                statuses.append({'review_id': review_id, 'status': 'global_stop'})
                continue
            branch = transport.CliCompletionClient(out_dir=HERE / 'results/reviews',
                review_id=review_id, schema=schema, gate=gate, timeout=300)
            inner = SharedInitialClient(branch, initial)
            started = time.time()
            try:
                inputs = builder.build_inputs(job['key'], 'proactive',
                    prepared_packet=prepared['body_packet'], product=product)
                result = adapter.run_review(job['key'], arm, inner,
                    prepared_packet=prepared['body_packet'], prepared_inputs=inputs,
                    expected_inputs=prepared['authoritative'], product=product,
                    probe_runtime=frozen['probe_runtime'])
            except BaseException as exc:
                result = {'status': 'harness_failure', 'error': f'{type(exc).__name__}: {exc}',
                          'traceback': traceback.format_exc(), 'findings_model_raw': []}
            result.update(review_id=review_id, experimental_arm=arm, experiment_block=job,
                shared_initial=str(initial.dir), harness_duration_seconds=time.time() - started,
                freeze_sha256=sha(HERE / 'freeze.json'))
            write(folder / 'review.result.json', result)
            if provider_schema_rejection(initial.dir):
                gate.stop('provider strict response schema rejected before inference', label=job['group_id'])
            statuses.append({'review_id': review_id, 'status': result['status']})
            print(json.dumps(statuses[-1]), flush=True)
    return {'group_id': job['group_id'], 'branches': statuses}


def run():
    verify_freeze()
    jobs = read(HERE / 'prepared/jobs.json')['blocks']
    # One scheduler owns job dispatch. A previous process must be gone before resume.
    import fcntl
    lock = HERE / 'scheduler.lock'
    with lock.open('a+') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Predeclared first randomized block is retained in the experiment. Its
        # API compatibility is checked before expanding concurrency; no case is
        # selected or replaced on model performance.
        first = run_block(jobs[0])
        print(json.dumps({'bootstrap_block': first}), flush=True)
        producer_path = HERE / 'results/initial' / jobs[0]['group_id'] / 'initial.result.json'
        if not producer_path.exists() or read(producer_path).get('status') != 'ok':
            print(json.dumps({'fanout': False, 'reason': 'first fixed producer did not return a valid response'}), flush=True)
            return
        with ProcessPoolExecutor(max_workers=WORKERS) as pool:
            futures = {pool.submit(run_block, job): job for job in jobs[1:]}
            for future in as_completed(futures):
                try:
                    value = future.result()
                except BaseException as exc:
                    value = {'group_id': futures[future]['group_id'], 'worker_error': str(exc)}
                with (HERE / 'progress.jsonl').open('a') as progress:
                    progress.write(json.dumps({'time': time.time(), **value}) + '\n')
                print(json.dumps(value), flush=True)


def status():
    results = list((HERE / 'results/reviews').glob('*/review.result.json'))
    counts = {}
    for path in results:
        value = read(path)
        key = value['status']
        counts[key] = counts.get(key, 0) + 1
    producer = list((HERE / 'results/initial').glob('*/initial.result.json'))
    attempts = list((HERE / 'results').glob('**/attempts/*/attempt_*/result.json'))
    return {'branches_finished': len(results), 'branches_expected': 576,
            'statuses': counts, 'initial_draws': len(producer), 'physical_attempts': len(attempts),
            'stop': read(HERE / 'results/gate/STOP') if (HERE / 'results/gate/STOP').exists() else None}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('prepare', 'freeze', 'run', 'status'))
    args = parser.parse_args()
    if args.action == 'status':
        print(json.dumps(status(), ensure_ascii=False, indent=2))
    else:
        globals()[args.action]()
