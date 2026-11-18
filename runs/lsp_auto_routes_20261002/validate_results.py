import hashlib
import json
from pathlib import Path
import experiment as ex

R = Path(__file__).resolve().parent
read = ex.read
f = read(R / 'freeze.json')
audit = read(R / 'audit_all.json')
packets = [read(p) for p in sorted((R / 'packets').glob('*.json'))]
source_valid = True
packages_valid = True
for p in packets:
    labels = read(R / 'labels.json')[p['case_id']]
    direction = labels['directions'][p['world']]
    base, head = ('original', 'fixed') if direction == 'forward' else ('fixed', 'original')
    checkout = R / 'checkouts' / p['instance_id']
    versions = {'base': ex.shared.full_sources(checkout / base), 'head': ex.shared.full_sources(checkout / head)}
    diff = p['common'].split('[DIFF]\n', 1)[1]
    c = ex.Corpus(versions, {v: {'nodes': {}, 'edges': []} for v in versions}, diff)
    from ace_review.context_adaptive import EvidenceState
    state = EvidenceState(c)
    state.lines = {k: set(v) for k, v in p['B_audit']['state']['source_lines'].items()}
    state.paths = p['B_audit']['state']['paths']
    source_valid &= state.render() == p['B']
    source_valid &= ex.policy.render(versions['head'], {k: set(v) for k, v in p['A_audit']['state']['source_lines'].items()}) == p['A']
    defs, parents = ex.policy.index(versions['head'])
    for item in p['A_audit']['admissions']:
        package = ex.policy.package(versions['head'], defs, parents, tuple(item['target']))
        packages_valid &= all(set(lines).issubset(p['A_audit']['state']['source_lines'][file]) for file, lines in package.items())
checks = {
    'freeze_valid': all(hashlib.sha256((ex.ROOT / p).read_bytes()).hexdigest() == h for p, h in f['files'].items()),
    'algorithm_seal_valid': all(hashlib.sha256((ex.ROOT / p).read_bytes()).hexdigest() == h for p, h in read(R / 'algorithm_seal.json')['files'].items()),
    'source_caps': all(ex.token_count(p[a]) <= 2600 for p in packets for a in ['B', 'A']),
    'observation_caps': all(ex.token_count(p[a + '_observations']) <= 400 for p in packets for a in ['B', 'A']),
    'same_native_query_caps': all(len(p[a + '_audit']['jumps']) <= 12 for p in packets for a in ['B', 'A']),
    'native_errors_zero': all(not q['error'] for p in packets for a in ['B', 'A'] for q in p[a + '_audit']['jumps']),
    'automatic_whole_packages_and_owner_headers': packages_valid,
    'source_rendering_authentic': source_valid,
    'PR_ids_previously_unused': not set(x['instance_id'] for x in read(R / 'selected.json')) & set(read(R / 'previously_selected_ids.json')),
    'workspace_hashes': all(ex.digest((R / 'workspaces' / m.stem / file).read_text()) == h for m in (R / 'workspace_manifests').glob('*.json') for file, h in read(m)['source_sha256'].items()),
    'input_identity': all(read(R / 'inputs' / f'{p["case_id"]}_{p["world"]}_{a}_r{i}.json')['prompt'] == ex.prompt(p, a) for p in packets for a in ['B', 'A'] for i in [1, 2, 3]),
    'all_rows_reviewed': set(x['run_id'] for x in audit['rows']) == set(read(R / 'reviewed_ids.json')),
    'logical_count': len(audit['rows']) == 24,
    'physical_count': sum(x['physical_calls'] for x in audit['operational'].values()) == 24,
    'transport_schema_valid': all(x['status'] == 'ok' and not x['errors'] for x in audit['rows']),
    'tools_zero': all(x['tools'] == 0 for x in audit['operational'].values()),
    'witnesses_verified': read(R / 'witness_validation.json')['all_contracts_verified'],
}
locations = []
for row in audit['rows']:
    p = read(R / 'packets' / f'{row["case_id"]}_{row["world"]}.json')
    for issue in (row['review'] or {}).get('issues', []):
        file = ex.ROOT / p['workspace_reference'] / issue['file']
        locations.append({'run_id': row['run_id'], 'file': issue['file'], 'line': issue['line'], 'valid': file.is_file() and 1 <= issue['line'] <= len(file.read_text().splitlines())})
checks['issue_locations_exist'] = all(x['valid'] for x in locations)
checks['all_checks_pass'] = all(checks.values())
(R / 'validation_results.json').write_text(json.dumps({'checks': checks, 'issue_locations': locations, 'localization_accuracy_measured': False}, indent=2) + '\n')
print(json.dumps(checks, indent=2))
assert checks['all_checks_pass']
