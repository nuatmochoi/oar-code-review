"""Execute existing scoped-cause decomposition; never force/restore findings."""
import copy,json,time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from common import ROOT,WORKERS,load,write_new,digest,sha
from sequential import CONFIG,verify,configure
import causal_loop as c
from candidate_policy import inventory,augmented,POLICY as CANDIDATE_POLICY,TASK,audit as candidate_audit
from claim_scope import with_claim_scopes,POLICY as SCOPE_POLICY,audit as scope_audit
from location_ids import wire_schema,decode,verify_result
from openai_transport import complete,request_body,parse_payload
from loop_policy import attach,plan_only
from reexplore_run import RecordedAnswers
from frontier import request_key

DECISION_POLICY='''Use claim_scopes and candidate_decisions for the judgments below; no separate condition_roles/report_assessments fields are present in this schema. For every historical candidate, assess its narrow causal defect separately from stronger consequences. A positive admissible witness can establish a conditional defect without evidence that it occurred in production. The witness must follow supplied code/public input/type/format contracts; a hypothetical missing API behavior is not a witness. Do not require proof of absent imaginary guards. To withhold, identify the particular unresolved essential premise and explain why falsifying it would eliminate the narrow defect, not merely reduce impact. To withdraw, cite affirmative refutation of an essential premise. Failed searches do not supply such refutation. Keep supported narrowing and all necessary unresolved premises honest. The controller only audits declared consistency and provenance; it cannot certify this reasoning or synthesize findings.'''

def build(case,n):
 up=Path(CONFIG['baseline']);result=load(up/'results/reviews'/f'{case["id"]}_{CONFIG["baseline_arm"]}_r{n}'/'review.result.json');provider=Path(result['actual_final_provider_reference']['path']);delivery=load(provider.parents[1]/'final.delivered_evidence.json');shared=load(up/'results/shared'/f'{case["id"]}_r{n}'/'shared.json');extra=load(up/'results/shared'/f'{case["id"]}_r{n}'/'extra_replan.json')
 es=delivery['evidence'];history=delivery['actual_history'];schema,cat=wire_schema(es);assert cat==delivery['location_catalog']
 items=inventory(shared,extra,history);raw=c.prompt(case,es,history,TASK,cat);raw['system']+='\n'+CANDIDATE_POLICY;raw['prompt']+='\nComplete historical candidate inventory (not gold; no filtering):\n'+json.dumps(items,ensure_ascii=False,separators=(',',':'));schema=augmented(schema,items)
 assert request_body(raw,schema)==load(provider.parent/'request.json'),'Only the declared reporting change may differ from baseline'
 raw['system']+='\n'+SCOPE_POLICY+'\n'+DECISION_POLICY;schema=with_claim_scopes(schema)
 return dict(prior=result,delivery=delivery,shared=shared,extra=extra,evidence=es,history=history,schema=schema,catalog=cat,candidates=items,raw=raw)

def audit(payload,items,evidence,initial):
 a=candidate_audit(payload,items,evidence);b=scope_audit(payload,evidence,initial)
 scopes={s['claim_id']:s for s in payload['claim_scopes']};errors=[]
 for cid,d in payload['candidate_decisions'].items():
  scope=scopes.get(d['current_claim_id'])
  if d['disposition']=='withhold'and scope and scope['status']=='supported':errors.append(dict(code='withheld_supported_scope',candidate=cid,semantic_truth_guaranteed=False))
 return dict(accepted=a['accepted']and b['accepted']and not errors,candidate_audit=a,scope_audit=b,decision_errors=errors,findings_unchanged=True,automatic_retention=False,semantic_truth_guaranteed=False)

def one(item):
 case,n=item;d=build(case,n);folder=ROOT/'results/reviews'/f'{case["id"]}_REPORT_r{n}';prior=d['prior'];refs=copy.deepcopy(prior['model_stages'][:-1]);used,calls=c.usage(refs);started=time.monotonic()
 write_new(folder/'final.delivered_evidence.json',dict(**d['delivery'],scope_policy_added=True,source_and_history_exact_prior=True));write_new(folder/'final.review_schema.json',d['schema'])
 receipt=complete(folder,'final',d['raw'],d['schema'],used,calls,900-d['shared']['active_seconds']-d['extra']['active_seconds']);refs.append(c.stage_ref(folder,'final'));wire=parse_payload(receipt);aux=None
 if wire is not None:
  canonical=decode(wire,d['catalog']);f=c.replay(case,d['shared']);latest=d['history']['latest_plan'];up=Path(CONFIG['baseline']);trace=load(up/'results/reviews'/f'{case["id"]}_FOUR_QUERY_r{n}'/'query_round_trace.json');record=trace['additional_round']
  if record:
   for q in plan_only(latest)['questions']:
    if q['query']=='search':f.cache.pop(request_key(q),None)
   actual=f.execute_plan(plan_only(latest),RecordedAnswers(record['core']),enable_queries=True);assert digest(actual)==digest(record['core'])
   attach(f,record['context']['evidence'])
  aux=f.controller.update(canonical,len(f.versions)+1)
  write_new(folder/'scope_execution.audit.json',audit(canonical,d['candidates'],d['evidence'],d['shared']['initial']['claims']))
  write_new(folder/'controller_final_update.json',dict(update=aux,source_only=True,semantic_truth_guaranteed=False))
 result=c.result(case,n,'REPORT',folder,receipt,d['evidence'],d['catalog'],d['delivery']['context'],refs,d['shared']['initial'],aux,True)
 write_new(folder/'fixed_source_comparison.json',dict(prior_review_sha256=digest(prior),source_evidence_sha256=digest(d['evidence']),initial_candidate_sha256=digest(d['shared']['initial']),history_sha256=digest(d['history']),new_model_calls=1,no_source_acquisition=True))
 print(json.dumps(dict(report=result['review_id'],normal=result['final_produced'],findings=result['raw_findings_count'])),flush=True);return result

def reviewers():
 verify();configure()
 with ThreadPoolExecutor(max_workers=WORKERS)as pool:list(pool.map(one,c.units()))
 write_new(ROOT/'audit/reviewers_complete.json',dict(complete=True,new_reviewer_calls=36,source_and_prefix_fixed=True))
