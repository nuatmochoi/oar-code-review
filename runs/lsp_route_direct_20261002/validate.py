import hashlib,json,sys
from pathlib import Path
import experiment as ex
R=Path(__file__).resolve().parent;read=ex.read
f=read(R/'freeze.json');packets=[read(p) for p in sorted((R/'packets').glob('*.json'))]
checks={'freeze_valid':all(ex.sha(ex.ROOT/p)==h for p,h in f['files'].items()),'prior_result_seals_valid':True,'four_packets':len(packets)==4,'same_cases':read(R/'labels.json')==read(ex.PRIOR/'labels.json'),'source_caps':all(ex.token_count(p[a])<=2600 for p in packets for a in ['A0','A1']),'observation_caps':all(ex.token_count(p[a+'_observations'])<=400 for p in packets for a in ['A0','A1']),'query_caps':all(len(p[a+'_audit']['jumps'])==12 for p in packets for a in ['A0','A1']),'native_errors_zero':all(not q['error'] for p in packets for a in ['A0','A1'] for q in p[a+'_audit']['jumps']),'workspace_hashes':all(ex.digest((R/'workspaces'/m.stem/file).read_text())==h for m in (R/'workspace_manifests').glob('*.json') for file,h in read(m)['source_sha256'].items()),'same_source_corpus':True,'source_authentic':True,'whole_packages_owner_headers':True,'A1_packet_exact_reuse':True,'A1_prompt_exact_reuse':True,'A1_annotations_locked':True,'new_semantic_query_count':0,'witness_verified_reuse':read(ex.PRIOR/'witness_validation.json')['all_contracts_verified']}
for d in [ex.OLD,ex.PRIOR]:
 checks['prior_result_seals_valid']&=all(ex.sha(d/p)==h for p,h in read(d/'result_seal.json')['files'].items())
for p in packets:
 stem=p['case_id']+'_'+p['world'];prior=read(ex.PRIOR/'packets'/f'{stem}.json');manifest=read(R/'workspace_manifests'/f'{stem}.json');checks['same_source_corpus']&=manifest['source_sha256']==read(ex.PRIOR/'workspace_manifests'/f'{stem}.json')['source_sha256']
 sources={file:(R/'workspaces'/stem/file).read_text() for file in manifest['source_sha256']};a=p['A0_audit'];defs,parents=ex.policy.index(sources)
 checks['source_authentic']&=ex.policy.render(sources,{file:set(lines) for file,lines in a['state']['source_lines'].items()})==p['A0']
 checks['whole_packages_owner_headers']&=all(set(lines).issubset(a['state']['source_lines'][file]) for adm in a['admissions'] for file,lines in ex.policy.package(sources,defs,parents,tuple(adm['target'])).items())
 checks['A1_packet_exact_reuse']&=p['common']==prior['common'] and p['A1']==prior['A'] and p['A1_observations']==prior['A_observations'] and p['A1_audit']==prior['A_audit']
 actual=[q for q in read(R/'lsp_traces'/f'{stem}_A0.json')['trace'] if q.get('method') in ['textDocument/definition','textDocument/references']];checks['new_semantic_query_count']+=len(actual);assert len(actual)==len(a['jumps'])
 for arm in ['A0','A1']:
  for rep in [1,2,3]:
   rid=f'{stem}_{arm}_r{rep}';inp=read(R/'inputs'/f'{rid}.json');assert inp['prompt']==ex.prompt(p,arm)
   if arm=='A1':
    info=f['reused'][rid];checks['A1_prompt_exact_reuse']&=inp['prompt']==read(ex.ROOT/info['input_path'])['prompt'];checks['A1_annotations_locked']&=read(R/'reused_annotations.json')[rid]==read(ex.PRIOR/'annotations_all.json')[info['original_run_id']]
checks['48_new_semantic_queries']=checks.pop('new_semantic_query_count')==48
if '--final' in sys.argv:
 audit=read(R/'audit_all.json');notes=read(R/'adjudication_notes.json');checks.update({'all_rows_reviewed':set(x['run_id'] for x in audit['rows'])==set(read(R/'reviewed_ids.json')),'24_logical_reviews':len(audit['rows'])==24,'12_new_model_calls':audit['operational']['A0']['new_physical_calls']==12,'12_reused_model_calls':audit['operational']['A1']['reused_physical_calls']==12,'24_unique_physical_calls':len({x['physical_run_id'] for x in audit['rows']})==24,'schema_and_transport':all(x['status']=='ok' and not x['errors'] for x in audit['rows']),'tools_zero':all(x['tools']==0 for x in audit['operational'].values()),'A1_grades_preserved':all(notes[rid]==grade for rid,grade in read(R/'reused_annotations.json').items())})
else:checks['no_new_model_calls_yet']=not any((R/'raw').glob('*.json'))
checks['all_checks_pass']=all(checks.values());ex.save(R/('validation_results.json' if '--final' in sys.argv else 'preparation_validation.json'),checks);print(json.dumps(checks,indent=2));assert checks['all_checks_pass']
