import json
from pathlib import Path
R=Path(__file__).resolve().parent
read=lambda p:json.loads(p.read_text())
a=read(R/'audit.json');res=read(R/'results.json')
head='''# 새 6쌍 코드 리뷰 실험 결과 — 2026-09-29

**S_priority는 기존 S보다 정상/결함 쌍 구분을 더 많이 성공했지만, BM25보다 전반적으로 우수하다는 결과는 아니다.** 근거 있는 결함 탐지는 BM25가 5/6으로 가장 많았고, 정상/결함 모두 맞춘 쌍은 S_priority가 3/6으로 가장 많았다. 작은 통제 실험의 기술적 관찰이며 우열의 통계적 검증은 아니다.

## 결과

6개 사례마다 정상·결함 두 조건을 구성했다. 방식당 평가 대상은 12개 입력이며, 호출 실패를 분모에서 빼지 않았다. `정상 판정`은 no_supported_issue, `유보`는 insufficient_evidence다.

| 지표 | BM25 (L) | 기존 S | S_priority |
|---|---:|---:|---:|
| 실제 문맥 근거가 있는 결함 탐지 / 결함 6개 | 5/6 | 2/6 | 3/6 |
| 정상 입력 오탐 / 정상 6개 | 2/6 | 2/6 | 1/6 |
| 정상 입력에 정상 판정 / 정상 6개 | 2/6 | 1/6 | 3/6 |
| 정상·결함을 모두 정확히 구분한 쌍 / 6쌍 | 2/6 | 1/6 | 3/6 |
| 판단 유보 / 전체 12개 | 2/12 | 3/12 | 3/12 |
| controller 실패로 최종 답변 차단 / 전체 12개 | 0/12 | 4/12 | 2/12 |
| 실제 최종 답변 수 | 12 | 8 | 10 |
| 세계 구분 함수 전체가 포함된 최종 문맥 / 생성 문맥 | 9/12 | 4/8 | 8/10 |

BM25는 결함 6개 모두에서 해당 버그 메커니즘을 언급했다. 그중 정규식 사례는 실제 배포 인자를 읽지 못한 조건부 경고라서 근거 있는 탐지에는 포함하지 않았다. S/S_priority의 차단된 답변은 모델의 잘못된 판정과 별개인 운영 실패다. 같은 입력을 재시도하지 않았으므로 실패 원인을 검색 방식의 의미적 열등함으로 단정할 수 없다.

## 사례별 결과

각 셀은 **정상 입력 / 결함 입력** 순서다. `탐지`는 실제 문맥에 근거한 탐지이며 `조건부 경고`는 실제 입력을 확인하지 못한 경고다.

| 사례 | BM25 | 기존 S | S_priority |
|---|---|---|---|
| F01 디버깅 포트 | 정상 / 탐지 | 차단 / 유보 | 정상 / 탐지 |
| F02 thinking_budget | 오탐 / 탐지 | 오탐 / 차단 | 오탐 / 차단 |
| F03 빈 문서명 조회 | 유보 / 탐지 | 유보 / 차단 | 유보 / 차단 |
| F04 remove 갱신 지시자 | 유보 / 탐지 | 유보 / 차단 | 유보 / 유보 |
| F07 정규식 선택적 그룹 | 오탐 / 조건부 경고 | 오탐 / 탐지 | 정상 / 탐지 |
| F08 미생성 이미지 캐시 | 정상 / 탐지 | 정상 / 탐지 | 정상 / 탐지 |

**문맥 검색의 유효한 개선 사례는 F07이다.** BM25의 정상·결함 최종 프롬프트는 완전히 동일했다. `arguments()`를 못 찾았으므로 `ab ab`와 `ab b`를 구분할 수 없었다. S_priority는 실제 인자를 가져와 정상에서는 침묵하고, 결함에서는 선택적 그룹의 None이 join에서 TypeError를 만드는 경로를 설명했다. 기존 S는 결함 입력의 인자만 찾고 정상에서는 조건부 경고를 냈다. 다만 이 관찰은 한 family, 각 조건 한 번의 모델 실행에 한정된다.

**F02는 검색 후 판단의 문제였다.** 세 방식 모두 정상 설정 -1을 보았고 그대로 유지된다는 점까지 인정하면서, 다른 설정값을 가정해 경고했다. 문맥을 더 가져오는 것만으로는 실제 배포 범위를 벗어난 오탐을 막지 못했다.

**F04는 호출 인자만으로 부족했다.** S_priority는 문자열 remove 인자는 가져왔지만, 유지된 remove 키가 실제 DB payload까지 전달되는 후속 코드를 확보하지 못해 유보했다. BM25는 전체 update 함수와 인자를 함께 확보해 결함을 설명했다. 필요한 것은 주변 코드의 양보다 원인→분기→관측 경계가 연결된 근거다.

F03의 정상 조건은 BM25가 정상 helper를 봤음에도 요청 바인딩 근거 부족으로 유보했다. 정답을 가르는 함수의 검색 성공과 최종 판정 성공은 동일하지 않다.

## 실행·무결성·비용

모델 gpt-5.6-sol, low, 최대 4개 동시 호출, 호출당 240초, 재시도 없음. 계획 78회 중 실제 70회 호출했고 65회 성공, 5회 timeout이었다. 실패의 후속 8회 호출을 실행하지 않아 최종 리뷰는 36개 중 30개 생성됐다. 공통 controller 실패 하나가 두 방식의 최종 답변을 막으므로 timeout 5회와 차단 답변 6개는 다른 수치다.

시간 제한 발생: F03 결함 공통 controller1, F01 정상 S controller2, F02 결함 S와 S_priority controller2, F04 결함 S controller2. 실행 환경·서비스 지연과 출력 생성 시간을 분리 측정하지 않았으므로 timeout의 근본 원인은 미확정이다.

독립 감사에서 입력·정책·평가 기준·단계별 프롬프트 해시, source span, 누적 문맥 보존, schema, 구조화된 인용 ID, 호출 장부를 모두 통과했다. 문맥은 최대 1,600 reference tokens였고 관측된 도구 호출은 0이다. S_priority F07 답변 본문에 인용 ID 오타 한 곳은 별도로 기록했다. 구조화된 evidence_ids와 실제 소스 근거는 맞지만, ID 검증만으로 본문 인용 품질까지 보장할 수는 없다.

| 호출 묶음 | 시도 / 성공 | 보고된 input tokens | 보고된 output tokens |
|---|---:|---:|---:|
'''
for key,label in [('questions','공통 질문'),('shared','공통 controller1'),('L','BM25 final'),('S','S 후속 controller·final'),('S_priority','S_priority 후속 controller·final')]:
 c=a['by_arm'][key];head+=f"| {label} | {c['attempted_calls']} / {c['successful_calls']} | {c['input_tokens']:,} | {c['output_tokens']:,} |\n"
head+='''
보고된 합계는 input 707,122, output 46,216 tokens다. cached input 229,760은 input의 부분집합이고 reasoning output 12,182도 별도 가산하지 않는다. Timeout 5회는 usage가 없어 실제 총비용은 미확정이다. 개별 방법을 독립 운영하면 공통 질문 비용에 더해 S 계열 각각에 공통 controller 비용도 들어간다. Source cap이 같아도 S 계열의 전체 계산 비용은 더 크며 위 후속 묶음만으로 단독 운영 비용을 비교해서는 안 된다. 금액이나 방법별 실시간 지연 우열은 계산하지 않았다.

## 다음 설계에 반영할 판단

이번 실행의 입력·정책은 변경하지 않았다. 다음 버전에 대해서는 다음 순서를 권한다.

1. BM25 근거를 유지하면서 부족한 사실만 추가 검색하는 방식을 먼저 비교한다. 현재 S_priority 전체 대체가 더 낫다는 근거는 없다.
2. 최종 리뷰에 실제 배포 조건 충족 여부를 점검하는 단계를 넣는다. F02처럼 정상 조건을 확인하고도 가상의 다른 호출을 경고하는 문제를 겨냥한다.
3. context 선택에 호출 인자뿐 아니라 분기와 최종 관측 경계까지 함께 확보하는 기준을 둔다. F04의 불완전한 경로를 겨냥한다.
4. timeout 시 마지막 검증된 문맥으로 리뷰하는 fallback은 별도 운영 실험으로 평가한다. 이번 실패를 사후 재시도로 덮거나 현재 점수에 섞지 않는다.

이번 6쌍은 이제 개발에 노출된 자료다. 수정 정책의 일반화 검증에는 새 holdout이 필요하다. S_priority는 lexical 우선순위·신규 후보 선택·graph quota 등을 함께 바꾼 정책이므로, 결과를 단일 순서 변경의 인과 효과로 해석할 수 없다.

'''
draft=(R/'RESULTS_DRAFT_KO.md').read_text();scope=draft[draft.index('## 이번에 테스트한 것'):]
scope=scope.replace('최종 결과·호출 비용·실패 상태는 완료 후 별도 REPORT_KO.md에 기록한다.','상기 결과와 함께 해석해야 하는 동결된 방법 설명이다.')
head+=scope+'''

## 재검토 자료

- [사전 실험 계획](PLAN_KO.md), [정적 출처 감사](provenance_audit.json), [48회 재현 기록](witness_results.json), [Peewee 검증](peewee_boundary_validation.json)
- [실행·무결성 감사](audit.json), [기계 판독 결과](results.json), [이름을 가린 원응답](blind_responses.json), [작성자 판정](blind_annotations.json)
- [원시 실행 결과](experiment_v1/predictions.json), [최종 입력](experiment_v1/final_packets.json), [실행 장부](experiment_v1/execution.json), [모델 호출 전 동결](experiment_v1/assessment_freeze.json)

작성자 판정이며 독립 평가자는 없었다. 정상 쌍 구분 3/6을 실제 PR 정확도 50%라고 부를 수 없다. 미리 정한 동작 범위를 테스트한 구성요소 실험이며, 실제 서비스 통합이나 논문 benchmark 재현 결과가 아니다.
'''
(R/'REPORT_KO.md').write_text(head)
print(R/'REPORT_KO.md')
