"""Freeze a balanced, exploratory reviewer screening from preserved SCIP requests."""
from __future__ import annotations

import argparse
import hashlib
import json
import random
from pathlib import Path

from .io import load_tasks, read_jsonl, write_json, write_tasks
from .reviewer import render_prompt
from .schema import estimate_tokens


CONDITIONS = ('diff', 'bm25', 'lsp', 'scip_head', 'scip_modules_dedup',
              'scip_bundle_flat', 'scip_bundles')


def balanced_requests(records, rows, conditions=CONDITIONS, seed=20260903):
    included = sorted(r['task_id'] for r in records if r.get('scope_complete'))
    excluded = sorted(r['task_id'] for r in records if not r.get('scope_complete'))
    if not included:
        raise ValueError('No scope-complete tasks')
    selected = [r for r in rows if r['task_id'] in included and r['condition'] in conditions]
    keys = [(r['task_id'], r['condition']) for r in selected]
    expected = {(t, c) for t in included for c in conditions}
    if len(keys) != len(set(keys)) or set(keys) != expected:
        raise ValueError('Missing or duplicate task-condition cells')
    for task_id in included:
        task_rows = [r for r in selected if r['task_id'] == task_id]
        for key in ('repository', 'language', 'pr', 'diff', 'focus_diff'):
            if any(r.get(key) != task_rows[0].get(key) for r in task_rows):
                raise ValueError(f'Non-context input mismatch: {task_id} {key}')
    selected.sort(key=lambda r: (r['task_id'], r['condition']))
    random.Random(seed).shuffle(selected)
    return selected, included, excluded


def validate_labels(requests, labels):
    expected = {(r['task_id'], r['condition']) for r in requests}
    keys = [(r['task_id'], r['condition']) for r in labels]
    if len(keys) != len(set(keys)) or set(keys) != expected:
        raise ValueError('Incomplete or duplicate labels; do not analyze as a balanced run')
    if any(r.get('status') != 'ok' or r.get('judge_status') not in ('ok', 'skipped') for r in labels):
        raise ValueError('Reviewer or judge failures must be resolved before analysis')
    if len({r.get('repeat', 0) for r in labels}) != 1:
        raise ValueError('Screening requires one consistent repeat')


def prepare(source, manifest, output):
    source, output = Path(source), Path(output)
    if output.exists():
        raise FileExistsError(f'Preserve existing run: {output}')
    records = [json.loads(p.read_text()) for p in sorted((source / 'tasks').glob('*.json'))]
    rows, included, excluded = balanced_requests(records, list(read_jsonl(source / 'requests.jsonl')))
    tasks = [t for t in load_tasks(manifest) if t.id in included]
    if {t.id for t in tasks} != set(included):
        raise ValueError('Manifest does not cover all included tasks')
    output.mkdir(parents=True)
    write_tasks(output / 'manifest.jsonl', tasks)
    for filename, subset in [('requests.jsonl', rows), ('smoke_requests.jsonl', rows[:1]),
                             ('remaining_requests.jsonl', rows[1:])]:
        with (output / filename).open('x') as handle:
            for row in subset:
                handle.write(json.dumps(row, ensure_ascii=False) + '\n')
    frozen_files = ['reviewer.py', 'batch_judge.py', 'evaluation.py', 'statistics.py',
                    'scip_screening.py', 'judge.py', 'schema.py', 'io.py',
                    'prompts/reviewer.txt', 'schemas/review_output.schema.json',
                    'schemas/batch_semantic_match.schema.json']
    hashes = {}
    for name in frozen_files:
        payload = (Path(__file__).parent / name).read_bytes()
        target = output / 'source' / 'ace_review' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        hashes[name] = hashlib.sha256(payload).hexdigest()
    prompt = output / 'source/ace_review/prompts/reviewer.txt'
    prompt_audit = [{'task_id': r['task_id'], 'condition': r['condition'],
                     'prompt_sha256': hashlib.sha256(render_prompt(r, prompt).encode()).hexdigest(),
                     'rendered_prompt_tokens_estimate': estimate_tokens(render_prompt(r, prompt)),
                     'context_status': r.get('context_status', {})} for r in rows]
    config = dict(stage='exploratory_review_screening', source_run=str(source), manifest=str(manifest),
                  source_requests_sha256=hashlib.sha256((source / 'requests.jsonl').read_bytes()).hexdigest(),
                  included=included, excluded=excluded, tasks=len(tasks),
                  gold_issues=sum(len(t.gold_issues) for t in tasks), conditions=list(CONDITIONS),
                  requests=len(rows), repeats=1, order_seed=20260903,
                  reviewer='gpt-5.6-sol', judge='gpt-5.4', tools_disabled=True,
                  source_sha256=hashes, prior_operational_gates_unchanged=True,
                  gate_policy='Allow exploratory complete-case screening, not confirmatory promotion',
                  token_accounting='existing content UTF8 bytes/4 proxy; not token-exact or equal actual lengths',
                  context_required_stratum='not independently annotated; Repo Level only descriptive',
                  missing_ablation_controls=['paired_no_delta', 'same_source_flat_vs_linked', 'independent_oracle'],
                  primary='semantic gold recall; precision and unmatched predictions per PR guardrails',
                  secondary='strict path and +/-1 line F1; paired PR bootstrap descriptive only')
    write_json(output / 'config.json', config)
    write_json(output / 'prompt_audit.json', prompt_audit)
    print(json.dumps({k: config[k] for k in ('tasks', 'gold_issues', 'requests', 'conditions', 'excluded')}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', default='runs/delta_scip_v2_final17')
    parser.add_argument('--manifest', default='data/manifests/aacr_go_pure_17_hydrated.jsonl')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    prepare(args.source, args.manifest, args.output)


if __name__ == '__main__':
    main()
