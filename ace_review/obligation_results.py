"""Input provenance checks and exploratory outcomes for obligation_review15."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import subprocess
from collections import Counter
from functools import lru_cache
from pathlib import Path

from .evaluation import aggregate_labels
from .io import load_tasks, read_jsonl
from .obligation import ARMS, neutral_source, render_blocks, token_count, write_exclusive
from .scip_review_results import transitions
from .scip_screening import validate_labels
from .scip_verify import verify_source_block
from .statistics import paired_bootstrap


def audit_response(request, response, candidates):
    assessments = response.get('assessments', [])
    issues = response.get('issues', [])
    expected = {f'C{i+1}' for i in range(candidates)}
    ids = Counter(a['candidate_id'] for a in assessments)
    provided = {b['id'] for b in request['contexts']} - {'PLAN'}
    citations = [e for a in assessments for key in ('support_evidence_ids', 'counterevidence_ids')
                 for e in a.get(key, [])] + [e for i in issues for e in i.get('evidence_ids', [])]
    confirmed = {a['candidate_id'] for a in assessments if a['verdict'] == 'confirmed'}
    return dict(missing_assessments=sorted(expected - set(ids)),
                unknown_assessments=sorted(set(ids) - expected),
                duplicate_assessments=sorted(i for i, n in ids.items() if n > 1),
                invalid_citations=sorted(set(citations) - provided - {'DIFF'}),
                reported_without_confirmation=sorted({i.get('origin_id') for i in issues
                    if i.get('origin_id') != 'NEW' and i.get('origin_id') not in confirmed}),
                issues_citing_context=sum(bool(set(i.get('evidence_ids', [])) & provided) for i in issues),
                new_issues=sum(i.get('origin_id') == 'NEW' for i in issues))


def summarize_funnel(predictions):
    result = {}
    for condition in sorted({p['condition'] for p in predictions}):
        rows = [p for p in predictions if p['condition'] == condition]
        issues = [i for p in rows for i in p['response']['issues']]
        result[condition] = dict(tasks=len(rows), issues=len(issues),
            new_issues=sum(i.get('origin_id') == 'NEW' for i in issues),
            verdicts=dict(Counter(a['verdict'] for p in rows for a in p['response'].get('assessments', []))),
            mean_call_seconds=sum(p.get('duration_seconds', 0) for p in rows) / len(rows))
    return result


def verify_inputs(root, repo_cache='data/repos'):
    root = Path(root)
    requests = list(read_jsonl(root / 'requests.jsonl'))
    tasks = {t.id: t for t in load_tasks(root / 'manifest.jsonl')}
    audit = json.loads((root / 'selection_audit.json').read_text())
    by_key = {(a['task_id'], a['condition']): a for a in audit}
    config = json.loads((root / 'config.json').read_text())
    expected = {(t, c) for t in tasks for c in ARMS}
    keys = [(r['task_id'], r['condition']) for r in requests]
    errors = []
    if len(keys) != len(set(keys)) or set(keys) != expected:
        errors.append('unbalanced_requests')
    @lru_cache(maxsize=2048)
    def source(task_id, version, file):
        t = tasks[task_id]
        commit = t.head_commit if version == 'head' else (t.metadata.get('merge_base_commit') or t.base_commit)
        return subprocess.check_output(['git', 'show', f'{commit}:{file}'],
            cwd=Path(repo_cache) / t.repo.replace('/', '__'), text=True)
    @lru_cache(maxsize=16)
    def pool(task_id):
        with gzip.open(Path(config['corpus']) / (task_id + '.json.gz'), 'rt') as handle:
            return {b['id']: b for b in json.load(handle)['bundle_sources']}
    seen = set()
    blocks = spans = 0
    for request in requests:
        task_id, condition = request['task_id'], request['condition']
        a = by_key[(task_id, condition)]
        original = [pool(task_id)[key] for key in a['selected_source_ids']]
        actual = request['contexts'][1:]
        expected_blocks = [neutral_source(b, condition == 'verify_no_base') for b in original]
        if actual != expected_blocks:
            errors.append(f'{task_id}/{condition}:render_mismatch')
        cost = token_count(render_blocks(actual))
        if cost > config['budget'] or cost != a['rendered_tokens']:
            errors.append(f'{task_id}/{condition}:budget_mismatch')
        for b in original:
            key = (task_id, b['id'])
            if key in seen:
                continue
            seen.add(key)
            blocks += 1
            spans += len(b['metadata'].get('source_spans', [1]))
            errors += [f'{task_id}/{b["id"]}:{e}' for e in verify_source_block(
                b, lambda v, f: source(task_id, v, f))]
    for task_id in tasks:
        shared = [r for r in requests if r['task_id'] == task_id]
        for key in ('diff', 'pr', 'repository', 'language'):
            if any(r[key] != shared[0][key] for r in shared):
                errors.append(f'{task_id}:shared_input_mismatch:{key}')
        if any(r['contexts'][0] != shared[0]['contexts'][0] for r in shared):
            errors.append(f'{task_id}:proposal_not_shared')
    hashes = json.loads((root / 'source_hashes.json').read_text())
    hashes_ok = all(hashlib.sha256((root / 'source/ace_review' / n).read_bytes()).hexdigest() == h
                    for n, h in hashes.items())
    if not hashes_ok:
        errors.append('source_hash_mismatch')
    report = dict(passed=not errors, requests=len(requests), unique_source_blocks=blocks,
                  source_spans=spans, source_hashes_valid=hashes_ok, errors=errors)
    write_exclusive(root / 'input_verification.json', report)
    print(json.dumps(report))
    if errors:
        raise ValueError('Input verification failed; do not launch reviewers')
    return report


def analyze(root):
    root = Path(root)
    requests = list(read_jsonl(root / 'requests.jsonl'))
    proposal_requests = list(read_jsonl(root / 'proposal_requests.jsonl'))
    predictions = list(read_jsonl(root / 'predictions.jsonl'))
    proposals = list(read_jsonl(root / 'proposal_predictions.jsonl'))
    labels = list(read_jsonl(root / 'labels.jsonl'))
    proposal_labels = list(read_jsonl(root / 'proposal_labels.jsonl'))
    validate_labels(requests, labels)
    validate_labels(proposal_requests, proposal_labels)
    expected = {(r['task_id'], r['condition']) for r in requests + proposal_requests}
    all_predictions = predictions + proposals
    if len(all_predictions) != len(expected) or {(p['task_id'], p['condition']) for p in all_predictions} != expected:
        raise ValueError('Incomplete predictions')
    if any(p['status'] != 'ok' or p.get('tool_isolation', {}).get('tool_call_count') != 0 for p in all_predictions):
        raise ValueError('Reviewer failure or missing isolation audit')
    tasks = load_tasks(root / 'manifest.jsonl')
    all_labels = labels + proposal_labels
    metrics = aggregate_labels(tasks, all_labels, baseline='verify_diff')
    metrics.pop('selector_outcomes', None)
    proposal_map = {p['task_id']: p for p in proposals}
    request_map = {(r['task_id'], r['condition']): r for r in requests}
    prediction_map = {(p['task_id'], p['condition']): p for p in all_predictions}
    label_map = {(p['task_id'], p['condition']): p for p in all_labels}
    citation_audit = [dict(task_id=p['task_id'], condition=p['condition'], **audit_response(
        request_map[(p['task_id'], p['condition'])], p['response'],
        len(proposal_map[p['task_id']]['response']['issues']))) for p in predictions]
    write_exclusive(root / 'response_audit.json', citation_audit)
    write_exclusive(root / 'analysis.json', metrics)
    comparisons = {}
    for baseline in ('verify_diff', 'verify_claim_bm25', 'verify_obligation'):
        for mode in ('semantic_anywhere', 'strict'):
            result = paired_bootstrap(tasks, labels, baseline=baseline, requests=requests,
                samples=5000, seed=20260903, match_mode=mode)
            write_exclusive(root / f'bootstrap_{baseline}_{mode}.json', result)
            if mode == 'semantic_anywhere':
                comparisons[baseline] = result['analyses']['intention_to_treat']
    cases = []
    for task in tasks:
        for gold in task.gold_issues:
            case = dict(task_id=task.id, gold_id=gold.id, file=gold.file,
                gold_claim=gold.claim, context_level=gold.context_level, conditions={})
            for condition in ('proposal',) + ARMS:
                label = label_map[(task.id, condition)]
                matches = [m for m in label['semantic_matches'] if m['gold_id'] == gold.id]
                issues = prediction_map[(task.id, condition)]['response']['issues']
                case['conditions'][condition] = dict(detected=gold.id in label['semantic_anywhere_gold_ids'],
                    strict_detected=gold.id in label['matched_gold_ids'],
                    matched_issues=[issues[m['prediction_index']] for m in matches])
            cases.append(case)
    write_exclusive(root / 'gold_cases.json', cases)
    costs = {}
    selection_audit = json.loads((root / 'selection_audit.json').read_text())
    for c in ARMS:
        values = [r['rendered_tokens'] for r in selection_audit if r['condition'] == c]
        costs[c] = dict(min=min(values), mean=sum(values)/len(values), max=max(values),
            mean_full_prompt_reference_tokens=sum(r['rendered_prompt_reference_tokens']
                for r in selection_audit if r['condition'] == c) / len(values))
    summaries = dict(comparisons=comparisons, funnel=summarize_funnel(all_predictions), costs=costs,
        semantic_transitions={c: transitions(all_labels, c, 'verify_diff') for c in ARMS if c != 'verify_diff'},
        proposal_transitions={c: transitions(all_labels, c, 'proposal') for c in ARMS},
        strata={stratum: {c: dict(gold=sum(g['context_level'] == stratum for g in cases),
                     tp=sum(g['conditions'][c]['detected'] for g in cases if g['context_level'] == stratum))
                    for c in ('proposal',) + ARMS} for stratum in sorted({g['context_level'] for g in cases})},
        calls=dict(proposal=len(proposals), verify=len(predictions),
            judge=sum(l['judge_status'] != 'skipped' for l in all_labels),
            judge_skipped=sum(l['judge_status'] == 'skipped' for l in all_labels),
            reviewer_tool_calls=sum(p['tool_isolation']['tool_call_count'] for p in all_predictions)),
        limitations=['Previously inspected development cohort; one repeat, no independent holdout',
            'Consumer and guarantee slots are heuristic, not sound dependence or obligation extraction',
            'Budget cap matched, not token exact; no equal reasoning-compute guarantee',
            'No independent context-required labels; original questionable gold retained',
            'Unmatched predictions are not human-verified false positives',
            'Exploratory bootstrap without multiplicity correction'])
    write_exclusive(root / 'diagnostics.json', summaries)
    print(json.dumps(dict(metrics=metrics['semantic_anywhere_conditions'], calls=summaries['calls'])))
    return summaries


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['verify-inputs', 'analyze'])
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    if args.action == 'verify-inputs':
        verify_inputs(args.output)
    else:
        analyze(args.output)


if __name__ == '__main__':
    main()
