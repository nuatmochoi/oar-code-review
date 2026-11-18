import hashlib,json,re,sys
from pathlib import Path
from collections import Counter
import jsonschema
R=Path(__file__).resolve().parent;ROOT=R.parents[1];sys.path.insert(0,str(R));import experiment as exp
read=lambda p:json.loads(p.read_text())
f=read(R/'freeze.json');schema=read(R/'schema.json');labels=read(R/'labels.json');raws=[read(p) for p in sorted((R/'raw').glob('*.json')) if not p.name.endswith('.trace.json')];byid={x['run_id']:x for x in raws};rows=[];resolution=Counter();common=[];requests=0;lookup=0.;startup=0.;stderr=[];identity=True;config_identity=True;native=Counter()
for pp in sorted((R/'packets').glob('*.json')):
 p=read(pp);trace=read(R/'lsp_traces'/pp.name);m=read(R/'workspace_manifests'/pp.name);root=R/'workspaces'/pp.stem
 identity &= all(exp.digest((root/path).read_text())==h for path,h in m['source_sha256'].items());config_identity &= read(root/'pyrightconfig.json')==m['pyrightconfig']
 native_requests=[t for t in trace['trace'] if t.get('method')=='textDocument/definition'];requests+=len(native_requests);lookup+=sum(t['duration_seconds'] for t in native_requests);startup+=trace['startup_seconds'];stderr+=trace['stderr'];assert len(native_requests)==len(p['LSP_audit']['jumps'])<=12
 for j in p['LSP_audit']['jumps']:native['server_error' if j.get('error') else 'in_corpus_callable' if j['targets'] else 'native_nonempty_filtered' if j['raw_result'] else 'native_empty_result']+=1
 maps=[{(j['source_file'],j['line'],j['utf16_character'],j['kind']):j for j in p[arm+'_audit']['jumps']} for arm in ['AST','LSP']]
 for site in sorted(maps[0].keys()&maps[1].keys()):
  aa,bb=[set(map(tuple,m[site]['targets'])) for m in maps];common.append({'case_id':p['case_id'],'world':p['world'],'site':site,'different':aa!=bb,'AST':sorted(aa),'LSP':sorted(bb)})
 for arm in ['ast','lsp']:
  up=arm.upper();jumps=p[up+'_audit']['jumps'];resolution.update({arm+'_'+k:sum(j['resolution']==k for j in jumps) for k in ['unique_static_candidate','multiple_static_candidates','unresolved','server_error']})
  rid=pp.stem+'_'+arm;physical=f['shared_identical_inputs'].get(rid,rid);raw=byid.get(physical);errors=[];review=None;checks=[]
  if raw and raw['status']=='ok':
   out=raw['response']
   try:jsonschema.validate(out,schema)
   except Exception as e:errors.append('schema:'+str(e))
   review=out['review'];checks=out['checks'];inp=read(R/'inputs'/f'{physical}.json');expected=exp.ledger.LEDGER_PROMPT+'\n'+exp.FINAL_PROMPT+p['common']+'\n[EVIDENCE]\n'+p[up]+'\n[STATIC DEFINITION OBSERVATIONS — NOT RUNTIME PROOF]\n'+p[up+'_observations']
   if inp['prompt']!=expected or exp.digest(expected)!=raw['prompt_sha256']:errors.append('prompt identity mismatch')
   if bool(review['issues'])!=(review['status']=='reported_issues'):errors.append('status/list mismatch')
   ids=set(re.findall(r'E-[A-Za-z0-9_-]+',p[up]+'\n'+p[up+'_observations']))|{'DIFF'}
   for item in review['issues']+checks:
    if not set(item['evidence_ids']).issubset(ids):errors.append('unknown evidence ID')
   if sorted(i for c in checks for i in c['issue_indices'])!=list(range(len(review['issues']))):errors.append('ledger issue coverage')
   if any(c['issue_indices'] and c['comparison']!='changed' for c in checks):errors.append('issue without changed observation')
  rows.append({'run_id':rid,'physical_run_id':physical,'shared_response':rid!=physical,'case_id':p['case_id'],'world':p['world'],'stage':'code_generation_format_diagnostic' if p['case_id']=='E09' else p['stage'],'repository':p['repository'],'instance_id':p['instance_id'],'label':labels[p['case_id']]['worlds'][p['world']],'arm':arm,'review':review,'checks':checks,'errors':errors,'operational_status':'invalid' if errors else raw['status'] if raw else 'blocked'})
operational={}
for arm in ['ast','lsp']:
 calls=[x for x in raws if x['arm']==arm];operational[arm]={'calls':len(calls),'transport_ok':sum(x['status']=='ok' for x in calls),'tools':sum(x['tool_audit']['tool_call_count'] for x in calls),'input_tokens':sum(u.get('input_tokens',0) for x in calls for u in x['usage']),'cached_input_tokens':sum(u.get('cached_input_tokens',0) for x in calls for u in x['usage']),'output_tokens':sum(u.get('output_tokens',0) for x in calls for u in x['usage']),'usage_missing':sum(not x['usage'] for x in calls)}
result={'frozen_files_unchanged':all(hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==h for p,h in f['files'].items()),'staged_source_identity':identity,'staged_configuration_identity':config_identity,'source_cap_valid':all(read(p)[arm+'_audit']['state']['tokens']<=1600 for p in (R/'packets').glob('*.json') for arm in ['AST','LSP']),'observation_cap_valid':all(exp.token_count(read(p)[arm+'_observations'])<=400 for p in (R/'packets').glob('*.json') for arm in ['AST','LSP']),'native_definition_requests':requests,'lsp_lookup_seconds_sum':lookup,'lsp_startup_seconds_sum':startup,'server_stderr':stderr,'native_outcomes':dict(native),'query_resolution':dict(resolution),'common_query_sites':len(common),'common_site_differences':sum(x['different'] for x in common),'common_queries':common,'source_changed_worlds':sum(read(p)['AST']!=read(p)['LSP'] for p in (R/'packets').glob('*.json')),'final_input_changed_worlds':24-len(f['shared_identical_inputs']),'operational':operational,'invalid':[x['run_id'] for x in rows if x['errors']],'rows':rows}
(R/'audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps({k:v for k,v in result.items() if k not in ['rows','common_queries']},ensure_ascii=False,indent=2))
