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
checks = {
    'frozen_files_unchanged': all(hashlib.sha256((ex.ROOT / p).read_bytes()).hexdigest() == h for p, h in freeze['files'].items()),
    'source_caps': all(p[a + '_audit']['state']['tokens'] <= 1600 for p in packets for a in ['O', 'C']),
    'observation_caps': all(ex.token_count(p[a + '_observations']) <= 400 for p in packets for a in ['O', 'C']),
    'observation_identity': all(p['C_observations'] == p['O_observations'] for p in packets),
    'old_O_input_identity': all(ex.prompt(p, 'O') == ex.prompt(read(ex.PRIOR / 'packets' / f'J04_{p["world"]}.json'), 'O') for p in packets),
    'native_routes_identity': all(p['C_audit']['extra_navigation'] == p['O_audit']['extra_navigation'] for p in packets),
    'whole_bundle_admitted': all(p['C_audit']['bundle_admission']['admitted'] for p in packets),
    'required_assignment_and_registration_visible': all(all(text in p['C'] for text in ['name = marker.name', 'faclist.append(fixture_def)', 'faclist.insert(i, fixture_def)', 'available.append(', 'fixturedef.argname', 'argname=name', 'cls.__pytest_class_setup = class_fixture']) for p in packets),
    'workspace_hashes': all(ex.digest((ex.STATE / 'workspaces' / f'J04_{w}' / path).read_text()) == h for w in ['a', 'b'] for path, h in read(ex.STATE / 'workspace_manifests' / f'J04_{w}.json')['source_sha256'].items()),
    'all_inputs_identical_to_frozen_prompts': all(read(R / 'inputs' / f'J04_{p["world"]}_{a}_r{i}.json')['prompt'] == ex.prompt(p, a) for p in packets for a in ['O', 'C'] for i in [1, 2, 3]),
    'all_rows_reviewed': set(x['run_id'] for x in audit['rows']) == set(read(R / 'reviewed_ids.json')),
    'logical_count': len(audit['rows']) == 12,
    'physical_count': sum(x['physical_calls'] for x in audit['operational'].values()) == 12,
    'transport_schema_valid': all(x['status'] == 'ok' and not x['errors'] for x in audit['rows']),
    'tools_zero': all(x['tools'] == 0 for x in audit['operational'].values()),
    'labels_identical': read(R / 'labels.json')['J04'] == read(ex.STATE / 'labels.json')['J04'],
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
out = {'checks': checks, 'issue_locations': locations, 'localization_accuracy_measured': False, 'new_native_queries': 0, 'new_runtime_witnesses': 0}
(R / 'validation_results.json').write_text(json.dumps(out, indent=2) + '\n')
print(json.dumps(checks, indent=2))
assert checks['all_checks_pass']
