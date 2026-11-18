"""Validate saved run integrity and scoring; no additional model calls."""
import hashlib,json
from pathlib import Path
R=Path(__file__).resolve().parent; ROOT=R.parents[1]
read=lambda name:json.loads((R/name).read_text())
f=read('freeze.json');a=read('audit.json');ann=read('annotations.json');res=read('results.json');nav=read('navigation_summary.json');w=read('witnesses.json');micro=read('resolver_tests.json')
checks={}
checks['frozen_file_hashes']=all(hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==h for p,h in f['files'].items())
for key in ['staged_source_identity','staged_configuration_identity','previous_corpus_identity','source_cap_valid']:
 checks[key]=a[key]
checks['all_logical_reviews_adjudicated']=len(ann)==len(a['rows'])==76 and set(ann)=={x['run_id'] for x in a['rows']}
checks['all_transport_and_schema_valid']=not a['invalid'] and all(x['calls']==x['transport_ok'] and x['tools']==0 and x['usage_missing']==0 for x in a['operational'].values())
checks['actual_call_count']=sum(x['calls'] for x in a['operational'].values())==f['planned_physical_calls']==50
shared_packets=[read('packets/'+k.removesuffix('_lsp')+'.json') for k in f['shared_identical_inputs']]
checks['shared_evidence_identical']=len(shared_packets)==26 and all(p['AST']==p['LSP'] and p['AST_observations']==p['LSP_observations'] for p in shared_packets)
checks['observation_cap_valid']=all(p[arm+'_observation_audit']['reference_tokens']<=400 for p in [json.loads(path.read_text()) for path in (R/'packets').glob('*.json')] for arm in ['AST','LSP'])
checks['actual_lsp_requests']=a['native_definition_requests']==a['common_query_sites']==424 and a['query_resolution']['lsp_server_error']==0
checks['native_outcome_accounting']=sum(nav['native_outcomes'][k] for k in ['in_corpus_callable','native_nonempty_filtered','native_empty_result'])==424
checks['micro_expected_static_definitions']=micro['passed']==10 and all(x['matches'] for x in micro['rows'])
checks['body_witnesses_repeat']=len(w)==32
for cid in ['W01','W02','W03','W04']:
 for world in ['a','b']:
  byrev={rev:[x['actual'] for x in w if x['case_id']==cid and x['world']==world and x['revision']==rev] for rev in ['base','head']}
  checks['body_witnesses_repeat'] &= all(len(v)==2 and v[0]==v[1] for v in byrev.values()) and ((byrev['base'][0]==byrev['head'][0])==(world=='a'))
checks['pair_score_bounds']=all(0<=m['correct_pairs']<=m['total_pairs'] and 0<=m['correct_worlds']<=m['total_worlds'] for group in res['summary'].values() for m in group.values())
result={'checks':checks,'passed':all(checks.values()),'note':'Checks saved evidence and integrity. Does not rerun model, LSP, framework or database.'}
(R/'validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(result,ensure_ascii=False,indent=2));assert result['passed']
