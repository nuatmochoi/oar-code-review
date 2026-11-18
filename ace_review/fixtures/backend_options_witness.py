"""Execute AnyIO's real adapter; identify the actual Trio clock without sleeping."""
import importlib.metadata
import json
import sys
from pathlib import Path

snapshot = Path(sys.argv[1]).resolve()
sys.path[:0] = [str(snapshot), str(snapshot / 'src')]
import anyio
import trio
from trio.testing import MockClock
from client import run_job

assert Path(anyio.__file__).resolve().is_relative_to(snapshot)
checks = []
for initial in (0, 17, 1000):
    clock = MockClock(rate=0)
    clock.jump(initial)
    async def probe():
        return trio.lowlevel.current_clock() is clock, trio.current_time()
    same_clock, observed = run_job(probe, backend_options={'clock': clock})
    checks.append({'requested_time': initial, 'observed_time': observed, 'same_clock': same_clock,
                   'status': 'pass' if same_clock and observed == initial else 'target_failure'})
statuses = {c['status'] for c in checks}
status = statuses.pop() if len(statuses) == 1 else 'environment_error'
print(json.dumps({'status': status, 'checks': checks, 'python': sys.version, 'library_file': anyio.__file__,
                  'dependencies': {p: importlib.metadata.version(p) for p in ('trio', 'sniffio', 'idna', 'attrs', 'outcome')}}))
sys.exit(0 if status == 'pass' else 10 if status == 'target_failure' else 20)
