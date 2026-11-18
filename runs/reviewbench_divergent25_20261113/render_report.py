"""Render final Markdown from the read-only official result summary."""
from pathlib import Path

from experiment import ROOT, load, write
from freeze_run import verify
from summarize import score_path


LABELS={'A':'기존 LSP 리뷰 A (과거 참고)', 'GENERAL':'일반 독립 검토 2회 + 공통 검증',
        'DIVERGENT':'계약·소비자 / 실패·복구 독립 검토 + 공통 검증',
        'ADD':'기존 A 보존 + 발산 검토에서 확인한 별개 원인 추가'}


def pct(v):return f'{v*100:.2f}%'


def added_report_status():
    from collections import Counter
    root=ROOT/'arms/ADD'
    status=Counter()
    for row in load(root/'scoring/results.details.json'):
        key=row['pr_key']
        baseline=load(score_path('A').parent/'findings'/f'{key}.json')['findings']
        raw=load(root/'findings'/f'{key}.json')['findings']
        evaluated=row['candidate_findings']
        assert len(raw)==len(evaluated) and raw[:len(baseline)]==baseline
        for i,(x,y) in enumerate(zip(raw,evaluated)):
            assert (x['file'],x['start_line'],x['message'])==(y['file'],y['start_line'],y['message'])
            if i>=len(baseline):status[y['status']]+=1
    return status


def main():
    frozen=verify()
    data=load(ROOT/'summary.json'); results=data['records']; methods=data['mechanism']; coverage=data['coverage']
    assert {'A','GENERAL','DIVERGENT'}<=set(results)
    assert load(ROOT/'status.json')['phase']=='complete'
    assert methods['GENERAL']['model_calls']==methods['DIVERGENT']['model_calls']==75
    costs=data['cost']['all']; assert costs['conservative_usd']<=frozen['total_usd_cap']
    assert not costs['statuses'].get('admitted_unknown')
    g,d=results['GENERAL'],results['DIVERGENT']; a=results['A']
    rel=(d['grounded_F1']-g['grounded_F1'])*100
    lines=['# ReviewBench: 새 가설을 넓히고 기존 정탐을 보존하는 비교','',
           '2026-11-13. 같은 25개 개발 PR, 공식 gold TP 352개, 공식 판독. 검토·판독 모델과 공개 원문을 주 비교 양쪽에 동일하게 제공했다. '+
           f'발산 검토의 Grounded F1은 일반 독립 검토 대비 **{rel:+.2f}pp**였다. 기존 A는 과거 결과이며 공통 입력의 동등 대조군이 아니다.','',
           '| 조건 | gold 탐지 /352 | gold 재현율 | Grounded F1 | Augmented F1 | 오탐 보고 | 정답표 밖 추가 정탐 |',
           '|---|---:|---:|---:|---:|---:|---:|']
    for name,r in results.items():
        lines.append(f'| {LABELS[name]} | {r["gold"]} | {pct(r["recall"])} | {pct(r["grounded_F1"])} | {pct(r["augmented_F1"])} | {r["false_findings"]} | {r["novel_tp"]} |')
    lines += ['', '오탐은 공식 matched FP와 novel FP의 합계다. Grounded는 기존 gold를, Augmented는 기존 gold와 공식 판독에서 추가 확인한 정탐을 반영한다. 자체 원인 F1·유효 보고 F1과 혼용하지 않는다.', '',
              '## 새 탐지와 보존', '', '| 비교 | 보존 | 소실 | 새 탐지 | Grounded 변화 | PR 재표집 95% 구간 |',
              '|---|---:|---:|---:|---:|---:|']
    for p in data['pairs']:
        left,right=p['comparison'].split('-'); diff=(results[left]['grounded_F1']-results[right]['grounded_F1'])*100
        lo,hi=[v*100 for v in p['F1_delta_95_pr_bootstrap']['grounded_F1']]
        lines.append(f'| {p["comparison"]} | {p["retained"]} | {p["lost"]} | {p["added"]} | {diff:+.2f}pp | {lo:+.2f} ~ {hi:+.2f}pp |')
    lines+=['','공식 채점의 (PR, gold ID) 기준이다. 같은 보고를 재판독해도 매칭 결과가 달라질 수 있다. 사후 PR 쌍 bootstrap 10,000회는 생성·판독 반복 변동을 포함하지 않는다.','',
            '## 가설 생성과 후보 판정','',
            '| 조건 | 후보 | 인용 오류 등으로 탈락 | 지지 | 반박 | 중복 | 미해결 | 상한으로 미보고 | 보고 | 리뷰 실제 토큰 | 리뷰 사용량 비용 |',
            '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for name in ('GENERAL','DIVERGENT'):
        m=methods[name]; v=m['verdicts']
        lines.append(f'| {name} | {m["raw_candidates"]} | {m["ineligible"]} | {v.get("supported",0)} | {v.get("refuted",0)} | {v.get("duplicate",0)} | {v.get("unresolved",0)} | {m["withheld_by_cap"]} | {m["emitted"]} | {m["actual_review_tokens"]:,} | ${m["review_usd"]:.4f} |')
    lines+=['', '지지는 공통 검증 모델의 판단이다. 후보 전체에 공식 정답을 매겨 계산한 precision이 아니다. 두 독립 검토가 보고한 후보 파일 집합의 PR 평균 Jaccard 겹침은 '
            f'GENERAL {methods["GENERAL"]["mean_candidate_path_overlap"]:.3f}, DIVERGENT {methods["DIVERGENT"]["mean_candidate_path_overlap"]:.3f}다. 후보가 없는 양쪽 쌍은 제외한다. 파일 다양성은 원인 다양성의 대용치일 뿐이다.']
    if 'ADD' in results:
        m=methods['ADD']; add=results['ADD']; pp=(add['grounded_F1']-a['grounded_F1'])*100
        fresh=added_report_status(); assert sum(fresh.values())==m['appended']
        lines+=['',f'보존+추가는 기존 보고 {m["original_reports_preserved"]}개를 내용·순서 그대로 유지하고, {m["with_addition_PRs"]}개 PR에서 별개 원인 보고 {m["appended"]}개를 추가했다. '
                f'A 대비 Grounded {pp:+.2f}pp, 오탐 {a["false_findings"]}→{add["false_findings"]}개다. 검증 호출 {m["verifications"]}회, 신규 리뷰 비용 ${m["review_usd"]:.4f}. '
                '기존 A와 발산 후보를 재사용한 비용은 해당 생성 조건의 논리 비용에도 포함된다. 보존+추가는 결합 방법의 비교이고 발산 지시만의 단독 효과로 해석하지 않는다.', '',
                f'추가 보고 {sum(fresh.values())}개 중 공식 gold에 매칭된 정탐 {fresh["matched_tp"]}개, 정답표 밖 정탐 {fresh["novel_tp"]}개, '
                f'오탐 {fresh["matched_fp"]+fresh["novel_fp"]}개다. 보고 단위와 정답 항목 단위가 다르므로 이 합계를 gold 순증과 섞지 않는다. '
                '다음 검증에서 A+일반 후보 추가 조건을 두면, 발산 후보가 결합에서 주는 추가 가치를 구분할 수 있다. '
                '또한 추가 보고의 80% 정밀도를 높이기 위해 실패 연산과 원인 근거를 더 엄격하게 확인할 수 있다.', '',
                '보존+추가 한 PR에서 수락 후보 5개 중 모델이 상위 3개만 순위로 반환했다. 사전 상한에 필요한 순위는 모두 유효했으므로 원시 응답의 순서대로 내보냈고 나머지 2개는 미보고로 기록했다. 모델 재호출이나 정답 조회 없이 복구했다.']
    lines+=['','## 범위와 실행 비용','',
            f'- 25개 PR 중 {coverage["partial_diff_PRs"]}개는 예산에 맞춰 일부 diff hunk만 제공했다. 전달 hunk {coverage["supplied_hunks"]:,}/{coverage["total_hunks"]:,}, diff 파일 {coverage["supplied_files"]:,}/{coverage["total_files"]:,}, 검증된 중복 제거 원문 블록 {coverage["supplied_bodies"]:,}/{coverage["available_bodies"]:,}. 양쪽은 같은 입력을 읽었다. 과거 A는 전체 diff를 보았고 구성·호출도 달랐다.',
            '- 주 비교는 각 25×3 = 75개 모델 호출이다. 신규 native/LSP/SCIP/probe 조회 0회. 과거 FLAT의 공개 원문만 사용했다. reviewer는 공식 gold나 과거 판독을 읽지 않았다.',
            f'- 이 실험에서 새 물리 API {costs["calls"]}회, 확인 사용량 기준 ${costs["known_usd"]:.4f}, 미확인 예약 포함 ${costs["conservative_usd"]:.4f} / $70. 청구서 실측과는 다르다.',
            '- 공식 판독은 완주 조건의 25개 전부다. 이미 사용한 개발 세트, 각 1회, 인간 판독 0회. 다른 PR에 대한 개선으로 일반화하지 않는다.', '',
            f'[동결 명세]({ROOT}/PROTOCOL_KO.md) · [보존+추가 명세]({ROOT}/ADD_PROTOCOL_KO.md) · [구조화 집계]({ROOT}/summary.json) · [원시 요청·응답·판독]({ROOT}/arms) · [공통 입력 검사]({ROOT}/preflight.json) · [완료 검증]({ROOT}/completion.json)','']
    if 'ADD' not in results:
        lines[4:4]=['보존+추가는 전체 공식 판독이 끝나지 않아 표에 포함하지 않았다.', '']
    rendered='\n'.join(lines)
    for p in (ROOT/'RESULTS_KO.md',ROOT.parents[1]/'paper/REVIEWBENCH_DIVERGENT25_20261113_KO.md'):
        p.write_text(rendered)
    print('Published',ROOT/'RESULTS_KO.md')


if __name__=='__main__':main()
