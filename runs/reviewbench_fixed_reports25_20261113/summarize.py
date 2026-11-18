"""Verify report preservation and attribute changes in the official scores."""

from collections import Counter
import json

from additions import CONTROL, FIXED, report_identity
from budget_proxy import estimated_cost
from experiment import ROOT, load
from freeze_run import verify
from merge import packets
from report import scorer_helpers


LABELS = {'BASE':'기존 다중 ID', 'SEMANTIC':'기존 보고 고정 + 의미 검색',
          'CONNECTED':'기존 보고 고정 + 호출·구현·실패 분기 연결 순위'}


def directory(arm):
    return FIXED if arm=='BASE' else ROOT/'arms'/arm


def gold_hits(reports, labels):
    return {index for report in reports for index in report['covered_golden_indices']
            if labels[index]['tp_fp']=='tp'}


def audit(arm, packet_list, all_details, original_details):
    counts, per_pr = Counter(), []
    gold_dir = ROOT/'arms/SEMANTIC/upstream/golden'
    for packet in packet_list:
        key = packet['key']
        old = load(FIXED/'findings'/f'{key}.json')
        new = load(directory(arm)/'findings'/f'{key}.json')
        n = len(old['findings'])
        assert new['pr']==old['pr'] and new['findings'][:n]==old['findings']
        reports = all_details[key]['candidate_findings']
        assert len(reports)==len(new['findings'])<=6
        assert [report_identity(r) for r in reports]==[report_identity(r) for r in new['findings']]
        labels = load(gold_dir/f'{key}.json')['findings']
        previous = gold_hits(original_details[key]['candidate_findings'],labels)
        fixed, added = gold_hits(reports[:n],labels),gold_hits(reports[n:],labels)
        lost = previous-(fixed|added)
        direct = added-previous
        all_new = (fixed|added)-previous
        row = dict(preserved_reports=n,added_reports=len(reports)-n,
                   added_matched_tp=sum(r['status']=='matched_tp' for r in reports[n:]),
                   added_novel_tp=sum(r['status']=='novel_tp' for r in reports[n:]),
                   added_fp=sum(r['status'] in ('matched_fp','novel_fp') for r in reports[n:]),
                   preserved_gold=len(previous& (fixed|added)),added_gold=len(all_new),
                   lost_gold=len(lost),direct_gold=len(direct),
                   existing_only_new_gold=len(all_new-direct),unchanged_pr_drift=int(len(reports)==n and bool(lost|all_new)))
        if arm!='BASE':
            folder = directory(arm)/'review'/f'{packet["index"]:02d}'
            log = load(folder/'complete.json')
            assert log['added']==new['findings'][n:]
            row.update(proposal_calls=int(bool(log['usage'])),eligible=log['eligible'],
                       clipped=int((folder/'verify/recovered_prefix.json').exists()),
                       omitted_by_cap=int(log['skip']=='report_cap'), withheld=len(log['withheld']))
        counts.update(row)
        per_pr.append(dict(key=key,index=packet['index'],**row))
    assert counts['preserved_reports']==79 and counts['preserved_gold']+counts['lost_gold']==65
    return dict(counts=dict(counts),prs=per_pr)


def cost(arm, frozen):
    if arm=='BASE':
        amount = load(CONTROL/'summary.json')['costs']['known_usd']
        return dict(attributed=amount,fresh=0)
    values = Counter()
    folder = directory(arm)
    for path in folder.glob('api/*/admission.json'):
        receipt = load(path.with_name('receipt.json'))
        usd = estimated_cost(receipt.get('usage') or {})
        values[receipt['phase']+'_usd'] += usd['usd_list_rate_estimate'] if usd else 0
        values['upper'] += receipt.get('settled_upper',receipt['reserved_usd'])
        values[receipt['phase']+'_calls'] += 1
    shared = frozen['shared_cost'][arm]
    assert shared+values['upper']<=frozen['new_api_cap_usd']
    fresh = values['review_usd']+values['judge_usd']
    return dict(**values,attributed=shared+fresh,shared=shared,fresh=fresh,
                judge_replays=len(list(folder.glob('audit/replays/*.json'))))


def render(data):
    s,a,c = data['scores'],data['audit'],data['costs']
    lines = ['# ReviewBench 25개 PR 기존 보고 고정 비교','',
             f'기존 보고 79개를 고정한 상태에서 Grounded F1은 다중 ID **{s["BASE"]["grounded_F1"]:.2%}**, '
             f'의미 검색 **{s["SEMANTIC"]["grounded_F1"]:.2%}**, 연결 순위 **{s["CONNECTED"]["grounded_F1"]:.2%}**였다.','',
             '| 조건 | 기존 보고 보존 | 기존 공식 정답 보존 | 새 보고가 찾은 추가 공식 정답¹ | 새 보고 / 그중 오탐 | Grounded F1 | Augmented F1 | 전체 오탐 | 귀속 API 비용 | 신규 API 지출 |',
             '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for arm,label in LABELS.items():
        q,score,price = a[arm]['counts'],s[arm],c[arm]
        lines.append(f'| {label} | {q["preserved_reports"]}/79 | {q["preserved_gold"]}/65 | {q["direct_gold"]} | '
                     f'{q["added_reports"]} / {q["added_fp"]} | {score["grounded_F1"]:.2%} | '
                     f'{score["augmented_F1"]:.2%} | {score["false_findings"]} | ${price["attributed"]:.2f} | ${price["fresh"]:.2f} |')
    lines += ['', '¹ 추가 공식 정답은 기존 다중 ID가 놓쳤고, 실제로 덧붙인 보고가 매칭한 gold ID다. 기존 보고의 재채점으로만 새로 매칭된 정답은 여기에서 제외했다. 공식 채점 모델이 gold 밖 정탐으로 판정한 보고는 Augmented F1에 포함된다.',
              '', '두 조건은 앞 실험에서 확보한 원문을 재생했다. PR당 추가 최대 3개와 전체 최대 6개를 적용했다. 두 조건 모두 추가 보고 자리가 있고 재조회 원문이 있는 17개 PR에서 새 후보를 생성했으며, 공식 채점은 25개 전체를 대상으로 했다. 새 보고는 재조회 원문 안에 실제 인용 범위가 있고 결합 모델이 별개 결함으로 판정한 경우에 추가했다. 사람 판독은 0회다.',
              '', '두 조건 모두 같은 $17.71 상한을 적용했다. 귀속 비용은 기존 다중 ID의 리뷰 비용 $11.57에 새 모델 호출과 공식 채점의 사용량 비용을 더한 값이다. 공통 LSP 리뷰 비용은 별도다.', '']
    for arm in ('SEMANTIC','CONNECTED'):
        q = a[arm]['counts']
        delta = 100*(s[arm]['grounded_F1']-s['BASE']['grounded_F1'])
        lines += [f'{LABELS[arm]} 조건의 기존 다중 ID 대비 Grounded F1 차이는 {delta:+.2f}%p였다. '
                  f'공식 채점에서 기존 보고의 재평가로만 추가된 gold는 {q["existing_only_new_gold"]}개, 기존에 매칭됐다가 빠진 gold는 {q["lost_gold"]}개였다. '
                  f'새로 붙인 보고 중 gold 밖 정탐은 {q["added_novel_tp"]}개였다. '
                  f'출력 상한에 도달한 후보 생성 응답은 {q["clipped"]}개였다. '
                  f'보고 전체가 동일한 PR 중 {q["unchanged_pr_drift"]}개에서 gold 매칭이 달라졌다.','']
    sensitivity = data['unchanged_pr_score_reuse']
    lines += [f'보고가 동일한 PR은 기존 채점을, 보고가 추가된 PR은 새 공식 채점을 사용해 재집계한 Grounded F1은 '
              f'의미 검색 **{sensitivity["SEMANTIC"]["grounded_F1"]:.2%}**, '
              f'연결 순위 **{sensitivity["CONNECTED"]["grounded_F1"]:.2%}**다. 이 집계는 보고가 그대로인 PR의 채점 변동을 제거한다.','']
    change = data['pairs']['CONNECTED-SEMANTIC']
    lo,hi = [100*x for x in change['F1_delta_95_pr_bootstrap']['grounded_F1']]
    grounded_tie = all(abs(x)<1e-12 for x in (lo,hi))
    lines += [('두 새 조건은 모든 PR에서 Grounded F1을 구성하는 매칭 집계가 같았다. ' if grounded_tie else
               f'연결 순위와 의미 검색 간 Grounded F1 차이의 PR 쌍 재표집 95% 구간은 {lo:+.2f}~{hi:+.2f}%p다. ')+
              '같은 개발 PR에서 조건당 한 번 평가했으며 이 구간에 모델 재실행 변동은 포함하지 않았다.','',
              f'[동결 명세]({ROOT}/PROTOCOL_KO.md) · [PR별 집계]({ROOT}/summary.json) · [공식 채점 및 원시 응답]({ROOT}/arms) · [기존 다중 ID 결과]({CONTROL}/RESULTS_KO.md)','']
    body = '\n'.join(lines)
    (ROOT/'RESULTS_KO.md').write_text(body)
    (ROOT.parents[1]/'paper/REVIEWBENCH_FIXED_REPORTS25_20261113_KO.md').write_text(body)
    print(body)


def main():
    frozen = verify()
    packet_list = packets()
    helper = scorer_helpers()
    helper.score_path = lambda arm: directory(arm)/'scoring'
    config = load(FIXED/'scoring/results.json')['eval_config']
    original = {row['pr_key']:row for row in load(FIXED/'scoring/results.details.json')}
    expected = {p['key'] for p in packet_list}
    scores, found, per, checks, costs = {}, {}, {}, {}, {}
    for arm in LABELS:
        if arm=='BASE':
            helper.ROOT = FIXED.parent.parent
        else:
            helper.ROOT = directory(arm)
        scores[arm],found[arm],per[arm] = helper.scores(arm)
        assert set(per[arm])==expected
        scoring = directory(arm)/'scoring'
        assert load(scoring/'results.json')['eval_config']==config
        detail = {row['pr_key']:row for row in load(scoring/'results.details.json')}
        checks[arm] = audit(arm,packet_list,detail,original)
        costs[arm] = cost(arm,frozen)
    pairs = {f'{a}-{b}':helper.delta(f'{a}-{b}',a,b,found,per) for a,b in
             [('SEMANTIC','BASE'),('CONNECTED','BASE'),('CONNECTED','SEMANTIC')]}
    for arm in ('SEMANTIC','CONNECTED'):
        q = checks[arm]['counts']
        assert q['proposal_calls']==17 and q['omitted_by_cap']==5
        assert q['added_gold']==pairs[f'{arm}-BASE']['added'] and q['lost_gold']==pairs[f'{arm}-BASE']['lost']
    sensitivity = {arm:helper.metrics(helper.counts((per[arm] if row['added_reports'] else per['BASE'])[row['key']]
                                                    for row in checks[arm]['prs']))
                   for arm in ('SEMANTIC','CONNECTED')}
    data = dict(scores=scores,audit=checks,costs=costs,pairs=pairs,unchanged_pr_score_reuse=sensitivity,
                verified_hashes=len(frozen['hashes']))
    (ROOT/'summary.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
    render(data)


if __name__=='__main__':
    main()
