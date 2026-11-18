"""Track old warnings, their evidence, and final emission separately from claims.

No source, model, private target, or runtime call occurs here. A current report
is never repaired, replaced with the initial report, or chosen by semantic
quality. Physical source validation is not semantic support/refutation.
"""
from __future__ import annotations
import copy,hashlib,json,re
import jsonschema
from warning_schema import dispositions_schema

SOURCE_KINDS={'source_lines','implementation_body','exception_branch','body'}
DIFF_KINDS={'diff_hunk','diff','diff_rows','diff_source'}
CORE_FIELDS=('title','explanation','trigger','impact')

def digest(value):
 return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
def text_sha(value):return hashlib.sha256(value.encode()).hexdigest()
def source_lines(value):
 rows=value.split('\n')if value else[]
 if rows and rows[-1]=='':rows.pop()
 return [row.removesuffix('\r')for row in rows]

def _rowmap(raw):
 rows={};valid=True
 if isinstance(raw,dict):items=list(raw.items())
 elif isinstance(raw,list):
  items=[(v.get('line'),v.get('text'))for v in raw if isinstance(v,dict)]
  valid=len(items)==len(raw)
 else:return {},False
 for n,text in items:
  if type(n)is not int and not(isinstance(n,str)and n.isdecimal()):valid=False;continue
  try:number=int(n)
  except(TypeError,ValueError):valid=False;continue
  if isinstance(n,bool)or number<1 or number in rows or not isinstance(text,str):valid=False;continue
  rows[number]=text
 return rows,valid

def _diff_rows(text,snapshot,path):
 """Extract actual side/path physical rows; hunk gaps remain gaps."""
 old=new=None;a=b=0;in_hunk=False;rows={}
 for row in source_lines(text):
  if row.startswith('diff --git '):old=new=None;a=b=0;in_hunk=False
  elif not in_hunk and row.startswith('--- '):
   old=row[4:].split('\t')[0].removeprefix('a/');old=None if old=='/dev/null'else old
  elif not in_hunk and row.startswith('+++ '):
   new=row[4:].split('\t')[0].removeprefix('b/');new=None if new=='/dev/null'else new
  elif row.startswith('@@'):
   match=re.match(r'@@ -(\d+)(?:,\d+)? \+(\d+)',row)
   if match:a,b=map(int,match.groups());in_hunk=True
  elif in_hunk and row.startswith(' '):
   if snapshot=='BASE'and old==path and a>0:rows[a]=row[1:]
   if snapshot=='HEAD'and new==path and b>0:rows[b]=row[1:]
   a+=1;b+=1
  elif in_hunk and row.startswith('-'):
   if snapshot=='BASE'and old==path and a>0:rows[a]=row[1:]
   a+=1
  elif in_hunk and row.startswith('+'):
   if snapshot=='HEAD'and new==path and b>0:rows[b]=row[1:]
   b+=1
 return rows

def physical_rows(evidence):
 """Return verified supplied rows, never invent an enclosing sparse span."""
 if not isinstance(evidence,dict):return {},['malformed_evidence']
 kind=evidence.get('kind');text=evidence.get('content');checks=evidence.get('mechanical_checks')or{}
 if not isinstance(text,str):return {},['missing_source_text']
 if checks.get('raw_sha256')and checks['raw_sha256']!=text_sha(text):return {},['source_hash_mismatch']
 raw=evidence.get('physical_rows',checks.get('physical_rows'))
 if kind in DIFF_KINDS:
  supplied,valid=_rowmap(raw)
  actual=_diff_rows(text,evidence.get('snapshot'),evidence.get('path'))
  if not valid or not supplied or any(actual.get(n)!=line for n,line in supplied.items()):return {},['diff_rows_not_exact_visible_source']
  return supplied,[]
 if kind not in SOURCE_KINDS:return {},['metadata_not_physical_source']
 start,end=evidence.get('start_line'),evidence.get('end_line');lines=source_lines(text)
 if type(start)is not int or type(end)is not int or start<1 or end<start or len(lines)!=end-start+1:return {},['source_range_length_mismatch']
 actual=dict(enumerate(lines,start))
 if raw is not None:
  supplied,valid=_rowmap(raw)
  if not valid or supplied!=actual:return {},['source_rows_not_exact_visible_source']
 return actual,[]

class WarningTracker:
 def __init__(self,initial_payload,evidence):
  self.initial_payload=copy.deepcopy(initial_payload)
  findings=(initial_payload or{}).get('findings',[])if isinstance(initial_payload,(dict,type(None)))else[]
  self.initial_structure_errors=[]
  if not isinstance(findings,list):self.initial_structure_errors.append('initial_findings_not_array');findings=[]
  self.initial_findings=copy.deepcopy(findings)
  self.warnings={}
  for i,finding in enumerate(findings):
   warning_id=f'W-{i:03d}-'+digest(finding)[:12]
   self.warnings[warning_id]={'warning_id':warning_id,'initial_finding_index':i,
    'initial_claim_id':finding.get('claim_id')if isinstance(finding,dict)else None,
    'initial_finding_sha256':digest(finding)}
  self.evidence={};self.questions={};self.answers={};self.events=[];self.last_audit=None;self._current_final_ids=None
  self.add_evidence(evidence)
  self.initial_evidence_ids=set(self.evidence)
  self.events.append({'stage':'initial','warning_catalog':self.catalog(),'initial_payload_sha256':digest(initial_payload),'semantic_correctness_assumed':False})

 def catalog(self):
  """Compact pointers; complete old finding text already occurs in input."""
  return copy.deepcopy(list(self.warnings.values()))

 def add_evidence(self,items):
  for evidence in items:
   eid=evidence['id']
   if eid in self.evidence:assert self.evidence[eid]==evidence,'Evidence ID collision; never silently replace source.'
   else:self.evidence[eid]=copy.deepcopy(evidence)

 def record_plan(self,plan,validation=None):
  """Record a proposal, not an implicit warning emission/withdrawal."""
  plan=plan if isinstance(plan,dict)else{}
  for question in plan.get('questions',[]):
   qid=question.get('id')
   if qid:self.questions[qid]=copy.deepcopy(question)
  claims={c.get('id'):c for c in plan.get('claims',[])if isinstance(c,dict)}
  entries=[]
  for wid,warning in self.warnings.items():
   claim=claims.get(warning['initial_claim_id'])
   entries.append({'warning_id':wid,'candidate_action':claim.get('action')if claim else None,
    'candidate_reason':claim.get('reason')if claim else None,
    'question_ids':[q['id']for q in self.questions.values()if q.get('claim_id')==warning['initial_claim_id']],
    'warning_disposition_committed':False})
  event={'stage':'planner_proposal','validation':copy.deepcopy(validation),'warnings':entries,
   'scope':'Candidate changes do not automatically emit, withdraw, merge, or delete warnings.'}
  self.events.append(event);return copy.deepcopy(event)

 def record_answers(self,execution):
  execution=execution or{};records=[]
  for answer in execution.get('answers',[]):
   question=answer.get('question')or{};qid=question.get('id')
   if not qid:continue
   self.questions.setdefault(qid,copy.deepcopy(question));self.add_evidence(answer.get('evidence',[]))
   record={'question_id':qid,'claim_id':question.get('claim_id'),'execution':answer.get('execution'),
    'selected_evidence_ids':list(dict.fromkeys(answer.get('all_answer_evidence_ids',[])+answer.get('selected_evidence_ids',[])+answer.get('already_available_evidence_ids',[])+[e['id']for e in answer.get('evidence',[])])),
    'unresolved':copy.deepcopy(answer.get('unresolved',[])),
    'new_source_ids':[e['id']for e in answer.get('evidence',[])],
    'semantic_support_or_refutation_established':False}
   self.answers[qid]=record;records.append(record)
  event={'stage':'query_outcomes','execution_accepted':execution.get('accepted'),'answers':records,
   'scope':'An empty, withheld, failed, or unresolved lookup does not refute a cause.'}
  self.events.append(event);return copy.deepcopy(event)

 def prompt_state(self):
  return {'initial_warning_catalog':self.catalog(),
   'query_outcomes':copy.deepcopy(list(self.answers.values())),
   'warning_disposition_required_for_every_initial_warning':True,
   'candidate_ledger_is_separate_from_warning_emission':True,
   'mechanical_validation_does_not_guarantee_semantic_truth':True}

 def _reference(self,ref):
  errors=[];e=self.evidence.get(ref.get('evidence_id'))
  if e is None:return {'accepted':False,'errors':['evidence_not_in_actual_input'],'requested':copy.deepcopy(ref)}
  if self._current_final_ids is not None and e['id']not in self._current_final_ids:return {'accepted':False,'errors':['collected_evidence_not_in_actual_final_input'],'requested':copy.deepcopy(ref)}
  if ref.get('snapshot')!=e.get('snapshot'):errors.append('snapshot_mismatch')
  if ref.get('path')!=e.get('path'):errors.append('path_mismatch')
  rows,row_errors=physical_rows(e);errors+=row_errors
  start,end=ref.get('start_line'),ref.get('end_line')
  if type(start)is not int or type(end)is not int or not 1<=start<=end or end-start+1>len(rows)or not set(range(start,end+1))<=set(rows):errors.append('range_not_wholly_in_actual_visible_rows')
  literal='\n'.join(rows[n]for n in range(start,end+1))if not errors else None
  return {'accepted':not errors,'errors':errors,'requested':copy.deepcopy(ref),'evidence_kind':e.get('kind'),
   'evidence_content_sha256':text_sha(e['content'])if isinstance(e.get('content'),str)else None,
   'available_in_initial_input':e['id']in self.initial_evidence_ids,
   'literal_text':literal,'literal_sha256':text_sha(literal)if literal is not None else None,
   'source_binding':copy.deepcopy(e.get('binding',{})),
   'mechanical_truth_scope':'Exact supplied ID, snapshot, path, literal source rows, and span only; not causal, API, environment, or execution truth.'}

 def audit_final(self,payload,delivered_evidence=None):
  """Observe and validate actual final; never rewrite/restore the raw report."""
  if delivered_evidence is not None:
   self.add_evidence(delivered_evidence);self._current_final_ids={e['id']for e in delivered_evidence}
  else:self._current_final_ids=set(self.evidence)
  errors=[{'code':'invalid_initial_warning_container','detail':e}for e in self.initial_structure_errors];notes=[];entries=[]
  if not isinstance(payload,dict):
   audit={'accepted':False,'errors':[{'code':'no_actual_new_final'}],'entries':[],
    'initial_warning_count':len(self.warnings),'raw_final_unchanged':True,'semantic_truth_guaranteed':False}
   self.last_audit=audit;self.events.append({'stage':'final_audit','audit':copy.deepcopy(audit)});return audit
  dispositions=payload.get('warning_dispositions')
  if dispositions is None:
   errors.append({'code':'missing_warning_dispositions'})
   dispositions=[]
  schema=dispositions_schema()
  shape=list(jsonschema.Draft202012Validator(schema).iter_errors(dispositions))
  if shape:
   errors.extend({'code':'invalid_warning_dispositions_schema','path':list(e.path),'message':e.message}for e in shape)
   dispositions=[]
  findings=payload.get('findings',[])
  if not isinstance(findings,list):errors.append({'code':'final_findings_not_array'});findings=[]
  seen={};index_owners={}
  for disposition in dispositions:
   wid=disposition['warning_id'];entry_errors=[]
   if wid not in self.warnings:entry_errors.append({'code':'unknown_warning_id','warning_id':wid})
   if wid in seen:entry_errors.append({'code':'duplicate_warning_disposition','warning_id':wid})
   seen[wid]=seen.get(wid,0)+1
   indices=disposition['final_finding_indices']
   if len(set(indices))!=len(indices):entry_errors.append({'code':'duplicate_final_finding_index','warning_id':wid})
   valid_indices=[n for n in indices if type(n)is int and 0<=n<len(findings)]
   if valid_indices!=indices:entry_errors.append({'code':'final_finding_index_out_of_range','warning_id':wid})
   action=disposition['disposition'];state=disposition['evidence_state'];basis=disposition['basis']
   if action in('retained','revised','merged')and not valid_indices:entry_errors.append({'code':'emitted_disposition_has_no_final_finding','warning_id':wid})
   if action in('withdrawn','deferred')and indices:entry_errors.append({'code':'nonemitted_disposition_has_final_finding','warning_id':wid})
   if not disposition['reason'].strip():entry_errors.append({'code':'missing_warning_disposition_reason','warning_id':wid})
   if len(set(disposition['question_ids']))!=len(disposition['question_ids']):entry_errors.append({'code':'duplicate_disposition_question_id','warning_id':wid})
   for qid in disposition['question_ids']:
    if qid not in self.questions:entry_errors.append({'code':'unknown_disposition_question_id','warning_id':wid,'question_id':qid})
   refs=[self._reference(ref)for ref in disposition['evidence_refs']]
   for ref in refs:
    if not ref['accepted']:entry_errors.append({'code':'invalid_warning_evidence_reference','warning_id':wid,'errors':ref['errors'],'requested':ref['requested']})
    if not ref['requested']['reason'].strip():entry_errors.append({'code':'missing_warning_reference_reason','warning_id':wid})
   support=[ref for ref in refs if ref['accepted']and ref['requested']['judgment']=='supports']
   refutation=[ref for ref in refs if ref['accepted']and ref['requested']['judgment']=='refutes']
   if state=='supported'and not support:entry_errors.append({'code':'support_without_physical_source_reference','warning_id':wid})
   if state=='refuted':
    if not refutation:entry_errors.append({'code':'refutation_without_physical_source_reference','warning_id':wid})
    if basis not in('new_refutation','interpretation_correction'):entry_errors.append({'code':'refutation_basis_not_positive_counterevidence','warning_id':wid})
    if action=='retained':entry_errors.append({'code':'refuted_initial_cause_claimed_retained','warning_id':wid})
    if action in('revised','merged')and not support:entry_errors.append({'code':'refuted_revision_without_support_reference','warning_id':wid})
   new_refuting_ids=[ref['requested']['evidence_id']for ref in refutation if not ref['available_in_initial_input']]
   if basis=='new_refutation'and refutation and not new_refuting_ids:notes.append({'code':'declared_new_refutation_uses_initial_source_only','warning_id':wid,'scope':'Actual code is cited, but no new physical refuting source was collected. Semantic interpretation remains model-assessed.'})
   if basis=='unresolved_check'and state=='refuted':entry_errors.append({'code':'unresolved_check_is_not_refutation','warning_id':wid})
   if action=='withdrawn'and state!='refuted':entry_errors.append({'code':'withdrawal_requires_refutation_use_deferred_for_unknown','warning_id':wid})
   if action=='deferred'and state!='unresolved':entry_errors.append({'code':'deferred_requires_unresolved_state','warning_id':wid})
   old=self.initial_findings[self.warnings[wid]['initial_finding_index']]if wid in self.warnings else None
   comparisons=[]
   for index in valid_indices:
    index_owners.setdefault(index,[]).append((wid,action))
    new=findings[index]
    oldcore={k:old.get(k)for k in CORE_FIELDS}if isinstance(old,dict)else old
    newcore={k:new.get(k)for k in CORE_FIELDS}if isinstance(new,dict)else new
    comparisons.append({'final_finding_index':index,'literal_causal_fields_unchanged':oldcore==newcore,
     'initial_trigger':old.get('trigger')if isinstance(old,dict)else None,'final_trigger':new.get('trigger')if isinstance(new,dict)else None,
     'initial_uncertainties':copy.deepcopy(old.get('uncertainties'))if isinstance(old,dict)else None,
     'final_uncertainties':copy.deepcopy(new.get('uncertainties'))if isinstance(new,dict)else None,
     'semantic_equivalence_guaranteed':False})
   if action=='retained'and any(not c['literal_causal_fields_unchanged']for c in comparisons):notes.append({'code':'retained_label_with_changed_literal_fields','warning_id':wid,'scope':'Recorded text difference; not an automatic semantic error.'})
   entry={'warning_id':wid,'declared':copy.deepcopy(disposition),'accepted':not entry_errors,'errors':entry_errors,
    'evidence_reference_checks':refs,'final_finding_comparisons':comparisons,
    'query_outcomes':[copy.deepcopy(self.answers[qid])if qid in self.answers else{'question_id':qid,'execution':'not_recorded','semantic_support_or_refutation_established':False}for qid in disposition['question_ids']],
    'new_refuting_source_ids':list(dict.fromkeys(new_refuting_ids)),
    'refutation_source_scope':'new_physical_source'if new_refuting_ids else'initial_supplied_source_reinterpretation'if refutation else'no_bound_refuting_source',
    'semantic_evidence_state':state,'semantic_assessor':'reviewer_model','semantic_truth_guaranteed':False}
   entries.append(entry);errors.extend(entry_errors)
  for wid in self.warnings:
   if wid not in seen:
    error={'code':'missing_warning_disposition','warning_id':wid};errors.append(error)
    entries.append({'warning_id':wid,'accepted':False,'state':'missing_disposition','errors':[error],'semantic_truth_guaranteed':False})
  for index,owners in index_owners.items():
   if len(owners)>1 and not any(action=='merged'for _,action in owners):errors.append({'code':'many_warnings_to_one_finding_without_merge','final_finding_index':index,'warning_ids':[wid for wid,_ in owners]})
  new_indices=sorted(set(range(len(findings)))-set(index_owners))
  audit={'accepted':not errors,'errors':errors,'notes':notes,'entries':entries,
   'initial_warning_count':len(self.warnings),'explicit_disposition_count':len(dispositions),
   'new_final_finding_indices':new_indices,'final_warning_catalog':[{'final_finding_id':f'F-{i:03d}-'+digest(f)[:12],'final_finding_index':i,'final_finding_sha256':digest(f)}for i,f in enumerate(findings)],
   'initial_structure_errors':copy.deepcopy(self.initial_structure_errors),
   'actual_final_input_evidence_ids':sorted(self._current_final_ids),
   'actual_final_input_scope':'explicit_pre_call_delivery'if delivered_evidence is not None else'caller_declared_available_catalog',
   'raw_final_sha256':digest(payload),'raw_final_unchanged':True,'automatic_retention_or_repair':False,
   'mechanical_truth_scope':'Stable warning IDs, explicit emission/disposition, actual source/reference bindings, and evidence-state consistency only.',
   'semantic_truth_guaranteed':False,'cause_correction_success_granted':False}
  self.last_audit=copy.deepcopy(audit);self.events.append({'stage':'final_audit','audit':copy.deepcopy(audit)});return audit

 def record_no_new_final(self,reason,final_origin='shared_initial_final'):
  audit={'accepted':None,'new_final_available':False,'reason':reason,'final_origin':final_origin,
   'entries':[{'warning_id':wid,'state':'initial_actual_final_retained'if final_origin=='shared_initial_final'else'no_new_final',
    'model_disposition_generated':False,'semantic_truth_guaranteed':False}for wid in self.warnings],
   'initial_warning_count':len(self.warnings),'raw_final_unchanged':True,
   'scope':'An already actual initial report may be retained by the runner. No fabricated final disposition, new warning, source graft, or correction success.'}
  self.last_audit=copy.deepcopy(audit);self.events.append({'stage':'no_new_final','audit':copy.deepcopy(audit)});return audit

 def export(self):
  return {'warning_catalog':self.catalog(),'initial_payload_sha256':digest(self.initial_payload),
   'initial_structure_errors':copy.deepcopy(self.initial_structure_errors),
   'available_evidence_ids':list(self.evidence),'questions':copy.deepcopy(list(self.questions.values())),
   'events':copy.deepcopy(self.events),'last_audit':copy.deepcopy(self.last_audit),
   'candidate_ledger_is_separate_from_warning_emission':True,'semantic_truth_guaranteed':False,
   'new_model_calls':0,'new_native_calls':0,'private_target_reads':0}
