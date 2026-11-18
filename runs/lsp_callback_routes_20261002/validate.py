import json,hashlib,sys
from pathlib import Path
import experiment as ex
R=Path(__file__).resolve().parent;read=ex.read
f=read(R/'freeze.json');ps=[read(p) for p in sorted((R/'packets').glob('*.json'))]
checks={'freeze_valid':all(hashlib.sha256((ex.ROOT/p).read_bytes()).hexdigest()==h for p,h in f['files'].items()),'algorithm_seal_valid':all(hashlib.sha256((ex.ROOT/p).read_bytes()).hexdigest()==h for p,h in read(R/'algorithm_seal.json')['files'].items()),'four_packets':len(ps)==4,'source_caps':all(ex.token_count(p[a])<=2600 for p in ps for a in ['A1','A2']),'observation_caps':all(ex.token_count(p[a+'_observations'])<=400 for p in ps for a in ['A1','A2']),'query_caps':all(len(p[a+'_audit']['jumps'])<=12 for p in ps for a in ['A1','A2']),'native_errors_zero':all(not j['error'] for p in ps for a in ['A1','A2'] for j in p[a+'_audit']['jumps']),'source_authentic':True,'whole_packages_owner_headers':True,'native_trace_counts':True,'callback_actual_callsite_and_visible_argument':True,'workspace_hashes':all(ex.digest((R/'workspaces'/m.stem/file).read_text())==h for m in (R/'workspace_manifests').glob('*.json') for file,h in read(m)['source_sha256'].items()),'PR_ids_unused':not set(x['instance_id'] for x in read(R/'selected.json'))&set(read(R/'previously_selected_ids.json')),'witnesses_verified':read(R/'witness_validation.json')['all_contracts_verified'],'prompt_identity':True}
for p in ps:
 stem=p['case_id']+'_'+p['world'];sources={file:(R/'workspaces'/stem/file).read_text() for file in read(R/'workspace_manifests'/f'{stem}.json')['source_sha256']}
 for arm,policy in [('A1',ex.policy1),('A2',ex.policy)]:
  a=p[arm+'_audit'];defs,parents=policy.index(sources);checks['source_authentic']&=policy.render(sources,{file:set(lines) for file,lines in a['state']['source_lines'].items()})==p[arm]
  checks['whole_packages_owner_headers']&=all(set(lines).issubset(a['state']['source_lines'][file]) for adm in a['admissions'] for file,lines in policy.package(sources,defs,parents,tuple(adm['target'])).items())
  actual=[q for q in read(R/'lsp_traces'/f'{stem}_{arm}.json')['trace'] if q.get('method') in ['textDocument/definition','textDocument/references']];checks['native_trace_counts']&=len(actual)==len(a['jumps'])
  for rep in [1,2,3]:checks['prompt_identity']&=read(R/'inputs'/f'{stem}_{arm}_r{rep}.json')['prompt']==ex.prompt(p,arm)
  for binding in a.get('callback_bindings',[]):
   if binding.get('scheduled'):checks['callback_actual_callsite_and_visible_argument']&=binding['caller_visible'] and binding['call_line'] in a['state']['source_lines'][binding['caller_file']] and binding['provenance'] in ['native_definition_exact_callsite','native_reference_exact_callsite'] and not binding['runtime_certified']
if '--final' in sys.argv:
 audit=read(R/'audit_all.json');checks.update({'24_logical':len(audit['rows'])==24,'24_physical':sum(x['physical_calls'] for x in audit['operational'].values())==24,'transport_and_schema':all(x['status']=='ok' and not x['errors'] for x in audit['rows']),'tools0':all(x['tools']==0 for x in audit['operational'].values()),'all_reviews_read':set(x['run_id'] for x in audit['rows'])==set(read(R/'reviewed_ids.json'))})
else:checks['no_model_calls_yet']=not any((R/'raw').glob('*.json'))
checks['all_checks_pass']=all(checks.values());ex.save(R/('validation_results.json' if '--final' in sys.argv else 'preparation_validation.json'),checks);print(json.dumps(checks,indent=2));assert checks['all_checks_pass']
