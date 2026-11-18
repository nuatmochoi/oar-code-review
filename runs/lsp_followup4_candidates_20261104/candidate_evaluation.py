import json,copy,time
from concurrent.futures import ThreadPoolExecutor
from common import ROOT,PROJECT,WORKERS,REPEATS,load,sha,digest,write_new
from location_ids import verify_result
from openai_transport import total_usage
from candidate_run import cases,expected,ARMS,LABELS
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

def summary():
    import evaluate_lsp48 as e
    from post.aggregate_lsp import make_row,summary as summarize,paired_bootstrap
    import post.aggregate_lsp as ag
    truth=e.truth();subset=load(ROOT/'private/source_supported_subset.json')['supported_primary_target_ids'];rows=[]
    for c in cases():
        for a in ARMS:
            for n in range(1,REPEATS+1):
                rid=f'{c["id"]}_{a}_r{n}';r=load(ROOT/'results/reviews'/rid/'review.result.json');gp=ROOT/'grades/final'/f'{rid}.json';fp=ROOT/'grades/final_failures'/f'{rid}.json';assert gp.exists()or fp.exists()
                rows.append(make_row(truth[c['id']],a,n,r,load(gp)if gp.exists()else None,load(fp)['annotation_status']if fp.exists()else'complete',subset[c['id']],c))
    scores={a:summarize([r for r in rows if r['arm']==a])for a in ARMS};ag.CONTRASTS=(('LEDGER','PLAIN'),('PLAIN','INITIAL'),('LEDGER','INITIAL'));boot=paired_bootstrap(rows)
    transitions=[]
    for a,b in [('LEDGER','PLAIN'),('LEDGER','INITIAL'),('PLAIN','INITIAL')]:
        for c in cases():
            for n in range(1,REPEATS+1):
                get=lambda arm:next(r for r in rows if r['case_id']==c['id']and r['repeat']==n and r['arm']==arm)
                ts=lambda r:{v['matched_target_id']for v in r['finding_labels']if v['primary_cause_tp']}
                before,after=ts(get(b)),ts(get(a));transitions.append(dict(case_id=c['id'],repeat=n,before=b,after=a,retained=sorted(before&after),lost=sorted(before-after),added=sorted(after-before)))
    usage=[dict(path=str(p),physical_calls=load(p)['physical_attempt_count'],tokens=total_usage(load(p)))for base in('results','evaluation')for p in(ROOT/base).glob('**/initial.result.json')]
    cost={};decision_audits=[]
    for a in ARMS:
        rs=[load(ROOT/'results/reviews'/r['review_id']/'review.result.json')for r in rows if r['arm']==a]
        cost[a]=dict(logical_model_calls=sum(r['logical_model_calls']for r in rs),known_model_tokens=sum(r['known_model_tokens']or 0 for r in rs),unknown_usage=sum(r['known_model_tokens']is None for r in rs),source_tokens_mean=sum(r['source_tokens']for r in rs)/len(rs),native_calls=sum(r['context_cost']['native_calls']for r in rs),file_reads=sum(r['context_cost']['file_reads']for r in rs))
    for p in(ROOT/'results/reviews').glob('*/candidate_decisions.audit.json'):decision_audits.append(dict(review_id=p.parent.name,**load(p)))
    retention=load(ROOT/'post/retention.json');value=dict(scores=scores,costs=cost,paired_bootstrap=boot,transitions=transitions,decision_audits=decision_audits,retention=retention['scores'],retention_failures=len(retention['failures']),usage=usage,human_review=False,development_only=True,freeze_sha256=sha(ROOT/'freeze.json'))
    write_new(ROOT/'post/rows.json',rows);write_new(ROOT/'post/aggregate.json',value)
    pct=lambda v:'미상'if v is None else f'{100*v:.2f}%'
    lines=['# 추가 실험4: 전체 후보별 판단·좁은 확인 결함 보존','', '같은12개 개발PR×3반복. 실제 초기3호출·조회·마지막 전달본문은 고정하고 양 조건의 최종리뷰1호출씩 새로 실행했다. 초기리뷰는 진단용 실제응답을 재사용한다. 모든 의미판독은 AI 비맹검·인간검토0이다.','', '| 실제 기능 | 원인F1 | 유효보고F1 | 재현율 | TP / 보고 | 확인오탐 | 미해결 / 미판독 |','|---|---:|---:|---:|---:|---:|---:|']
    for a,v in scores.items():lines.append(f'| {LABELS[a]} | {pct(v["cause"]["f1"])} | {pct(v["valid_report"]["f1"])} | {pct(v["cause"]["recall"])} | {v["cause"]["tp"]} / {v["raw_reports"]} | {v["actual_confirmed_false_findings"]} | {v["unresolved_findings"]} / {v["ungraded_raw_findings"]} |')
    lines+=['','| 실제 기능 | 논리 모델 호출 | 토큰(공유prefix 포함) | native / file | 평균source토큰 |','|---|---:|---:|---:|---:|']
    for a,v in cost.items():lines.append(f'| {LABELS[a]} | {v["logical_model_calls"]} | {v["known_model_tokens"]:,} | {v["native_calls"]} / {v["file_reads"]} | {v["source_tokens_mean"]:.1f} |')
    lines+=['','| 최종 판단 | 초기 참 보고 | 유지한 참 보고 | 소실·미해결 | 초기 정답표밖 참 보고 | 유지한 정답표밖 참 보고 |','|---|---:|---:|---:|---:|---:|']
    for a,v in retention['scores'].items():lines.append(f'| {LABELS[a]} | {v["initial_confirmed_true"]} | {v["retained_confirmed_true"]} | {v["lost_or_unresolved"]} | {v["initial_off_gold_true"]} | {v["retained_off_gold_true"]} |')
    lines+=['',f'후보별 보조원장 수락 {sum(v["accepted"]for v in decision_audits)}/{len(decision_audits)}. 원장 거절로 실제finding을 삭제하지 않았다. 별도 보존판독 실패 {len(retention["failures"])}문맥이며 이 표의 보존 분모는 정상 판독 부분집합이다. 동일 대상 primary 정탐 유지/소실/추가는 aggregate.json에서 전체분모로 별도 제공한다.','',f'이 실험 디렉터리 실제 API {sum(v["physical_calls"]for v in usage)}호출·확인토큰 {sum(v["tokens"]or 0 for v in usage):,}. source/native 추가0이며 승계비용과 신규호출을 중복합산하지 않는다. 기존동결 결과는 수정하지 않았다.','']
    report='\n'.join(lines);write_new(ROOT/'RESULTS_KO.md',report);write_new(PROJECT/'paper/LSP_FOLLOWUP_4_CANDIDATE_RETENTION_20261104_KO.md',report);return value

def pipeline():
    from candidate_run import verify,run
    verify()
    if not(ROOT/'audit/reviewers_complete.json').exists():run()
    run_judges()
    if not(ROOT/'post/retention.json').exists():
        from retention_evaluation import run as retain
        retain()
    if not(ROOT/'post/aggregate.json').exists():summary()
    write_new(ROOT/'completion.json',dict(complete=True,stage=4,freeze_sha256=sha(ROOT/'freeze.json'),aggregate_sha256=sha(ROOT/'post/aggregate.json')))
    print(json.dumps(dict(stage_complete=4,scores={a:v['cause']for a,v in load(ROOT/'post/aggregate.json')['scores'].items()})),flush=True)
if __name__=='__main__':pipeline()

