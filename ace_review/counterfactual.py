"""A real-PR-derived same-diff feasibility pilot; not an independent benchmark.

The builder deliberately combines historical revisions. Labels/provenance stay
outside model prompts. A four-cell executable gate precedes any model calls.
"""
from __future__ import annotations

import argparse
import ast
import difflib
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from .bm25 import BM25Index, Document
from .obligation import render_blocks, token_count
from .reviewer import render_prompt

ANCHOR = 'db575eeedcfdb03bf31285afd3033e301df8b685'
COMPANION_BASE = '7a13c041dbef42f9f3feb14110f02626f6892e9a'
COMPANION_HEAD = '3ff3ff21dd45957c9e143cd500291959bb15f690'
ARMS = ('diff', 'bm25', 'definition', 'oracle', 'irrelevant')
MODEL_FILE = 'requests/models.py'
EXCEPTION_FILE = 'requests/exceptions.py'


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def write_new(path, value, *, raw=False, jsonl=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        if raw:
            stream.write(value)
        elif jsonl:
            for row in value:
                stream.write(json.dumps(row, ensure_ascii=False) + '\n')
        else:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write('\n')


def class_node(source, name):
    matches = [n for n in ast.parse(source).body if isinstance(n, ast.ClassDef) and n.name == name]
    if len(matches) != 1:
        raise ValueError(f'Expected exactly one class {name}')
    return matches[0]


def replace_class(source, donor, name):
    old, new = class_node(source, name), class_node(donor, name)
    lines, donor_lines = source.splitlines(keepends=True), donor.splitlines(keepends=True)
    return ''.join(lines[:old.lineno - 1] + donor_lines[new.lineno - 1:new.end_lineno] + lines[old.end_lineno:])


def make_diff(base, head, path):
    return ''.join(difflib.unified_diff(base.splitlines(keepends=True), head.splitlines(keepends=True),
                                      fromfile='a/' + path, tofile='b/' + path))


def validate_gate(safe_diff, unsafe_diff, records):
    expected = {'safe_base': 'pass', 'unsafe_base': 'pass', 'safe_head': 'pass', 'unsafe_head': 'target_failure'}
    checks = {key: records.get(key, {}).get('status') == val for key, val in expected.items()}
    checks['same_nonempty_diff'] = bool(safe_diff) and safe_diff == unsafe_diff
    return {'eligible': all(checks.values()), 'checks': checks, 'diff_sha256': sha(safe_diff)}


def block(file, lines, start=1, end=None):
    end = len(lines) if end is None else end
    return {'id': 'CTX-' + sha(f'{file}:{start}:{end}')[:12], 'kind': 'repository_source',
            'file': file, 'start_line': start, 'end_line': end,
            'content': '\n'.join(lines[start - 1:end])}


def source_blocks(sources, excluded=None, chunk_lines=80):
    result = []
    for file, source in sorted(sources.items()):
        if file in (excluded or set()):
            continue
        lines = source.splitlines()
        for start in range(0, len(lines), chunk_lines):
            result.append(block(file, lines, start + 1, min(len(lines), start + chunk_lines)))
    return result


def definition_blocks(sources, diff):
    """Resolve changed raised constructors and local class bases (bounded AST baseline)."""
    names = list(dict.fromkeys(re.findall(r'^\+[^+].*?raise\s+([A-Za-z_]\w*)\s*\(', diff, re.M)))
    models = ast.parse(sources.get(MODEL_FILE, ''))
    aliases = {}
    for node in models.body:
        if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module:
            for alias in node.names:
                aliases[alias.asname or alias.name] = ('requests/' + node.module.replace('.', '/') + '.py', alias.name)
    queue = [aliases.get(name, (MODEL_FILE, name)) for name in names]
    result, seen, headers, unresolved, external = [], set(), set(), [], []
    while queue:
        file, name = queue.pop(0)
        if (file, name) in seen:
            continue
        seen.add((file, name))
        source = sources.get(file, '')
        tree = ast.parse(source)
        classes = {n.name: n for n in tree.body if isinstance(n, ast.ClassDef)}
        if name not in classes:
            unresolved.append(name)
            continue
        lines = source.splitlines()
        first_class = min(n.lineno for n in classes.values())
        if file not in headers and first_class > 1:
            result.append(block(file, lines, 1, first_class - 1))
            headers.add(file)
        node = classes[name]
        result.append(block(file, lines, node.lineno, node.end_lineno))
        for base in node.bases:
            if isinstance(base, ast.Name):
                if base.id in classes:
                    queue.append((file, base.id))
                else:
                    external.append(base.id)
    return result, {'unresolved': unresolved, 'external_bases_not_expanded': sorted(set(external))}


def select_context(sources, diff, arm, budget=2000):
    audit = {}
    if arm == 'diff':
        candidates = []
    elif arm == 'bm25':
        pool = source_blocks(sources, excluded={MODEL_FILE})
        docs = [Document(b['file'], b['start_line'], b['end_line'], b['content']) for b in pool]
        by_location = {(b['file'], b['start_line']): b for b in pool}
        candidates = [by_location[(d.file, d.start_line)] for _, d in BM25Index(docs).search(diff, top_k=len(docs))]
    elif arm == 'definition':
        candidates, audit = definition_blocks(sources, diff)
    elif arm in ('oracle', 'irrelevant'):
        file = EXCEPTION_FILE if arm == 'oracle' else 'requests/status_codes.py'
        candidates = [block(file, sources[file].splitlines())] if sources.get(file) else []
    else:
        raise ValueError(f'Unknown condition: {arm}')
    selected = []
    for candidate in candidates:
        if token_count(render_blocks(selected + [candidate])) <= budget:
            selected.append(candidate)
    audit.update(rendered_tokens=token_count(render_blocks(selected)), budget=budget,
                 selected=[{'id': b['id'], 'file': b['file'], 'start': b['start_line'],
                            'end': b['end_line'], 'sha256': sha(b['content'])} for b in selected])
    return selected, audit


def make_request(task_id, arm, diff, contexts, tokens):
    return {'task_id': task_id, 'condition': arm, 'repository': 'psf/requests',
            'language': 'Python', 'pr': {'title': '', 'description': ''}, 'diff': diff,
            'contexts': contexts, 'context_status': {'context_tokens': tokens}}


def check_witness(snapshot, witness, python=None):
    command = [str(python or sys.executable), '-B', str(Path(witness).resolve()), str(Path(snapshot).resolve())]
    result = subprocess.run(command, capture_output=True, text=True, timeout=60,
                            cwd=snapshot, env={'PATH': os.environ.get('PATH', ''), 'PYTHONNOUSERSITE': '1'})
    try:
        record = json.loads(result.stdout)
    except json.JSONDecodeError:
        record = {'status': 'environment_error'}
    allowed = (result.returncode == 0 and record.get('status') == 'pass') or (
        result.returncode == 10 and record.get('status') == 'target_failure')
    if not allowed:
        record['status'] = 'environment_error'
    return record | {'returncode': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr}


def analyze(predictions, labels, mapping):
    def key(row):
        return row['task_id'], row['condition'], row['repeat']
    indexed = {key(row): row for row in labels}
    if len(indexed) != len(labels):
        raise ValueError('Duplicate labels')
    if len({key(row) for row in predictions}) != len(predictions):
        raise ValueError('Duplicate predictions')
    conditions = {}
    for arm in sorted({p['condition'] for p in predictions}):
        rows = [p for p in predictions if p['condition'] == arm]
        valid = [p for p in rows if p['status'] == 'ok' and key(p) in indexed]
        true = false = pairs = correct = 0
        for p in valid:
            label = indexed[key(p)]['target_present']
            true += mapping[p['task_id']] == 'unsafe' and label
            false += mapping[p['task_id']] == 'safe' and label
        for repeat in sorted({p['repeat'] for p in rows}):
            cells = {mapping[p['task_id']]: indexed[key(p)]['target_present'] for p in valid if p['repeat'] == repeat}
            if set(cells) == {'safe', 'unsafe'}:
                pairs += 1
                correct += cells['unsafe'] and not cells['safe']
        conditions[arm] = {'requested': len(rows), 'valid_scored': len(valid), 'target_hits_unsafe': true,
                           'target_false_claims_safe': false, 'complete_pairs': pairs, 'paired_correct': correct}
    return {'independent_families': 1, 'conditions': conditions,
            'scope': 'Target-specific correctness; repeated calls are not independent defect samples.'}


def prepare(repo, output, budget=2000, runtime_python=None):
    root = Path(output).resolve()
    root.mkdir(parents=True, exist_ok=False)
    repo = Path(repo).resolve()
    def git(*args):
        return subprocess.check_output(['git', '--git-dir=' + str(repo), *args]).decode()
    files = git('ls-tree', '-r', '--name-only', ANCHOR).splitlines()
    if any(Path(f).name == 'AGENTS.md' for f in files):
        raise ValueError('Read repository AGENTS.md before preparing snapshots')
    sources = {f: git('show', f'{ANCHOR}:{f}') for f in files if f.startswith('requests/') and f.endswith('.py')}
    donor = {variant: git('show', commit + ':src/requests/exceptions.py')
             for variant, commit in [('safe', COMPANION_HEAD), ('unsafe', COMPANION_BASE)]}
    head_model = sources[MODEL_FILE]
    start = head_model.index('        try:\n            return complexjson.loads(self.text, **kwargs)')
    end = head_model.index('\n    @property', start)
    base_model = head_model[:start] + '        return complexjson.loads(self.text, **kwargs)\n' + head_model[end:]
    # Preserve the actual PR's introduced import bindings in the change so even
    # diff-only sees where the caught and newly raised exception types originate.
    binding = 'from .exceptions import JSONDecodeError as RequestsJSONDecodeError\n'
    if base_model.count(binding) != 1 or base_model.count('basestring, JSONDecodeError)') != 1:
        raise ValueError('Historical import anchors do not match')
    base_model = base_model.replace(binding, '').replace('basestring, JSONDecodeError)', 'basestring)')
    witness = Path(__file__).parent / 'fixtures' / 'requests_roundtrip.py'
    results, diffs, heads, hashes = {}, {}, {}, {}
    for variant in ('safe', 'unsafe'):
        package = sources | {EXCEPTION_FILE: replace_class(sources[EXCEPTION_FILE], donor[variant], 'JSONDecodeError')}
        heads[variant] = package
        for version, model in [('base', base_model), ('head', head_model)]:
            cell = variant + '_' + version
            snapshot = root / 'snapshots' / cell
            snapshot.mkdir(parents=True)
            contents = package | {MODEL_FILE: model}
            for file, text in contents.items():
                write_new(snapshot / file, text, raw=True)
            hashes[cell] = {file: sha(text) for file, text in contents.items()}
            results[cell] = check_witness(snapshot, witness, runtime_python)
        diffs[variant] = make_diff(base_model, head_model, MODEL_FILE)
    gate = validate_gate(diffs['safe'], diffs['unsafe'], results)
    # Check every file, not just the anchor passed to unified_diff.
    for variant in ('safe', 'unsafe'):
        changed = [f for f in hashes[variant + '_base'] if hashes[variant + '_base'][f] != hashes[variant + '_head'][f]]
        gate['checks'][variant + '_only_anchor_changed'] = changed == [MODEL_FILE]
    gate['eligible'] = all(gate['checks'].values())
    write_new(root / 'execution.json', {'gate': gate, 'cells': results, 'file_sha256': hashes,
              'builder_python': sys.version, 'runtime_python': str(runtime_python or sys.executable),
              'anchor_commit': ANCHOR, 'companion_base': COMPANION_BASE,
              'companion_head': COMPANION_HEAD, 'derived_counterfactual': True, 'independent_families': 1})
    write_new(root / 'anchor.diff', diffs['safe'], raw=True)
    write_new(root / 'witness.py', witness.read_text(), raw=True)
    for name in ('LICENSE', 'NOTICE'):
        write_new(root / name, git('show', ANCHOR + ':' + name), raw=True)
    if not gate['eligible']:
        return {'eligible': False, 'output': str(root)}
    prompt = Path(__file__).parent / 'prompts' / 'companion_review.txt'
    schema = Path(__file__).parent / 'schemas' / 'review_output.schema.json'
    write_new(root / 'prompt.txt', prompt.read_text(), raw=True)
    write_new(root / 'schema.json', schema.read_text(), raw=True)
    mapping = {sha('context-' + variant)[:16]: variant for variant in ('safe', 'unsafe')}
    requests, audit = [], []
    for arm in ARMS:
        for task_id, variant in mapping.items():
            blocks, selection = select_context(heads[variant], diffs[variant], arm, budget)
            row = make_request(task_id, arm, diffs[variant], blocks, selection['rendered_tokens'])
            rendered = render_prompt(row, root / 'prompt.txt')
            requests.append(row)
            audit.append({'task_id': task_id, 'condition': arm, 'variant': variant, **selection,
                          'prompt_sha256': sha(rendered), 'prompt_reference_tokens': token_count(rendered)})
    diff_hashes = {a['prompt_sha256'] for a in audit if a['condition'] == 'diff'}
    if len(diff_hashes) != 1:
        raise AssertionError('Diff-only inputs are not identical')
    write_new(root / 'requests.jsonl', requests, jsonl=True)
    write_new(root / 'selection_audit.json', audit)
    write_new(root / 'private_labels.json', {'mapping': mapping,
              'target': 'JSON error pickle round-trip fails because IOError reduce loses doc/pos constructor arguments.',
              'companion_location': EXCEPTION_FILE, 'anchor_location': MODEL_FILE})
    write_new(root / 'frozen_builder.py', Path(__file__).read_text(), raw=True)
    return {'eligible': True, 'requests': len(requests), 'output': str(root)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--repo', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--budget', type=int, default=2000)
    parser.add_argument('--runtime-python')
    args = parser.parse_args()
    print(json.dumps(prepare(args.repo, args.output, args.budget, args.runtime_python), indent=2))


if __name__ == '__main__':
    main()
