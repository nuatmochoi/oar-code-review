"""Verify report preservation and attribute changes in the official scores."""

from collections import Counter
import json

from additions import CONTROL, FIXED, PREVIOUS, fallback, report_identity
from breadth import expanded_source, public_entries
from budget_proxy import estimated_cost
from experiment import ROOT, load
from freeze_run import verify
from merge import packets
from report import scorer_helpers


LABELS = {'BASE':'기존 다중 ID', 'SEMANTIC':'기존 보고 고정 + 의미 검색',
          'BREADTH':'재조회 범위 확대', 'CANDIDATES':'재조회 확대 + 후보 생성 개선',
          'CAP':'이전 의미 검색 + 보고 상한 완화'}
NEW = ('BREADTH','CANDIDATES','CAP')


def directory(arm):
    if arm=='BASE':
        return FIXED
    if arm=='SEMANTIC':
        return PREVIOUS/'arms/SEMANTIC'
    return ROOT/'arms'/arm


def gold_hits(reports, labels):
    return {index for report in reports for index in report['covered_golden_indices']
            if labels[index]['tp_fp']=='tp'}


def audit(arm, packet_list, all_details, original_details):
    counts, per_pr = Counter(), []
    gold_dir = ROOT/'arms/BREADTH/upstream/golden'
    for packet in packet_list:
        key = packet['key']
        old = load(FIXED/'findings'/f'{key}.json')
        new = load(directory(arm)/'findings'/f'{key}.json')
        n = len(old['findings'])
        assert new['pr']==old['pr'] and new['findings'][:n]==old['findings']
        reports = all_details[key]['candidate_findings']
        assert len(reports)==len(new['findings'])<=(9 if arm=='CAP' else 6)
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
            row.update(proposal_calls=int(bool(log['usage']) and not log.get('reused')),eligible=log['eligible'],
                       clipped=int((folder/'verify/recovered_prefix.json').exists()),
                       omitted_by_cap=int(log['skip']=='report_cap'), withheld=len(log['withheld']),
                       reused_review=int(log.get('reused',False)),raw_candidates=log['eligible']+len(log['rejected']),
                       filtered_citations=len(log['rejected']))
        row['direct_gold_indices'] = sorted(direct)
        row['lost_gold_indices'] = sorted(lost)
        counts.update({key:value for key,value in row.items() if isinstance(value,int)})
        per_pr.append(dict(key=key,index=packet['index'],**row))
    assert counts['preserved_reports']==79 and counts['preserved_gold']+counts['lost_gold']==65
    return dict(counts=dict(counts),prs=per_pr)


def cost(arm, frozen):
    if arm=='BASE':
        amount = load(CONTROL/'summary.json')['costs']['known_usd']
        return dict(attributed=amount,fresh=0)
    if arm=='SEMANTIC':
        previous = load(PREVIOUS/'summary.json')['costs']['SEMANTIC']
        return dict(attributed=previous['attributed'],fresh=0,original_fresh=previous['fresh'])
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
    reuse = load(folder/'scoring/score_reuse.json')
    return dict(**values,attributed=shared+fresh,shared=shared,fresh=fresh,PR_scores_reused=len(reuse),
                judge_replays=len(list(folder.glob('audit/replays/*.json'))))


def coverage(packet_list):
    totals = Counter()
    for packet in packet_list:
        if len(load(FIXED/'findings'/f'{packet["key"]}.json')['findings'])>=6:
            continue
        entries = public_entries(packet)
        for label,retrieval in (('old',fallback('SEMANTIC',packet)),('wide',expanded_source(packet))):
            totals[label+'_blocks'] += len(retrieval['targets'])
            totals[label+'_file_pr_pairs'] += len({entries[i]['path'] for i in retrieval['targets']})
        for arm in ('BREADTH','CANDIDATES'):
            folder = directory(arm)/'review'/f'{packet["index"]:02d}'/'verify'
            response = folder/'parsed.json'
            candidates = load(response) if response.exists() else load(folder/'recovered_prefix.json')['parsed']
            totals[arm+'_empty'] += not candidates['candidates']
    return dict(totals)


def render(data):
    s,a,c = data['scores'],data['audit'],data['costs']
    lines = ['# ReviewBench 25개 PR 재조회 범위·후보 생성·보고 상한 비교','',
             '| 조건 | 기존 보고 보존 | 기존 공식 정답 보존 | 새 보고가 찾은 추가 공식 정답¹ | 새 보고 / 그중 오탐 | Grounded F1 | Augmented F1 | 전체 오탐 | 귀속 API 비용 | 신규 API 지출 |',
             '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for arm,label in LABELS.items():
        q,score,price = a[arm]['counts'],s[arm],c[arm]
        lines.append(f'| {label} | {q["preserved_reports"]}/79 | {q["preserved_gold"]}/65 | {q["direct_gold"]} | '
                     f'{q["added_reports"]} / {q["added_fp"]} | {score["grounded_F1"]:.2%} | '
                     f'{score["augmented_F1"]:.2%} | {score["false_findings"]} | ${price["attributed"]:.2f} | ${price["fresh"]:.2f} |')
    lines += ['', '¹ 추가 공식 정답은 기존 다중 ID가 놓쳤고, 실제로 덧붙인 보고가 매칭한 gold ID다. 공식 정답 목록 밖의 모델 정탐은 Augmented F1에 포함된다.',
              '', '새 조건마다 같은 $17.71 비용 상한을 적용했다. 귀속 비용은 기존 다중 ID 리뷰 $11.57, 재사용한 상한 완화 대조군의 추가 리뷰비, 해당 조건의 신규 API 사용량을 합산했다. 동일 PR 제출의 공식 채점 재사용은 신규 청구가 없다. 기존 두 행은 과거 실험 비용이다. 공통 LSP 리뷰 비용은 별도다.',
              '', '범위 확대와 후보 생성 개선은 동일한 20개 PR에 동일한 확대 원문을 제공했다. 후보 생성 개선은 변경 전후와 구체 실패 경로를 대조하는 지시를 더했다. 보고 상한 완화는 이전 의미 검색의 상한 도달 PR 5개에서만 새 후보를 생성하고 나머지 20개 결과를 재사용했다. 공식 채점 모델은 보고가 달라진 PR을 평가했다. 사람 판독은 0회다.', '']
    for arm,reference in (('BREADTH','SEMANTIC'),('CANDIDATES','BREADTH'),('CAP','SEMANTIC')):
        q = a[arm]['counts']
        delta = 100*(s[arm]['grounded_F1']-s[reference]['grounded_F1'])
        direct = data['direct_changes'][arm]
        lines += [f'{LABELS[arm]}의 {LABELS[reference]} 대비 Grounded F1 차이는 {delta:+.2f}%p다. '
                  f'새 보고가 직접 매칭한 공식 정답 ID는 {direct["added"]}개 늘고 {direct["lost"]}개 빠졌다. '
                  f'목록 밖 정탐은 {q["added_novel_tp"]}개, 인용 검사 탈락은 {q["filtered_citations"]}개, '
                  f'출력 상한에 도달한 응답은 {q["clipped"]}개였다. '
                  f'기존 공식 채점은 {c[arm]["PR_scores_reused"]}개 PR에서 재사용했다.','']
    observed = data['coverage']
    lines += [f'같은 20개 PR에서 제공한 재조회 원문 조각은 {observed["old_blocks"]}개에서 {observed["wide_blocks"]}개로 늘었다. '
              f'PR별 파일 수 합계는 {observed["old_file_pr_pairs"]}개에서 {observed["wide_file_pr_pairs"]}개로 늘었다. '
              f'확대 원문에서 후보를 내지 않은 PR은 범위 확대 {observed["BREADTH_empty"]}개, 후보 생성 개선 {observed["CANDIDATES_empty"]}개였다.','',
              '보고 79개는 모든 조건에서 보존됐다. 채점에서 기존 정답 매칭이 빠지거나 생긴 사례는 PR별 집계에 기록했다. 같은 개발 PR을 조건당 한 번 평가했다. '
              '실행 중 집계 스크립트까지 동결 검사에 걸려 일부 요청이 모델에 전달되기 전에 중단됐다. 동결 파일의 원본 해시를 복구하고 실패 로그와 기존 응답을 보존해 남은 단계만 완료했다.','',
              f'[동결 명세]({ROOT}/PROTOCOL_KO.md) · [PR별 집계]({ROOT}/summary.json) · [공식 채점 및 원시 응답]({ROOT}/arms) · [기존 다중 ID 결과]({CONTROL}/RESULTS_KO.md)','']
    body = '\n'.join(lines)
    (ROOT/'RESULTS_KO.md').write_text(body)
    (ROOT.parents[1]/'paper/REVIEWBENCH_BREADTH_CANDIDATES25_20261113_KO.md').write_text(body)
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
    comparisons = [('BREADTH','SEMANTIC'),('CANDIDATES','BREADTH'),('CAP','SEMANTIC')]
    pairs = {f'{a}-{b}':helper.delta(f'{a}-{b}',a,b,found,per) for a,b in comparisons}
    for arm in NEW:
        q = checks[arm]['counts']
        assert q['proposal_calls']==(5 if arm=='CAP' else 20)
        assert q['omitted_by_cap']==(0 if arm=='CAP' else 5)
        assert q['reused_review']==(20 if arm=='CAP' else 0)
    identities = {arm:{(row['key'],i) for row in check['prs'] for i in row['direct_gold_indices']}
                  for arm,check in checks.items()}
    direct_changes = {a:dict(added=len(identities[a]-identities[b]),lost=len(identities[b]-identities[a]))
                      for a,b in comparisons}
    data = dict(scores=scores,audit=checks,costs=costs,pairs=pairs,direct_changes=direct_changes,
                coverage=coverage(packet_list),
                verified_hashes=len(frozen['hashes']))
    (ROOT/'summary.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
    render(data)


if __name__=='__main__':
    main()
