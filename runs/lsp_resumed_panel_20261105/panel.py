"""Separate common-source evaluation, never modifies previous grades/results."""
import copy,json,time,sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from common import ROOT,PROJECT,ARMS,REPEATS,WORKERS,load,write_new,sha,digest
from openai_transport import total_usage
from location_ids import verify_result
import evaluate_lsp48 as e
import post.aggregate_lsp as ag

RUNS=dict(PRIOR='lsp_followup5_protected_search_20261104',SEARCH='lsp_step1_search_20261105',SOURCE='lsp_step2b_strict_budget_20261105',OLD_REPORT='lsp_step3_report_decisions_20261105',REPORT='lsp_step3b_scope_contract_20261105',RECOVER='lsp_step4b_goal_recovery_20261105')
LABELS={'DIFF':'공개 설명+diff·리뷰1회','AST':'diff+AST 본문6k·1회','LSP':'diff+AST/LSP 본문6k·1회','INITIAL':'공통 초기 후보·리뷰1회','PRIOR':'기존 조회 모드 분리·예약·4회','SEARCH':'검색 범위 분리·복수 후보·4회','SOURCE':'검색+다중언어/의존성 소스·엄격 상한·4회','OLD_REPORT':'이전 분리된 scope 출력·4회','REPORT':'동일 본문+claim·condition 검증 정보 결속·4회','RECOVER':'위 기능+실패별 재조회·위임 조사·4회'}
CONTRASTS=(('AST','DIFF'),('LSP','AST'),('PRIOR','LSP'),('SEARCH','PRIOR'),('SOURCE','SEARCH'),('REPORT','SOURCE'),('REPORT','OLD_REPORT'),('OLD_REPORT','SOURCE'),('RECOVER','REPORT'),('RECOVER','LSP'),('PRIOR','INITIAL'),('SEARCH','INITIAL'),('SOURCE','INITIAL'),('REPORT','INITIAL'),('RECOVER','INITIAL'))

def result_path(case,n,arm):
 original=PROJECT/'runs'/RUNS['PRIOR']
 if arm in('DIFF','AST','LSP'):base,prior=original,arm
 elif arm=='INITIAL':base,prior=original,'PROTECTED_INITIAL'
 elif arm=='PRIOR':base,prior=original,'PROTECTED'
 elif arm=='OLD_REPORT':base,prior=PROJECT/'runs'/RUNS[arm],'REPORT'
 elif arm=='REPORT':base,prior=PROJECT/'runs'/RUNS[arm],'REPORT2'
 else:base,prior=PROJECT/'runs'/RUNS[arm],arm
 return base/'results/reviews'/f'{case["id"]}_{prior}_r{n}'/'review.result.json'

def prepare():
 from evidence_delivery import common_packet
 assert all((PROJECT/'runs'/name/'completion.json').exists()for name in RUNS.values())
 references={};ts=e.truth();old=e.verify_reused();groups={}
 for case in e.cases():
  results=[]
  for arm in ARMS:
   for n in range(1,4):
    p=result_path(case,n,arm);r=copy.deepcopy(load(p));references[str(p)]=sha(p);r.update(review_id=f'{case["id"]}_{arm}_r{n}',arm=arm,copied_reference=dict(path=str(p),sha256=sha(p)),final_origin='separate_panel_actual_response_reference',generation_in_this_panel=False)
    if r['final_produced']:verify_result(r)
    write_new(ROOT/'results/reviews'/r['review_id']/'review.result.json',r);results.append(r)
  packet=common_packet(case,ts[case['id']],load(old['fixed_source_packets'][case['id']]['path']),results,expected_repeats=[1,2,3]);write_new(ROOT/'private/common_judge_packets'/f'{case["id"]}.json',packet)
  for r in results:
   raw,schema=e.semantic_input(r);key=digest(dict(case_id=case['id'],raw=raw,schema=schema));groups.setdefault(key,dict(key=key,case_id=case['id'],representative=r['review_id'],review_ids=[],semantic_input_sha256=digest(raw),schema_sha256=digest(schema)))['review_ids'].append(r['review_id'])
 for name in RUNS.values():
  base=PROJECT/'runs'/name
  for p in[base/'freeze.json',base/'completion.json',base/'post/aggregate.json']:references[str(p)]=sha(p)
  for sub in ['results','grades','evaluation','post']:
   for p in(base/sub).rglob('*.json'):references[str(p)]=sha(p)
 interruption=PROJECT/'runs'/RUNS['RECOVER']/'audit/infrastructure_interruption_20261105.json'
 excluded=[]
 if interruption.exists():
  references[str(interruption)]=sha(interruption)
  excluded=[dict(case_id=x['case_id'],repeat=x['repeat'])for x in load(interruption)['requests']]
 write_new(ROOT/'private/judge_manifest.json',dict(groups=list(groups.values()),references=references,planned_reviews=360,new_reviewer_calls=0,sealed_before_judges=True,human_review=False,infrastructure_excluded_units=excluded,exclusion_selection='Only unpersisted request records in interruption audit; determined before this panel judges, never scores.'))
 files={}
 for directory in[ROOT,ROOT/'prepared',ROOT/'private',ROOT/'post']:
  for p in directory.iterdir():
   if p.is_file()and p.suffix in('.json','.py','.md'):files[str(p.relative_to(ROOT))]=sha(p)
 for p in(ROOT/'private/common_judge_packets').glob('*.json'):files[str(p.relative_to(ROOT))]=sha(p)
 write_new(ROOT/'freeze.json',dict(files=files,model_calls_before_freeze=0,new_reviewer_calls=0,arms=ARMS,planned_reviews=360,reason='Common source evaluation; prior grades and failures remain immutable.',created=time.time()))

def verify():
 for p,h in load(ROOT/'freeze.json')['files'].items():assert sha(ROOT/p)==h,p
 for p,h in load(ROOT/'private/judge_manifest.json')['references'].items():assert sha(p)==h,p

def transitions(rows,before,after):
 out=[]
 for case in e.cases():
  for n in range(1,4):
   def ids(arm):
    r=next(r for r in rows if r['case_id']==case['id']and r['repeat']==n and r['arm']==arm)
    return{v['matched_target_id']for v in r['finding_labels']if v['primary_cause_tp']}
   b,a=ids(before),ids(after);out.append(dict(case_id=case['id'],repeat=n,before=before,after=after,retained=sorted(a&b),lost=sorted(b-a),added=sorted(a-b)))
 return out

def summary():
 ts=e.truth();subset=load(ROOT/'private/source_supported_subset.json')['supported_primary_target_ids'];rows=[]
 for case in e.cases():
  for arm in ARMS:
   for n in range(1,4):
    rid=f'{case["id"]}_{arm}_r{n}';r=load(ROOT/'results/reviews'/rid/'review.result.json');good=ROOT/'grades/final'/f'{rid}.json';bad=ROOT/'grades/final_failures'/f'{rid}.json';assert good.exists()or bad.exists(),rid
    rows.append(ag.make_row(ts[case['id']],arm,n,r,load(good)if good.exists()else None,load(bad)['annotation_status']if bad.exists()else'complete',subset[case['id']],case))
 scores={a:ag.summary([r for r in rows if r['arm']==a])for a in ARMS};ag.CONTRASTS=CONTRASTS;intervals=ag.paired_bootstrap(rows);pairs=[('DIFF',a)for a in ARMS if a!='DIFF']+[(b,a)for a,b in CONTRASTS];tr=[]
 for a,b in dict.fromkeys(pairs):tr+=transitions(rows,a,b)
 costs={};violations={}
 for arm in ARMS:
  rs=[load(ROOT/'results/reviews'/r['review_id']/'review.result.json')for r in rows if r['arm']==arm]
  costs[arm]=dict(logical_model_calls=sum(r['logical_model_calls']for r in rs),known_model_tokens=sum(r['known_model_tokens']or 0 for r in rs),model_usage_unknown_units=sum(r['known_model_tokens']is None for r in rs),source_tokens_mean=sum(r['source_tokens']for r in rs)/36,native_calls=sum(r['context_cost'].get('native_calls',0)for r in rs),file_reads=sum(r['context_cost'].get('file_reads',0)for r in rs),native_lower_bound_units=sum(bool(r.get('cost_completeness'))for r in rs))
  violations[arm]=[r['review_id']for r in rs if(r['known_model_tokens']or 0)>400000 or r['logical_model_calls']>4 or r['source_tokens']>12000 or r['context_cost'].get('native_calls',0)>48 or r['context_cost'].get('native_seconds',0)>180 or r['context_cost'].get('file_reads',0)>256]
 use=[dict(path=str(p),calls=load(p)['physical_attempt_count'],tokens=total_usage(load(p)))for p in(ROOT/'evaluation').rglob('initial.result.json')]
 excluded={(x['case_id'],x['repeat'])for x in load(ROOT/'private/judge_manifest.json').get('infrastructure_excluded_units',[])}
 secondary={a:ag.summary([r for r in rows if r['arm']==a and(r['case_id'],r['repeat'])not in excluded])for a in ARMS}
 value=dict(complete=True,cases=12,repeats=3,reviews=360,scores=scores,paired_bootstrap=intervals,target_transitions=tr,costs=costs,budget_violations=violations,new_reviewer_calls=0,new_judge_calls=sum(x['calls']for x in use),known_judge_tokens=sum(x['tokens']or 0 for x in use),unknown_usage=sum(x['calls']and x['tokens']is None for x in use),human_review=False,development_only=True,infrastructure_sensitivity=dict(excluded_units=[dict(case_id=c,repeat=n)for c,n in sorted(excluded)],units_per_arm=36-len(excluded),scores=secondary,selection='Infrastructure audit only; primary full36 preserved.'))
 write_new(ROOT/'post/rows.json',rows);write_new(ROOT/'post/aggregate.json',value)
 pct=lambda v:'평가불가'if v is None else f'{100*v:.2f}%'
 lines=['# 순차 개선 검증: 같은 source로 판독한 통합 결과','', '같은12개 개발PR(Python 주언어6·Go 주언어6)×3반복,10조건360개 실제응답. 개선조건은같은run5초기36응답을사용한다. 과거결과/실패보존, 새reviewer0인별도통합판독이다. 같은PR의모든조건에공통source packet을쓴다. 비맹검AI/인간0/개발사례이며일반화증거가아니다.','', '| 실제 기능 | 원인 F1 | 유효 보고 F1 | 재현율 | diff 정탐 보존 | 확인 참 보고¹ | 확인 오탐 | 의미 미해결 / 미판독 |','|---|---:|---:|---:|---:|---:|---:|---:|']
 for arm,s in scores.items():
  if arm=='DIFF':keep=den=s['cause']['tp']
  else:
   rs=[r for r in tr if r['before']=='DIFF'and r['after']==arm];keep=sum(len(r['retained'])for r in rs);den=keep+sum(len(r['lost'])for r in rs)
  lines.append(f'| {LABELS[arm]} | {pct(s["cause"]["f1"])} | {pct(s["valid_report"]["f1"])} | {pct(s["cause"]["recall"])} | {keep}/{den} | {s["all_functional_cause"]["tp"]+s["other_confirmed_defects"]} | {s["actual_confirmed_false_findings"]} | {s["unresolved_findings"]} / {s["ungraded_raw_findings"]} |')
 lines+=['', '¹ 확인 참 보고는전체functional target원인정탐+정답표밖확인참결함이다. 정답표밖참결함의동일원인보존은이개수만으로판정하지않는다. 원인F1은정답표결함의원인일치를요구한다. 유효보고F1은여기에회귀위치·실패연산·근거의관련성과기계적인용결속/정상출력을추가요구한다. 두F1=2TP/(전체보고+정답target). 정답표밖참결함/중복/미해결/미판독이분모에남으므로비정탐전체를확인오탐이라고하지않는다. 정탐보존은같은target×반복이며독립결함수/PR수가아니다.','', '| 비교 | 정탐 유지 | 소실 | 추가 |','|---|---:|---:|---:|']
 for before,after in [('INITIAL',x)for x in ['PRIOR','SEARCH','SOURCE','REPORT','RECOVER']]+[('PRIOR','SEARCH'),('SEARCH','SOURCE'),('SOURCE','REPORT'),('REPORT','RECOVER')]:
  if(before,after)not in pairs:ts2=transitions(rows,before,after)
  else:ts2=[r for r in tr if r['before']==before and r['after']==after]
  lines.append(f'| {LABELS[before]} → {LABELS[after]} | {sum(len(r["retained"])for r in ts2)} | {sum(len(r["lost"])for r in ts2)} | {sum(len(r["added"])for r in ts2)} |')
 lines+=['', '| 비교 | 원인 F1 차이 | 저장소 paired 95% 구간 |','|---|---:|---:|']
 for x in intervals:
  if x['metric']!='cause_f1':continue
  a,b=x['contrast'].split(' minus ');lo,hi=x['percentile_95_ci'];lines.append(f'| {LABELS[a]} − {LABELS[b]} | {100*x["observed_delta"]:+.2f}%p | {100*lo:+.2f} ~ {100*hi:+.2f}%p |')
 lines+=['', '| 실제 기능 | 논리 모델 호출 | 논리 모델 token | 평균 source token | native 호출 | 파일 읽기 | 예산위반 문맥 |','|---|---:|---:|---:|---:|---:|---:|---:|']
 for arm,c in costs.items():lines.append(f'| {LABELS[arm]} | {c["logical_model_calls"]} | {c["known_model_tokens"]:,} | {c["source_tokens_mean"]:.1f} | {c["native_calls"]} | {c["file_reads"]} | {len(violations[arm])} |')
 lines+=['', '공통상한400k모델token/최대4모델호출/source12k/native48·180초/file256/active900초。1호출기준군과4호출개선안의실제사용량은같지않다. SOURCE는상한가드가수정된2b이며원단계2의3문맥file257 결과를채택용행에섞지않았다. REPORT는SOURCE와본문/초기후보/이전계획이같고최종판단만새실행했다. RECOVER는같은초기에서계획을새생성하며실패별조회·file예산공정배분·위임함수조사를실행했다. 요소차이는누적순서에조건부이며각요소의독립/상호작용효과전체를입증하지않는다.','', f'이통합패널신규판독 {value["new_judge_calls"]}물리호출 / 알려진 token {value["known_judge_tokens"]:,} / usage미상 {value["unknown_usage"]}. 초기/계획/최종reviewer를다시호출하지않았다. 단계별신규비용은각별도MD에남긴다. 논리행비용을합쳐신규청구비용이라고하지않는다.','', '판독실패/미해결은성공으로채우거나FP로변경하지않는다. CI는확인TP에조건부이며판독자오류/불확실성을포괄하지않는다. 이통합패널과이전패널의절대점수차이를방법의성능향상으로계산하지않는다.','']
 lines+=['', 'RECOVER의4문맥은실행프로세스중단으로계획요청응답이미저장되어최종보고를만들지못했다. 재발급없이전체36개주분모에실패로보존했다. 해당요청4회의실제발급/청구/usage는미상이고실패문맥의native비용은초기기준하한이다. 위논리known token합을완전한총사용량으로해석하지않는다. 원시요청과중단감사를보존한다.','',f'인프라중단4문맥만모든조건에서공통제외한{36-len(excluded)}문맥보조민감도(새판독전중단기록으로선정;주결과대체아님):','', '| 실제 기능 | 원인 F1 | 유효 보고 F1 | 원인 정탐 |','|---|---:|---:|---:|']
 for arm,s in secondary.items():lines.append(f'| {LABELS[arm]} | {pct(s["cause"]["f1"])} | {pct(s["valid_report"]["f1"])} | {s["cause"]["tp"]} |')
 lines+=['', '원인교정률은이F1패널만으로측정하지않았다.','']
 report='\n'.join(lines).replace('초。','초.');write_new(ROOT/'RESULTS_KO.md',report);write_new(PROJECT/'paper/LSP_RESUMED_COMPARISON_20261105_KO.md',report);verify();write_new(ROOT/'completion.json',dict(complete=True,reviews=360,freeze_sha256=sha(ROOT/'freeze.json'),aggregate_sha256=sha(ROOT/'post/aggregate.json'),new_reviewer_calls=0))
 print(json.dumps(dict(complete=True,scores={k:v['cause']['f1']for k,v in scores.items()},failures={k:v['annotation_status_counts']for k,v in scores.items()})),flush=True)

def run():
 verify();m=load(ROOT/'private/judge_manifest.json')
 with ThreadPoolExecutor(max_workers=WORKERS)as pool:list(pool.map(e.safe_review_group,m['groups']))
 if not(ROOT/'completion.json').exists():summary()
if __name__=='__main__':{'prepare':prepare,'run':run}[sys.argv[1]]()
