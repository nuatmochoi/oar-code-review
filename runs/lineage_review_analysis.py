"""Complete raw-response accounting and explicit development-pair gates."""
import json
from pathlib import Path
import sys

import jsonschema

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ace_review.context_necessity import assert_frozen, digest  # noqa: E402
from ace_review.counterfactual import write_new  # noqa: E402
from ace_review.obligation import token_count  # noqa: E402

RUN = ROOT / 'runs/lineage_review1'
ARMS = ('isolated_predicate', 'production_types', 'production_lineage')


def key(row):
    return row['index'], row['condition'], row['repeat']


def summarize(annotations):
    amap = {key(r): r for r in annotations}
    expected = {(i, a, r) for i in (0, 1) for a in ARMS for r in (1, 2, 3)}
    if len(annotations) != 18 or set(amap) != expected:
        raise ValueError('Expected complete unique 2x3x3 accounting')
    for a in annotations:
        if not set(a['codes']) <= set('TWMO'):
            raise ValueError('Unknown mechanism code')
        indices = a['path_indices']
        if len(indices) != len(set(indices)) or any(type(i) is not int or not 0 <= i < len(a['codes']) for i in indices):
            raise ValueError('Invalid path indices')
    results = []
    for arm in ARMS:
        safe, unsafe = ([amap[i, arm, r] for r in (1, 2, 3)] for i in (0, 1))
        results.append({'arm': arm, 'unsafe_exact_hits': sum('T' in a['codes'] for a in unsafe),
            'safe_path_alarms': sum(bool(a['path_indices']) for a in safe),
            'wrong_mechanism_items': sum(a['codes'].count('W') for a in safe + unsafe),
            'mixed_mechanism_items': sum(a['codes'].count('M') for a in safe + unsafe),
            'paired_exact_and_no_safe_path_alarm': sum('T' in u['codes'] and not s['path_indices'] for s, u in zip(safe, unsafe))})
    def improves(candidate, reference):
        return candidate['unsafe_exact_hits'] >= reference['unsafe_exact_hits'] and candidate['safe_path_alarms'] < reference['safe_path_alarms']
    return {'results': results, 'production_package_gate': improves(results[2], results[0]),
            'explicit_identity_gate': improves(results[2], results[1])}


def analyze(root=RUN):
    assert_frozen(root)
    annotations = json.loads((root / 'annotations.json').read_text())
    summary = summarize(annotations)
    predictions = list(map(json.loads, (root / 'predictions.jsonl').read_text().splitlines()))
    manifest = json.loads((root / 'manifest.json').read_text())
    amap, pmap, mmap = ({key(r): r for r in rows} for rows in (annotations, predictions, manifest))
    if len(predictions) != 18 or len(manifest) != 18 or not amap.keys() == pmap.keys() == mmap.keys():
        raise ValueError('Incomplete or duplicate results')
    for k, prediction in pmap.items():
        item = mmap[k]
        raw = json.loads((root / 'raw' / (item['stem'] + '.jsonl')).read_text())
        if raw | {'index': prediction['index'], 'stage': 'verify'} != prediction or prediction['task_id'] != item['task_id']:
            raise ValueError('Raw/manifest identity mismatch')
        jsonschema.validate(prediction['response'], json.loads((root / 'inputs' / (item['stem'] + '.schema.json')).read_text()))
        if prediction['status'] != 'ok' or prediction['tool_isolation']['tool_call_count']:
            raise ValueError('Failed or non-isolated generation')
        if len(amap[k]['codes']) != len(prediction['response']['issues']):
            raise ValueError('One annotation per issue required')
    for index in (0, 1):
        for repeat in (1, 2, 3):
            group = [mmap[index, a, repeat] for a in ARMS]
            for field in ('source_sha256', 'proposal_sha256', 'prompt_reference_tokens'):
                if len({r[field] for r in group}) != 1:
                    raise ValueError('Matching constraint violated')
    costs = []
    for arm in ARMS:
        group = [p for p in predictions if p['condition'] == arm]
        costs.append({'arm': arm, 'prompt_reference_tokens': sum(m['prompt_reference_tokens'] for m in manifest if m['condition'] == arm),
            'output_json_reference_tokens': sum(token_count(json.dumps(p['response'], ensure_ascii=False)) for p in group),
            'summed_call_seconds': sum(p['duration_seconds'] for p in group)})
    return summary | {'calls': 18, 'issues': sum(len(p['response']['issues']) for p in predictions),
                      'costs': costs, 'adjudication': 'Session author, not independent or blinded',
                      'cohort': 'One observed development family, repeats are not independent PRs'}


if __name__ == '__main__':
    result = analyze()
    write_new(RUN / 'analysis.json', result)
    write_new(RUN / 'result_integrity.json', {'files': {str(p.relative_to(ROOT)): digest(p) for p in
        [RUN / 'predictions.jsonl', RUN / 'annotations.json', RUN / 'analysis.json', Path(__file__)] + sorted((RUN / 'raw').glob('*.jsonl'))}})
    print(json.dumps(result, indent=2))
