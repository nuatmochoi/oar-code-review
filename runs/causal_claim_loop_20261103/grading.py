"""Immutable, literal manual annotations. Controller acceptance is not truth."""
from pathlib import Path
from common import ROOT,load,sha,digest,write_new

INITIAL_STATES=('correct','wrong','absent','unresolved')
FINDING_LABELS=('target_correct','other_confirmed_defect','false','unresolved','duplicate_target','duplicate_other','structure_failure')
CLAIM_LABELS=('supported','refuted','unsupported','unresolved')

def _truth(case_id):
 values=load(ROOT/'private/truth.json')
 if isinstance(values,dict):values=values['cases']
 return next(v for v in values if v['id']==case_id)

def _case(case_id):return next(v for v in load(ROOT/'prepared/cases.json') if v['id']==case_id)
def _put(path,row):
 if path.exists():assert load(path)==row,('Annotation conflicts require explicit preserved adjudication.',str(path))
 else:write_new(path,row)
 return row

def _strings(v):
 if isinstance(v,str):yield v
 elif isinstance(v,list):
  for x in v:yield from _strings(x)
 elif isinstance(v,dict):
  for x in v.values():yield from _strings(x)

def validate_atomic(payload,rows):
 """Validate provenance/completeness declarations only, never semantic labels."""
 findings=(payload or {}).get('findings',[]);seen=set()
 for a in rows:
  assert isinstance(a,dict) and a['label'] in CLAIM_LABELS
  assert type(a['finding_index']) is int and 0<=a['finding_index']<len(findings)
  assert a['scope'] in ('core','ancillary')
  assert a['mode'] in ('asserted','conditional','explicit_uncertainty')
  if a.get('structure_failure'):
   f=findings[a['finding_index']]
   assert not isinstance(f,dict) or not any(isinstance(f.get(k),str) and f[k].strip() for k in ('title','explanation','trigger','impact')), 'Readable narrative must be literally annotated, not skipped.'
   assert a.get('fragment') is None and a['label']=='unresolved'
  else:
   assert isinstance(a['fragment'],str) and len(a['fragment'])>3
   assert any(a['fragment'] in text for text in _strings(findings[a['finding_index']])),('fragment not literal final finding',a)
  assert isinstance(a['reason'],str) and len(a['reason'])>15
  k=(a['finding_index'],a['fragment']);assert k not in seen;seen.add(k)
 if findings:assert {a['finding_index'] for a in rows}==set(range(len(findings))), 'Every final finding needs at least one literal atomic annotation.'
 return rows

def validate_finding_labels(payload,rows):
 findings=(payload or {}).get('findings',[])
 assert len(rows)==len(findings)
 assert {x['finding_index'] for x in rows}==set(range(len(findings)))
 for r in rows:
  assert r['label'] in FINDING_LABELS and isinstance(r['reason'],str) and len(r['reason'])>15
  if r['label']=='other_confirmed_defect':
   assert isinstance(r.get('defect_key'),str) and r['defect_key']
   assert r.get('source_validation'), 'An independent new defect requires actual source witness, not absence from gold.'
 return rows

def record_initial(owner,key,*,state,reason,finding_index=None,atomic_claims=None,finding_labels=None):
 assert state in INITIAL_STATES and isinstance(reason,str) and len(reason)>20
 path=ROOT/'results/initial'/key/'initial_summary.json';r=load(path);t=_truth(r['case_id']);c=_case(r['case_id'])
 p=r.get('payload');findings=p.get('findings',[]) if isinstance(p,dict) else [];n=len(findings)
 if state in ('correct','wrong'):
  assert type(finding_index) is int and 0<=finding_index<n
 if state=='correct':assert t['unsafe']
 if state=='absent':assert finding_index is None
 if not isinstance(p,dict):assert state=='unresolved'
 atoms=validate_atomic(p,atomic_claims or []);labels=validate_finding_labels(p,finding_labels or [])
 if state=='correct':assert next(x for x in labels if x['finding_index']==finding_index)['label']=='target_correct'
 if state=='wrong':assert next(x for x in labels if x['finding_index']==finding_index)['label'] in ('false','unresolved')
 row=dict(owner=owner,initial_id=key,case_id=r['case_id'],repeat=r['repeat'],cohort=c['cohort'],language=c['language'],information_condition=c['information_condition'],
  unsafe=t['unsafe'],state=state,reason=reason,finding_index=finding_index,raw_finding_count=n,atomic_claims=atoms,finding_labels=labels,
  output_failure=r['status']!='ok',result_sha256=sha(path),truth_sha256=digest(t),case_sha256=digest(c),model_calls_for_grading=0)
 return _put(ROOT/'grades/initial'/f'{key}.json',row)

def record(owner,rid,*,semantic,regression_anchor_valid=None,failure_operation_valid=None,evidence_mechanical_valid=None,evidence_relevant=None,
           reason,finding_index=None,atomic_claims=None,finding_labels=None,lineage_match=None,lineage_reason='',withdrawn_initial_error=False,abstained=False,evidence=None):
 assert type(semantic) is int and semantic in (0,1) and isinstance(reason,str) and len(reason)>20
 path=ROOT/'results/reviews'/rid/'review.result.json';r=load(path);t=_truth(r['case_id']);c=_case(r['case_id'])
 p=r.get('payload');p=p if isinstance(p,dict) and r.get('final_produced') else None
 findings=p.get('findings',[]) if p else [];n=len(findings);assert n==r['raw_findings_count']
 assert semantic<=min(n,int(t['unsafe']))
 flags=(regression_anchor_valid,failure_operation_valid,evidence_mechanical_valid,evidence_relevant)
 if semantic:
  assert all(type(x) is bool for x in flags)
  assert type(finding_index) is int and 0<=finding_index<n
 else:assert flags==(None,None,None,None)
 labels=validate_finding_labels(p,finding_labels or []);atoms=validate_atomic(p,atomic_claims or [])
 if semantic:assert next(x for x in labels if x['finding_index']==finding_index)['label']=='target_correct'
 assert sum(x['label']=='target_correct' for x in labels)==semantic
 valid=int(bool(semantic and all(flags) and r['status']=='ok' and r['final_produced']))
 # Binding is a separate mechanical diagnostic; no semantic truth is inferred.
 if valid:
  bound=[b for b in r['binding'].get('bindings',[]) if b['finding']==finding_index]
  assert bound and all(b['valid'] for b in bound), 'Valid TP cannot bypass actual typed binding failures.'
 initial_key=f'{r["case_id"]}_r{r["repeat"]}';initial_path=ROOT/'grades/initial'/f'{initial_key}.json';initial=load(initial_path)
 state=initial['state']
 if state=='wrong':
  assert type(lineage_match) is bool and isinstance(lineage_reason,str) and len(lineage_reason)>15
 else:assert lineage_match is None
 correction=int(state=='wrong' and semantic and lineage_match)
 if withdrawn_initial_error:assert state=='wrong' and not semantic and not findings
 transition=('wrong_to_right' if correction else 'withdrawal_or_safe_recovery' if withdrawn_initial_error else 'new_detection' if state=='absent' and semantic else 'correct_retained' if state=='correct' and semantic else 'correct_lost' if state=='correct' else 'different_lineage_correct_finding' if state=='wrong' and semantic else 'no_correction')
 row={k:r[k] for k in ('review_id','case_id','repeat','arm','cohort','language','information_condition')}
 row.update(owner=owner,unsafe=t['unsafe'],reported_count=n,cause_tp=valid,semantic_cause_tp=semantic,target_semantic_tp=semantic,
  regression_anchor_valid=regression_anchor_valid,failure_operation_valid=failure_operation_valid,evidence_mechanical_valid=evidence_mechanical_valid,evidence_relevant=evidence_relevant,
  report_binding_losses=semantic-valid,reason=reason,finding_index=finding_index,atomic_claims=atoms,finding_labels=labels,
  initial_state=state,lineage_match=lineage_match,lineage_reason=lineage_reason,transition=transition,semantic_correction=correction,valid_correction=int(correction and valid),
  withdrawn_initial_error=withdrawn_initial_error,abstained=bool(abstained),output_failure=r['status']!='ok' or not r['final_produced'],
  safe_target_alarm=not t['unsafe'] and n>0,unresolved_location_roles=r.get('binding',{}).get('unresolved',[]),
  mechanical_citation_errors=r.get('binding',{}).get('errors',[]),evidence=evidence or [],result_sha256=sha(path),initial_grade_sha256=sha(initial_path),
  truth_sha256=digest(t),case_sha256=digest(c),model_calls_for_grading=0)
 return _put(ROOT/'grades/final'/f'{rid}.json',row)


def _candidate_stage(context_id,stage):
 assert stage in ('initial','verify','final')
 if stage=='initial':
  p=ROOT/'results/initial'/context_id/'initial_summary.json';r=load(p)
  return p,r.get('payload'),r['status'],r['case_id'],r['repeat']
 p=ROOT/'results/reviews'/context_id/stage/'initial.result.json';r=load(p)
 payload=None
 import json
 try:
  value=json.loads(r.get('raw_response'));payload=value if isinstance(value,dict) else None
 except (ValueError,TypeError):pass
 result=load(ROOT/'results/reviews'/context_id/'review.result.json')
 return p,payload,r['status'],result['case_id'],result['repeat']


def record_candidate_judgments(owner,context_id,stage,*,annotations,reason):
 """Literal stage hypotheses, including tentative wrong candidates without alerts.

 A proposition's truth and the model's reported status are separate. A refuted
 tentative hypothesis is not automatically a false final factual assertion.
 """
 assert isinstance(reason,str) and len(reason)>20
 path,payload,status,case_id,repeat=_candidate_stage(context_id,stage)
 claims=(payload or {}).get('claims',[]);assert isinstance(claims,list)
 required={(i,None) for i,c in enumerate(claims)}|{(i,j) for i,c in enumerate(claims) for j,p in enumerate(c['conditions'])}
 actual=set();normalized=[]
 for a in annotations:
  assert a['label'] in CLAIM_LABELS
  candidates=[i for i,c in enumerate(claims) if c['id']==a['candidate_id']]
  idx=a.get('candidate_index')
  if idx is None:
   assert len(candidates)==1, 'Duplicate raw IDs require literal candidate_index; never silently deduplicate.'
   idx=candidates[0]
  assert type(idx) is int and idx in candidates
  c=claims[idx];pid=a.get('condition_id');pidx=a.get('condition_index')
  if pid is None:
   assert pidx is None;obj=c;field='claim';reported={'action':c.get('action','unknown_raw'),'termination':c.get('termination','unknown_raw')}
  else:
   matches=[j for j,p in enumerate(c['conditions']) if p['id']==pid]
   if pidx is None:
    assert len(matches)==1, 'Duplicate condition IDs require literal condition_index.'
    pidx=matches[0]
   assert type(pidx) is int and pidx in matches
   obj=c['conditions'][pidx];field='predicate';reported={'status':obj.get('status','unknown_raw'),'reason':obj.get('reason')}
  key=(idx,pidx);assert key not in actual;actual.add(key)
  assert isinstance(a['fragment'],str) and len(a['fragment'])>3 and a['fragment'] in obj[field]
  assert a['target_relation'] in ('target','ancillary','non_target','unclear')
  assert isinstance(a['reason'],str) and len(a['reason'])>15
  stance=('already_refuted' if reported.get('status')=='refuted' or reported.get('action')=='withdraw' or reported.get('termination')=='refuted' else 'endorsed' if reported.get('status')=='supported' or reported.get('termination')=='sufficient_evidence' else 'open')
  normalized.append({**a,'candidate_index':idx,'condition_index':pidx,'reported_model_judgment':reported,'model_stance_classification':stance,'raw_proposition':obj[field]})
 assert actual==required,('Annotate every actual raw candidate and condition; no omission.',required-actual,actual-required)
 row=dict(owner=owner,context_id=context_id,stage=stage,case_id=case_id,repeat=repeat,status=status,
  annotations=normalized,annotation_status='complete',reason=reason,result_sha256=sha(path),model_calls_for_grading=0,
  semantics='Proposition truth and model endorsement/status are distinct; tentative false hypotheses are not automatically false final assertions.')
 return _put(ROOT/'grades/candidates'/f'{context_id}_{stage}.json',row)


def record_candidate_corrections(owner,rid,*,outcomes,reason):
 """A false initial hypothesis can become a correct actual final finding.

 Stores every false initial candidate/predicate outcome. No ID-only or
 intermediate-only success and no automatic semantic inference is permitted.
 """
 assert isinstance(reason,str) and len(reason)>20
 rpath=ROOT/'results/reviews'/rid/'review.result.json';r=load(rpath)
 initial_id=f'{r["case_id"]}_r{r["repeat"]}';ip=ROOT/'grades/candidates'/f'{initial_id}_initial.json';initial=load(ip)
 fp=ROOT/'grades/candidates'/f'{rid}_final.json'
 # A has no new physical final; its actual final candidate view is initial.
 final=initial if r['arm']=='A' else load(fp) if fp.exists() else None
 gradepath=ROOT/'grades/final'/f'{rid}.json';grade=load(gradepath)
 false_units=[a for a in initial['annotations'] if a['label']=='refuted']
 already_refuted=[a for a in false_units if a['model_stance_classification']=='already_refuted']
 wrong={(a['candidate_index'],a.get('condition_index')):a for a in false_units if a['model_stance_classification'] in ('endorsed','open')}
 denominator_measurable=initial.get('annotation_status','complete')=='complete'
 seen=set();corrected_candidates=set();ancillary_corrected=set()
 allowed=('corrected_to_target_finding','ancillary_API_corrected','withdrawn_or_held','persisted_error','unresolved','different_lineage')
 for o in outcomes:
  matches=[k for k,a in wrong.items() if a['candidate_id']==o['initial_candidate_id'] and a.get('condition_id')==o.get('initial_condition_id') and ('initial_candidate_index' not in o or k[0]==o['initial_candidate_index']) and ('initial_condition_index' not in o or k[1]==o['initial_condition_index'])]
  assert len(matches)==1, 'Ambiguous initial hypothesis IDs require literal array indices.'
  key=matches[0];assert key not in seen;seen.add(key)
  assert o['outcome'] in allowed and isinstance(o['reason'],str) and len(o['reason'])>20
  assert type(o['same_trigger']) is bool and type(o['lineage_confirmed']) is bool
  if o['outcome']=='corrected_to_target_finding':
   assert grade['semantic_cause_tp'] and r['final_produced'] and final is not None
   assert o['same_trigger'] and o['lineage_confirmed']
   idx=o['final_finding_index'];assert idx==grade['finding_index']
   finding=r['payload']['findings'][idx];assert o['final_candidate_id']==finding['claim_id']
   assert any(a['candidate_id']==o['final_candidate_id'] and a['label']=='supported' and a['target_relation']=='target' for a in final['annotations'])
   assert wrong[key]['target_relation']=='target'
   corrected_candidates.add(key[0])
  elif o['outcome']=='ancillary_API_corrected':
   assert r['final_produced'] and final is not None and o['same_trigger'] and o['lineage_confirmed']
   assert wrong[key]['target_relation']=='ancillary'
   assert o.get('final_candidate_id') in {a['candidate_id'] for a in final['annotations']}
   assert o.get('final_judgment_reference') and o.get('literal_correction_fragment')
   assert any(o['literal_correction_fragment'] in text for text in _strings(r['payload'])), 'Ancillary correction must be literal final content.'
   # Human/AI literal adjudication must explain whether predicate meaning changed,
   # or a false predicate was retained but correctly refuted. No label shortcut.
   ancillary_corrected.add(key[0])
 assert seen==set(wrong),('Every false initial hypothesis needs an outcome, including withdrawal/unresolved.',set(wrong)-seen)
 valid_success=bool(corrected_candidates and grade['cause_tp'])
 row=dict(owner=owner,review_id=rid,case_id=r['case_id'],arm=r['arm'],repeat=r['repeat'],reason=reason,outcomes=outcomes,
  initial_hypothesis_denominator_measurable=denominator_measurable,
  initial_false_proposition_units=len(false_units) if denominator_measurable else None,already_refuted_proposition_units=len(already_refuted),already_refuted_hypotheses=already_refuted,
  initial_refuted_hypothesis_units=len(wrong),eligible_initial_wrong_hypothesis_units=len(wrong),initial_refuted_candidates=len({k[0] for k in wrong}),
  initial_refuted_target_candidates=len({k[0] for k,a in wrong.items() if a['target_relation']=='target'}),
  corrected_target_candidates=sorted(corrected_candidates),candidate_correction=int(bool(corrected_candidates)),valid_candidate_correction=int(valid_success),
  ancillary_API_corrected_candidates=sorted(ancillary_corrected),
  initial_candidate_grade_sha256=sha(ip),final_candidate_grade_sha256=sha(ip if r['arm']=='A' else fp) if final is not None else None,
  final_grade_sha256=sha(gradepath),result_sha256=sha(rpath),model_calls_for_grading=0,
  warning='Exploratory candidate mechanism: false tentative hypotheses and asserted final errors are different; conditions/candidates/PR denominators are reported separately.')
 return _put(ROOT/'grades/candidate_corrections'/f'{rid}.json',row)


def record_candidate_structure_failure(owner,context_id,stage,*,reason):
 """Record unusable raw claim structure without inventing zero true/false claims."""
 assert isinstance(reason,str) and len(reason)>20
 path,payload,status,case_id,repeat=_candidate_stage(context_id,stage)
 raw=(payload or {}).get('claims')
 malformed=(not isinstance(raw,list) or any(not isinstance(c,dict) or not isinstance(c.get('id'),str) or not isinstance(c.get('claim'),str) or not isinstance(c.get('conditions'),list) or any(not isinstance(p,dict) or not isinstance(p.get('id'),str) or not isinstance(p.get('predicate'),str) for p in c.get('conditions',[])) for c in raw))
 assert malformed, 'Well-formed raw hypotheses require actual literal judgments.'
 row=dict(owner=owner,context_id=context_id,stage=stage,case_id=case_id,repeat=repeat,status=status,annotations=[],annotation_status='structure_failure',
  reason=reason,raw_candidate_count=len(raw) if isinstance(raw,list) else None,raw_claims_value=raw,result_sha256=sha(path),model_calls_for_grading=0,
  semantics='Unknown hypothesis truth denominator, not zero errors. Original raw structure/count preserved; no repair or intermediate promotion.')
 return _put(ROOT/'grades/candidates'/f'{context_id}_{stage}.json',row)
