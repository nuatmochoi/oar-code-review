"""Fixed Click production-path observation, interrupted before consumer write.

This is a development probe, not a general-purpose safe repository executor.
"""
import ast
import builtins
import contextlib
import hashlib
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ace_review.runtime_probe import run_probe, sandbox_profile, verified_snapshot  # noqa: E402


def write_site(fn):
    lines, start = inspect.getsourcelines(fn)
    calls = [n for n in ast.walk(ast.parse(textwrap.dedent(''.join(lines))))
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == 'write' and isinstance(n.func.value, ast.Name)
             and len(n.args) == 1 and isinstance(n.args[0], ast.Name)]
    if len(calls) != 1:
        raise ValueError('Expected one unambiguous consumer write site')
    call = calls[0]
    return start + call.lineno - 1, call.func.value.id, call.args[0].id


def observe(snapshot):
    sys.path.insert(0, str(snapshot / 'src'))
    import click
    import click._termui_impl as impl
    for module in (click, impl, sys.modules[click.echo_via_pager.__module__]):
        Path(module.__file__).resolve().relative_to(snapshot)
    line, receiver, argument = write_site(click.echo_via_pager)
    original_provider = impl._tempfilepager
    original_capability = getattr(impl, '_has_binary_buffer', None)
    missing = object()
    original_isinstance = getattr(impl, 'isinstance', missing)
    original_win, original_isatty = impl.WIN, impl.isatty
    original_pager = os.environ.get('PAGER')
    previous_trace = sys.gettrace()
    state = {'python_version': sys.version, 'production_provider_calls': 0,
             'capability_checks': [], 'binaryio_checks': [],
             'stopped_before_write': False, 'consumer_write_executed': False,
             'pager_subprocess_executed': False,
             'scope': 'Actual Click echo_via_pager; forced WIN=True and isatty=True; '
                      'PAGER=/bin/cat; color=False; macOS, not native Windows; '
                      'interrupted before first consumer write, no write outcome observed.'}
    objects = {}

    @contextlib.contextmanager
    def provider(*args, **kwargs):
        state['production_provider_calls'] += 1
        with original_provider(*args, **kwargs) as yielded:
            stream = yielded[0]
            objects['producer'] = stream
            state.update(producer_type=type(stream).__name__,
                         producer_file_type=type(stream.file).__name__,
                         producer_has_buffer=hasattr(stream, 'buffer'))
            yield yielded

    def capability(stream):
        result = original_capability(stream)
        state['capability_checks'].append({'same_as_producer': stream is objects['producer'], 'result': result})
        return result

    def nominal(value, target):
        result = builtins.isinstance(value, target)
        if target is impl.t.BinaryIO:
            state['binaryio_checks'].append({'same_as_producer': value is objects['producer'], 'result': result})
        return result

    class StopBeforeWrite(Exception):
        pass

    def trace(frame, event, arg):
        if previous_trace:
            previous_trace(frame, event, arg)
        if frame.f_code is click.echo_via_pager.__code__ and event == 'line' and frame.f_lineno == line:
            consumer, value = frame.f_locals[receiver], frame.f_locals[argument]
            producer = objects['producer']
            state.update(consumer_type=type(consumer).__name__, consumer_argument_type=type(value).__name__,
                         consumer_is_producer=consumer is producer,
                         consumer_buffer_is_producer_buffer=(hasattr(consumer, 'buffer') and
                             hasattr(producer, 'buffer') and consumer.buffer is producer.buffer),
                         stopped_before_write=True)
            raise StopBeforeWrite()
        return trace

    impl._tempfilepager = provider
    if original_capability:
        impl._has_binary_buffer = capability
    impl.isinstance = nominal
    impl.WIN, impl.isatty = True, lambda _: True
    os.environ['PAGER'] = '/bin/cat'
    try:
        sys.settrace(trace)
        try:
            click.echo_via_pager('review-probe', color=False)
        except StopBeforeWrite:
            pass
        else:
            raise ValueError('Consumer interception was not reached')
    finally:
        sys.settrace(previous_trace)
        impl._tempfilepager = original_provider
        if original_capability:
            impl._has_binary_buffer = original_capability
        if original_isinstance is missing:
            del impl.isinstance
        else:
            impl.isinstance = original_isinstance
        impl.WIN, impl.isatty = original_win, original_isatty
        if original_pager is None:
            os.environ.pop('PAGER', None)
        else:
            os.environ['PAGER'] = original_pager
        if 'producer' in objects:
            objects['producer'].close()
    return state


def run_lineage(python, snapshot, *, timeout=15):
    if not 0 < timeout <= 60:
        raise ValueError('Invalid timeout')
    root, manifest_hash = verified_snapshot(snapshot)
    isolation = run_probe('isolation_selftest', python, timeout=timeout)
    if isolation['status'] != 'ok' or not all(isolation['observation'].values()):
        return {'status': 'isolation_failed', 'isolation': isolation}
    record = {'source_manifest_sha256': manifest_hash, 'snapshot': str(root),
              'worker_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'isolation': isolation, 'timeout_seconds': timeout}
    with tempfile.TemporaryDirectory(prefix='review-lineage-') as directory:
        scratch = Path(directory).resolve()
        command = ['/usr/bin/sandbox-exec', '-p', sandbox_profile(scratch), str(Path(python).absolute()),
                   '-I', '-B', '-X', 'utf8', str(Path(__file__).resolve()), str(root)]
        start = time.monotonic()
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, cwd=scratch,
                                    env={'PATH': '/usr/bin:/bin', 'TMPDIR': str(scratch),
                                         'LC_ALL': 'en_US.UTF-8', 'PYTHONDONTWRITEBYTECODE': '1'})
        except subprocess.TimeoutExpired:
            return record | {'status': 'timeout'}
        except OSError as exc:
            return record | {'status': 'execution_error', 'error': str(exc)}
        record.update(stdout=result.stdout, stderr=result.stderr, returncode=result.returncode,
                      duration_seconds=time.monotonic() - start)
        if result.returncode:
            return record | {'status': 'execution_error'}
        try:
            data = json.loads(result.stdout)
            if not isinstance(data['observation'], dict) or not isinstance(data['worker_lines'], list):
                raise ValueError('Invalid worker envelope')
        except (ValueError, KeyError, TypeError):
            return record | {'status': 'invalid_output'}
        return record | data | {'status': 'ok'}


if __name__ == '__main__':
    visited = set()

    def record_line(frame, event, arg):
        if event == 'line' and frame.f_code.co_filename == __file__:
            visited.add(frame.f_lineno)
        return record_line

    sys.settrace(record_line)
    observation = observe(Path(sys.argv[1]).resolve())
    sys.settrace(None)
    print(json.dumps({'observation': observation, 'worker_lines': sorted(visited)}))
