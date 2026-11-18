"""Frozen context-selector / generated-counterexample development pilot.

This is inspired by SwingArena, not a reproduction of its dataset or arena.
Source selection has no access to labels; private pairing is evaluation-only.
"""
from __future__ import annotations

import argparse
import ast
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
import tempfile

from .auto_context import Graph, changed_query, retrieve
from .bm25 import BM25Index, Document
from .counterfactual import block
from .obligation import render_blocks, token_count
from .packet_review import write_new
from .isolated_review import call_packet
from .protocol_hypothesis import mask_source
from .selector_ablation import retrieve_selector

ARMS = ('diff_only', 'bm25_lines', 'bm25_syntax', 'paired_scip')
CELLS = ('safe_base', 'safe_head', 'unsafe_base', 'unsafe_head')
SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'required': ['verdict', 'explanation', 'test_intent', 'test_source'],
    'properties': {
        'verdict': {'type': 'string', 'enum': ['introduced_defect', 'no_supported_defect', 'insufficient_evidence']},
        'explanation': {'type': 'string'}, 'test_intent': {'type': 'string'},
        'test_source': {'type': 'string'},
    },
}
INSTRUCTION = '''Review the patch in its stated deployment scope. Decide whether it introduces a concrete behavioral defect, using only the supplied diff and repository evidence. A missing dependency implementation is not proof of a defect; express uncertainty when appropriate.
Also write one standalone Python regression-test script for the behavior most plausibly at risk. Even if you find no supported defect, test an applicable expected behavior; do not deliberately fail merely to agree with a verdict. The script should exit 0 if the expected behavior holds and fail an assertion or raise if it does not. The runner supplies the repository root and src on sys.path. Use normal package imports; no sys.path manipulation is needed. Dependencies are Python 3.14.6 and Trio 0.32.0. You may use stdlib unittest.mock, tempfile and pathlib, and import click, anyio, trio, or client as appropriate.
Write at most 120 lines, include an executable assertion, do not use pytest or unittest discovery, do not modify source files, inspect source/AST, branch on file paths/version labels, read unrelated files, access network, launch processes, or use eval/exec. A public behavior test may mock only environmental boundaries (TTY, OS platform flags, display subprocess) required by the stated deployment scope; do not replace the changed implementation or its internal adapter with a fake. Temporary output files may be read to check their contents. Catch expected errors only when that is the actual API contract. Return JSON only.
'''


def sha(data):
    if isinstance(data, str):
        data = data.encode()
    return hashlib.sha256(data).hexdigest()


def line_bm25(sources, diff, budget=800):
    documents = []
    for file, source in sorted(sources.items()):
        lines = mask_source(source).splitlines()
        for lo in range(0, len(lines), 60):
            documents.append(Document(file, lo + 1, min(lo + 60, len(lines)), '\n'.join(lines[lo:lo + 60])))
    ranked = BM25Index(documents).search(changed_query(diff), top_k=len(documents))
    selected, spans = [], []
    for score, document in ranked:
        candidate = block(document.file, document.text.splitlines())
        candidate.update(id=f'CTX-{len(selected) + 1:02}', start_line=document.start_line, end_line=document.end_line)
        if token_count(render_blocks(selected + [candidate])) <= budget:
            selected.append(candidate)
            spans.append(dict(file=document.file, start=document.start_line, end=document.end_line))
    return selected, dict(arm='bm25_lines', budget=budget, source_tokens=token_count(render_blocks(selected)), selected_spans=spans)


def load_graph(index_root):
    audit = json.loads((index_root / 'index_audit.json').read_text())
    sources = {name: (index_root / name).read_text() for name in audit['source_sha256']}
    for name, text in sources.items():
        if sha(text) != audit['source_sha256'][name]:
            raise ValueError(f'Indexed source changed: {index_root}/{name}')
    graph = Graph(sources, json.loads((index_root / 'index.json').read_text()))
    if graph.audit != audit['graph']:
        raise ValueError('Indexed graph changed')
    return graph, sources


def prompt(row, evidence):
    return (INSTRUCTION + '\nRepository: ' + row['repository'] + '\nDeployment scope: '
            + row['pr']['description'] + '\nPATCH:\n' + row['diff']
            + '\nADDITIONAL HEAD SOURCE:\n' + (render_blocks(evidence) or '(none)'))


def prepare(project_root, output):
    project_root, output = Path(project_root).resolve(), Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    source = project_root / 'runs/auto_context2'
    labels = json.loads((source / 'private_labels.json').read_text())
    original = [json.loads(line) for line in (source / 'requests.jsonl').read_text().splitlines()
                if json.loads(line)['condition'] == 'diff']
    if len(original) != 4:
        raise ValueError('Expected exactly four frozen development variants')
    packets, audits, private = [], [], {}
    for row in original:
        label = labels[row['task_id']]
        project = {'pager_stream': 'click', 'backend_options': 'anyio'}[label['family']]
        indices = project_root / 'runs/auto_context_dependency_check1/indices' / project
        base, _ = load_graph(indices / (label['variant'] + '_base'))
        head, sources = load_graph(indices / (label['variant'] + '_head'))
        # Only runtime paths/pair labels are retained privately; no target narrative.
        private[row['task_id']] = {k: label[k] for k in ('family', 'variant', 'source_run')}
        for arm in ARMS:
            if arm == 'diff_only':
                selected, audit = [], dict(source_tokens=0, selected_spans=[])
            elif arm == 'bm25_lines':
                selected, audit = line_bm25(sources, row['diff'])
            elif arm == 'bm25_syntax':
                selected, audit = retrieve(base, head, row['diff'], 'bm25', 800)
            else:
                selected, audit = retrieve_selector(base, head, row['diff'], 'selector_both', 800, 400)
            rendered = prompt(row, selected)
            packets.append(dict(run_id=f'swing_{len(packets):02}', task_id=row['task_id'], arm=arm,
                                repeat=1, prompt=rendered, prompt_sha256=sha(rendered),
                                source_tokens=token_count(render_blocks(selected)), prompt_tokens=token_count(rendered)))
            audits.append(dict(task_id=row['task_id'], condition=arm, **audit))
    write_new(output / 'packets.json', {'records': packets})
    write_new(output / 'private_pairing.json', private)
    write_new(output / 'retrieval_audit.json', audits)
    write_new(output / 'schema.json', SCHEMA)
    plan = '''# Swing-inspired pilot — fixed before model outputs

Two previously observed development families (Click pager, AnyIO backend options), two context-world variants each; no holdout and not a SwingArena benchmark reproduction.

- Four arms: diff-only; lexical BM25 over fixed 60-line blocks; lexical BM25 over AST blocks; existing paired-SCIP selector_both (diff-visible dedup + 400-token 2-hop unchanged reservation).
- All retrieval arms have an 800 o200k_base token maximum, including source block metadata. Whole blocks may leave unused budget. Diff-only intentionally supplies zero source context. AST BM25 is a modest syntax baseline, not SwingArena's CodeBERT reranker.
- Model gpt-5.6-sol, low reasoning, one generation per arm/world, 16 calls; at most two concurrent calls. One JSON response contains verdict plus generated test. No iterative repair or selection among several generations.
- Model tool access and web search disabled. Gold target descriptions, prior reviewer outputs, original witnesses and safe/unsafe labels are excluded from prompt. Only the source family deployment description retained from frozen tasks is shown.
- The paired selector was developed using these cases previously; superiority here would be a development signal, not an unbiased estimate.
- Primary witness criterion: identical generated script passes safe_base, unsafe_base and safe_head and fails unsafe_head, without source mutation, environment failure, or out-of-scope mocking. Four-cell execution alone is necessary but not sufficient: generated assertions and mocking must also be manually adjudicated against the stated deployment contract.
- Secondary: reviewer defect verdict on unsafe worlds and false-alarm verdict on safe worlds; test validity, runtime errors and token use. Two families and one draw do not support significance tests.
- Each generated test runs twice per snapshot to check repeatability, in fresh disposable copies with minimal environment, timeout, write restriction and network denial. Runtime 3.14.6 with frozen libraries. The generated-code safety gate is conservative; rejected tests count as invalid, not defects.
- Fixed selection and prompts are frozen before model calls. No test or prompt repair after seeing results. Runtime/harness faults must be reported separately.
'''
    (output / 'plan.md').write_text(plan)
    frozen = {name: sha((output / name).read_bytes()) for name in ['packets.json', 'private_pairing.json', 'retrieval_audit.json', 'schema.json', 'plan.md']}
    frozen['implementation'] = sha(Path(__file__).read_bytes())
    frozen['call_packet'] = sha((project_root / 'ace_review/packet_review.py').read_bytes())
    frozen['selector_ablation'] = sha((project_root / 'ace_review/selector_ablation.py').read_bytes())
    frozen['corrected_indices_freeze'] = sha((project_root / 'runs/auto_context_dependency_check1/freeze.json').read_bytes())
    write_new(output / 'freeze.json', frozen)
    return dict(packets=len(packets), max_source_tokens=max(p['source_tokens'] for p in packets))


ALLOWED_IMPORTS = {'click', 'anyio', 'trio', 'client', 'unittest', 'tempfile', 'pathlib', 'os', 'sys',
                   'io', 'contextlib', 'typing', 'json', 'math', 'functools', 'itertools', 'time', 'collections'}
FORBIDDEN_CALLS = {'eval', 'exec', 'compile', '__import__', 'breakpoint', 'input', 'exit', 'quit'}
FORBIDDEN_ATTRS = {'system', 'popen', 'spawn', 'fork', 'execv', 'execve', 'exit', '_exit', 'getsource',
                   'getfile', 'readlink', 'rglob', 'glob', 'walk', 'listdir', 'scandir'}


def validate_test(source):
    errors = []
    if len(source.splitlines()) > 120 or not source.strip():
        errors.append('empty or over 120 lines')
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return ['invalid Python syntax: ' + str(exc)]
    if not any(isinstance(n, ast.Assert) for n in ast.walk(tree)):
        errors.append('no explicit assertion')
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots = [alias.name.split('.')[0] for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            roots = [(node.module or '').split('.')[0]]
        else:
            roots = []
        errors.extend('import not allowed: ' + name for name in roots if name not in ALLOWED_IMPORTS)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in FORBIDDEN_CALLS:
            errors.append('call not allowed: ' + node.func.id)
        if isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_ATTRS:
            errors.append('attribute not allowed: ' + node.attr)
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if any(marker in node.value for marker in ('safe_head', 'unsafe_head', 'safe_base', 'unsafe_base', 'private_labels', 'witness.py', '/Users/', '/etc/', '/proc/')):
                errors.append('private label/path literal')
    return sorted(set(errors))


def tree_hashes(root):
    return {str(p.relative_to(root)): sha(p.read_bytes()) for p in root.rglob('*') if p.is_file()}


def execute_cell(source, snapshot, python, repeat=0):
    # macOS Seatbelt enforces no network, no writes beyond the disposable tree,
    # and no access to the user's home except the selected dependency runtime.
    runtime = Path(python).absolute().parent.parent
    with tempfile.TemporaryDirectory(prefix='swing-test-') as temporary:
        temp = Path(temporary).resolve()
        repository = temp / 'repository'
        shutil.copytree(snapshot, repository, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        before = tree_hashes(repository)
        (temp / 'test_generated.py').write_text(source)
        bootstrap = ('import runpy,sys\n'
                     f'sys.path[:0] = [{str(repository)!r}, {str(repository / "src")!r}]\n'
                     f'runpy.run_path({str(temp / "test_generated.py")!r}, run_name="__main__")\n')
        (temp / 'bootstrap.py').write_text(bootstrap)
        profile = ('(version 1)\n(allow default)\n(deny network*)\n(deny file-write*)\n'
                   f'(allow file-write* (subpath {json.dumps(str(temp))}))\n'
                   f'(deny file-read* (subpath {json.dumps(str(Path.home()))}))\n'
                   '(allow file-read-metadata)\n'
                   f'(allow file-read* (subpath {json.dumps(str(runtime))}))\n')
        (temp / 'sandbox.sb').write_text(profile)
        env = {'PATH': '/usr/bin:/bin', 'HOME': str(temp), 'TMPDIR': str(temp),
               'PYTHONNOUSERSITE': '1', 'PYTHONDONTWRITEBYTECODE': '1', 'LC_ALL': 'en_US.UTF-8'}
        try:
            result = subprocess.run(['/usr/bin/sandbox-exec', '-f', str(temp / 'sandbox.sb'),
                                     str(python), '-B', str(temp / 'bootstrap.py')],
                                    cwd=temp, env=env, text=True, capture_output=True, timeout=20)
            record = dict(returncode=result.returncode, stdout=result.stdout[-6000:], stderr=result.stderr[-6000:], timeout=False)
        except subprocess.TimeoutExpired:
            record = dict(returncode=None, stdout='', stderr='', timeout=True)
        record['source_mutation'] = before != tree_hashes(repository)
        record['repeat'] = repeat
        return record


def classify_cells(cells):
    if any(r['timeout'] or r['source_mutation'] for results in cells.values() for r in results):
        return 'invalid_execution'
    if any(len({r['returncode'] for r in results}) != 1 for results in cells.values()):
        return 'unstable_execution'
    passed = {name: results[0]['returncode'] == 0 for name, results in cells.items()}
    if all(passed.values()):
        return 'valid_nondiscriminating'
    if all(passed[name] for name in ('safe_base', 'unsafe_base', 'safe_head')) and not passed['unsafe_head']:
        return 'differential_candidate_needs_manual_adjudication'
    if not passed['safe_head']:
        return 'rejects_safe_or_environment_error'
    return 'invalid_base_or_other_pattern'


def run_models(output):
    output = Path(output)
    freeze = json.loads((output / 'freeze.json').read_text())
    for name in ('packets.json', 'private_pairing.json', 'retrieval_audit.json', 'schema.json', 'plan.md'):
        if sha((output / name).read_bytes()) != freeze[name]:
            raise ValueError('Frozen input changed: ' + name)
    rows = json.loads((output / 'packets.json').read_text())['records']
    if len(rows) != 16 or any(sha(r['prompt']) != r['prompt_sha256'] for r in rows):
        raise ValueError('Frozen request matrix mismatch')
    model_output = output / 'model'
    model_output.mkdir(exist_ok=False)
    random.Random(20260928).shuffle(rows)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda row: call_packet(row, model_output, 'gpt-5.6-sol', SCHEMA), rows))
    write_new(output / 'model_completion.json', {'attempted': len(results), 'ok': sum(r['status'] == 'ok' for r in results)})


def evaluate(output, python):
    output = Path(output)
    private = json.loads((output / 'private_pairing.json').read_text())
    results = []
    for path in sorted((output / 'model').glob('swing_*.json')):
        if path.name.endswith('.trace.json'):
            continue
        record = json.loads(path.read_text())
        label = private[record['task_id']]
        result = {k: record[k] for k in ('run_id', 'task_id', 'arm', 'status', 'source_tokens')}
        result.update(family=label['family'], variant=label['variant'])
        if record['status'] != 'ok':
            result['classification'] = 'generation_error'
        else:
            response = record['response']
            result.update(verdict=response['verdict'], test_intent=response['test_intent'],
                          safety_errors=validate_test(response['test_source']))
            if result['safety_errors']:
                result['classification'] = 'rejected_by_static_gate'
            else:
                result['cells'] = {cell: [execute_cell(response['test_source'], Path(label['source_run']) / 'snapshots' / cell, python, rep)
                                         for rep in range(2)] for cell in CELLS}
                result['classification'] = classify_cells(result['cells'])
        results.append(result)
    write_new(output / 'execution_results.json', results)
    return {arm: {classification: sum(r['arm'] == arm and r['classification'] == classification for r in results)
                  for classification in sorted({r['classification'] for r in results})} for arm in ARMS}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['prepare', 'run', 'evaluate'])
    parser.add_argument('--project-root', default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument('--output', required=True)
    parser.add_argument('--python', default=sys.executable)
    args = parser.parse_args()
    if args.mode == 'prepare':
        result = prepare(args.project_root, args.output)
    elif args.mode == 'run':
        result = run_models(args.output)
    else:
        result = evaluate(args.output, args.python)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
