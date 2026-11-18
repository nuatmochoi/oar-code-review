"""Shared-pool, span-wire judging. No model executes at import."""
from pathlib import Path
import argparse,collections,concurrent.futures,json,time
import jsonschema
from common import ROOT,ARMS,REPEATS,WORKERS,PREVIOUS,load,sha
import grading48 as g
import judging48 as j
from evaluator_schema import judge_schema
from transport48 import complete,parse_payload,total_usage

def cases():
 values=load(ROOT/'prepared/cases.json');return values['cases']if isinstance(values,dict)else values

def truth():return {v['id']:v for v in load(ROOT/'private/truth.json')['cases']}
def planned():return [f'{c["id"]}_{a}_r{n}'for c in cases()for a in ARMS for n in range(1,REPEATS+1)]
def verify_reused():
 m=load(ROOT/'private/reused_evaluation_manifest.json');assert m['complete']and all(sha(p)==h for p,h in m['references'].items());return m

def verify_freeze():
 from freeze_lsp48 import verify_freeze as full_verify
 full_verify();verify_reused()

def verify_actual_initial_retention(result,previous=PREVIOUS):
 """An old actual final may be retained; a planner/new-source mixture may not."""
 if result.get('final_origin')!='shared_B2_initial_final':return
 path=previous/'results/reviews'/f'{result["case_id"]}_B2_r{result["repeat"]}'/'review.result.json';initial=load(path)
 assert result.get('raw_final_payload')==initial.get('raw_final_payload'),'Retained final must be exact old actual B2 payload'
 assert result.get('final_produced')==initial.get('final_produced'),'Planner must not become final'
 assert result.get('evidence')==initial.get('evidence'),'Retained final cannot gain newly queried source'
 assert result.get('final_input_evidence_ids')==initial.get('final_input_evidence_ids'),'Retained actual delivery IDs must be exact old IDs'

def verify_actual_final_selection(result):
 origin=result.get('final_origin');assert origin in('shared_B2_initial_final','new_final','none')
 if origin=='shared_B2_initial_final':verify_actual_initial_retention(result);return
 if origin=='none':assert not result.get('final_produced');return
 ref=result['actual_final_provider_reference'];path=Path(ref['path']);assert sha(path)==ref['sha256'];payload=parse_payload(load(path));assert payload is not None and payload==result['raw_final_payload'],'Chosen new final must equal its actual normal provider response'
 assert result.get('final_new_generated')is True and result.get('final_produced')is True
 delivery=load(path.parents[1]/'final.delivered_evidence.json');assert delivery['evidence']==result['evidence']and delivery['evidence_ids']==result['final_input_evidence_ids']and g.digest(delivery['evidence'])==delivery['evidence_sha256'],'New final must retain exact actual pre-call delivery'

def prepare_common():
 verify_freeze();expected=planned();missing=[rid for rid in expected if not(ROOT/'results/reviews'/rid/'review.result.json').exists()];assert not missing,('No common pool/judging before all288 terminals.',len(missing))
 assert not list((ROOT/'evaluation').glob('**/initial.result.json')),'Common fact pool must be sealed before judging.'
 old=verify_reused();ts=truth();refs={};packets=[]
 for c in cases():
  results=[]
  for a in ARMS:
   for n in range(1,REPEATS+1):
    rid=f'{c["id"]}_{a}_r{n}';p=ROOT/'results/reviews'/rid/'review.result.json';r=load(p);assert r['case_id']==c['id']and r['arm']==a and r['repeat']==n;verify_actual_final_selection(r);refs[str(p)]=sha(p);results.append(r)
  oldpath=old['fixed_source_packets'][c['id']]['path'];packet=j.build_common_packet(c,ts[c['id']],load(oldpath),results);p=ROOT/'private/common_judge_packets'/f'{c["id"]}.json';g.put_new(p,packet);packets.append({'case_id':c['id'],'path':str(p),'sha256':sha(p),'extra_source_display_reference_tokens':packet['extra_source_display_reference_tokens'],'all_display_source_reference_tokens':packet['all_display_source_reference_tokens']})
 manifest={'complete':True,'cases':48,'planned_reviews':288,'shared_across_all3arms_2repeats':True,'packets':packets,'reviewer_results':refs,'truth_sha256':sha(ROOT/'private/truth.json'),'reused_manifest_sha256':sha(ROOT/'private/reused_evaluation_manifest.json'),'builder_sha256':sha(ROOT/'judging48.py'),'wire_schema_builder_sha256':sha(ROOT/'evaluator_schema.py'),'sealed_before_judges':True,'new_model_calls':0}
 g.put_new(ROOT/'private/common_judge_manifest.json',manifest);return manifest

def verify_common():
 m=load(ROOT/'private/common_judge_manifest.json');assert m['complete']and m['planned_reviews']==288 and m['sealed_before_judges'];assert m['truth_sha256']==sha(ROOT/'private/truth.json')and m['reused_manifest_sha256']==sha(ROOT/'private/reused_evaluation_manifest.json')
 assert m['builder_sha256']==sha(ROOT/'judging48.py')and m['wire_schema_builder_sha256']==sha(ROOT/'evaluator_schema.py');assert all(sha(p)==h for p,h in m['reviewer_results'].items())and all(sha(v['path'])==v['sha256']for v in m['packets']);return m

def review_one(rid):
 dest=ROOT/'grades/final'/f'{rid}.json';failed=ROOT/'grades/final_failures'/f'{rid}.json'
 if dest.exists():return load(dest)
 if failed.exists():return load(failed)
 rp=ROOT/'results/reviews'/rid/'review.result.json';r=load(rp);verify_actual_final_selection(r);t=truth()[r['case_id']];packet=load(ROOT/'private/common_judge_packets'/f'{r["case_id"]}.json');assert packet['reviewer_result_sha256s'][rid]==g.digest(r)
 findings,observable=g.actual_final_findings(r)
 if not findings:return g.record('automatic_empty_actual_final',rid,[],packet,evaluation_kind='Mechanical no-final/no-warning observation; no semantic judge call.')
 c=next(c for c in cases()if c['id']==r['case_id']);folder=ROOT/'evaluation/review'/rid;mechanical=[{'finding_index':i,**g.finding_binding(f,g.delivered_evidence(r))}for i,f in enumerate(findings)];schema=judge_schema(t,packet,len(findings));raw=j.span_review_prompt(c,t,r,packet,mechanical)
 g.put_new(folder/'canonical_input.json',raw);g.put_new(folder/'wire_schema.json',schema);receipt=complete(folder,'judge',raw,schema);provider=folder/'judge/initial.result.json'
 try:
  payload=parse_payload(receipt);assert payload is not None,'Judge provider/unparseable/unknown-usage failure';jsonschema.validate(payload,schema);annotations,extractions=j.wire_to_annotations(payload,r,packet)
  g.put_new(folder/'controller_extracted_annotations.json',{'findings':annotations,'source_span_extractions':extractions,'mechanical_truth_scope':'Physical source ID/row span and literal field extraction only. Semantic correctness is AI-assessed.'})
  row=g.record('nonblind_AI_span_source_judge',rid,annotations,packet)
  g.put_new(folder/'evaluation_link.json',{'provider_result':str(provider),'provider_sha256':sha(provider),'result_sha256':sha(rp),'common_packet_sha256':g.digest(packet),'grade_sha256':sha(dest),'judge_model_tokens':total_usage(receipt),'source_limitations':payload['source_limitations'],'judge_reasoning':payload['reasoning'],'wire_only_changed_semantic_criteria_unchanged':True})
 except Exception as e:
  row={'review_id':rid,'case_id':r['case_id'],'arm':r['arm'],'repeat':r['repeat'],'annotation_status':'unattempted'if receipt.get('status')=='unattempted'else'judge_failed','error':type(e).__name__+': '+str(e),'result_sha256':sha(rp),'provider_result':str(provider),'provider_sha256':sha(provider),'common_packet_sha256':g.digest(packet),'judge_model_tokens':total_usage(receipt),'report_count':len(findings),'all_raw_findings_preserved':True};g.put_new(failed,row)
 return row

def safe_review_one(rid):
 try:return review_one(rid)
 except Exception as e:
  rp=ROOT/'results/reviews'/rid/'review.result.json';r=load(rp);findings,_=g.actual_final_findings(r);p=ROOT/'evaluation/review'/rid/'judge/initial.result.json';row={'review_id':rid,'case_id':r['case_id'],'arm':r['arm'],'repeat':r['repeat'],'annotation_status':'judge_failed','error':type(e).__name__+': '+str(e),'result_sha256':sha(rp),'provider_result':str(p)if p.exists()else None,'provider_sha256':sha(p)if p.exists()else None,'judge_model_tokens':total_usage(load(p))if p.exists()else None,'report_count':len(findings),'all_raw_findings_preserved':True,'scope':'Wrapper failure; no synthetic semantic annotation or known-zero usage.'};return g.put_new(ROOT/'grades/final_failures'/f'{rid}.json',row)

def run_judges():
 verify_freeze();verify_common()
 with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS)as pool:rows=list(pool.map(safe_review_one,planned()))
 g.put_new(ROOT/'evaluation/completion.json',{'planned_reviews':288,'terminal_annotations':len(rows),'annotation_status_counts':dict(collections.Counter(r['annotation_status']for r in rows)),'no_F1_computed':True})

def inventory():
 out=[]
 for rid in planned():
  gp=ROOT/'grades/final'/f'{rid}.json';fp=ROOT/'grades/final_failures'/f'{rid}.json';out.append({'review_id':rid,'reviewer_terminal':(ROOT/'results/reviews'/rid/'review.result.json').exists(),'annotation_status':'complete'if gp.exists()else load(fp)['annotation_status']if fp.exists()else'unattempted'})
 return {'planned':288,'rows':out,'no_F1_computed':True}

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('phase',choices=['prepare-common','judge-run','inventory']);a=p.parse_args()
 if a.phase=='prepare-common':print(json.dumps({k:v for k,v in prepare_common().items()if k not in('packets','reviewer_results')},ensure_ascii=False))
 elif a.phase=='judge-run':run_judges()
 else:print(json.dumps(inventory(),ensure_ascii=False,indent=2))
