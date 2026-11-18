"""Read-only source-prefix audit plus new-directory verification artifacts.

No labels, model clients, retrieval execution, repository execution, or historical
annotations are used. This independently checks the supplied record manifest
against original response JSON rather than trusting its delivery flags.
"""
from __future__ import annotations

import argparse
from collections import Counter
import copy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import re

from bootstrap import ROOT, PARENT
import cli_transport as transport
import oar_core as oc

STATE = re.compile(r'<causal_review_state>(.*?)</causal_review_state>', re.S)
EVIDENCE_KEYS = ('evidence_id', 'path', 'lines')
PROBE_FIELDS = ('status', 'id', 'claim_id', 'logical_queries', 'runtime_scope',
    'source_output_gate', 'validation_reasons', 'stdout', 'stderr', 'exception_type',
    'exception_message', 'expected_stdout_matches', 'matches_expected',
    'model_hypothesis_expected_stdout', 'model_hypothesis_output_matched',
    'interpretation', 'error')


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def digest(value):
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(text.encode()).hexdigest()


def leaves(value, pointer=''):
    if isinstance(value, dict) and value:
        for key, child in value.items():
            yield from leaves(child, pointer + '/' + str(key).replace('~', '~0').replace('/', '~1'))
    elif isinstance(value, list) and value:
        for i, child in enumerate(value):
            yield from leaves(child, pointer + '/' + str(i))
    else:
        yield pointer, value


def at(value, pointer):
    for key in pointer.split('/')[1:]:
        key = key.replace('~1', '/').replace('~0', '~')
        try:
            value = value[int(key)] if isinstance(value, list) else value[key]
        except (KeyError, IndexError, TypeError, ValueError):
            return False, None
    return True, value


def projection(obs):
    # Intentionally independent of the treatment module's projection function.
    result = {key: copy.deepcopy(obs[key]) for key in ('title', 'body', 'classification') if key in obs}
    if 'evidence' in obs:
        result['evidence'] = [{key: copy.deepcopy(ref[key]) for key in EVIDENCE_KEYS if key in ref}
                              for ref in obs['evidence']]
    return result


def old_adapter():
    spec = importlib.util.spec_from_file_location('prefix_audit_parent_adapter', PARENT / 'causal_adapter.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def unpack(text):
    matches = list(STATE.finditer(text))
    assert len(matches) == 1, 'expected one unambiguous tagged causal state'
    return matches[0], json.loads(matches[0].group(1))


def audit_built(built, sources, provenance):
    text, manifest = built['state_text'], built['manifest']
    _, state = unpack(text)
    assert oc.count_reference_tokens(text) == built['tokens'] == manifest['tokens'] <= 1600
    assert manifest['state_sha256'] == hashlib.sha256(text.encode()).hexdigest()
    assert state['blocked'] == manifest['blocked']
    assert manifest['required_complete'] == (not manifest['blocked'])
    record_rows = manifest['records']
    assert len({row['id'] for row in record_rows}) == len(record_rows)
    shown = {row['id']: row['value'] for row in state.get('records', [])}
    assert len(shown) == len(state.get('records', []))
    counts = Counter()
    covered = {name: set() for name in sources}
    for row in record_rows:
        origin, pointer = row['origin'], row['pointer']
        assert row['id'] == origin + ':' + pointer
        exists, original = at(sources[origin], pointer)
        assert exists, (row['id'], 'invented record pointer')
        assert row['content_sha256'] == digest(original)
        assert row['source_sha256'] == manifest['sources'][origin]['source_sha256']
        delivered = row['id'] in shown
        assert row['delivered'] == delivered
        if delivered:
            assert row['delivered_content_sha256'] == digest(shown[row['id']])
            for local, value in leaves(shown[row['id']]):
                assert at(original, local) == (True, value), (row['id'], local, 'altered or invented field')
        else:
            assert row['delivered_content_sha256'] is None
        expected_fields = []
        omitted = []
        for local, value in leaves(original):
            full = pointer + local
            covered[origin].add(full)
            visible = delivered and at(shown[row['id']], local) == (True, value)
            expected_fields.append((full, digest(value), visible))
            if not visible:
                omitted.append(local)
        observed_fields = [(f['pointer'], f['content_sha256'], f['delivered']) for f in row['fields']]
        assert observed_fields == expected_fields
        assert all(f['reason'] if not f['delivered'] else f['reason'] is None for f in row['fields'])
        assert row['omitted_fields'] == (omitted if delivered else ['*'])
        assert row['complete'] == (delivered and not omitted)
        if not row['complete']:
            assert row['reason']
        counts[f'{origin}_records_total'] += 1
        counts[f'{origin}_records_delivered'] += delivered
        counts[f'{origin}_records_complete'] += row['complete']
        counts[f'{origin}_fields_omitted'] += len(omitted)
    assert set(shown) == {r['id'] for r in record_rows if r['delivered']}
    if not manifest['blocked']:
        expected_omitted = [{"id": r['id'], "fields": r['omitted_fields']}
                            for r in record_rows if r['omitted_fields']]
        assert state['omitted'] == expected_omitted
        assert state['omitted_record_count'] == sum(not r['delivered'] for r in record_rows)
        assert state['omitted_field_count'] == sum(len(r['omitted_fields']) for r in record_rows)
        for i, obs in enumerate(sources['critic']['review']['observations']):
            actual = shown[f'critic:/review/observations/{i}']
            for key, value in projection(obs).items():
                if key == 'evidence':
                    assert [{k: v[k] for k in EVIDENCE_KEYS if k in v} for v in actual[key]] == value
                else:
                    assert actual[key] == value
            counts['critic_observation_core_delivered'] += 1
        assert shown['critic:/review/summary'] == sources['critic']['review']['summary']
        for i, probe in enumerate(sources['probes']):
            actual = shown[f'probes:/{i}']
            for key in PROBE_FIELDS:
                if key in probe:
                    assert actual[key] == probe[key]
    for origin, value in sources.items():
        assert manifest['sources'][origin]['payload_sha256'] == digest(value)
        assert manifest['sources'][origin]['provenance'] == provenance.get(origin, {})
    for origin in ('initial', 'critic'):
        assert state.get('origins', {}).get(origin, {}).get('source_sha256') == provenance[origin]['response_sha256'] or manifest['blocked']
    unrepresented = {name: [ptr for ptr, _ in leaves(value) if ptr not in covered[name]]
                     for name, value in sources.items()}
    return dict(counts=counts, unrepresented_source_fields=unrepresented,
                records=record_rows, shown=shown)


def provenance_for(directory, records):
    result = {}
    for origin, stage in (('initial', 'initial'), ('critic', 'update_1')):
        result[origin] = dict(stage=stage, response_sha256=oc._sha(records[stage]['raw_response']),
            input_path=str(directory / f'{stage}.input.json'), input_sha256=sha(directory / f'{stage}.input.json'),
            result_path=str(directory / f'{stage}.result.json'), result_sha256=sha(directory / f'{stage}.result.json'))
    result['probes'] = dict(result_path=str(directory / 'review.result.json'),
        result_sha256=sha(directory / 'review.result.json'), json_pointer='/causal_audit/probes')
    return result


def decoded_legacy(state, key):
    value = state['sections'][key]
    try:
        return 'valid_complete_json', json.loads(value)
    except (TypeError, ValueError):
        return 'partial_unverifiable_inner_json', None


def validate(*, prepared_required=True):
    import causal_adapter as adapter
    import critic_handoff
    parent_adapter = old_adapter()
    assert adapter.RESPONSE_SCHEMA == parent_adapter.RESPONSE_SCHEMA
    assert adapter.ROLES == parent_adapter.ROLES and adapter.INITIAL_POLICY == parent_adapter.INITIAL_POLICY
    assert adapter.PROBE_GUIDE == parent_adapter.PROBE_GUIDE
    tests = None
    if prepared_required:
        tests = read(ROOT / 'verification/critic_handoff_tests.json')
        assert tests['exit_code'] == 0 and tests['status'] == 'passed'
        assert tests['test_count'] >= 11
        for name, expected in tests['code_sha256'].items():
            assert sha(ROOT / name) == expected, ('tests stale', name)
    directories = sorted((PARENT / 'results/reviews').glob('*_debate'))
    assert len(directories) == 144
    totals = Counter()
    rows = []
    for directory in directories:
        directory = directory.resolve()
        group = directory.name.removesuffix('_debate')
        original = read(directory / 'update_2.input.json')
        match, prior = unpack(original['raw']['suffix'])
        records = {s: read(directory / f'{s}.result.json') for s in ('initial', 'update_1')}
        assert all(record['status'] == 'ok' for record in records.values())
        for stage, record in records.items():
            assert (directory / f'{stage}.raw.txt').read_text() == record['raw_response']
        review = read(directory / 'review.result.json')
        causal = review['causal_audit']
        runtime = {k: v for k, v in review['causal_policy']['probe_runtime'].items() if k != 'python_executable'}
        probes = adapter.probe_digest(causal['probes'])
        assert probes == parent_adapter.probe_digest(causal['probes'])
        sources = dict(initial=json.loads(records['initial']['raw_response']),
                       critic=json.loads(records['update_1']['raw_response']),
                       claims=causal['verification']['claims'], probes=probes, runtime=runtime)
        provenance = provenance_for(directory, records)
        built = critic_handoff.build_handoff_state(role=prior['role'], instruction=prior['instruction'],
            initial_payload=sources['initial'], critic_payload=sources['critic'], claims=sources['claims'],
            probe_results=probes, experimental_runtime=runtime, source_provenance=provenance, cap=1600)
        checked = audit_built(built, sources, provenance)
        assert not built['manifest']['blocked'], (group, 'critical handoff blocked: do not launch')
        new_raw = copy.deepcopy(original['raw'])
        new_raw['suffix'] = new_raw['suffix'][:match.start()] + built['state_text'] + new_raw['suffix'][match.end():]
        assert STATE.sub('<STATE>', new_raw['suffix']) == STATE.sub('<STATE>', original['raw']['suffix'])
        assert {k: v for k, v in new_raw.items() if k != 'suffix'} == {k: v for k, v in original['raw'].items() if k != 'suffix'}
        new_text, _ = transport.compose_input(original['order_name'], **new_raw)
        legacy_sections = {key: decoded_legacy(prior, key) for key in prior['sections']}
        old_probe_status, old_probes = legacy_sections['semantic_probe_results']
        probe_visibility = []
        for i, probe in enumerate(probes):
            delivered = checked['shown'][f'probes:/{i}']
            for field, value in leaves(probe):
                old_visible = (at(old_probes, f'/{i}' + field) == (True, value)) if old_probes is not None else None
                new_visible = at(delivered, field) == (True, value)
                row = dict(probe_index=i, pointer=field, value_sha256=digest(value),
                           legacy='exact' if old_visible else 'absent_or_changed' if old_visible is False else 'partial_unverifiable',
                           repaired='exact' if new_visible else 'explicitly_omitted')
                probe_visibility.append(row)
                totals[f"probe_field_{row['legacy']}_to_{row['repaired']}"] += 1
        zero_findings = not sources['critic'].get('findings')
        totals['reviews'] += 1
        totals['critic_observations'] += len(sources['critic']['review']['observations'])
        totals['zero_findings_reviews'] += zero_findings
        totals['zero_findings_with_observations'] += zero_findings and bool(sources['critic']['review']['observations'])
        totals['probe_reviews'] += bool(probes)
        totals['probe_legacy_partial_reviews'] += old_probe_status != 'valid_complete_json'
        totals['required_complete_reviews'] += built['manifest']['required_complete']
        totals.update(checked['counts'])
        for key, (status, _) in legacy_sections.items():
            totals[f'legacy_{key}_{status}'] += 1
        prepared_paths = []
        if prepared_required:
            for arm in ('current', 'legacy', 'repaired'):
                path = ROOT / 'prepared/inputs' / f'{group}_{arm}.json'
                prepared = read(path)
                src = PARENT / 'results/reviews' / f"{group}_{'current' if arm == 'current' else 'debate'}"
                saved = read(src / 'update_2.input.json')
                for ref in prepared['inherited_references'].values():
                    assert sha(ref['path']) == ref['sha256']
                expected_raw = new_raw if arm == 'repaired' else saved['raw']
                assert prepared['raw'] == expected_raw, (group, arm, 'actual final input differs')
                text, parts = transport.compose_input(prepared['order'], **prepared['raw'])
                assert prepared['composed_sha256'] == oc._sha(text)
                if arm != 'repaired':
                    assert text == saved['composed_text'] and parts == saved['parts']
                else:
                    assert prepared['handoff_manifest'] == built['manifest']
                    assert text == new_text
                inherited = read(src / 'review.result.json')
                assert prepared['inherited_source_exposure'] == inherited['source_exposure']
                assert prepared['inherited_queries'] == inherited['queries']
                assert prepared['provided_evidence_ids'] == inherited['stages'][-1]['provided_evidence_ids']
                assert prepared['new_final_judgments'] == 1
                prepared_paths.append({'path': str(path.relative_to(ROOT)), 'sha256': sha(path)})
                totals['prepared_inputs_checked'] += 1
        rows.append(dict(group_id=group, tokens=built['tokens'], required_tokens=built['manifest']['required_tokens'],
            state_sha256=built['manifest']['state_sha256'], zero_findings=zero_findings,
            source_provenance=provenance, records=checked['records'],
            unrepresented_source_fields=checked['unrepresented_source_fields'],
            legacy_sections={k: v[0] for k, v in legacy_sections.items()},
            probe_visibility=probe_visibility, prepared_inputs=prepared_paths))
    if prepared_required:
        assert len(list((ROOT / 'prepared/inputs').glob('*.json'))) == 432
        assert read(ROOT / 'prepared/response_schema.json') == parent_adapter.RESPONSE_SCHEMA
        jobs = read(ROOT / 'prepared/jobs.json')
        assert len(jobs['blocks']) == 144 and jobs['branches'] == 432
        assert {row['group_id'] for row in jobs['blocks']} == {row['group_id'] for row in rows}
    return dict(passed=True, created_at=datetime.now(timezone.utc).isoformat(),
        scope='144 saved debate prefixes; source immutable; no labels or model/retrieval calls',
        treatment='critic prose transport plus state allocation repair',
        prepared_required=prepared_required, totals=dict(totals),
        synthetic_tests=tests,
        max_state_tokens=max(row['tokens'] for row in rows),
        max_required_tokens=max(row['required_tokens'] for row in rows),
        rows=rows, code_sha256={name: sha(ROOT / name) for name in
            ('critic_handoff.py', 'causal_adapter.py', 'test_critic_handoff.py', 'validate_critic_handoff.py', 'handoff_experiment.py')})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prefix-only', action='store_true')
    args = parser.parse_args()
    result = validate(prepared_required=not args.prefix_only)
    target = ROOT / 'verification' / ('prefix_validation.json' if args.prefix_only else 'preflight.json')
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k != 'rows'}, ensure_ascii=False, indent=2))
