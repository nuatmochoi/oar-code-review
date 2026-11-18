"""Finalize selector-ablation scores, factor contrasts, and retrieval diagnostics."""
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ace_review.context_necessity import assert_frozen, digest  # noqa: E402
from ace_review.context_results import items, key, summarize  # noqa: E402
from ace_review.counterfactual import write_new  # noqa: E402

RUN = ROOT / 'runs/selector_ablation2'
ENDPOINT_PATTERNS = {
    'pager_stream': ('_tempfilepager', 'echo_via_pager'),
    'backend_options': ('TrioBackend.run',),
}


def factor_contrasts(metrics):
    result = {}
    for metric in ('target_hits_unsafe', 'target_false_claims_safe', 'paired_correct'):
        value = {arm: metrics[arm][metric] for arm in metrics}
        dedup_off = value['selector_dedup'] - value['selector_current']
        dedup_on = value['selector_both'] - value['selector_reserve']
        reserve_off = value['selector_reserve'] - value['selector_current']
        reserve_on = value['selector_both'] - value['selector_dedup']
        result[metric] = {
            'dedup_effect_without_reserve': dedup_off,
            'dedup_effect_with_reserve': dedup_on,
            'dedup_main_effect_mean': (dedup_off + dedup_on) / 2,
            'reserve_effect_without_dedup': reserve_off,
            'reserve_effect_with_dedup': reserve_on,
            'reserve_main_effect_mean': (reserve_off + reserve_on) / 2,
            'interaction': (value['selector_both'] - value['selector_dedup']) - reserve_off,
        }
    return result


def main():
    assert_frozen(RUN)
    records = [json.loads(line) for line in (RUN / 'predictions.jsonl').read_text().splitlines()]
    inputs = [json.loads(line) for line in (RUN / 'requests.jsonl').read_text().splitlines()]
    annotations = json.loads((RUN / 'annotations.json').read_text())
    mapping = json.loads((RUN / 'private_labels.json').read_text())
    audits = [entry for repeat in (1, 2, 3)
              for entry in json.loads((RUN / f'hypothesis_audit_r{repeat}.json').read_text())]
    analysis = summarize(records, annotations, mapping, inputs, audits)
    analysis['scope'] = ('Two previously observed development families (Click and AnyIO), each with one '
                         'constructed safe/unsafe pair. This is a selector ablation, not a fresh holdout; '
                         'three generations per cell are stochastic repeats, not independent PR samples.')
    analysis['factor_contrasts'] = {
        family: factor_contrasts(metrics)
        for family, metrics in analysis['families'].items()
    }

    predictions = {key(row): row for row in records}
    author = {key(row): row for row in annotations}
    categories = defaultdict(lambda: {'exact_target_items': 0, 'wrong_mechanism_items': 0,
                                      'other_items': 0, 'responses': 0})
    for record_key, annotation in author.items():
        if record_key[3] != 'verify':
            continue
        response_items = items(predictions[record_key])
        target = set(annotation['target_indices'])
        wrong = set(annotation['wrong_mechanism_indices'])
        family = mapping[record_key[0]]['family']
        bucket = categories[(family, record_key[1])]
        bucket['responses'] += 1
        bucket['exact_target_items'] += len(target)
        bucket['wrong_mechanism_items'] += len(wrong)
        bucket['other_items'] += len(response_items) - len(target | wrong)
    analysis['final_item_categories'] = {
        family: {arm: categories[family, arm] for arm in sorted({r['condition'] for r in inputs})}
        for family in sorted(ENDPOINT_PATTERNS)
    }

    source_audits = json.loads((RUN / 'source_audit.json').read_text())
    current_keys = {entry['task_id']: entry['selected_keys'] for entry in source_audits
                    if entry['arm'] == 'selector_current'}
    diagnostics = []
    for entry in source_audits:
        family = mapping[entry['task_id']]['family']
        endpoints = []
        for pattern in ENDPOINT_PATTERNS[family]:
            keys = [candidate for candidate in entry['base_ranked_keys'] if pattern in candidate]
            endpoints.append({
                'pattern': pattern,
                'candidate_keys': keys,
                'candidate_present': bool(keys),
                'selected': any(candidate in entry['selected_keys'] for candidate in keys),
                'reserved': any(candidate in entry['reserved_selected_keys'] for candidate in keys),
            })
        diagnostics.append({
            'task_id': entry['task_id'],
            'family': family,
            'variant': mapping[entry['task_id']]['variant'],
            'condition': entry['arm'],
            'source_tokens': entry['source_tokens'],
            'selected_chunks': len(entry['selected_keys']),
            'diff_visible_seeds_removed': len(entry['seed_dedup_excluded_keys']),
            'reserved_chunks': len(entry['reserved_selected_keys']),
            'selection_changed_from_current': entry['selected_keys'] != current_keys[entry['task_id']],
            'all_gold_endpoints_selected': all(endpoint['selected'] for endpoint in endpoints),
            'endpoints': endpoints,
        })
    retrieval = {
        'evaluator_only': True,
        'computed_after_input_freeze': True,
        'rows': diagnostics,
        'summary': {
            family: {
                arm: {
                    'variants_with_all_endpoints_selected': sum(
                        row['all_gold_endpoints_selected'] for row in diagnostics
                        if row['family'] == family and row['condition'] == arm),
                    'variants_with_changed_selection': sum(
                        row['selection_changed_from_current'] for row in diagnostics
                        if row['family'] == family and row['condition'] == arm),
                }
                for arm in sorted({row['condition'] for row in diagnostics})
            }
            for family in sorted(ENDPOINT_PATTERNS)
        },
    }
    write_new(RUN / 'retrieval_diagnostics.json', retrieval)
    write_new(RUN / 'analysis.json', analysis)
    write_new(RUN / 'final_integrity.json', {
        'frozen_files_unchanged': True,
        'raw_sha256': {path.name: digest(path) for path in sorted((RUN / 'raw').glob('*.jsonl'))},
        'annotations_sha256': digest(RUN / 'annotations.json'),
        'predictions_sha256': digest(RUN / 'predictions.jsonl'),
        'retrieval_diagnostics_sha256': digest(RUN / 'retrieval_diagnostics.json'),
    })
    print(json.dumps({'families': analysis['families'],
                      'factor_contrasts': analysis['factor_contrasts'],
                      'final_item_categories': analysis['final_item_categories']}, indent=2))


if __name__ == '__main__':
    main()
