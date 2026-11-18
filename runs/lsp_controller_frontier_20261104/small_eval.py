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

def frontier_mechanism():
    rows=[];counts=Counter()
    manifest=load(ROOT/'prepared/first_acquisition_manifest.json')['units']
    for u in s.units():
        key=f'{u["case_id"]}_r{u["repeat"]}';rid=f'{u["case_id"]}_AGENTIC_r{u["repeat"]}'
        shared=load(ROOT/'results/shared'/key/'shared.result.json');final=load(ROOT/'results/reviews'/rid/'review.result.json')
        delivery_path=ROOT/'results/reviews'/rid/'final.delivered_evidence.json'
        delivery=load(delivery_path)if delivery_path.exists()else{}
        prior=load(manifest[key]['path']);priorids={e['id']for e in prior['context']['evidence']}
        reports=[r for r in(shared.get('controller_first_report'),delivery.get('controller_frontier_report'))if r]
        new=[dict(id=e['id'],snapshot=e['snapshot'],path=e['path'],start_line=e['start_line'],end_line=e['end_line'],unit_kind=e.get('mechanical_checks',{}).get('unit_kind'))for e in delivery.get('evidence',[])if e['id']not in priorids and e.get('kind')in('implementation_body','source_lines')]
        local=Counter()
        for report in reports:
            for record in report['records']:local['structural_steps'if record['question']['query'].startswith('_')else'backend_queries']+=1
            local.update({'static_'+relation:n for relation,n in Counter(e['relation']for e in report['static_edges']).items()})
            for e in report['errors']:local['controller_errors']+=1
        methods=Counter()
        for base in(ROOT/'results/shared'/key,ROOT/'results/reviews'/rid):
            for path in base.glob('**/native/**/rpc-*.json'):methods[load(path).get('method','unknown')]+=1
        local['actual_LSP_textDocument_RPCs']=sum(n for method,n in methods.items()if method.startswith('textDocument/'))
        local['new_delivered_source_units']=len(new)
        local['new_delivered_declarations']=sum(str(e['unit_kind']).endswith('_declaration')for e in new)
        counts.update(local)
        rows.append(dict(unit=key,counts=dict(local),actual_LSP_RPC_methods=dict(methods),new_delivered_sources=new,phases=[{k:r[k]for k in('phase','termination_reason','remaining_candidates','source_tokens_after','source_cap','native_cap','incremental_cost','errors')}for r in reports],native_environment_recoveries=delivery.get('context',{}).get('native_environment_recoveries',[])))
    return dict(counts=dict(counts),rows=rows,static_candidates_not_runtime_or_causal_proof=True)

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
        logical[arm]=dict(tokens=sum(r.get('known_model_tokens',r.get('actual_model_tokens',0))or 0 for r in rs),calls=sum(r['logical_model_calls']for r in rs),normal_final=sum(r.get('final_new_generated',False)for r in rs),runner_exceptions=sum(bool(r.get('exception'))for r in rs),native_calls=sum(r['context_cost'].get('native_calls',0)for r in rs),native_seconds=sum(r['context_cost'].get('native_seconds',0)for r in rs),file_reads=sum(r['context_cost'].get('file_reads',0)for r in rs),delivered_source_tokens=sum(r.get('delivered_source_tokens_measured',r.get('source_tokens',0))for r in rs),active_wall_seconds=sum(r.get('active_wall_seconds',0)for r in rs))
    gates=dict(historical_initial_TPs=3,historical_TPs_retained=sum(x['by_arm']['AGENTIC']['same_primary_target_reported']is True for x in checks if x['historical_initial_label']=='target_correct'),after_confirmed_false_findings=by['AGENTIC']['actual_confirmed_false_findings'],after_unresolved_findings=by['AGENTIC']['unresolved_findings'],after_ungraded_findings=by['AGENTIC']['ungraded_raw_findings'],all_after_new_finals=logical['AGENTIC']['normal_final']==5 and logical['AGENTIC']['runner_exceptions']==0)
    gates['historical_warning_ids_with_source_confirmed_report']=sum(x['by_arm']['AGENTIC']['source_confirmed_same_claim_reports']>0 for x in checks if x['historical_initial_label']=='target_correct')
    gates['development_regression_passed']=gates['historical_TPs_retained']==3 and gates['after_confirmed_false_findings']==0 and gates['after_ungraded_findings']==0 and gates['all_after_new_finals']
    scope_execution=scope_mechanism()
    mechanism=frontier_mechanism()
    result=dict(controller_frontier_execution=mechanism,scope_execution=scope_execution,complete=True,development_only=True,selected_units=5,reviews=15,by_arm=by,logical_cost=logical,regression_gates=gates,tracked_initial_warnings=checks,freeze_sha256=sha(ROOT/'freeze.json'),human_review=False)
    write_new(ROOT/'post/small_aggregate.json',result)
    labels={'INITIAL':'AST·LSP 기존 수집 후 초기 리뷰 1회','BEFORE':'모델 질문에 따른 재탐색·원인 범위 분리','AGENTIC':'현재 주장·변경 식별자에서 자동 경로 확장 추가'}
    lines=['# LSP 탐색 시작점·자동 경로 확장: 개발 비교','', '같은4개 PR의5문맥을 비교했다. 실제 초기 응답과 첫 모델 요청 조회는 그대로 재사용하고 원본 비용을 차감했다. 새 조건은 동일한 원인·영향 분리 지침에 controller의 자동 탐색을 추가했다. 모델이 질문하지 않아도 현재 주장의 인용 코드, 변경 식별자, 호출·반환값 소비 후보, 타입·상수 선언을 따라간다. 이는 이미 결과를 본 개발 사례이며 미사용/일반화 평가가 아니다.','', '| 실제 기능 | 원인 F1 | 유효 보고 F1 | 원인 재현율 | 원인 정탐 | 보고 | 확인 오탐 | 의미 미해결 / 미판독 |','|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    pct=lambda x:'미상'if x is None else f'{100*x:.2f}%'
    for arm in ARMS:
        v=by[arm];lines.append(f'| {labels[arm]} | {pct(v["cause"]["f1"])} | {pct(v["valid_report"]["f1"])} | {pct(v["cause"]["recall"])} | {v["cause"]["tp"]} | {v["raw_reports"]} | {v["actual_confirmed_false_findings"]} | {v["unresolved_findings"]} / {v["ungraded_raw_findings"]} |')
    lines+=['',f'과거 초기 정탐3회 중 동일 benchmark 타깃 유지 {gates["historical_TPs_retained"]}/3. 같은 claim ID에 원인이 정확한 보고가 남은 경우 {gates["historical_warning_ids_with_source_confirmed_report"]}/3이며, 이는 동일 타깃 보존과 별개다. 회귀 gate 통과 {gates["development_regression_passed"]}. 무보고·보류·삭제를 교정 성공으로 세지 않는다.','','| 문맥·반복 | 과거 초기 판정 | 초기 재판독 같은 타깃 | 기존 재탐색 같은 타깃 | 자동 확장 같은 타깃 |','|---|---|---|---|---|']
    for x in checks:
        fmt=lambda a:str(x['by_arm'][a]['same_primary_target_reported'])if x['historical_initial_label']=='target_correct'else ', '.join(l for f in x['by_arm'][a]['same_claim_findings']for l in f['labels'])or'무보고'
        lines.append(f'| {x["case_id"]} r{x["repeat"]} | {x["historical_initial_label"]} {x["historical_target_id"]or""} | {fmt("INITIAL")} | {fmt("BEFORE")} | {fmt("AGENTIC")} |')
    lines+=['','## 실제 추가 실행','','현재 주장에 연결된 소스·첫 조회 응답을 우선 구조 분석하고, 변경된 AST 식별자도 독립적으로 시작점으로 만든다. LSP definition으로 타입·상수·변수의 실제 선언/초기화 구문을 읽고, 읽은 본문의 직접 호출·단순 반환 대입→소비 인자 후보를 다음 조회에 넣는다. 후보와 미해결 상태는 단계 사이에 보존한다. source ID·snapshot·범위·해시·다중 후보를 기록하며 정적 연결 후보를 실제 실행이나 원인 입증으로 간주하지 않는다.','', '| 문맥·반복 | 구조 분석 단계 | backend 조회 | 실제 LSP 문서 RPC | 새 전달 소스 단위 / 선언 | 종료 사유 |','|---|---:|---:|---:|---:|---|']
    for row in mechanism['rows']:
        c=row['counts'];why='; '.join(p['phase']+': '+p['termination_reason']for p in row['phases'])
        lines.append(f'| {row["unit"]} | {c.get("structural_steps",0)} | {c.get("backend_queries",0)} | {c.get("actual_LSP_textDocument_RPCs",0)} | {c.get("new_delivered_source_units",0)} / {c.get("new_delivered_declarations",0)} | {why} |')
    lines+=['','backend 조회에는 직접 본문 읽기와 검색도 포함하며, AST 구조 단계·LSP RPC와 구별했다. 새 소스 전체가 자동 조회의 효과라는 뜻은 아니다. 모델의 후속 질문도 새 소스를 수집하므로 세부 원시 기록으로 구분한다. source 확보→모델의 반박 반영→최종 원인 정답은 별도 단계이다.','','## 비교 범위와 비용','','같은 모델·최대200,000 provider토큰·3모델호출·소스12,000 reference토큰·native48호출/180초·파일256읽기·활성900초 상한이다. 신규 reviewer는 최대10호출이며 초기 실제1호출을 논리 비용에 포함한다. worker4, 시작 간격3초. 판독 비용은 별도다.','', '| 기능 | 논리 모델 호출 | 확인 모델 토큰 | native 호출 / 초 | 파일 읽기 | 활성 초 합계 |','|---|---:|---:|---:|---:|---:|']
    for arm in ARMS:
        v=logical[arm];lines.append(f'| {labels[arm]} | {v["calls"]} | {v["tokens"]} | {v["native_calls"]} / {v["native_seconds"]:.2f} | {v["file_reads"]} | {v["active_wall_seconds"]:.2f} |')
    lines+=['','자동 탐색은 재계획 전 native34, 최종 전46의 부분 상한으로 후속 모델 질문·종료 비용을 남긴다. 재계획 전에는 남은 소스의 절반(최대2k)을 남긴다. 기존 prefix가 이미 부분 상한을 썼으면 자동 탐색을 보류하고 pending을 다음 단계로 넘긴다. 전체 예산을 늘리지 않았다.','', '이번 변경은 자동 탐색 정책, 타입·상수 AST/LSP 결속, 해시/파일 상태를 확인하는 collector 내부 AST 메모리 재사용, 누락된 Go 모듈 pin을 사전 허용·해시 검증된 환경 receipt로 복구하는 실행 경로를 함께 포함한다. 소스 허용 root는 같지만 실제 전달 소스·native 사용량은 달라진다. 따라서 순수한 LSP 기능 하나나 탐색 정책만의 효과로 분리 해석할 수 없다. 호스트 기본 stdlib를 PR 구현으로 대체하지 않는다. 동적 dispatch·heap alias·일반 분기간 dataflow는 unresolved이며 runtime probe·SCIP는0이다.','', '15개 리뷰 terminal 후 같은 PR의 모든 조건·선정 반복에 공통 source packet을 봉인하고 새 AI 판독했다. 인간 검토0, 비맹검이며 실패·모순은 재시도/수리하지 않았다. 원인 F1=2×primary 정탐/(보고+정답 노출33회); 유효 보고는 원인·anchor·실패 연산·의미 근거·실제 범위·예산을 함께 요구한다. 정답표 밖 실제 결함은 오탐으로 합치거나 사후 primary 정답으로 편입하지 않는다. 재판독 변동은 원시 과거 판정과 함께 보존했다.','',f'동결 SHA: {sha(ROOT/"freeze.json")}.','']
    text='\n'.join(lines);write_new(ROOT/'RESULTS_KO.md',text);write_new(PROJECT/'paper/LSP_CONTROLLER_FRONTIER_20261104_KO.md',text)
    return result

def run():
    m=prepare()
    with ThreadPoolExecutor(max_workers=WORKERS)as pool:list(pool.map(e.safe_review_group,m['groups']))
    return render()

if __name__=='__main__':print(json.dumps(run()['regression_gates']))
