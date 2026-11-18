"""Render complete results only; findings are explicitly reviewed separately."""
import json,statistics
from pathlib import Path
R=Path(__file__).resolve().parent
read=lambda p:json.loads(p.read_text())
s=read(R/'scores.json');a=read(R/'audit_all.json');nav=read(R/'retrieval_analysis.json');run=read(R/'run_summary.json');prep=read(R/'preparation_summary.json');findings=read(R/'report_findings.json')
assert len(s['rows'])==144 and a['checks']['complete_trajectories']
assert all(a['checks'][k] for k in ['algorithm_hashes','freeze_hashes','workspace_hashes','packet_and_native_integrity'])
fmt=lambda v:'정의되지 않음' if v is None else f'{v*100:.1f}%'
c=s['contrasts']['grounded_F1'];out=['# 컨텍스트 누락 보완과 동일 사례 비교 결과','','2026-10-23. 같은 Python PR 12개, 원본/수정본 2방향, 2구성, 3회 반복. 144개 리뷰를 새로 생성했다.','',
f"근거 충분 지적 F1은 기존 C0 **{fmt(s['summary']['C0']['grounded_F1']['F1'])}**, 개선 C1 **{fmt(s['summary']['C1']['grounded_F1']['F1'])}**다. 차이는 **{c['delta_percentage_points']:+.1f}%p**, PR cluster bootstrap 95% 구간은 {c['PR_cluster_percentile_95CI_pp'][0]:+.1f}~{c['PR_cluster_percentile_95CI_pp'][1]:+.1f}%p다.", '', findings['interpretation'], '',
'## F1과 지적의 근거', '', '| 구성 | TP | FP | FN | Precision | Recall | F1 |', '|---|---:|---:|---:|---:|---:|---:|']
for arm,name in [('C0','기존 컨텍스트'),('C1','누락 보완')]:
    q=s['summary'][arm]['grounded_F1'];out.append(f"| {arm} {name} | {q['TP']} | {q['FP']} | {q['FN']} | {fmt(q['precision'])} | {fmt(q['recall'])} | {fmt(q['F1'])} |")
out+=['','F1은 정밀도와 재현율의 조화평균이며 `2TP/(2TP+FP+FN)`으로 계산했다. 결함 리뷰마다 정답 원인 1개와 지적을 1:1 매칭한다. TP는 제공된 코드로 필요한 원인·연결을 입증한 지적, FP는 매칭되지 않은 최종 지적, FN은 찾지 못한 실제 결함이다. 결함본의 보류와 실패도 FN이다. 분모 36은 독립 PR 36개가 아닌 12 PR×3회 반복이다.','',
'FP에는 정상본 오탐뿐 아니라 잘못된 원인 설명, 필수 근거 부족, 중복 지적도 포함된다. **근거 부족으로 매칭하지 못한 지적을 실제로 없는 버그라고 단정하지 않는다.** 정상본에 대한 오탐은 아래에 따로 보였다.','',
'| 구성 | 최종 지적 수 | 정상본 오탐 지적 | 결함본 미매칭 지적 | 근거 충분 정상 /36 | 보류 /72 | 양방향 성공 /36 |','|---|---:|---:|---:|---:|---:|---:|']
for arm in ['C0','C1']:
    q=s['summary'][arm];out.append(f"| {arm} | {q['reported_issues']} | {q['safe_false_alarm_issues']} | {q['unsafe_unmatched_issues']} | {q['safe_grounded']} | {q['abstained']} | {q['pair_success']} |")
out+=['','## 채점 기준에 따른 보조 결과','','| 구성 | 조건부 로컬 TP / FP / FN | 조건부 로컬 F1 | 단순 응답 TP / FP / FN | 단순 응답 F1 |','|---|---:|---:|---:|---:|']
for arm in ['C0','C1']:
    q=s['summary'][arm];l=q['local_F1'];b=q['status_F1'];out.append(f"| {arm} | {l['TP']} / {l['FP']} / {l['FN']} | {fmt(l['F1'])} | {b['TP']} / {b['FP']} / {b['FN']} | {fmt(b['F1'])} |")
out+=['','조건부 로컬 인과 F1은 올바른 국소 원인을 설명했지만 필수 외부 연결이 부족한 지적도 별도로 인정하는 민감도 분석이다. 잘못된 예외 위치나 계산은 인정하지 않는다. 단순 응답 상태 F1은 결함본에서 지적했는지만 보는 이진 분류 참고치로, 원인 설명의 정확성을 보증하지 않는다. 세 지표를 혼용하지 않는다. 모든 TP/FP/FN은 `scores.json`에 저장했다.','',
'## 실제로 달라진 부분','']
out += ['- '+x for x in findings['observations']]
out+=['','## 컨텍스트와 비용','',
f"C1은 24개 코드 상태 중 {nav['summary']['source_changed']}개에서 소스가 달라졌다. 평균 최종 소스는 C0 {nav['summary']['mean_old_source_tokens']:.0f}, C1 {nav['summary']['mean_new_source_tokens']:.0f} reference tokens다. C1에 추가한 numbered source는 합계 {nav['summary']['added_numbered_lines']}줄이고, 기존에 있던 {nav['summary']['removed_numbered_lines']}줄은 선택 순서·예산 변화로 빠졌다. 클래스 metadata도 5200 소스 토큰 상한에 포함했다.", '',
f"실제 신규 native 조회는 C1 준비 단계에서 **{a['actual_new_native_requests']}회** 수행했다. C0는 이전 동결 packet을 재사용했으므로 새 native 조회는 0회다. 준비 결과는 반복마다 재사용했지만, 양쪽 모델 응답은 모두 새로 생성했다. 준비 시간을 두 구성의 동등한 end-to-end 비용 비교로 해석하지 않는다.", '',
'## 병렬 실행과 검증','',
f"- 실제 모델 호출 {sum(x['physical_calls'] for x in s['rows'])}회, 완료 리뷰 144개, 상태 `{json.dumps(run['status_counts'],ensure_ascii=False)}`.",
f"- 모델 동시성 최대 **{run['maximum_simultaneous_model_calls']}개**, 모델 구간 **{run['elapsed_seconds']/60:.1f}분**, 평균 **{run['calls_per_minute']:.1f}호출/분**. 입력 준비 후 JSON packet만 읽어 호출별 AST 역직렬화를 제거했다.",
f"- 코드 준비는 동시2개. 준비 시간은 **{prep['preparation_wall_seconds']/60:.2f}분**이며 실제 시각은 `preparation_runtime/`에 보존했다. 순환 참조를 준비 작업2개마다 정리했다.",
'- 이전 실행은 구성 수와 GC 병목이 달랐다. 이번 전체 시간이 짧아졌더라도 동시성을 4→8로 바꾼 효과만 분리한 속도 실험은 아니다.',
'- 같은 모델 gpt-5.6-sol low, 리뷰2회, 실제 LSP 최대12회·깊이3, 소스5200·관측800 상한. C0 packet과 prompt template이 이전 P0Q0와 동일함을 확인했다.',
'- 실제 Pyright fixture, 클래스·조건부 import·예산 경계 검증과 별도 AI 에이전트 코드 검토를 수행한 뒤 정책을 동결했다. 결과를 보고 선택 정책을 수정하지 않았다.',
f"- 원본/공식 수정본 계약은 이전 48회 재현 및 N05 입력별 추가 검증을 재사용했다. 코드 hash와 입력·응답·도구·native trace 검사 결과: `{json.dumps(a['checks'],ensure_ascii=False)}`.", '',
'- 첫 응답 2건에서 evidence ID 오타가 있었다. 최종 응답에서는 모두 수정됐으며 해당 두 리뷰는 최종 보류다. 최종 지적을 채점하는 F1에는 영향이 없다. `citation_diagnostics.json`에 원문 위치를 보존했다.', '',
'## 사례별 근거 충분 탐지','','| 사례 | C0 /3 | C1 /3 | C0 F1 | C1 F1 |','|---|---:|---:|---:|---:|']
for cid,rr in s['per_case'].items():
    c0=rr['C0']['grounded_F1'];c1=rr['C1']['grounded_F1'];out.append(f"| {cid} | {c0['TP']} | {c1['TP']} | {fmt(c0['F1'])} | {fmt(c1['F1'])} |")
out+=['','## 다음 우선순위','']+['- '+x for x in findings['next_priorities']]
out+=['','## 해석의 범위','','실험을 작성한 AI와 별도 AI 검토자가 응답·실제 공급 근거를 읽고 비블라인드로 채점했다. 독립 전문가 평가가 아니다. 같은 12 PR의 이전 누락 분석을 참고해 정책을 설계했으므로 **개발 사례의 개선 확인**이며 미사용 PR·저장소·다른 언어로 일반화하지 않는다. 공개 계약과 고정 입력에 대한 제한된 리뷰이며 실제 제품의 전체 MR 리뷰나 현장의 Python 강점 피드백을 직접 재현하지 않았다.','',
'추가된 근거가 이미 있던 의심을 입증한 경우와 처음 못 찾던 결함을 찾게 한 경우를 구분했다. 모델 응답은 독립 생성됐으므로 동일 소스에서 생긴 차이는 추론·표본 변동을 배제할 수 없다. 후보 철회 프롬프트, 전체 삭제 후 fallback, AST 실행 개요, RL은 이번 변경에 넣지 않았다.','',
'자료: `PROTOCOL_KO.md`, `algorithm_seal.json`, `freeze.json`, `raw/`, `inputs/`, `packets/`, `author_annotations.json`, `scores.json`, `audit_all.json`, `context_coverage_review.json`, `result_seal.json`.','']
(R/'REPORT_KO.md').write_text('\n'.join(out));print(R/'REPORT_KO.md')
