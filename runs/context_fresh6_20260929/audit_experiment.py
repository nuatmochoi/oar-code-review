"""Independent frozen-input, prompt, budget, source and controller audit."""
from collections import Counter,defaultdict
import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
import jsonschema
from ace_review.context_adaptive import Corpus,EvidenceState,digest,validate_controller,QUESTION_SCHEMA,FINAL_SCHEMA
from ace_review.obligation import token_count
R=Path(__file__).resolve().parent;OUT=R/'experiment_v1';read=lambda p:json.loads(p.read_text());errors=[]
freeze=read(OUT/'freeze.json')
for p,h in freeze['files'].items():
 if digest((OUT/p).read_text())!=h:errors.append('freeze:'+p)
for p,h in freeze['workspace_policy_hashes'].items():
 if digest((ROOT/p).read_text())!=h:errors.append('workspace_policy:'+p)
for name in ['proposal_packets','controller1_packets','controller2_packets','final_packets']:
 if digest(read(OUT/(name+'.json')))!=read(OUT/(name+'.freeze.json'))['sha256']:errors.append('phase_freeze:'+name)
if (OUT/'assessment_freeze.json').exists():
 for f,h in read(OUT/'assessment_freeze.json')['files'].items():
  if digest((R/f).read_text())!=h:errors.append('assessment_freeze:'+f)
labels=read(OUT/'private/labels.json');facts=read(R/'evaluation_targets.json');retrieval=read(OUT/'retrieval.json');states={};coverage=[];packet_checks=[];ppr=Counter()
for row in read(OUT/'cases.json'):
 case=read(OUT/row['file']);cid=case['case_id']
 for world,spec in case['worlds'].items():
  corpus=Corpus(spec['versions'],spec['graphs'],case['diff'])
  entries=[r for r in retrieval if r['case_id']==cid and r['world']==world]
  initial=next((r for r in entries if r['arm']=='shared'),None)
  for entry in entries:
   arm=entry['arm'];prior={};prior_paths={}
   rounds=entry['rounds']
   if arm in ('S','S_priority'):rounds=initial['rounds']+rounds
   for index,item in enumerate(rounds):
    state=EvidenceState(corpus);a=item['state'];state.lines={k:set(v) for k,v in a['source_lines'].items()};state.paths=a['paths']
    if state.tokens!=a['tokens'] or state.tokens>item['cap'] or digest(state.render())!=a['packet_sha256']:errors.append(f'packet:{cid}:{world}:{arm}:{index}')
    if any(not state.lines.get(k,set())>=v for k,v in prior.items()):errors.append(f'source_loss:{cid}:{world}:{arm}')
    if any(state.paths.get(k)!=v for k,v in prior_paths.items()):errors.append(f'path_loss:{cid}:{world}:{arm}')
    for k,lines in state.lines.items():
     u=corpus.units[k]
     if not lines<=set(range(u.start,u.end+1)):errors.append('span:'+k)
    for q in item.get('queries',[]):
     if 'converged' in q:ppr['attempts']+=1;ppr['nonconverged']+=not q['converged']
    states[cid,world,arm,index]=state
    packet_checks.append({'case_id':cid,'world':world,'arm':arm,'round':index,'tokens':state.tokens,'cap':item['cap']})
    prior,prior_paths=state.lines,state.paths
   if arm!='shared':
    fact=facts[cid][labels[cid]['worlds'][world]];seen=set()
    for k,lines in state.lines.items():
     u=corpus.units[k]
     if u.file==fact['file'] and u.version=='head':seen.update(lines)
    needed=set(range(fact['start'],fact['end']+1))
    coverage.append({'case_id':cid,'world':world,'arm':arm,'world_label':labels[cid]['worlds'][world],'fact_file':fact['file'],'fact_symbol':fact['symbol'],'complete_world_fact':needed<=seen,'fact_lines_seen':len(needed&seen),'fact_lines_required':len(needed),'last_retrieval_round':len(rounds)-1,'source_line_map_sha256':digest(state.audit()['source_lines'])})
records=[read(p) for p in (OUT/'raw').glob('*.json') if not p.name.endswith('.trace.json')]
questions={r['case_id']:r['response']['questions'] for r in read(OUT/'proposals.json') if r['status']=='ok'}
full=read(OUT/'controller.schema.json');usage=Counter();by_arm=defaultdict(Counter);transport=[]
for r in records:
 rid=r['run_id'];i=read(OUT/'inputs'/(rid+'.json'))
 if digest(i['prompt'])!=r['prompt_sha256'] or token_count(i['prompt'])!=r['prompt_reference_tokens']:errors.append('prompt:'+rid)
 if r['tool_audit']['tool_call_count']:errors.append('tools:'+rid)
 if r['status']!='ok':transport.append(rid)
 phase=r['phase'];arm=r.get('arm','questions');by_arm[arm]['attempted_calls']+=1
 by_arm[arm]['successful_calls']+=r['status']=='ok';by_arm[arm]['duration_seconds_sum']+=r['duration_seconds']
 for x in r.get('usage',[]):
  for k,v in x.items():
   if isinstance(v,(int,float)):usage[k]+=v;by_arm[arm][k]+=v
 if phase!='questions':
  index=0 if phase=='controller1' or arm=='L' else (1 if phase=='controller2' else 2)
  state=states[r['case_id'],r['world'],arm,index]
  if digest(state.render())!=r['evidence_sha256'] or not i['prompt'].endswith(state.render()):errors.append('visible_source:'+rid)
 if r['status']!='ok':continue
 response=r['response'];schema=QUESTION_SCHEMA if phase=='questions' else FINAL_SCHEMA if phase=='final' else full
 errors.extend(rid+':schema:'+e.message for e in jsonschema.Draft202012Validator(schema).iter_errors(response))
 if phase.startswith('controller'):
  errors.extend(rid+':'+e for e in validate_controller(response,state,questions[r['case_id']]))
  bound=read(OUT/'schemas'/(rid+'.json'));errors.extend(rid+':bound:'+e.message for e in jsonschema.Draft202012Validator(bound).iter_errors(response))
 elif phase=='final':
  if (response['status']=='reported_issues')!=bool(response['issues']):errors.append('inconsistent_final:'+rid)
  for issue in response['issues']:
   if not set(issue['evidence_ids'])<=set(state.lines)|{'DIFF'}:errors.append('final_citation:'+rid)
execution=read(OUT/'execution.json')
if len(records)!=execution['attempted_calls']:errors.append('call_accounting')
finals=read(OUT/'final_packets.json');same_world=[];same_arms=[]
index={(f['case_id'],f['world'],f['arm']):f for f in finals}
for cid in labels:
 for arm in ['L','S','S_priority']:
  a=index.get((cid,'a',arm));b=index.get((cid,'b',arm))
  if a and b and a['prompt']==b['prompt']:same_world.append({'case_id':cid,'arm':arm})
 for w in ['a','b']:
  a=index.get((cid,w,'S'));b=index.get((cid,w,'S_priority'))
  if a and b and a['prompt']==b['prompt']:same_arms.append({'case_id':cid,'world':w})
result={'integrity_valid':not errors,'errors':errors,'planned_calls':78,'attempted_calls':len(records),'successful_calls':sum(r['status']=='ok' for r in records),'transport_failures':transport,'calls_without_usage':sum(not r.get('usage') for r in records),'observed_tool_calls':sum(r['tool_audit']['tool_call_count'] for r in records),'reported_usage':dict(usage),'by_arm':{k:dict(v) for k,v in by_arm.items()},'packet_checks':packet_checks,'world_fact_coverage':coverage,'same_safe_unsafe_final_prompts':same_world,'same_S_priority_final_prompts':same_arms,'ppr_diagnostics_including_repeated_shared_prefix_audit':dict(ppr),'execution':execution,'scope':'six derived component families, no significance or natural-PR claim'}
(R/'audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps({k:result[k] for k in ['integrity_valid','errors','attempted_calls','successful_calls','transport_failures','observed_tool_calls','reported_usage']}))
