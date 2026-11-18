"""Complete accounting and fixed gates for the adaptive TypeScript-Go pair."""
import json
from pathlib import Path
import sys

import jsonschema

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ace_review.context_necessity import assert_frozen, digest  # noqa: E402
from ace_review.counterfactual import write_new  # noqa: E402
from ace_review.obligation import token_count  # noqa: E402

RUN = ROOT / 'runs/adaptive_tsgo_review1'
PROBE = ROOT / 'runs/adaptive_tsgo_probe1'
ARMS = ('diff_only', 'automatic_static', 'adaptive_probe', 'oracle_producer')


def key(row):
    return row['index'], row['condition'], row['repeat']


def summarize(annotations):
    amap = {key(row): row for row in annotations}
    expected = {(index, arm, repeat) for index in (0, 1) for arm in ARMS
                for repeat in (1, 2, 3)}
    if len(annotations) != 24 or set(amap) != expected:
        raise ValueError('Expected complete unique 2x4x3 accounting')
    for row in annotations:
        if not set(row['codes']) <= set('TWMO'):
            raise ValueError('Unknown mechanism code')
        indices = row['path_indices']
        if (len(indices) != len(set(indices)) or
                any(type(index) is not int or not 0 <= index < len(row['codes'])
                    for index in indices)):
            raise ValueError('Invalid path indices')
    results = []
    for arm in ARMS:
        safe = [amap[0, arm, repeat] for repeat in (1, 2, 3)]
        unsafe = [amap[1, arm, repeat] for repeat in (1, 2, 3)]
        results.append({'arm': arm,
            'unsafe_exact_hits': sum('T' in row['codes'] for row in unsafe),
            'safe_protocol_alarms': sum(bool(row['path_indices']) for row in safe),
            'wrong_mechanism_items': sum(row['codes'].count('W') for row in safe + unsafe),
            'other_items': sum(row['codes'].count('O') for row in safe + unsafe),
            'paired_exact_and_no_safe_alarm':
                sum('T' in unsafe_row['codes'] and not safe_row['path_indices']
                    for safe_row, unsafe_row in zip(safe, unsafe))})
    diff, static, adaptive, oracle = results
    static_gate = (static['unsafe_exact_hits'] > diff['unsafe_exact_hits'] and
                   static['safe_protocol_alarms'] <= diff['safe_protocol_alarms'])
    adaptive_gate = (adaptive['unsafe_exact_hits'] >= static['unsafe_exact_hits'] and
                     adaptive['safe_protocol_alarms'] <= static['safe_protocol_alarms'] and
                     adaptive['paired_exact_and_no_safe_alarm'] >
                     static['paired_exact_and_no_safe_alarm'])
    return {'results': results, 'static_review_gate': static_gate,
            'adaptive_review_gate': adaptive_gate,
            'automatic_vs_oracle_exact_hit_gap':
                static['unsafe_exact_hits'] - oracle['unsafe_exact_hits'],
            'adaptive_vs_static': {
                'unsafe_exact_hit_delta': adaptive['unsafe_exact_hits'] - static['unsafe_exact_hits'],
                'safe_protocol_alarm_delta': adaptive['safe_protocol_alarms'] - static['safe_protocol_alarms'],
                'paired_success_delta': adaptive['paired_exact_and_no_safe_alarm'] -
                                        static['paired_exact_and_no_safe_alarm']}}


def analyze(root=RUN):
    assert_frozen(root)
    assert_frozen(PROBE)
    annotations = json.loads((root / 'annotations.json').read_text())
    summary = summarize(annotations)
    predictions = list(map(json.loads, (root / 'predictions.jsonl').read_text().splitlines()))
    manifest = json.loads((root / 'manifest.json').read_text())
    amap, pmap, mmap = ({key(row): row for row in rows}
                        for rows in (annotations, predictions, manifest))
    if (len(predictions) != 24 or len(manifest) != 24 or
            not amap.keys() == pmap.keys() == mmap.keys()):
        raise ValueError('Incomplete or duplicate results')
    for identity, prediction in pmap.items():
        item = mmap[identity]
        raw = json.loads((root / 'raw' / (item['stem'] + '.jsonl')).read_text())
        if (raw | {'index': prediction['index'], 'stage': 'verify'} != prediction or
                prediction['task_id'] != item['task_id']):
            raise ValueError('Raw/manifest identity mismatch')
        schema = json.loads((root / 'inputs' / (item['stem'] + '.schema.json')).read_text())
        jsonschema.validate(prediction['response'], schema)
        if prediction['status'] != 'ok' or prediction['tool_isolation']['tool_call_count']:
            raise ValueError('Failed or non-isolated generation')
        if len(amap[identity]['codes']) != len(prediction['response']['issues']):
            raise ValueError('One annotation per issue required')
    if {row['prompt_reference_tokens'] for row in manifest} != {4096}:
        raise ValueError('Whole-prompt budget mismatch')
    if len({row['prompt_sha256'] for row in manifest if row['condition'] == 'diff_only'}) != 1:
        raise ValueError('Diff-only safe/unsafe prompts differ')
    completion = json.loads((PROBE / 'completion.json').read_text())
    if not completion['probe_discrimination_gate']:
        raise ValueError('Probe discrimination was not established')
    costs = []
    for arm in ARMS:
        group = [row for row in predictions if row['condition'] == arm]
        costs.append({'arm': arm, 'calls': len(group),
            'prompt_reference_tokens': sum(row['prompt_reference_tokens'] for row in manifest
                                           if row['condition'] == arm),
            'output_json_reference_tokens': sum(token_count(json.dumps(row['response'], ensure_ascii=False))
                                                for row in group),
            'summed_call_seconds': sum(row['duration_seconds'] for row in group)})
    return summary | {'automatic_retrieval_gate':
        completion['automatic_retrieval_matches_oracle'] == 2,
        'probe_discrimination_gate': completion['probe_discrimination_gate'],
        'calls': 24, 'issues': sum(len(row['response']['issues']) for row in predictions),
        'costs': costs, 'adjudication': 'Session author, not independent or blinded',
        'cohort': 'One author-known derived counterfactual family; repeats are not independent PRs'}


def finalize(root=RUN):
    result = analyze(root)
    write_new(root / 'analysis.json', result)
    files = ([root / 'predictions.jsonl', root / 'annotations.json',
              root / 'analysis.json', Path(__file__)] + sorted((root / 'raw').glob('*.jsonl')))
    write_new(root / 'result_integrity.json',
              {'files': {str(path.relative_to(ROOT)): digest(path) for path in files}})
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    finalize()
