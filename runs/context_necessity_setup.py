"""Audit and freeze this bounded comparison before its first model call."""
import ast
import hashlib
import json
import subprocess
import urllib.request
from pathlib import Path

from ace_review.context_necessity import prepare
from ace_review.counterfactual import write_new
from ace_review.protocol_hypothesis import mask_source

PROJECT = Path(__file__).resolve().parents[1]


def normalized(source):
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                first.value.value = ''
    return ast.dump(tree, include_attributes=False)


def main():
    cases = PROJECT / 'runs/context_necessity_cases1'
    cases.mkdir(exist_ok=False)
    rows, labels, manifest, audited, full = [], {}, [], [], {}
    for name in ('context_pager_case1', 'context_backend_case3'):
        source = PROJECT / 'runs' / name
        execution = json.loads((source / 'execution.json').read_text())
        assert execution['gate']['eligible']
        these_labels = json.loads((source / 'private_labels.json').read_text())
        these_rows = [json.loads(line) for line in (source / 'requests.jsonl').read_text().splitlines()]
        for row in these_rows:
            variant = these_labels[row['task_id']]['variant']
            snapshot = source / 'snapshots' / (variant + '_head')
            for block in row['contexts']:
                path = snapshot / block['file']
                text = path.read_text()
                masked = mask_source(text)
                assert '\n'.join(masked.splitlines()[block['start_line'] - 1:block['end_line']]) == block['content']
                assert normalized(text) == normalized(masked)
                assert len(text.splitlines()) == len(masked.splitlines())
                full[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
                audited.append({'task_id': row['task_id'], 'condition': row['condition'], 'id': block['id'],
                                'source_file': str(path), 'span_sha256': hashlib.sha256(block['content'].encode()).hexdigest()})
        rows.extend(these_rows); labels.update(these_labels)
        manifest.append({'source_run': str(source), 'family': execution['family'],
                         'gate': execution['gate'], 'source_commits': execution['source_commits'],
                         'context_audit': json.loads((source / 'context_audit.json').read_text()),
                         'execution_sha256': hashlib.sha256((source / 'execution.json').read_bytes()).hexdigest()})
    write_new(cases / 'requests.jsonl', rows, jsonl=True)
    write_new(cases / 'private_labels.json', labels)
    write_new(cases / 'case_manifest.json', {'cases': manifest, 'generation_call_cap': 72,
        'assembly_attempts': [
            'context_backend_case1 passed runtime gate but its typed-attribute placebo was too short (358 vs 557/575 tokens). No model calls.',
            'context_backend_case2 used the longer file-I/O placebo and passed length matching; source audit then added exact import/backend_class bindings before inference. No model calls.',
            'context_backend_case3 is the frozen backend input. No target, reviewer, or scoring changes were based on model outputs.']})
    write_new(cases / 'source_audit.json', {'source_spans': len(audited), 'full_source_files': len(full),
        'source_sha256': full, 'spans': audited, 'errors': [],
        'mask_scope': 'Normalized docstring-value AST equality; not equivalence for __doc__-observing code.'})
    write_new(cases / 'plan.md', (PROJECT / 'runs/CONTEXT_NECESSITY_PLAN_KO.md').read_text(), raw=True)
    for name, module in [('frozen_cases.py', 'context_cases.py'), ('frozen_protocol.py', 'protocol_hypothesis.py'),
                         ('frozen_reviewer.py', 'reviewer.py')]:
        write_new(cases / name, (PROJECT / 'ace_review' / module).read_text(), raw=True)
    deps = subprocess.check_output([str(PROJECT / 'tools/venv-context-runtime/bin/python'), '-m', 'pip', 'freeze'], text=True)
    write_new(cases / 'dependencies.json', {'runtime': deps.splitlines(), 'schema_validator': 'jsonschema==4.25.1',
                                         'reference_tokenizer': 'tiktoken/o200k_base'})
    for repo, number in [('pallets/click', 3739), ('agronholm/anyio', 1161)]:
        url = f'https://api.github.com/repos/{repo}/pulls/{number}'
        data = json.load(urllib.request.urlopen(url))
        assert data['merged']
        write_new(cases / f'provenance_{repo.split("/")[-1]}_{number}.json', data)
    output = PROJECT / 'runs/context_necessity1'
    count = prepare(cases / 'requests.jsonl', output)
    audit = json.loads((output / 'input_audit.json').read_text())
    for family in ('pager_stream', 'backend_options'):
        ids = {i for i, meta in labels.items() if meta['family'] == family}
        for condition in ('diff', 'irrelevant'):
            hashes = [a['proposal_prompt_sha256'] for a in audit if a['task_id'] in ids and a['condition'] == condition]
            assert len(hashes) == 2 and len(set(hashes)) == 1
    print(json.dumps({'model_inputs': count, 'planned_calls': count * 6,
                      'source_spans_audited': len(audited), 'control_prompt_pairs_identical': 4}))


if __name__ == '__main__':
    main()
