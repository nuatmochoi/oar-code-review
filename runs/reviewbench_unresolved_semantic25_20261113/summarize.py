"""Compare fixed questions and 25-PR official outcomes after the paired run."""

from collections import Counter
import json

from adaptive import source_index
from budget_proxy import estimated_cost
from experiment import ARM_PORTS, ROOT, load
from fallback import AVAILABLE
from freeze_run import verify
from merge import BASE, packets
from relationships import block_rows
from report import scorer_helpers
from run import CONTROL


LABELS = {'MULTI_ID':'기존 다중 ID','SEMANTIC':'다중 ID + 미해결 질문 의미 검색',
          'CONNECTED':'의미 검색 + 호출·구현·실패 분기 연결 순위'}


def directory(arm):
    return CONTROL/'arms/ADD' if arm=='MULTI_ID' else ROOT/'arms'/arm


def overlaps(span, entry):
    rows = block_rows(entry,span['snapshot'])
    return span['path']==entry['path'] and any(span['start']<=n<=span['end'] and row.strip()
                                              for n,row in rows.items())


def questions_and_reports(arm, packet_list, results):
    counts, per_pr, followups = Counter(), [], {}
    for packet in packet_list:
        key = packet['key']
        root = directory(arm)
        folder = root/'review'/f'{packet["index"]:02d}'
        historical = directory('MULTI_ID')/'review'/folder.name
        trace = load(folder/'questions.json')
        old = load(historical/'questions.json')
        complete = load(folder/'complete.json')
        assert trace['question_round_1']==old['question_round_1']
        direct = trace['question_round_1']+trace['question_round_2']
        original = old['question_round_1']+old['question_round_2']
        assert [{k:v for k,v in q.items() if not k.startswith('review_')} for q in direct]==[
            {k:v for k,v in q.items() if not k.startswith('review_')} for q in original]
        follow = trace.get('fallback',{}).get('receipts',[])
        if arm!='MULTI_ID':
            assert {q['parent_id'] for q in follow}=={q['id'] for q in trace['question_round_1']
                                                   if q['review_status']=='needs_evidence'}
        answered = {q['id'] for q in direct if q['review_status']=='answered'}
        recovered = {q['parent_id']:q for q in follow if q['review_status']=='answered'}
        recovered_links = {q['id']:q for q in recovered.values()}
        delivered = {q['id'] for q in direct if all(r['result'] in AVAILABLE for r in q['target_results'])}
        stats = Counter(questions=len(direct), original_evidence_complete=len(delivered),
                        evidence_with_fallback=len(delivered|recovered.keys()),
                        solved=len(answered|recovered.keys()), solved_direct=len(answered),
                        solved_after_complete_direct=len(answered&delivered),
                        eligible_fallback=sum(q['review_status']=='needs_evidence' for q in trace['question_round_1']),
                        searched=sum(bool(q['selected']) for q in follow), solved_fallback=len(recovered),
                        reranked=sum(q['selected']!=q['semantic_choice'] for q in follow),
                        route_selected=sum(bool(q['static_route']) for q in follow),
                        failure_branch_selected=sum(bool(q['static_route']) and any(
                            set(q['selected'])&set(b['ids']) for b in q['static_route']['failure_branches'])
                            for q in follow),
                        source_tokens=trace.get('fallback',{}).get('tokens',0),
                        reports_added=len(complete['added']), candidate_eligible=len(trace['candidate_ledger']),
                        verify_reused=(folder/'verify_reused.json').exists(),
                        merge_reused=(folder/'merge_reused.json').exists(),
                        truncated=(folder/'verify/recovered_prefix.json').exists())
        base = load(BASE/f'{key}.json')['findings']
        reports = load(root/'findings'/f'{key}.json')['findings']
        scored = results[key]['candidate_findings']
        assert reports[:len(base)]==base and len(reports)==len(scored)
        assert all(all(a[k]==b[k] for k in ('file','start_line','end_line','message')) for a,b in zip(reports,scored))
        assert len(scored[len(base):])==len(complete['added'])
        indexed = {c['id']:c for c in trace['candidate_ledger']}
        _, entries = source_index(packet)
        for cid, result in zip(complete['added'],scored[len(base):],strict=True):
            links = set(trace['candidate_question_links'][cid])
            linked = [q for name,q in recovered_links.items() if name in links or q['parent_id'] in links]
            relevant = [q for q in linked if any(overlaps(span,entries[identifier])
                                               for identifier in q['selected'] for span in indexed[cid]['evidence'])]
            stats['reported_with_resolved_fallback_and_overlap'] += bool(relevant)
            stats['TP_with_resolved_fallback_and_overlap'] += bool(relevant) and result['status'] in ('matched_tp','novel_tp')
        counts.update(stats)
        per_pr.append(dict(index=packet['index'],key=key,**stats))
        followups[key] = {q['parent_id']:(q['selected'],q['review_status']) for q in follow}
    return dict(counts=dict(counts),PRs=per_pr),followups


def cost(arm, frozen):
    if arm=='MULTI_ID':
        previous = load(CONTROL/'summary.json')['costs']['known_usd']
        return dict(attributed=previous,fresh=0,historical=previous)
    folder = directory(arm)
    spent = Counter()
    for path in folder.glob('api/*/admission.json'):
        receipt = load(path.with_name('receipt.json'))
        spent['fresh'] += (estimated_cost(receipt['usage'])['usd_list_rate_estimate']
                           if receipt.get('usage') else 0)
        spent['upper'] += receipt.get('settled_upper',receipt['reserved_usd'])
        spent[receipt['phase']+'_calls'] += 1
        if receipt.get('reservation_underestimated'):
            spent['underestimated'] += 1
    shared = frozen['shared_cost'][arm]
    assert shared+spent['upper']<=frozen['new_api_cap_usd']
    replay = [load(p) for p in folder.glob('audit/replays/*.json')]
    return dict(**spent,historical=shared,attributed=shared+spent['fresh'],
                judge_replays=len(replay),judge_replay_reference_usd=sum(r['historical_usd'] for r in replay))


def paired_lookup_audit(followups, packet_list, found):
    stats = Counter()
    changed_keys = set()
    for packet in packet_list:
        key = packet['key']
        before, after = (followups[arm][key] for arm in ('SEMANTIC','CONNECTED'))
        assert before.keys()==after.keys()
        for qid, (selected,status) in before.items():
            after_selected,after_status = after[qid]
            if selected!=after_selected:
                stats['changed_queries'] += 1
                changed_keys.add(key)
                stats['semantic_solved_on_changed_queries'] += status=='answered'
                stats['connected_solved_on_changed_queries'] += after_status=='answered'
        path = f'review/{packet["index"]:02d}/verify/request.json'
        left,right = (directory(arm)/path for arm in ('SEMANTIC','CONNECTED'))
        if left.exists() and right.exists() and load(left)==load(right):
            stats['identical_fresh_inputs'] += 1
        candidate_path = f'findings/{key}.json'
        left,right = (directory(arm)/candidate_path for arm in ('SEMANTIC','CONNECTED'))
        if load(left)==load(right):
            stats['identical_final_reports'] += 1
            semantic,connected = (set(i for k,i in found[arm] if k==key) for arm in ('SEMANTIC','CONNECTED'))
            stats['judge_gold_disagreements_on_identical_reports'] += semantic!=connected
    stats['changed_prs'] = len(changed_keys)
    stats['semantic_gold_in_changed_prs'] = sum(k in changed_keys for k,_ in found['SEMANTIC'])
    stats['connected_gold_in_changed_prs'] = sum(k in changed_keys for k,_ in found['CONNECTED'])
    return dict(stats)


def render(data):
    s,a,c = data['scores'],data['audit'],data['costs']
    base = s['MULTI_ID']['grounded_F1']
    lines = ['# ReviewBench 25개 PR 미해결 질문 재조회 비교','',
             f'기존 다중 ID의 Grounded F1은 **{base:.2%}**, 의미 검색 추가는 '
             f'**{s["SEMANTIC"]["grounded_F1"]:.2%}**, 코드 연결을 순위에 반영한 조건은 '
             f'**{s["CONNECTED"]["grounded_F1"]:.2%}**였다.','',
             '| 조건 | 원래 ID 완결률¹ | 재조회 해결 포함 근거 충족률¹ | 질문 해결률¹ | 미해결 질문 재조회 / 해결 | 기존 LSP 정탐 보존 | LSP 대비 추가 정탐 | 기존 다중 ID 대비 gold 증감² | Grounded F1 | Augmented F1 | 오탐 | API 비용³ |',
             '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for arm,label in LABELS.items():
        q, scores, price = a[arm]['counts'],s[arm],c[arm]
        n, full, solved = q['questions'],q['original_evidence_complete'],q['solved']
        lsp = data['pairs'][f'{arm}-LSP']
        paired = data['pairs'].get(f'{arm}-MULTI_ID')
        gain = f'+{paired["added"]} / −{paired["lost"]}' if paired else '기준'
        fallback = f'{q["searched"]}/{q["eligible_fallback"]} · {q["solved_fallback"]}' if arm!='MULTI_ID' else '—'
        evidence = q['evidence_with_fallback']
        lines.append(f'| {label} | {full/n:.2%} ({full}/{n}) | {evidence/n:.2%} ({evidence}/{n}) | {solved/n:.2%} ({solved}/{n}) | '
                     f'{fallback} | {lsp["retained"]}/45 | {lsp["added"]} | {gain} | {scores["grounded_F1"]:.2%} | '
                     f'{scores["augmented_F1"]:.2%} | {scores["false_findings"]} | ${price["attributed"]:.2f} |')
    lines += ['', '¹ 완결률은 같은 457개 질문의 원래 ID를 모두 전달한 비율이다. 근거 충족률은 원래 ID가 완결됐거나 재조회 원문으로 미해결 질문을 풀었다고 모델이 판정한 질문의 비율이다. 해결률은 1·2차 모델 판정과 1차 질문의 재조회 후 모델 판정을 합쳐 같은 분모에서 중복 없이 계산했다. 사람 검토는 없었다.',
              '', '² 새로 찾은 gold ID와 놓친 gold ID를 따로 표시했다. 세 조건 모두 기존 LSP 보고 40개의 원문을 보존했다. 공통 보고를 포함해 각 조건을 공식 모델로 채점했으므로 gold ID 일치에는 채점 변동이 포함된다.',
              '', '³ 표는 공통 재사용 단계의 귀속액과 신규 사용량의 요율 추정액을 합산했다. 기존 공통 LSP 리뷰 $12.79는 별도다. 동일 채점 요청 재사용은 추가 청구가 없다. 두 새 조건은 각 $17.71 상한을 적용했다.', '']
    for arm in ('SEMANTIC','CONNECTED'):
        q, price = a[arm]['counts'],c[arm]
        p = data['pairs'][f'{arm}-MULTI_ID']
        lo,hi = [100*x for x in p['F1_delta_95_pr_bootstrap']['grounded_F1']]
        delta = (s[arm]['grounded_F1']-base)*100
        lines += [f'{LABELS[arm]}의 다중 ID 대비 Grounded F1 차이는 {delta:+.2f}%p였다. PR 쌍 재표집 95% 구간은 {lo:+.2f}~{hi:+.2f}%p다. '
                  f'재조회가 해결된 질문에 연결되며 그 원문과 인용 행이 겹치는 추가 보고는 {q["reported_with_resolved_fallback_and_overlap"]}개였고 '
                  f'그중 공식 정탐은 {q["TP_with_resolved_fallback_and_overlap"]}개였다. '
                  f'신규 API 지출은 ${price["fresh"]:.2f}이며, 공통 재사용 단계 귀속액은 ${price["historical"]:.2f}다. '
                  f'출력 한도에 도달한 검증 응답은 {q["truncated"]}개였으며, 이 경우 완성된 항목만 사용했다.','']
    graph = data['graph']
    compared = data['pairs']['CONNECTED-SEMANTIC']
    lo,hi = [100*x for x in compared['F1_delta_95_pr_bootstrap']['grounded_F1']]
    delta = (s['CONNECTED']['grounded_F1']-s['SEMANTIC']['grounded_F1'])*100
    lines += [f'동일 원문 안에서 확인된 호출부→구현 후보는 {graph["routes"]}개였고, 그중 실패 분기 구문까지 있는 경로는 {graph["full_routes"]}개였다. '
              f'두 순위 조건에서 선택 원문이 실제로 달라진 질문은 {graph["changed_queries"]}개, PR은 {graph["changed_prs"]}개였다. '
              f'이 질문 중 해결된 수는 의미 검색 {graph["semantic_solved_on_changed_queries"]}개, 연결 순위 {graph["connected_solved_on_changed_queries"]}개였다. '
              f'연결 조건이 확인된 정적 경로를 순위에 사용해 선택한 질문은 {a["CONNECTED"]["counts"]["route_selected"]}개였다. '
              f'선택한 추가 원문에 해당 실패 분기까지 들어간 질문은 {a["CONNECTED"]["counts"]["failure_branch_selected"]}개였다. 정적 연결은 실제 실행 경로를 보장하지 않는다.','',
              f'연결 순위의 의미 검색 대비 Grounded F1 차이는 {delta:+.2f}%p였다. PR 쌍 재표집 95% 구간은 {lo:+.2f}~{hi:+.2f}%p다. '
              f'새로 검증한 PR 중 {graph.get("identical_fresh_inputs",0)}개는 두 조건에서 최종 검증 입력이 같았으며 모델을 각각 실행했다. '
              f'최종 보고가 동일한 PR은 {graph.get("identical_final_reports",0)}개이고, 그중 gold ID가 다르게 채점된 PR은 {graph.get("judge_gold_disagreements_on_identical_reports",0)}개였다. '
              '재표집 구간에는 모델 재실행 변동이 포함되지 않는다.','',
              f'출력 잘림이 있었던 {data["uncut"]["omitted_prs"]}개 PR을 제외한 비교에서도 Grounded F1은 다중 ID {data["uncut"]["MULTI_ID"]:.2%}, '
              f'의미 검색 {data["uncut"]["SEMANTIC"]:.2%}, 연결 순위 {data["uncut"]["CONNECTED"]:.2%}였다. 첫 채점 시도는 정답 디렉터리 경로 오류로 모델 호출 전에 끝났다. 경로를 복구한 뒤 동일한 리뷰 결과로 25개 모두 공식 채점했다.','',
              f'[동결 명세]({ROOT}/PROTOCOL_KO.md) · [PR별 집계]({ROOT}/summary.json) · [새 조건 원시 기록]({ROOT}/arms) · [다중 ID 기존 결과]({CONTROL}/RESULTS_KO.md)','']
    text = '\n'.join(lines)
    (ROOT/'RESULTS_KO.md').write_text(text)
    (ROOT.parents[1]/'paper/REVIEWBENCH_UNRESOLVED_SEMANTIC25_20261113_KO.md').write_text(text)


def main():
    frozen = verify()
    packet_list = packets()
    expected = {p['key'] for p in packet_list}
    helper = scorer_helpers()
    helper.score_path = lambda arm: BASE.parent/'scoring' if arm=='LSP' else directory(arm)/'scoring'
    scores, found, per, audit, followups, costs = {}, {}, {}, {}, {}, {}
    config = load(helper.score_path('MULTI_ID')/'results.json')['eval_config']
    for arm in ('LSP',*LABELS):
        scores[arm],found[arm],per[arm] = helper.scores(arm)
        assert set(per[arm])==expected
        if arm!='LSP':
            root = helper.score_path(arm)
            assert load(root/'results.json')['eval_config']==config
            detail = {r['pr_key']:r for r in load(root/'results.details.json')}
            audit[arm],followups[arm] = questions_and_reports(arm,packet_list,detail)
            costs[arm] = cost(arm,frozen)
    for arm in LABELS:
        assert audit[arm]['counts']['questions']==457 and audit[arm]['counts']['original_evidence_complete']==310
    pairs = {f'{arm}-{prior}':helper.delta(f'{arm}-{prior}',arm,prior,found,per) for arm,prior in
             [*( (arm,'LSP') for arm in LABELS),('SEMANTIC','MULTI_ID'),('CONNECTED','MULTI_ID'),('CONNECTED','SEMANTIC')]}
    rows = load(ROOT/'preflight.json')['prs']
    graph = {field:sum(r[field] for r in rows) for field in ('routes','full_routes')}
    graph.update(paired_lookup_audit(followups,packet_list,found))
    clipped_indices = {int(p.parents[1].name) for arm in ARM_PORTS
                       for p in directory(arm).glob('review/*/verify/recovered_prefix.json')}
    uncut_keys = {p['key'] for p in packet_list if p['index'] not in clipped_indices}
    uncut = {arm:helper.metrics(helper.counts(per[arm][key] for key in uncut_keys))['grounded_F1']
             for arm in LABELS}
    uncut['omitted_prs'] = len(clipped_indices)
    data = dict(scores=scores,pairs=pairs,audit=audit,costs=costs,graph=graph,uncut=uncut,verified_hashes=len(frozen['hashes']))
    (ROOT/'summary.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
    render(data)
    print((ROOT/'RESULTS_KO.md').read_text())


if __name__=='__main__':
    main()
