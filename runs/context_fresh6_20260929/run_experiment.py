"""Three-arm experiment; frozen source inputs and no retries or policy changes."""
from concurrent.futures import ThreadPoolExecutor
import json,random,sys,copy
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
import jsonschema
from ace_review.context_adaptive import Corpus,EvidenceState,digest,retrieve_round,PROPOSAL_PROMPT,CONTROLLER_PROMPT,FINAL_PROMPT,QUESTION_SCHEMA,FINAL_SCHEMA,validate_controller
from ace_review.context_adaptive_run import ask,common
from ace_review.context_gap_priority import priority_step,bound_controller_schema
from ace_review.auto_context import changed_query
from ace_review.counterfactual import write_new
R=Path(__file__).resolve().parent;OUT=R/'experiment_v1'
freeze=json.loads((OUT/'freeze.json').read_text())
for name,h in freeze['files'].items():assert digest((OUT/name).read_text())==h,name
for name,h in freeze['workspace_policy_hashes'].items():assert digest((ROOT/name).read_text())==h,name
assert digest((R/'witness_results.json').read_text())==freeze['witness_sha256']
(OUT/'raw').mkdir(exist_ok=False)
cases=[json.loads((OUT/c['file']).read_text()) for c in json.loads((OUT/'cases.json').read_text())]
controller_schema=json.loads((OUT/'controller.provider.schema.json').read_text());full=json.loads((OUT/'controller.schema.json').read_text())
failures=[];retrieval=[];contexts={}
def parallel(rows,fn):
 with ThreadPoolExecutor(max_workers=4) as pool:return list(pool.map(fn,rows))
def invoke(row,schema):return ask(OUT,row['run_id'],row['prompt'],schema,{k:v for k,v in row.items() if k not in ('run_id','prompt')})
def save_phase(name,rows):write_new(OUT/(name+'.json'),rows);write_new(OUT/(name+'.freeze.json'),{'sha256':digest(rows)})
def controller_row(cid,w,arm,phase,state,case,questions):
 return {'run_id':f'{cid}_{w}_{arm}_{phase}','case_id':cid,'world':w,'arm':arm,'phase':phase,'prompt':CONTROLLER_PROMPT+common(case)+'\nQUESTIONS:\n'+json.dumps(questions)+'\nVISIBLE EVIDENCE:\n'+state.render(),'evidence_tokens':state.tokens,'evidence_sha256':digest(state.render())}
def validate(r,state,questions):
 errors=[]
 if r['status']=='ok':
  errors=[e.message for e in jsonschema.Draft202012Validator(full).iter_errors(r['response'])]+validate_controller(r['response'],state,questions)
 if r['status']!='ok' or errors:
  failures.append({'run_id':r['run_id'],'status':r['status'],'errors':errors});return False
 return True
def ctrl_invoke(row):
 state,case,questions=contexts[row['case_id'],row['world'],row['arm']]
 schema=bound_controller_schema(controller_schema,state,questions)
 write_new(OUT/'schemas'/(row['run_id']+'.json'),schema)
 return invoke(row,schema)
def clone_state(state):
 result=EvidenceState(state.corpus);result.lines={k:set(v) for k,v in state.lines.items()};result.paths=copy.deepcopy(state.paths);return result
proposals=[{'run_id':c['case_id']+'_questions','case_id':c['case_id'],'phase':'questions','prompt':PROPOSAL_PROMPT+common(c)} for c in cases]
save_phase('proposal_packets',proposals)
results=parallel(proposals,lambda row:invoke(row,QUESTION_SCHEMA))
write_new(OUT/'proposals.json',results)
qs={r['case_id']:r for r in results};first=[];finals=[];corpora={}
for case in cases:
 cid=case['case_id'];proposal=qs[cid]
 if proposal['status']!='ok':failures.append({'run_id':proposal['run_id'],'status':proposal['status']});continue
 questions=proposal['response']['questions']
 if [q['id'] for q in questions] != [f'Q{i+1}' for i in range(len(questions))]:
  failures.append({'run_id':proposal['run_id'],'status':'invalid_question_ids'});continue
 for world,spec in case['worlds'].items():
  corpus=Corpus(spec['versions'],spec['graphs'],case['diff']);corpora[cid,world]=corpus
  lexical=EvidenceState(corpus);audit=retrieve_round(corpus,lexical,questions,changed_query(case['diff']),'L',0)
  retrieval.append({'case_id':cid,'world':world,'arm':'L','rounds':[audit]});contexts[cid,world,'L']=lexical,case,questions
  initial=EvidenceState(corpus);audit=retrieve_round(corpus,initial,questions,changed_query(case['diff']),'S',0)
  retrieval.append({'case_id':cid,'world':world,'arm':'shared','rounds':[audit]});contexts[cid,world,'shared']=initial,case,questions
  first.append(controller_row(cid,world,'shared','controller1',initial,case,questions))
save_phase('controller1_packets',first);write_new(OUT/'initial_retrieval.json',retrieval)
first_results=parallel(first,ctrl_invoke);second=[]
for r in first_results:
 cid,world=r['case_id'],r['world'];initial,case,questions=contexts[cid,world,'shared']
 if not validate(r,initial,questions):continue
 for arm in ('S','S_priority'):
  state=clone_state(initial);corpus=state.corpus
  audit=retrieve_round(corpus,state,questions,changed_query(case['diff']),'S',1,r['response']) if arm=='S' else priority_step(corpus,state,questions,changed_query(case['diff']),r['response'],1200)
  retrieval.append({'case_id':cid,'world':world,'arm':arm,'rounds':[audit]});contexts[cid,world,arm]=state,case,questions
  second.append(controller_row(cid,world,arm,'controller2',state,case,questions))
save_phase('controller2_packets',second);write_new(OUT/'first_update_retrieval.json',retrieval)
second_results=parallel(second,ctrl_invoke)
ready={(cid,w,'L') for cid,w,a in contexts if a=='L'}
for r in second_results:
 cid,world,arm=r['case_id'],r['world'],r['arm'];state,case,questions=contexts[cid,world,arm]
 if not validate(r,state,questions):continue
 corpus=state.corpus
 audit=retrieve_round(corpus,state,questions,changed_query(case['diff']),'S',2,r['response']) if arm=='S' else priority_step(corpus,state,questions,changed_query(case['diff']),r['response'],1600)
 next(x for x in retrieval if (x['case_id'],x['world'],x['arm'])==(cid,world,arm))['rounds'].append(audit)
 ready.add((cid,world,arm))
for cid,world,arm in sorted(ready):
 state,case,questions=contexts[cid,world,arm]
 finals.append({'run_id':f'{cid}_{world}_{arm}_final','case_id':cid,'world':world,'arm':arm,'phase':'final', 'prompt':FINAL_PROMPT+common(case)+'\nQUESTIONS:\n'+json.dumps(questions)+'\nREPOSITORY EVIDENCE:\n'+state.render(),'evidence_tokens':state.tokens,'evidence_ids':sorted(state.lines),'evidence_sha256':digest(state.render())})
random.Random(20260929).shuffle(finals)
save_phase('final_packets',finals);write_new(OUT/'retrieval.json',retrieval);write_new(OUT/'controller_failures.json',failures)
predictions=parallel(finals,lambda row:invoke(row,FINAL_SCHEMA));invalid=[]
for r in predictions:
 if r['status']!='ok':invalid.append({'run_id':r['run_id'],'transport':r['status']});continue
 response=r['response'];errors=[]
 if (response['status']=='reported_issues') != bool(response['issues']):errors.append('status_issue_inconsistency')
 for i in response['issues']:
  if not set(i['evidence_ids']) <= set(r['evidence_ids'])|{'DIFF'}:errors.append('unknown_evidence_id')
 if errors:invalid.append({'run_id':r['run_id'],'errors':errors})
write_new(OUT/'predictions.json',predictions);write_new(OUT/'execution.json',{'planned_calls':78,'attempted_calls':len(results)+len(first_results)+len(second_results)+len(predictions),'final_calls':len(predictions),'controller_failures':failures,'final_invalid':invalid})
print(json.dumps({'done':True,'finals':len(predictions),'controller_failures':len(failures),'invalid_finals':len(invalid)}))
