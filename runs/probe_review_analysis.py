"""Account for every frozen response and explicit author annotation."""
import json
import sys
from pathlib import Path

import jsonschema

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ace_review.context_necessity import assert_frozen, digest  # noqa: E402
from ace_review.counterfactual import write_new  # noqa: E402
from ace_review.obligation import token_count  # noqa: E402

RUN = ROOT / 'runs/probe_review1'
ARMS = ('source_only', 'environment_control', 'targeted_probe')


def analyze(root=RUN):
    assert_frozen(root)
    predictions = [json.loads(s) for s in (root / 'predictions.jsonl').read_text().splitlines()]
    annotations = json.loads((root / 'annotations.json').read_text())
    manifest = json.loads((root / 'manifest.json').read_text())
    def key(row):
        return row['index'], row['condition'], row['repeat']
    pmap, amap, mmap = ({key(r): r for r in rows} for rows in (predictions, annotations, manifest))
    if any(len(rows) != 36 for rows in (predictions, annotations, manifest)) or not len(pmap) == len(amap) == len(mmap) == 36 or not pmap.keys() == amap.keys() == mmap.keys():
        raise ValueError('Expected complete unique 4x3x3 accounting')
    for k, p in pmap.items():
        m, a = mmap[k], amap[k]
        raw = json.loads((root / 'raw' / (m['stem'] + '.jsonl')).read_text())
        if raw | {'index': p['index'], 'stage': 'verify'} != p or p['task_id'] != m['task_id']:
            raise ValueError('Raw/manifest identity mismatch')
        jsonschema.validate(p['response'], json.loads((root / 'inputs' / (m['stem'] + '.schema.json')).read_text()))
        if p['status'] != 'ok' or p['tool_isolation']['tool_call_count']:
            raise ValueError('Failed or non-isolated generation')
        codes, count = a['codes'], len(p['response']['issues'])
        if len(codes) != count or not set(codes) <= set('TWMO'):
            raise ValueError('One explicit mechanism code required per issue')
        if len(a['path_indices']) != len(set(a['path_indices'])) or any(type(i) is not int or not 0 <= i < count for i in a['path_indices']):
            raise ValueError('Invalid path annotation')
    results = []
    for family, safe, unsafe in [('pager_stream', 0, 1), ('backend_options', 2, 3)]:
        for arm in ARMS:
            s = [amap[safe, arm, r] for r in (1, 2, 3)]
            u = [amap[unsafe, arm, r] for r in (1, 2, 3)]
            results.append({'family': family, 'arm': arm, 'repeats': 3,
                            'unsafe_exact_hits': sum('T' in a['codes'] for a in u),
                            'safe_mechanism_alarms': sum(bool(set(a['codes']) & set('TM')) for a in s),
                            'safe_path_alarms': sum(bool(a['path_indices']) for a in s),
                            'safe_any_issue_outputs': sum(bool(a['codes']) for a in s),
                            'wrong_mechanism_items': sum(a['codes'].count('W') for a in s + u),
                            'mixed_mechanism_items': sum(a['codes'].count('M') for a in s + u),
                            'paired_exact_and_no_safe_path_alarm': sum('T' in b['codes'] and not a['path_indices'] for a, b in zip(s, u))})
    costs = []
    for arm in ARMS:
        group = [p for p in predictions if p['condition'] == arm]
        costs.append({'arm': arm, 'calls': len(group),
                      'prompt_reference_tokens': sum(m['prompt_reference_tokens'] for m in manifest if m['condition'] == arm),
                      'input_tokens_driver_estimate': sum(p['input_tokens_estimate'] for p in group),
                      'output_json_reference_tokens': sum(token_count(json.dumps(p['response'], ensure_ascii=False)) for p in group),
                      'summed_call_seconds': sum(p['duration_seconds'] for p in group)})
    return {'calls': 36, 'issues': sum(len(p['response']['issues']) for p in predictions), 'results': results,
            'costs': costs, 'adjudication': 'Session author; not independent or blinded',
            'cohort': 'Two observed development families; repeats are not independent PRs'}


if __name__ == '__main__':
    result = analyze()
    write_new(RUN / 'analysis.json', result)
    write_new(RUN / 'result_integrity.json', {'files': {str(p.relative_to(ROOT)): digest(p) for p in
              [RUN / 'predictions.jsonl', RUN / 'annotations.json', RUN / 'analysis.json', Path(__file__)] + sorted((RUN / 'raw').glob('*.jsonl'))}})
    print(json.dumps(result, indent=2))
