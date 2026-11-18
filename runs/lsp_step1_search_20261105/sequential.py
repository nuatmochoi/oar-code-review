"""Prospective single-change experiment; old evidence/results are read-only."""
import copy,json,os,time,sys,traceback
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from common import ROOT,PROJECT,BUDGETS,WORKERS,REPEATS,ARMS,load,sha,digest,write_new
from openai_transport import total_usage
import causal_loop as c

ORIGINAL=PROJECT/'runs/lsp_followup5_protected_search_20261104'
CONFIG=dict(step=1,arm='SEARCH',baseline=str(ORIGINAL),baseline_arm='PROTECTED',label='검색 입력 분리·복수 후보·페이지 재개')

def configure():
 from step_search import SEARCH_INSTRUCTION
 original=c.prompt
 def prompt(case,es,history,task,cat=None):
  raw=original(case,es,history,task,cat)
  if history:raw['system']+='\n'+SEARCH_INSTRUCTION
  return raw
 c.prompt=prompt
 original_answer_view=c.answer_view
 def answer_view(record):
  view=original_answer_view(record)
  for out,answer in zip(view['answers'],record['core'].get('answers',[])):
   if answer['question'].get('query')=='search':out['search_binding']=copy.deepcopy(answer.get('callee_binding'))
  return view
 c.answer_view=answer_view

def refs():
 out={}
 bases=[ORIGINAL,Path(CONFIG['baseline'])]
 for base in set(bases):
  for name in ['freeze.json','completion.json','post/aggregate.json','post/rows.json']:
   p=base/name
   if p.exists():out[str(p)]=sha(p)
  for sub in ['results','evaluation','grades','post']:
   for p in(base/sub).rglob('*'):
    if p.is_file()and p.suffix in('.json','.md','.py'):out[str(p)]=sha(p)
 return out

def prepare():
 from protected_run import copy_reference
 assert not(ROOT/'freeze.json').exists()
 manifest={};external={}
 for case,n in c.units():
  source=ORIGINAL/'results/shared'/f'{case["id"]}_r{n}'
  request=source/'final/request.json';provider=source/'final/initial.result.json';delivery=source/'final.delivered_evidence.json'
  from diff48 import diff_evidence
  from loop_policy import initial_schema
  from prompts_lsp48 import changed_operations
  from openai_transport import request_body,admission
  context=load(ROOT/'contexts'/case['id']/'context_LSP.json');es=diff_evidence(case['diff'])+context['evidence'];schema,cat=initial_schema(es,changed_operations(case,es));raw=c.prompt(case,es,{},c.INITIAL_TASK,cat)
  assert request_body(raw,schema)==load(request),'Initial request differs; no model may run'
  assert admission(raw,schema,final_reserve=65000)['admitted']
  manifest[f'results/shared/{case["id"]}_r{n}/final']=dict(request=str(request),request_sha256=sha(request),provider_result=str(provider),provider_sha256=sha(provider),delivery=str(delivery),delivery_sha256=sha(delivery))
  for p in(request,provider,delivery):external[str(p)]=sha(p)
  p=Path(CONFIG['baseline'])/'results/reviews'/f'{case["id"]}_{CONFIG["baseline_arm"]}_r{n}'/'review.result.json'
  copy_reference(case,n,'BASE',p)
 write_new(ROOT/'prepared/exact_reuse.json',dict(calls=manifest,selection='Exact counterpart initial request only; no output selection.'))
 write_new(ROOT/'audit/prior_seal.json',dict(files=refs(),external_initial=external))
 write_new(ROOT/'prepared/step.json',CONFIG)

def verify():
 f=load(ROOT/'freeze.json')
 for p,h in f['files'].items():assert sha(ROOT/p)==h,p
 for p,h in f.get('external_code',{}).items():assert sha(p)==h,p
 for group in load(ROOT/'audit/prior_seal.json').values():
  for p,h in group.items():assert sha(p)==h,p

def freeze():
 assert load(ROOT/'audit/tests.json')['passed'];assert load(ROOT/'audit/search_smoke.json')['passed']
 files={}
 for directory in [ROOT,ROOT/'post',ROOT/'prepared',ROOT/'private',ROOT/'audit']:
  for p in directory.iterdir():
   if p.is_file()and p.suffix in('.py','.json','.md'):files[str(p.relative_to(ROOT))]=sha(p)
 for directory in [ROOT/'contexts',ROOT/'tools']:
  for p in directory.rglob('*'):
   if p.is_file():files[str(p.relative_to(ROOT))]=sha(p)
 external_code={str(Path(m.__file__).resolve()):sha(Path(m.__file__).resolve())for m in list(sys.modules.values())if getattr(m,'__file__',None)and str(getattr(m,'__file__','')).endswith('.py')and Path(m.__file__).resolve().is_relative_to(PROJECT)and not Path(m.__file__).resolve().is_relative_to(ROOT)}
 write_new(ROOT/'freeze.json',dict(files=files,external_code=external_code,step=CONFIG,budgets=BUDGETS,same_initial_36=True,model_calls_before_freeze=0,scoring='Existing grading48.py primary cause and valid-report F1; all reports remain in denominator; unknown != false.',model='gpt-5.6-sol',created=time.time()))

def finish(item):
 case,n=item;r=c.branch(case,n,'FOUR_QUERY');r=copy.deepcopy(r);r.update(review_id=f'{case["id"]}_{CONFIG["arm"]}_r{n}',arm=CONFIG['arm'])
 write_new(ROOT/'results/reviews'/r['review_id']/'review.result.json',r);return r

def reviewers():
 verify();configure()
 from protected_run import initial_stage
 items=c.units()
 # Exact initial reuse is mandatory; no new schema/initial-generation effect.
 with ThreadPoolExecutor(max_workers=WORKERS)as pool:list(pool.map(initial_stage,items))
 assert not any(not(ROOT/'results/shared'/f'{case["id"]}_r{n}'/'final/reused.actual.json').exists()for case,n in items)
 with ThreadPoolExecutor(max_workers=4)as pool:list(pool.map(lambda x:c.common_prefix(*x),items))
 with ThreadPoolExecutor(max_workers=WORKERS)as pool:list(pool.map(lambda x:c.extra_replan(*x),items))
 with ThreadPoolExecutor(max_workers=WORKERS)as pool:list(pool.map(finish,items))
 write_new(ROOT/'audit/reviewers_complete.json',dict(complete=True,logical_units=36,new_reviewer_calls=108,initial_references=36))

def judges():
 import evaluate_lsp48 as e
 from evidence_delivery import common_packet
 from evaluator_schema import judge_schema
 manifest=ROOT/'private/judge_manifest.json'
 if manifest.exists():
  with ThreadPoolExecutor(max_workers=WORKERS)as pool:list(pool.map(e.safe_review_group,load(manifest)['groups']))
  return
 old=e.verify_reused();truth=e.truth();groups={};references={}
 for case in c.cases():
  rs=[load(ROOT/'results/reviews'/f'{case["id"]}_{arm}_r{n}'/'review.result.json')for arm in ARMS for n in range(1,4)]
  packet=common_packet(case,truth[case['id']],load(old['fixed_source_packets'][case['id']]['path']),rs,expected_repeats=[1,2,3])
  p=ROOT/'private/common_judge_packets'/f'{case["id"]}.json';write_new(p,packet);references[str(p)]=sha(p)
  for r in rs:
   raw,schema=e.semantic_input(r);key=digest(dict(case_id=case['id'],raw=raw,schema=schema));groups.setdefault(key,dict(key=key,case_id=case['id'],representative=r['review_id'],review_ids=[],semantic_input_sha256=digest(raw),schema_sha256=digest(schema)))['review_ids'].append(r['review_id'])
 write_new(ROOT/'private/judge_manifest.json',dict(references=references,groups=list(groups.values()),sealed_before_judges=True,human_review=False))
 with ThreadPoolExecutor(max_workers=WORKERS)as pool:list(pool.map(e.safe_review_group,groups.values()))

def summarize():
 import evaluate_lsp48 as e
 import post.aggregate_lsp as ag
 import grading48 as g
 truth=e.truth();subset=load(ROOT/'private/source_supported_subset.json')['supported_primary_target_ids'];rows=[]
 for case in c.cases():
  for arm in ARMS:
   for n in range(1,4):
    rid=f'{case["id"]}_{arm}_r{n}';r=load(ROOT/'results/reviews'/rid/'review.result.json');gp=ROOT/'grades/final'/f'{rid}.json';fp=ROOT/'grades/final_failures'/f'{rid}.json';assert gp.exists()or fp.exists(),rid
    rows.append(ag.make_row(truth[case['id']],arm,n,r,load(gp)if gp.exists()else None,load(fp)['annotation_status']if fp.exists()else'complete',subset[case['id']],case))
 scores={arm:ag.summary([r for r in rows if r['arm']==arm])for arm in ARMS};ag.CONTRASTS=((CONFIG['arm'],'BASE'),);intervals=ag.paired_bootstrap(rows)
 keep=lost=added=0
 for case,n in c.units():
  ids=lambda arm:{x['matched_target_id']for x in next(r for r in rows if r['case_id']==case['id']and r['repeat']==n and r['arm']==arm)['finding_labels']if x['primary_cause_tp']}
  a,b=ids('BASE'),ids(CONFIG['arm']);keep+=len(a&b);lost+=len(a-b);added+=len(b-a)
 physical=[]
 for p in(ROOT/'results').rglob('initial.result.json'):
  if(p.parent/'reused.actual.json').exists():continue
  v=load(p);physical.append(dict(phase='reviewer',calls=v.get('physical_attempt_count',0),tokens=total_usage(v),path=str(p)))
 for p in(ROOT/'evaluation').rglob('initial.result.json'):
  v=load(p);physical.append(dict(phase='judge',calls=v.get('physical_attempt_count',0),tokens=total_usage(v),path=str(p)))
 operational={};violations=[]
 for arm in ARMS:
  rs=[load(ROOT/'results/reviews'/r['review_id']/'review.result.json')for r in rows if r['arm']==arm]
  operational[arm]=dict(logical_model_calls=sum(x['logical_model_calls']for x in rs),known_model_tokens=sum(x['known_model_tokens']or 0 for x in rs),native_calls=sum(x['context_cost']['native_calls']for x in rs),source_tokens_mean=sum(x['source_tokens']for x in rs)/36)
  for r in rs:
   if(r['known_model_tokens']or 0)>400000 or r['logical_model_calls']>4 or r['source_tokens']>12000 or r['context_cost']['native_calls']>48 or r['context_cost']['file_reads']>256 or r['context_cost']['native_seconds']>180:violations.append(r['review_id'])
 value=dict(complete=True,step=CONFIG,development_only=True,human_review=False,scores=scores,paired_bootstrap=intervals,retention=dict(retained=keep,lost=lost,added=added,reference_tp=keep+lost),costs=operational,physical_usage=physical,known_new_tokens=sum(v['tokens']or 0 for v in physical),new_physical_calls=sum(v['calls']for v in physical),usage_unknown=sum(v['calls']and v['tokens']is None for v in physical),budget_violations=violations)
 write_new(ROOT/'post/rows.json',rows);write_new(ROOT/'post/aggregate.json',value)
 pct=lambda v:f'{100*v:.2f}%'
 lines=[f'# 단계{CONFIG["step"]}: {CONFIG["label"]}','', '같은12개 개발PR×3반복·같은 실제 초기응답36개. 기존 결과는 읽기 전용 참조. 각조건 공통400k token/최대4모델호출/source12k/native48·180초/file256. 실제사용량은 같지 않다. 비맹검AI·인간검토0. 기존 점수는 교체하지 않는다.','', '| 조건 | 원인 F1 | 유효 보고 F1 | 원인 정탐 | 재현율 | 확인 오탐 | 의미미해결 / 미판독 |','|---|---:|---:|---:|---:|---:|---:|']
 for arm,s in scores.items():lines.append(f'| {arm} | {pct(s["cause"]["f1"])} | {pct(s["valid_report"]["f1"])} | {s["cause"]["tp"]} | {pct(s["cause"]["recall"])} | {s["actual_confirmed_false_findings"]} | {s["unresolved_findings"]} / {s["ungraded_raw_findings"]} |')
 lines+=['',f'직전단계 대비 정탐 유지{keep} / 소실{lost} / 추가{added}. 판독실패는오탐으로바꾸지않는다. 원인F1은정답표target의원인일치, 유효보고F1은이에위치·근거관련성·인용범위·출력정상까지요구한다. F1=2TP/(전체보고+정답target), 정답표밖참보고를확인오탐으로세지않는다.','',f'신규 물리 API {value["new_physical_calls"]}호출 / 알려진 token {value["known_new_tokens"]:,} / usage미상 {value["usage_unknown"]}. 예산위반 {len(violations)}.','', 'paired bootstrap(저장소단위10,000회; 확인된TP에조건부):']
 for v in intervals:lines.append(json.dumps(v,ensure_ascii=False))
 lines+=['', '단계별 차이는 해당 개발조건에서의 추가효과이며 독립 확증/일반화 증거가 아니다. 본문확보·기계적유효성·의미관련성·최종정답을구분한다.','']
 report='\n'.join(lines);write_new(ROOT/'RESULTS_KO.md',report);write_new(PROJECT/'paper'/f'LSP_STEP_{CONFIG["step"]}_20261105_KO.md',report)
 verify();write_new(ROOT/'completion.json',dict(complete=True,freeze_sha256=sha(ROOT/'freeze.json'),aggregate_sha256=sha(ROOT/'post/aggregate.json'),new_physical_calls=value['new_physical_calls']))
 print(json.dumps(dict(step=CONFIG['step'],complete=True,scores={a:s['cause']for a,s in scores.items()},retention=value['retention']),ensure_ascii=False),flush=True)

def pipeline():
 if not(ROOT/'audit/reviewers_complete.json').exists():reviewers()
 judges()
 if not(ROOT/'completion.json').exists():summarize()

if __name__=='__main__':
 {'prepare':prepare,'freeze':freeze,'run':pipeline}[sys.argv[1]if len(sys.argv)>1 else'run']()
