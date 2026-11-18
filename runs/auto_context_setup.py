"""Prepare real SCIP artifacts then frozen requests; never runs the reviewer."""
import hashlib
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ace_review.auto_context import ARMS, Graph, retrieve, stage_snapshot
from ace_review.context_necessity import digest, freeze, prepare
from ace_review.counterfactual import write_new
from ace_review.protocol_hypothesis import clean_row

SETUP = ROOT / 'runs/auto_context_setup2'
RUN = ROOT / 'runs/auto_context2'
RUNTIME = ROOT / 'tools/venv-auto-scip311'
SCIP = ROOT / 'tools/scip-python/node_modules/.bin/scip-python'
EXPORT = ROOT / 'tools/bin/scip-export'


def index_cell(job):
    case, project, cell = job
    source = case / 'snapshots' / cell
    output = SETUP / 'indices' / project / cell
    expected = json.loads((case / 'execution.json').read_text())['file_sha256'][cell]
    audit = stage_snapshot(source, output, project, RUNTIME)
    if audit['source_sha256'] != expected:
        raise ValueError('Original snapshot changed: ' + str(source))
    command = [str(SCIP), 'index', '--cwd', str(output), '--project-name', project,
               '--project-version', '0.0.0', '--environment', str(SETUP / 'environment.json')]
    env = dict(os.environ, PATH=str(RUNTIME / 'bin') + os.pathsep + os.environ['PATH'])
    started = time.monotonic()
    result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=240)
    audit.update(command=command, elapsed_seconds=time.monotonic() - started, returncode=result.returncode)
    write_new(output / 'stdout.log', result.stdout, raw=True)
    write_new(output / 'stderr.log', result.stderr, raw=True)
    incompatible = any(s in (result.stdout + result.stderr).lower() for s in
                       ('unsupported version', 'is unsupported', 'could not resolve', 'not found'))
    audit['incompatible_environment_log'] = incompatible
    if result.returncode or incompatible or not (output / 'index.scip').exists():
        write_new(output / 'index_audit.json', audit)
        raise RuntimeError('SCIP failed: ' + str(output))
    raw = json.loads(subprocess.check_output([str(EXPORT), str(output / 'index.scip')], text=True, timeout=60))
    write_new(output / 'index.json', raw)
    sources = {f: (source / f).read_text() for f in expected}
    graph = Graph(sources, raw)
    audit.update(graph=graph.audit, protobuf_sha256=digest(output / 'index.scip'), json_sha256=digest(output / 'index.json'))
    write_new(output / 'index_audit.json', audit)
    if graph.audit['missing_documents'] or not graph.audit['occurrences']:
        raise ValueError('Incomplete project document index: ' + str(output))
    print(json.dumps({'project': project, 'cell': cell, 'seconds': audit['elapsed_seconds'], **graph.audit}), flush=True)
    return (project, cell), (graph, audit)


def main():
    SETUP.mkdir(exist_ok=False)
    write_new(SETUP / 'plan.md', (ROOT / 'runs/AUTO_CONTEXT_PLAN_KO.md').read_text(), raw=True)
    write_new(SETUP / 'frozen_cases.py', (ROOT / 'ace_review/auto_context.py').read_text(), raw=True)
    write_new(SETUP / 'frozen_setup.py', Path(__file__).read_text(), raw=True)
    code = ('import importlib.metadata as m,json; print(json.dumps(['
            '{"name":d.metadata["Name"],"version":d.version,"files":[str(f) for f in d.files or []]}'
            ' for d in m.distributions()]))')
    environment = json.loads(subprocess.check_output([str(RUNTIME / 'bin/python'), '-c', code], text=True))
    write_new(SETUP / 'environment.json', environment)
    dependencies = {'scip_python': '0.6.6', 'python_runtime': subprocess.check_output([str(RUNTIME / 'bin/python'), '--version'], text=True).strip(),
                    'python_analysis_target': '3.11', 'witness_runtime': 'CPython 3.14.6 (unchanged original execution gate)',
                    'node': subprocess.check_output(['node', '--version'], text=True).strip(),
                    'package_lock_sha256': digest(ROOT / 'tools/scip-python/package-lock.json'),
                    'indexer_bundle_sha256': digest(ROOT / 'tools/scip-python/node_modules/@sourcegraph/scip-python/dist/scip-python.js'),
                    'exporter_sha256': digest(EXPORT), 'reference_tokenizer': 'o200k_base', 'source_cap': 800}
    write_new(SETUP / 'dependencies.json', dependencies)
    specs = [(ROOT / 'runs/context_pager_case1', 'click'), (ROOT / 'runs/context_backend_case3', 'anyio')]
    jobs = [(case, project, variant + '_' + version) for case, project in specs
            for variant in ('safe', 'unsafe') for version in ('base', 'head')]
    with ThreadPoolExecutor(max_workers=2) as pool:
        indices = dict(pool.map(index_cell, jobs))
    rows, audits, labels = [], [], {}
    for case, project in specs:
        case_labels = json.loads((case / 'private_labels.json').read_text())
        originals = [json.loads(line) for line in (case / 'requests.jsonl').read_text().splitlines()]
        for original in originals:
            if original['condition'] != 'diff':
                continue
            variant = case_labels[original['task_id']]['variant']
            base, head = [indices[project, variant + '_' + version][0] for version in ('base', 'head')]
            for arm in ARMS:
                # Selection has no access to metadata above, labels, manual sources or responses.
                blocks, audit = retrieve(base, head, original['diff'], arm)
                row = clean_row(original) | {'condition': arm, 'contexts': blocks,
                    'context_status': {'context_tokens': audit['source_tokens']}}
                rows.append(row)
                audits.append(dict(task_id=row['task_id'], **audit))
        labels.update(case_labels)
    write_new(SETUP / 'requests.jsonl', rows, jsonl=True)
    write_new(SETUP / 'private_labels.json', labels)
    write_new(SETUP / 'source_audit.json', audits)
    manifest = {'indices': {project + '/' + cell: a for (project, cell), (_, a) in indices.items()},
                'source_cases': [str(c) for c, _ in specs], 'cohort': 'Previously observed development cases',
                'calls_cap': 96, 'setup_script_sha256': digest(Path(__file__))}
    write_new(SETUP / 'case_manifest.json', manifest)
    for name, path in [('frozen_protocol.py', 'ace_review/protocol_hypothesis.py'), ('frozen_reviewer.py', 'ace_review/reviewer.py')]:
        write_new(SETUP / name, (ROOT / path).read_text(), raw=True)
    freeze(SETUP)
    print({'prepared_rows': prepare(SETUP / 'requests.jsonl', RUN)}, flush=True)


if __name__ == '__main__':
    main()
