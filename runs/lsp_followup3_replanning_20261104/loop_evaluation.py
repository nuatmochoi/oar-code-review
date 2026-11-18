import json,copy,time
from concurrent.futures import ThreadPoolExecutor
from common import ROOT,PROJECT,WORKERS,REPEATS,load,sha,digest,write_new
from location_ids import verify_result
from openai_transport import total_usage
from causal_loop import cases,expected,ARMS,LABELS
def prepare_judges():
    import evaluate_lsp48 as e
    import grading48 as g
    from evidence_delivery import common_packet
    path=ROOT/'private/judge_manifest.json'
    if path.exists():return load(path)
    assert all((ROOT/'results/reviews'/rid/'review.result.json').exists()for rid in expected())
    old=e.verify_reused();truth=e.truth();refs={}
    for c in cases():
        results=[]
        for a in ARMS:
            for n in range(1,REPEATS+1):
                p=ROOT/'results/reviews'/f'{c["id"]}_{a}_r{n}'/'review.result.json';r=load(p)
                if r['final_produced']:verify_result(r)
                results.append(r);refs[str(p)]=sha(p)
        packet=common_packet(c,truth[c['id']],load(old['fixed_source_packets'][c['id']]['path']),results,expected_repeats=list(range(1,REPEATS+1)))
        p=ROOT/'private/common_judge_packets'/f'{c["id"]}.json';write_new(p,packet);refs[str(p)]=sha(p)
    groups={}
    for rid in expected():
        r=load(ROOT/'results/reviews'/rid/'review.result.json');raw,schema=e.semantic_input(r);key=digest(dict(case_id=r['case_id'],raw=raw,schema=schema))
        groups.setdefault(key,dict(key=key,case_id=r['case_id'],representative=rid,review_ids=[],semantic_input_sha256=digest(raw),schema_sha256=digest(schema)))['review_ids'].append(rid)
    value=dict(sealed_before_judges=True,references=refs,groups=list(groups.values()),planned_reviews=len(expected()),no_retry=True,human_review=False)
    write_new(path,value);return value

def run_judges():
    import evaluate_lsp48 as e
    m=prepare_judges()
    with ThreadPoolExecutor(max_workers=WORKERS)as p:list(p.map(e.safe_review_group,m['groups']))

def summary(arms=ARMS):
    import evaluate_lsp48 as e
    import grading48 as g
    from post.aggregate_lsp import make_row,summary as summarize,paired_bootstrap
    truth=e.truth();subset=load(ROOT/'private/source_supported_subset.json')['supported_primary_target_ids'];rows=[]
    for c in cases():
        for a in arms:
            for n in range(1,REPEATS+1):
                rid=f'{c["id"]}_{a}_r{n}';r=load(ROOT/'results/reviews'/rid/'review.result.json');gp=ROOT/'grades/final'/f'{rid}.json';fp=ROOT/'grades/final_failures'/f'{rid}.json'
                assert gp.exists()or fp.exists(),rid
                rows.append(make_row(truth[c['id']],a,n,r,load(gp)if gp.exists()else None,load(fp)['annotation_status']if fp.exists()else'complete',subset[c['id']],c))
    scores={a:summarize([r for r in rows if r['arm']==a])for a in arms}
    import post.aggregate_lsp as ag
    ag.CONTRASTS=(('THREE','INITIAL'),('FOUR_SAME','THREE'),('FOUR_QUERY','FOUR_SAME'),('FOUR_QUERY','THREE'))
    bootstrap=paired_bootstrap(rows)
    transitions=[]
    for a,b in [('THREE','INITIAL'),('FOUR_SAME','INITIAL'),('FOUR_QUERY','INITIAL'),('FOUR_QUERY','THREE')]:
        for c in cases():
            for n in range(1,REPEATS+1):
                find=lambda arm:next(r for r in rows if r['case_id']==c['id']and r['repeat']==n and r['arm']==arm)
                target=lambda r:{v['matched_target_id']for v in r['finding_labels']if v['primary_cause_tp']}
                before,after=target(find(b)),target(find(a));transitions.append(dict(case_id=c['id'],repeat=n,after=a,before=b,retained=sorted(before&after),lost=sorted(before-after),added=sorted(after-before)))
    usage=[]
    for p in ROOT.glob('**/initial.result.json'):
        if any(v in p.parts for v in('cache','audit')):continue
        r=load(p);usage.append(dict(path=str(p),physical_calls=r.get('physical_attempt_count',0),tokens=total_usage(r)))
    mechanism=load(ROOT/'post/mechanism.json')
    costs={}
    execution={}
    for a in arms:
        rs=[load(ROOT/'results/reviews'/r['review_id']/'review.result.json')for r in rows if r['arm']==a]
        costs[a]=dict(logical_model_calls=sum(r['logical_model_calls']for r in rs),known_model_tokens=sum(r['known_model_tokens']or 0 for r in rs),unknown_model_usage=sum(r['known_model_tokens']is None for r in rs),native_calls=sum(r['context_cost']['native_calls']for r in rs),native_seconds=sum(r['context_cost']['native_seconds']for r in rs),file_reads=sum(r['context_cost']['file_reads']for r in rs),source_tokens_mean=sum(r['source_tokens']for r in rs)/len(rs))
        from causal_loop import shared_path
        records=[]
        if a!='INITIAL':
            for c in cases():
                for n in range(1,REPEATS+1):
                    shared=load(shared_path(c,n));records.extend(x for x in(shared['first'],shared['second'])if x)
                    trace=ROOT/'results/reviews'/f'{c["id"]}_{a}_r{n}'/'query_round_trace.json'
                    if trace.exists()and load(trace).get('additional_round'):records.append(load(trace)['additional_round'])
        answers=[q for rec in records for q in rec['core'].get('answers',[])]
        execution[a]=dict(rounds=len(records),plan_accepted=sum(bool(rec['core'].get('validation',{}).get('accepted'))for rec in records),questions=len(answers),question_rejected=sum(q['execution']=='question_rejected'for q in answers),backend_attempts=sum(rec['core'].get('backend_query_attempts',0)for rec in records),independent_reads=sum(len(rec.get('independent_reads',[]))for rec in records),withheld=sum(q['execution']=='withheld'for q in answers),answer_with_body_compatible_ids=sum(bool(q.get('mechanically_compatible_evidence_ids'))and q['question']['kind']=='implementation_body'for q in answers),new_evidence_ids=sum(len(rec['core'].get('new_evidence_ids',[]))for rec in records),scope='Structural/source availability counts; neither semantic relevance nor causal truth.')
    value=dict(complete=True,development_only=True,human_review=False,cases=len(cases()),repeats=REPEATS,reviews=len(rows),scores=scores,costs=costs,execution=execution,mechanism=mechanism['scores'],mechanism_failures=len(mechanism['failures']),paired_bootstrap=bootstrap,target_transitions=transitions,usage=usage,freeze_sha256=sha(ROOT/'freeze.json'))
    write_new(ROOT/'post/aggregate.json',value);write_new(ROOT/'post/rows.json',rows)
    lines=['# 추가 실험 3: 실제 추가 재계획·재조회와 같은본문 추가호출 비교','',f'같은 개발 PR {len(cases())}개(Python6·Go6)×{REPEATS}반복. 인간 검토0, AI 비맹검 판독. 전 조건 공개 설명·전체 diff·동일 최종 reviewer/schema/위치 ID 방식. INITIAL=실제 초기1호출, THREE=3호출, FOUR_SAME/FOUR_QUERY=4호출. 동일 초기·첫2차 수집·계획을 공유하며 추가 계획도 두4호출 조건이 공유한다.','', '| 실제 기능 | 원인 F1 | 유효 보고 F1 | 재현율 | 원인 TP | 보고 | 확인 오탐 | 의미 미해결 / 미판독 |','|---|---:|---:|---:|---:|---:|---:|---:|']
    pct=lambda v:'미상'if v is None else f'{100*v:.2f}%'
    for a in arms:
        v=scores[a];lines.append(f'| {LABELS[a]} | {pct(v["cause"]["f1"])} | {pct(v["valid_report"]["f1"])} | {pct(v["cause"]["recall"])} | {v["cause"]["tp"]} | {v["raw_reports"]} | {v["actual_confirmed_false_findings"]} | {v["unresolved_findings"]} / {v["ungraded_raw_findings"]} |')
    lines+=['','| 실제 기능 | 논리 모델 호출 | 모델 토큰 | native 호출/초 | 파일 읽기 | 평균 source 토큰 |','|---|---:|---:|---:|---:|---:|']
    for a,v in costs.items():lines.append(f'| {LABELS[a]} | {v["logical_model_calls"]} | {v["known_model_tokens"]:,} | {v["native_calls"]} / {v["native_seconds"]:.1f} | {v["file_reads"]} | {v["source_tokens_mean"]:.1f} |')
    lines+=['','| 실제 기능 | 구조 수락 / 계획 | 질문 | 거절 | backend 시도 | 독립 조회 | 조회 보류 |','|---|---:|---:|---:|---:|---:|---:|']
    for a,v in execution.items():lines.append(f'| {LABELS[a]} | {v["plan_accepted"]} / {v["rounds"]} | {v["questions"]} | {v["question_rejected"]} | {v["backend_attempts"]} | {v["independent_reads"]} | {v["withheld"]} |')
    lines+=['','| 경로 | 초기 반박 확인 후보 | 실제 교정 보고 | 교정률 | 맞던 후보가 반박된 설명으로 변경 | 새 반박 본문 확보 |','|---|---:|---:|---:|---:|---:|']
    for a,v in mechanism['scores'].items():lines.append(f'| {LABELS[a]} | {v["initial_refuted"]} | {v["strict_corrected"]} | {pct(v["strict_correction_rate"])} | {v["supported_to_refuted"]} | {v["new_refutation_delivered"]} |')
    lines+=['',f'별도 원인 교정 판독 실패 {len(mechanism["failures"])}문맥. 교정 성공에는 초기 원인 반박 근거, 실제 최종 전 전달, 같은 실패 조사의 명시적 원인 수정, 최종 보고와 독립 원인 판독 정답이 모두 필요하다. 초기 반박 후보가 없으면 교정률은 미상으로 남긴다. 본문 확보·의미적 반박·반박 반영·최종 정답은 별도 기록하며 모든 의미 판단은 비맹검 AI다.','']
    lines+=['','총 모델200k/source12k/native48/180초/file256/active900초 상한은 같다. 실제 비용은 구분한다. 초기 LSP 전체근거는 보존한다. 추가 계획 후 실제 조회가 실행된 조건과 같은본문 대조군을 비교하며 controller는 의미정답을 보장하지 않는다. 원인 F1과 유효 보고 F1은 기존 정의를 유지하며 primary 미매칭을 전부 오탐으로 세지 않는다.','',f'모델 사용량 확인 합계: {sum(v["tokens"]or 0 for v in usage):,}토큰. usage 미확인 {sum(v["physical_calls"]and v["tokens"]is None for v in usage)}건. source/native 비용은 contexts별 원장에 보존.','', '기존 동결 결과는 수정하지 않았고 새 위치 ID 선택은 모든 조건에 공통 적용했다. 과거 48개/최근5문맥 점수와의 차이를 이번 검색 정책 효과로 해석하지 않는다.','']
    report='\n'.join(lines);write_new(ROOT/'RESULTS_KO.md',report);write_new(PROJECT/'paper/LSP_FOLLOWUP_3_REPLANNING_20261104_KO.md',report)
    return value

def pipeline():
 from stage_freeze import verify,preserve
 import causal_loop
 verify()
 if not(ROOT/'audit/reviewers_complete.json').exists():causal_loop.run()
 run_judges()
 if not(ROOT/'post/mechanism.json').exists():
  from mechanism_evaluation import run as run_mechanism
  run_mechanism()
 if not(ROOT/'post/aggregate.json').exists():summary()
 write_new(ROOT/'completion.json',dict(experiment_complete=True,stage=3,reviews=len(expected()),freeze=verify(),preservation=preserve(),results_sha256=sha(ROOT/'post/aggregate.json')))
 print(json.dumps(dict(stage_complete=3,scores={a:v['cause']for a,v in load(ROOT/'post/aggregate.json')['scores'].items()})),flush=True)
if __name__=='__main__':pipeline()
