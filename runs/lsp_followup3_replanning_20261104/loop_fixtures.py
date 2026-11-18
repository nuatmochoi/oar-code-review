import copy
def evidence(eid='E-api',path='api.py',kind='function'):
 text='def adapt(value):\n    return value'if path=='api.py'else'def consume(value):\n    return value.metadata'
 return {'id':eid,'snapshot':'HEAD','path':path,'start_line':1,'end_line':2,'content':text,'kind':'implementation_body','physical_rows':[{'line':i,'text':s}for i,s in enumerate(text.splitlines(),1)],'mechanical_checks':{'complete_unit':True,'unit_kind':kind}}

def condition(cid,kind='implementation_body',status='unresolved',ids=()):
 return {'id':cid,'predicate':'The API or downstream consumer may access a missing metadata attribute.','kind':kind,'snapshot':'HEAD','status':status,'evidence_ids':list(ids),'reason':'This condition needs an actual implementation and downstream source inspection.','effect_if_true':'The proposed failure can occur under the stated input.','effect_if_false':'This route must be revised or rejected.'}

def claim():
 return {'id':'C1','claim':'The adapter itself raises when metadata is absent.','trigger':'A callable has no metadata attribute.','action':'new','change_basis':'none','reason':'Initial source-level candidate; semantic correctness is not established.',
  'conditions':[condition('P-api',status='supported',ids=['E-api']),condition('P-consumer','value_path')],
  'challenges':[{'id':'CH-consumer','text':'Does a later consumer, rather than the adapter, dereference metadata?','state':'pending','evidence_ids':[],'reason':'Inspect the downstream consumer path.'}],
  'route_assignment':{'current_condition_ids':['P-api','P-consumer'],'historical':[]},'termination':'continue'}

def question(qid='Q-consumer'):
 return {'id':qid,'claim_id':'C1','kind':'implementation_body','query':'references','snapshot':'HEAD','file':'api.py','line':1,'column':5,'symbol':'adapt','reason':'Find the consumer implementation to test the remaining current condition and challenge.',
  'condition_ids':['P-consumer'],'challenge_ids':['CH-consumer'],'exploration_id':''}

def plan(revised=True):
 c=claim()
 if revised:
  c.update(claim='A downstream consumer dereferences absent metadata.',action='revise',change_basis='interpretation_error',reason='The existing adapter implementation returns the value unchanged, so the prior immediate-failure explanation is wrong.')
  c['conditions'][0].update(status='refuted',reason='The complete existing adapter body returns value without reading metadata.')
  c['route_assignment']={'current_condition_ids':['P-consumer'],'historical':[{'condition_id':'P-api','reason':'The immediate adapter failure assumption was an interpretation error; inspect the later consumer.','basis':'interpretation_error','evidence_ids':['E-api']}]}
 coverage=[{'claim_id':'C1','target_kind':k,'target_id':tid,'mode':'query','question_ids':['Q-consumer'],'evidence_ids':[],'reason':'Inspect the actual downstream implementation.'}for k,tid in[('condition','P-consumer'),('challenge','CH-consumer')]]
 if not revised:coverage.append({'claim_id':'C1','target_kind':'condition','target_id':'P-api','mode':'reuse','question_ids':[],'evidence_ids':['E-api'],'reason':'Reuse the already delivered complete adapter implementation.'})
 return {'claims':[c],'questions':[question()],'coverage':coverage,'explorations':[],'diff_facts':[],'reasoning':'Retain the historical refutation and inspect the current consumer route.','termination_reason':'Planned evidence acquisition.'}

class Backend:
 def __init__(self):self.calls=0
 def query(self,q):
  self.calls+=1
  return {'question':q,'evidence':[evidence('E-consumer','consumer.py')],'selected_evidence_ids':['E-api','E-consumer'],'already_available_evidence_ids':['E-api'],'cost':{'native_calls':1},'unresolved':[]}

