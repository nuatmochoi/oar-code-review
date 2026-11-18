"""Codex-CLI CompletionClient transport with claim files, call cap, and admission gate.

Reuses (read-only import) ``ace_review.isolated_review.command_for`` /
``child_environment`` and ``ace_review.reviewer._trace_audit``. The command line,
model, reasoning effort and tool isolation are exactly what ``command_for`` builds;
nothing here edits Codex configuration or credentials.

``CliCompletionClient.complete(*, system, prompt, suffix='', shared='') -> str`` matches
the product CompletionClient signature. Differences the adapter must know about:
  * ``command_for`` fixes the developer instruction, so ``system`` cannot travel as a
    developer message; all four parts are placed in the single stdin text in a recorded order.
  * a call is only legal inside a declared stage (initial / update_1 / update_2), exactly one
    ``complete`` per stage. A second call in the stage (the product's format retry or a
    reflection call) is refused, not issued, and logged. Cap is 3 claims per review.
  * a failed call raises ``CallFailed`` (with the full record). It never returns text that
    failed schema/tool gates, so an invalid response cannot be mistaken for success.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Optional

HERE = Path(__file__).resolve().parent
if sys.pycache_prefix is None:                      # keep bytecode of reused modules inside this folder
    sys.pycache_prefix = str(HERE / '.pycache')
ACE_PARENT = os.environ.get('OAR_ACE_PARENT', str(HERE.parent.parent))
if ACE_PARENT not in sys.path:
    sys.path.insert(0, ACE_PARENT)

import jsonschema  # noqa: E402

from ace_review.isolated_review import DISABLED, REMOVED_ENV, child_environment, command_for  # noqa: E402
from ace_review.reviewer import _trace_audit  # noqa: E402

import oar_core  # noqa: E402

MODEL = 'gpt-5.6-sol'
STAGES = ('initial', 'update_1', 'update_2')
MAX_CALLS = 3
MIN_START_INTERVAL = 3.0
ORDERS = {
    # name -> section order inside the single stdin text
    'system_shared_prompt_suffix': ('system', 'shared', 'prompt', 'suffix'),
    'system_prompt_shared_suffix': ('system', 'prompt', 'shared', 'suffix'),
}
DEFAULT_ORDER = 'system_shared_prompt_suffix'
RESOURCE_PATTERN = re.compile(
    r'\b429\b|rate[ _-]?limit|too many requests|insufficient[_ ]quota|quota exceeded|'
    r'resource[_ ]exhausted|usage limit', re.I)
# Only explicit CLI failure events and non-warning stderr lines are searched for resource
# exhaustion; model prose (agent_message text, the response body) is never searched.
ERROR_EVENT_TYPES = ('error', 'turn.failed')
STDERR_BENIGN = re.compile(r'\b(WARN|WARNING|INFO|DEBUG|TRACE)\b')
DRAIN_TIMEOUT = 10.0
# markers in addition to _trace_audit's (command_execution/tool_call/web_search/file_search/computer_call)
EXTRA_TOOL_MARKERS = ('function_call', 'local_shell', 'file_change', 'apply_patch', 'patch_apply', 'collab')


class TransportError(Exception):
    pass


class CallCapExceeded(TransportError):
    pass


class RetryRefused(TransportError):
    pass


class StageError(TransportError):
    pass


class OutputExists(TransportError):
    pass


class ClaimedIncomplete(TransportError):
    """A claim exists without a result: the attempt may have been issued. Never re-issued."""


class GlobalStop(TransportError):
    pass


class CallFailed(TransportError):
    def __init__(self, record: dict):
        super().__init__(f"{record['stage']}: {record['failure_kinds']}")
        self.record = record


def write_new(path: Path, value) -> None:
    """Exclusive create; raises FileExistsError instead of overwriting."""
    with Path(path).open('x', encoding='utf-8') as stream:
        if isinstance(value, str):
            stream.write(value)
        else:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write('\n')


# ---------------------------------------------------------------- admission gate
class AdmissionGate:
    """Process-safe spacing of real CLI starts + global stop file.

    ``acquire`` holds an flock while it checks the stop file, checks spacing against the
    last recorded start, runs ``claim_fn`` (the exclusive claim) and records the start.
    Sleeping happens outside the lock. clock/sleep are injectable for deterministic tests.
    """

    def __init__(self, directory, *, min_interval: float = MIN_START_INTERVAL,
                 clock: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.min_interval, self.clock, self.sleep = min_interval, clock, sleep
        self.lock_path = self.dir / 'start.lock'
        self.starts_path = self.dir / 'starts.jsonl'
        self.stop_path = self.dir / 'STOP'
        self.unattempted_path = self.dir / 'unattempted.jsonl'

    @contextmanager
    def _locked(self):
        with open(self.lock_path, 'a+') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def starts(self) -> list:
        if not self.starts_path.exists():
            return []
        return [json.loads(line) for line in self.starts_path.read_text().splitlines() if line.strip()]

    def is_stopped(self) -> Optional[dict]:
        if self.stop_path.exists():
            return json.loads(self.stop_path.read_text())
        return None

    def stop(self, reason: str, label: str = '') -> bool:
        """Create the global stop file once (first reason wins). True if this call created it."""
        try:
            write_new(self.stop_path, {'reason': reason, 'label': label, 'time': self.clock()})
            return True
        except FileExistsError:
            return False

    def record_unattempted(self, label: str, why: str) -> None:
        with self._locked(), self.unattempted_path.open('a', encoding='utf-8') as out:
            out.write(json.dumps({'label': label, 'why': why, 'time': self.clock()}) + '\n')

    def start(self, *, label: str, spawn: Optional[Callable[[], object]] = None,
              claim_fn: Optional[Callable[[], None]] = None) -> tuple:
        """Admit and (optionally) spawn under the start lock; returns ``(entry, process)``.

        Under the flock: check the stop file, check spacing against the previous recorded
        *spawn*, run ``claim_fn`` (exclusive claim), call ``spawn()`` and record the start.
        Sleeping happens outside the lock. The caller collects the process after this returns,
        so the lock is never held while a call runs. A failing ``claim_fn`` records nothing; a
        failing ``spawn`` is recorded (``spawn_error``, slot consumed) and returns process None.
        """
        waited = 0.0
        while True:
            with self._locked():
                stop = self.is_stopped()
                if stop:
                    raise GlobalStop(f"global stop active: {stop['reason']}")
                now = self.clock()
                starts = self.starts()
                last = (starts[-1].get('spawned_at', starts[-1]['started_at'])) if starts else None
                wait = 0.0 if last is None else last + self.min_interval - now
                if wait <= 0:
                    return _start_now(label, spawn, claim_fn, self.clock, waited, self.starts_path)
            self.sleep(wait)
            waited += wait

    def acquire(self, *, label: str, claim_fn: Optional[Callable[[], None]] = None) -> dict:
        """Admission only (no process); kept for callers that spawn elsewhere."""
        return self.start(label=label, claim_fn=claim_fn)[0]


def _start_now(label, spawn, claim_fn, clock, waited, log_path) -> tuple:
    if claim_fn is not None:
        claim_fn()                                   # may raise; then no start is recorded
    called = clock()
    process, error = None, None
    if spawn is not None:
        try:
            process = spawn()
        except Exception as exc:                     # recorded; the caller turns it into a failure
            error = f'{type(exc).__name__}: {exc}'
    try:
        entry = {'label': label, 'started_at': called, 'spawn_called_at': called, 'spawned_at': clock(),
                 'child_pid': getattr(process, 'pid', None), 'pid': os.getpid(), 'waited': waited}
        if error:
            entry['spawn_error'] = error
        if log_path is not None:
            with Path(log_path).open('a', encoding='utf-8') as out:
                out.write(json.dumps(entry) + '\n')
    except BaseException:
        reap(process)                                # a spawned child must never outlive a failed record
        raise
    return entry, process


def reap(process) -> None:
    """Best-effort kill + drain of a spawned child; never raises. No-op for ``None``."""
    if process is None:
        return
    for action in (lambda: process.kill(), lambda: process.communicate(timeout=DRAIN_TIMEOUT)):
        try:
            action()
        except BaseException:                         # noqa: BLE001 - cleanup must not mask the original error
            pass


def min_gap(starts: list) -> Optional[float]:
    times = sorted(s['started_at'] for s in starts)
    return min((b - a for a, b in zip(times, times[1:])), default=None)


# ---------------------------------------------------------------- client
def compose_input(order_name: str, system: str, shared: str, prompt: str, suffix: str) -> tuple:
    """Returns (text, parts) where parts records exact order and per-part hashes."""
    if order_name not in ORDERS:
        raise ValueError(f'unknown order {order_name!r}')
    values = {'system': system, 'shared': shared, 'prompt': prompt, 'suffix': suffix}
    blocks, parts = [], []
    for name in ORDERS[order_name]:
        text = values[name]
        parts.append({'name': name, 'present': bool(text), 'chars': len(text),
                      'sha256': oar_core._sha(text)})
        if text:
            blocks.append(f'[{name.upper()}]\n{text}')
    return '\n\n'.join(blocks), parts


class CliCompletionClient:
    def __init__(self, *, out_dir, review_id: str, schema: dict, model: str = MODEL,
                 order: str = DEFAULT_ORDER, max_calls: int = MAX_CALLS,
                 gate: Optional[AdmissionGate] = None, timeout: float = 240,
                 spawner: Optional[Callable] = None, clock: Callable[[], float] = time.monotonic,
                 response_validator: Optional[Callable[[dict], list]] = None):
        if not review_id or '/' in review_id or '\\' in review_id or review_id in {'.', '..'}:
            raise ValueError('invalid review id')
        if max_calls > MAX_CALLS:
            raise ValueError(f'max_calls above the protocol cap {MAX_CALLS}')
        if order not in ORDERS:
            raise ValueError(f'unknown order {order!r}')
        jsonschema.Draft202012Validator.check_schema(schema)
        probe = command_for('/nonexistent', model)
        if 'model_reasoning_effort="low"' not in probe or model not in probe:
            raise TransportError('command_for no longer pins model/reasoning low; refusing to run')
        self.dir = Path(out_dir) / review_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.review_id, self.schema, self.model, self.order = review_id, schema, model, order
        self.max_calls, self.gate, self.timeout = max_calls, gate, timeout
        self.spawner, self.clock, self.response_validator = spawner or _default_spawn, clock, response_validator
        self._stage: Optional[str] = None
        self._stage_calls = 0
        self._begun: list = []

    # -- stages
    def begin_stage(self, stage: str) -> None:
        if stage not in STAGES:
            raise StageError(f'stage must be one of {STAGES}')
        if STAGES.index(stage) != len(self._begun):
            raise StageError(f'next stage must be {STAGES[len(self._begun)] if len(self._begun) < len(STAGES) else None}, got {stage}')
        self._begun.append(stage)
        self._stage, self._stage_calls = stage, 0

    def claims(self) -> list:
        return sorted(self.dir.glob('*.claim.json'))

    def _paths(self, stage: str) -> dict:
        return {k: self.dir / f'{stage}.{k}' for k in
                ('claim.json', 'input.json', 'trace.json', 'raw.txt', 'result.json')}

    def _refuse(self, exc: TransportError, detail: dict) -> None:
        with (self.dir / 'refusals.jsonl').open('a', encoding='utf-8') as out:
            out.write(json.dumps({'refusal': type(exc).__name__, 'message': str(exc), **detail}) + '\n')
        raise exc

    def recover(self, stage: str) -> Optional[dict]:
        """Read-only: the finished record of a stage, or None. Never triggers a call."""
        path = self._paths(stage)['result.json']
        return json.loads(path.read_text()) if path.exists() else None

    # -- the product-facing method
    def complete(self, *, system: str, prompt: str, suffix: str = '', shared: str = '') -> str:
        stage = self._stage
        if stage is None:
            self._refuse(StageError('complete() outside begin_stage()'), {})
        if self._stage_calls >= 1:
            self._refuse(RetryRefused(
                f'second complete() in stage {stage}: retry/reflection calls are not issued'),
                {'stage': stage})
        self._stage_calls += 1
        if len(self.claims()) >= self.max_calls:
            self._refuse(CallCapExceeded(f'{len(self.claims())} claims already exist (cap {self.max_calls})'),
                         {'stage': stage})
        paths = self._paths(stage)
        text, parts = compose_input(self.order, system, shared, prompt, suffix)
        if not prompt.strip():
            self._refuse(TransportError('empty prompt'), {'stage': stage})
        claim = {'review_id': self.review_id, 'stage': stage, 'model': self.model,
                 'order': self.order, 'input_sha256': oar_core._sha(text), 'claimed_at': time.time()}

        with tempfile.TemporaryDirectory(prefix='oar-cli-') as temporary:
            prepared = self._prepare(text, Path(temporary))        # slow work happens before the start lock
            input_record = {
                'stage': stage, 'order_name': self.order, 'order': list(ORDERS[self.order]), 'parts': parts,
                'raw': {'system': system, 'shared': shared, 'prompt': prompt, 'suffix': suffix},
                'composed_text': text, 'composed_sha256': oar_core._sha(text),
                'reference_tokens_o200k': prepared['reference_tokens'],
                'note': 'system rides inside stdin because command_for fixes developer_instructions'}

            def make_claim():
                # exclusive claim, then the input record: both exist before any child is spawned, so a
                # failure here can never leave a running process behind (the stage stays burned instead)
                write_new(paths['claim.json'], claim)
                write_new(paths['input.json'], input_record)
            spawn = lambda: self.spawner(prepared['command'], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=subprocess.PIPE, text=True, env=prepared['env'])
            try:
                if self.gate is not None:
                    start, proc = self.gate.start(label=f'{self.review_id}:{stage}', spawn=spawn,
                                                  claim_fn=make_claim)
                else:
                    start, proc = _start_now(f'{self.review_id}:{stage}', spawn, make_claim, time.time,
                                             0.0, None)
            except FileExistsError:
                if paths['result.json'].exists():
                    self._refuse(OutputExists(f'{stage} already has a result; not overwritten'), {'stage': stage})
                self._refuse(ClaimedIncomplete(f'{stage} was claimed but has no result; not re-issued'),
                             {'stage': stage})
            except GlobalStop as exc:
                if self.gate is not None:
                    self.gate.record_unattempted(f'{self.review_id}:{stage}', str(exc))
                self._refuse(exc, {'stage': stage, 'claimed': False})
            record = self._attempt(stage, text, parts, system, shared, prompt, suffix, start, proc,
                                   prepared, paths, Path(temporary))
        if record['status'] != 'ok':
            raise CallFailed(record)
        return record['raw_response']

    def _prepare(self, text: str, temp: Path) -> dict:
        """Everything slow and side-effect free in the review dir: schema file, command, tokens, env."""
        write_new(temp / 'schema.json', self.schema)
        return {'command': command_for(temp, self.model), 'env': child_environment(),
                'reference_tokens': oar_core.count_reference_tokens(text)}

    # -- collect one (real or stubbed) CLI process; the claim exists and the process is already spawned
    def _attempt(self, stage, text, parts, system, shared, prompt, suffix, start, proc, prepared,
                 paths, temp) -> dict:
        try:
            return self._collect(stage, text, start, proc, prepared, paths, temp)
        except BaseException:
            reap(proc)               # any exception between spawn and the final record kills the child
            raise

    def _collect(self, stage, text, start, proc, prepared, paths, temp) -> dict:
        command = prepared['command']
        failure: list = []
        stdout = stderr = ''
        returncode = raw = parsed = None
        killed = False
        began = self.clock()
        if proc is None:
            failure.append('os_error')
            stderr = start.get('spawn_error', '')
        else:
            try:
                stdout, stderr = proc.communicate(input=text, timeout=self.timeout)
            except subprocess.TimeoutExpired as exc:
                failure.append('timeout')
                proc.kill()
                killed = True
                try:
                    stdout, stderr = proc.communicate(timeout=DRAIN_TIMEOUT)
                except subprocess.TimeoutExpired:
                    failure.append('drain_timeout')
                    stdout, stderr = _text(exc.stdout), _text(exc.stderr)
            stdout, stderr, returncode = _text(stdout), _text(stderr), proc.returncode
        response_path = temp / 'response.json'
        if response_path.exists():
            raw = response_path.read_text(encoding='utf-8')
        duration = self.clock() - began
        if returncode not in (None, 0) and 'timeout' not in failure:
            failure.append('nonzero_exit')
        audit = _trace_audit(stdout)
        extra = [m for m in _event_markers(stdout) if any(w in m for w in EXTRA_TOOL_MARKERS)]
        if audit['tool_call_count'] or extra:
            failure.append('tool_calls_observed')
        if raw is None:
            if 'timeout' not in failure and 'os_error' not in failure:
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
                    failure.append('schema_invalid')
                    failure.append(f'schema_error: {exc.message[:200]}')
                if self.response_validator is not None and not any(f.startswith('schema') for f in failure):
                    problems = self.response_validator(parsed)
                    if problems:
                        failure.append('validator_failed')
                        failure.extend(f'validator: {p}' for p in problems)
        resource = bool(failure) and resource_failure_signal(stdout, stderr)
        if resource:
            failure.append('resource_failure')
            if self.gate is not None:
                self.gate.stop('resource_failure:' + stage, f'{self.review_id}:{stage}')
        usages = _usages(stdout)
        record = {
            'review_id': self.review_id, 'stage': stage, 'model': self.model,
            'status': 'ok' if not failure else 'error', 'failure_kinds': failure,
            'resource_failure': resource, 'returncode': returncode, 'killed_on_timeout': killed,
            'raw_response': raw,
            'tool_audit': audit, 'extra_tool_markers': extra,
            'actual_tool_calls': audit['tool_call_count'] + len(extra),
            'actual_usage': {'per_turn': usages, 'total': _sum_usage(usages)},
            'reference_tokens_o200k_input': prepared['reference_tokens'],
            'timing': {'admission_started_at_epoch': start['started_at'],
                       'spawn_called_at_epoch': start['spawn_called_at'],
                       'spawned_at_epoch': start['spawned_at'], 'child_pid': start['child_pid'],
                       'admission_waited_seconds': start.get('waited', 0.0), 'duration_seconds': duration},
            'order_name': self.order, 'input_sha256': oar_core._sha(text),
            'isolation': {'disabled_features': list(DISABLED), 'removed_environment_keys': sorted(REMOVED_ENV),
                          'web_search': 'disabled', 'mcp_servers': {}, 'sandbox': 'read-only',
                          'tools_required': 0, 'command_source': 'ace_review.isolated_review.command_for'}}
        write_new(paths['trace.json'], {'command': command, 'stdout': stdout, 'stderr': stderr})
        if raw is not None:
            write_new(paths['raw.txt'], raw)
        write_new(paths['result.json'], {k: v for k, v in record.items()})
        return record


def _default_spawn(*args, **kwargs):
    return subprocess.Popen(*args, **kwargs)


def resource_failure_signal(stdout: str, stderr: str) -> bool:
    """True only for explicit CLI error events or non-warning stderr lines naming exhaustion.

    Model prose (agent_message text, the response body) is never inspected, so a schema error
    whose answer mentions '429' or 'rate limit' is not an infrastructure failure.
    """
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and event.get('type') in ERROR_EVENT_TYPES:
            error = event.get('error')
            message = event.get('message') or (error.get('message') if isinstance(error, dict) else error)
            if RESOURCE_PATTERN.search(str(message or '')):
                return True
    return any(RESOURCE_PATTERN.search(l) and not STDERR_BENIGN.search(l) for l in stderr.splitlines())


def _text(value) -> str:
    if value is None:
        return ''
    return value.decode(errors='replace') if isinstance(value, bytes) else value


def _event_markers(stdout: str) -> list:
    out = []
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            item = event.get('item') if isinstance(event.get('item'), dict) else {}
            out.append(f"{event.get('type', '')}:{item.get('type', '')}".lower())
    return out


def _usages(stdout: str) -> list:
    out = []
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and event.get('type') == 'turn.completed' and 'usage' in event:
            out.append(event['usage'])
    return out


def _sum_usage(usages: list) -> dict:
    total: dict = {}
    for usage in usages:
        for key, value in usage.items():
            if isinstance(value, (int, float)):
                total[key] = total.get(key, 0) + value
    return total
