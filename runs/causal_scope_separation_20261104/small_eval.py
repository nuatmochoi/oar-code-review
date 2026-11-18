"""Fresh common-source AI judging of all 15 cells, never selective retries."""
from concurrent.futures import ThreadPoolExecutor
import json
from common import ROOT,PROJECT,ARMS,WORKERS,load,sha,digest,write_new
import small_run as s
import evaluate_lsp48 as e
import grading48 as g
import judging48 as j
from post.aggregate_lsp import make_row,summary
from collections import Counter

def scope_mechanism():
    counts=Counter();records=[]
    for u in s.units():
        cid=u['case_id'];n=u['repeat'];shared=load(ROOT/'results/shared'/f'{cid}_r{n}'/'shared.result.json')
        rid=f'{cid}_AGENTIC_r{n}';r=load(ROOT/'results/reviews'/rid/'review.result.json')
        gp=ROOT/'grades/final'/f'{rid}.json';labels=load(gp)['finding_labels']if gp.exists()else[]
        final=r.get('payload')or{};initial={c['id']:c['claim']for c in(shared.get('initial')or{}).get('claims',[])}
        counts['normal_replans']+=shared.get('replan')is not None;counts['normal_finals']+=bool(r.get('final_new_generated'))
        for stage,payload in(('replan',shared.get('replan')or{}),('final',final)):
            for scope in payload.get('claim_scopes',[]):
                counts[stage+'_scopes_recorded']+=1;counts[stage+'_scope_'+scope['scope_change']]+=1
                counts[stage+'_unknown_extensions']+=sum(e['status']=='unresolved'for e in scope['impact_extensions'])
                if stage=='final':
                    indices=[i for i,f in enumerate(final.get('findings',[]))if f['claim_id']==scope['claim_id']]
                    correct=[x for x in labels if x['finding_index']in indices and x.get('cause_correct')and x['label']in('target_correct','other_confirmed_defect')]
                    counts['narrowed_scopes_reported']+=scope['scope_change']=='narrowed'and bool(indices)
                    counts['narrowed_scopes_reported_and_AI_cause_correct']+=scope['scope_change']=='narrowed'and bool(correct)
                    records.append(dict(review_id=rid,initial_claim=initial.get(scope['claim_id']),scope=scope,finding_indices=indices,AI_correct_report_indices=[x['finding_index']for x in correct]))
        ap=ROOT/'results/reviews'/rid/'final.auxiliary_update.json'
        if ap.exists():
            audit=load(ap).get('claim_scope_audit')or{};counts['final_scope_audit_accepted']+=bool(audit.get('accepted'))
            for issue in audit.get('errors',[]):counts['scope_audit_'+issue['code']]+=1
    return dict(counts=dict(counts),records=records,semantic_truth_guaranteed=False,source_confirmed_is_AI_not_human=True)

def prepare():
    dest=ROOT/'private/small_judge_manifest.json'
    if dest.exists():
        m=load(dest);assert all(sha(p)==h for p,h in m['references'].items());return m
    assert all((ROOT/'results/reviews'/rid/'review.result.json').exists()for rid in s.expected())
    assert not list((ROOT/'evaluation').glob('**/initial.result.json'))
    old=e.verify_reused();truth=e.truth();cases={c['id']:c for c in e.cases()};refs={}
    for cid in sorted(cases):
        repeats=[u['repeat']for u in s.units()if u['case_id']==cid];rs=[]
        for n in repeats:
            for arm in ARMS:
                p=ROOT/'results/reviews'/f'{cid}_{arm}_r{n}'/'review.result.json';r=load(p);e.verify_actual_final_selection(r);refs[str(p)]=sha(p);rs.append(r)
        packet=j.build_common_packet(cases[cid],truth[cid],load(old['fixed_source_packets'][cid]['path']),rs,expected_repeats=repeats)
        p=ROOT/'private/common_judge_packets'/f'{cid}.json';g.put_new(p,packet);refs[str(p)]=sha(p)
    groups={}
    for rid in s.expected():
        r=load(ROOT/'results/reviews'/rid/'review.result.json');raw,schema=e.semantic_input(r);key=digest({'case_id':r['case_id'],'raw':raw,'schema':schema})
        groups.setdefault(key,dict(key=key,case_id=r['case_id'],representative=rid,review_ids=[],semantic_input_sha256=digest(raw),schema_sha256=digest(schema)))['review_ids'].append(rid)
    manifest=dict(sealed_before_judges=True,planned_reviews=15,references=refs,groups=list(groups.values()),human_review=False,evaluation='Unchanged source-span schema/semantic matching; shared actual evidence pool; model failure/contradiction preserved with no repair or judge retry.')
    write_new(dest,manifest);return manifest

def render():
    truth=e.truth();cases={c['id']:c for c in e.cases()};subset=load(ROOT/'private/source_supported_subset.json')['supported_primary_target_ids'];rows=[]
    for rid in s.expected():
        r=load(ROOT/'results/reviews'/rid/'review.result.json');gp=ROOT/'grades/final'/f'{rid}.json';fp=ROOT/'grades/final_failures'/f'{rid}.json';assert gp.exists()or fp.exists()
        rows.append(make_row(truth[r['case_id']],r['arm'],r['repeat'],r,load(gp)if gp.exists()else None,load(fp)['annotation_status']if fp.exists()else'complete',subset[r['case_id']],cases[r['case_id']]))
    by={a:summary([r for r in rows if r['arm']==a])for a in ARMS};checks=[];old=s.OLD
    for u in s.units():
        cid=u['case_id'];n=u['repeat'];base=load(old/'grades/final'/f'{cid}_INITIAL_r{n}.json');initial=load(old/'results/reviews'/f'{cid}_INITIAL_r{n}'/'review.result.json')
        for b in base['finding_labels']:
            if not b['primary_cause_tp']and b['label']not in('false','target_wrong_cause'):continue
            item=dict(case_id=cid,repeat=n,initial_finding_index=b['finding_index'],claim_id=initial['payload']['findings'][b['finding_index']]['claim_id'],historical_initial_label=b['label'],historical_target_id=b.get('matched_target_id'),by_arm={})
            for arm in ARMS:
                row=next(r for r in rows if(r['case_id'],r['repeat'],r['arm'])==(cid,n,arm));labs=row['finding_labels']
                item['by_arm'][arm]=dict(source_confirmed_same_claim_reports=sum(bool(x.get('cause_correct'))and x['label']in('target_correct','other_confirmed_defect')and row['raw_findings'][x['finding_index']].get('claim_id')==item['claim_id']for x in labs),same_primary_target_reported=any(l['primary_cause_tp']and l['matched_target_id']==b.get('matched_target_id')for l in labs)if b['primary_cause_tp']else None,same_claim_findings=[dict(finding_index=i,title=f.get('title'),labels=[x['label']for x in labs if x['finding_index']==i])for i,f in enumerate(row['raw_findings'])if f.get('claim_id')==item['claim_id']],annotation_complete=row['annotation_complete'])
            checks.append(item)
    logical={}
    for arm in ARMS:
        rs=[load(ROOT/'results/reviews'/f'{u["case_id"]}_{arm}_r{u["repeat"]}'/'review.result.json')for u in s.units()]
        logical[arm]=dict(tokens=sum(r.get('known_model_tokens',r.get('actual_model_tokens',0))or 0 for r in rs),calls=sum(r['logical_model_calls']for r in rs),normal_final=sum(r.get('final_new_generated',False)for r in rs),runner_exceptions=sum(bool(r.get('exception'))for r in rs),native_calls=sum(r['context_cost'].get('native_calls',0)for r in rs),native_seconds=sum(r['context_cost'].get('native_seconds',0)for r in rs))
    gates=dict(historical_initial_TPs=3,historical_TPs_retained=sum(x['by_arm']['AGENTIC']['same_primary_target_reported']is True for x in checks if x['historical_initial_label']=='target_correct'),after_confirmed_false_findings=by['AGENTIC']['actual_confirmed_false_findings'],after_unresolved_findings=by['AGENTIC']['unresolved_findings'],after_ungraded_findings=by['AGENTIC']['ungraded_raw_findings'],all_after_new_finals=logical['AGENTIC']['normal_final']==5 and logical['AGENTIC']['runner_exceptions']==0)
    gates['historical_warning_ids_with_source_confirmed_report']=sum(x['by_arm']['AGENTIC']['source_confirmed_same_claim_reports']>0 for x in checks if x['historical_initial_label']=='target_correct')
    gates['development_regression_passed']=gates['historical_TPs_retained']==3 and gates['after_confirmed_false_findings']==0 and gates['after_ungraded_findings']==0 and gates['all_after_new_finals']
    scope_execution=scope_mechanism()
    result=dict(scope_execution=scope_execution,complete=True,development_only=True,selected_units=5,reviews=15,by_arm=by,logical_cost=logical,regression_gates=gates,tracked_initial_warnings=checks,freeze_sha256=sha(ROOT/'freeze.json'),human_review=False)
    write_new(ROOT/'post/small_aggregate.json',result)
    labels={'INITIAL':'동일한 실제 초기 리뷰','BEFORE':'원인·영향 분리 전 재탐색','AGENTIC':'확인된 원인·미확인 영향 분리'}
    lines=['# 확인된 원인·미확인 영향 분리: 소규모 회귀 점검','', '과거 정탐3개·확인 오탐2개가 있었던5문맥을 모델 실행 전에 선정했다. 각 초기 리뷰의 모든 경고를 포함한다. 같은 실제 초기 응답·첫 조회 응답과 같은200k provider토큰·3호출 상한으로 비교한다. 변경은 원인과 강한 영향의 분리를 요구하는 재계획/최종 지침 및 보조 claim_scopes schema이며, finding 형식·LSP/AST·source pool·채점은 유지했다. 개발용으로 이미 본 결과를 사용했으므로 일반화 성능이나 전체48개 결과가 아니다.','', '| 실제 기능 | 원인 F1 | 유효 보고 F1 | 원인 재현율 | 원인 TP | 보고 | 확인 오탐 | 의미 미해결 / 미판독 |','|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for arm in ARMS:
        v=by[arm];pct=lambda x:'미상'if x is None else f'{100*x:.2f}%';lines.append(f'| {labels[arm]} | {pct(v["cause"]["f1"])} | {pct(v["valid_report"]["f1"])} | {pct(v["cause"]["recall"])} | {v["cause"]["tp"]} | {v["raw_reports"]} | {v["actual_confirmed_false_findings"]} | {v["unresolved_findings"]} / {v["ungraded_raw_findings"]} |')
    lines+=['',f'과거 초기 정탐3개 중 수정 후 같은 타깃 정탐 유지: {gates["historical_TPs_retained"]}/3. 수정 후 확인 오탐 {gates["after_confirmed_false_findings"]}개. 개발 회귀 통과 여부: {gates["development_regression_passed"]}. 무보고·삭제를 교정 성공으로 세지 않았다.','', '| 문맥·반복 | 과거 초기 판정 | 초기 재판독의 같은 타깃 | 수정 전 같은 타깃 | 수정 후 같은 타깃 |','|---|---|---|---|---|']
    for x in checks:
        fmt=lambda a:str(x['by_arm'][a]['same_primary_target_reported'])if x['historical_initial_label']=='target_correct'else ', '.join(l for f in x['by_arm'][a]['same_claim_findings']for l in f['labels'])or'무보고'
        lines.append(f'| {x["case_id"]} r{x["repeat"]} | {x["historical_initial_label"]} {x["historical_target_id"]or""} | {fmt("INITIAL")} | {fmt("BEFORE")} | {fmt("AGENTIC")} |')
    lines+=['','## 실제 원인·영향 분리 기록','','| 실제 분리 기록 | 재계획 | 최종 |\n|---|---:|---:|\n'+''.join(f'| {label} | {scope_execution["counts"].get("replan_"+key,0)} | {scope_execution["counts"].get("final_"+key,0)} |\n'for key,label in(("scopes_recorded","원인·영향 분리 기록"),("scope_narrowed","원인 범위를 실제로 좁힘"),("unknown_extensions","미확인 영향으로 유지"))),'','claim_scopes에는 원래 주장·수정한 주장·확인된 연산/결과·필수조건·증거·미확인 확장 주장과 필요성 판정이 기록된다. 감사는 ID·본문 전달·선언 일관성만 검사하며 의미적 정답을 보장하지 않는다. 좁은 원인으로 실제 보고한 수와 AI가 정확하다고 판독한 수를 분리했다.','',f'과거 정탐 경고 ID 중 원인이 정확한 보고가 남은 경우: {gates["historical_warning_ids_with_source_confirmed_report"]}/3. 이 값은 같은 claim ID의 정확한 보고 수이며 동일 benchmark 타깃/동일 원인 보존을 별도로 보장하지 않는다. 엄격한 타깃 보존은 위 표와 gate를 유지한다.','','## 구현과 실행 범위','','아래 AST·LSP·route 기능은 두 재탐색 조건에 공통이다. 이번 변경은 확인된 원인/미확인 영향을 명시적으로 분리하고 재계획·최종 보고에 반영하는 단계이다.', '- AST가 같은 함수 안의 유일한 실제 호출 토큰을 확인하고, 최대3행의 위치 오차를 보정한다. 복수 후보는 unresolved로 보존한다.','- 호출 구현 본문 요청은 LSP definition으로 실제 선언 URI/범위를 연결하고 완전본문·버전·해시를 기록한다. 검색 결과는 후보이며 다른 caller 본문을 callee 구현 확보로 승격하지 않는다.','- route 전체의 분류 오류는 그대로 남기되 고유한 현재 condition/pending challenge의 안전한 read를 차단하지 않는다. 과거·철회·중복·다른 snapshot 대상은 허용하지 않는다.','- 원시 질문·AST 좌표 보정·LSP RPC·본문·후보 변경·응답·실패는 새 디렉터리에 보존했다. 모델의 의미 판단과 기계적 연결 검사는 다르며 실제 runtime/probe는0이다.','', '## 비용과 제한','', '| 기능 | 논리 모델 호출 | 확인 모델 토큰 | native 호출 / 초 |','|---|---:|---:|---:|']
    for arm in ARMS:
        v=logical[arm];lines.append(f'| {labels[arm]} | {v["calls"]} | {v["tokens"]} | {v["native_calls"]} / {v["native_seconds"]:.2f} |')
    lines+=['','초기 응답·첫 조회·수정 전 결과는 실제 과거 사용량과 native/벽시계 비용을 논리 비용에 포함한다. 첫 조회를 새로 발행한 것처럼 세지 않는다. 새 reviewer는 최대10호출이며, judge 비용은 별도 원시 receipt에 기록한다. 사전 native 점검은 본실험 비용과 분리했다.','', '기존 primary 타깃·일대일 매칭·F1 정의는 유지했고,15개 결과를 확보한 뒤 같은 PR의 모든 조건에 동일한 source packet을 봉인하여 새로 AI 판독했다. 초기/수정 전의 과거 판정도 함께 보존하며 새 사실로 판정이 달라지면 그대로 표시한다. AI 판독·인간검토0이며 모순·실패는 재시도하거나 유리하게 교정하지 않는다. 정답표 밖 결함은 오탐으로 합치거나 사후 정답으로 편입하지 않는다.','',f'동결 SHA: {sha(ROOT/"freeze.json")}.','']
    text='\n'.join(lines);write_new(ROOT/'RESULTS_KO.md',text);write_new(PROJECT/'paper/CAUSAL_SCOPE_SEPARATION_20261104_KO.md',text)
    return result

def run():
    m=prepare()
    with ThreadPoolExecutor(max_workers=WORKERS)as pool:list(pool.map(e.safe_review_group,m['groups']))
    return render()

if __name__=='__main__':print(json.dumps(run()['regression_gates']))
