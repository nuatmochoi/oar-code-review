"""Aggregate the new run, frozen comparison arms and a paired offline lookup replay."""
from collections import Counter
import json
import socket

from adaptive import lookup, source_index
from experiment import ROOT, PORT, load, write
from freeze_run import verify
from merge import PRIOR, BASE, merge_input, packets
from report import assess_added, costs, overlaps_new_read, scorer_helpers

BEFORE = ROOT.parent/'reviewbench_adaptive_add25_20261113'
AVAILABLE = {'delivered', 'shared_read', 'already_visible'}
LABELS = {'FIXED':'고정 두 관점', 'BEFORE':'질문 갱신 · 수정 전', 'AFTER':'질문 갱신 · 수정 후'}


def folder(run, packet):
    return run/'arms/ADD/review'/f'{packet["index"]:02d}'


def lookup_metrics(questions):
    status = Counter()
    for question in questions:
        outcomes = [target['result'] for target in question['target_results']]
        status.update(outcomes)
        status['questions'] += 1
        status['some_source'] += any(result in AVAILABLE for result in outcomes)
        status['all_source'] += all(result in AVAILABLE for result in outcomes)
        status['current_round_source'] += any(result in ('delivered','shared_read') for result in outcomes)
        status['cross_round_only'] += all(result=='already_visible' for result in outcomes)
        status['multi_target'] += len(outcomes)>1
    return status


def historical_replay(packet_list):
    """Use the original model outputs, with no new plan, candidate or score generation."""
    old, replay = Counter(), Counter()
    recovered = Counter()
    pr_rows = []
    for packet in packet_list:
        trace = load(folder(BEFORE,packet)/'questions.json')
        _, entries = source_index(packet)
        seen = set(trace['sampled_hunks'])
        for number in (1,2):
            original = trace[f'question_round_{number}']
            old['questions'] += len(original)
            old['some_source'] += sum(q['result'] in AVAILABLE for q in original)
            inputs = [dict(perspective=q['perspective'],question=q['question'],targets=[q['target']]) for q in original]
            result = lookup(inputs,entries,number,already_visible=seen if number==2 else ())
            replay.update(lookup_metrics(result['receipts']))
            for previous, new in zip(original,result['receipts']):
                if previous['result']!='unknown_target':
                    continue
                matches = [item['result'] for item in new['target_results']]
                recovered['eligible_id'] += any(x!='unknown_target' for x in matches)
                recovered['source_available'] += any(x in AVAILABLE for x in matches)
                recovered['total_previously_unknown'] += 1
            seen.update(result['targets'])
            pr_rows.append(dict(index=packet['index'],round=number,receipts=result['receipts'],
                                reference_tokens=result['reference_tokens']))
    return dict(before=dict(old),replay=dict(replay),recovered=dict(recovered),per_PR=pr_rows)


def read_audit(packet_list, found):
    lookup_counts = Counter()
    coverage = Counter()
    links = set()
    added_status = {a:Counter() for a in ('FIXED','BEFORE','AFTER')}
    cited = Counter()
    for packet in packet_list:
        key = packet['key']
        _, entries = source_index(packet)
        current = folder(ROOT,packet)
        trace = load(current/'questions.json')
        fresh = set(trace['newly_read_hunks'])
        questions = trace['question_round_1']+trace['question_round_2']
        lookup_counts.update(lookup_metrics(questions))
        coverage.update(dict(supplied_hunks=packet['coverage']['supplied_hunks'],
                             total_hunks=packet['coverage']['total_hunks'],
                             looked_up_hunks=len(set(trace['lookup1_hunks'])|set(trace['lookup2_hunks'])),
                             fresh_second_hunks=len(fresh),PRs_with_fresh_second_hunks=bool(fresh),
                             model_calls=len(trace['usage']),review_tokens=trace['actual_tokens']))
        gold = load(ROOT/'upstream/golden'/f'{key}.json')['findings']
        for arm, run in [('FIXED',PRIOR),('BEFORE',BEFORE),('AFTER',ROOT)]:
            merge = load(folder(run,packet)/'complete.json')
            ledger = (merge_input(packet,'DIVERGENT')[2] if arm=='FIXED'
                      else load(folder(run,packet)/'questions.json')['candidate_ledger'])
            by_id = {c['id']:c for c in ledger}
            selected = [by_id[cid] for cid in merge['added']]
            cited[arm] += sum(entry['kind']=='hunk' and any(overlaps_new_read(c,entry) for c in selected)
                              for entry in entries.values())
            scored = assess_added('ADAPT',packet) if arm=='AFTER' else get_additions(run,packet)
            assert len(scored)==len(selected)
            added_status[arm].update(r['status'] for r in scored)
            if arm!='AFTER':
                continue
            coverage['baseline_reports_preserved'] += merge['baseline_reports_preserved']
            q2 = {q['id']: {t['target'] for t in q['target_results'] if t['result'] in AVAILABLE}
                  for q in trace['question_round_2']}
            for identifier, finding, score in zip(merge['added'],selected,scored):
                related = set().union(*(q2.get(qid,set()) for qid in trace['candidate_question_links'][identifier]))
                if score['status']=='matched_tp' and any(overlaps_new_read(finding,entries[i]) for i in related&fresh):
                    links.update((key,i) for i in score['covered_golden_indices'] if gold[i]['tp_fp']=='tp')
    return dict(lookup=dict(lookup_counts),coverage=dict(coverage),cited_hunks=dict(cited),
                added_report_status={a:dict(counts) for a,counts in added_status.items()},
                fresh_second_linked_gold=len(links),fresh_second_linked_new_gold=len(links-found['A']))


def get_additions(run, packet):
    name = f'{packet["key"]}.json'
    output = run/'arms/ADD'
    old = load(BASE/name)['findings']
    findings = load(output/'findings'/name)['findings']
    assert findings[:len(old)]==old
    details = load(output/'scoring/results.details.json')
    scored = next(x for x in details if x['pr_key']==packet['key'])['candidate_findings']
    assert len(scored)==len(findings)
    assert all(all(a[k]==b[k] for k in ('file','start_line','end_line','message')) for a,b in zip(findings,scored))
    return scored[len(old):]


def pct(n, d):
    return f'{n/d:.2%}'


def render(data):
    scores, audit, c = data['scores'], data['audit'], data['costs']
    old = data['old_summary']
    fixed_cost = old['fixed_cost']
    current = audit['lookup']
    replay = data['replay']
    pairs = {p['comparison']:p for p in data['pairs']}
    rows = []
    for arm,label in LABELS.items():
        s = scores[arm]
        p = pairs[f'{arm}-A']
        if arm=='FIXED':
            lookup_cell, full_cell = '—', '—'
            reach = '—'
            cost = (f'${fixed_cost["cost_excluding_common_baseline_lower_usd"]:.2f}–'
                    f'${fixed_cost["cost_excluding_common_baseline_upper_usd"]:.2f}')
        elif arm=='BEFORE':
            n, d = old['coverage']['actual_question_targets'], old['coverage']['questions']
            lookup_cell = full_cell = f'{pct(n,d)} ({n}/{d})'
            reach = f'{old["coverage"]["lookup_hunks"]} / {old["coverage"]["new_second_round_hunks"]}'
            cost = f'${old["costs"]["known_usd"]:.2f}'
        else:
            d = current['questions']
            lookup_cell = f'{pct(current["some_source"],d)} ({current["some_source"]}/{d})'
            full_cell = f'{pct(current["all_source"],d)} ({current["all_source"]}/{d})'
            reach = f'{audit["coverage"]["looked_up_hunks"]} / {audit["coverage"]["fresh_second_hunks"]}'
            cost = f'${c["known_usd"]:.2f}'
            if c['upper_usd']-c['known_usd']>.005:
                cost += f' (예약 포함 ${c["upper_usd"]:.2f})'
        rows.append(f'| {label} | {lookup_cell} | {full_cell} | {reach} | {p["retained"]}/45 | '
                    f'{p["added"]} | {s["gold"]}/352 | {s["grounded_F1"]:.2%} | {s["augmented_F1"]:.2%} | '
                    f'{s["false_findings"]} | {cost} |')
    delta = (scores['AFTER']['grounded_F1']-scores['BEFORE']['grounded_F1'])*100
    fixed_delta = (scores['AFTER']['grounded_F1']-scores['FIXED']['grounded_F1'])*100
    lo, hi = [100*v for v in pairs['AFTER-BEFORE']['F1_delta_95_pr_bootstrap']['grounded_F1']]
    matched = replay['replay']
    lines = [
        '# ReviewBench 25개 PR 다중 ID 조회 재실험','',
        f'수정 후 Grounded F1은 **{scores["AFTER"]["grounded_F1"]:.2%}**였다. 수정 전과 비교해 **{delta:+.2f}%p**, 고정 두 관점과 비교해 **{fixed_delta:+.2f}%p**였다.','',
        '| 기존 리뷰에 결합한 방식 | 조회 성공¹ | 전체 대상 확보¹ | 조회 hunk / 2차 신규 hunk | 기존 정탐 보존 | 추가 정탐² | gold 탐지 | Grounded F1 | Augmented F1 | 오탐 | 비교 비용³ |',
        '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|',*rows,'',
        f'¹ 조회 성공은 질문에 필요한 원문을 한 개 이상 확보한 경우다. 수정 후에는 앞서 전달한 원문을 재사용한 질문도 포함했다. 현재 라운드에서 원문을 받은 질문은 {current["current_round_source"]}/{current["questions"]}개였다. 전체 대상 확보는 요청한 ID의 원문이 모두 제공된 경우다. 고정 두 관점은 질문별 조회 단계 없이 같은 공개 원문 묶음을 읽었다. 공통 원문은 {audit["coverage"]["supplied_hunks"]:,}/{audit["coverage"]["total_hunks"]:,}개 hunk이며, 대형 PR 5개의 diff 일부를 담았다.','',
        '² 추가 정탐은 기존 LSP 리뷰가 놓쳤으나 결합 후 탐지한 벤치마크 정답의 수다. 기존 보고 40개는 세 조건에서 내용과 순서가 같았다. 정탐 보존은 공식 판독의 gold ID 기준이다.','',
        f'³ 후보 생성·검증, 결합 검증, 공식 채점의 사용량 비용 합계다. 공통인 기존 리뷰 비용 $12.79는 별도다. 이번 실험의 새 API 비용은 ${c["known_usd"]:.2f}, 이전 실험을 합한 예약 포함 누적액은 ${c["cumulative_upper_usd"]:.2f}/$70이다.','',
        '첫 채점에서 23개 PR을 완료했고, 나머지 2개는 입력 비용 예약 때문에 중단됐다. 체크포인트를 이어받고 완전히 동일한 판독 입력의 응답을 재사용한 뒤, 남은 호출의 입력 토큰을 제공자 계수기로 확인해 같은 상한에서 25개 모두 공식 채점했다. 재개 초기에는 인증 헤더 비교 오류가 있었고, 해당 시도는 모델 호출 전에 실패했다. 원시 리뷰, 이전 결과 및 실패 로그를 보존했다.','',
        f'저장된 수정 전 질문 {matched["questions"]}개를 같은 조회 예산에서 오프라인으로 재생했을 때 원문을 한 개 이상 받은 질문은 {replay["before"]["some_source"]}개에서 {matched["some_source"]}개로 바뀌었다. 이전에 ID를 인식하지 못했던 질문 {replay["recovered"]["total_previously_unknown"]}개 중 {replay["recovered"]["source_available"]}개에서 원문을 확보했다. 이 재생은 기존 질문을 고정한 조회기 검증이며, 표의 수정 후 F1은 질문을 새로 생성하고 공식 채점한 결과다.','',
        f'수정 후 질문 {current["questions"]}개 중 여러 대상을 요청한 질문은 {current["multi_target"]}개였다. 대상 단위로는 ID 연결 실패 {current.get("unknown_target",0)}건, 원문 한도 때문에 보류된 요청 {current.get("budget_deferred",0)}건이었다.','',
        f'수정 후 2차에서 {audit["coverage"]["PRs_with_fresh_second_hunks"]}/25개 PR의 hunk를 처음 읽었다. 2차 질문 ID와 새 hunk의 원문을 함께 인용한 추가 보고 중 기존 리뷰가 놓쳤던 gold는 {audit["fresh_second_linked_new_gold"]}개였다. 이 집계는 질문과 인용의 연결을 확인한다.','',
        f'수정 후−수정 전 Grounded F1 차이의 PR 쌍 재표집 95% 구간은 {lo:+.2f}~{hi:+.2f}%p다. 같은 개발 PR에서 조건당 한 번 평가했으며, 이 구간에는 모델 재실행 변동이 들어가지 않는다.','',
        f'[실험 명세]({ROOT}/PROTOCOL_KO.md) · [집계 JSON]({ROOT}/summary.json) · [오프라인 조회 재생]({ROOT}/lookup_replay.json) · [수정 후 원시 결과]({ROOT}/arms/ADD) · [수정 전 원시 결과]({BEFORE}/arms/ADD) · [고정 두 관점 결과]({PRIOR}/arms/ADD)','',
    ]
    doc = '\n'.join(lines)
    (ROOT/'RESULTS_KO.md').write_text(doc)
    (ROOT.parents[1]/'paper/REVIEWBENCH_ADAPTIVE_MULTI_ID25_20261113_KO.md').write_text(doc)


def main():
    frozen = verify()
    assert load(ROOT/'status.json')['phase']=='complete'
    with socket.socket() as sock:
        sock.settimeout(.5)
        assert sock.connect_ex(('127.0.0.1',PORT))!=0
    packet_list = packets()
    previous = load(BEFORE/'summary.json')
    helper = scorer_helpers()
    helper.score_path = lambda arm: (BASE.parent/'scoring' if arm=='A' else
                                     {'FIXED':PRIOR,'BEFORE':BEFORE,'AFTER':ROOT}[arm]/'arms/ADD/scoring')
    scores, found, per_pr = {}, {}, {}
    for arm in ('A',*LABELS):
        scores[arm], found[arm], per_pr[arm] = helper.scores(arm)
        assert set(per_pr[arm])=={p['key'] for p in packet_list}
    configs = [load(helper.score_path(a)/'results.json')['eval_config'] for a in LABELS]
    assert configs[0]==configs[1]==configs[2]
    audit = read_audit(packet_list,found)
    assert audit['coverage']['baseline_reports_preserved']==40 and audit['coverage']['model_calls']==75
    replay = historical_replay(packet_list)
    write(ROOT/'lookup_replay.json',replay)
    replay.pop('per_PR')
    pairs = [helper.delta(f'{a}-{b}',a,b,found,per_pr) for a,b in
             [(a,'A') for a in LABELS]+[('AFTER','BEFORE'),('AFTER','FIXED')]]
    data = dict(scores=scores,pairs=pairs,audit=audit,replay=replay,old_summary=previous,
                costs=costs(frozen),frozen_files_verified=len(frozen['hashes']))
    write(ROOT/'summary.json',data)
    render(data)
    print(json.dumps({k:v for k,v in data.items() if k not in ('old_summary','pairs')},ensure_ascii=False))


if __name__ == '__main__':
    main()
