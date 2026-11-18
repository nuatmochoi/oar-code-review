"""Common CLI transport: isolated process groups, live admission, one narrow retry.

The product still receives at most three logical completions. Each stage has at
most two physical attempts, and only a response-less transient transport failure
can use the second. Existing claims are never resumed. Attempt artifacts are
immutable; canonical stage artifacts describe the last attempt and aggregate
usage across *all* attempts. No answer or schema repair is performed here.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import signal
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

import jsonschema
import cli_transport as legacy

# Preserve the exception identities consumed by the existing adapter.
TransportError = legacy.TransportError
CallCapExceeded = legacy.CallCapExceeded
RetryRefused = legacy.RetryRefused
StageError = legacy.StageError
OutputExists = legacy.OutputExists
ClaimedIncomplete = legacy.ClaimedIncomplete
GlobalStop = legacy.GlobalStop
CallFailed = legacy.CallFailed
MODEL, STAGES = legacy.MODEL, legacy.STAGES
MIN_START_INTERVAL = legacy.MIN_START_INTERVAL
MAX_CALLS, MAX_ATTEMPTS_PER_STAGE = 3, 1
MAX_PHYSICAL_ATTEMPTS = MAX_CALLS * MAX_ATTEMPTS_PER_STAGE
CLI_TIMEOUT = 300.0
write_new, min_gap = legacy.write_new, legacy.min_gap
TRANSIENT = re.compile(
    r'connection (?:reset|closed|aborted)|stream (?:disconnected|closed)|'
    r'unexpected (?:eof|end of stream)|timed? ?out|temporar(?:y|ily) (?:failure|unavailable)|'
    r'service unavailable|network (?:error|unreachable)|\bHTTP[ /:]*(?:500|502|503|504)\b|'
    r'\b(?:ECONNRESET|ECONNABORTED|ETIMEDOUT|EHOSTUNREACH|ENETUNREACH|EAGAIN)\b|'
    r'\[Errno (?:11|35|54|60|101|104|110|113)\]', re.I)


class AdmissionGate(legacy.AdmissionGate):
    """Shared physical-start spacing plus live-process flock slots.

    A slot stays locked through group termination, pipe drain and parent wait.
    The slot FD is also inherited by the CLI, so a killed worker cannot release
    capacity while that CLI remains alive. All workers/arms must share this dir.
    """
    def __init__(self, directory, *, max_live_calls=2, poll_interval=0.05, **kwargs):
        if not isinstance(max_live_calls, int) or max_live_calls < 1:
            raise ValueError('max_live_calls must be a positive integer')
        super().__init__(directory, **kwargs)
        self.max_live_calls, self.poll_interval = max_live_calls, poll_interval
        self.live_dir = self.dir / 'live_slots'
        self.live_dir.mkdir(exist_ok=True)
        config = {'max_live_calls': max_live_calls, 'min_interval': self.min_interval}
        with self._locked():
            path = self.dir / 'reliable_gate.json'
            if path.exists():
                if json.loads(path.read_text()) != config:
                    raise ValueError('shared admission gate configuration mismatch')
            else:
                write_new(path, config)

    def start(self, *, label, spawn=None, claim_fn=None):
        waited = 0.0
        while True:
            with self._locked():
                if self.is_stopped():
                    raise GlobalStop('global stop active before physical start')
                starts = self.starts()
                last = starts[-1]['spawned_at'] if starts else None
                delay = 0 if last is None else last + self.min_interval - self.clock()
                if delay <= 0:
                    if claim_fn is not None:
                        claim_fn()
                    called, proc, error = self.clock(), None, None
                    try:
                        if spawn is not None:
                            try:
                                proc = spawn()
                            except Exception as exc:
                                error = f'{type(exc).__name__}: {exc}'
                        entry = {'label': label, 'started_at': called, 'spawn_called_at': called,
                                 'spawned_at': self.clock(), 'child_pid': getattr(proc, 'pid', None),
                                 'pid': os.getpid(), 'waited': waited}
                        if error:
                            entry['spawn_error'] = error
                        with self.starts_path.open('a', encoding='utf-8') as out:
                            out.write(json.dumps(entry) + '\n')
                    except BaseException:
                        reap_group(proc)
                        raise
                    return entry, proc
            self.sleep(delay)
            waited += delay

    @contextmanager
    def live_slot(self):
        fd = None
        while fd is None:
            if self.is_stopped():
                raise GlobalStop('global stop active while waiting for a live slot')
            for index in range(self.max_live_calls):
                candidate = os.open(self.live_dir / f'{index}.lock', os.O_CREAT | os.O_RDWR, 0o600)
                try:
                    fcntl.flock(candidate, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    os.close(candidate)
                    continue
                fd = candidate
                break
            if fd is None:
                self.sleep(self.poll_interval)
        try:
            yield {'index': index, 'fd': fd}
        finally:
            # close, not explicit LOCK_UN: the inherited child must retain the
            # lock if process collection was interrupted by worker termination.
            os.close(fd)


def _signal_group(proc, sig, audit):
    try:
        os.killpg(proc.pid, sig)
        audit['signals'].append(signal.Signals(sig).name)
    except ProcessLookupError:
        pass
    except OSError as exc:
        audit['errors'].append(f'killpg:{type(exc).__name__}:{exc}')


def _group_exists(pid):
    try:
        os.killpg(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # macOS can report EPERM for the brief exiting/zombie group window.
        # Keep treating the group as present until poll/wait reaps the child.
        return True


def reap_group(proc, *, grace=0.25, drain_timeout=2.0):
    """TERM then KILL the whole owned session group, drain pipes, wait parent.

    Grandchildren are terminated as group members; POSIX permits waiting only
    for our direct child. Orphan zombie reaping belongs to the system reaper.
    The CLI must not daemonize into a different session (not used by command_for).
    """
    audit = {'process_group': getattr(proc, 'pid', None), 'signals': [], 'errors': [],
             'direct_child_reaped': proc is None, 'drained': proc is None}
    if proc is None:
        return '', '', audit
    _signal_group(proc, signal.SIGTERM, audit)
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline and _group_exists(proc.pid):
        proc.poll()  # reap the direct child even while descendants hold pipes
        time.sleep(min(0.01, max(0, deadline - time.monotonic())))
    _signal_group(proc, signal.SIGKILL, audit)
    stdout = stderr = ''
    try:
        stdout, stderr = proc.communicate(timeout=drain_timeout)
        audit['drained'] = True
    except subprocess.TimeoutExpired as exc:
        stdout, stderr = legacy._text(exc.stdout), legacy._text(exc.stderr)
        audit['errors'].append('drain_timeout_after_group_kill')
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            if stream is not None:
                stream.close()
    finally:
        try:
            proc.wait(timeout=drain_timeout)
            audit['direct_child_reaped'] = True
        except subprocess.TimeoutExpired:
            audit['errors'].append('wait_timeout_after_group_kill')
    return legacy._text(stdout), legacy._text(stderr), audit


def _events(stdout):
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except (ValueError, TypeError):
            continue
        if isinstance(event, dict):
            yield event


def retry_reason(record, trace):
    """Conservative allowlist; response contents never drive a retry."""
    if record['raw_response'] is not None:
        return None
    if record['resource_failure'] or record['actual_tool_calls']:
        return None
    if record.get('process_cleanup', {}).get('errors'):
        return None
    events = list(_events(trace['stdout']))
    if any(e.get('type') == 'turn.completed' or
           (isinstance(e.get('item'), dict) and e['item'].get('type') == 'agent_message')
           for e in events):
        return None  # already produced an answer or completed model decision
    if 'timeout' in record['failure_kinds']:
        return 'response_less_timeout'
    messages = []
    for e in events:
        if e.get('type') in legacy.ERROR_EVENT_TYPES:
            error = e.get('error')
            messages.append(str(e.get('message') or
                                (error.get('message') if isinstance(error, dict) else error) or ''))
    messages.extend(line for line in trace['stderr'].splitlines()
                    if not legacy.STDERR_BENIGN.search(line))
    if any(TRANSIENT.search(message) for message in messages):
        return 'response_less_explicit_transient_error'
    return None


class CliCompletionClient(legacy.CliCompletionClient):
    def __init__(self, *, gate=None, timeout=CLI_TIMEOUT, cleanup_grace=0.25,
                 drain_timeout=2.0, **kwargs):
        if timeout <= 0 or timeout > CLI_TIMEOUT:
            raise ValueError('timeout must be in (0, 300]')
        super().__init__(gate=gate, timeout=timeout, **kwargs)
        if not 1 <= self.max_calls <= MAX_CALLS:
            raise ValueError('max_calls must be between 1 and 3')
        # Even independent callers share a default gate under their output root.
        self.gate = gate or AdmissionGate(Path(kwargs['out_dir']) / '_reliable_gate')
        if not isinstance(self.gate, AdmissionGate):
            raise TypeError('reliable transport requires reliable AdmissionGate')
        self.cleanup_grace, self.drain_timeout = cleanup_grace, drain_timeout

    def complete(self, *, system, prompt, suffix='', shared=''):
        stage = self._stage
        if stage is None:
            self._refuse(StageError('complete() outside begin_stage()'), {})
        if self._stage_calls:
            self._refuse(RetryRefused(f'second complete() in {stage} is refused'), {'stage': stage})
        self._stage_calls += 1
        paths = self._paths(stage)
        if paths['claim.json'].exists():
            exc = OutputExists if paths['result.json'].exists() else ClaimedIncomplete
            self._refuse(exc(f'{stage} already claimed; never automatically re-issued'), {'stage': stage})
        if len(self.claims()) >= self.max_calls:
            self._refuse(CallCapExceeded('logical stage cap reached'), {'stage': stage})
        if not prompt.strip():
            self._refuse(TransportError('empty prompt'), {'stage': stage})
        if self.gate.is_stopped():
            self.gate.record_unattempted(f'{self.review_id}:{stage}', 'global stop')
            self._refuse(GlobalStop('global stop active'), {'stage': stage})
        text, parts = legacy.compose_input(self.order, system, shared, prompt, suffix)
        claim = {'review_id': self.review_id, 'stage': stage, 'model': self.model,
                 'order': self.order, 'input_sha256': legacy.oar_core._sha(text),
                 'claimed_at': time.time(), 'max_physical_attempts': MAX_ATTEMPTS_PER_STAGE}
        input_record = {'stage': stage, 'order_name': self.order, 'order': list(legacy.ORDERS[self.order]),
                        'parts': parts, 'raw': {'system': system, 'shared': shared, 'prompt': prompt, 'suffix': suffix},
                        'composed_text': text, 'composed_sha256': legacy.oar_core._sha(text),
                        'reference_tokens_o200k': legacy.oar_core.count_reference_tokens(text),
                        'note': 'system rides inside stdin because command_for fixes developer_instructions'}
        try:
            write_new(paths['claim.json'], claim)
            write_new(paths['input.json'], input_record)
        except FileExistsError:
            self._refuse(ClaimedIncomplete(f'{stage} concurrently claimed; not re-issued'), {'stage': stage})
        attempts = []
        for number in range(1, MAX_ATTEMPTS_PER_STAGE + 1):
            folder = self.dir / 'attempts' / stage / f'attempt_{number}'
            folder.mkdir(parents=True, exist_ok=False)
            with tempfile.TemporaryDirectory(prefix='reliable-cli-') as temporary:
                prepared = self._prepare(text, Path(temporary))
                proc = None
                try:
                    with self.gate.live_slot() as slot:
                        def claim_attempt():
                            write_new(folder / 'claim.json', {**claim, 'attempt': number,
                                                            'claimed_at': time.time(), 'live_slot': slot['index']})
                            write_new(folder / 'input.json', input_record)

                        def spawn():
                            return self.spawner(prepared['command'], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                                stderr=subprocess.PIPE, text=True, env=prepared['env'],
                                                start_new_session=True, pass_fds=(slot['fd'],))

                        start, proc = self.gate.start(label=f'{self.review_id}:{stage}:attempt_{number}',
                                                     spawn=spawn, claim_fn=claim_attempt)
                        try:
                            record, trace = self._collect_attempt(stage, number, text, start, proc,
                                                                  prepared, Path(temporary), slot['index'])
                            record['retry_eligible_reason'] = retry_reason(record, trace)
                            write_new(folder / 'trace.json', trace)
                            if record['raw_response'] is not None:
                                write_new(folder / 'raw.txt', record['raw_response'])
                            write_new(folder / 'result.json', record)
                        except BaseException:
                            reap_group(proc, grace=self.cleanup_grace, drain_timeout=self.drain_timeout)
                            raise
                except GlobalStop as exc:
                    self.gate.record_unattempted(f'{self.review_id}:{stage}:attempt_{number}', str(exc))
                    write_new(folder / 'unattempted.json', {'reason': str(exc), 'time': time.time()})
                    if not attempts:
                        raise
                    break
            attempts.append(record)
            if record['status'] == 'ok' or not record['retry_eligible_reason']:
                break
        final = dict(attempts[-1])
        usages = [u for r in attempts for u in r['actual_usage']['per_turn']]
        final.update(actual_usage={'per_turn': usages, 'total': legacy._sum_usage(usages)},
                     actual_tool_calls=sum(r['actual_tool_calls'] for r in attempts),
                     logical_stage_count=1, successful_model_decisions=int(final['status'] == 'ok'),
                     physical_attempt_count=len(attempts),
                     physical_spawn_count=sum(r['timing']['child_pid'] is not None for r in attempts),
                     attempts_without_reported_usage=sum(not r['actual_usage']['per_turn'] for r in attempts),
                     reference_tokens_o200k_input_all_attempts=sum(r['reference_tokens_o200k_input'] for r in attempts),
                     retry_count=max(0, len(attempts)-1), max_physical_attempts_per_review=MAX_PHYSICAL_ATTEMPTS,
                     attempts=[{'attempt': r['attempt'], 'path': f"attempts/{stage}/attempt_{r['attempt']}/result.json",
                                'status': r['status'], 'failure_kinds': r['failure_kinds'],
                                'retry_eligible_reason': r['retry_eligible_reason']} for r in attempts],
                     usage_semantics='Sum of reported usage for every physical attempt; absent CLI usage is unknown, not measured zero.')
        final['timing'] = {**final['timing'], 'all_attempts_duration_seconds':
                           sum(r['timing']['duration_seconds'] for r in attempts)}
        last = self.dir / 'attempts' / stage / f"attempt_{final['attempt']}"
        write_new(paths['trace.json'], (last / 'trace.json').read_text())
        if final['raw_response'] is not None:
            write_new(paths['raw.txt'], final['raw_response'])
        write_new(paths['result.json'], final)
        if final['status'] != 'ok':
            raise CallFailed(final)
        return final['raw_response']

    def _collect_attempt(self, stage, number, text, start, proc, prepared, temp, slot):
        began = self.clock()
        failure, stdout, stderr, cleanup = [], '', '', {}
        if proc is None:
            failure.append('os_error')
            stderr = start.get('spawn_error', '')
            returncode = None
        else:
            try:
                stdout, stderr = proc.communicate(input=text, timeout=self.timeout)
            except subprocess.TimeoutExpired as exc:
                failure.append('timeout')
                stdout, stderr, cleanup = reap_group(proc, grace=self.cleanup_grace,
                                                     drain_timeout=self.drain_timeout)
                if not stdout:
                    stdout = legacy._text(exc.stdout)
                if not stderr:
                    stderr = legacy._text(exc.stderr)
            else:
                # A successful direct CLI must not leave background group members.
                if _group_exists(proc.pid):
                    _, _, cleanup = reap_group(proc, grace=self.cleanup_grace,
                                               drain_timeout=self.drain_timeout)
                else:
                    cleanup = {'process_group': proc.pid, 'signals': [], 'errors': [],
                               'direct_child_reaped': proc.returncode is not None, 'drained': True}
            returncode = proc.returncode
            if cleanup.get('errors'):
                failure.append('process_cleanup_failure')
        stdout, stderr = legacy._text(stdout), legacy._text(stderr)
        if returncode not in (None, 0) and 'timeout' not in failure:
            failure.append('nonzero_exit')
        response = temp / 'response.json'
        raw = response.read_text(encoding='utf-8') if response.exists() else None
        audit = legacy._trace_audit(stdout)
        extra = [m for m in legacy._event_markers(stdout) if any(w in m for w in legacy.EXTRA_TOOL_MARKERS)]
        if audit['tool_call_count'] or extra:
            failure.append('tool_calls_observed')
        if raw is None:
            failure.append('no_response')
        else:
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                failure.append('invalid_json')
            else:
                try:
                    jsonschema.validate(parsed, self.schema)
                except jsonschema.ValidationError as exc:
                    failure += ['schema_invalid', f'schema_error: {exc.message[:200]}']
                if self.response_validator is not None and not any(f.startswith('schema') for f in failure):
                    failure += [f'validator: {p}' for p in self.response_validator(parsed)]
                    if any(f.startswith('validator:') for f in failure):
                        failure.append('validator_failed')
        resource = legacy.resource_failure_signal(stdout, stderr)
        if resource:
            failure.append('resource_failure')
            self.gate.stop('resource_failure:' + stage, f'{self.review_id}:{stage}')
        if cleanup.get('errors') or (proc is not None and
                                     (not cleanup.get('direct_child_reaped') or not cleanup.get('drained'))):
            self.gate.stop('process_cleanup_failure:' + stage, f'{self.review_id}:{stage}')
        # Some failed turns report usage too. Keep it when supplied; never
        # fabricate zero billing for an attempt whose CLI omitted usage.
        usages = [e['usage'] for e in _events(stdout)
                  if e.get('type') in ('turn.completed', 'turn.failed') and isinstance(e.get('usage'), dict)]
        record = {'review_id': self.review_id, 'stage': stage, 'attempt': number, 'model': self.model,
                  'status': 'ok' if not failure else 'error', 'failure_kinds': failure,
                  'resource_failure': resource, 'returncode': returncode, 'killed_on_timeout': 'timeout' in failure,
                  'raw_response': raw, 'tool_audit': audit, 'extra_tool_markers': extra,
                  'actual_tool_calls': audit['tool_call_count'] + len(extra),
                  'actual_usage': {'per_turn': usages, 'total': legacy._sum_usage(usages)},
                  'reference_tokens_o200k_input': prepared['reference_tokens'],
                  'timing': {'admission_started_at_epoch': start['started_at'], 'spawn_called_at_epoch': start['spawn_called_at'],
                             'spawned_at_epoch': start['spawned_at'], 'child_pid': start['child_pid'],
                             'admission_waited_seconds': start.get('waited', 0), 'duration_seconds': self.clock()-began},
                  'process_cleanup': cleanup, 'live_slot': slot, 'order_name': self.order,
                  'input_sha256': legacy.oar_core._sha(text),
                  'isolation': {'disabled_features': list(legacy.DISABLED), 'removed_environment_keys': sorted(legacy.REMOVED_ENV),
                                'web_search': 'disabled', 'mcp_servers': {}, 'sandbox': 'read-only', 'tools_required': 0,
                                'start_new_session': True, 'command_source': 'ace_review.isolated_review.command_for'}}
        return record, {'command': prepared['command'], 'stdout': stdout, 'stderr': stderr}
