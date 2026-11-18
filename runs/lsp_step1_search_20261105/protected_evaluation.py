import json,copy,time
from concurrent.futures import ThreadPoolExecutor
from common import ROOT,PROJECT,WORKERS,REPEATS,load,sha,digest,write_new
from location_ids import verify_result
from openai_transport import total_usage
from protected_run import cases,expected,ARMS,LABELS
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

def transitions(rows,before,after):
    out=[]
    for c in cases():
        for n in range(1,REPEATS+1):
            get=lambda a:next(r for r in rows if r['case_id']==c['id']and r['repeat']==n and r['arm']==a)
            ids=lambda r:{v['matched_target_id']for v in r['finding_labels']if v['primary_cause_tp']}
            b,a=ids(get(before)),ids(get(after));out.append(dict(case_id=c['id'],repeat=n,before=before,after=after,retained=sorted(a&b),lost=sorted(b-a),added=sorted(a-b)))
    return out

def cost_summary(rs):
    return dict(logical_model_calls=sum(r['logical_model_calls']for r in rs),known_model_tokens=sum(r['known_model_tokens']or 0 for r in rs),unknown_model_usage=sum(r['known_model_tokens']is None for r in rs),native_calls=sum(r['context_cost'].get('native_calls',0)for r in rs),native_seconds=sum(r['context_cost'].get('native_seconds',0)for r in rs),file_reads=sum(r['context_cost'].get('file_reads',0)for r in rs),source_tokens_mean=sum(r['source_tokens']for r in rs)/len(rs))

def query_execution():
    from protected_run import UP
    import causal_loop as c
    from collections import Counter
    source=ROOT.parent/'lsp_followup3_budget400k_20261104'
    result={}
    for arm in('CURRENT','PROTECTED'):
        records=[];prefix_kept=[];failure_reasons=Counter()
        for case in cases():
            for n in range(1,REPEATS+1):
                folder=(source if arm=='CURRENT'else ROOT)
                s=load(folder/'results/shared'/f'{case["id"]}_r{n}'/'shared.json');records.extend(r for r in(s['first'],s['second'])if r)
                tp=folder/'results/reviews'/f'{case["id"]}_FOUR_QUERY_r{n}'/'query_round_trace.json'
                if tp.exists():
                    t=load(tp);prefix_kept.append(t['source_prefix_preserved'])
                    if t['additional_round']:records.append(t['additional_round'])
                else:prefix_kept.append(False)
        answers=[q for rec in records for q in rec['core'].get('answers',[])]
        for q in answers:
            for u in q.get('unresolved',[]):failure_reasons[str(u.get('reason'))]+=1
        result[arm]=dict(rounds=len(records),plan_accepted=sum(bool(r['core'].get('validation',{}).get('accepted'))for r in records),questions=len(answers),rejected=sum(q['execution']=='question_rejected'for q in answers),backend_attempts=sum(r['core'].get('backend_query_attempts',0)for r in records),neutral_reads=sum(len(r.get('independent_reads',[]))for r in records),compatible_body_answers=sum(q['question']['kind']=='implementation_body'and bool(q.get('mechanically_compatible_evidence_ids'))for q in answers),new_core_evidence_ids=sum(len(r['core'].get('new_evidence_ids',[]))for r in records),source_prefix_preserved=sum(prefix_kept),source_prefix_contexts=len(prefix_kept),unresolved_reasons=dict(failure_reasons),semantic_success_guaranteed=False)
    return result

def summarize_panel():
    import evaluate_lsp48 as e
    import post.aggregate_lsp as ag
    truth=e.truth();subset=load(ROOT/'private/source_supported_subset.json')['supported_primary_target_ids'];rows=[]
    for case in cases():
        for arm in ARMS:
            for n in range(1,REPEATS+1):
                rid=f'{case["id"]}_{arm}_r{n}';r=load(ROOT/'results/reviews'/rid/'review.result.json');gp=ROOT/'grades/final'/f'{rid}.json';fp=ROOT/'grades/final_failures'/f'{rid}.json';assert gp.exists()or fp.exists(),rid
                rows.append(ag.make_row(truth[case['id']],arm,n,r,load(gp)if gp.exists()else None,load(fp)['annotation_status']if fp.exists()else'complete',subset[case['id']],case))
    scores={a:ag.summary([r for r in rows if r['arm']==a])for a in ARMS}
    ag.CONTRASTS=(('AST','DIFF'),('LSP','AST'),('CURRENT','LSP'),('PROTECTED','CURRENT'),('PROTECTED','LSP'),('CURRENT','CURRENT_INITIAL'),('PROTECTED','PROTECTED_INITIAL'))
    bootstrap=ag.paired_bootstrap(rows);transition=[]
    pairs=[('DIFF',a)for a in ARMS if a!='DIFF']+[('AST','LSP'),('LSP','CURRENT'),('LSP','PROTECTED'),('CURRENT_INITIAL','CURRENT'),('PROTECTED_INITIAL','PROTECTED'),('CURRENT','PROTECTED')]
    for b,a in pairs:transition+=transitions(rows,b,a)
    retained={}
    for a in ARMS:
        b='DIFF'
        if b==a:den=sum(v['cause_tp']for v in rows if v['arm']==b);keep=den;lost=added=0
        else:
            ts=[t for t in transition if t['before']==b and t['after']==a];keep=sum(len(t['retained'])for t in ts);lost=sum(len(t['lost'])for t in ts);added=sum(len(t['added'])for t in ts);den=keep+lost
        retained[a]=dict(reference=b,retained=keep,reference_tp=den,lost=lost,added=added,rate=keep/den if den else None)
    costs={};limits={}
    for a in ARMS:
        rs=[load(ROOT/'results/reviews'/r['review_id']/'review.result.json')for r in rows if r['arm']==a]
        costs[a]=cost_summary(rs)
        limits[a]=dict(model_token_over=sum((r['known_model_tokens']or 0)>400000 for r in rs),model_calls_over=sum(r['logical_model_calls']>4 for r in rs),source_over=sum(r['source_tokens']>12000 for r in rs),native_call_over=sum(r['context_cost'].get('native_calls',0)>48 for r in rs),native_seconds_over=sum(r['context_cost'].get('native_seconds',0)>180 for r in rs),file_over=sum(r['context_cost'].get('file_reads',0)>256 for r in rs))
    usage=[dict(path=str(p),physical_calls=load(p)['physical_attempt_count'],tokens=total_usage(load(p)),phase='reviewer'if'fact_extraction'not in p.parts and'results'in p.parts else'evaluator')for base in('results','evaluation')for p in(ROOT/base).glob('**/initial.result.json')]
    effects=load(ROOT/'post/effect.json');execution=query_execution()
    metric=lambda arm:scores[arm]['cause']['f1'];better=metric('PROTECTED')>max(metric('LSP'),metric('CURRENT'));ci=next(v for v in bootstrap if v['contrast']=='PROTECTED minus LSP'and v['metric']=='cause_f1')['percentile_95_ci']
    truth_count=lambda a:scores[a]['all_functional_cause']['tp']+scores[a]['other_confirmed_defects']
    adopt=bool(better and ci[0]>0 and scores['PROTECTED']['valid_report']['f1']>=scores['LSP']['valid_report']['f1']and truth_count('PROTECTED')>=truth_count('LSP')and scores['PROTECTED']['actual_confirmed_false_findings']<=scores['CURRENT']['actual_confirmed_false_findings']and not any(limits['PROTECTED'].values()))
    value=dict(complete=True,cases=12,repeats=3,logical_reviews=len(rows),scores=scores,retention=retained,target_transitions=transition,costs=costs,budget_violations=limits,execution=execution,effect=effects['scores'],effect_failures=len(effects['failures']),paired_bootstrap=bootstrap,source_strata={a:ag.source_strata([r for r in rows if r['arm']==a])for a in ARMS},usage=usage,development_only=True,human_review=False,adoption_gate_passed=adopt,freeze_sha256=sha(ROOT/'freeze.json'))
    write_new(ROOT/'post/rows.json',rows);write_new(ROOT/'post/aggregate.json',value)
    pct=lambda x:'평가불가'if x is None else f'{100*x:.2f}%'
    lines=['# 조회 라우팅·후속 자원 예약 통합 비교','', '같은12개 개발 PR(Python 주언어6·Go 주언어6)×3반복. 실제 target 파일에는 다른 언어도 포함한다. 기존180개 응답은 보존된 실제 응답을 참조했고, 개선안의 초기·중간·최종144호출은 새 schema로 실행했다. 이 패널의 모든 응답을 같은 PR별 source packet으로 새 판독했다. 과거 점수를 교체하거나 과거 패널 대비 변화로 효과를 계산하지 않는다. 인간검토0·비맹검AI 판독이다.','', '| 실제 기능 | 원인 F1 | 유효 보고 F1 | 재현율 | 정탐 보존 | 보존 기준 | 소실 / 추가 | 확인 오탐 | 의미 미해결 / 미판독 |','|---|---:|---:|---:|---:|---|---:|---:|---:|']
    for a in ARMS:
        s=scores[a];r=retained[a];lines.append(f'| {LABELS[a]} | {pct(s["cause"]["f1"])} | {pct(s["valid_report"]["f1"])} | {pct(s["cause"]["recall"])} | {r["retained"]}/{r["reference_tp"]} ({pct(r["rate"])}) | {LABELS[r["reference"]]} | {r["lost"]} / {r["added"]} | {s["actual_confirmed_false_findings"]} | {s["unresolved_findings"]} / {s["ungraded_raw_findings"]} |')
    lines+=['','정탐 보존은 같은 canonical target×반복 기준이며 위 표는 모든 행에서 diff-only 정탐을 공통 기준으로 사용한다. diff행의 자기보존100%는 새로운 검증 효과가 아니다. 요소별·자기 초기 대비 소실/추가는 아래와 aggregate.json에 별도 제공한다. 정답표 밖 실제 참 보고 보존은 의미 매칭 감사와 구분한다.','', '| 단계 | 유지 | 소실 | 추가 |','|---|---:|---:|---:|']
    for b,a in[('DIFF','AST'),('AST','LSP'),('LSP','CURRENT'),('CURRENT','PROTECTED'),('CURRENT_INITIAL','CURRENT'),('PROTECTED_INITIAL','PROTECTED')]:
        ts=[t for t in transition if t['before']==b and t['after']==a];lines.append(f'| {LABELS[b]} → {LABELS[a]} | {sum(len(t["retained"])for t in ts)} | {sum(len(t["lost"])for t in ts)} | {sum(len(t["added"])for t in ts)} |')
    lines+=['','| 실제 기능 | 모델 호출(논리) | 모델 token(논리) | native 호출 / 초 | 파일 | 평균 source token |','|---|---:|---:|---:|---:|---:|']
    for a,v in costs.items():lines.append(f'| {LABELS[a]} | {v["logical_model_calls"]} | {v["known_model_tokens"]:,} | {v["native_calls"]} / {v["native_seconds"]:.1f} | {v["file_reads"]} | {v["source_tokens_mean"]:.1f} |')
    lines+=['','공통 상한은 모델400k·최대4호출·source12k·native48/180초·file256·active900초다. diff/AST/LSP의 실제 source와 호출수는 더 적으며 compute가 같다는 뜻이 아니다. AST↔LSP는 같은6k/1호출이고, 기존4호출↔개선4호출이 주 정책 비교다.','', '| 탐색 | 질문 | 거절 | backend 시도 | 중립 조회 | body 호환 응답 | 초기 LSP 근거 보존 |','|---|---:|---:|---:|---:|---:|---:|']
    for a,v in execution.items():lines.append(f'| {LABELS[a]} | {v["questions"]} | {v["rejected"]} | {v["backend_attempts"]} | {v["neutral_reads"]} | {v["compatible_body_answers"]} | {v["source_prefix_preserved"]}/{v["source_prefix_contexts"]} |')
    lines+=['','본문 호환/ID 확보는 의미 정답이나 runtime 경로 증명이 아니다. 원래 거절된 route를 살리지 않고 허가된 source를 읽은 것은 중립 조회로 따로 기록한다. 기존 참조 fan-out/동적 dispatch/본문 크기 한계는 유지한다.','', '| 탐색 | 초기 참 보고 | 유지한 참 보고 | 초기 정답표밖 참 보고 | 유지한 정답표밖 참 보고 | 초기 반박 후보 / 엄격 교정 |','|---|---:|---:|---:|---:|---:|']
    for a,v in effects['scores'].items():lines.append(f'| {LABELS[a]} | {v["initial_confirmed_true"]} | {v["retained_confirmed_true"]} | {v["initial_off_gold_true"]} | {v["retained_off_gold_true"]} | {v["initial_refuted"]} / {v["strict_corrected"]} |')
    lines+=['',f'기전·보존 감사 실패 {len(effects["failures"])}문맥. 이 표의 분모는 정상 감사 부분집합이며, 실패를 성공/오탐으로 수리하지 않았다. 같은 잘못된 조사 원인이 실제로 교정되고 source 반박이 최종 전 전달·반영되어 독립 최종 cause 판독까지 맞은 경우만 엄격교정이다. 무보고·삭제·보류는 성공이 아니다.','', '| 비교 | 원인 F1 차이 | 저장소 paired 95% 구간 |','|---|---:|---:|']
    for v in bootstrap:
        if v['metric']!='cause_f1':continue
        a,b=v['contrast'].split(' minus ');lo,hi=v['percentile_95_ci'];lines.append(f'| {LABELS[a]} − {LABELS[b]} | {100*v["observed_delta"]:+.2f}%p | {100*lo:+.2f}~{100*hi:+.2f}%p |')
    lines+=['',f'사전 채택 기준 통과: {adopt}. 단순 LSP와 기존 탐색보다 원인F1이 높고, LSP 대비 구간이0을 넘으며 유효보고·실제 참 보고·오탐·예산 조건을 함께 만족해야 한다. 개발 사례·AI 판독이며, 통과해도 미사용 PR 일반화 증거를 대신하지 않는다.','',f'이번 run 새 물리 API {sum(v["physical_calls"]for v in usage)}호출·알려진 token {sum(v["tokens"]or 0 for v in usage):,}. usage 미상 {sum(v["physical_calls"]and v["tokens"]is None for v in usage)}건. 참조한 기존180개 응답/과거수집 비용은 논리 표에는 포함하지만 신규 물리 비용에 다시 더하지 않았다.','', '개선안은 조회형식·읽기 복구·질문 순서·예약의 묶음 효과다. 각 요소의 독립 효과나 SCIP/runtime probe/완전한 slicing의 효과로 주장하지 않는다. 관련문헌·적용범위는 동결 PROTOCOL_KO.md에 기록했다.','']
    report='\n'.join(lines);write_new(ROOT/'RESULTS_KO.md',report);write_new(PROJECT/'paper/CAUSAL_REVIEW_PROTECTED_COMPARISON_20261105_KO.md',report);return value

def pipeline():
    from protected_run import verify,run
    verify()
    if(ROOT/'completion.json').exists():return load(ROOT/'completion.json')
    if not(ROOT/'audit/reviewers_complete.json').exists():run()
    run_judges()
    if not(ROOT/'post/effect.json').exists():
        from effect_audit import run as effect
        effect()
    if not(ROOT/'post/aggregate.json').exists():summarize_panel()
    from preservation import verify as preserve
    verify();write_new(ROOT/'completion.json',dict(complete=True,freeze_sha256=sha(ROOT/'freeze.json'),aggregate_sha256=sha(ROOT/'post/aggregate.json'),reviews=len(expected()),human_review=False,preservation=preserve()))
    print(json.dumps(dict(complete=True,scores={a:v['cause']for a,v in load(ROOT/'post/aggregate.json')['scores'].items()})),flush=True)
if __name__=='__main__':pipeline()

