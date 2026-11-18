"""Per-repeat input/schema/native audit, preserving failures in logical denominators."""
import hashlib,json,re,sys
from collections import Counter
from pathlib import Path
import jsonschema
R=Path(__file__).resolve().parent;sys.path.insert(0,str(R));import experiment as ex
stage=sys.argv[1] if len(sys.argv)>1 else 'all';arms=['B','O'];f=ex.read(R/'freeze.json');labels=ex.read(R/'labels.json');schema=ex.read(R/'schema.json');raws=[ex.read(p) for p in sorted((R/'raw').glob('*.json')) if not p.name.endswith('.trace.json')];raw_by={x['run_id']:x for x in raws};rows=[];caps=True
for pp in sorted((R/'packets').glob('*.json')):
 p=ex.read(pp)
 for arm in arms:
  caps &= p[arm+'_audit']['state']['tokens']<=1600 and ex.token_count(p[arm+'_observations'])<=400
  ids=set(re.findall(r'E-[A-Za-z0-9_-]+',p[arm]+'\n'+p[arm+'_observations']))|{'DIFF'}
  for rep in [1,2,3]:
   rid=f'{pp.stem}_{arm}_r{rep}';physical=f['shared_identical_inputs'].get(rid,rid);raw=raw_by.get(physical);errors=[];review=None;checks=[]
   if raw and raw['status']=='ok':
    out=raw['response']
    try:jsonschema.validate(out,schema)
    except Exception as e:errors.append('schema:'+str(e))
    review=out['review'];checks=out['checks'];inp=ex.read(R/'inputs'/f'{physical}.json');expected=ex.prompt(p,arm)
    if inp['prompt']!=expected or ex.digest(expected)!=raw['prompt_sha256']:errors.append('prompt_identity')
    if bool(review['issues'])!=(review['status']=='reported_issues'):errors.append('status_issue_list')
    for item in checks+review['issues']:
     if not set(item['evidence_ids']).issubset(ids):errors.append('unknown_evidence_id')
    if sorted(set(i for c in checks for i in c['issue_indices']))!=list(range(len(review['issues']))):errors.append('ledger_coverage')
    if any(c['issue_indices'] and c['comparison']!='changed' for c in checks):errors.append('issue_without_changed_observation')
   rows.append({'run_id':rid,'physical_run_id':physical,'case_id':p['case_id'],'world':p['world'],'arm':arm,'repeat':rep,'instance_id':p['instance_id'],'repository':p['repository'],'group':p['group'],'label':labels[p['case_id']]['worlds'][p['world']],'review':review,'checks':checks,'errors':errors,'status':'invalid' if errors else raw['status'] if raw else 'missing'})
operational={}
for arm in arms:
 calls=[x for x in raws if x['arm']==arm];operational[arm]={'physical_calls':len(calls),'transport_ok':sum(x['status']=='ok' for x in calls),'tools':sum(x['tool_audit']['tool_call_count'] for x in calls),'input_tokens':sum(u.get('input_tokens',0) for x in calls for u in x['usage']),'cached_input_tokens':sum(u.get('cached_input_tokens',0) for x in calls for u in x['usage']),'output_tokens':sum(u.get('output_tokens',0) for x in calls for u in x['usage']),'usage_missing':sum(not x['usage'] for x in calls)}
navigation={}
for arm in arms:
 jumps=[j for pp in (R/'packets').glob('*.json') for j in ex.read(pp)[arm+'_audit']['jumps']];navigation[arm]={'shared_common_definition_queries':len(jumps),'new_extra_queries':sum(len(ex.read(pp)[arm+'_audit'].get('extra_navigation',[])) for pp in (R/'packets').glob('*.json')),'effective_query_count_including_common':len(jumps)+sum(len(ex.read(pp)[arm+'_audit'].get('extra_navigation',[])) for pp in (R/'packets').glob('*.json')),'callable_targets':sum(bool(j['targets']) for j in jumps),'native_source_unit_targets':sum(bool(j.get('unit_targets')) for j in jumps),'server_errors':sum(bool(j.get('error')) for j in jumps),'origins':dict(Counter(j['origin'] for j in jumps))}
result={'stage':stage,'frozen_files_unchanged':all(hashlib.sha256((ex.ROOT/p).read_bytes()).hexdigest()==h for p,h in f['files'].items()),'caps_valid':caps,'operational':operational,'navigation':navigation,'invalid':[x['run_id'] for x in rows if x['errors']],'missing':[x['run_id'] for x in rows if x['status']=='missing'],'rows':rows};(R/f'audit_{stage}.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps({k:v for k,v in result.items() if k!='rows'},ensure_ascii=False,indent=2))
