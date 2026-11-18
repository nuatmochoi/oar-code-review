"""Actual failure-directed reads under the original native/source/time caps.

All children remain static candidates. Mechanically obtaining source never
promotes the model's causal premise to supported/refuted.
"""
import copy,time
from pathlib import PurePosixPath
from step_sources import ContextCollector as Previous
from context48 import digest,put,Limit,SOURCE_KINDS
from frontier import evidence_capability,request_key

RECOVERY_INSTRUCTION='''Tool answers can include actual failure-directed child reads and return/delegation-call source candidates. Inspect the recovery receipt and actual evidence; a recovered source candidate is not automatically the requested API, a value-flow proof, or a refutation. The original failed query remains recorded. Distinguish needed implementation identity, source availability, candidate relation and semantic resolution. Use the next planning call to assess the original question and ask a discriminating next fact when the recovered source is insufficient. Do not treat a mechanically available body as a resolved causal premise.'''

class ContextCollector(Previous):
 def __init__(self,*args,initial_state=None,**kwargs):
  super().__init__(*args,initial_state=initial_state,**kwargs)
  self.recovery_state=copy.deepcopy((initial_state or{}).get('recovery_state',dict(goals=[],visited=[],pending=[])))
  self.stats['recovery_child_queries']=(initial_state or{}).get('cost',{}).get('recovery_child_queries',0)
 def export(self):return super().export()|dict(recovery_state=copy.deepcopy(self.recovery_state))
 def node_key(self,q):
  progress=[]
  if q['query']=='search':
   for state in self.search_sessions.values():
    if state['request']['snapshot']==q['snapshot']and state['request']['scope']==(q.get('file')or'.')and state['request']['literal']==q.get('symbol'):
     progress=[state['file_cursor'],len(state['delivered']),len(state['pending'])];break
  return digest([request_key(q),progress])
 def recipes(self,q,answer):
  selected=set(answer.get('selected_evidence_ids',[])+answer.get('already_available_evidence_ids',[]))
  es=[e for e in self.evidence if e['id']in selected];binding=answer.get('callee_binding')or{};out=[]
  available=any(evidence_capability(e,q['kind'],q['snapshot'])for e in es)
  if not available and q.get('symbol')and q['query']!='search':
   out.append((0,'failed_fact_discovery',dict(q,file=q.get('file')or'.',line=1,column=0,query='search')))
  # A search may have a useful later page. It stays queued rather than being
  # hard-discarded; shared file/native/source/deadline limits still terminate.
  if q['query']=='search':
   for state in self.search_sessions.values():
    if state['request']['snapshot']==q['snapshot']and state['request']['scope']==(q.get('file')or'.')and state['request']['literal']==q.get('symbol')and(not state['scan_complete']or state['pending']):out.append((3,'remaining_search_frontier',copy.deepcopy(q)));break
  # Follow actual static call locations in bodies returned by a native binding.
  # Literal-search candidates never acquire that binding through this rule.
  bound=set(binding.get('target_body_ids',[]))
  if q['query']=='body':
   for e in es:
    if e['path']!=q['file']or not e.get('mechanical_checks',{}).get('complete_unit')or not self.language_for(e['path']):continue
    try:
     data=self._parse(e['snapshot'],e['path']);unit=self._unit_at(data,q['line'],definition=True)
     if unit and unit['name']==q.get('symbol','').rsplit('.',1)[-1]:bound.add(e['id'])
    except(ValueError,OSError,RuntimeError):pass
  if q['kind']in('implementation_body','exception_branch','value_path')and bound:
   for e in es:
    if e['id']not in bound or not self.language_for(e['path']):continue
    try:
     data=self._parse(e['snapshot'],e['path']);unit=next((u for u in data['units']if u['start_line']==e['start_line']and u['end_line']==e['end_line']),None)
     if not unit:continue
     calls=[x for x in data['calls']if unit['start_line']<=x['start_line']<=unit['end_line']]
     for call in calls:
      row=data['rows'][call['start_line']-1].strip();priority=1 if row.startswith('return ')or 'raise 'in row else 2
      out.append((priority,'static_return_or_delegate_call_candidate',dict(q,snapshot=e['snapshot'],file=e['path'],line=call.get('line',call['start_line']),column=call.get('column',0),symbol=call['name'],query='definition')))
    except(ValueError,OSError,RuntimeError)as exc:self.unresolved.append(dict(reason='recovery_delegate_expansion_unresolved',detail=str(exc)))
  return out
 def set_planned_questions(self,questions):self.plan_questions=copy.deepcopy(questions)
 def query(self,q,**kwargs):
  pending=[x for x in getattr(self,'plan_questions',[])if x['id']!=q['id']]
  self.plan_questions=pending;count=1+len(pending);old=self.budgets['max_file_reads']
  self.budgets['max_file_reads']=min(old,self.stats['file_reads']+max(1,(old-self.stats['file_reads'])//count))
  try:
   answer=self._query_goal(q,**kwargs)
   answer['goal_recovery']['shared_plan_goals']=count
   answer['goal_recovery']['temporary_file_slice']=self.budgets['max_file_reads']
   answer['goal_recovery']['global_file_limit']=old
   # Persist the complete replay input after adding the scheduling receipt.
   path=self.out/f'goal-scheduled-{self.sequence:04d}.json';put(path,{k:v for k,v in answer.items()if k!='query_receipt'});answer['query_receipt']=str(path)
   return answer
  finally:self.budgets['max_file_reads']=old
 def _query_goal(self,q,**kwargs):
  started=time.monotonic();deadline=started+self.budgets['max_query_seconds'];before=copy.deepcopy(self.stats);oldids={e['id']for e in self.evidence};original=copy.deepcopy(q);root=super().query(q,**kwargs);receipts=[root]
  fact=digest(request_key(q));resume=[x for x in self.recovery_state['pending']if x.get('root_fact_key')==fact];self.recovery_state['pending']=[x for x in self.recovery_state['pending']if x.get('root_fact_key')!=fact]
  queue=[(x['priority'],x['relation'],x['question'])for x in resume]+self.recipes(q,root);visited=set(self.recovery_state['visited']);visited.add(self.node_key(q));children=[];stop='frontier_exhausted'
  while queue:
   queue.sort(key=lambda x:x[0]);priority,relation,child=queue.pop(0);key=self.node_key(child)
   if key in visited:continue
   remaining=deadline-time.monotonic()
   if remaining<=0:queue.insert(0,(priority,relation,child));stop='shared_query_deadline';break
   if self.stats['file_reads']>=self.budgets['max_file_reads']or self.stats['native_calls']>=self.budgets['max_native_calls']-2:
    queue.insert(0,(priority,relation,child));stop='shared_native_or_file_budget';break
   visited.add(key);child=copy.deepcopy(child);child['id']=q['id']+'-R'+str(len(children)+1);cap=self.budgets['max_query_seconds'];self.budgets['max_query_seconds']=min(cap,remaining)
   try:answer=super().query(child,**kwargs)
   finally:self.budgets['max_query_seconds']=cap
   self.stats['recovery_child_queries']+=1;receipts.append(answer);children.append(dict(relation=relation,question=child,receipt=answer['query_receipt'],binding=answer.get('callee_binding'),evidence_ids=answer.get('selected_evidence_ids',[]),unresolved=answer.get('unresolved',[]),semantic_resolution='not_inferred'))
   queue.extend(self.recipes(child,answer))
  selected=list(dict.fromkeys(x for a in receipts for x in a.get('selected_evidence_ids',[])+a.get('already_available_evidence_ids',[])))
  sources=[e for e in self.evidence if e['id']in selected];compatible=[e['id']for e in sources if evidence_capability(e,q['kind'],q['snapshot'])]
  pending=[dict(priority=p,relation=r,question=x,root_fact_key=fact)for p,r,x in queue]
  goal=dict(question_id=q['id'],needed_kind=q['kind'],original_receipt=root['query_receipt'],children=children,pending=pending,termination=stop,mechanically_compatible_evidence_ids=compatible,semantic_status='requires_model_assessment',not_causal_or_runtime_proof=True)
  self.recovery_state['goals'].append(goal);self.recovery_state['visited']=sorted(visited);self.recovery_state['pending'].extend(pending)
  answer=copy.deepcopy(root);answer.update(question=original,evidence=[e for e in self.evidence if e['id']not in oldids],selected_evidence_ids=selected,already_available_evidence_ids=[eid for eid in selected if eid in oldids],cost={k:self.stats[k]-before[k]for k in before},unresolved=[u for a in receipts for u in a.get('unresolved',[])],source_tokens_after=__import__('context48').source_tokens(self.evidence),goal_recovery=goal)
  p=self.out/f'goal-query-{self.sequence:04d}.json';put(p,{k:v for k,v in answer.items()if k!='query_receipt'});answer['query_receipt']=str(p);return answer
