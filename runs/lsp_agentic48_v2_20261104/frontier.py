"""Keep raw pending obligations independently of atomic ledger acceptance.

This controller validates IDs, current routes, coverage and source kinds. All
support/refutation, necessity, and causal relevance remain model judgments.
"""
from __future__ import annotations
import copy,hashlib,json
from collections import Counter
import jsonschema
from common import digest,source_lines
from schemas import CLAIM
from planner_schema import PLAN
from claim_controller import ClaimController
from diff48 import diff_evidence

SOURCE_KINDS={'source_lines','implementation_body','exception_branch','body'}
IMPLEMENTATION_UNITS={'function','class','closure','method','function_literal'}

def request_key(q):
 return tuple(q.get(k)for k in('snapshot','file','line','column','query','symbol'))

def _unique(values):return list(dict.fromkeys(values))

def evidence_capability(e,kind,snapshot=None,literal_facts=()):
 """Mechanical compatibility only, never the requested API/path identity."""
 if snapshot and e.get('snapshot')!=snapshot:return False
 source=e.get('kind')in SOURCE_KINDS;checks=e.get('mechanical_checks',{})
 if kind=='literal_diff_fact':return e.get('kind')=='diff_hunk'and any(f.get('mechanically_valid')is True and f.get('evidence_id')==e.get('id')and f.get('snapshot')==e.get('snapshot')and f.get('file')==e.get('path')for f in literal_facts)
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

def _all_targets(claims):
 """Identity catalog includes archived records; only _targets selects work."""
 out={};live=_targets(claims)
 for c in claims:
  for p in c['conditions']:
   key=c['id'],'condition',p['id'];out[key]={'kind':p['kind'],'snapshot':p['snapshot'],'predicate':p['predicate'],'model_status':p['status'],
    'role':'current'if key in live else'withdrawn_claim'if c['action']=='withdraw'else'historical_condition'}
  for ch in c['challenges']:
   key=c['id'],'challenge',ch['id'];out[key]={'kind':None,'snapshot':None,'predicate':ch['text'],'model_status':ch['state'],
    'role':'current'if key in live else'withdrawn_claim'if c['action']=='withdraw'else'closed_challenge'}
 return out

def validate_diff_facts(rows,claims,evidence):
 """Extract exact visible rows, not a claim's meaning or an execution proof."""
 targets=_all_targets(claims);bindings=[];errors=[];seen=set();parsed={}
 for f in rows:
  key=f['claim_id'],'condition',f['condition_id'];target=targets.get(key);problems=[]
  if not f['id'].strip()or f['id']in seen:problems.append('missing_or_duplicate_fact_id')
  seen.add(f['id'])
  if target is None:problems.append('unknown_condition')
  elif target['kind']!='literal_diff_fact':problems.append('condition_not_literal_diff_fact')
  if not f['reason'].strip():problems.append('empty_fact_reason')
  e=evidence.get(f['evidence_id']);nums=[]
  if e is None:problems.append('unknown_evidence_id')
  elif e.get('kind')!='diff_hunk':problems.append('not_diff_evidence')
  else:
   if e.get('snapshot')!=f['snapshot']or target is not None and target['snapshot']!=f['snapshot']:problems.append('wrong_snapshot')
   if e.get('path')!=f['file']:problems.append('wrong_path')
   # IDs bind exact section bytes and a one-sided physical map. Reparse the
   # supplied diff rather than trusting a model or an enclosing min/max span.
   if e['id']not in parsed:
    candidates=diff_evidence(e.get('content',''));canonical=next((x for x in candidates if x['id']==e['id']),None)
    parsed[e['id']]=canonical is not None and all(canonical[k]==e.get(k)for k in('id','snapshot','path','start_line','end_line','content','physical_rows'))
   if not parsed[e['id']]:problems.append('diff_identity_or_physical_map_mismatch')
   a,b=f['start_line'],f['end_line']
   if a<1 or b<a:problems.append('invalid_range')
   elif b-a+1>64:problems.append('literal_fact_range_exceeds_64_rows')
   elif b-a+1>len(e.get('physical_rows',{}))or not all(str(n)in e.get('physical_rows',{})for n in range(a,b+1)):problems.append('range_contains_unsupplied_diff_rows')
   else:nums=list(range(a,b+1))
  if problems:
   errors.append({'code':'invalid_literal_diff_fact','fact_id':f['id'],'target':key,'problems':problems});continue
  physical=[{'line':n,'text':e['physical_rows'][str(n)]}for n in nums];literal='\n'.join(x['text']for x in physical)
  bindings.append({**copy.deepcopy(f),'physical_rows':physical,'literal_text':literal,'literal_sha256':hashlib.sha256(literal.encode()).hexdigest(),
   'parent_evidence_sha256':digest(e),'mechanically_valid':True,'semantic_truth_guaranteed':False,
   'mechanical_truth_scope':'Exact public diff identity, snapshot/path and supplied contiguous physical rows only; no complete-body, interprocedural, external-API, runtime or causal proof.'})
 return bindings,errors

def _fact_matches(f,cid,pid,eid):return f.get('claim_id')==cid and f.get('condition_id')==pid and f.get('evidence_id')==eid and f.get('mechanically_valid')is True

class Frontier:
 def __init__(self,initial_payload,evidence,controller=None,changed_operations=()):
  self.evidence={e['id']:copy.deepcopy(e)for e in evidence};self.raw_payload=copy.deepcopy(initial_payload or{'claims':[],'questions':[]})
  self.raw_claims=copy.deepcopy(self.raw_payload.get('claims',[]));self.initial_raw_claim_count=len(self.raw_claims)if isinstance(self.raw_claims,list)else 1;self.structural_errors=_structure(self.raw_claims)
  self.changed_operations={x['id']:copy.deepcopy(x)for x in changed_operations};self.cache={};self.events=[];self.versions=[]
  self.diff_fact_bindings=[];self.archived_coverage=[];self.current_coverage=[];self.selection_incomplete=False
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
   'pending_target_source':'controller-derived current conditions/pending challenges from latest structurally valid raw model claims, independently of atomic ledger acceptance',
   'archived_targets':[{'claim_id':k[0],'target_kind':k[1],'target_id':k[2],**v}for k,v in _all_targets(self.raw_claims).items()if v['role']!='current']if not self.structural_errors else[],
   'current_coverage':copy.deepcopy(self.current_coverage),'archived_coverage':copy.deepcopy(self.archived_coverage),'selection_incomplete':self.selection_incomplete,
   'diff_fact_bindings':copy.deepcopy(self.diff_fact_bindings),
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
  if errors:return {'accepted':False,'complete':False,'errors':errors,'semantic_truth_guaranteed':False}
  if not isinstance(self.raw_claims,list):return {'accepted':False,'complete':False,'errors':[{'code':'invalid_initial_claim_container_requires_explicit_repair'}],'semantic_truth_guaranteed':False}
  claims=plan['claims'];errors.extend(_structure(claims));new={c['id']:c for c in claims};reclassifications=[]
  for old in self.raw_claims:
   if not isinstance(old,dict):errors.append({'code':'invalid_raw_claim_requires_explicit_repair'});continue
   cid=old.get('id')
   if not isinstance(cid,str):errors.append({'code':'invalid_raw_claim_id_requires_explicit_repair'});continue
   if cid not in new:errors.append({'code':'raw_claim_omitted','claim_id':cid});continue
   for field in('conditions','challenges'):
    olditems=old.get(field,[])if isinstance(old.get(field,[]),list)else[]
    oldids={x.get('id')for x in olditems if isinstance(x,dict)and isinstance(x.get('id'),str)};newids={x['id']for x in new[cid][field]}
    if not oldids<=newids:errors.append({'code':'raw_'+field+'_omitted','claim_id':cid,'ids':sorted(oldids-newids)})
   oldconditions={x['id']:x for x in old.get('conditions',[])if isinstance(x,dict)and isinstance(x.get('id'),str)}
   for condition in new[cid]['conditions']:
    prior=oldconditions.get(condition['id'])
    if prior and prior.get('kind')!=condition['kind']:
     change={'claim_id':cid,'condition_id':condition['id'],'old_kind':prior.get('kind'),'new_kind':condition['kind'],'reason':condition['reason'],'change_basis':new[cid]['change_basis'],'semantic_reclassification_verified':False}
     reclassifications.append(change)
     if new[cid]['change_basis']=='none' or not condition['reason'].strip():errors.append({'code':'condition_kind_change_without_basis',**change})
  bindings,failures=validate_diff_facts(plan['diff_facts'],claims,self.evidence);errors.extend(failures)
  byfact={f['id']:copy.deepcopy(f)for f in self.diff_fact_bindings}
  for f in bindings:
   if f['id']in self.evidence:errors.append({'code':'diff_fact_id_collides_with_evidence','fact_id':f['id']})
   if f['id']in byfact and byfact[f['id']]!=f:errors.append({'code':'diff_fact_id_conflict','fact_id':f['id']})
   byfact[f['id']]=f
  bindings=list(byfact.values())
  targets=_targets(claims);catalog=_all_targets(claims);questions={q['id']:q for q in plan['questions']};coverage={};archived=[];seen=set();explorations={x['id']:x for x in plan['explorations']}
  if len(questions)!=len(plan['questions']):errors.append({'code':'duplicate_question_id'})
  if len(explorations)!=len(plan['explorations']):errors.append({'code':'duplicate_exploration_id'})
  for row in plan['coverage']:
   key=row['claim_id'],row['target_kind'],row['target_id'];target=catalog.get(key)
   if key in seen:errors.append({'code':'duplicate_coverage','target':key})
   seen.add(key)
   if target is None:errors.append({'code':'coverage_unknown_target','target':key});continue
   if not row['reason'].strip():errors.append({'code':'empty_coverage_reason','target':key})
   unknown=[eid for eid in row['evidence_ids']if eid not in self.evidence]
   if unknown:errors.append({'code':'coverage_unknown_evidence','target':key,'ids':unknown})
   wrong=[eid for eid in row['evidence_ids']if eid in self.evidence and target['snapshot'] is not None and self.evidence[eid]['snapshot']!=target['snapshot']]
   if wrong:errors.append({'code':'coverage_wrong_snapshot','target':key,'ids':wrong})
   if key not in targets:
    if row['mode']=='query' or row['question_ids']:errors.append({'code':'archived_coverage_cannot_query','target':key})
    if row['mode']=='reuse'and not row['evidence_ids']:errors.append({'code':'archived_reuse_without_evidence','target':key})
    archived.append({**copy.deepcopy(row),'archive_role':target['role'],'target_snapshot':target['snapshot'],
     'execution':'historical_record_only_no_query','archive_record_committed':False,'evidence_bindings':[{'id':eid,'snapshot':self.evidence[eid]['snapshot'],'path':self.evidence[eid]['path'],'kind':self.evidence[eid]['kind']}for eid in row['evidence_ids']if eid in self.evidence],
     'semantic_truth_guaranteed':False})
    continue
   coverage[key]={**copy.deepcopy(row),'selection_origin':'model_explicit_current_coverage'}
   if row['mode']=='query':
    if not row['question_ids']:errors.append({'code':'query_coverage_without_question','target':key})
    for qid in row['question_ids']:
     q=questions.get(qid);field='condition_ids'if key[1]=='condition'else'challenge_ids'
     if q is None or q['claim_id']!=key[0]or key[2]not in q[field]:errors.append({'code':'coverage_question_binding_mismatch','target':key,'question_id':qid})
   else:
    if row['question_ids']:errors.append({'code':'nonquery_coverage_has_question','target':key})
    if row['mode']=='reuse':
     fs=[f for f in bindings if f['claim_id']==key[0]and f['condition_id']==key[2]]
     compatible=[eid for eid in row['evidence_ids']if eid in self.evidence and(target['kind']is None or evidence_capability(self.evidence[eid],target['kind'],target['snapshot'],fs))]
     if not compatible:errors.append({'code':'reuse_lacks_mechanically_compatible_evidence','target':key,'required_kind':target['kind']})
  # IDs and work frontier come from the controller. Explicit question bindings
  # are sufficient intent; redundant coverage bookkeeping is not a second
  # opportunity to lose a query. No new question or source is invented.
  derived=[];unselected=[]
  for key in sorted(set(targets)-set(coverage)):
   field='condition_ids'if key[1]=='condition'else'challenge_ids'
   qs=[q for q in plan['questions']if not q['exploration_id']and q['claim_id']==key[0]and key[2]in q[field]]
   row={'claim_id':key[0],'target_kind':key[1],'target_id':key[2],'mode':'query'if qs else'defer','question_ids':[q['id']for q in qs],
    'evidence_ids':[],'reason':' | '.join(q['reason']for q in qs)if qs else'Controller preserved required target with no explicit model query/reuse/defer selection; unresolved, not verified.',
    'selection_origin':'controller_from_explicit_question_links'if qs else'controller_unselected_placeholder'}
   coverage[key]=row
   if qs:derived.append(copy.deepcopy(row))
   else:unselected.append({'claim_id':key[0],'target_kind':key[1],'target_id':key[2],'required_kind':targets[key]['kind'],'snapshot':targets[key]['snapshot'],'execution':'not_selected_unresolved'})
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
     elif coverage[key]['mode']!='query'or q['id']not in coverage[key]['question_ids']:errors.append({'code':'question_missing_corresponding_coverage','question_id':q['id'],'target':key})
  for c in claims:
   for condition in c['conditions']:
    if condition['kind']=='literal_diff_fact'and condition['status']in('supported','refuted'):
     fs=[f for f in bindings if f['claim_id']==c['id']and f['condition_id']==condition['id']]
     if not any(eid in self.evidence and evidence_capability(self.evidence[eid],'literal_diff_fact',condition['snapshot'],fs)for eid in condition['evidence_ids']):errors.append({'code':'literal_condition_without_verified_diff_span','claim_id':c['id'],'condition_id':condition['id']})
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
   if preview['required']and preview['controller_update'].get('accepted')is not True:errors.append({'code':'retirement_requires_accepted_controller_preview','retirements':preview['retirements'],'pending_frontier_preserved':True})
  return {'accepted':not errors,'complete':not errors and not unselected,'selection_incomplete':bool(unselected),'errors':errors,
   'required_targets':len(targets),'covered_targets':len(coverage)-len(unselected),'current_coverage':list(coverage.values()),'archived_coverage':archived,
   'derived_selection':derived,'unselected':unselected,'diff_fact_bindings':bindings,'kind_reclassifications':reclassifications,
   'retirement_preview':preview,'semantic_truth_guaranteed':False}

 def register_cached_answer(self,question,answer):
  ids=_unique(answer.get('selected_evidence_ids',[])+answer.get('already_available_evidence_ids',[])+[e['id']for e in answer.get('evidence',[])])
  missing=[eid for eid in ids if eid not in self.evidence]
  if missing:raise ValueError('cached_answer_evidence_not_available:'+','.join(missing))
  self.cache[request_key(question)]={'question':copy.deepcopy(question),'answer':copy.deepcopy(answer),'all_evidence_ids':ids}

 def check_literal_conditions(self,payload):
  """Final aux check; does not rewrite/filter a finding or infer its truth."""
  errors=[];checked=[]
  for c in payload.get('claims',[]):
   for p in c.get('conditions',[]):
    if p.get('kind')!='literal_diff_fact':continue
    facts=[f for f in self.diff_fact_bindings if f['claim_id']==c['id']and f['condition_id']==p['id']]
    ids=[eid for eid in p.get('evidence_ids',[])if eid in self.evidence and evidence_capability(self.evidence[eid],'literal_diff_fact',p['snapshot'],facts)]
    row={'claim_id':c['id'],'condition_id':p['id'],'model_status':p['status'],'compatible_evidence_ids':ids,'diff_fact_ids':[f['id']for f in facts],'semantic_truth_guaranteed':False}
    checked.append(row)
    if p['status']in('supported','refuted')and not ids:errors.append({'code':'literal_condition_without_verified_diff_span',**row})
  return {'accepted':not errors,'errors':errors,'conditions':checked,'semantic_truth_guaranteed':False,'scope':'Binding to previously controller-extracted literal public rows, not truth of the condition predicate.'}

 def execute_plan(self,plan,backend=None,enable_queries=True):
  validation=self.validate_plan(plan)
  if not validation['accepted']:return {'accepted':False,'complete':False,'validation':validation,'controller_update':None,'answers':[],'coverage':[],
   'archived_coverage':validation.get('archived_coverage',[]),'diff_fact_bindings':validation.get('diff_fact_bindings',[]),
   'new_evidence_ids':[],'selected_evidence_ids':[],'frontier':self.export(),'planner_incomplete':True,'automatic_seed_fallback':False,'semantic_truth_guaranteed':False}
  try:update=self.controller.update({'claims':plan['claims'],'questions':plan['questions']},len(self.versions))
  except Exception as error:update={'accepted':False,'errors':[{'code':'planner_controller_exception','type':type(error).__name__,'message':str(error)}]}
  if validation['retirement_preview']['required']and update.get('accepted')is not True:
   validation['accepted']=False;validation['complete']=False;validation['errors'].append({'code':'retirement_actual_commit_rejected','pending_frontier_preserved':True})
   self.versions.append({'stage':'planner_retirement_rejected','raw_claims':copy.deepcopy(self.raw_claims),'proposed_claims':copy.deepcopy(plan['claims']),'controller_update':copy.deepcopy(update),'retirement_preview':copy.deepcopy(validation['retirement_preview'])})
   return {'accepted':False,'complete':False,'validation':validation,'controller_update':update,'answers':[],'coverage':[],
    'archived_coverage':validation['archived_coverage'],'diff_fact_bindings':validation['diff_fact_bindings'],
    'new_evidence_ids':[],'selected_evidence_ids':[],'frontier':self.export(),'planner_incomplete':True,'automatic_seed_fallback':False,'semantic_truth_guaranteed':False}
  self.raw_claims=copy.deepcopy(plan['claims']);self.structural_errors=[];self.versions.append({'stage':'planner','raw_claims':copy.deepcopy(self.raw_claims),'controller_update':copy.deepcopy(update)})
  self.diff_fact_bindings=copy.deepcopy(validation['diff_fact_bindings']);self.current_coverage=copy.deepcopy(validation['current_coverage']);self.selection_incomplete=validation['selection_incomplete']
  committed_archive=[{**copy.deepcopy(row),'archive_record_committed':True,'controller_update_accepted':update.get('accepted')}for row in validation['archived_coverage']]
  self.archived_coverage.extend({'round':len(self.versions)-1,**row}for row in committed_archive)
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
  for row in validation['current_coverage']:
   record=copy.deepcopy(row);record['semantic_status']='model_assessed_unverified'
   if row['mode']=='query':record['execution']='withheld'if not enable_queries else'answered_or_unresolved';record['answer_evidence_ids']=_unique([eid for qid in row['question_ids']for eid in byq[qid]['all_answer_evidence_ids']]);record['query_executions']=[byq[qid]['execution']for qid in row['question_ids']]
   else:record['execution']='existing_evidence_selected'if row['mode']=='reuse'else'controller_unselected_unresolved'if row['selection_origin']=='controller_unselected_placeholder'else'explicitly_deferred';record['answer_evidence_ids']=list(row['evidence_ids']);selected.extend(row['evidence_ids'])
   t=_targets(self.raw_claims)[row['claim_id'],row['target_kind'],row['target_id']]
   record['required_kind']=t['kind'];record['required_snapshot']=t['snapshot']
   fs=[f for f in self.diff_fact_bindings if f['claim_id']==row['claim_id']and f['condition_id']==row['target_id']]
   record['mechanically_compatible_evidence_ids']=[eid for eid in record['answer_evidence_ids']if eid in self.evidence and(t['kind']is None or evidence_capability(self.evidence[eid],t['kind'],t['snapshot'],fs))]
   record['required_evidence_mechanically_available']=bool(record['mechanically_compatible_evidence_ids'])
   coverage.append(record)
  output={'accepted':True,'complete':validation['complete'],'selection_incomplete':validation['selection_incomplete'],'validation':validation,'controller_update':update,'answers':answers,'coverage':coverage,
   'archived_coverage':committed_archive,'diff_fact_bindings':copy.deepcopy(self.diff_fact_bindings),'derived_selection':copy.deepcopy(validation['derived_selection']),'unselected':copy.deepcopy(validation['unselected']),
   'new_evidence_ids':_unique(newids),'selected_evidence_ids':_unique(selected),
   'backend_query_attempts':native_attempts,'cache_hits':sum(a['cache_hit']for a in answers),'frontier':self.export(),'semantic_truth_guaranteed':False,'automatic_seed_fallback':False}
  self.events.append(copy.deepcopy(output));return output
