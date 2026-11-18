"""Explicit CLI entry points for frozen source-gold and arm-masked review judging.

Nothing executes at import. Model execution requires a pre-existing root freeze.
All judge provider calls/costs live under evaluation/, separate from reviewers.
"""
from pathlib import Path
import argparse,collections,concurrent.futures,json,sys,time,traceback
import jsonschema
from common import ROOT,ARMS,REPEATS,WORKERS,load,sha,write_new
import grading48 as g
import judging48 as j
from schemas import GOLD_JUDGE,JUDGE
from transport48 import complete,parse_payload,total_usage

def cases():
 values=load(ROOT/'prepared/cases.json');return values['cases']if isinstance(values,dict)else values
def truth():return {c['id']:c for c in load(ROOT/'private/truth.json')['cases']}
def verify_freeze():
 from freeze48 import verify_freeze as full_verify
 full_verify()
 m=load(ROOT/'private/source_packets_manifest.json');assert m['complete']and m['truth_sha256']==sha(ROOT/'private/truth.json')
 assert all(sha(x['path'])==x['sha256']for x in m['source_packets'])
def _result_payload(r,schema):
 payload=parse_payload(r)
 if payload is None:raise ValueError('judge_provider_failure_or_unparseable_payload')
 jsonschema.validate(payload,schema);return payload

def gold_one(c):
 cid=c['id'];dest=ROOT/'grades/gold'/f'{cid}.json'
 if dest.exists():return load(dest)
 t=truth()[cid];sp=ROOT/'private/frozen_source_packets'/f'{cid}.json';packet=load(sp);folder=ROOT/'evaluation/gold'/cid
 raw=j.gold_prompt(c,t,packet);g.put_new(folder/'canonical_input.json',raw)
 receipt=complete(folder,'gold',raw,GOLD_JUDGE);rp=folder/'gold/initial.result.json'
 row=dict(case_id=cid,annotation_status='unattempted'if receipt.get('status')=='unattempted'else'judge_failed',source_packet_sha256=sha(sp),truth_sha256=g.digest(t),provider_result={'path':str(rp),'sha256':sha(rp)},physical_model_tokens=total_usage(receipt),human_review=False,evaluation_kind='nonblind_AI_source_gold_judge',reviewer_model_calls=0)
 try:
  payload=_result_payload(receipt,GOLD_JUDGE);assessments=j.validate_gold_assessments(t,packet,payload)
  row.update(annotation_status='complete',targets=assessments,source_limitations=payload['source_limitations'],reasoning=payload['reasoning'])
 except Exception as e:row.update(error=type(e).__name__+': '+str(e),targets=None)
 g.put_new(dest,row);print(json.dumps({'gold_case':cid,'annotation_status':row['annotation_status']}),flush=True);return row
def run_gold():
 verify_freeze()
 assert not list((ROOT/'results').glob('**/initial.result.json')),'Gold validation must precede reviewer model execution.'
 with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS)as pool:rows=list(pool.map(safe_gold_one,cases()))
 g.put_new(ROOT/'evaluation/gold_completion.json',{'complete':True,'cases':len(rows),'complete_annotations':sum(r['annotation_status']=='complete'for r in rows),'evaluation_errors':sum(r['annotation_status']!='complete'for r in rows),'new_reviewer_calls':0})

def safe_gold_one(c):
 try:return gold_one(c)
 except Exception as e:
  cid=c['id'];p=ROOT/'evaluation/gold'/cid/'gold/initial.result.json';t=truth()[cid]
  row={'case_id':cid,'annotation_status':'judge_failed','targets':None,'truth_sha256':g.digest(t),'human_review':False,'error':type(e).__name__+': '+str(e),'physical_model_tokens':total_usage(load(p))if p.exists()else None,'provider_result':{'path':str(p),'sha256':sha(p)}if p.exists()else None,'scope':'Wrapper/source-packet failure; no synthetic source judgment. Missing receipt does not prove zero issued cost.'}
  return g.put_new(ROOT/'grades/gold'/f'{cid}.json',row)

def seal_gold():
 verify_freeze();assert not list((ROOT/'results').glob('**/initial.result.json')),'No post-review reference filtering allowed.'
 ts=truth();subset={};refs={};errors=[];counts=collections.Counter()
 for c in cases():
  cid=c['id'];p=ROOT/'grades/gold'/f'{cid}.json';v=load(p);refs[str(p)]=sha(p);assert v['truth_sha256']==g.digest(ts[cid])
  if v['annotation_status']!='complete':errors.append(cid);subset[cid]=[];continue
  for row in v['targets']:counts[row['source_status']]+=1
  subset[cid]=[row['target_id']for row in v['targets']if row['target_id']in ts[cid]['primary_target_ids']and row['source_status']=='supported'and row['scope_assessment']in g.REGRESSION_SCOPES]
 payload={'complete':True,'cases':len(subset),'supported_primary_target_ids':subset,'source_gold_status_counts':dict(counts),'gold_evaluation_failure_cases':errors,
  'reference_primary_targets_unchanged':366,'subset_canonical_targets':sum(len(v)for v in subset.values()),'grading':'Nonblind AI source adjudication; no human review. Failed gold judge is a verification failure, not automatically source-refuted.','frozen_before_reviewer':True,'references':refs,'recorded_at':time.time()}
 g.put_new(ROOT/'private/source_supported_subset.json',payload)
 g.put_new(ROOT/'private/gold_validation_seal.json',{'status':'complete','complete':True,'source_supported_subset_sha256':sha(ROOT/'private/source_supported_subset.json'),'references':refs,'truth_sha256':sha(ROOT/'private/truth.json'),'recorded_at':time.time()})
 print(json.dumps({'gold_sealed':True,'supported_subset':payload['subset_canonical_targets'],'judge_failure_cases':len(errors)}))

def verify_gold_seal():
 seal=load(ROOT/'private/gold_validation_seal.json');assert seal['status']=='complete'and seal['truth_sha256']==sha(ROOT/'private/truth.json')
 assert seal['source_supported_subset_sha256']==sha(ROOT/'private/source_supported_subset.json')
 assert all(sha(p)==h for p,h in seal['references'].items())

def review_one(rid):
 dest=ROOT/'grades/final'/f'{rid}.json'
 if dest.exists():return load(dest)
 failed=ROOT/'grades/final_failures'/f'{rid}.json'
 if failed.exists():return load(failed) # no hidden retry; direct adjudication is separate
 rp=ROOT/'results/reviews'/rid/'review.result.json';r=load(rp);t=truth()[r['case_id']];c=next(c for c in cases()if c['id']==r['case_id']);base=load(ROOT/'private/frozen_source_packets'/f'{c["id"]}.json')
 findings,observable=g.actual_final_findings(r)
 if not findings:
  return g.record('automatic_empty_actual_final',rid,[],base,evaluation_kind='Mechanical no-final/no-warning observation; no semantic judge call.')
 folder=ROOT/'evaluation/review'/rid;packet=j.supplement_review_source_packet(c,t,r,base);g.put_new(folder/'source_packet.json',packet)
 mechanical=[{'finding_index':i,**g.finding_binding(f,g.delivered_evidence(r))}for i,f in enumerate(findings)]
 raw=j.review_prompt(c,t,r,packet,mechanical);g.put_new(folder/'canonical_input.json',raw)
 receipt=complete(folder,'judge',raw,JUDGE);provider=folder/'judge/initial.result.json'
 try:
  payload=_result_payload(receipt,JUDGE)
  row=g.record('nonblind_AI_CLI_source_judge',rid,payload['findings'],packet)
  g.put_new(folder/'evaluation_link.json',{'result_sha256':sha(rp),'provider_result':str(provider),'provider_sha256':sha(provider),'source_packet_sha256':g.digest(packet),'grade_sha256':sha(dest),'judge_model_tokens':total_usage(receipt),'source_limitations':payload['source_limitations'],'judge_reasoning':payload['reasoning']})
 except Exception as e:
  row=dict(review_id=rid,case_id=r['case_id'],arm=r['arm'],repeat=r['repeat'],annotation_status='unattempted'if receipt.get('status')=='unattempted'else'judge_failed',error=type(e).__name__+': '+str(e),result_sha256=sha(rp),provider_result=str(provider),provider_sha256=sha(provider),source_packet_sha256=g.digest(packet),judge_model_tokens=total_usage(receipt),all_raw_findings_preserved=True,report_count=len(findings))
  g.put_new(ROOT/'grades/final_failures'/f'{rid}.json',row)
 print(json.dumps({'judged_review':rid,'status':row.get('annotation_status')}),flush=True);return row
def run_reviews():
 verify_freeze();verify_gold_seal()
 planned=[f'{c["id"]}_{a}_r{n}'for c in cases()for a in ARMS for n in range(1,REPEATS+1)]
 available=[rid for rid in planned if(ROOT/'results/reviews'/rid/'review.result.json').exists()]
 with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS)as pool:rows=list(pool.map(safe_review_one,available))
 print(json.dumps({'available_reviews':len(available),'planned':len(planned),'pending_reviewer_results':len(planned)-len(available),'graded_now':sum(r.get('annotation_status')=='complete'for r in rows),'no_F1_computed':True}))

def safe_review_one(rid):
 try:return review_one(rid)
 except Exception as e:
  rp=ROOT/'results/reviews'/rid/'review.result.json';r=load(rp);p=ROOT/'evaluation/review'/rid/'judge/initial.result.json';findings,_=g.actual_final_findings(r)
  row={'review_id':rid,'case_id':r['case_id'],'arm':r['arm'],'repeat':r['repeat'],'annotation_status':'judge_failed','error':type(e).__name__+': '+str(e),'result_sha256':sha(rp),'provider_result':str(p)if p.exists()else None,'provider_sha256':sha(p)if p.exists()else None,'judge_model_tokens':total_usage(load(p))if p.exists()else None,'all_raw_findings_preserved':True,'report_count':len(findings),'scope':'Wrapper/source-packet failure; no synthetic finding judgments. Missing receipt does not prove zero issued cost.'}
  return g.put_new(ROOT/'grades/final_failures'/f'{rid}.json',row)

def evaluation_inventory():
 """Full planned ledger; absence is explicit, never a synthetic source verdict."""
 gold=[];reviews=[]
 for c in cases():
  cid=c['id'];p=ROOT/'grades/gold'/f'{cid}.json'
  gold.append({'case_id':cid,'annotation_status':load(p)['annotation_status']if p.exists()else'unattempted','record':str(p)if p.exists()else None})
  for a in ARMS:
   for n in range(1,REPEATS+1):
    rid=f'{cid}_{a}_r{n}';rp=ROOT/'results/reviews'/rid/'review.result.json';gp=ROOT/'grades/final'/f'{rid}.json';fp=ROOT/'grades/final_failures'/f'{rid}.json'
    status='complete'if gp.exists()else load(fp)['annotation_status']if fp.exists()else'unattempted'
    reviews.append({'review_id':rid,'case_id':cid,'arm':a,'repeat':n,'reviewer_result_available':rp.exists(),'annotation_status':status,'record':str(gp)if gp.exists()else str(fp)if fp.exists()else None})
 return {'planned_gold_cases':len(gold),'planned_reviews':len(reviews),'gold':gold,'reviews':reviews,'gold_status_counts':dict(collections.Counter(v['annotation_status']for v in gold)),'review_status_counts':dict(collections.Counter(v['annotation_status']for v in reviews)),'no_F1_computed':True}

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('phase',choices=['gold-run','gold-seal','review-run','review-one','inventory']);p.add_argument('--review-id');args=p.parse_args()
 if args.phase=='gold-run':run_gold()
 elif args.phase=='gold-seal':seal_gold()
 elif args.phase=='review-run':run_reviews()
 elif args.phase=='inventory':print(json.dumps(evaluation_inventory(),ensure_ascii=False,indent=2))
 else:
  verify_freeze();verify_gold_seal();assert args.review_id;safe_review_one(args.review_id)
