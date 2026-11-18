"""Read official scores and reconstruct usage from the two experiment ledgers."""
from collections import Counter, defaultdict
from pathlib import Path
import importlib.util
import json
import re
import socket

from budget_proxy import estimated_cost
from experiment import ROOT, PORT, load, sha, write
from freeze_run import verify
from merge import BASE, PRIOR, packets


def score_path(arm):
    if arm == 'A':
        return BASE.parent/'scoring'
    run = ROOT if arm == 'GENERAL' else PRIOR
    return run/'arms/ADD/scoring'


def scorer_helpers():
    spec = importlib.util.spec_from_file_location('prior_summarize',PRIOR/'summarize.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.score_path = score_path
    return module


def receipt_rows(run):
    for admission in sorted((run/'api').glob('*/admission.json')):
        path = admission.with_name('receipt.json')
        assert path.is_file(), path
        yield admission.parent, load(path)


def usage_cost(usage):
    return estimated_cost(usage)['usd_list_rate_estimate']


def total_cost(receipts):
    known = sum(usage_cost(row['usage']) for _, row in receipts if row.get('usage'))
    upper = sum(row.get('settled_upper',row['reserved_usd']) for _, row in receipts)
    assert known <= upper + 1e-7
    return dict(known_usd=known,upper_usd=upper,calls=len(receipts),
                statuses=dict(Counter(row['status'] for _, row in receipts)))


def render_matcher_finding(role, index, finding):
    escape = lambda s:s.replace('&','&amp;').replace('<','&lt;')
    path = escape(finding['file']).replace('"','&quot;')
    return (f'<{role} index="{index}" file="{path}" lines="{finding["start_line"]}-{finding["end_line"]}">'
            f'{escape(finding["message"])}</{role}>')


def matcher_requests(candidate, golden):
    groups = defaultdict(list)
    for finding in candidate:
        groups[finding['file']].append(finding)
    golden_groups = defaultdict(list)
    for finding in golden:
        golden_groups[finding['file']].append(finding)
    out = []
    for path, candidates in groups.items():
        gold = golden_groups[path]
        for i in range(0,len(candidates),10):
            left = '\n'.join(render_matcher_finding('candidate',j,f) for j,f in enumerate(candidates[i:i+10]))
            for k in range(0,len(gold),10):
                right = '\n'.join(render_matcher_finding('golden',j,f) for j,f in enumerate(gold[k:k+10]))
                out.append(f'## Candidate findings\n{left}\n\n## Golden findings\n{right}')
    return tuple(out)


def judge_cost_for_prior_add():
    """Attribute classifier sessions and full matcher prompt sequences to the historical arms."""
    classifier, expected = {}, defaultdict(list)
    arms = ('GENERAL','DIVERGENT','ADD')
    for arm in arms:
        root = PRIOR/'arms'/arm
        log = (root/'scoring/judge-1.log').read_text()
        for path in re.findall(r'Session log: (.+)',log):
            with Path(path).open() as session:
                identifier = json.loads(session.readline())['id']
            assert identifier not in classifier
            classifier[identifier] = arm
        for file in (root/'findings').glob('*.json'):
            expected_prompts = matcher_requests(load(file)['findings'],load(PRIOR/'upstream/golden'/file.name)['findings'])
            if expected_prompts:
                expected[expected_prompts].append((arm,file.stem))

    matcher = defaultdict(list)
    cost = Counter()
    historical_calls = [(p,r) for p,r in receipt_rows(PRIOR) if r['phase']=='judge']
    for path, row in historical_calls:
        request = load(path/'request.json')
        cache_key = request['prompt_cache_key']
        usd = usage_cost(row['usage'])
        if cache_key in classifier:
            cost[classifier[cache_key]] += usd
            continue
        assert request['input'][0]['content'].startswith('You evaluate whether two sets of code review findings')
        content = request['input'][-1]['content']
        prompt = content if isinstance(content,str) else ''.join(x['text'] for x in content if x['type']=='input_text')
        matcher[cache_key].append((prompt,usd,row['admitted_at']))
    # GENERAL completed before the other two scorers ran concurrently.
    log = (PRIOR/'pipeline.log').read_text().splitlines()
    boundary = next(d['time'] for d in map(json.loads,log) if d.get('phase')=='judge' and d.get('arm')=='DIVERGENT')
    bins = defaultdict(list)
    for rows in matcher.values():
        prompts = tuple(r[0] for r in rows)
        is_general = rows[0][2] < boundary
        bins[prompts,is_general].append(sum(r[1] for r in rows))
    lo = hi = ambiguous = 0
    for (prompts,is_general), values in bins.items():
        matches = [(arm,key) for arm,key in expected[prompts] if (arm=='GENERAL')==is_general]
        assert len(matches)==len(values)>0
        add_count = sum(arm=='ADD' for arm,_ in matches)
        if add_count:
            ordered = sorted(values)
            lo += sum(ordered[:add_count])
            hi += sum(ordered[-add_count:])
            ambiguous += int(add_count < len(values))
    assert sum(len(v) for v in expected.values())==len(matcher)
    assert abs(sum(cost.values())+sum(sum(v) for v in bins.values())-sum(usage_cost(r['usage']) for _,r in historical_calls)) < 1e-8
    return dict(lower_usd=cost['ADD']+lo,upper_usd=cost['ADD']+hi,
                identical_matcher_sequences_shared_with_other_arm=ambiguous)


def reviewer_cost(source_arm, merge_run):
    def stages(root):
        return [load(p) for p in (root/'review').glob('*/complete.json')]
    discovery = stages(PRIOR/'arms'/source_arm)
    merge = stages(merge_run/'arms/ADD')
    assert len(discovery)==len(merge)==25
    return dict(discover_and_verify_usd=sum(usage_cost(u) for r in discovery for u in r['usage']),
                merge_verify_usd=sum(usage_cost(r['usage']) for r in merge if r['usage']),
                merge_calls=sum(bool(r['usage']) for r in merge),
                appended=sum(len(r['added']) for r in merge),
                omitted_due_to_cap=sum(len(r['withheld_by_cap']) for r in merge))


def additions_status(run):
    root = run/'arms/ADD'
    counts = Counter()
    for row in load(root/'scoring/results.details.json'):
        name = f'{row["pr_key"]}.json'
        base = load(BASE/name)['findings']
        complete = load(root/'findings'/name)['findings']
        assert complete[:len(base)]==base
        scored = row['candidate_findings']
        assert len(scored)==len(complete)
        for original, result in zip(complete,scored):
            assert all(original[k]==result[k] for k in ('file','start_line','end_line','message'))
        counts.update(c['status'] for c in scored[len(base):])
    return dict(counts)


def report():
    frozen = verify()
    assert load(ROOT/'status.json')['phase']=='complete'
    receipts = list(receipt_rows(ROOT))
    new_cost = total_cost(receipts)
    judge_cost = total_cost([(p,r) for p,r in receipts if r['phase']=='judge'])
    assert new_cost['upper_usd'] <= frozen['new_api_cap_usd']
    with socket.socket() as sock:
        sock.settimeout(.5)
        assert sock.connect_ex(('127.0.0.1',PORT)) != 0
    historical_cost = judge_cost_for_prior_add()
    helper = scorer_helpers()
    base_cost = load(BASE.parents[2]/'comparison.final.json')['arms']['A']['logical_review_usd']
    labels = {'A':'기존 LSP 리뷰', 'GENERAL':'기존 리뷰 + 일반 후보', 'DIVERGENT':'기존 리뷰 + 발산 후보'}
    rows = {}; found = {}; per = {}
    old_config = load(score_path('DIVERGENT')/'results.json')['eval_config']
    assert old_config == load(score_path('GENERAL')/'results.json')['eval_config']
    for arm in labels:
        rows[arm],found[arm],per[arm] = helper.scores(arm)
        assert set(per[arm])=={p['key'] for p in packets()}
    rows['GENERAL'].update(reviewer_cost('GENERAL',ROOT),
                           judge_cost_lower_usd=judge_cost['known_usd'],judge_cost_upper_usd=judge_cost['upper_usd'],
                           added_report_status=additions_status(ROOT))
    rows['DIVERGENT'].update(reviewer_cost('DIVERGENT',PRIOR),
                             judge_cost_lower_usd=historical_cost['lower_usd'],judge_cost_upper_usd=historical_cost['upper_usd'],
                             added_report_status=additions_status(PRIOR))
    diffs = [helper.delta(f'{a}-{b}',a,b,found,per) for a,b in
             [('GENERAL','A'),('DIVERGENT','A'),('GENERAL','DIVERGENT')]]
    for arm in ('GENERAL','DIVERGENT'):
        r = rows[arm]
        r['cost_excluding_common_baseline_lower_usd'] = r['discover_and_verify_usd']+r['merge_verify_usd']+r['judge_cost_lower_usd']
        r['cost_excluding_common_baseline_upper_usd'] = r['discover_and_verify_usd']+r['merge_verify_usd']+r['judge_cost_upper_usd']
    result = dict(PRs=25,gold=352,rows=rows,pairs=diffs,common_A_reviewer_usd=base_cost,new_physical_api=new_cost,
                  old_add_judge_attribution=historical_cost,prior_paid_usd=frozen['prior_api_usd'],
                  cumulative_conservative_usd=new_cost['upper_usd']+frozen['prior_api_usd'],
                  frozen_files_verified=len(frozen['hashes']),eval_config=old_config,human_review=0)
    write(ROOT/'summary.json',result)
    render(result,labels)
    print(json.dumps(result,ensure_ascii=False))


def render(result, labels):
    def pct(x): return f'{100*x:.2f}%'
    def usd(lo, hi):
        return f'${lo:.2f}' if round(lo,2)==round(hi,2) else f'${lo:.2f}–${hi:.2f}'
    rows = result['rows']
    pairs = {p['comparison']:p for p in result['pairs']}
    delta = (rows['GENERAL']['grounded_F1']-rows['DIVERGENT']['grounded_F1'])*100
    lines = ['# ReviewBench 25개 PR 일반 후보와 발산 후보 결합 비교','',
             f'기존 리뷰에 일반 후보를 결합한 Grounded F1은 **{pct(rows["GENERAL"]["grounded_F1"])}**, 발산 후보를 결합한 점수는 **{pct(rows["DIVERGENT"]["grounded_F1"])}**였다. 일반 후보 결합의 차이는 **{delta:+.2f}%p**다.','',
             '| 조건 | 탐지 / 352 | Grounded F1 | Augmented F1 | 기존 정탐 보존 / 소실 / 새 탐지 | 오탐 | 후보 준비 | 결합 검증 | 공식 판독 | 비용 합계¹ |',
             '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for arm in labels:
        r = rows[arm]
        if arm == 'A':
            preservation='45/45 · 0 · 0'
            prices='— | — | — | —'
        else:
            p = pairs[f'{arm}-A']
            preservation=f'{p["retained"]}/45 · {p["lost"]} · {p["added"]}'
            prices=(f'${r["discover_and_verify_usd"]:.2f} | ${r["merge_verify_usd"]:.2f} | '
                    f'{usd(r["judge_cost_lower_usd"],r["judge_cost_upper_usd"])} | '
                    f'{usd(r["cost_excluding_common_baseline_lower_usd"],r["cost_excluding_common_baseline_upper_usd"])}')
        lines.append(f'| {labels[arm]} | {r["gold"]} | {pct(r["grounded_F1"])} | {pct(r["augmented_F1"])} | {preservation} | {r["false_findings"]} | {prices} |')
    p = pairs['GENERAL-DIVERGENT']
    low,high = [100*v for v in p['F1_delta_95_pr_bootstrap']['grounded_F1']]
    lines.extend(['',f'두 결합 방식은 정답 {p["retained"]}개를 함께 잡았다. 일반 후보 결합만 잡은 정답은 {p["added"]}개, 발산 후보 결합만 잡은 정답은 {p["lost"]}개였다. '
                  f'일반−발산의 Grounded F1 차이에 대한 PR 쌍 재표집 95% 구간은 {low:+.2f}~{high:+.2f}%p다. 이미 사용한 개발 PR에서 각 방식 한 번씩 실행한 결과이며, 이 구간에는 모델 재실행 변동이 포함되지 않는다.',''])
    for arm in ('GENERAL','DIVERGENT'):
        r = rows[arm]; s = r['added_report_status']
        lines.append(f'- {labels[arm]}에서 추가한 보고 {r["appended"]}개 중 gold 매칭 정탐 {s.get("matched_tp",0)}개, 정답표 밖 정탐 {s.get("novel_tp",0)}개, 오탐 {s.get("matched_fp",0)+s.get("novel_fp",0)}개였다.')
    cost = result['new_physical_api']
    lines.extend(['',f'¹ 비용 합계에는 각 후보를 생성하고 공통 검증한 비용, 기존 리뷰와 결합하는 검증 비용, 공식 판독 비용이 들어간다. 두 방식에 공통인 A 리뷰 비용 ${result["common_A_reviewer_usd"]:.2f}는 별도다. '
                  '발산 후보의 공식 판독 비용은 당시 병행 실행에서 입력이 동일했던 일부 매칭 호출의 귀속 범위를 반영한다. '
                  f'이번에 새로 사용한 API 비용은 ${cost["known_usd"]:.4f}였고 예약 포함 상한은 ${cost["upper_usd"]:.4f}였다. 이전 실험을 포함한 누적 상한 집계는 ${result["cumulative_conservative_usd"]:.4f} / $70이다.','',
                  '두 방식 모두 기존 보고 40개의 내용과 순서를 보존했다. 정탐 보존은 각 결과를 공식 판독한 gold ID 기준이다. 같은 25개 PR에서 같은 공개 원문과 결합 프롬프트를 사용했고, 기존 결합 요청 22개를 원본과 대조했다. '
                  '각 PR의 추가 보고 상한은 3개, 전체 상한은 6개다. 공식 채점의 gold·모델·프롬프트 해시가 두 결합 조건에서 일치했다.','',
                  f'[실험 명세]({ROOT}/PROTOCOL_KO.md) · [공통 조건 검증]({ROOT}/preflight.json) · [집계 JSON]({ROOT}/summary.json) · [일반 결합 원시 결과]({ROOT}/arms/ADD) · [발산 결합 원시 결과]({PRIOR}/arms/ADD)',''])
    doc = ROOT.parents[1]/'paper/REVIEWBENCH_GENERAL_VS_DIVERGENT_ADD25_20261113_KO.md'
    doc.write_text('\n'.join(lines))
    (ROOT/'RESULTS_KO.md').write_text('\n'.join(lines))


if __name__ == '__main__':
    report()
