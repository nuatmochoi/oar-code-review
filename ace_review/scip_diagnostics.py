"""Small follow-up diagnostics, NOT independent Oracle or token-matched ablations."""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import subprocess
from pathlib import Path

from .io import load_tasks, read_jsonl, write_json
from .schema import estimate_tokens


def sources_only(row):
    blocks = [b for b in row['contexts'] if b['kind'] != 'scip_bundle_manifest']
    return row | {'condition': 'scip_bundles_sources_only', 'contexts': blocks,
                  'context_status': row.get('context_status', {}) | {
                      'context_tokens': sum(estimate_tokens(b['content']) for b in blocks),
                      'diagnostic': 'Identical selected source blocks/order; manifest removed. Shorter input, NOT token-matched.'}}


def provisional_requests(rows, annotations, candidates):
    baselines = {r['task_id']: r for r in rows if r['condition'] == 'diff'}
    by_gold = {(r['task_id'], r['gold_id']): r for r in candidates}
    selected, targets = {}, {}
    for a in annotations:
        if (a['task_id'] not in baselines or a.get('gold_validity') != 'valid'
                or not a.get('sufficient_to_verify_defect') or not a.get('selected_evidence_ids')):
            continue
        pool = {b['id']: b for b in by_gold.get((a['task_id'], a['gold_id']), {}).get('candidate_evidence', [])}
        for bid in a['selected_evidence_ids']:
            b = pool.get(bid)
            if not b or b.get('kind') not in ('lsp', 'bm25', 'testsource') or not b.get('start_line') or not b.get('end_line'):
                raise ValueError('Selected provisional evidence lacks verifiable source coordinates')
            selected.setdefault(a['task_id'], {})[bid] = b
        targets.setdefault(a['task_id'], []).append(a['gold_id'])
    output = []
    for tid, pool in sorted(selected.items()):
        blocks = []
        for i, b in enumerate(pool.values(), 1):
            blocks.append({k: b[k] for k in ('kind', 'content', 'file', 'start_line', 'end_line')} | {
                'id': f'CTX-{i:02d}', 'token_cost': estimate_tokens(b['content']),
                'metadata': {'original_id': b['id'], 'version': 'head'}})
        output.append(baselines[tid] | {'condition': 'provisional_evidence', 'contexts': blocks,
                      'context_status': {'context_tokens': sum(b['token_cost'] for b in blocks),
                                         'diagnostic': 'Old AI-assisted source selection; NOT independent/minimal Oracle'}})
    return output, targets


def prepare(run_dir, output):
    run_dir, output = Path(run_dir), Path(output)
    if output.exists():
        raise FileExistsError(f'Preserve existing diagnostics: {output}')
    requests = list(read_jsonl(run_dir/'requests.jsonl'))
    annotations = Path('runs/aacr_go_pure_17_oracle_repo_level_annotations_provisional.jsonl')
    candidates = Path('runs/aacr_go_pure_17_oracle_repo_level_candidates.jsonl')
    injected, targets = provisional_requests(requests, list(read_jsonl(annotations)), list(read_jsonl(candidates)))
    tasks = {t.id: t for t in load_tasks(run_dir/'manifest.jsonl')}
    verification = []
    for row in injected:
        task = tasks[row['task_id']]
        for b in row['contexts']:
            source = subprocess.check_output(['git', 'show', f'{task.head_commit}:{b["file"]}'],
                                            cwd=Path('data/repos')/task.repo.replace('/', '__'), text=True)
            exact = '\n'.join(source.splitlines()[b['start_line']-1:b['end_line']]).strip()
            if exact != b['content'].strip():
                raise ValueError(f'Provisional source mismatch: {task.id} {b["id"]}')
            verification.append(dict(task_id=task.id, source_id=b['id'], file=b['file'],
                                     start_line=b['start_line'], end_line=b['end_line'],
                                     commit=task.head_commit, verified=True))
    flat = [sources_only(r) for r in requests if r['condition'] == 'scip_bundles']
    rows = flat + injected
    random.Random(20260903).shuffle(rows)
    output.mkdir(parents=True)
    with (output/'requests.jsonl').open('x') as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False)+'\n')
    write_json(output/'config.json', dict(
        stage='exploratory_mechanism_diagnostics', source_run=str(run_dir),
        requests=len(rows), sources_only_tasks=len(flat), provisional_evidence_tasks=len(injected),
        provisional_target_gold_ids_for_evaluator_only=targets,
        prior_annotation_sha256=hashlib.sha256(annotations.read_bytes()).hexdigest(),
        requests_sha256=hashlib.sha256((output/'requests.jsonl').read_bytes()).hexdigest(),
        code_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        review_outcomes_read_to_select_cases=False,
        independent_oracle=False, token_matched=False,
        source_verification=verification,
        limitations=['Previously observed AI-assisted annotation, not new human annotation',
                     'Manifest omission changes both relational metadata and prompt length',
                     'No placebo/removal proof of evidence-specific benefit',
                     'Do not call these results minimal-context or a generalization result']))
    (output/'source.py').write_bytes(Path(__file__).read_bytes())
    print(json.dumps({'requests':len(rows), 'sources_only':len(flat), 'provisional_evidence':len(injected), 'targets':targets}))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run-dir', required=True)
    p.add_argument('--output', required=True)
    args = p.parse_args()
    prepare(args.run_dir, args.output)


if __name__ == '__main__':
    main()
