"""Render primary and explicitly separated recovery-secondary comparison."""
import json
from pathlib import Path

R = Path(__file__).resolve().parent
REC = R.parent / 'lsp_cumulative_recovery_20261024'
read = lambda p: json.loads(p.read_text())
p, rec, subset = read(R/'scores.json'), read(REC/'scores.json'), read(R/'operational_sensitivity.json')
execution, audit = read(R/'run_summary.json'), read(R/'audit_all.json')
arms = ['B0','B1','B2','F','G']
names = {'B0':'공통 계약·DIFF만 / 2회 판단','B1':'+ 변경 파일 AST 컨텍스트',
         'B2':'+ 고정 native LSP 컨텍스트 / 선택적 초기 근거 교체',
         'F':'+ 초기 전체 근거 보존 (B3)','G':'+ 리뷰 피드백에 따른 agentic 탐색 (B4)'}
fmt = lambda x: '—' if x is None else f'{100*x:.2f}%'
lines = ['# 동일 12개 사례의 단계별 코드 리뷰 F1 비교', '',
         '12개 Python PR의 안전/결함 방향을 각각 3회 평가했다. 군당 72개 리뷰, 결함 36개·안전 36개이며, 모델은 gpt-5.6-sol / low, 리뷰당 두 번 판단이다. 기존 F/G 결과와 원본 파일은 보존했다.', '',
         '**원계획 결과**에는 초기 출력 저장 실패 6건도 남겼다. 이후 응답 내용을 전혀 관측하지 못한 여섯 위치를 미리 고정해 별도 복구 반복측정을 한 번씩 수행했다. 아래 복구 반영 결과는 이 프로토콜 변경을 공개한 보조 분석이며 원계획 결과를 덮어쓰지 않는다.', '',
         '| 단계 | 제공 정보 | 원계획 근거 충족 F1 | 복구 반영 F1¹ | 원계획 운영 실패 |',
         '|---|---|---:|---:|---:|']
for a in arms:
    failures = sum(x['arm']==a and x['status']!='ok' for x in audit['rows'])
    lines.append(f'| {a} | {names[a]} | {fmt(p["summary"][a]["grounded_F1"]["F1"])} | {fmt(rec["summary"][a]["grounded_F1"]["F1"])} | {failures} |')
lines += ['', '¹ 기존 정상 210개 궤적과 별도 복구 6개 궤적을 원래 PR·방향·반복 위치에 연결했다. 새 독립 사례로 늘리지 않았다. F/G는 과거에 완료된 144개 궤적을 그대로 참조한다.', '',
          '**복구를 반영한 보조 결과의 세부 지표**', '',
          '| 단계 | TP / FP / FN | 정밀도 | 재현율 | 근거 충족 F1 | Local-causal F1 | Raw-status F1 | 안전 코드 오경고 | 보류 |',
          '|---|---:|---:|---:|---:|---:|---:|---:|---:|']
for a in arms:
    s=rec['summary'][a]; m=s['grounded_F1']
    lines.append(f'| {a} | {m["TP"]} / {m["FP"]} / {m["FN"]} | {fmt(m["precision"])} | {fmt(m["recall"])} | {fmt(m["F1"])} | {fmt(s["local_F1"]["F1"])} | {fmt(s["status_F1"]["F1"])} | {s["safe_false_alarm_reviews"]}/36 | {s["abstained"]}/72 |')
lines += ['', '| 단계 | 근거가 충족된 안전 판정 | 안전/결함 양방향 쌍 성공 | 평균 최종 추가 소스 토큰 |',
          '|---|---:|---:|---:|']
for a in arms:
    s=rec['summary'][a]
    lines.append(f'| {a} | {s["safe_grounded"]}/36 | {s["pair_success"]}/36 | {s["mean_source_tokens"]:.1f} |')
lines += ['', 'F1은 정밀도와 재현율의 조화평균이며 2TP/(2TP+FP+FN)으로 계산했다. 주 지표는 고정 입력의 정확한 원인·필요한 저장소 연결·base/head 차이를 공급된 근거로 입증한 최종 보고만 TP로 인정한다. FP에는 실제 존재하지 않는 버그뿐 아니라 원인은 맞아도 필수 근거가 빠진 보고와 중복 보고도 포함된다. 안전 코드 오경고는 이를 분리해 보여준다.', '',
          'Local-causal F1은 올바른 국소 원인을 제시했지만 외부 타입·진입점 연결이 조건부인 보고를 추가로 인정한다. Raw-status F1은 결함 방향에서 보고했는지만 세는 참고치이며 원인을 검증하지 않는다. 실제로 보류한 응답을 정답으로 구제하지 않았다. 따라서 근거 충족 F1을 단순 버그 인지 능력과 동일시하면 안 된다. 특히 diff-only의 근거 충족 F1 0%는 결함을 전혀 인지하지 못했다는 뜻이 아니다. 더 많은 코드 연결을 제공받는 군이 이 입증 기준을 충족하기 쉬우므로 국소 원인 지표를 함께 비교해야 한다.', '',
          '**인접 단계 차이 — 복구 반영 보조 결과**', '',
          '| 비교 | 근거 충족 F1 차이 | 95% PR-cluster bootstrap 구간 | 해석 |',
          '|---|---:|---:|---|']
for k,v in rec['contrasts'].items():
    m=v['grounded_F1'];lo,hi=m['PR_cluster_percentile_95CI_pp']
    note='안전 방향 한 입력만 실제로 달라져 결함 탐지에 대한 보존 효과는 식별 불가' if k=='F_minus_B2' else ('구간에 0 포함: 방향 확정 어려움' if lo<=0<=hi else '이 개발 사례 내 관측 차이')
    lines.append(f'| {k.replace("_minus_"," − ")} | {m["delta_percentage_points"]:+.2f} pp | [{lo:+.2f}, {hi:+.2f}] pp | {note} |')
lines += ['', '각 PR의 모든 방향·반복·비교군을 함께 10,000회 재표집해 pooled counts로 F1을 다시 계산했다. B2/F의 동일 입력·동일 응답은 공유 관측이며 독립 표본이 아니다.', '',
          '**운영 실패 위치를 모든 군에서 똑같이 제외한 사후 보조 점검**', '',
          '| 단계 | 리뷰 수 | 결함 / 안전 분모 | 근거 충족 F1 | Local-causal F1 | Raw-status F1 |',
          '|---|---:|---:|---:|---:|---:|']
for a in arms:
    s=subset['summary'][a]
    lines.append(f'| {a} | {s["trajectories"]} | {s["unsafe_reviews"]} / {s["safe_reviews"]} | {fmt(s["grounded_F1"]["F1"])} | {fmt(s["local_F1"]["F1"])} | {fmt(s["status_F1"]["F1"])} |')
lines += ['', '제외 위치는 N04_a_r3, N05_a_r3, N10_a_r1, N10_a_r2다. 정상 응답의 원인 오류나 근거 부족은 이 보조 집계에서 제외하지 않았다. 원래 72개/군 분모의 주 결과는 별도로 유지된다.', '',
          '**사례별 근거 충족 탐지 수 — 복구 반영, 각 결함 방향 3회**', '',
          '| 사례 | B0 | B1 | B2 | F | G |', '|---|---:|---:|---:|---:|---:|']
for c,vals in rec['per_case'].items():
    lines.append('| '+c+' | '+' | '.join(str(vals[a]['grounded_F1']['TP'])+'/3' for a in arms)+' |')
lines += ['', '관측 차이를 설명하는 대표 사례는 N09와 N02다. N09의 문자열 분모 반복 오류는 AST-only에서 세 최종 응답이 모두 보류했지만 고정 LSP의 F는 세 번 모두 보고했다. F에는 Number/Integer 생성자 근거가 추가되어 있다. 한 AST-only 응답은 첫 판단에서 올바르게 보고한 뒤 같은 소스를 받는 최종 판단에서 철회했다. 이는 단순히 정답 설명이 한 번 나왔는지가 아니라 최종 판단까지 유지되는지도 중요함을 보여준다.', '',
          'N02의 CharField deepcopy 오류는 F의 0/3에서 G의 3/3으로 바뀌었다. 다만 G는 N05와 N09에서 각각 F보다 한 번씩 탐지를 놓쳤다. 따라서 agentic 탐색의 추가 발견만 세어 전체 향상으로 표현하지 않고, 손실도 포함한 전체 F1을 비교했다. 이 사례 설명 역시 과거 F/G와 새 B1의 순차 실행이라는 한계를 가진다.']
lines += ['', '**실행과 보존**', '',
          f'원실험은 B0~B2의 216개 논리 궤적·432개 단계로 구성된다. 141개 단계는 완전히 동일한 F prompt·schema·모델 설정 및 원본 해시를 확인해 재사용했다. 신규 호출 진입은 {execution["fresh_CLI_invocations"]}회, 저장된 정상 응답은 {execution["fresh_model_statuses"].get("ok",0)}개다. 초기 6회는 raw 출력 폴더 누락으로 trace와 응답을 저장하지 못했으며 API 성공 여부·사용량·내부 재시도는 확인할 수 없다. 이 중 첫 판단이 소실된 B0/B1 네 궤적의 후속 단계는 원실험에서 실행하지 않았다.', '',
          f'별도 복구는 {rec["recovery_execution"]["fresh_CLI_invocations"]}회 신규 호출과 기존 B2 첫 응답 2개 참조로 구성된다. 최대 2개 모델 worker를 사용했고, 현재 원실험과 복구 실행을 겹치지 않았다. 기존 성공 응답을 보고 복구 대상을 고르거나 좋은 응답을 선택하지 않았다. 이번 보충·복구의 실제 호출 진입은 합계 297회(원실험 287 + 복구 10), 보존된 신규 응답은 291개이며 유실 6회를 비용 집계에서 제외하지 않는다.', '',
          '복구에서 추가된 AST 탐지는 N05 한 건이다. 같은 AST 소스로 첫 응답은 정밀도를 잘못 계산해 보류했지만, 자기 첫 응답을 받은 최종 판단은 units=-1, prec=0 및 Context 예외를 정확히 보고했다. 이는 추가 검색으로 얻은 개선이 아니라 동일 근거에서의 추론 수정이다.', '',
          'B0는 추가 source/plain이 없고, B1은 변경 파일 AST만 사용했다. B2는 F의 실제 native 조회·응답·후보 순서를 고정하여 재생했으며 이번 실행에서 native 조회는 0회다. B2의 최종 입력은 24개 코드 상태 중 23개에서 F와 같다. 안전 N10_a에서만 선택적 Polygon 함수 근거 203줄을 제거하고 62줄·클래스 목록 4개를 추가했다. 변경/삭제 함수 근거는 보존됐다. 따라서 F−B2가 같더라도 보존 기능이 효과 없다는 결론을 낼 수 없다.', '',
          'B0→B1은 변경 파일의 구조적 컨텍스트 추가 효과다. B1→B2에는 LSP 탐색뿐 아니라 더 넓은 코드 범위와 단계별 근거 제공이 함께 포함된다. F/G는 기존 실행이므로 이번 B0/B1과 동시에 무작위 배정된 5군 실험이 아니다. 모델 실행 시점과 확률적 변동을 완전히 통제하지 못했다. 동일한 12개 Python 개발 사례를 반복 사용했고 비블라인드 AI 채점이므로 미관측 사례·다른 언어·운영 MR 전체로 일반화할 수 없다.', '',
          f'[원계획 및 기준]({R/"PROTOCOL_KO.md"}) · [원계획 점수/사례별 기록]({R/"scores.json"}) · [감사 결과]({R/"audit_all.json"}) · [실행 분석]({R/"execution_analysis.json"})', '',
          f'[복구 프로토콜]({REC/"PROTOCOL_KO.md"}) · [복구 반영 보조 점수]({REC/"scores.json"}) · [공통 성공 위치 보조 분석]({R/"operational_sensitivity.json"})', '']
(R/'REPORT_KO.md').write_text('\n'.join(lines))
print(R/'REPORT_KO.md')
