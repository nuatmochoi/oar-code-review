"""Audit the frozen reports and compare the new union with historical scorecards."""

from collections import Counter
import json

from budget_proxy import estimated_cost
from experiment import ROOT, load
from freeze_run import verify
from merge import packets
from report import scorer_helpers
from union_review import FIXED, PREVIOUS, report_identity


UNION = ROOT/'arms/UNION'
ARMS = {
    'SEMANTIC': ('기존 의미 검색',FIXED),
    'BREADTH': ('재조회 범위 확대',PREVIOUS/'arms/BREADTH'),
    'CANDIDATES': ('범위 확대 + 후보 생성 개선',PREVIOUS/'arms/CANDIDATES'),
    'UNION': ('기존 의미 검색 보고 고정 + 두 후보군 결합',UNION),
}


def audit(arm, gold, details, baseline_details, found, baseline_found, cases):
    folder = ARMS[arm][1]
    retained_reports, pr_rows = 0, []
    added_hits = set()
    added_status = Counter()
    for key, original in baseline_details.items():
        baseline = load(FIXED/'findings'/f'{key}.json')
        output = load(folder/'findings'/f'{key}.json')
        assert output['pr']==baseline['pr'] and len(output['findings'])<=6
        expected = baseline['findings']
        output_reports = output['findings']
        rows = details[key]['candidate_findings']
        assert [report_identity(item) for item in rows]==[report_identity(item) for item in output_reports]
        counts = Counter(map(report_identity,output_reports))
        baseline_counts = Counter(map(report_identity,expected))
        retained_reports += sum((counts&baseline_counts).values())
        if arm in ('SEMANTIC','UNION'):
            assert output_reports[:len(expected)]==expected
        added = []
        for item in rows:
            identity = report_identity(item)
            if baseline_counts[identity]:
                baseline_counts[identity] -= 1
            else:
                added.append(item)
                added_status[item['status']] += 1
                added_hits.update((key,i) for i in item['covered_golden_indices']
                                  if gold[key][i]['tp_fp']=='tp' and (key,i) not in baseline_found)
        old = {i for k,i in baseline_found if k==key}
        new = {i for k,i in found if k==key}
        pr_rows.append(dict(key=key,previous=list(sorted(old)),current=list(sorted(new)),
                            lost=sorted(old-new),gained=sorted(new-old),added_reports=added))
    if arm in ('SEMANTIC','UNION'):
        assert retained_reports==81
    return dict(retained_reports=retained_reports,retained_gold=len(found&baseline_found),
                lost_gold=sorted(baseline_found-found),added_status=dict(added_status),
                added_direct_gold=sorted(added_hits),all_added_gold=sorted(found-baseline_found),
                target_cases={label:(key,index) in found for label,(key,index) in cases.items()},PRs=pr_rows)


def union_cost(frozen):
    amounts = Counter()
    for path in UNION.glob('api/*/admission.json'):
        receipt = load(path.with_name('receipt.json'))
        estimate = estimated_cost(receipt.get('usage') or {})
        amounts[receipt['phase']] += estimate['usd_list_rate_estimate'] if estimate else 0.
        amounts['upper'] += receipt.get('settled_upper',receipt['reserved_usd'])
    shared = frozen['shared_cost']['UNION']
    assert shared+amounts['upper']<=frozen['new_api_cap_usd']
    fresh = amounts['review']+amounts['judge']
    return dict(attributed=shared+fresh,fresh=fresh,review=amounts['review'],judge=amounts['judge'],
                shared=shared,upper=shared+amounts['upper'])


def review_checks(packet_list):
    stats = Counter()
    selected = []
    for packet in packet_list:
        row = load(UNION/'review'/f'{packet["index"]:02d}'/'complete.json')
        stats['pool'] += len(row['candidate_pool'])
        if row['candidate_pool'] and not row['cap']:
            stats['report_cap'] += len(row['candidate_pool'])
        verdict = row['merge_assessment']
        if verdict:
            stats['judged'] += len(verdict['verdicts'])
            stats['judge_calls'] += 1
            stats.update(v['disposition'] for v in verdict['verdicts'])
        stats['reports_added'] += len(row['added'])
        stats['withheld'] += len(row['withheld'])
        for cid,report in zip(row['selected'],row['added'],strict=True):
            selected.append(dict(key=packet['key'],index=packet['index'],origin=row['origins'][cid],
                                 report=report))
    return dict(counts=dict(stats),selected=selected)


def render(data):
    lines = ['# ReviewBench 25개 PR 기존 의미 검색 보고와 추가 후보 결합','',
             '| 조건 | 기존 의미 검색 정탐 보존 | 추가 공식 정답¹ | fastimage BMP | wirechat 자동 스크롤 | Grounded F1 | Augmented F1 | 전체 오탐 | 귀속 API 비용 | 이번 신규 API 지출 |',
             '|---|---:|---:|:---:|:---:|---:|---:|---:|---:|---:|']
    for arm,(label,_) in ARMS.items():
        score, check, cost = (data[k][arm] for k in ('scores','audit','costs'))
        fast = '탐지' if check['target_cases']['fastimage'] else '미탐'
        wire = '탐지' if check['target_cases']['wirechat'] else '미탐'
        lines.append(f'| {label} | {check["retained_gold"]}/66 | {len(check["added_direct_gold"])} | {fast} | {wire} | '
                     f'{score["grounded_F1"]:.2%} | {score["augmented_F1"]:.2%} | {score["false_findings"]} | '
                     f'${cost["attributed"]:.2f} | ${cost["fresh"]:.2f} |')
    counts = data['review']['counts']
    check = data['audit']['UNION']
    status = check['added_status']
    lines += ['', '¹ 기존 의미 검색이 놓친 공식 정답 중 추가 보고가 직접 매칭한 건수다. 기존 보고의 재채점에서 새로 생긴 매칭은 제외한다. 사람 판독은 없었다.','',
              f'기존 의미 검색 보고 {check["retained_reports"]}개의 문구·위치·순서를 보존했다. '
              f'두 이전 조건의 후보 {counts["pool"]}개 중 {counts["judged"]}개를 다시 검증했다. '
              f'상한에 걸린 후보는 {counts.get("report_cap",0)}개였고, 검증에서 중복 {counts.get("overlap",0)}개, '
              f'근거 부족 {counts.get("insufficient_evidence",0)}개, 원문과 맞지 않는 주장 {counts.get("invalid",0)}개를 제외했다. '
              f'새 보고 {counts["reports_added"]}개의 공식 채점은 목록과 매칭한 정탐 {status.get("matched_tp",0)}개, '
              f'목록 밖 정탐 {status.get("novel_tp",0)}개, 오탐 {status.get("matched_fp",0)+status.get("novel_fp",0)}개였다.','',
              f'공식 채점은 제출이 같은 {data["score_reuse_count"]}개 PR에서 기존 결과를 재사용했다. '
              f'총 비용 상한은 ${data["cap"]:.2f}다. 결합 귀속 비용에는 고정 리뷰와 두 조건의 후보 생성비 '
              f'${data["costs"]["UNION"]["shared"]:.2f}, 이번 결합·채점 사용량이 포함된다. '
              '기존 세 조건의 귀속 비용은 원래 실험의 기록이며 공통 LSP 리뷰 비용은 별도다.','',
              f'[동결 명세]({ROOT}/PROTOCOL_KO.md) · [PR별 집계]({ROOT}/summary.json) · '
              f'[결합 판정]({UNION}/review) · [공식 채점]({UNION}/scoring/results.details.json)','']
    body = '\n'.join(lines)
    (ROOT/'RESULTS_KO.md').write_text(body)
    (ROOT.parents[1]/'paper/REVIEWBENCH_CANDIDATE_UNION25_20261114_KO.md').write_text(body)
    print(body)


def main():
    frozen = verify()
    packet_list = packets()
    helper = scorer_helpers()
    helper.score_path = lambda arm: ARMS[arm][1]/'scoring'
    keys = {packet['key'] for packet in packet_list}
    config = load(FIXED/'scoring/results.json')['eval_config']
    gold = {key:load(UNION/'upstream/golden'/f'{key}.json')['findings'] for key in keys}
    cases = {label:(next(key for key in keys if key.startswith(prefix)),index) for label,prefix,index in
             [('fastimage','sdsykes_fastimage_165-',4),('wirechat','wirechat_wirechat_183-',16)]}
    assert all(gold[k][i]['tp_fp']=='tp' for k,i in cases.values())
    scores, found, details = {}, {}, {}
    for arm,(_,folder) in ARMS.items():
        helper.ROOT = folder
        scores[arm],found[arm],per = helper.scores(arm)
        assert set(per)==keys
        assert load(folder/'scoring/results.json')['eval_config']==config
        details[arm] = {row['pr_key']:row for row in load(folder/'scoring/results.details.json')}
    assert len(found['SEMANTIC'])==66
    audits = {arm:audit(arm,gold,details[arm],details['SEMANTIC'],found[arm],found['SEMANTIC'],cases)
              for arm in ARMS}
    old_costs = load(PREVIOUS/'summary.json')['costs']
    costs = {arm:dict(attributed=old_costs[arm]['attributed'],fresh=0.) for arm in ARMS if arm!='UNION'}
    costs['UNION'] = union_cost(frozen)
    reused = load(UNION/'scoring/score_reuse.json')
    summary = dict(scores=scores,audit=audits,costs=costs,review=review_checks(packet_list),
                   cap=frozen['new_api_cap_usd'],score_reuse_count=len(reused))
    (ROOT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    render(summary)


if __name__=='__main__':
    main()
