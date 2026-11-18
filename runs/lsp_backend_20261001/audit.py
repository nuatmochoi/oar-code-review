"""Native LSP transport/positions, retrieval coverage and reviewer relation audit."""
import json,re,sys
from pathlib import Path
from collections import Counter
import jsonschema
R=Path(__file__).resolve().parent;ROOT=R.parents[1];sys.path.insert(0,str(ROOT))
from ace_review.context_adaptive import digest,token_count,FINAL_PROMPT
read=lambda p:json.loads(p.read_text())
f=read(R/'freeze.json');schema=read(R/'schema.json');labels=read(R/'labels.json');targets=read(ROOT/'runs/context_fresh6_20260929/evaluation_targets.json');raws=[read(p) for p in sorted((R/'raw').glob('*.json')) if not p.name.endswith('.trace.json')];byid={x['run_id']:x for x in raws};rows=[];resolution=Counter();common_deltas=[];requests=0;lookup_time=0.;start_time=0.;stderr=[];source_identity=True;original_identity=True;configuration_identity=True
for pp in sorted((R/'packets').glob('*.json')):
 p=read(pp);cid=p['case_id'];world=p['world'];key=pp.stem;label=labels[cid]['worlds'][world];trace=read(R/'lsp_traces'/pp.name);manifest=read(R/'workspace_manifests'/pp.name);root=R/'workspaces'/key;source_identity &= all(digest((root/file).read_text())==h for file,h in manifest['source_sha256'].items());configuration_identity &= read(root/'pyrightconfig.json')==manifest['pyrightconfig'];requests+=trace['definition_requests'];lookup_time+=sum(t['duration_seconds'] for t in trace['trace'] if t.get('method')=='textDocument/definition');start_time+=trace['startup_seconds'];stderr.extend(trace['stderr'])
 if cid[0] in 'FHN' or cid=='P08':
  original=read(ROOT/'runs/binding_context_20261001/packets'/pp.name);original_identity &= p['source_sha256']==original['source_sha256']
 assert trace['definition_requests']==len(p['LSP_audit']['jumps'])<=f['limits']['queries']
 amap={(x['source_file'],x['line'],x['utf16_character'],x['kind']):x for x in p['AST_audit']['jumps']};bmap={(x['source_file'],x['line'],x['utf16_character'],x['kind']):x for x in p['LSP_audit']['jumps']}
 for site in sorted(amap.keys()&bmap.keys()):
  a,b=amap[site],bmap[site];at={tuple(x) for x in a['targets']};bt={tuple(x) for x in b['targets']};common_deltas.append(dict(case_id=cid,world=world,site=list(site),called=a['called'],AST=sorted(at),LSP=sorted(bt),different=at!=bt))
 for arm in ['ast','lsp']:
  text=p[arm.upper()];observations=p[arm.upper()+'_observations'];state=p[arm.upper()+'_audit']['state'];jumps=p[arm.upper()+'_audit']['jumps'];resolution.update({arm+'_'+kind:sum(x['resolution']==kind for x in jumps) for kind in ['unique_static_candidate','multiple_static_candidates','unresolved','server_error']})
  target=targets['F08' if cid=='P08' else cid][label] if cid.startswith('F') or cid=='P08' else {'file':'deployment.py','start':3,'end':4};covered=set();entry_lines=set();target_defs=[]
  for eid,lines in state['source_lines'].items():
   header=text.split('['+eid+'] ')[1].split('\n')[0]
   if header.startswith('head '+target['file']+':'):covered.update(lines)
   if header.startswith('head deployment.py:'):entry_lines.update(lines)
   target_defs.append(dict(id=eid,header=header))
  key_arm=key+'_'+arm;physical=f['shared_identical_inputs'].get(key_arm,key_arm);raw=byid.get(physical);errors=[];review=None;checks=[];ids=set(re.findall(r'E-[A-Za-z0-9_-]+',text+'\n'+observations))|{'DIFF'}
  if raw and raw['status']=='ok':
   x=raw['response'];jsonschema.validate(x,schema);review=x['review'];checks=x['checks'];inp=read(R/'inputs'/f'{physical}.json');expected=__import__('experiment').prior.LEDGER_PROMPT+'\n'+FINAL_PROMPT+p['common']+'\n[EVIDENCE]\n'+text+'\n[STATIC DEFINITION OBSERVATIONS — NOT RUNTIME PROOF]\n'+observations
   if inp['prompt']!=expected or digest(inp['prompt'])!=raw['prompt_sha256']:errors.append('prompt identity/hash mismatch')
   if bool(review['issues'])!=(review['status']=='reported_issues'):errors.append('status/list mismatch')
   for item in review['issues']+checks:
    if not set(item['evidence_ids']).issubset(ids):errors.append('unknown evidence ID')
   if sorted(i for c in checks for i in c['issue_indices'])!=list(range(len(review['issues']))):errors.append('ledger issue coverage')
   if any(c['issue_indices'] and c['comparison']!='changed' for c in checks):errors.append('issue without changed observation')
  rows.append(dict(run_id=key_arm,physical_run_id=physical,shared_response=key_arm!=physical,case_id=cid,world=world,stage=p['stage'],label=label,arm=arm,complete_world_fact=set(range(target['start'],target['end']+1)).issubset(covered),entry_source_lines=sorted(entry_lines),visible_units=target_defs,review=review,checks=checks,errors=errors,operational_status='invalid' if errors else raw['status'] if raw else 'blocked'))
operational={}
for arm in ['ast','lsp']:
 calls=[x for x in raws if x['arm']==arm];operational[arm]={'calls':len(calls),'transport_ok':sum(x['status']=='ok' for x in calls),'tools':sum(x['tool_audit']['tool_call_count'] for x in calls),'input_tokens':sum(u.get('input_tokens',0) for x in calls for u in x['usage']),'cached_input_tokens':sum(u.get('cached_input_tokens',0) for x in calls for u in x['usage']),'output_tokens':sum(u.get('output_tokens',0) for x in calls for u in x['usage']),'usage_missing':sum(not x['usage'] for x in calls)}
result={'frozen_files_unchanged':all(digest((ROOT/p).read_text())==h for p,h in f['files'].items()),'staged_source_identity':source_identity,'staged_configuration_identity':configuration_identity,'previous_corpus_identity':original_identity,'source_cap_valid':all(read(p)[a+'_audit']['state']['tokens']<=f['source_cap'] for p in (R/'packets').glob('*.json') for a in ['AST','LSP']),'native_definition_requests':requests,'lsp_lookup_seconds_sum':lookup_time,'lsp_startup_seconds_sum':start_time,'server_stderr':stderr,'query_resolution':dict(resolution),'common_query_sites':len(common_deltas),'common_site_differences':sum(x['different'] for x in common_deltas),'common_queries':common_deltas,'operational':operational,'invalid':[x['run_id'] for x in rows if x['errors']],'rows':rows}
(R/'audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps({k:v for k,v in result.items() if k not in ['rows','common_queries']},ensure_ascii=False,indent=2))
