"""Post-execution integrity, source coverage and cost audit; never used for retrieval."""
import json,re,sys
from pathlib import Path
from collections import Counter
R=Path(__file__).resolve().parent;ROOT=R.parents[1];sys.path.insert(0,str(ROOT))
from ace_review.context_adaptive import digest
read=lambda p:json.loads(p.read_text())
freeze=read(R/'freeze.json');labels=read(ROOT/'runs/context_fresh6_20260929/experiment_v1/private/labels.json');labels.update(read(R/'holdout/labels.json'));targets=read(ROOT/'runs/context_fresh6_20260929/evaluation_targets.json')
rows=[];coverage=[];calls=[read(p) for p in sorted((R/'raw').glob('*.json')) if not p.name.endswith('.trace.json')];byid={r['run_id']:r for r in calls}
for p in sorted((R/'packets').glob('*.json')):
 packet=read(p);cid=packet['case_id'];world=packet['world'];label=labels[cid]['worlds'][world]
 target=targets[cid][label] if cid.startswith('F') else {'file':'deployment.py','start':3,'end':4}
 for arm in ['L','D','SELF2','CRITIC']:
  sourcearm='D' if arm=='D' else 'L';state=packet['D_audit']['state'] if sourcearm=='D' else packet['L_audit'];text=packet[sourcearm];covered=set()
  for key,lines in state['source_lines'].items():
   header=text.split('['+key+'] ')[1].split('\n')[0]
   if header.startswith('head '+target['file']+':'):covered.update(lines)
  complete=set(range(target['start'],target['end']+1)).issubset(covered)
  rid=cid+'_'+world+'_'+('critic_final' if arm=='CRITIC' else arm);raw=byid.get(rid);invalid=list((R/'invalid').glob(rid+'*.json')) if (R/'invalid').exists() else []
  coverage.append(dict(run_id=rid,stage=packet['stage'],case_id=cid,world=world,label=label,arm=arm,complete_world_fact=complete,visible_world_lines=sorted(covered),response=raw['response'] if raw else None,operational_status=raw['status'] if raw and not invalid else ('invalid' if invalid else 'blocked')))
summary={}
for stage in ['development','near_holdout']:
 c=[x for x in calls if x['stage']==stage];summary[stage]={'physical_calls':len(c),'ok':sum(x['status']=='ok' for x in c),'errors':sum(x['status']!='ok' for x in c),'observed_tool_calls':sum(x['tool_audit']['tool_call_count'] for x in c),'input_tokens':sum(u.get('input_tokens',0) for x in c for u in x['usage']),'cached_input_tokens':sum(u.get('cached_input_tokens',0) for x in c for u in x['usage']),'output_tokens':sum(u.get('output_tokens',0) for x in c for u in x['usage']),'calls_without_usage':sum(not x['usage'] for x in c),'logical_arm_cost':{}}
 for arm,phases in {'L':['L'],'D':['D'],'SELF2':['L','self1','SELF2'],'CRITIC':['L','critic','critic_final']}.items():
  a=[x for x in c if x['phase'] in phases];summary[stage]['logical_arm_cost'][arm]={'calls':len(a),'input_tokens':sum(u.get('input_tokens',0) for x in a for u in x['usage']),'output_tokens':sum(u.get('output_tokens',0) for x in a for u in x['usage']),'calls_without_usage':sum(not x['usage'] for x in a)}
audit={'frozen_files_unchanged':all(digest((ROOT/p).read_text())==h for p,h in freeze['source_and_input_sha256'].items()),'source_budget_valid':all(read(p)['L_audit']['tokens']<=1600 and read(p)['D_audit']['state']['tokens']<=1600 for p in (R/'packets').glob('*.json')),'witnesses_match':all(x['expected_exit']==x['actual_exit'] for x in read(R/'witnesses.json')),'operational':summary,'coverage':coverage,'failures':[{'run_id':x['run_id'],'error':x['error'],'returncode':x['returncode']} for x in calls if x['status']!='ok'],'invalid_files':[str(p.relative_to(R)) for p in (R/'invalid').glob('*.json')] if (R/'invalid').exists() else []}
(R/'audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2));print(json.dumps({k:v for k,v in audit.items() if k!='coverage'},ensure_ascii=False,indent=2))
