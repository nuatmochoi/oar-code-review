"""Real native smoke/coverage validation on an already-seen fixture PR; no model calls."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shared
from ace_review.context_adaptive import Corpus, token_count

R = Path(__file__).resolve().parent
sp = importlib.util.spec_from_file_location('automatic_route_policy', R / 'policy.py')
policy = importlib.util.module_from_spec(sp); sp.loader.exec_module(policy)
prior = shared.ROOT / 'runs/lsp_fixture_binding_v2_20261002'
p = shared.read(prior / 'packets/J04_a.json')
head = {f: (shared.ROOT / p['workspace_reference'] / f).read_text() for f in shared.read(prior / 'workspace_manifests/J04_a.json')['source_sha256']}
base = shared.full_sources(shared.ROOT / 'runs/lsp_state_holdout_20261002/checkouts' / p['instance_id'] / 'original')
diff = p['common'].split('[DIFF]\n', 1)[1]
c = Corpus({'base': base, 'head': head}, {v: {'nodes': {}, 'edges': []} for v in ['base', 'head']}, diff)
resolver = shared.native_policy.ref.old.Bindings(c, head)
client = shared.native_policy.Client(shared.ROOT / p['workspace_reference'])
try:
    native = shared.native_policy.ref.LSPBackend(resolver, client)
    source, observations, audit = policy.packet(head, diff, p['common'].split('[DIFF]', 1)[0], resolver, native, client, shared.native_policy.b.edited_lines)
finally: client.close()
shared.save(R / 'development_trace.json', {'trace': client.trace, 'initialization': client.initialization, 'stderr': client.errors})
shared.save(R / 'development_packet.json', {'source': source, 'observations': observations, 'audit': audit, 'already_seen_case': 'J04_a', 'model_calls': 0})
defs, parents = policy.index(head)
checks = {'source_cap': token_count(source) <= 2600, 'observation_cap': token_count(observations) <= 400, 'query_cap': len(audit['jumps']) <= 12, 'native_errors_zero': all(not j['error'] for j in audit['jumps']), 'whole_admitted_function_packages_and_owner_headers': all(set(lines).issubset(audit['state']['source_lines'][file]) for a in audit['admissions'] for file, lines in policy.package(head, defs, parents, tuple(a['target'])).items()), 'no_case_specific_route_table': 'ROUTES' not in (R / 'policy.py').read_text(), 'no_model_calls': True}
checks['all_checks_pass'] = all(checks.values())
shared.save(R / 'development_validation.json', checks)
assert checks['all_checks_pass'], checks
deps = [R / 'policy.py', R / 'shared.py', R / 'develop.py', shared.ROOT / 'runs/lsp_fixture_binding_v2_20261002/result_seal.json']
shared.save(R / 'algorithm_seal.json', {'policy_sha256': hashlib.sha256((R / 'policy.py').read_bytes()).hexdigest(), 'files': {str(p.relative_to(shared.ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in deps}, 'new_PR_selection_has_not_started': True, 'source_cap': 2600, 'query_cap_each_arm': 12, 'depth_cap': 3, 'development_model_calls': 0})
print(json.dumps({'checks': checks, 'source_tokens': token_count(source), 'queries': len(audit['jumps']), 'admitted': len(audit['admissions']), 'blocked': len(audit['blocked']), 'complete_runtime_path_certified': False}, indent=2))
