from pathlib import Path
import collections,json,time
from experiment import ROOT,load,sha
from budget_proxy import estimated_cost
from diff48 import diff_evidence
OLD=ROOT.parent/'reviewbench_changeunits_commit_query25_20261105'
LABELS={'FLAT':'공통 수리 + 개별 함수·참조 조회','PATH':'공통 수리 + 새 본문에서 연결을 이어 가는 조회','GROUPED':'FLAT과 동일 사실·관계 + 연결 순서로 본문 배치'}

def covered(details):
    out=set()
    for row in details:
        gold=load(ROOT/'upstream/golden'/f'{row["pr_key"]}.json')['findings']
        for f in row['candidate_findings']:
            for n in f['covered_golden_indices']:
                if gold[n]['tp_fp']=='tp':out.add((row['pr_key'],n))
    return out
def f1(m,k):
    p,r=m[k+'_precision'],m[k+'_recall']
    return 2*p*r/(p+r)if p is not None and r is not None and p+r else 0 if p==r==0 else None
def rows(evidence):
    out={}
    for e in evidence:
        if e['kind']not in ('source_lines','body','implementation_body','exception_branch'):continue
        for n,t in enumerate(e['content'].splitlines(),e['start_line']):out[e['snapshot'],e['path'],n]=t
    return out
def mechanism(arm):
    per=[];totals=collections.Counter();reasons=collections.Counter()
    native_arm='FLAT'if arm=='GROUPED'else arm
    for p in sorted((ROOT/'native').glob('[0-9][0-9]')):
        if not(p/native_arm/'complete.json').exists():continue
        context=load(p/native_arm/'context.json');ledger=load(p/native_arm/'query_ledger.json');packet=load(ROOT/'public'/f'{p.name}.json')
        new=set(rows(context['evidence']))-set(rows(packet['prefix']['evidence']+diff_evidence(packet['case']['diff'])))
        cited_new=set();finding_new=[]
        decoded=ROOT/'arms'/arm/'review'/p.name/'decoded.json'
        if decoded.exists():
            for index,f in enumerate(load(decoded)['findings']):
                fresh=set()
                for loc in f['supporting_evidence']:
                    if loc.get('status')!='bound':continue
                    fresh|={(loc['snapshot'],loc['path'],n)for n in range(loc['start_line'],loc['end_line']+1)}&new
                if fresh:finding_new.append(index);cited_new|=fresh
        depth=collections.Counter(q['question'].get('depth',0)for q in ledger['queries'])
        for q in ledger['queries']:
            for u in q['unresolved']:reasons[u['reason']]+=1
        record=dict(index=int(p.name),queries=len(ledger['queries']),selected=sum(bool(q['selected_evidence_ids'])for q in ledger['queries']),
                    unresolved=sum(bool(q['unresolved'])for q in ledger['queries']),deferred=len(ledger['deferred']),
                    depth_counts=dict(depth),connected_followup_queries=sum(n for d,n in depth.items()if d>1),
                    new_physical_rows=len(new),new_support_rows_cited=len(cited_new),findings_citing_new_rows=finding_new,
                    **load(p/native_arm/'complete.json'))
        per.append(record)
        for k in ['queries','selected','unresolved','deferred','connected_followup_queries','new_physical_rows','new_support_rows_cited']:
            totals[k]+=record[k]
    return dict(per_PR=per,totals=dict(totals),unresolved_reasons=dict(reasons))
def update():
    old=load(OLD/'comparison.final.json')['arms']['A'];aset=covered(load(OLD/'arms/A/scoring/results.details.json'))
    costs=[]
    for p in sorted((ROOT/'api').glob('*/admission.json')):
        a=load(p);r=load(p.parent/'receipt.json')if(p.parent/'receipt.json').exists()else a
        c=estimated_cost(r.get('usage')or{});req=load(p.parent/'request.json')
        costs.append(dict(id=a['id'],phase=a['phase'],slot=req.get('metadata',{}).get('experiment_slot'),
                          status=r['status'],usage=r.get('usage'),known_usd=c['usd_list_rate_estimate']if c else None,
                          upper=r.get('settled_upper',r['reserved_usd'])))
    total=dict(known_usd=sum(x['known_usd']or 0 for x in costs),unknown_calls=sum(x['known_usd']is None for x in costs),
               upper=sum(x['upper']for x in costs),physical_calls=len(costs))
    arms={};sets={};mechanisms={a:mechanism(a)for a in LABELS}
    for arm in LABELS:
        folder=ROOT/'arms'/arm/'scoring'
        if not(folder/'results.json').exists():continue
        score=load(folder/'results.json');assert score['pr_count']==25
        original=load(OLD/'arms/A/scoring/results.json');assert score['eval_config']['golden_hash']==original['eval_config']['golden_hash']
        details=load(folder/'results.details.json');found=covered(details);sets[arm]=found;m=score['micro']['overall']
        statuses=collections.Counter(f['status']for row in details for f in row['candidate_findings'])
        logical=0;tok=0;wall=0
        for i in range(25):
            d=ROOT/'arms'/arm/'review'/f'{i:02d}';complete=load(d/'complete.json')
            for response in (load(d/'response.json'),load(ROOT/'plans/current'/f'{i:02d}'/'response.json')):
                logical+=estimated_cost(response['usage'])['usd_list_rate_estimate']
            tok+=complete['logical_tokens'];wall+=complete['logical_active_wall_seconds']
            assert complete['token_cap_respected']
        judgeids=set(load(folder/'exit.json')['api_ids']);judgecost=sum(x['known_usd']or 0 for x in costs if x['id']in judgeids)
        ref=aset if arm=='FLAT'else sets['FLAT']
        arms[arm]=dict(label=LABELS[arm],PRs=25,covered=len(found),gold=m['golden_tp_count'],grounded_recall=m['grounded_recall'],
            grounded_F1=f1(m,'grounded'),augmented_F1=f1(m,'augmented'),reports=m['candidate_count'],
            false_findings=statuses['matched_fp']+statuses['novel_fp'],novel_tp=statuses['novel_tp'],
            A_retained=len(found&aset),A_lost=len(aset-found),A_added=len(found-aset),
            reference='A'if arm=='FLAT'else'FLAT',retained=len(found&ref),lost=len(ref-found),added=len(found-ref),
            logical_review_usd=logical,judge_physical_usd=judgecost,logical_review_plus_judge_usd=logical+judgecost,
            logical_model_tokens=tok,logical_active_wall_seconds=wall)
    preservation=load(ROOT/'audit/preservation.json')
    changed=[p for p,h in preservation['files'].items()if sha(p)!=h]
    assert not changed,changed
    result=dict(updated_at=time.time(),status='complete'if len(arms)==3 else'in_progress',historical_A=old,arms=arms,
                physical_cost=total,mechanism=mechanisms,old_artifacts_unchanged=True,human_review=0)
    (ROOT/'comparison.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    (ROOT/'audit/cost.json').write_text(json.dumps(dict(**total,records=costs),indent=2))
    def pct(x):return '—'if x is None else f'{x*100:.2f}%'
    lines=['# ReviewBench25: 개별 조회와 연결된 코드 묶음 조회','',
        '같은25개 개발 PR·공개 계획·BASE/HEAD·gpt-5.6-sol low·공식 scorer 비교다. 기존 결과 보존. 공식 Grounded F1을 과거 자체 원인F1/유효보고F1과 혼용하지 않는다. 인간검토0, 완료 판독1회다.','',
        '| 조건 | gold 탐지/352 | gold 재현율 | Grounded F1 | Augmented F1 | A 정탐 보존/소실/추가 | 오탐 | 논리 리뷰+확인 판독 비용 |',
        '|---|---:|---:|---:|---:|---|---:|---:|',
        '| 과거 A: 기존 LSP 본문+계획/최종 (참고) | 45 | 12.78% | 22.60% | 24.12% | 45/0/0 | 1 | $14.2655 |']
    for arm,label in LABELS.items():
        if arm not in arms:lines.append(f'| {arm}: {label} | 미완료 | — | — | — | — | — | — |');continue
        v=arms[arm];lines.append(f'| {arm}: {label} | {v["covered"]} | {pct(v["grounded_recall"])} | {pct(v["grounded_F1"])} | {pct(v["augmented_F1"])} | {v["A_retained"]}/{v["A_lost"]}/{v["A_added"]} | {v["false_findings"]} | ${v["logical_review_plus_judge_usd"]:.4f} |')
    if 'PATH'in arms:
        v=arms['PATH'];lines+=['',f'PATH−FLAT gold 보존/소실/추가: {v["retained"]}/{v["lost"]}/{v["added"]}. Grounded F1 차이 {(v["grounded_F1"]-arms["FLAT"]["grounded_F1"])*100:+.2f}pp.']
    lines+=['','## 실제 실행과 비용','',
        '공통 위치 수리·중립 본문 읽기·일부 assignment/return 구문 표시·AST 메모리 재사용을 모든 조건에 동일 적용했다. FLAT은 공통 본문에서 만든 고정 초기 후보를 조회하고, PATH는 새로 얻은 본문에서 다음 AST/LSP 후보를 만들어 이어 간다. GROUPED는 FLAT과 정확히 같은 본문·관계·조회 trace·location ID를 전달하고 부모 본문 다음에 연결된 조회 결과가 오도록 배치만 바꾼다. 부모 evidence/query ID와 depth가 원시 기록에 있다. 정적 연결이며 runtime dispatch/dataflow 증명이 아니다. PCST·PARoG branch join·SCIP·runtime probe·추가 모델 비평은 이번에 실행하지 않았다.','',
        '| 조건 | 조회 | 근거 선택 있음 | 미해결 기록 있음 | depth>1 후속 조회 | 새 물리 소스 행 | 미처리 후보 |',
        '|---|---:|---:|---:|---:|---:|---:|']
    for arm in LABELS:
        v=mechanisms[arm]['totals']
        lines.append('| '+arm+' | '+' | '.join(str(v.get(k,0))for k in ['queries','selected','unresolved','connected_followup_queries','new_physical_rows','deferred'])+' |')
    lines+=['','선택/미해결은 중복 가능하다. 새 행은 전체 diff와 기존 prefix 모두에 없던 실제 소스 행이며 공백을 포함한다. supporting 인용의 새 행 사용은 구조화 결과에서 별도 기록한다. 새 body나 depth 자체는 추가 정탐·실행 경로 검증 성공이 아니다.','',
        f'새 물리 API {total["physical_calls"]}회, 확인 usage 기반 비용 ${total["known_usd"]:.4f}, 미확인 {total["unknown_calls"]}회, 예약 포함 상한 ${total["upper"]:.4f}/$70. 과거 계획은 정확히 재사용하고 각 행의 논리 비용에 포함하므로 논리 행 합계와 새 물리 청구를 합산하지 않는다. 요율 추정이며 invoice 실측이 아니다.','',
        '공통 상한: 계획+최종2호출, 모델750k/PR, source+관계/조회 trace12k, native64회/240초, file256, collection900초, active wall3600초. 전체 diff와 기존 본문을 보존한다. 동일 상한이 동일 실제 사용량을 뜻하지 않는다. 커밋 문맥·검토 범위·보고 개수는 확대하지 않았다.','',
        '현재25개는 이미 사용한 개발 사례다. 연결 정책은 실제 발견 후보풀·새 본문도 바꿀 수 있으므로 동일 사실의 표현 효과로 부르지 않는다. 메커니즘 점검은 AI/기계 점검이며 새 일반화 증거가 아니다. 실패를 제외한 부분 점수를 전체25개 F1로 발행하지 않는다.','',
        f'[동결 명세]({ROOT}/PROTOCOL_KO.md) · [동결 해시]({ROOT}/freeze.json) · [구조화 결과]({ROOT}/comparison.json) · [원시 native 조회]({ROOT}/native) · [원시 리뷰·공식 판독]({ROOT}/arms) · [비용]({ROOT}/audit/cost.json)','']
    text='\n'.join(lines);(ROOT/'RESULTS_KO.md').write_text(text)
    (ROOT.parents[1]/'paper/REVIEWBENCH_CONNECTED_PATHS25_20261113_KO.md').write_text(text)
    return result
if __name__=='__main__':print(json.dumps(update()['physical_cost']))
