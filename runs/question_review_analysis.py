"""Explicit author annotations and accounting for the frozen 60-call pilot.

Not an independent judge. T = target mechanism; W = wrong predicate-raises
mechanism; M = target mechanism plus an unsupported raises alternative;
C = candidate includes the target among hypothetical alternatives; O = other.
Safe labels describe false alarms, never true positives.
"""
import json
import sys
from pathlib import Path

import jsonschema

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ace_review.context_necessity import assert_frozen, digest  # noqa: E402
from ace_review.counterfactual import write_new  # noqa: E402
from ace_review.obligation import token_count  # noqa: E402
from ace_review.question_context import ARMS  # noqa: E402

RUN = ROOT / 'runs/question_review1'
# Explicitly read all 12 proposals and 48 final reviews. Each string labels
# every response item in order; empty strings explicitly mean no items.
PROPOSAL = {
    0: ('WO', 'WOO', 'W'), 1: ('', 'OW', 'W'),
    2: ('C', 'CO', 'CO'), 3: ('C', 'C', 'C'),
}
FINAL = {
    0: {'diff_recheck': ('T', 'T', ''), 'reserve': ('W', 'W', 'W'),
        'claim_search': ('', '', ''), 'question_search': ('', 'M', 'T')},
    1: {'diff_recheck': ('W', '', ''), 'reserve': ('', '', 'W'),
        'claim_search': ('', 'W', 'W'), 'question_search': ('T', '', 'W')},
    2: {'diff_recheck': ('T', '', ''), 'reserve': ('', '', ''),
        'claim_search': ('', '', ''), 'question_search': ('', '', '')},
    3: {'diff_recheck': ('', '', ''), 'reserve': ('T', 'T', 'T'),
        'claim_search': ('T', 'T', 'T'), 'question_search': ('', '', '')},
}


def validate_codes(codes, items, stage):
    allowed = set('CWO') if stage == 'proposal' else set('TWMO')
    if len(codes) != len(items) or not set(codes) <= allowed:
        raise ValueError('Every item needs one valid author annotation')


def identity(row):
    return tuple(row[k] for k in ('task_id', 'condition', 'repeat', 'stage'))


def source_blocks(row):
    return [c for c in row['contexts'] if c['id'] != 'HYPOTHESES']


def analyze(root=RUN):
    assert_frozen(root)
    assert_frozen(root / 'final_inputs')
    base = [json.loads(line) for line in (root / 'base_requests.jsonl').read_text().splitlines()]
    predictions = [json.loads(line) for line in (root / 'predictions.jsonl').read_text().splitlines()]
    keyed = {identity(p): p for p in predictions}
    if len(predictions) != 60 or len(keyed) != 60:
        raise ValueError('Expected 60 unique predictions')
    annotations = []
    for index, row in enumerate(base):
        for repeat in (1, 2, 3):
            for stage, arm in [('proposal', 'questions')] + [('verify', a) for a in ARMS]:
                stem = (f'proposal_{index:02}_r{repeat}' if stage == 'proposal'
                        else f'verify_{index:02}_{arm}_r{repeat}')
                path = root / 'raw' / (stem + '.jsonl')
                raw = json.loads(path.read_text())
                expected = (row['task_id'], arm, repeat, stage)
                if identity(raw | {'stage': stage}) != expected or keyed.pop(expected) != raw | {'stage': stage}:
                    raise ValueError('Raw/aggregate/expected identity mismatch')
                schema = (root / 'proposal.schema.json' if stage == 'proposal' else
                          root / 'final_inputs' / (stem.removeprefix('verify_') + '.schema.json'))
                jsonschema.validate(raw['response'], json.loads(schema.read_text()))
                if raw['status'] != 'ok' or raw['returncode'] != 0 or raw['tool_isolation']['tool_call_count'] != 0:
                    raise ValueError('Failed or non-isolated generation')
                items = raw['response']['hypotheses' if stage == 'proposal' else 'issues']
                codes = PROPOSAL[index][repeat - 1] if stage == 'proposal' else FINAL[index][arm][repeat - 1]
                validate_codes(codes, items, stage)
                annotations.append({'task_id': row['task_id'], 'index': index, 'condition': arm,
                                    'repeat': repeat, 'stage': stage, 'codes': codes,
                                    'target_indices': [i for i, c in enumerate(codes) if c in 'TM'],
                                    'candidate_target_indices': [i for i, c in enumerate(codes) if c == 'C'],
                                    'wrong_mechanism_indices': [i for i, c in enumerate(codes) if c == 'W'],
                                    'mixed_mechanism_indices': [i for i, c in enumerate(codes) if c == 'M'],
                                    'target_path_indices': list(range(len(items))),
                                    'raw_file': str(path.relative_to(root)), 'raw_sha256': digest(path),
                                    'reviewer': 'session author; not independent adjudication'})
    if keyed:
        raise ValueError('Unexpected predictions')
    # All final nonempty items concern the scoped target path. Proposals may
    # mention other issues on that path; candidate coverage is not a finding.
    results = []
    for safe, unsafe, family in [(0, 1, 'pager_stream'), (2, 3, 'backend_options')]:
        for arm in ARMS:
            s, u = FINAL[safe][arm], FINAL[unsafe][arm]
            candidate = [bool('C' in PROPOSAL[unsafe][r]) for r in range(3)]
            results.append({'family': family, 'arm': arm, 'repeats': 3,
                            'unsafe_exact_hits': sum('T' in c for c in u),
                            'safe_target_mechanism_alarms': sum(bool(set(c) & set('TM')) for c in s),
                            'safe_target_path_alarms': sum(bool(c) for c in s),
                            'safe_any_issue_outputs': sum(bool(c) for c in s),
                            'paired_exact_and_no_safe_path_alarm': sum('T' in b and not a for a, b in zip(s, u)),
                            'wrong_mechanism_items_all_variants': sum(c.count('W') for c in s + u),
                            'mixed_mechanism_items_all_variants': sum(c.count('M') for c in s + u),
                            'unsafe_candidate_covered_repeats': sum(candidate),
                            'unsafe_exact_hits_with_candidate': sum('T' in u[r] and candidate[r] for r in range(3)),
                            'unsafe_exact_hits_without_candidate': sum('T' in u[r] and not candidate[r] for r in range(3))})
    audits = json.loads((root / 'final_inputs/source_audit.json').read_text())
    endpoints = {0: ('::_tempfilepager::', '::echo_via_pager::'),
                 1: ('::_tempfilepager::', '::echo_via_pager::'),
                 2: ('::TrioBackend.run::',), 3: ('::TrioBackend.run::',)}
    retrieval = []
    for a in audits:
        retrieval.append({k: a[k] for k in ('index', 'repeat', 'arm', 'source_tokens')} | {
            'candidate_endpoint_presence': {e: any(e in k for k in a['candidate_keys']) for e in endpoints[a['index']]},
            'selected_endpoint_presence': {e: any(e in k for k in a['selection_order']) for e in endpoints[a['index']]},
            'proxy_coverage': a['proxy_coverage'], 'first_greedy_step': a['greedy_schedule'][:1]})
    manifest = json.loads((root / 'final_inputs/manifest.json').read_text())
    for row in base:
        for repeat in (1, 2, 3):
            hashes = [m['shared_proposal_sha256'] for m in manifest if m['task_id'] == row['task_id'] and m['repeat'] == repeat]
            if len(hashes) != 4 or len(set(hashes)) != 1:
                raise ValueError('Four arms must share one proposal')
    paired_inputs = []
    for safe, unsafe in [(0, 1), (2, 3)]:
        for arm in ARMS:
            for repeat in (1, 2, 3):
                a, b = [json.loads((root / f'final_inputs/{i:02}_{arm}_r{repeat}.jsonl').read_text()) for i in (safe, unsafe)]
                paired_inputs.append({'safe_index': safe, 'arm': arm, 'repeat': repeat,
                                      'same_source_blocks': source_blocks(a) == source_blocks(b),
                                      'same_proposal': a['contexts'][-1] == b['contexts'][-1]})
    costs = []
    for stage, arm in [('proposal', 'questions')] + [('verify', a) for a in ARMS]:
        group = [p for p in predictions if p['stage'] == stage and p['condition'] == arm]
        costs.append({'stage': stage, 'arm': arm, 'calls': len(group),
                      'input_tokens_driver_estimate': sum(p['input_tokens_estimate'] for p in group),
                      'output_json_o200k_tokens': sum(token_count(json.dumps(p['response'], ensure_ascii=False)) for p in group),
                      'summed_call_seconds': sum(p['duration_seconds'] for p in group)})
    report = {'calls': 60, 'proposal_hypotheses': sum(len(a['codes']) for a in annotations if a['stage'] == 'proposal'),
              'final_issue_items': sum(len(a['codes']) for a in annotations if a['stage'] == 'verify'),
              'results': results, 'retrieval': retrieval, 'paired_inputs': paired_inputs, 'costs': costs,
              'limitations': ['Two previously observed development families, not 12 independent PRs',
                              'Author labels, no independent adjudication',
                              'Token cap, not exact token matching; no total-latency causal estimate',
                              'Candidate coverage/retention is family-level, not exact hypothesis identity',
                              'Safe and unsafe share a diff but independently generated hypotheses differ'],
              'annotations': annotations}
    return report


if __name__ == '__main__':
    result = analyze()
    write_new(RUN / 'annotations.json', result.pop('annotations'))
    write_new(RUN / 'analysis.json', result)
    files = [RUN / 'predictions.jsonl', RUN / 'completion.json', RUN / 'annotations.json', RUN / 'analysis.json',
             Path(__file__)] + sorted((RUN / 'raw').glob('*.jsonl'))
    write_new(RUN / 'analysis_integrity.json', {'files': {str(p.relative_to(ROOT)): digest(p) for p in files}})
    print(json.dumps({k: result[k] for k in ('calls', 'proposal_hypotheses', 'final_issue_items', 'results', 'costs')}, indent=2))
