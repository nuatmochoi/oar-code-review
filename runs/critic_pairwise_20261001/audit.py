"""Mechanical audit and candidate selection mapping; does not judge claim correctness."""
import json,re,sys
from pathlib import Path
import jsonschema
R=Path(__file__).resolve().parent;ROOT=R.parents[1];sys.path.insert(0,str(ROOT))
from ace_review.context_adaptive import digest
read=lambda p:json.loads(p.read_text())
f=read(R/'freeze.json');schemas=read(R/'schemas.json');orders=read(R/'order.json');labels=read(R/'labels.json');records=[read(p) for p in sorted((R/'raw').glob('*.json')) if not p.name.endswith('.trace.json')];byid={d['run_id']:d for d in records};errors={};rows=[];resolved={}
for pp in sorted((R/'packets').glob('*.json')):
 p=read(pp);key=p['case_id']+'_'+p['world'];ids=set(re.findall(r'E-[A-Za-z0-9_-]+',p['evidence']))|{'DIFF'}
 for phase in ['baseline','critic','alternate','pair','pair_swapped']:
  rid=key+'_'+phase;raw=byid.get(rid)
  if not raw:continue
  errs=[]
  inp=read(R/'inputs'/f'{rid}.json')
  if digest(inp['prompt'])!=raw['prompt_sha256']:errs.append('input hash mismatch')
  if raw['status']=='ok':
   response=raw['response'];jsonschema.validate(response,schemas['pair' if phase.startswith('pair') else 'review'])
   if phase.startswith('pair'):
    if not set(response['evidence_ids']).issubset(ids):errs.append('unknown evidence ID')
   else:
    review=response['review'];checks=response['checks']
    if bool(review['issues'])!=(review['status']=='reported_issues'):errs.append('review status/list mismatch')
    for item in review['issues']+checks:
     if not set(item['evidence_ids']).issubset(ids):errs.append('unknown evidence ID')
    ix=[i for check in checks for i in check['issue_indices']]
    if sorted(ix)!=list(range(len(review['issues']))):errs.append('ledger issue coverage')
    if any(c['issue_indices'] and c['comparison']!='changed' for c in checks):errs.append('reported issue without changed comparison')
  else:errs.append('transport failure')
  if errs:errors[rid]=errs
 for phase in ['baseline','critic','alternate']:
  rid=key+'_'+phase;raw=byid.get(rid)
  if raw and rid not in errors:resolved[rid]=raw['response']
 for phase in ['pair','pair_swapped']:
  rid=key+'_'+phase;raw=byid.get(rid)
  if not raw or rid in errors:continue
  names=orders[key] if phase=='pair' else orders[key][::-1];choice=raw['response']['choice'];selected=names[0] if choice=='LEFT' else names[1] if choice=='RIGHT' else 'baseline' if choice=='tie' else None
  chosen=resolved.get(key+'_'+selected) if selected else {'checks':[],'review':{'status':'insufficient_evidence','issues':[],'unresolved_questions':[raw['response']['comparison']]}}
  if chosen is None:errors[rid]=['selected invalid candidate'];continue
  resolved[rid]=chosen;rows.append(dict(run_id=rid,case_id=p['case_id'],world=p['world'],choice=choice,display_order=names,selected_candidate=selected,review=chosen['review'],checks=chosen['checks']))
operational={}
for phase in ['baseline','critic','alternate','pair','pair_swapped']:
 calls=[x for x in records if x['phase']==phase]
 operational[phase]={'physical_calls':len(calls),'transport_ok':sum(x['status']=='ok' for x in calls),'valid_calls':sum(x['run_id'] not in errors for x in calls),'tool_calls':sum(x['tool_audit']['tool_call_count'] for x in calls),'input_tokens':sum(u.get('input_tokens',0) for x in calls for u in x['usage']),'cached_input_tokens':sum(u.get('cached_input_tokens',0) for x in calls for u in x['usage']),'output_tokens':sum(u.get('output_tokens',0) for x in calls for u in x['usage']),'duration_seconds_sum':sum(x['duration_seconds'] for x in calls),'usage_missing':sum(not x['usage'] for x in calls)}
audit={'frozen_files_unchanged':all(digest((ROOT/p).read_text())==h for p,h in f['files'].items()),'source_cap_valid':all(read(p)['source_tokens']<=f['source_cap'] for p in (R/'packets').glob('*.json')),'operational':operational,'invalid':errors,'selections':rows,'resolved_outputs':resolved}
(R/'audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2)+'\n');print(json.dumps({k:v for k,v in audit.items() if k not in ['selections','resolved_outputs']},indent=2))
