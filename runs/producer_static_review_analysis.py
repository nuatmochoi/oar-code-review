"""Account for every producer-static review and evaluate fixed development gates."""
import json
from pathlib import Path
import sys

import jsonschema

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ace_review.context_necessity import assert_frozen, digest  # noqa: E402
from ace_review.counterfactual import write_new  # noqa: E402
from ace_review.obligation import token_count  # noqa: E402

RUN = ROOT / 'runs/producer_static_review1'
ARMS = ('retrieved_only', 'producer_static', 'production_runtime')


def key(row):
    return row['index'], row['condition'], row['repeat']


def summarize(annotations):
    amap = {key(row): row for row in annotations}
    expected = {(index, arm, repeat) for index in (0, 1) for arm in ARMS
                for repeat in (1, 2, 3)}
    if len(annotations) != 18 or set(amap) != expected:
        raise ValueError('Expected complete unique 2x3x3 accounting')
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
            'safe_path_alarms': sum(bool(row['path_indices']) for row in safe),
            'wrong_mechanism_items': sum(row['codes'].count('W') for row in safe + unsafe),
            'mixed_mechanism_items': sum(row['codes'].count('M') for row in safe + unsafe),
            'paired_exact_and_no_safe_path_alarm':
                sum('T' in unsafe_row['codes'] and not safe_row['path_indices']
                    for safe_row, unsafe_row in zip(safe, unsafe))})

    def improves(candidate, reference):
        return (candidate['unsafe_exact_hits'] >= reference['unsafe_exact_hits'] and
                candidate['safe_path_alarms'] < reference['safe_path_alarms'])

    return {'results': results,
        'static_producer_package_gate': improves(results[1], results[0]),
        'runtime_incremental_gate': improves(results[2], results[1]),
        'static_vs_retrieved': {
            'unsafe_exact_hit_delta': results[1]['unsafe_exact_hits'] - results[0]['unsafe_exact_hits'],
            'safe_path_alarm_delta': results[1]['safe_path_alarms'] - results[0]['safe_path_alarms'],
            'wrong_mechanism_item_delta': results[1]['wrong_mechanism_items'] - results[0]['wrong_mechanism_items']},
        'runtime_vs_static': {
            'unsafe_exact_hit_delta': results[2]['unsafe_exact_hits'] - results[1]['unsafe_exact_hits'],
            'safe_path_alarm_delta': results[2]['safe_path_alarms'] - results[1]['safe_path_alarms'],
            'wrong_mechanism_item_delta': results[2]['wrong_mechanism_items'] - results[1]['wrong_mechanism_items']}}


def analyze(root=RUN):
    assert_frozen(root)
    annotations = json.loads((root / 'annotations.json').read_text())
    summary = summarize(annotations)
    predictions = list(map(json.loads, (root / 'predictions.jsonl').read_text().splitlines()))
    manifest = json.loads((root / 'manifest.json').read_text())
    amap, pmap, mmap = ({key(row): row for row in rows}
                        for rows in (annotations, predictions, manifest))
    if (len(predictions) != 18 or len(manifest) != 18 or
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
    for index in (0, 1):
        for repeat in (1, 2, 3):
            group = [mmap[index, arm, repeat] for arm in ARMS]
            for field in ('source_sha256', 'proposal_sha256', 'prompt_reference_tokens'):
                if len({row[field] for row in group}) != 1:
                    raise ValueError('Matching constraint violated')
    costs = []
    for arm in ARMS:
        group = [row for row in predictions if row['condition'] == arm]
        costs.append({'arm': arm, 'calls': len(group),
            'prompt_reference_tokens': sum(row['prompt_reference_tokens'] for row in manifest
                                           if row['condition'] == arm),
            'output_json_reference_tokens': sum(token_count(json.dumps(row['response'], ensure_ascii=False))
                                                for row in group),
            'summed_call_seconds': sum(row['duration_seconds'] for row in group)})
    return summary | {'calls': 18,
        'issues': sum(len(row['response']['issues']) for row in predictions),
        'costs': costs, 'adjudication': 'Session author, not independent or blinded',
        'cohort': 'One observed development family; repeats are not independent PRs'}


def finalize(root=RUN):
    result = analyze()
    version = '_v2' if (root / 'analysis.json').exists() else ''
    analysis = root / f'analysis{version}.json'
    integrity = root / f'result_integrity{version}.json'
    write_new(analysis, result)
    files = ([root / 'predictions.jsonl', root / 'annotations.json',
              analysis, Path(__file__)] + sorted((root / 'raw').glob('*.jsonl')))
    write_new(integrity, {'files': {str(path.relative_to(ROOT)): digest(path) for path in files},
                          'supersedes': 'analysis.json' if version else None})
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    finalize()
