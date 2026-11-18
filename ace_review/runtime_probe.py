"""Fixed local probe templates. Never accepts generated source or shell commands.

Network/writes/forks are OS-restricted; reads are not confidentially isolated.
"""
import argparse
import errno
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

KINDS = ('isolation_selftest', 'timeout_selftest', 'binaryio_predicate', 'anyio_option_path')


def sandbox_profile(scratch):
    return ('(version 1) (allow default) (deny network*) (deny file-write*) '
            f'(allow file-write* (subpath {json.dumps(str(Path(scratch).resolve()))})) '
            '(deny process-fork)')


def verified_snapshot(snapshot):
    if snapshot is None:
        raise ValueError('A verified source snapshot is required')
    root = Path(snapshot).resolve()
    manifest = json.loads((root / 'index_audit.json').read_text())['source_sha256']
    if not manifest:
        raise ValueError('Empty source manifest')
    for name, expected in manifest.items():
        path = root / name
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError('Unsafe source path')
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('Source hash mismatch: ' + name)
    return root, hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()


def run_probe(kind, python, *, snapshot=None, timeout=15):
    if kind not in KINDS or not 0 < timeout <= 60:
        raise ValueError('Unknown probe or invalid timeout')
    root, manifest_hash = (verified_snapshot(snapshot) if kind == 'anyio_option_path' else (None, None))
    # The OS policy must pass actual negative checks before task-source imports.
    if kind not in ('isolation_selftest', 'timeout_selftest'):
        isolation = run_probe('isolation_selftest', python, timeout=timeout)
        if isolation['status'] != 'ok' or not all(isolation['observation'].values()):
            return {'kind': kind, 'status': 'isolation_failed', 'isolation': isolation}
    with tempfile.TemporaryDirectory(prefix='review-probe-') as directory:
        directory = Path(directory).resolve()
        scratch = directory / 'scratch'
        scratch.mkdir()
        command = ['/usr/bin/sandbox-exec', '-p', sandbox_profile(scratch),
                   str(Path(python).absolute()), '-I', '-B', '-X', 'utf8', str(Path(__file__).resolve()),
                   kind, str(scratch), str(directory / 'outside-write')]
        if root:
            command.extend(['--snapshot', str(root)])
        start = time.monotonic()
        record = {'kind': kind, 'python': str(python), 'snapshot': str(root) if root else None,
                  'source_manifest_sha256': manifest_hash, 'timeout_seconds': timeout,
                  'worker_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  'policy': 'deny network, writes outside scratch, forks; reads allowed'}
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=timeout,
                                    cwd=scratch, env={'PATH': '/usr/bin:/bin', 'TMPDIR': str(scratch),
                                                      'LC_ALL': 'en_US.UTF-8', 'PYTHONDONTWRITEBYTECODE': '1'})
        except subprocess.TimeoutExpired:
            return record | {'status': 'timeout', 'duration_seconds': time.monotonic() - start}
        except OSError as exc:
            return record | {'status': 'execution_error', 'error': str(exc)}
        record.update(returncode=result.returncode, stdout=result.stdout, stderr=result.stderr,
                      duration_seconds=time.monotonic() - start)
        if result.returncode:
            return record | {'status': 'execution_error'}
        try:
            payload = json.loads(result.stdout)
            observation = payload['observation']
            worker_lines = payload['worker_lines']
            if not isinstance(observation, dict) or not isinstance(worker_lines, list):
                raise ValueError('Invalid worker envelope')
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return record | {'status': 'invalid_output'}
        return record | {'status': 'ok', 'observation': observation, 'worker_lines': worker_lines}


def blocked(action):
    try:
        action()
    except OSError as exc:
        return exc.errno == errno.EPERM
    return False


def isolation_selftest(scratch, outside):
    with socket.socket() as sock:
        sock.settimeout(1)
        network = blocked(lambda: sock.connect(('127.0.0.1', 9)))
    write = blocked(lambda: outside.write_text('sandbox self-test'))
    child = blocked(lambda: subprocess.run(['/usr/bin/true'], check=True))
    (scratch / 'allowed').write_text('scratch-only')
    return {'network_blocked': network, 'outside_write_blocked': write,
            'child_process_blocked': child, 'scratch_write_allowed': True}


def binaryio_predicate(scratch):
    import typing
    with tempfile.TemporaryFile(mode='w+b', dir=scratch) as stream:
        try:
            predicate = {'result': isinstance(stream, typing.BinaryIO), 'exception': None}
        except TypeError as exc:
            predicate = {'result': None, 'exception': str(exc)}
        return {'python_version': sys.version, 'stream_type': type(stream).__name__,
                'predicate': predicate, 'has_buffer': hasattr(stream, 'buffer'),
                'has_encoding': hasattr(stream, 'encoding'), 'whole_click_path_executed': False}


def anyio_option_path(snapshot):
    import inspect
    import importlib.metadata
    sys.path.insert(0, str(snapshot / 'src'))
    import anyio
    import trio
    from anyio._core._eventloop import get_async_backend
    if not Path(anyio.__file__).resolve().is_relative_to(snapshot):
        raise ValueError('Imported a different AnyIO source tree')
    backend = get_async_backend('trio')
    original_bound = backend.run
    method_path = Path(inspect.getsourcefile(original_bound)).resolve()
    method_path.relative_to(snapshot)  # Refuse an installed, different adapter.
    descriptor = inspect.getattr_static(backend, 'run')
    marker, result_marker = object(), object()
    supplied = {'clock': marker}
    boundary = {}

    def job(*args):
        raise AssertionError('Probe must never execute user job')

    def boundary_spy(cls, func, args, kwargs, options):
        boundary.update(func=func, args=args, kwargs=kwargs, options=options)
        return result_marker

    backend.run = classmethod(boundary_spy)
    try:
        boundary_result = anyio.run(job, backend='trio', backend_options=supplied)
    finally:
        backend.run = descriptor
    if boundary_result is not result_marker or boundary.get('func') is not job:
        raise ValueError('Boundary spy was not invoked as expected')
    downstream = {}
    original_trio = trio.run

    def trio_spy(func, *args, **kwargs):
        downstream.update(func=func, args=args, kwargs=kwargs)
        return result_marker

    trio.run = trio_spy
    try:
        adapter_result = original_bound(boundary['func'], boundary['args'], boundary['kwargs'], boundary['options'])
    finally:
        trio.run = original_trio
    if adapter_result is not result_marker or downstream.get('func') is not job:
        raise ValueError('Downstream spy was not invoked as expected')
    return {'python_version': sys.version, 'trio_version': importlib.metadata.version('trio'),
            'backend_literal': 'trio', 'backend_class': backend.__name__, 'backend_module': backend.__module__,
            'method_file': str(method_path.relative_to(snapshot)),
            'method_file_sha256': hashlib.sha256(method_path.read_bytes()).hexdigest(),
            'boundary_same_mapping': boundary['options'] is supplied,
            'boundary_same_clock': boundary['options'].get('clock') is marker,
            'trio_keyword_names': sorted(downstream['kwargs']),
            'trio_received_clock': downstream['kwargs'].get('clock') is marker,
            'job_executed': False, 'actual_clock_installed': False,
            'scope': 'Fixed nonempty mapping; two separate boundary interceptions; not complete client execution'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('kind', choices=KINDS)
    parser.add_argument('scratch', type=Path)
    parser.add_argument('outside', type=Path)
    parser.add_argument('--snapshot', type=Path)
    args = parser.parse_args()
    visited_lines = set()

    def record_line(frame, event, arg):
        if event == 'line' and frame.f_code.co_filename == __file__:
            visited_lines.add(frame.f_lineno)
        return record_line

    sys.settrace(record_line)
    if args.kind == 'isolation_selftest':
        data = isolation_selftest(args.scratch, args.outside)
    elif args.kind == 'timeout_selftest':
        time.sleep(60)
        data = {}
    elif args.kind == 'binaryio_predicate':
        data = binaryio_predicate(args.scratch)
    else:
        data = anyio_option_path(args.snapshot)
    sys.settrace(None)
    print(json.dumps({'observation': data, 'worker_lines': sorted(visited_lines)}))
