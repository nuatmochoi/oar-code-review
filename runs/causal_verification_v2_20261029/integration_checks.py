"""Actual frozen-source integration with deterministic fake responses; no model.

48 directions verify all four initial inputs. Eight full reviews exercise both
datasets' real source/LSP plumbing. Fixture files never enter performance results.
"""
import json
import os
from concurrent.futures import ProcessPoolExecutor, as_completed

import experiment_harness as h
import causal_adapter as ca
import frontier_builder as fb
import oar_fixture as fx

VERIFY = h.HERE / 'verification' / os.environ.get('CAUSAL_INTEGRATION_TAG', '')


def direction(key):
    record = h.read(h.HERE / 'prepared/inputs' / f'{key}.json')
    values, errors = [], []
    with h.case_loader():
        product = h.ad.load_product()
        for arm in h.ARMS:
            inputs = fb.build_inputs(key, 'proactive', record['body_packet'], product)
            client = fx.FixtureClient(out_dir=VERIFY / 'initial_fixtures',
                review_id=f'{key}_{arm}', script=lambda *_: '{invalid synthetic stop')
            result = ca.run_review(key, arm, client, prepared_packet=record['body_packet'],
                prepared_inputs=inputs, expected_inputs=record['authoritative'], product=product)
            input_path = client.dir / 'initial.input.json'
            if not input_path.exists():
                errors.append({'arm': arm, 'error': result.get('error')})
                continue
            actual = h.read(input_path)
            audit = result['delivery_audit']
            values.append({'arm': arm, 'input_sha256': actual['composed_sha256'],
                           'delivery': audit['summary']})
            if result['model_calls'] != 1 or result['runtime_tool_queries'] != 0:
                errors.append({'arm': arm, 'error': 'unexpected post-format-error work'})
            if audit['errors'] or not audit['summary']['initial_prepared_complete']:
                errors.append({'arm': arm, 'error': 'initial delivery incomplete', 'details': audit['errors']})
            if result['source_exposure']['union_reference_tokens'] > 5200:
                errors.append({'arm': arm, 'error': 'source cap exceeded'})
    if len(values) != 4 or len({v['input_sha256'] for v in values}) != 1:
        errors.append({'error': 'four initial prompts are not identical'})
    return {'key': key, 'values': values, 'errors': errors, 'ok': not errors}


def smoke(key):
    record = h.read(h.HERE / 'prepared/inputs' / f'{key}.json')
    values = []
    with h.case_loader():
        product = h.ad.load_product()
        for arm in h.ARMS:
            inputs = fb.build_inputs(key, 'proactive', record['body_packet'], product)
            client = fx.FixtureClient(out_dir=VERIFY / 'full_fixtures',
                review_id=f'{key}_{arm}', script=lambda *_: fx.empty_response(semantic_probe=None))
            result = ca.run_review(key, arm, client, prepared_packet=record['body_packet'],
                prepared_inputs=inputs, expected_inputs=record['authoritative'], product=product)
            h.write(client.dir / 'review.fixture.json', {**result, 'fixture_not_a_model': True})
            values.append({'key': key, 'arm': arm, 'status': result['status'],
                'calls': result['model_calls'], 'queries': result['queries']['total'],
                'source_tokens': result['source_exposure']['union_reference_tokens'],
                'delivery_errors': result['delivery_audit']['errors'],
                'error': result.get('error'),
                'ok': result['status'] == 'completed' and result['model_calls'] == 3
                    and result['queries']['total'] <= 6 and not result['delivery_audit']['errors']})
    return values


def run():
    keys = sorted({b['key'] for b in h.blocks()})
    rows = []
    with ProcessPoolExecutor(max_workers=4) as pool:
        for future in as_completed([pool.submit(direction, key) for key in keys]):
            row = future.result()
            rows.append(row)
            print(json.dumps({'key': row['key'], 'ok': row['ok']}), flush=True)
    full = [*smoke('N01_a'), *smoke('H01_a')]
    report = {'model_calls': 0, 'initial_directions': len(rows), 'initial_checks': rows,
              'full_smoke': full, 'passed': all(r['ok'] for r in rows + full)}
    h.write(VERIFY / 'integration.json', report)
    print(json.dumps({'passed': report['passed'], 'initial_directions': len(rows), 'full_smokes': len(full)}))
    if not report['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    run()
