import json,re,sys
from pathlib import Path
import jsonschema
R=Path(__file__).resolve().parent;ROOT=R.parents[1];sys.path.insert(0,str(ROOT))
from ace_review.context_adaptive import digest
read=lambda p:json.loads(p.read_text())
f=read(R/'freeze.json');labels=read(R/'labels.json');targets=read(ROOT/'runs/context_fresh6_20260929/evaluation_targets.json');schema=read(R/'schema.json');calls=[read(p) for p in sorted((R/'raw').glob('*.json')) if not p.name.endswith('.trace.json')];byid={d['run_id']:d for d in calls};rows=[]
for p in sorted((R/'packets').glob('*.json')):
 packet=read(p);cid=packet['case_id'];world=packet['world'];label=labels[cid]['worlds'][world];target=targets['F08' if cid=='P08' else cid][label] if (cid.startswith('F') or cid=='P08') else {'file':'deployment.py','start':3,'end':4}
 for arm in ['definition','binding']:
  key=cid+'_'+world+'_'+arm;raw=byid.get(f.get('shared_identical_inputs',{}).get(key,key));text=packet[arm];state=packet[arm+'_audit']['state'];covered=set();visible=[]
  for eid,lines in state['source_lines'].items():
   header=text.split('['+eid+'] ')[1].split('\n')[0]
   if header.startswith('head '+target['file']+':'):covered.update(lines)
   visible.append({'id':eid,'header':header})
  complete=set(range(target['start'],target['end']+1)).issubset(covered);errors=[];review=None;checks=[];ids=set(re.findall(r'E-[A-Za-z0-9_-]+',text))|{'DIFF'}
  if raw and raw['status']=='ok':
   response=raw['response'];jsonschema.validate(response,schema);review=response['review'];checks=response['checks'];input=read(R/'inputs'/f"{raw['run_id']}.json")
   if digest(input['prompt'])!=raw['prompt_sha256']:errors.append('prompt hash mismatch')
   if bool(review['issues'])!=(review['status']=='reported_issues'):errors.append('status/list mismatch')
   for item in review['issues']+checks:
    if not set(item['evidence_ids']).issubset(ids):errors.append('unknown evidence id')
   ix=[i for c in checks for i in c['issue_indices']]
   if sorted(ix)!=list(range(len(review['issues']))):errors.append('ledger issue coverage')
   if any(c['issue_indices'] and c['comparison']!='changed' for c in checks):errors.append('ledger changed relation')
  rows.append({'run_id':key,'case_id':cid,'world':world,'label':label,'arm':arm,'complete_world_fact':complete,'review':review,'checks':checks,'operational_status':('invalid' if errors else raw['status']) if raw else 'blocked','errors':errors,'visible_units':visible,'physical_run_id':raw['run_id'] if raw else None,'shared_response':key in f.get('shared_identical_inputs',{})})
summary={}
for arm in ['definition','binding']:
 c=[x for x in calls if x['arm']==arm];summary[arm]={'calls':len(c),'transport_ok':sum(x['status']=='ok' for x in c),'tool_calls':sum(x['tool_audit']['tool_call_count'] for x in c),'input_tokens':sum(u.get('input_tokens',0) for x in c for u in x['usage']),'cached_input_tokens':sum(u.get('cached_input_tokens',0) for x in c for u in x['usage']),'output_tokens':sum(u.get('output_tokens',0) for x in c for u in x['usage']),'usage_missing':sum(not x['usage'] for x in c)}
a={'frozen_files_unchanged':all(digest((ROOT/p).read_text())==h for p,h in f['files'].items()),'source_cap_valid':all(read(p)[arm+'_audit']['state']['tokens']<=1600 for p in (R/'packets').glob('*.json') for arm in ['definition','binding']),'operational':summary,'invalid':[x['run_id'] for x in rows if x['errors']],'rows':rows}
(R/'audit.json').write_text(json.dumps(a,ensure_ascii=False,indent=2));print(json.dumps({k:v for k,v in a.items() if k!='rows'},indent=2))
