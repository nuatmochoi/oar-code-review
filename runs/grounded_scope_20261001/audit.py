"""Independent post-run checks; no labels enter reviewer inputs."""
import json,re,sys
from pathlib import Path
from collections import Counter
import jsonschema
R=Path(__file__).resolve().parent;ROOT=R.parents[1];sys.path.insert(0,str(ROOT))
from ace_review.context_adaptive import digest
read=lambda p:json.loads(p.read_text())
f=read(R/'freeze.json');schemas=read(R/'schemas.json');labels=read(R/'labels.json');old=read(ROOT/'runs/icml_priority_20261001/results.json');oldcoverage={(x['case_id'],x['world']):x['complete_world_fact'] for x in old['rows'] if x['arm']=='D'};calls=[];rows=[]
for p in sorted((R/'raw').glob('*.json')):
 if p.name.endswith('.trace.json'):continue
 d=read(p);calls.append(d)
byid={d['run_id']:d for d in calls}
for p in sorted((R/'packets').glob('*.json')):
 packet=read(p);cid=packet['case_id'];world=packet['world'];text=packet['D'];ids=set(re.findall(r'E-[A-Za-z0-9_-]+',text))|{'DIFF'}
 if cid.startswith('N'):
  lines=set()
  for key,positions in packet['D_audit']['state']['source_lines'].items():
   header=text.split('['+key+'] ')[1].split('\n')[0]
   if header.startswith('head deployment.py:'):lines.update(positions)
  complete={3,4,6,7}.issubset(lines)
 else:complete=oldcoverage[cid,world]
 for arm in ['baseline','grounded']:
  key=cid+'_'+world+'_'+arm;raw=byid.get(key);errors=[];review=None;checks=[]
  if raw and raw['status']=='ok':
   response=raw['response'];jsonschema.validate(response,schemas[arm]);review=response['review'] if arm=='grounded' else response;checks=response['checks'] if arm=='grounded' else []
   if bool(review['issues'])!=(review['status']=='reported_issues'):errors.append('status/list mismatch')
   for item in review['issues']+checks:
    if not set(item['evidence_ids']).issubset(ids):errors.append('unknown evidence ID')
   if arm=='grounded':
    ix=[i for c in checks for i in c['issue_indices']]
    if sorted(ix)!=list(range(len(review['issues']))):errors.append('ledger issue coverage')
    if any(c['issue_indices'] and c['comparison']!='changed' for c in checks):errors.append('reported issue without changed observation')
   inp=read(R/'inputs'/p.name.replace('.json','_'+arm+'.json'))
   if digest(inp['prompt'])!=raw['prompt_sha256']:errors.append('prompt hash mismatch')
  rows.append({'run_id':key,'case_id':cid,'world':world,'label':labels[cid]['worlds'][world],'stage':packet['stage'],'arm':arm,'complete_world_fact':complete,'operational_status':('invalid' if errors else raw['status']) if raw else 'blocked','errors':errors,'review':review,'checks':checks})
summary={}
for stage in ['regression','fresh_same_repository']:
 c=[d for d in calls if d['stage']==stage];summary[stage]={'physical_calls':len(c),'transport_ok':sum(d['status']=='ok' for d in c),'tool_calls':sum(d['tool_audit']['tool_call_count'] for d in c),'usage_missing':sum(not d['usage'] for d in c),'arms':{}}
 for arm in ['baseline','grounded']:
  a=[d for d in c if d['arm']==arm];summary[stage]['arms'][arm]={'calls':len(a),'input_tokens':sum(u.get('input_tokens',0) for d in a for u in d['usage']),'cached_input_tokens':sum(u.get('cached_input_tokens',0) for d in a for u in d['usage']),'output_tokens':sum(u.get('output_tokens',0) for d in a for u in d['usage']),'wall_seconds_sum':sum(d['duration_seconds'] for d in a)}
audit={'frozen_files_unchanged':all(digest((ROOT/p).read_text())==h for p,h in f['files'].items()),'evidence_cap_valid':all(read(p)['D_audit']['state']['tokens']<=1600 for p in (R/'packets').glob('*.json')),'witnesses_valid':all((x['actual']!=x['expected'])==x['target_failure'] and x['target_failure']==(x['label']=='unsafe' and x['revision']=='head') for x in read(R/'witnesses.json')),'operational':summary,'rows':rows,'invalid':[x['run_id'] for x in rows if x['errors']]}
(R/'audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2));print(json.dumps({k:v for k,v in audit.items() if k!='rows'},ensure_ascii=False,indent=2))
