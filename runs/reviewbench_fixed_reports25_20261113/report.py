"""Compare the two complete official scorecards and audit question coverage."""
from collections import Counter
import importlib.util
import json
import re
import socket

from adaptive import source_index
from budget_proxy import estimated_cost
from experiment import ROOT, PORT, load, write, _diff_rows
from freeze_run import verify
from merge import PRIOR, BASE, merge_input, packets

PREVIOUS = ROOT.parent/'reviewbench_general_add25_20261113'
LABELS = {'A':'기존 LSP 리뷰', 'FIXED':'기존 리뷰 + 고정 두 관점', 'ADAPT':'기존 리뷰 + 질문 갱신'}


def score_path(arm):
    if arm == 'A':
        return BASE.parent/'scoring'
    return (ROOT if arm=='ADAPT' else PRIOR)/'arms/ADD/scoring'


def scorer_helpers():
    spec = importlib.util.spec_from_file_location('prior_summarize',PRIOR/'summarize.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.score_path = score_path
    return module


def usage_cost(usage):
    return estimated_cost(usage)['usd_list_rate_estimate']


def assess_added(arm, packet):
    name = f'{packet["key"]}.json'
    source = score_path(arm).parent
    base = load(BASE/name)['findings']
    review = load(source/'findings'/name)['findings']
    assert review[:len(base)]==base
    detail = next(x for x in load(source/'scoring/results.details.json') if x['pr_key']==packet['key'])
    scored = detail['candidate_findings']
    assert len(scored)==len(review)
    for raw, result in zip(review,scored):
        assert all(raw[k]==result[k] for k in ('file','start_line','end_line','message'))
    return scored[len(base):]


def overlaps_new_read(candidate, entry):
    for span in candidate['evidence']:
        if span['path']!=entry['path']:
            continue
        rows = _diff_rows(entry['text'],span['snapshot'],span['path'])
        if any(span['start']<=row<=span['end'] and value.strip() for row,value in rows.items()):
            return True
    return False


def coverage_audit(found, packet_list):
    counts = Counter()
    perspectives, statuses, lookup_results = Counter(), Counter(), Counter()
    linked, per_pr = set(), []
    added_status = {a:Counter() for a in ('FIXED','ADAPT')}
    cited_by_added = Counter()
    for packet in packet_list:
        i,key = packet['index'],packet['key']
        folder = ROOT/'arms/ADD/review'/f'{i:02d}'
        trace = load(folder/'questions.json')
        merge = load(folder/'complete.json')
        gold = load(ROOT/'upstream/golden'/f'{key}.json')['findings']
        _, entries = source_index(packet)
        receipts = trace['question_round_1']+trace['question_round_2']
        actual = [q for q in receipts if q['result'] in ('delivered','shared_read')]
        perspectives.update(q['perspective'] for q in actual)
        statuses.update(q['review_status'] for q in receipts)
        lookup_results.update(q['result'] for q in receipts)
        counts['unrecognized_lookup_target_with_id'] += sum(
            q['result']=='unknown_target' and bool(re.search(r'\b[HB][0-9]{3}\b',q['target']))
            for q in receipts)
        fresh = set(trace['newly_read_hunks'])
        reads = set(trace['lookup1_hunks'])|set(trace['lookup2_hunks'])
        seen = reads|set(trace['sampled_hunks'])
        counts.update(dict(supplied_hunks=packet['coverage']['supplied_hunks'],total_hunks=packet['coverage']['total_hunks'],
                           sampled_hunks=len(trace['sampled_hunks']),lookup_hunks=len(reads),
                           source_hunks_before_final=len(seen),new_second_round_hunks=len(fresh),
                           PRs_with_new_second_round_hunks=int(bool(fresh)),
                           repeated_followups_skipped=trace['repeated_followups_skipped'],
                           questions=len(receipts),actual_question_targets=len(actual),
                           baseline_reports_preserved=merge['baseline_reports_preserved'],
                           round_model_calls=len(trace['usage']),review_input_output_tokens=trace['actual_tokens']))
        ledger = {c['id']:c for c in trace['candidate_ledger']}
        fixed_merge = load(PRIOR/'arms/ADD/review'/f'{i:02d}'/'complete.json')
        _, _, fixed_eligible = merge_input(packet,'DIVERGENT')
        fixed_ledger = {c['id']:c for c in fixed_eligible}
        for arm, candidates, selection in [('ADAPT',ledger,merge),('FIXED',fixed_ledger,fixed_merge)]:
            cited = {identifier for identifier,e in entries.items() if e['kind']=='hunk'
                     and any(overlaps_new_read(candidates[cid],e) for cid in selection['added'])}
            cited_by_added[arm] += len(cited)
        second = {q['id']:q for q in trace['question_round_2']}
        results = assess_added('ADAPT',packet)
        assert len(merge['added'])==len(results)
        pr_linked = set()
        for candidate_id, score in zip(merge['added'],results):
            related = [second[qid]['target'] for qid in trace['candidate_question_links'][candidate_id]
                       if qid in second and second[qid]['target'] in fresh]
            if score['status']=='matched_tp' and any(overlaps_new_read(ledger[candidate_id],entries[j]) for j in related):
                pr_linked.update((key,n) for n in score['covered_golden_indices'] if gold[n]['tp_fp']=='tp')
        linked |= pr_linked
        per_pr.append(dict(index=i,lookup_hunks=len(reads),new_second_round_hunks=len(fresh),
                           questions=len(receipts),supported_new_region_gold=len(pr_linked),
                           supported_new_region_gold_vs_fixed=len(pr_linked-found['FIXED'])))
        for arm in added_status:
            added_status[arm].update(x['status'] for x in assess_added(arm,packet))
    return dict(**dict(counts),question_perspectives=dict(perspectives),question_statuses=dict(statuses),
                lookup_outcomes=dict(lookup_results),added_report_status={a:dict(c) for a,c in added_status.items()},
                hunks_cited_by_added_reports=dict(cited_by_added),
                fresh_hunk_linked_gold=len(linked),fresh_hunk_linked_gold_vs_fixed=len(linked-found['FIXED']),
                fresh_hunk_linked_gold_vs_baseline=len(linked-found['A']),per_PR=per_pr)


def costs(frozen):
    by_phase = {k:Counter() for k in ('judge','review')}
    for admission in (ROOT/'api').glob('*/admission.json'):
        receipt = load(admission.with_name('receipt.json'))
        phase = by_phase[receipt['phase']]
        phase['calls'] += 1
        phase['known_usd'] += usage_cost(receipt['usage']) if receipt.get('usage') else 0
        phase['upper_usd'] += receipt.get('settled_upper',receipt['reserved_usd'])
        phase[receipt['status']] += 1
    upper = sum(p['upper_usd'] for p in by_phase.values())
    assert upper<=frozen['new_api_cap_usd']
    return dict(by_phase={k:dict(v) for k,v in by_phase.items()},upper_usd=upper,
                known_usd=sum(p['known_usd'] for p in by_phase.values()),
                cumulative_upper_usd=frozen['prior_api_usd']+upper)


def render(result):
    def pct(value): return f'{100*value:.2f}%'
    def money(lo,hi): return f'${lo:.2f}' if round(lo,2)==round(hi,2) else f'${lo:.2f}–${hi:.2f}'
    rows = result['scores']
    audit = result['coverage']
    pairs = {p['comparison']:p for p in result['pairs']}
    d = pairs['ADAPT-FIXED']
    cost = result['costs']
    fixed = result['fixed_cost']
    delta = (rows['ADAPT']['grounded_F1']-rows['FIXED']['grounded_F1'])*100
    lo,hi = [x*100 for x in d['F1_delta_95_pr_bootstrap']['grounded_F1']]
    lines = ['# ReviewBench 25개 PR 질문 갱신과 고정 두 관점 비교','',
             f'질문을 갱신한 뒤 기존 리뷰와 결합한 Grounded F1은 **{pct(rows["ADAPT"]["grounded_F1"])}**, 고정 두 관점 결합은 **{pct(rows["FIXED"]["grounded_F1"])}**였다. 질문 갱신의 차이는 **{delta:+.2f}%p**다.','',
             '| 조건 | 후보 탐색에 제공한 diff hunk¹ | 질문 조회 hunk / 2차에서 처음 읽은 hunk | 추가 보고 인용 hunk | 기존 정탐 보존 / 새 탐지 | 정답 탐지 / 352 | Grounded F1 | Augmented F1 | 오탐 | 비용² |',
             '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for arm,label in LABELS.items():
        s=rows[arm]
        if arm=='A':
            coverage='전체 diff'; query='—'; retention='45/45 · 0'; usd='공통 $12.79'
        else:
            coverage=f'{audit["supplied_hunks"]:,}/{audit["total_hunks"]:,}'
            p=pairs[f'{arm}-A']; retention=f'{p["retained"]}/45 · {p["added"]}'
            if arm=='FIXED':
                query='질문별 미계측'
                usd=money(fixed['cost_excluding_common_baseline_lower_usd'],fixed['cost_excluding_common_baseline_upper_usd'])
            else:
                query=f'{audit["lookup_hunks"]} / {audit["new_second_round_hunks"]}'
                usd=money(cost['known_usd'],cost['upper_usd'])
        cited = audit['hunks_cited_by_added_reports'][arm] if arm!='A' else '—'
        lines.append(f'| {label} | {coverage} | {query} | {cited} | {retention} | {s["gold"]} | {pct(s["grounded_F1"])} | {pct(s["augmented_F1"])} | {s["false_findings"]} | {usd} |')
    lines+=['',f'두 결합 방식의 공통 정답은 {d["retained"]}개다. 질문 갱신만 탐지한 정답은 {d["added"]}개, 고정 두 관점만 탐지한 정답은 {d["lost"]}개였다. '
            f'Grounded F1 차이의 PR 쌍 재표집 95% 구간은 {lo:+.2f}~{hi:+.2f}%p다. 이미 사용한 개발 PR에서 조건당 한 번 실행했으며, 재표집에는 모델 재실행 변동이 들어가지 않는다.','',
            f'질문 갱신에서 {audit["questions"]}개 질문을 요청했고 {audit["actual_question_targets"]}개는 원문을 받았다. '
            f'대상 ID를 연결하지 못한 요청은 {audit["lookup_outcomes"].get("unknown_target",0)}개였고, 이 중 {audit["unrecognized_lookup_target_with_id"]}개에는 ID 형태의 문자열이 들어 있었다. '
            f'2차 조회는 {audit["PRs_with_new_second_round_hunks"]}/25개 PR에서 앞서 조회 입력에 제공하지 않은 hunk를 읽었다. '
            f'2차 질문 ID를 명시하고 그 hunk의 실제 원문을 인용한 추가 정탐 보고가 덮은 gold는 {audit["fresh_hunk_linked_gold"]}개다. '
            f'그중 고정 두 관점에서 미탐한 gold는 {audit["fresh_hunk_linked_gold_vs_fixed"]}개였다. '
            '질문·인용·공식 정답의 연결을 확인한 수치이며 2차 조회만의 인과 효과를 추정하지 않는다.','',
            '¹ 원문 제공량은 모델이 실제로 충분히 검토한 범위와 구분한다. 두 방식은 동일한 공개 원문과 기존 보고 40개, 동일한 결합 판정과 상한을 사용했다. '
            '고정 두 관점에는 질문별 답 기록이 없다. 두 방식에 공통으로 비교할 수 있는 인용 hunk는 추가 보고의 근거 범위가 겹친 고유 hunk를 PR별로 센 합계다. '
            '질문 갱신은 동일한 원문 패킷 내에서 찾아 읽었고, 최종 검증에도 이 패킷을 제공했다. 큰 PR 5개에는 일부 hunk가 제공됐다.','',
            f'² 결합 비용은 후보 탐색·검증, 결합 검증, 공식 판독의 사용량 기준 비용이다. 공통 기존 리뷰 비용 $12.79는 별도다. '
            f'새 실행의 리뷰 비용은 ${cost["by_phase"]["review"]["known_usd"]:.2f}, 공식 판독은 ${cost["by_phase"]["judge"]["known_usd"]:.2f}였다. '
            f'이전 실행을 합친 예약 포함 누적 비용은 ${cost["cumulative_upper_usd"]:.2f} / $70이다.','',
            f'[실험 명세]({ROOT}/PROTOCOL_KO.md) · [집계 및 PR별 조회 범위]({ROOT}/summary.json) · [질문과 채점 원시 기록]({ROOT}/arms/ADD) · [고정 두 관점 원시 결과]({PRIOR}/arms/ADD)','']
    doc = '\n'.join(lines)
    (ROOT/'RESULTS_KO.md').write_text(doc)
    (ROOT.parents[1]/'paper/REVIEWBENCH_ADAPTIVE_VS_FIXED_ADD25_20261113_KO.md').write_text(doc)


def main():
    frozen = verify()
    assert load(ROOT/'status.json')['phase']=='complete'
    assert len(list((ROOT/'arms/ADD/review').glob('*/complete.json')))==25
    with socket.socket() as sock:
        sock.settimeout(.5)
        assert sock.connect_ex(('127.0.0.1',PORT))!=0
    helper = scorer_helpers()
    scores,found,per = {},{},{}
    packet_list = packets()
    for arm in LABELS:
        scores[arm],found[arm],per[arm]=helper.scores(arm)
        assert set(per[arm])=={p['key'] for p in packet_list}
    assert load(score_path('ADAPT')/'results.json')['eval_config']==load(score_path('FIXED')/'results.json')['eval_config']
    audit = coverage_audit(found,packet_list)
    assert audit['baseline_reports_preserved']==40 and audit['round_model_calls']==75
    pairs = [helper.delta(f'{a}-{b}',a,b,found,per) for a,b in [('FIXED','A'),('ADAPT','A'),('ADAPT','FIXED')]]
    result = dict(PRs=25,gold=352,scores=scores,coverage=audit,pairs=pairs,costs=costs(frozen),
                  fixed_cost=load(PREVIOUS/'summary.json')['rows']['DIVERGENT'],hashes_verified=len(frozen['hashes']))
    write(ROOT/'summary.json',result)
    render(result)
    print(json.dumps({k:v for k,v in result.items() if k!='fixed_cost'},ensure_ascii=False))


if __name__=='__main__':
    main()
