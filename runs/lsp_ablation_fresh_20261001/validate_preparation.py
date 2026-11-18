"""Preparation audit only. Does not infer review quality without model outputs."""
import hashlib,json
from pathlib import Path
import experiment as ex
R=Path(__file__).resolve().parent;read=lambda p:json.loads(p.read_text());f=read(R/'freeze.json');rows=[read(p) for p in sorted((R/'packets').glob('*.json'))];checks={'frozen_files_unchanged':all(hashlib.sha256((ex.ROOT/p).read_bytes()).hexdigest()==h for p,h in f['files'].items()),'six_new_cases':len(read(R/'selected.json'))==6,'novelty_inventory_disjoint':not set(read(R/'selection_initial.json')['selected_instance_ids'])&set(read(R/'selection_initial.json')['used_instances']),'witnesses_verified':read(R/'witness_validation.json')['all_contracts_verified'],'integration_tests_passed':read(R/'policy_tests.json')['passed'],'twelve_direction_packets':len(rows)==12,'source_caps':True,'observation_caps':True,'P_no_extra_queries':True,'query_caps':True,'P_protected_in_R':True,'R_identical_P_seed':True,'R_only_native_supplement':True,'workspace_hashes':True,'input_identity':True,'logical_inputs_108':len(list((R/'inputs').glob('*.json')))==108,'no_model_calls':not any((R/'raw').glob('*.json'))}
nav={'common_definition_queries':0,'new_reference_queries':0,'reference_queries_with_in_corpus_candidates':0,'reference_errors':[]};source={'P_changed_from_B':0,'R_changed_from_P':0,'R_prompt_changed_from_P':0,'P_protected_unit_count':0}
for p in rows:
 ba=p['B_audit'];pa=p['P_audit'];ra=p['R_audit'];bs=ba['state']['source_lines'];ps=pa['state']['source_lines'];rs=ra['state']['source_lines'];core=set(pa['protected_ids'])|{x['unit_id'] for x in pa['additions']};source['P_protected_unit_count']+=len(core)
 checks['P_protected_in_R'] &= all(k in rs and ps[k]==rs[k] for k in core)
 checks['R_identical_P_seed'] &= p['R_common_seed_sha256']==ex.digest(p['P'])
 checks['R_only_native_supplement'] &= all(x['reason'] in ['native_reference_caller','referenced_import_statement'] for x in ra['additions'])
 checks['P_no_extra_queries'] &= not pa['extra_navigation']
 for a in ['B','P','R']:
  checks['source_caps'] &= ex.token_count(p[a])<=1600
  checks['observation_caps'] &= ex.token_count(p[a+'_observations'])<=400
  checks['query_caps'] &= len(p[a+'_audit']['jumps'])+len(p[a+'_audit'].get('extra_navigation',[]))<=15
 m=read(R/'workspace_manifests'/f'{p["case_id"]}_{p["world"]}.json');checks['workspace_hashes'] &= all(ex.digest((ex.ROOT/p['workspace_reference']/file).read_text())==h for file,h in m['source_sha256'].items())
 nav['common_definition_queries']+=len(ba['jumps']);nav['new_reference_queries']+=len(ra['extra_navigation']);nav['reference_queries_with_in_corpus_candidates']+=sum(bool(x['units']) for x in ra['extra_navigation']);nav['reference_errors'] += [x['error'] for x in ra['extra_navigation'] if x['error']];source['P_changed_from_B']+=p['P']!=p['B'];source['R_changed_from_P']+=p['R']!=p['P'];source['R_prompt_changed_from_P']+=ex.prompt(p,'R')!=ex.prompt(p,'P')
 for a in ['B','P','R']:
  for rep in [1,2,3]:
   inp=read(R/'inputs'/f'{p["case_id"]}_{p["world"]}_{a}_r{rep}.json');checks['input_identity'] &= inp['prompt']==ex.prompt(p,a) and inp['prompt_sha256']==ex.digest(inp['prompt'])
checks['no_native_errors']=not nav['reference_errors'] and not any(j.get('error') for p in rows for j in p['B_audit']['jumps']);out={'checks':checks,'all_passed':all(checks.values()),'navigation':nav,'source':source,'future_logical_reviews':108,'future_physical_calls':f['physical_calls'],'model_calls_completed':0,'note':'Preparation validation, no performance conclusion. P and R share frozen protected P core; R observation/source supplementation is the primary treatment.'};(R/'preparation_validation.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n');print(json.dumps(out,ensure_ascii=False,indent=2));assert out['all_passed']
