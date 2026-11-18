"""All20 actual terminal reviews; same protected common facts, unchanged labels."""
from concurrent.futures import ThreadPoolExecutor
from collections import Counter
import json
from common import ROOT,PROJECT,ARMS,WORKERS,load,sha,digest,write_new
import staged_run as s
import evaluate_lsp48 as e
import grading48 as g
from evidence_delivery import common_packet
from post.aggregate_lsp import make_row,summary

LABELS={'INITIAL':'전체 diff·기존 AST/LSP 후 초기 리뷰1회','R0':'공통 정비+기존 정적 탐색','R1':'위험 연산·신규 후보 발굴 추가','R2':'질문별 경로 선택·값 전달 조회 추가'}
def prepare():
    path=ROOT/'private/staged_judge_manifest.json'
    if path.exists():
        m=load(path);assert all(sha(p)==h for p,h in m['references'].items());return m
    assert all((ROOT/'results/reviews'/rid/'review.result.json').exists()for rid in s.expected())
    assert not list((ROOT/'evaluation').glob('**/initial.result.json'))
    old=e.verify_reused();truth=e.truth();refs={}
    for case in e.cases():
        repeats=[u['repeat']for u in s.units()if u['case_id']==case['id']];reviews=[]
        for n in repeats:
            for arm in ARMS:
                p=ROOT/'results/reviews'/f'{case["id"]}_{arm}_r{n}'/'review.result.json';r=load(p);e.verify_actual_final_selection(r);refs[str(p)]=sha(p);reviews.append(r)
        packet=common_packet(case,truth[case['id']],load(old['fixed_source_packets'][case['id']]['path']),reviews,expected_repeats=repeats)
        p=ROOT/'private/common_judge_packets'/f'{case["id"]}.json';write_new(p,packet);refs[str(p)]=sha(p)
    groups={}
    for rid in s.expected():
        r=load(ROOT/'results/reviews'/rid/'review.result.json');raw,schema=e.semantic_input(r);key=digest(dict(case_id=r['case_id'],raw=raw,schema=schema))
        groups.setdefault(key,dict(key=key,case_id=r['case_id'],representative=rid,review_ids=[],semantic_input_sha256=digest(raw),schema_sha256=digest(schema)))['review_ids'].append(rid)
    manifest=dict(sealed_before_judges=True,references=refs,groups=list(groups.values()),planned_reviews=20,AI_nonblind=True,human_review=False,no_retry_or_semantic_repair=True)
    write_new(path,manifest);return manifest

def render():
    truth=e.truth();metadata={c['id']:c for c in e.cases()};subset=load(ROOT/'private/source_supported_subset.json')['supported_primary_target_ids'];rows=[];reviews={}
    for rid in s.expected():
        r=load(ROOT/'results/reviews'/rid/'review.result.json');reviews[rid]=r;gp=ROOT/'grades/final'/f'{rid}.json';fp=ROOT/'grades/final_failures'/f'{rid}.json';assert gp.exists()or fp.exists()
        rows.append(make_row(truth[r['case_id']],r['arm'],r['repeat'],r,load(gp)if gp.exists()else None,load(fp)['annotation_status']if fp.exists()else'complete',subset[r['case_id']],metadata[r['case_id']]))
    by={a:summary([x for x in rows if x['arm']==a])for a in ARMS};mechanism={};retention={}
    for arm in s.BRANCHES:
        rs=[v for v in reviews.values()if v['arm']==arm];q=Counter();new_source=0
        for v in rs:
            for report in v['controller_reports']:q.update(x['question']['query']for x in report['records']if not x['question']['query'].startswith('_'))
        checks=[]
        for u in s.units():
            key=f'{u["case_id"]}_{{arm}}_r{u["repeat"]}';i=next(x for x in rows if x['case_id']==u['case_id']and x['repeat']==u['repeat']and x['arm']=='INITIAL');a=next(x for x in rows if x['case_id']==u['case_id']and x['repeat']==u['repeat']and x['arm']==arm)
            targets={v['matched_target_id']for v in i['finding_labels']if v['primary_cause_tp']};got={v['matched_target_id']for v in a['finding_labels']if v['primary_cause_tp']};checks.append(dict(unit=f'{u["case_id"]}_r{u["repeat"]}',initial_correct_target_ids=sorted(targets),retained=sorted(targets&got),lost=sorted(targets-got),new_correct_targets=sorted(got-targets)))
        retention[arm]=checks
        mechanism[arm]=dict(new_claim_records=sum(len(v['new_claim_ids'])for v in rs),automatic_query_operations=dict(q),logical_calls=sum(v['logical_model_calls']for v in rs),logical_model_tokens=sum(v['known_model_tokens']for v in rs),native_calls=sum(v['context_cost']['native_calls']for v in rs),go_ast_invocations=sum(v['context_cost'].get('go_ast_invocations',0)for v in rs),native_seconds=sum(v['context_cost']['native_seconds']for v in rs),file_reads=sum(v['context_cost']['file_reads']for v in rs),source_tokens=sum(v['source_tokens']for v in rs),normal_finals=sum(v['final_new_generated']for v in rs),runner_exceptions=sum(bool(v.get('exception'))for v in rs),budget_failures=[dict(review_id=v['review_id'],failures=v['failures'])for v in rs if v['failures']])
    delivery={cid:dict(complete=load(ROOT/'private/common_judge_packets'/f'{cid}.json')['required_citation_delivery_complete'],mandatory_tokens=load(ROOT/'private/common_judge_packets'/f'{cid}.json')['mandatory_source_display_reference_tokens'])for cid in truth}
    aggregate=dict(complete=True,development_only=True,selected_units=5,unique_PRs=4,reviews=20,by_arm=by,mechanism=mechanism,initial_target_retention=retention,judge_required_delivery=delivery,human_review=False,freeze_sha256=sha(ROOT/'freeze.json'))
    write_new(ROOT/'post/staged_aggregate.json',aggregate)
    lines=['# 전체 변경점·신규 후보·질문별 경로 선택 비교','', '같은4개 PR의5문맥·원래 반복 번호를 사용했다. 전체 diff를 사용한 실제 initial5개를 입력·schema·전달 원문 동일성 검증 후 재사용하고 조건 사이에 공유했다. 도구 여유분 이중 차감을 고친 버전이며 수정 전 비교 출력은 섞지 않았다. 이전 동결 결과는 보존하며 이 표의 새 공통 정비 조건과 소급 혼합하지 않는다. 개발 사례·AI 비맹검 판독이며 인간 검토0이다.','', '| 실제 기능 | 원인 F1 | 유효 보고 F1 | 원인 재현율 | 정탐 | 보고 | 확인 오탐 | 의미 미해결 / 미판독 |','|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    pct=lambda n:'미상'if n is None else f'{100*n:.2f}%'
    for arm in ARMS:
        v=by[arm];lines.append(f'| {LABELS[arm]} | {pct(v["cause"]["f1"])} | {pct(v["valid_report"]["f1"])} | {pct(v["cause"]["recall"])} | {v["cause"]["tp"]} | {v["raw_reports"]} | {v["actual_confirmed_false_findings"]} | {v["unresolved_findings"]} / {v["ungraded_raw_findings"]} |')
    lines+=['','| 기능 | 신규 claim 기록 | 자동 조회 종류 | 논리 모델 토큰 / 호출 | native / 그중 Go AST | 파일 읽기 |','|---|---:|---|---:|---:|---:|']
    for arm in s.BRANCHES:
        v=mechanism[arm];lines.append(f'| {LABELS[arm]} | {v["new_claim_records"]} | {json.dumps(v["automatic_query_operations"],ensure_ascii=False)} | {v["logical_model_tokens"]} / {v["logical_calls"]} | {v["native_calls"]} / {v["go_ast_invocations"]} | {v["file_reads"]} |')
    lines+=['','| 기능 | 동일 초기 정탐 유지 | 초기 정탐 소실 | 초기 대비 추가 primary 정탐 | 정상 최종 |','|---|---:|---:|---:|---:|']
    for arm in s.BRANCHES:
        cs=retention[arm];lines.append(f'| {LABELS[arm]} | {sum(len(c["retained"])for c in cs)} | {sum(len(c["lost"])for c in cs)} | {sum(len(c["new_correct_targets"])for c in cs)} | {mechanism[arm]["normal_finals"]}/5 |')
    lines+=['','모든 새 조건에 전체 diff, 동일 초기 source, 단일 collector/LSP 세션의 단계 간 재사용, 같은3호출·200,000 provider토큰·source12,000·native48회/180초·file256·active900초 상한을 적용했다. 첫 수집은 공통 부분 상한native34/file212와 소스 여유를 적용한다. R1의 seed는 공개 diff의 lexical 후보이며 AST/실행/결함 정답이 아니다. R2의 점수는 질문별 missing fact와 비용을 쓰는 연구용 변형이며 원 QAFD 알고리즘 재현이 아니다. 동적 dispatch·분기 실행·전체 프로그램 dataflow·runtime probe·SCIP를 확인했다고 주장하지 않는다.','', '최종 reviewer schema·원인 범위 분리 정책·primary gold·의미 판독 기준은 조건 사이에 같다. judge는 실제 인용된 원문 단위를 optional source보다 먼저 보존하고 모든 조건에 같은 공통 packet을 준다. 새 evaluator 추가소스 상한100k는 reviewer의200k 토큰 상한과 별도이며 과거24k 판독과 구분한다. 필수 증거가 넘치면 조용히 빼지 않고 evaluation 실패로 남긴다. 채점/모델 실패는 유리하게 재시도하지 않으며 무보고/삭제를 교정 성공으로 세지 않는다.','', '신규 claim 수는 새로운 참 결함 수가 아니다. 중복·오탐·정답표 밖 실제 결함을 별도 판독하고, 동일 Go PR 반복을 고유 결함 여러 개로 세지 않는다. 원인 F1은2×primary 정탐/(실제 보고+정답 노출33회)로 기존 정의를 유지한다. 초기 정탐 보존은 이번 새 initial의 같은 primary 타깃 기준이며 과거 label 변동과 혼합하지 않는다.','',f'동결 SHA: {sha(ROOT/"freeze.json")}','']
    text='\n'.join(lines);write_new(ROOT/'RESULTS_KO.md',text);write_new(PROJECT/'paper/REVIEW_DISCOVERY_PATHS_V2_20261104_KO.md',text)
    return aggregate

def run():
    m=prepare()
    with ThreadPoolExecutor(max_workers=WORKERS)as p:list(p.map(e.safe_review_group,m['groups']))
    return render()
if __name__=='__main__':print(json.dumps({a:v['cause']for a,v in run()['by_arm'].items()}))
