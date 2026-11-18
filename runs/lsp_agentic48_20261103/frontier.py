"""Keep raw pending obligations independently of atomic ledger acceptance.

This controller validates IDs, current routes, coverage and source kinds. All
support/refutation, necessity, and causal relevance remain model judgments.
"""
from __future__ import annotations
import copy,json
from collections import Counter
import jsonschema
from common import digest,source_lines
from schemas import CLAIM
from planner_schema import PLAN
from claim_controller import ClaimController

SOURCE_KINDS={'source_lines','implementation_body','exception_branch','body'}
IMPLEMENTATION_UNITS={'function','class','closure','method','function_literal'}

def request_key(q):
 return tuple(q.get(k)for k in('snapshot','file','line','column','query','symbol'))

def _unique(values):return list(dict.fromkeys(values))

def evidence_capability(e,kind,snapshot=None):
 """Mechanical compatibility only, never the requested API/path identity."""
 if snapshot and e.get('snapshot')!=snapshot:return False
 source=e.get('kind')in SOURCE_KINDS;checks=e.get('mechanical_checks',{})
 if kind=='implementation_body':return source and checks.get('complete_unit')is True and checks.get('unit_kind')in IMPLEMENTATION_UNITS
 if kind=='exception_branch':return source and checks.get('complete_unit')is True
 if kind=='definition_location':return source or e.get('kind')=='definition_location'
 if kind=='value_path':return source or e.get('kind')=='value_path'
 if kind=='dependency_version':return source or e.get('kind')in('environment_record','dependency_version')
 if kind=='runtime_observation':return e.get('kind')=='runtime_observation'and checks.get('runtime_observed')is True
 return False

def _structure(claims):
 if not isinstance(claims,list):return [{'code':'raw_claims_not_array'}]
 errors=[];ids=[]
 for c in claims:
  cid=c.get('id')if isinstance(c,dict)else None;ids.append(cid)
  claim_errors=list(jsonschema.Draft202012Validator(CLAIM).iter_errors(c))
  for error in claim_errors:errors.append({'code':'invalid_raw_claim_schema','claim_id':cid,'path':list(error.path),'message':error.message})
  if claim_errors:continue
  if not isinstance(c,dict):continue
  conditions=c.get('conditions',[]);assignment=c.get('route_assignment')or{}
  if not isinstance(conditions,list)or not isinstance(assignment,dict):continue
  byid={p.get('id'):p for p in conditions if isinstance(p,dict)}
  current=assignment.get('current_condition_ids',[]);hist=assignment.get('historical',[])
  if not isinstance(current,list)or not isinstance(hist,list):continue
  historical=[h.get('condition_id')for h in hist if isinstance(h,dict)]
  if len(byid)!=len(conditions)or len(set(current))!=len(current)or len(set(historical))!=len(historical)or set(current)&set(historical)or set(current)|set(historical)!=set(byid):errors.append({'code':'route_not_exact_unique_partition','claim_id':cid})
  challenge_ids=[x.get('id')for x in c.get('challenges',[])if isinstance(x,dict)]if isinstance(c.get('challenges',[]),list)else[]
  if len(set(challenge_ids))!=len(challenge_ids):errors.append({'code':'duplicate_raw_challenge','claim_id':cid})
 if any(not isinstance(x,str)or not x for x in ids)or len(set(ids))!=len(ids):errors.append({'code':'invalid_or_duplicate_raw_claim_id'})
 return errors

def _targets(claims):
 out={}
 for c in claims:
  if c.get('action')=='withdraw':continue
  current=set(c['route_assignment']['current_condition_ids'])
  for p in c['conditions']:
   if p['id']in current:out[c['id'],'condition',p['id']]={'kind':p['kind'],'snapshot':p['snapshot'],'predicate':p['predicate'],'model_status':p['status']}
  for challenge in c['challenges']:
   if challenge['state']=='pending':out[c['id'],'challenge',challenge['id']]={'kind':None,'snapshot':None,'predicate':challenge['text'],'model_status':'pending'}
 return out

class Frontier:
 def __init__(self,initial_payload,evidence,controller=None,changed_operations=()):
  self.evidence={e['id']:copy.deepcopy(e)for e in evidence};self.raw_payload=copy.deepcopy(initial_payload or{'claims':[],'questions':[]})
  self.raw_claims=copy.deepcopy(self.raw_payload.get('claims',[]));self.initial_raw_claim_count=len(self.raw_claims)if isinstance(self.raw_claims,list)else 1;self.structural_errors=_structure(self.raw_claims)
  self.changed_operations={x['id']:copy.deepcopy(x)for x in changed_operations};self.cache={};self.events=[];self.versions=[]
  self.controller=controller if controller is not None else ClaimController(evidence,mapping_mode='active')
  self.initial_update=None
  if controller is None:
   try:self.initial_update=self.controller.update(self.raw_payload,0)
   except Exception as error:self.initial_update={'accepted':False,'errors':[{'code':'initial_controller_exception','type':type(error).__name__,'message':str(error)}]}
  elif getattr(controller,'updates',[]):self.initial_update=copy.deepcopy(controller.updates[-1])
  self.versions.append({'stage':'initial','raw_claims':copy.deepcopy(self.raw_claims),'structural_errors':copy.deepcopy(self.structural_errors),'controller_update':copy.deepcopy(self.initial_update)})

 def export(self):
  targets=_targets(self.raw_claims)if not self.structural_errors else{}
  accepted_routes=copy.deepcopy(getattr(self.controller,'declared_route_assignments',{}));accepted_challenges={}
  ledger=getattr(self.controller,'ledger',None)
  if ledger is not None:accepted_challenges={cid:{x.id:x.state for x in c.challenges.values()}for cid,c in ledger.candidates.items()}
  shape_warnings=[]
  for c in self.raw_claims if isinstance(self.raw_claims,list)and not self.structural_errors else[]:
   if not isinstance(c,dict):continue
   for p in c.get('conditions',[])if isinstance(c.get('conditions',[]),list)else[]:
    if isinstance(p,dict)and p.get('status')in('supported','refuted')and p.get('kind')=='implementation_body'and not any(eid in self.evidence and evidence_capability(self.evidence[eid],p['kind'],p.get('snapshot'))for eid in p.get('evidence_ids',[])if isinstance(eid,str)):
     shape_warnings.append({'claim_id':c.get('id'),'condition_id':p.get('id'),'code':'model_implementation_judgment_lacks_complete_implementation_unit'})
  return {'raw_claims':copy.deepcopy(self.raw_claims),'initial_raw_claim_count':self.initial_raw_claim_count,'structural_errors':copy.deepcopy(self.structural_errors),
   'pending_targets':[{'claim_id':k[0],'target_kind':k[1],'target_id':k[2],**v,'semantic_status':'model_assessed_unverified'}for k,v in targets.items()],
   'changed_operations':copy.deepcopy(list(self.changed_operations.values())),'controller_initial_update':copy.deepcopy(self.initial_update),
   'accepted_controller_route_assignments':accepted_routes,'accepted_controller_challenge_states':accepted_challenges,'implementation_shape_warnings':shape_warnings,
   'pending_target_source':'latest structurally valid raw model claims, independently of atomic ledger acceptance',
   'cache_entries':len(self.cache),'raw_frontier_preserved_despite_atomic_ledger_rejection':True,'automatic_seed_fallback':False,
   'scope':'Structural pending frontier, not accepted causal truth. Full raw claims survive atomic rejection; invalid structures remain visible and require explicit repair.'}

 def _body_binding(self,anchor):
  e=self.evidence.get(anchor.get('evidence_id'))
  if not e or e.get('snapshot')!=anchor.get('snapshot')or e.get('path')!=anchor.get('file'):return False
  physical=e.get('physical_rows',[])
  nums={int(n)for n in physical}if isinstance(physical,dict)else{r['line']for r in physical if isinstance(r,dict)and'line'in r}
  return anchor.get('line')in nums

 def _retirements(self,claims):
  """Identify pending targets a proposed raw payload would hide."""
  new={c['id']:c for c in claims};changes=[]
  for old in self.raw_claims:
   if not isinstance(old,dict)or old.get('id')not in new:continue
   cid=old['id'];current=new[cid]
   if current['action']=='withdraw'and old.get('action')!='withdraw':
    changes.append({'kind':'claim_withdrawal','claim_id':cid})
   previous=old.get('route_assignment')or{}
   prior=previous.get('current_condition_ids',[])if isinstance(previous,dict)else[]
   prior={x for x in prior if isinstance(x,str)}if isinstance(prior,list)else set()
   after=set(current['route_assignment']['current_condition_ids'])
   for pid in sorted(prior-after):changes.append({'kind':'current_condition_retirement','claim_id':cid,'condition_id':pid})
   challenges={x['id']:x for x in current['challenges']}
   for x in old.get('challenges',[])if isinstance(old.get('challenges',[]),list)else[]:
    if isinstance(x,dict)and x.get('state')=='pending'and x.get('id')in challenges and challenges[x['id']]['state']!='pending':
     changes.append({'kind':'pending_challenge_termination','claim_id':cid,'challenge_id':x['id'],'new_state':challenges[x['id']]['state']})
  return changes

 def _retirement_preview(self,plan):
  changes=self._retirements(plan['claims'])
  preview={'required':bool(changes),'retirements':changes,'execution_scope':'deepcopy_controller_only; not an actual committed update','semantic_truth_guaranteed':False,'controller_update':None}
  if changes:
   try:preview['controller_update']=copy.deepcopy(self.controller).update({'claims':plan['claims'],'questions':plan['questions']},len(self.versions))
   except Exception as error:preview['controller_update']={'accepted':False,'errors':[{'code':'retirement_preview_exception','type':type(error).__name__,'message':str(error)}]}
  return preview

 def validate_plan(self,plan):
  errors=[]
  for error in jsonschema.Draft202012Validator(PLAN).iter_errors(plan):errors.append({'code':'invalid_plan_schema','path':list(error.path),'message':error.message})
  if errors:return {'accepted':False,'errors':errors,'semantic_truth_guaranteed':False}
  if not isinstance(self.raw_claims,list):return {'accepted':False,'errors':[{'code':'invalid_initial_claim_container_requires_explicit_repair'}],'semantic_truth_guaranteed':False}
  claims=plan['claims'];errors.extend(_structure(claims));new={c['id']:c for c in claims}
  for old in self.raw_claims:
   if not isinstance(old,dict):errors.append({'code':'invalid_raw_claim_requires_explicit_repair'});continue
   cid=old.get('id')
   if not isinstance(cid,str):errors.append({'code':'invalid_raw_claim_id_requires_explicit_repair'});continue
   if cid not in new:errors.append({'code':'raw_claim_omitted','claim_id':cid});continue
   for field in('conditions','challenges'):
    olditems=old.get(field,[])if isinstance(old.get(field,[]),list)else[]
    oldids={x.get('id')for x in olditems if isinstance(x,dict)and isinstance(x.get('id'),str)};newids={x['id']for x in new[cid][field]}
    if not oldids<=newids:errors.append({'code':'raw_'+field+'_omitted','claim_id':cid,'ids':sorted(oldids-newids)})
  targets=_targets(claims);questions={q['id']:q for q in plan['questions']};coverage={};explorations={x['id']:x for x in plan['explorations']}
  if len(questions)!=len(plan['questions']):errors.append({'code':'duplicate_question_id'})
  if len(explorations)!=len(plan['explorations']):errors.append({'code':'duplicate_exploration_id'})
  for row in plan['coverage']:
   key=row['claim_id'],row['target_kind'],row['target_id']
   if key in coverage:errors.append({'code':'duplicate_coverage','target':key})
   coverage[key]=row
   if key not in targets:errors.append({'code':'coverage_target_not_live','target':key});continue
   if not row['reason'].strip():errors.append({'code':'empty_coverage_reason','target':key})
   unknown=[eid for eid in row['evidence_ids']if eid not in self.evidence]
   if unknown:errors.append({'code':'coverage_unknown_evidence','target':key,'ids':unknown})
   if row['mode']=='query':
    if not row['question_ids']:errors.append({'code':'query_coverage_without_question','target':key})
    for qid in row['question_ids']:
     q=questions.get(qid);field='condition_ids'if key[1]=='condition'else'challenge_ids'
     if q is None or q['claim_id']!=key[0]or key[2]not in q[field]:errors.append({'code':'coverage_question_binding_mismatch','target':key,'question_id':qid})
   else:
    if row['question_ids']:errors.append({'code':'nonquery_coverage_has_question','target':key})
    if row['mode']=='reuse':
     t=targets[key];compatible=[eid for eid in row['evidence_ids']if eid in self.evidence and(t['kind']is None or evidence_capability(self.evidence[eid],t['kind'],t['snapshot']))]
     if not compatible:errors.append({'code':'reuse_lacks_mechanically_compatible_evidence','target':key,'required_kind':t['kind']})
  missing=set(targets)-set(coverage)
  if missing:errors.append({'code':'required_frontier_coverage_missing','targets':sorted(missing)})
  for q in plan['questions']:
   if len(set(q['condition_ids']))!=len(q['condition_ids'])or len(set(q['challenge_ids']))!=len(q['challenge_ids']):errors.append({'code':'duplicate_question_target_binding','question_id':q['id']})
   if not q['id'].strip()or not q['reason'].strip():errors.append({'code':'question_id_or_reason_empty','question_id':q['id']})
   if q['line']<1 or q['column']<0:errors.append({'code':'question_position_invalid','question_id':q['id']})
   if q['exploration_id']:
    x=explorations.get(q['exploration_id'])
    if q['condition_ids']or q['challenge_ids']or q['claim_id']!='exploration'or x is None or q['id']not in x['question_ids']:errors.append({'code':'invalid_exploration_question_binding','question_id':q['id']})
   else:
    links=[(q['claim_id'],'condition',x)for x in q['condition_ids']]+[(q['claim_id'],'challenge',x)for x in q['challenge_ids']]
    if not links:errors.append({'code':'question_not_bound_to_frontier','question_id':q['id']})
    for key in links:
     if key not in targets:errors.append({'code':'question_target_not_live','question_id':q['id'],'target':key})
     elif key not in coverage or coverage[key]['mode']!='query'or q['id']not in coverage[key]['question_ids']:errors.append({'code':'question_missing_corresponding_coverage','question_id':q['id'],'target':key})
  for x in plan['explorations']:
   if self.initial_raw_claim_count or claims:errors.append({'code':'exploration_forbidden_when_candidates_exist','exploration_id':x['id']})
   expected=self.changed_operations.get(x['id']);fields=('snapshot','file','line','column','operation','evidence_id')
   if expected is None or any(expected.get(k)!=x[k]for k in fields)or not self._body_binding(x):errors.append({'code':'exploration_not_bound_to_declared_changed_operation','exploration_id':x['id']})
   if not x['reason'].strip()or not x['question_ids']:errors.append({'code':'exploration_missing_reason_or_question','exploration_id':x['id']})
   for qid in x['question_ids']:
    if qid not in questions or questions[qid]['exploration_id']!=x['id']:errors.append({'code':'exploration_question_mismatch','exploration_id':x['id'],'question_id':qid})
  preview={'required':False,'retirements':[],'execution_scope':'not run because structural validation failed','controller_update':None,'semantic_truth_guaranteed':False}
  if not errors:
   preview=self._retirement_preview(plan)
   if preview['required']and preview['controller_update'].get('accepted')is not True:
    errors.append({'code':'retirement_requires_accepted_controller_preview','retirements':preview['retirements'],'pending_frontier_preserved':True})
  return {'accepted':not errors,'errors':errors,'required_targets':len(targets),'covered_targets':len(set(targets)&set(coverage)),'retirement_preview':preview,'semantic_truth_guaranteed':False}

 def register_cached_answer(self,question,answer):
  ids=_unique(answer.get('selected_evidence_ids',[])+answer.get('already_available_evidence_ids',[])+[e['id']for e in answer.get('evidence',[])])
  missing=[eid for eid in ids if eid not in self.evidence]
  if missing:raise ValueError('cached_answer_evidence_not_available:'+','.join(missing))
  self.cache[request_key(question)]={'question':copy.deepcopy(question),'answer':copy.deepcopy(answer),'all_evidence_ids':ids}

 def execute_plan(self,plan,backend=None,enable_queries=True):
  validation=self.validate_plan(plan)
  if not validation['accepted']:return {'accepted':False,'validation':validation,'controller_update':None,'answers':[],'coverage':[],'new_evidence_ids':[],'selected_evidence_ids':[],'frontier':self.export(),'planner_incomplete':True,'automatic_seed_fallback':False,'semantic_truth_guaranteed':False}
  try:update=self.controller.update({'claims':plan['claims'],'questions':plan['questions']},len(self.versions))
  except Exception as error:update={'accepted':False,'errors':[{'code':'planner_controller_exception','type':type(error).__name__,'message':str(error)}]}
  if validation['retirement_preview']['required']and update.get('accepted')is not True:
   validation['accepted']=False;validation['errors'].append({'code':'retirement_actual_commit_rejected','pending_frontier_preserved':True})
   self.versions.append({'stage':'planner_retirement_rejected','raw_claims':copy.deepcopy(self.raw_claims),'proposed_claims':copy.deepcopy(plan['claims']),'controller_update':copy.deepcopy(update),'retirement_preview':copy.deepcopy(validation['retirement_preview'])})
   return {'accepted':False,'validation':validation,'controller_update':update,'answers':[],'coverage':[],'new_evidence_ids':[],'selected_evidence_ids':[],'frontier':self.export(),'planner_incomplete':True,'automatic_seed_fallback':False,'semantic_truth_guaranteed':False}
  self.raw_claims=copy.deepcopy(plan['claims']);self.structural_errors=[];self.versions.append({'stage':'planner','raw_claims':copy.deepcopy(self.raw_claims),'controller_update':copy.deepcopy(update)})
  answers=[];newids=[];selected=[];native_attempts=0
  for q in plan['questions']:
   key=request_key(q)
   if not enable_queries:answer={'question':copy.deepcopy(q),'evidence':[],'selected_evidence_ids':[],'already_available_evidence_ids':[],'unresolved':[{'reason':'query_withheld_same_source_control'}],'cost':{},'execution':'withheld','cache_hit':False}
   elif key in self.cache:
    cached=self.cache[key];original=cached['answer'];ids=list(cached['all_evidence_ids'])
    answer={'question':copy.deepcopy(q),'evidence':[],'selected_evidence_ids':ids,'already_available_evidence_ids':ids,'cached_original_new_evidence_ids':[e['id']for e in original.get('evidence',[])],
     'unresolved':copy.deepcopy(original.get('unresolved',[])),'cost':{},'execution':'cached_answer_reused','cache_hit':True,'original_question':copy.deepcopy(cached['question']),'original_query_receipt':original.get('query_receipt')}
   elif backend is None:answer={'question':copy.deepcopy(q),'evidence':[],'selected_evidence_ids':[],'already_available_evidence_ids':[],'unresolved':[{'reason':'query_backend_unavailable'}],'cost':{},'execution':'unresolved','cache_hit':False}
   else:
    native_attempts+=1
    try:answer=copy.deepcopy(backend.query(q));answer.update(execution='backend_query',cache_hit=False)
    except Exception as error:answer={'question':copy.deepcopy(q),'evidence':[],'selected_evidence_ids':[],'already_available_evidence_ids':[],'unresolved':[{'reason':'backend_query_exception','type':type(error).__name__,'message':str(error)}],'cost':{},'execution':'unresolved','cache_hit':False}
    for e in answer.get('evidence',[]):
     if e['id']in self.evidence:assert self.evidence[e['id']]==e
     else:self.evidence[e['id']]=copy.deepcopy(e);newids.append(e['id'])
    self.controller.add_evidence(answer.get('evidence',[]));self.register_cached_answer(q,answer)
   ids=_unique(answer.get('selected_evidence_ids',[])+answer.get('already_available_evidence_ids',[])+[e['id']for e in answer.get('evidence',[])])
   answer['all_answer_evidence_ids']=ids;answer['mechanically_compatible_evidence_ids']=[eid for eid in ids if eid in self.evidence and evidence_capability(self.evidence[eid],q['kind'],q['snapshot'])]
   answer['question_binding']={'claim_id':q['claim_id'],'condition_ids':list(q['condition_ids']),'challenge_ids':list(q['challenge_ids']),'exploration_id':q['exploration_id'],'live_structural_binding_valid':True,'semantic_relevance_guaranteed':False}
   if q['kind']=='implementation_body'and not answer['mechanically_compatible_evidence_ids']:answer.setdefault('unresolved',[]).append({'reason':'actual_complete_implementation_not_available','complete_statement_is_not_implementation':True})
   selected.extend(ids);answers.append(answer)
  coverage=[];byq={q['question']['id']:q for q in answers}
  for row in plan['coverage']:
   record=copy.deepcopy(row);record['semantic_status']='model_assessed_unverified'
   if row['mode']=='query':record['execution']='withheld'if not enable_queries else'answered_or_unresolved';record['answer_evidence_ids']=_unique([eid for qid in row['question_ids']for eid in byq[qid]['all_answer_evidence_ids']]);record['query_executions']=[byq[qid]['execution']for qid in row['question_ids']]
   else:record['execution']='existing_evidence_selected'if row['mode']=='reuse'else'explicitly_deferred';record['answer_evidence_ids']=list(row['evidence_ids']);selected.extend(row['evidence_ids'])
   t=_targets(self.raw_claims)[row['claim_id'],row['target_kind'],row['target_id']]
   record['required_kind']=t['kind'];record['required_snapshot']=t['snapshot']
   record['mechanically_compatible_evidence_ids']=[eid for eid in record['answer_evidence_ids']if eid in self.evidence and(t['kind']is None or evidence_capability(self.evidence[eid],t['kind'],t['snapshot']))]
   record['required_evidence_mechanically_available']=bool(record['mechanically_compatible_evidence_ids'])
   coverage.append(record)
  output={'accepted':True,'validation':validation,'controller_update':update,'answers':answers,'coverage':coverage,'new_evidence_ids':_unique(newids),'selected_evidence_ids':_unique(selected),
   'backend_query_attempts':native_attempts,'cache_hits':sum(a['cache_hit']for a in answers),'frontier':self.export(),'semantic_truth_guaranteed':False,'automatic_seed_fallback':False}
  self.events.append(copy.deepcopy(output));return output
