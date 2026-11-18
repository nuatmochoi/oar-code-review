"""Source-extracted component probe, not a full Langflow integration witness.

Executes only three inspected upstream methods. Transaction persistence is an
async no-op boundary; object construction is a local fixture. Positive evidence
is the coroutine wait chain and locked/waiter state, never a timeout alone.
"""
import ast
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / 'candidate_sources/langflow-ai__langflow/5896'
METHODS = ('get_result', '_get_result', '_log_transaction_async')
source_hashes, excerpts = ({}, {})
for version in ('base', 'head'):
    path = SOURCE / version / 'src/backend/base/langflow/graph/vertex/base.py'
    source = path.read_text()
    owner = next((n for n in ast.parse(source).body if isinstance(n, ast.ClassDef) and n.name == 'Vertex'))
    nodes = {n.name: n for n in owner.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in METHODS}
    assert set(nodes) == set(METHODS)
    excerpts[version] = '\n\n'.join((textwrap.dedent(ast.get_source_segment(source, nodes[name])) for name in METHODS))
    source_hashes[version] = {'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'spans': {name: [nodes[name].lineno, nodes[name].end_lineno] for name in METHODS}}
HARNESS = 'from __future__ import annotations\nimport asyncio\nfrom contextlib import suppress\nimport json\nfrom types import SimpleNamespace\n\nasync def log_transaction(*args, **kwargs):\n    return None\n\nclass Subject:\n{methods}\n\ndef chain(coroutine):\n    result=[]\n    for _ in range(16):\n        if coroutine is None:\n            break\n        code=getattr(coroutine, "cr_code", None)\n        result.append(code.co_name if code else type(coroutine).__name__)\n        coroutine=getattr(coroutine, "cr_await", None)\n    return result\n\nasync def run(flow_id):\n    subject=Subject()\n    subject._lock=asyncio.Lock()\n    subject.graph=SimpleNamespace(flow_id=flow_id)\n    subject.built=True\n    subject.use_result=True\n    subject.built_result="result-marker"\n    subject.built_object=None\n    subject.log_transaction_tasks=set()\n    task=asyncio.create_task(subject.get_result(subject))\n    await asyncio.sleep(0)\n    await asyncio.sleep(0)\n    waiting=chain(task.get_coro())\n    waiters=len(subject._lock._waiters or ())\n    if task.done():\n        result=task.result()\n        status="pass" if result == "result-marker" else "unexpected_result"\n    elif subject._lock.locked() and waiters == 1 and all(name in waiting for name in\n            ("get_result", "_get_result", "_log_transaction_async", "__aenter__", "acquire")):\n        status="observed_nested_nonreentrant_wait"\n        task.cancel()\n        with suppress(asyncio.CancelledError):\n            await task\n    else:\n        status="unresolved"\n        task.cancel()\n        with suppress(asyncio.CancelledError):\n            await task\n    await asyncio.sleep(0)\n    return {{"flow_enabled":bool(flow_id),"status":status,"wait_chain":waiting,"waiter_count":waiters}}\n\nasync def main():\n    print(json.dumps([await run(None), await run("flow-id")]))\n\nasyncio.run(main())\n'
records = []
with tempfile.TemporaryDirectory(prefix='context-source-probe-') as temp:
    directory = Path(temp)
    profile = directory / 'profile.sb'
    profile.write_text('(version 1)\n(allow default)\n(deny network*)\n(deny file-write*)\n(deny file-read* (subpath "${USER_HOME}"))\n'.replace('${USER_HOME}', str(Path.home())))
    for version in ('base', 'head'):
        program = directory / (version + '.py')
        program.write_text(HARNESS.format(methods=textwrap.indent(excerpts[version], '    ')))
        for repeat in range(2):
            result = subprocess.run(['/usr/bin/sandbox-exec', '-f', str(profile), str(Path(sys.executable).resolve()), '-I', '-B', str(program)], capture_output=True, text=True, timeout=8)
            if result.returncode:
                raise RuntimeError(result.stderr)
            observations = json.loads(result.stdout)
            expected = ['pass', 'pass' if version == 'base' else 'observed_nested_nonreentrant_wait']
            assert [r['status'] for r in observations] == expected
            records.append({'version': version, 'repeat': repeat + 1, 'observations': observations, 'sandbox': 'network_and_file_writes_denied; HOME_reads_denied'})
report = {'status': 'component_mechanism_reproduced', 'repository': 'langflow-ai/langflow', 'pr': 5896, 'source_hashes': source_hashes, 'records': records, 'limitations': ['Only three source-extracted methods executed; full Vertex construction and dependency environment not reproduced.', 'Transaction persistence is an async no-op boundary.', 'flow_id None/string is a constructed scenario contrast, not an independently annotated benchmark pair.', 'No reviewer evaluation or independent gold approval performed.'], 'timeout_is_target_failure': False, 'ready_for_new_12_family_pilot': False}
(ROOT / 'langflow_5896_component_probe.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
print(json.dumps({'status': report['status'], 'four_cells_repeated': 2, 'pilot_ready': False}))
