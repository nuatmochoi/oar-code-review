"""Immutable multi-finding evaluation. Semantic truth is supplied by an evaluator.

The controller validates source identity/citations; neither its verdict nor a
benchmark label is a proof of causal truth. No network/model calls occur here.
"""
from pathlib import Path
import hashlib,json,re
ROOT=Path(__file__).resolve().parent
FUNCTIONAL={'correctness','reliability','security','performance'}
REGRESSION_SCOPES={'introduced-by-pr','exacerbated-by-pr'}
LABELS={'target_correct','target_wrong_cause','other_confirmed_defect','false','unresolved','duplicate_target','duplicate_other','structure_failure'}
SOURCE_KINDS={'source_lines','implementation_body','exception_branch','body','diff','diff_rows','diff_source','diff_hunk'}
def load(p):return json.loads(Path(p).read_text())
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def digest(v):return hashlib.sha256(json.dumps(v,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
def put_new(p,v):
 p=Path(p);p.parent.mkdir(parents=True,exist_ok=True)
 if p.exists():
  assert load(p)==v,('Immutable record conflict; preserve original and adjudicate separately.',str(p));return v
 with p.open('x')as f:json.dump(v,f,ensure_ascii=False,indent=2);f.write('\n')
 return v
def case_id_for(c):return c.get('id',c.get('case_id','RB_'+c['nwo'].replace('/','__')+'-'+str(c['pr_number'])))
def source_language(path):
 ext=Path(path).suffix.lower()
 return 'Python'if ext in('.py','.pyi')else'Go'if ext=='.go'else'TypeScript'if ext in('.ts','.tsx')else'JavaScript'if ext in('.js','.jsx')else'YAML'if ext in('.yaml','.yml')else'other'
def canonicalize_gold(golden,aliases=None):
 """Conservative pre-model equivalence groups; no effect/score-driven merging.

 aliases is a list of {indices:[raw_indices],reason:literal_manual_reason}.
 All original records remain; categories/scopes are never silently rewritten.
 """
 aliases=aliases or[];fs=golden['findings'];selected={i for i,f in enumerate(fs)if f.get('tp_fp')=='tp'and f.get('category')in FUNCTIONAL}
 owner={i:i for i in selected};reasons={}
 for group in aliases:
  ids=group['indices'];assert len(ids)>1 and len(ids)==len(set(ids))and set(ids)<=selected
  assert len(group['reason'])>30
  assert len({fs[i].get('scope')in REGRESSION_SCOPES for i in ids})==1,'Do not merge primary and pre-existing scopes.'
  assert all(owner[i]==i for i in ids),'Overlapping manual alias groups must be made explicit.'
  canonical=min(ids)
  for i in ids:owner[i]=canonical
  reasons[canonical]=group['reason']
 targets=[]
 for idx in sorted(set(owner.values())):
  members=sorted(i for i in selected if owner[i]==idx);records=[dict(raw_index=i,raw_finding=fs[i])for i in members]
  targets.append(dict(id=f'G{idx:03d}',raw_indices=members,records=records,primary=fs[idx].get('scope')in REGRESSION_SCOPES,
   category=fs[idx]['category'],scope=fs[idx]['scope'],source_language=source_language(fs[idx]['file']),
   required_context=fs[idx].get('context_required'),difficulty=fs[idx].get('difficulty'),
   canonicalization_reason=reasons.get(idx,'Single original functional TP record; no equivalence merge.'),
   semantic_truth_status='benchmark_labeled_not_independently_source_verified'))
 return targets
def prepare_truth(candidates,golden_by_pr,alias_groups=None):
 """Pure constructor. Caller writes/freezes private truth before model review."""
 alias_groups=alias_groups or{};out=[]
 for c in candidates:
  cid=case_id_for(c);key=(c['repo'].rstrip('/'),c['pr_number']);golden=golden_by_pr[key]
  assert all(golden['pr'][k]==c[k]for k in('base','head'))
  targets=canonicalize_gold(golden,alias_groups.get(cid,[]))
  out.append(dict(id=cid,case_id=cid,language=c['language'],pr_url=c['pr_url'],repo=c['repo'],base=c['base'],head=c['head'],targets=targets,
   primary_target_ids=[t['id']for t in targets if t['primary']],all_functional_target_ids=[t['id']for t in targets],
   primary_raw_records=sum(len(t['raw_indices'])for t in targets if t['primary']),all_functional_raw_records=sum(len(t['raw_indices'])for t in targets),
   original_golden_sha256=digest(golden),source_verification='Separate pre-review gold judge, never inferred from TP metadata.'))
 return {'cases':out,'selection_cases':len(out),'scope':'Fixed functional benchmark references; introduced/exacerbated primary, pre-existing only all-functional secondary. No clean-PR ground-truth inference.'}

def actual_final_findings(result):
 if not result.get('final_produced'):return [],False
 payload=result.get('raw_final_payload',result.get('payload'))
 if not isinstance(payload,dict)or not isinstance(payload.get('findings'),list):return [],False
 return payload['findings'],True
def _strings(v):
 if isinstance(v,str):yield v
 elif isinstance(v,list):
  for x in v:yield from _strings(x)
 elif isinstance(v,dict):
  for x in v.values():yield from _strings(x)
def delivered_evidence(result):
 if 'delivered_final_evidence'in result:
  values=result['delivered_final_evidence'];return {e['id']:e for e in(values or[])}
 ids=result.get('final_input_evidence_ids')
 assert isinstance(ids,list),'Actual final input evidence manifest is required; collected is not delivered.'
 byid={e['id']:e for e in result.get('evidence',[])};assert set(ids)<=set(byid)
 return {i:byid[i]for i in ids}
def diff_physical_rows(text,snapshot,path):
 """Independently recover literal source rows from unified diff, with gaps."""
 old=new=None;a=b=0;out={};conflict=False
 def name(value,prefix):
  value=value.split('\t')[0]
  if value.startswith('"'):
   try:value=json.loads(value)
   except ValueError:return None
  return None if value=='/dev/null'else value.removeprefix(prefix)
 def add(p,side,n,line):
  nonlocal conflict
  if p==path and side==snapshot and n>0:
   if n in out and out[n]!=line:conflict=True
   out[n]=line
 for row in text.splitlines():
  if row.startswith('diff --git '):old=new=None;a=b=0
  elif row.startswith('--- '):old=name(row[4:],'a/');new=None
  elif row.startswith('+++ '):new=name(row[4:],'b/')
  elif row.startswith('@@'):
   m=re.match(r'@@ -(\d+)(?:,\d+)? \+(\d+)',row)
   if m:a,b=map(int,m.groups())
   else:a=b=0
  elif row.startswith(' ')and a and b:add(old,'BASE',a,row[1:]);add(new,'HEAD',b,row[1:]);a+=1;b+=1
  elif row.startswith('-')and not row.startswith('---'):add(old,'BASE',a,row[1:]);a+=1
  elif row.startswith('+')and not row.startswith('+++'):add(new,'HEAD',b,row[1:]);b+=1
 return {}if conflict else out
def physical_rows(e,snapshot):
 def rowmap(v):
  if isinstance(v,list)and all(type(x)is int and x>0 for x in v):
   return ({x:None for x in v},len(v)==len(set(v)))
  if isinstance(v,list)and all(isinstance(x,dict)and type(x.get('line'))is int and x['line']>0 and isinstance(x.get('text'),str)for x in v):
   return ({x['line']:x['text']for x in v},len(v)==len({x['line']for x in v}))
  if isinstance(v,dict)and all(isinstance(k,str)and k.isdigit()and int(k)>0 and isinstance(text,str)for k,text in v.items()):
   return ({int(k):text for k,text in v.items()},len(v)==len({int(k)for k in v}))
  return {},False
 raw=None
 meta=e.get('mechanical_checks',{})
 for obj in(e,meta):
  bysnap=obj.get('physical_rows_by_snapshot')or obj.get('actual_rows_by_snapshot')
  if isinstance(bysnap,dict)and snapshot in bysnap:raw=bysnap[snapshot];break
 if raw is None:
  if e.get('snapshot')!=snapshot:return set()
  for obj in(e,meta):
   if isinstance(obj.get('physical_rows'),(list,dict)):raw=obj['physical_rows'];break
 text=e.get('content')
 if not isinstance(text,str):return set()
 body_hash=meta.get('raw_sha256')
 if body_hash is not None and body_hash!=hashlib.sha256(text.encode()).hexdigest():return set()
 if e.get('kind')in('diff','diff_rows','diff_source','diff_hunk'):
  if raw is None:return set() # Never infer unified-diff source ranges.
  rows,valid=rowmap(raw);actual=diff_physical_rows(text,snapshot,e.get('path'))
  if not valid or any(n not in actual or value is not None and value!=actual[n]for n,value in rows.items()):return set()
  return set(rows)
 a,b=e.get('start_line'),e.get('end_line');text=e.get('content')
 if type(a)is int and type(b)is int and len(text.splitlines())==b-a+1:
  actual=dict(enumerate(text.splitlines(),a))
  if raw is None:return set(actual)
  rows,valid=rowmap(raw)
  if valid and set(rows)==set(actual)and all(value is None or value==actual[n]for n,value in rows.items()):return set(actual)
 return set()
def check_location(location,evidence,role):
 if not isinstance(location,dict):return {'valid':False,'reason':'malformed_location'}
 if location.get('status')=='unresolved':
  valid_form=not location.get('evidence_id')and not location.get('path')and location.get('start_line')==location.get('end_line')==0
  return {'valid':False,'unresolved':True,'well_formed_unresolved':valid_form,'reason':'honest_unresolved_is_not_a_bound_operation'}
 if location.get('status')!='bound':return {'valid':False,'reason':'unknown_location_status'}
 e=evidence.get(location.get('evidence_id'));errors=[]
 if e is None:return {'valid':False,'reason':'evidence_not_in_actual_final_input'}
 if e.get('kind')not in SOURCE_KINDS:errors.append('metadata_not_physical_source')
 if location.get('path')!=e.get('path'):errors.append('path_mismatch')
 snapshot=location.get('snapshot')
 if role in('regression_anchor','failure_operation')and snapshot!='HEAD':errors.append('regression_and_failure_operation_must_bind_HEAD')
 a,b=location.get('start_line'),location.get('end_line');rows=physical_rows(e,snapshot)
 if type(a)is not int or type(b)is not int or not 1<=a<=b or not set(range(a,b+1))<=rows:errors.append('whole_range_not_physically_delivered_in_snapshot')
 return {'valid':not errors,'errors':errors,'evidence_id':location.get('evidence_id'),'role':role}
def finding_binding(finding,evidence):
 if not isinstance(finding,dict):return {'valid':False,'slots':[],'errors':['malformed_finding']}
 slots=[]
 for role in('regression_anchor','failure_operation'):
  slots.append(check_location(finding.get(role),evidence,role))
 supports=finding.get('supporting_evidence')
 if not isinstance(supports,list):return {'valid':False,'slots':slots,'errors':['supporting_evidence_not_array']}
 for p in supports:slots.append(check_location(p,evidence,'supporting_evidence'))
 return {'valid':all(v['valid']for v in slots),'slots':slots,'errors':[]}

def validate_annotations(result,truth,annotations,source_packet):
 findings,observable=actual_final_findings(result)
 if not observable:assert not annotations
 assert len(annotations)==len(findings)
 assert {v['finding_index']for v in annotations}==set(range(len(findings)))
 targets={t['id']:t for t in truth['targets']};source={s['id']:s for s in source_packet.get('sources',[])};seen_targets=set();byid=delivered_evidence(result)if findings else{}
 retained_correct_targets={v.get('matched_target_id')for v in annotations if v.get('label')=='target_correct'}
 out=[]
 for v in sorted(annotations,key=lambda x:x['finding_index']):
  assert v['label']in LABELS and type(v['cause_correct'])is bool
  assert isinstance(v['reason'],str)and len(v['reason'])>20
  f=findings[v['finding_index']];fragment=v['literal_core_fragment']
  if v['label']=='structure_failure':
   assert not isinstance(f,dict)or not any(isinstance(f.get(k),str)and f[k].strip()for k in('title','explanation','trigger','impact'))
   assert not fragment
  else:assert isinstance(fragment,str)and len(fragment)>3 and any(fragment in s for s in _strings(f))
  target=v.get('matched_target_id')or None
  if target is not None:assert target in targets
  if v['label']=='target_correct':
   assert target is not None and target not in seen_targets and v['cause_correct'];seen_targets.add(target)
  if v['label']in('false','target_wrong_cause','unresolved','structure_failure'):assert not v['cause_correct']
  if v['label']=='other_confirmed_defect':assert target is None and v['cause_correct']
  if v['label']=='duplicate_target':assert target in retained_correct_targets and v['cause_correct'],'A duplicate must point to a retained source-supported target report.'
  if v['label']=='duplicate_other':assert target is None and v['cause_correct']and any(x.get('label')=='other_confirmed_defect'for x in annotations),'A duplicate other defect needs a retained independently confirmed report.'
  assessments=v['source_assessments']
  for a in assessments:
   assert a['source_id']in source and a['judgment']in('supports','refutes','unresolved')and len(a['reason'])>15
   assert len(a['fragment'])>2 and a['fragment']in source[a['source_id']]['content']
  if v['label']in('target_correct','other_confirmed_defect','duplicate_target','duplicate_other'):assert any(a['judgment']=='supports'for a in assessments),'A new/known/duplicate correct cause requires literal supplied source support.'
  if v['label']in('false','target_wrong_cause'):assert any(a['judgment']=='refutes'for a in assessments),'No-source or unmatched must remain unresolved, not false.'
  flags=[v.get(k)for k in('regression_anchor_relevant','failure_operation_relevant','evidence_relevant')]
  assert all(x is None or type(x)is bool for x in flags)
  bind=finding_binding(f,byid)
  report_ok=result.get('report_status')=='ok'and result.get('final_produced')
  valid=bool(v['label']=='target_correct'and all(x is True for x in flags)and bind['valid']and report_ok)
  aux=result.get('auxiliary_final_update_accepted')
  assert aux is None or type(aux)is bool
  out.append({**v,'matched_target_id':target,'mechanical_binding':bind,'primary_cause_tp':int(v['label']=='target_correct'and targets[target]['primary']),
   'primary_valid_report_tp':int(valid and targets[target]['primary']),'all_functional_cause_tp':int(v['label']=='target_correct'),
   'all_functional_valid_report_tp':int(valid),'compatibility_valid_tp_with_aux_gate':int(valid and targets[target]['primary']and aux is True),
   'matched_target_source_language':targets[target]['source_language']if target else None})
 return out,observable

def build_grade(owner,result,truth,annotations,source_packet,*,evaluation_kind='nonblind_AI_source_judge'):
 rows,observable=validate_annotations(result,truth,annotations,source_packet)
 findings,_=actual_final_findings(result);n=len(findings)
 if type(result.get('raw_findings_count'))is int:assert result['raw_findings_count']==n
 assert result.get('report_status')in('ok','error','unattempted')
 primary=sum(v['primary_cause_tp']for v in rows);valid=sum(v['primary_valid_report_tp']for v in rows)
 assert valid<=primary<=len(truth['primary_target_ids'])
 return dict(owner=owner,review_id=result['review_id'],case_id=result['case_id'],arm=result['arm'],repeat=result['repeat'],language=result['language'],
  annotation_status='complete',evaluation_kind=evaluation_kind,human_review=False,raw_final_observable=observable,
  reported_count=n,primary_target_count=len(truth['primary_target_ids']),all_functional_target_count=len(truth['all_functional_target_ids']),
  cause_tp=primary,valid_report_tp=valid,all_functional_cause_tp=sum(v['all_functional_cause_tp']for v in rows),all_functional_valid_report_tp=sum(v['all_functional_valid_report_tp']for v in rows),
  compatibility_valid_tp_with_aux_gate=sum(v['compatibility_valid_tp_with_aux_gate']for v in rows),
  confirmed_false_findings=sum(v['label']in('false','target_wrong_cause')for v in rows),wrong_cause_findings=sum(v['label']=='target_wrong_cause'for v in rows),
  other_confirmed_defects=sum(v['label']=='other_confirmed_defect'for v in rows),unresolved_findings=sum(v['label']=='unresolved'for v in rows),
  finding_labels=rows,report_status=result['report_status'],legacy_combined_status=result.get('status'),auxiliary_final_update_accepted=result.get('auxiliary_final_update_accepted'),
  output_failure=result['report_status']!='ok'or not result.get('final_produced'),truth_sha256=digest(truth),result_content_sha256=digest(result),source_packet_sha256=digest(source_packet),
  limits='A target-matching score is not full defect recall. Off-target reports include valid pre-existing/new defects, duplicates and unresolved; not all are false positives.')

def record(owner,rid,annotations,source_packet,*,root=ROOT,evaluation_kind='nonblind_AI_source_judge'):
 rp=root/'results/reviews'/rid/'review.result.json';r=load(rp);truth=next(v for v in load(root/'private/truth.json')['cases']if v['id']==r['case_id'])
 g=build_grade(owner,r,truth,annotations,source_packet,evaluation_kind=evaluation_kind)
 g.update(result_sha256=sha(rp),source_packet_reference_sha256=digest(source_packet))
 return put_new(root/'grades/final'/f'{rid}.json',g)

def metric(tp,reports,targets):
 assert type(tp)is int and 0<=tp<=min(reports,targets)
 return {'tp':tp,'fn':targets-tp,'off_target_reports':reports-tp,'precision':tp/reports if reports else None,'recall':tp/targets if targets else None,'f1':2*tp/(reports+targets)if targets else None}
def summarize(grades):
 reports=sum(g['reported_count']for g in grades);targets=sum(g['primary_target_count']for g in grades);alltargets=sum(g['all_functional_target_count']for g in grades)
 return {'reviews':len(grades),'primary_targets':targets,'all_functional_targets':alltargets,'raw_reports':reports,
  'cause':metric(sum(g['cause_tp']for g in grades),reports,targets),'valid_report':metric(sum(g['valid_report_tp']for g in grades),reports,targets),
  'all_functional_cause':metric(sum(g['all_functional_cause_tp']for g in grades),reports,alltargets),
  'all_functional_valid_report':metric(sum(g['all_functional_valid_report_tp']for g in grades),reports,alltargets),
  'compatibility_valid_with_aux_gate':metric(sum(g['compatibility_valid_tp_with_aux_gate']for g in grades),reports,targets),
  'actual_confirmed_false_findings':sum(g['confirmed_false_findings']for g in grades),'unresolved_findings':sum(g['unresolved_findings']for g in grades),
  'other_confirmed_defects':sum(g['other_confirmed_defects']for g in grades),'report_pipeline_failures':sum(g['output_failure']for g in grades),
  'no_actual_final':sum(not g['raw_final_observable']for g in grades),'zero_reference_target_reviews':sum(g['primary_target_count']==0 for g in grades)}
