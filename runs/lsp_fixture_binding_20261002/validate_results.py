import difflib
import hashlib
import json
import sys
from pathlib import Path

R = Path(__file__).resolve().parent
sys.path.insert(0, str(R))
import experiment as ex

read = ex.read
freeze = read(R / 'freeze.json')
audit = read(R / 'audit_all.json')
packets = [read(p) for p in sorted((R / 'packets').glob('*.json'))]
filler_valid = True
source_lines_valid = True
statement_spans_valid = True
for p in packets:
    direction = read(ex.STATE / 'labels.json')['J04']['directions'][p['world']]
    base, head = ('original', 'fixed') if direction == 'forward' else ('fixed', 'original')
    checkout = ex.STATE / 'checkouts' / p['instance_id']
    all_src = {v: ex.full_sources(checkout / rev) for v, rev in [('base', base), ('head', head)]}
    versions = {v: {f: all_src[v][f] for f in p['source_sha256'][v]} for v in all_src}
    corpus = ex.Corpus(versions, {v: {'nodes': {}, 'edges': []} for v in versions}, '')
    for k in p['M_audit']['filler_ids']:
        unit = corpus.units[k]
        lines = set(p['M_audit']['state']['source_lines'][k])
        filler_valid &= lines == set(range(unit.start, unit.end + 1))
        filler_valid &= not bool(lines & set(p['M_audit']['forbidden_binding_ranges'].get(unit.file, [])))
    for arm in ['C', 'M']:
        state = ex.EvidenceState(corpus)
        state.lines = {k: set(v) for k, v in p[arm + '_audit']['state']['source_lines'].items()}
        state.paths = p[arm + '_audit']['state']['paths']
        source_lines_valid &= state.render() == p[arm]
    selected = {f: set(v) for f, v in p['D_audit']['state']['source_lines'].items()}
    source_lines_valid &= ex.policy.render(versions['head'], selected) == p['D']
    statement_spans_valid &= all(set(range(s['start'], s['end'] + 1)).issubset(selected[s['file']]) for s in p['D_audit']['statements'])
checks = {
    'frozen_files_unchanged': all(hashlib.sha256((ex.ROOT / p).read_bytes()).hexdigest() == h for p, h in freeze['files'].items()),
    'source_caps': all(ex.token_count(p[a]) <= 2600 for p in packets for a in ['C', 'M', 'D']),
    'observation_caps': all(ex.token_count(p[a + '_observations']) <= 400 for p in packets for a in ['C', 'M', 'D']),
    'observation_identity': all(p['C_observations'] == p['M_observations'] == p['D_observations'] for p in packets),
    'old_C_prompt_identity': all(ex.prompt(p, 'C') == ex.prompt(read(ex.PRIOR / 'packets' / f'J04_{p["world"]}.json'), 'C') for p in packets),
    'token_match_within_two_percent': all(abs(ex.token_count(p['M']) - ex.token_count(p['D'])) / ex.token_count(p['D']) <= .02 for p in packets),
    'M_preserves_C_lines': all(set(v).issubset(p['M_audit']['state']['source_lines'][k]) for p in packets for k, v in p['C_audit']['state']['source_lines'].items()),
    'M_filler_is_whole_units_outside_binding_declarations': filler_valid,
    'source_rendering_matches_audited_lines': source_lines_valid,
    'selected_statement_spans_complete': statement_spans_valid,
    'native_queries_24': sum(len(p['D_audit']['new_native_navigation']) for p in packets) == 24,
    'native_errors_zero': all(j['targets'] and not j['error'] for p in packets for j in p['D_audit']['new_native_navigation']),
    'native_decorator_alias_target_verified': all(('_pytest/fixtures.py', 'fixture') in [tuple(t) for t in p['D_audit']['new_native_navigation'][-1]['targets']] for p in packets),
    'required_bindings_visible': all(all(s in p['D'] for s in ['function._pytestfixturefunction = self', 'name=name', 'self.argname = argname', 'name = marker.name', 'faclist.append(fixture_def)', 'available.append(', 'from _pytest.fixtures import fixture', 'return fixture', 'parsefactories(self, unittest=True)']) for p in packets),
    'workspace_hashes': all(ex.digest((R / 'workspaces' / m.stem / path).read_text()) == h for m in (R / 'workspace_manifests').glob('*.json') for path, h in read(m)['source_sha256'].items()),
    'all_inputs_match_frozen_prompts': all(read(R / 'inputs' / f'J04_{p["world"]}_{a}_r{i}.json')['prompt'] == ex.prompt(p, a) for p in packets for a in ['C', 'M', 'D'] for i in [1, 2, 3]),
    'all_rows_reviewed': set(x['run_id'] for x in audit['rows']) == set(read(R / 'reviewed_ids.json')),
    'logical_count': len(audit['rows']) == 18,
    'physical_count': sum(x['physical_calls'] for x in audit['operational'].values()) == 18,
    'transport_schema_valid': all(x['status'] == 'ok' and not x['errors'] for x in audit['rows']),
    'tools_zero': all(x['tools'] == 0 for x in audit['operational'].values()),
    'labels_identical': read(R / 'labels.json') == read(ex.PRIOR / 'labels.json'),
    'prior_witnesses_verified': read(ex.STATE / 'witness_validation.json')['all_contracts_verified'],
}
locations = []
for row in audit['rows']:
    packet = read(R / 'packets' / f'J04_{row["world"]}.json')
    for issue in (row['review'] or {}).get('issues', []):
        file = ex.ROOT / packet['workspace_reference'] / issue['file']
        locations.append({'run_id': row['run_id'], 'file': issue['file'], 'line': issue['line'], 'valid': file.is_file() and 1 <= issue['line'] <= len(file.read_text().splitlines())})
checks['issue_locations_exist'] = all(x['valid'] for x in locations)
checks['all_checks_pass'] = all(checks.values())
out = {'checks': checks, 'issue_locations': locations, 'localization_accuracy_measured': False, 'new_native_queries': 24, 'new_runtime_witnesses': 0}
(R / 'validation_results.json').write_text(json.dumps(out, indent=2) + '\n')
print(json.dumps(checks, indent=2))
assert checks['all_checks_pass']
