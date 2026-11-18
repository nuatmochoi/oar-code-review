"""Compare official scores and trace evidence and candidate outcomes for each PR."""
from collections import Counter
import json
import socket

from adaptive import resolve_targets, source_index
from experiment import ROOT, PORT, load, write
from freeze_run import verify
from merge import BASE, packets
from report import costs, scorer_helpers

BEFORE = ROOT.parent/'reviewbench_adaptive_multi_id25_20261113'
AVAILABLE = {'delivered','shared_read','already_visible'}


def audit(run, role_bundles):
    details = load(run/'arms/ADD/scoring/results.details.json')
    by_key = {row['pr_key']:row['candidate_findings'] for row in details}
    counts, states, merges, added_status, roles = (Counter() for _ in range(5))
    complete_question_status = Counter()
    reported_missing_roles = Counter()
    per_pr = []
    for packet in packets():
        index, key = packet['index'], packet['key']
        folder = run/'arms/ADD/review'/f'{index:02d}'
        review, complete = load(folder/'questions.json'), load(folder/'complete.json')
        baseline = load(BASE/f'{key}.json')['findings']
        reports = load(run/'arms/ADD/findings'/f'{key}.json')['findings']
        scored = by_key[key]
        assert reports[:len(baseline)]==baseline and len(reports)==len(scored)
        for raw, result in zip(reports,scored):
            assert all(raw[k]==result[k] for k in ('file','start_line','end_line','message'))
        qcount = Counter()
        questions = review['question_round_1']+review['question_round_2']
        _, entries = source_index(packet)
        for question in questions:
            requests = question['roles' if role_bundles else 'target_results']
            complete_set = bool(requests) and all(r['result'] in AVAILABLE for r in requests)
            qcount.update(dict(questions=1,available_set=int(complete_set),
                               answered_set=int(complete_set and question['review_status']=='answered'),
                               partial=int(not complete_set and any(r['result'] in AVAILABLE for r in requests))))
            if complete_set:
                complete_question_status[question['review_status']] += 1
                if role_bundles and question['review_status']=='needs_evidence':
                    reported_missing_roles.update(set(question['missing_roles']))
            if role_bundles:
                if len(requests)>1:
                    qcount['multi_role_questions'] += 1
                    qcount['multi_role_complete'] += complete_set
                    qcount['multi_role_answered'] += complete_set and question['review_status']=='answered'
                    qcount['multi_role_distinct_sources'] += complete_set and len({r['target'] for r in requests})>1
                for request in requests:
                    available = request['result'] in AVAILABLE
                    roles[request['role'],'requested'] += 1
                    roles[request['role'],'available'] += available
                    hints = {identifier for identifier, valid in resolve_targets(request['hints'],entries).items() if valid}
                    qcount['roles_without_hints'] += not hints
                    qcount['roles_filled_without_hints'] += available and not hints
                    qcount['roles_filled_outside_hints'] += available and bool(hints) and request['target'] not in hints
        counts.update(qcount)
        counts.update(dict(baseline_reports=len(baseline),model_calls=len(review['usage']),
                           candidate_tokens=review['actual_tokens'],
                           lookup_hunks=len(set(review['lookup1_hunks'])|set(review['lookup2_hunks'])),
                           fresh_second_hunks=len(review['newly_read_hunks']),
                           mechanical_failures=len(review['mechanically_ineligible']),
                           eligible_candidates=len(review['candidate_ledger']),added=len(complete['added']),
                           held_by_cap=len(complete['withheld_by_cap'])))
        states.update(v['state'] for v in review['prior_candidate_verdicts'])
        merges.update(v['disposition'] for v in (complete['merge_assessment'] or {}).get('verdicts',[]))
        added_status.update(item['status'] for item in scored[len(baseline):])
        if role_bundles:
            revisions = set(review['accepted_revisions'])
            published = set(complete['added'])
            answered_questions = {question['id'] for question in questions
                                  if question['review_status']=='answered' and all(
                                      r['result'] in AVAILABLE for r in question['roles'])}
            counts['linked_revisions'] += len(revisions)
            counts['revised_published'] += len(revisions&published)
            for candidate, result in zip(complete['added'],scored[len(baseline):],strict=True):
                is_tp = result['status'] in ('matched_tp','novel_tp')
                counts['revised_published_TP'] += candidate in revisions and is_tp
                linked = set(review['candidate_question_links'][candidate]) & answered_questions
                counts['published_linked_to_answered'] += bool(linked)
                counts['TP_linked_to_answered'] += bool(linked) and is_tp
        per_pr.append(dict(index=index,key=key,**dict(qcount)))
    assert counts['baseline_reports']==40 and counts['model_calls']==75
    role_detail = {role:{metric:roles[role,metric] for metric in ('requested','available')}
                   for role in sorted({role for role,_ in roles})}
    return dict(counts=dict(counts),prior_verdicts=dict(states),merge_verdicts=dict(merges),
                added_status=dict(added_status),role_detail=role_detail,per_PR=per_pr,
                complete_question_status=dict(complete_question_status),
                reported_missing_roles_after_complete_delivery=dict(reported_missing_roles))


def judge_recovery():
    replay_count = len(list((ROOT/'audit/judge_replays').glob('*.json')))
    limited = []
    for path in (ROOT/'api').glob('*/receipt.json'):
        receipt = load(path)
        if receipt['phase']=='judge' and receipt['max_output_tokens']!=16384:
            assert receipt['status']=='completed' and receipt['usage']['output_tokens']<receipt['max_output_tokens']
            limited.append(dict(id=receipt['id'],cap=receipt['max_output_tokens'],
                                output_tokens=receipt['usage']['output_tokens']))
    assert len(limited)==1
    return dict(replayed_calls=replay_count,limited_requests=limited)


def output_recovery(found):
    recovered = list(ROOT.glob('arms/ADD/review/*/*/recovered_prefix.json'))
    indices = {int(path.parent.parent.name) for path in recovered}
    keys = {packet['key'] for packet in packets() if packet['index'] in indices}
    assert len(indices)==len(keys)
    return dict(response_count=len(recovered),PR_count=len(keys),gold_without_recovered_PRs={
        arm:sum(key not in keys for key,_ in found[arm]) for arm in ('BEFORE','BUNDLES')})


def render(data):
    metrics, audits = data['scores'], data['audit']
    fixed, current = metrics['BEFORE'], metrics['BUNDLES']
    delta = (current['grounded_F1']-fixed['grounded_F1'])*100
    pairs = {row['comparison']:row for row in data['pairs']}
    lo,hi = [v*100 for v in pairs['BUNDLES-BEFORE']['F1_delta_95_pr_bootstrap']['grounded_F1']]
    lines = [
        '# ReviewBench 25개 PR 질문별 근거 묶음 실험','',
        f'근거 묶음 방식의 Grounded F1은 **{current["grounded_F1"]:.2%}**, 기존 다중 ID 방식은 **{fixed["grounded_F1"]:.2%}**였다. 차이는 **{delta:+.2f}%p**다.','',
        '| 기존 리뷰에 결합한 방식 | 질문별 전체 원문 확보¹ | 확보 후 답변 완료¹ | 기존 정탐 보존 | 추가 정탐² | 전체 gold / 352 | Grounded F1 | Augmented F1 | 오탐 | API 비용³ |',
        '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|',
    ]
    for arm,label in [('BEFORE','기존 다중 ID'),('BUNDLES','역할별 근거 묶음 + 의미 검색')]:
        c, score = audits[arm]['counts'], metrics[arm]
        n = c['questions']
        retained = pairs[f'{arm}-A']
        cost = data['costs'][arm]
        api_price = f'${cost["known_usd"]:.2f}'
        if cost['upper_usd']-cost['known_usd']>.005:
            api_price += f' (예약 포함 ${cost["upper_usd"]:.2f})'
        lines.append(f'| {label} | {c["available_set"]/n:.2%} ({c["available_set"]}/{n}) | '
                     f'{c["answered_set"]/n:.2%} ({c["answered_set"]}/{n}) | {retained["retained"]}/45 | '
                     f'{retained["added"]} | {score["gold"]} | {score["grounded_F1"]:.2%} | '
                     f'{score["augmented_F1"]:.2%} | {score["false_findings"]} | {api_price} |')
    old = audits['BEFORE']
    new = audits['BUNDLES']
    states = new['prior_verdicts']
    c = new['counts']
    p = pairs['BUNDLES-BEFORE']
    index = data['local_index']
    missing = new['reported_missing_roles_after_complete_delivery']
    judge = data['judge_recovery']['limited_requests'][0]
    recovery = data['output_recovery']
    unaffected_gold = recovery['gold_without_recovered_PRs']
    lines += [
        '',
        '¹ 기존 방식은 질문이 지정한 ID를 모두 제공했는지, 근거 묶음 방식은 질문에 명시한 모든 역할에 원문을 제공했는지 셌다. 답변 완료의 분모는 전체 질문이며, 원문을 모두 받은 뒤 리뷰 모델이 답할 수 있었다고 판정한 질문을 센다. 두 방식에서 별도로 질문을 생성했다. 원문의 의미와 공식 정답 일치는 모델이 판정했다. 사람의 검토는 0회다.','',
        '² 추가 정탐은 기존 LSP 리뷰가 놓친 공식 정답 중 결합 결과가 찾은 수다. 양쪽 결과는 기존 보고 40개를 보존했다. 기존 정탐 보존은 공식 채점의 gold ID로 측정했다.','',
        f'³ 리뷰 모델, PR당 탐색·검증 3회, 토큰 상한, 신규 지출 상한을 맞췄다. API 비용은 후보 탐색·검증·결합·공식 채점 사용량의 요율 추정액이다. 공통 LSP 리뷰 비용 $12.79는 별도다. 로컬 임베딩 색인은 {index["index_seconds"]:.1f}초 걸렸다. 신규 실행 상한은 $17.71이며 이전 원장과 분리했다.','',
        f'4,096토큰 출력 한도에 도달한 응답 {recovery["response_count"]}개는 모델을 다시 호출하지 않고 완전한 배열 항목만 복구했다. 끊긴 항목과 그 뒤의 항목은 집계에 들어가지 않았다. 이 {recovery["PR_count"]}개 PR을 제외한 나머지 {25-recovery["PR_count"]}개에서도 기존 다중 ID는 공식 정답 {unaffected_gold["BEFORE"]}개, 근거 묶음은 {unaffected_gold["BUNDLES"]}개를 찾았다. 원시 출력과 복구 내역을 보존했다.','',
        f'공식 채점은 체크포인트와 이전과 동일한 입력의 응답을 재사용해 25개 모두 마쳤다. 마지막 요청 1회의 채점 출력 상한을 16,384에서 {judge["cap"]:,}로 낮췄고 실제 출력은 {judge["output_tokens"]:,}토큰이었다. 파일별 조회 결과가 일치하는 경우에 한해 파일 순서를 이전과 같게 복원했다. 채점 모델·프롬프트·집계 기준은 동일하다. 재개 과정의 지출도 표의 비용에 포함했다.','',
        f'근거 묶음의 초기 후보 판정은 지지 {states.get("supported",0)}개, 수정 {states.get("revised",0)}개, 반박 {states.get("refuted",0)}개, 보류 {states.get("unresolved",0)}개, 중복 {states.get("duplicate",0)}개였다. '
        f'수정안 {c.get("linked_revisions",0)}개가 초기 후보 ID에 연결됐고, 그중 {c.get("revised_published",0)}개가 최종 보고에 포함됐다. 공식 채점에서 그중 {c.get("revised_published_TP",0)}개를 정탐으로 판정했다. '
        f'최종 추가 보고는 기존 {old["counts"]["added"]}개, 근거 묶음 {c["added"]}개였다. 후보별 원문, 상위 후보, 원인 판정, 결합 결과는 각 PR의 기록에 남겼다.','',
        f'복수 역할을 요청한 질문 {c.get("multi_role_questions",0)}개 중 {c.get("multi_role_complete",0)}개에서 모든 역할에 원문을 배정했고, {c.get("multi_role_answered",0)}개에서 답변까지 완료했다. '
        f'유효한 ID 힌트가 없었던 역할 {c.get("roles_without_hints",0)}개 중 {c.get("roles_filled_without_hints",0)}개에 검색으로 원문을 제공했다. '
        f'힌트가 있었지만 그 밖에서 원문을 찾은 역할은 {c.get("roles_filled_outside_hints",0)}개였다. '
        f'완료 질문 ID와 연결된 추가 보고는 {c.get("published_linked_to_answered",0)}개였고 그중 {c.get("TP_linked_to_answered",0)}개를 공식 판독에서 정탐으로 봤다.','',
        f'원문을 모두 받은 질문 중 {new["complete_question_status"].get("needs_evidence",0)}개는 여전히 근거가 부족하다고 판정됐다. 실패 분기 {missing.get("failure",0)}개, 반증 {missing.get("counterevidence",0)}개, 구현 {missing.get("implementation",0)}개 질문에서 해당 역할이 부족하다고 모델이 기록했다. 한 질문에 여러 역할이 겹친다. 근거 묶음의 기계적 검사를 통과한 후보는 {old["counts"]["eligible_candidates"]}개에서 {c["eligible_candidates"]}개로 줄었다. 공식 판독에서 추가 보고가 기존 gold에 일치한 건수는 {old["added_status"].get("matched_tp",0)}개에서 {new["added_status"].get("matched_tp",0)}개로 줄었고, gold 밖 정탐 보고는 {old["added_status"].get("novel_tp",0)}개에서 {new["added_status"].get("novel_tp",0)}개로 늘었다.','',
        f'근거 묶음 방식만 찾은 공식 정답은 {p["added"]}개, 기존 다중 ID 방식만 찾은 정답은 {p["lost"]}개였다. '
        f'Grounded F1 차이의 PR 쌍 재표집 95% 구간은 {lo:+.2f}~{hi:+.2f}%p다. 같은 개발 PR에서 조건당 한 번 실행한 비교이며, 재실행 변동은 이 구간에 들어가지 않는다. 이번 실험은 역할 분해·의미 순위·묶음 조회·수정 추적을 함께 평가했다.','',
        '의미 검색은 질문과 역할마다 기존 원문 안에서 상위 3개를 순위화했다. 공통 원문은 두 방식의 마지막 리뷰 입력에도 들어간다. 순위는 앞선 검증 단계에서 어떤 원문을 읽고 조합하는지에 영향을 주며, 동일 심볼의 호출·구현·실패 경로가 실제로 연결되는지는 보장하지 않는다.','',
        f'[실험 명세]({ROOT}/PROTOCOL_KO.md) · [집계와 PR별 근거 제공률]({ROOT}/summary.json) · [원시 질문·후보·채점]({ROOT}/arms/ADD) · [채점 복구 기록]({ROOT}/audit) · [기존 다중 ID 실험]({BEFORE}/RESULTS_KO.md) · [임베딩 모델 카드](https://huggingface.co/BAAI/bge-small-en-v1.5)','',
    ]
    doc = '\n'.join(lines)
    (ROOT/'RESULTS_KO.md').write_text(doc)
    (ROOT.parents[1]/'paper/REVIEWBENCH_ROLE_BUNDLES25_20261113_KO.md').write_text(doc)


def main():
    frozen = verify()
    assert load(ROOT/'status.json')['phase']=='complete'
    with socket.socket() as sock:
        sock.settimeout(.5)
        assert sock.connect_ex(('127.0.0.1',PORT))!=0
    helper = scorer_helpers()
    helper.score_path = lambda arm: BASE.parent/'scoring' if arm=='A' else (
        BEFORE if arm=='BEFORE' else ROOT)/'arms/ADD/scoring'
    scores, found, per = {}, {}, {}
    for arm in ('A','BEFORE','BUNDLES'):
        scores[arm], found[arm], per[arm] = helper.scores(arm)
        assert set(per[arm])=={p['key'] for p in packets()}
    assert load(helper.score_path('BEFORE')/'results.json')['eval_config']==load(helper.score_path('BUNDLES')/'results.json')['eval_config']
    pairs = [helper.delta(f'{a}-{b}',a,b,found,per) for a,b in
             [('BEFORE','A'),('BUNDLES','A'),('BUNDLES','BEFORE')]]
    data = dict(scores=scores,pairs=pairs,audit={'BEFORE':audit(BEFORE,False),'BUNDLES':audit(ROOT,True)},
                costs={'BEFORE':load(BEFORE/'summary.json')['costs'],'BUNDLES':costs(frozen)},
                local_index=load(ROOT/'preflight.json'),hashes_verified=len(frozen['hashes']),
                judge_recovery=judge_recovery(),output_recovery=output_recovery(found))
    temporary = ROOT/'summary.rebuild.json'
    write(temporary,data)
    temporary.replace(ROOT/'summary.json')
    render(data)
    print(json.dumps({k:v for k,v in data.items() if k not in ('audit','local_index')},ensure_ascii=False))


if __name__=='__main__':
    main()
