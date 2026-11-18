# 원인 주장별 검증·반박·재탐색 실험 — V1 완료

**실제 실행 루프를 구현했지만, 이번 개발 사례에서는 추가 성능을 입증하지 못했다.** 기존 본문으로 한 번 리뷰한 A의 원인 내용 F1은 80.0%, 추가 검토 B는 66.7%, 질문 기반 LSP·AST 조회 C는 70.3%, BASE/HEAD SCIP 후보까지 추가한 D는 66.7%다. C/D의 새로운 정확한 표적 탐지는 0건이며, 잠정 오원인 후보를 정확한 최종 결함 설명으로 교정한 성공은 조건마다 0/9다. 본문 확보 증가나 ledger 상태 변경만으로 method가 효과적이었다고 결론 내리지 않는다.

이 문서는 **V1 56개 공통 초기 응답·224개 최종 리뷰 결과**만 집계한다. 다중 파일 입력 직렬화 보충 실험과 역사적 Django 오답 재생은 별도 디렉터리·보고서이며 이 표에 섞지 않는다. 모든 판독은 실제 소스와 응답을 읽은 AI 직접 판독이다. 인간 판독은 0건, 별도 judge API 호출은 0회다. 구현·자료 준비·AI 판독 자체의 추론 비용은 reviewer 사용량에 포함하지 않았으며 0으로 주장하지 않는다.

## 보존·동결·실행 증거

- 이전 위치 실험은 576개 실행·판독을 마치고 원형 보존했다. [이전 보고서](LOCATION_BINDING_F1_20261103_KO.md)와 이전 완료 봉인을 변경하지 않았다. 이번 후속 변경을 소급하지 않았다.
- 사전 명세, 판독 기준, 코드·prompt·schema·source manifest·예산 등 **225,229개 파일**을 모델 실행 전에 freeze.json으로 동결했다. 최종 코드 기준 테스트 95개 통과, 실제 실행 전 재검증을 보존했다.
- 실행 후 동결 검증 225,229개와 이전 자료 보존 검사 72,540개 통과. 최종 전체 일관성 검사는 56 initial·224 final·1,662개 판독 입력 SHA·349개 실제 provider 장부·F1 산술을 대조했다.
- 완료 지표 JSON, 물리·논리 비용, 기전 교차표, 대표 실패 원문·소스 SHA를 함께 제공한다. 원시 입력·응답·native 질의·거부·미발급·후보 변경은 실행 산출물에 있다.

## 추가된 실제 호출 경로

```text
공개 입력·diff + A/B/C/D 공통 초기 본문
  → 새 초기 후보·발생 조건·typed questions 생성 (A의 최종 리뷰)
  → C/D: 질문별 실제 native/AST 조회 → 본문·관계·실패 수집
  → verify 모델: supported / refuted / unresolved 의미 판정
  → controller: ID·snapshot·kind·완전 본문·변경 이유 검증
  → 실제 oar_core.Ledger.apply_update, 상태·반박·거부를 다음 입력에 전달
  → 수정된 질문으로 두 번째 조회 (D는 SCIP 후보로 우선순위 보조)
  → 최종 reviewer → 세 위치 역할 결속 → 별도 비공개 근거 판독
```

[실제 오케스트레이터](../runs/causal_claim_loop_20261103/experiment.py)의 `perform_queries`가 backend.query를 호출하며, [controller](../runs/causal_claim_loop_20261103/claim_controller.py)가 실제 Ledger.apply_update를 실행한다. 후보·조건·challenge·변경 이유·근거 ID·상태 해시·종료 이유를 보존한다. 기존 core가 과거 제품 실행에서 이미 사용됐는지도 기존 호출 경로 감사로 확인했다. core나 obligation/challenge 자체를 이번에 처음 발명했다고 주장하지 않는다.

질문이나 후보가 비어 있어도 변경된 호출·반환·대입·조건에서 AST seed 조회를 시작한다. B는 조회 없이 A와 동일한 본문으로 추가 검토 2회를 한다. C/D에는 최대 조회 2라운드와 verify·final 호출이 있다. 이 유한 일정 때문에 마지막에 정의를 찾고도 다음 본문을 읽을 기회가 없어지는 실패가 실제 발생했다. 실제 실행 테스트는 backend 두 라운드·core 업데이트·빈 후보 seed·거부 feedback 전달·중간 응답의 final 승격 금지를 확인한다. 테스트가 의미 정확도를 보장하지는 않는다.

조건이 `implementation_body`나 `exception_branch`이면 정의 헤더만으로 해결 처리하지 않는다. Controller의 `complete_unit` 검사는 공급한 범위의 완전성만 검사한다. 잘못된 동명 구현이나 fallback 본문도 완전할 수 있으므로 실제 callee·의미·경로 정답은 별도로 판독한다. 실패한 actual final의 원시 경고는 남기고, 실패한 verify의 경고를 final로 바꾸지 않는다.

최종 finding은 `regression_anchor`(HEAD 검토 위치), `failure_operation`(실패 또는 오값 생성 연산), `supporting_evidence`(생성·전달·분기)를 구분한다. 변경 파일 밖 실패 연산을 허용하고, 없는 위치는 unresolved로 기록한다. Controller가 인용 범위를 결속한다는 사실은 원인 정답이나 인용의 의미적 관련성을 보장하지 않는다.

## 자료·정보 경계

개발은 기존 12개 PR/과제의 24방향×2반복이다. Python·Go 각 6과제이며 공개된 구체적 계약과 과거 수집 본문이 있다. 언어별로 조건당 24회, 양성 표적 12회다. **개발·기전 확인용이며 일반화 근거가 아니다.** A도 diff-only가 아니라 기존 수집 본문을 받은 대조군이다. 과거 diff-only·AST 단계 실험과 이번 A를 같은 baseline으로 부르지 않는다.

미사용 PR은 결과를 보기 전 메타데이터와 로컬 과거 입력 이력으로 선정했다. 최초 후보 4개 중 실제 PR의 도입 결함을 검증할 수 없는 자료 1개와 변경 구현 언어가 Go가 아닌 자료 1개를 모델 실행 전에 제외하고, Langflow #5388과 LocalAI #4577의 실제 merge-base/HEAD 및 역방향을 사용했다. 선정 이력, 과거 실제 입력·패치 노출 대조를 보존했다. 로컬 모델 입력 비노출은 사전학습 비노출 보장이 아니다.

사전 source 타당성 기록에 제외 이유를 보존했다. vLLM #12608은 실제 최종 PR에서 지목한 `_untouch` 구현 변경이 없어 도입 결함을 검증할 수 없었고, TypeScript-Go #1446은 변경 구현이 `.mts`였다.

미사용 자료는 결함 힌트나 계약을 추가하지 않은 `no_extra_hint`다. 개발 계약 조건과 합쳐 언어 효과로 해석하지 않는다. Langflow 두 방향은 정해진 표적상 음성이라 Python holdout 재현율/F1은 NA다. 전체 PR 무결함을 뜻하지 않는다. 표적 외 실제 결함은 독립 소스 확인 없이는 false나 confirmed defect로 만들지 않는다. private truth·이전 채점·수정 후 설명은 reviewer 입력이 아니다.

## 조건별 결과

‘원인 내용’은 실제 최종 finding의 trigger→실패 동작→영향이 맞는지를 본다. ‘승인 포함’은 그 정답에 anchor·failure operation·인용 기계 유효성·의미 관련성·status 승인까지 요구한다. 조건명 약어는 다음 기능을 뜻한다.

| 조건 | 실제 기능 |
|---|---|
| A | 기존 수집 본문 + 최종 리뷰 1회 |
| B | A와 동일한 본문 + verify·final 2회 추가 검토 |
| C | 원인 질문에 따른 실제 LSP·AST 조회 + 검증·재탐색 |
| D | C + BASE/HEAD SCIP 후보·관계 metadata·질문 우선순위 |

**개발 전체: 12과제, 조건당 48회·양성 24회.**

| 조건 | 원인 내용 TP/양성 | 원인 내용 F1 | 위치·인용·실행 승인 포함 TP | 승인 포함 F1 | 내용 재현율 | 승인 포함 재현율 | 확인된 실제 오탐 | 출력 실패/전체 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| A | 16/24 | 80.0% | 12 | 60.0% | 66.7% | 50.0% | 0 | 10/48 |
| B | 12/24 | 66.7% | 8 | 44.4% | 50.0% | 33.3% | 0 | 3/48 |
| C | 13/24 | 70.3% | 9 | 48.6% | 54.2% | 37.5% | 0 | 8/48 |
| D | 12/24 | 66.7% | 10 | 55.6% | 50.0% | 41.7% | 0 | 13/48 |

**개발 Python: 조건당 24회·양성 12회.**

| 조건 | 원인 내용 TP/양성 | 원인 내용 F1 | 위치·인용·실행 승인 포함 TP | 승인 포함 F1 | 내용 재현율 | 승인 포함 재현율 | 확인된 실제 오탐 | 출력 실패/전체 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| A | 8/12 | 80.0% | 5 | 50.0% | 66.7% | 41.7% | 0 | 7/24 |
| B | 5/12 | 58.8% | 2 | 23.5% | 41.7% | 16.7% | 0 | 1/24 |
| C | 6/12 | 66.7% | 3 | 33.3% | 50.0% | 25.0% | 0 | 6/24 |
| D | 5/12 | 58.8% | 3 | 35.3% | 41.7% | 25.0% | 0 | 9/24 |

**개발 Go: 조건당 24회·양성 12회.**

| 조건 | 원인 내용 TP/양성 | 원인 내용 F1 | 위치·인용·실행 승인 포함 TP | 승인 포함 F1 | 내용 재현율 | 승인 포함 재현율 | 확인된 실제 오탐 | 출력 실패/전체 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| A | 8/12 | 80.0% | 7 | 70.0% | 66.7% | 58.3% | 0 | 3/24 |
| B | 7/12 | 73.7% | 6 | 63.2% | 58.3% | 50.0% | 0 | 2/24 |
| C | 7/12 | 73.7% | 6 | 63.2% | 58.3% | 50.0% | 0 | 2/24 |
| D | 7/12 | 73.7% | 7 | 73.7% | 58.3% | 58.3% | 0 | 4/24 |

표적 F1은 `2TP / (실제 최종 reported findings + 양성 표적 수)`다. 확인된 실제 오탐과 최종 finding의 반박된 API 원자 주장은 모두 0이다. 이것은 recall 손실·보류·실패를 포함한 결과이며 안전성 개선의 증거로 단독 해석하지 않는다. 표적 F1의 `off_target_reports`에는 원인 내용은 맞지만 위치·인용·실행 승인이 없는 보고도 포함된다. 이 수를 실제 오탐이라고 부르지 않는다. 최종 내용이 맞아도 controller가 거부한 finding은 내용 정답 진단을 남기고 승인 포함 TP는 0이다.

개발 전체의 C−B 원인 내용 F1 차이는 +3.6%p이지만 A보다 낮고, 새로운 정확한 결함을 추가로 찾은 결과가 아니다. D−C는 승인 포함 F1 +6.9%p, 내용 F1 −3.6%p다. D의 위치·승인 손실 감소를 새 원인 발견으로 포장하지 않는다. PR 단위 대응 비교·10,000회 cluster bootstrap·반복별/사례별 결과는 지표 JSON에 있다. 방향과 반복을 독립 PR로 부풀리지 않는다.

**미사용 PR V1은 충분한 일반화 평가가 되지 못했다.** Langflow는 경고 0, 실제 오탐 0이지만 양성 0이라 recall/F1 NA다. LocalAI는 전체 diff를 파일별 evidence마다 반복한 직렬화 문제로 초기 4회 모두 admission 단계에서 미발급이었다. 실제 provider 호출 0이며 유효 TP 0, 양성 recall/F1 0이다. 이 실패를 데이터에서 제거하지 않았고, 수정은 별도 보충 실험에서 새 초기 응답으로 진행했다. 단 하나의 Python 음성 PR과 실행되지 못한 Go PR로 언어·저장소 일반화를 주장하지 않는다.

## 교정·정답 상실·기전

공통 initial 56개는 정답 16·finding 없음 36·출력/입력 미해결 4다. 잘못된 원인이 실제 initial finding에 보고된 관측은 **0개**이므로 보고된 오원인→정답 교정률은 0%가 아니라 **NA(분모0)**다. 잠정 후보까지 별도 읽었을 때, 교정 대상인 잘못된 표적 후보가 있는 개발 리뷰는 조건마다 9개, 그 교정 성공은 **0/9**다. 이미 반박·철회한 후보는 교정 분모에서 제외했다. 이 9개가 모두 실제 결함을 놓친 양성은 아니다.

| 개발 기전 지표 | A | B | C | D |
|---|---:|---:|---:|---:|
| 초기 정답 16개 중 최종 정확한 경고 유지 | 16 | 12 | 13 | 12 |
| 초기 정답에서 경고 소실 | 0 | 4 | 3 | 4 |
| 초기 정답에서 실제 틀린 원인 단정으로 전환 | 0/16 | 0/16 | 0/16 | 0/16 |
| 잠정 잘못된 표적 후보→정확한 최종 결함 | 0/9 | 0/9 | 0/9 | 0/9 |
| 필요한 본문 확보 / 48 | 17 | 17 | 20 | 20 |
| 반박 필요 리뷰 / 48 | 10 | 10 | 10 | 10 |
| 올바르게 결속된 반박 근거 확보 / 필요10 | 2 | 2 | 3 | 3 |
| 모델이 제시된 반박을 실제 사용 / 필요10 | 0 | 2 | 4 | 3 |
| 올바른 반박 확보 ∩ 사용 | 0 | 2 | 2 | 1 |
| 확보 ∩ 사용 ∩ 새로운 정확한 최종 결함 | 0 | 0 | 0 | 0 |

반박 필요 10개는 음성 9·양성 1로 구성된다. 무경고나 보류를 양성 원인 교정으로 올리지 않았다. C/D에서 사용 건수가 올바른 확보 건수보다 클 수 있는 이유는 모델이 잘못 결속된 Python `partial` fallback도 반박으로 사용했기 때문이다. 원본 노트는 보존하고 SHA 연결된 adjudication overlay를 적용했다. 본문을 얻음·유효한 반박을 얻음·모델이 반영함·최종 원인이 맞음은 서로 다른 사건이다.

현재 Django 강한 초기 문맥에서는 최종 내용이 이미 맞은 응답이 있어, 이것을 과거의 wrong-wraps 설명 교정 성공으로 세지 않는다. 과거 실제 오답을 시작점으로 하는 후속 재생 실험은 개발용 기전 사례로 분리한다. public callback 설명은 repository 함수 본문이나 runtime 관측으로 승격하지 않는다.

## 도구·외부 구현의 실제 보장 범위

- Python Pyright와 Go gopls의 실제 capability/RPC를 사용하고 미지원·timeout·복수 대상·동적 연결을 unresolved로 보존했다. definition/reference는 후보이며 구현 본문이 아니다.
- AST는 참조를 호출·인자·반환·속성 등으로 구분하고 단순 alias·인자→매개변수·반환→대입·closure capture 후보를 지원한다. 함수 내부 def-use와 함수 경계 후보를 구분하며, 완전한 SSA·실행 dataflow나 동적 dispatch 증명을 구현했다고 주장하지 않는다.
- BASE/HEAD AST delta와 실제 SCIP index를 함께 사용했다. 참조 relation을 call edge로 재명명하지 않았다. 선택된 56 snapshot 중 34개 available·22개 failed다. 개발 Python의 12 HEAD index는 모두 실패했고, 개발 Go는 12방향 중 10개만 양쪽이 available이다. D는 완전한 paired-index 조건이 아니다.
- C/D의 허용 source pool은 동일하다. 다만 index가 노출하는 후보·사실·순서와 실패율은 달라, D−C를 순수 batch index 속도 효과로 부를 수 없다. 같은 fact mirror 점검에서 Python 0/3, Go 동일 위치 2/3, 실제 구현 본문 제공 0/3이어서 모든 LSP/SCIP 사실을 동등화한 비교도 성립하지 않았다. 추가 source universe 확대는 없지만 후보 가용성 효과는 남는다.
- interpreter·lockfile·workspace 설정과 origin/version/hash·import 연결 근거를 저장했다. 외부 Python 3.11.13 stdlib와 Go 분석 profile 중 PR 배포 환경을 확정하지 못한 연결은 assumption/unresolved다. 호스트의 genuine source라는 사실만으로 PR 실제 구현이라고 하지 않는다.
- 이 V1의 runtime probe는 **0회**다. 정적 소스 읽기·모델 판정은 실행 관측이 아니다. 관측 추가 조건은 별도 동결·개발 기전 실험으로 진행한다.

## 어디서 막혔는가

다음은 실제 원문·질의·소스 SHA로 확인한 대표 사례다. 전체 실패의 빈도를 자동 분류한 표가 아니다.

| 사전 분류 | 실제 관측 |
|---|---|
| 연결 후보 누락 | GO02_b_D_r1: native reference 32곳 중 constructor도 있었지만 앞 8개만 source 후보로 편입. constructor 본문·값 전달은 확보되지 않음. |
| 잘못된 구현 바인딩 | PY_N03_b C/D: `functools.py` Python fallback class를 읽었으나 바로 뒤 `_functools.partial` export override가 빠짐. 실제 imported 구현을 증명하지 못했는데 모델은 반박으로 사용. |
| 본문 누락 | GO03_a_C_r1: `rootOpenDir` body 요청이 enclosing `doInRoot` 본문으로 돌아왔고 다음 definition timeout. 이미 주어진 본문만 재획득. Python 필드 상수·Array alias 대입도 function-only 조회가 읽지 못함. |
| 본문 오독 | 올바르게 결속되어 실제 전달된 본문을 오독한 독립 확정 대표는 이번 inventory에서 미분류. 누락이나 잘못된 binding을 오독으로 바꾸어 부르지 않음. |
| 반박 미반영 | 올바른 실제 반박을 확보했는데 무시한 순수 대표는 미분류. 잘못된 fallback 사용이나 final 미발급을 이 범주로 대신하지 않음. |
| 다음 소비 경로 누락 | GO03_b_D_r2: 마지막 조회에 createLink·Root.OpenFile 정의를 찾았지만 final도 본문을 다시 요청. 정의→본문을 닫을 후속 라운드 부족. GO01은 Userinfo.String 대신 URL.String을 받기도 함. |
| 위치·인용 오류 | 의미 정답인데 잘못된 source ID/범위나 unresolved failure slot, 잘못된 조건-kind로 core가 거부. 원인 정답·위치 관련성·기계 validity·pipeline status를 각각 남김. |

별도 운영 병목은 두 가지다. LocalAI multi-file diff가 31번 반복되어 canonical 입력 약20만 reference tokens가 된 직렬화 오류, 그리고 environment/flow/state metadata 팽창으로 본문 cap 안에서도 final admission이 막힌 경우다. 수집 뒤 final에 전달되지 않은 본문은 확보·사용 성공과 구분한다. 추가 native 조회나 자기반성 호출이 자동으로 원인 교정이 되지 않았다.

## 동일 상한과 실제 비용

모델 `gpt-5.6-sol`, reasoning low, 반복2, 최대3 calls·provider input+output 120,000/리뷰, source union 12,000 reference tokens, native48/180초, 파일256, query20초, active wall900초, SCIP pair120초 및 staged/index 256MB다. model workers4·시작 간격3초로 실행했다. A는 정의상 1회이며 B/C/D는 같은 추가 호출 상한을 사용한다. 회수·본문량·실제 토큰은 같지 않다.

이 상한은 controller의 다음 호출 admission + CLI tracking이다. 검증된 서버 hard output cap은 아니다. 알려진 실제 model overrun·발급 후 usage 미상은 이번 실행에서 0이었고, 미발급은 물리 비용0으로 증거를 남겼다. 미상 발급 비용을 0으로 채우는 규칙은 사용하지 않는다.

| 개발 전체 논리 비용 | A | B | C | D |
|---|---:|---:|---:|---:|
| input+output tokens | 1,227,101 | 3,968,200 | 4,162,815 | 4,275,491 |
| 호출(공유 initial을 조건마다 포함) | 48 | 143 | 138 | 136 |
| 추가 native 시간(초) | 0 | 0 | 107.61 | 96.57 |
| 추가 명시적 file reads | 0 | 0 | 669 | 516 |

전체 V1 **물리 호출349회·재시도0·provider input+output 10,548,035 tokens**다. cached input 2,912,000은 input의 부분집합, reasoning output 89,335는 output의 부분집합이므로 더하지 않는다. 조건별 논리 비용의 공통 initial은 물리 전체에서 한 번만 센다.

SCIP 준비는 별도로 native index/export 118회·1,983.17초·index bytes 725,704,620·staged bytes 1,977,609,798이다. 실패한 빌드 비용도 포함했다. 새 offline recovery의 native 재색인은 0회이며 추가 CPU/read 비용은 장부에 별도 있다. 초기 개발 본문 수집은 과거 query/native/read 비용을 보존하여 상속했고 이번에 다시 수집한 것처럼 계산하지 않았다. 미사용 초기 수집은 별도 AST/read 장부다. Python `native_calls`와 Go RPC+AST 단위가 달라 그 수만으로 언어별 효율을 비교하지 않는다. native 내부 숨은 I/O·실제 금전 청구액·자료 fetch/설치/사전 감사/AI 판독 비용은 전부 계측했다고 주장하지 않는다.

각 리뷰의 실제 active wall 초, native/RPC/AST/read 세부·index 상태·입력 미발급 receipt는 비용 JSON에 있다. 프로세스 시간 합 6,602.16초는 병렬 실행의 전체 경과시간과 다르다. 추가 호출이나 source 증가를 탐색 정책의 순수 효과로 해석하지 않는다.

## 논문 기여 후보의 현재 경계

CuraLoc은 source retrieval 전에 symbol과 탐색 행동을 계획하고, 제한된 이웃 source로 최종 edit-symbol 순위를 매긴다. 주 설정에서는 iterative hypothesis validation이 비활성이다. 따라서 계획·관계 탐색·짧은 symbol ID 자체는 이번 기여라고 주장하지 않는다. [CuraLoc v2 §3·Appendix A](https://arxiv.org/html/2610.31809v2)

ARISE는 함수 내부 statement def-use slicing을 agent 도구로 제공하며, slice가 call 경계를 넘지 않는 한계를 명시한다. AST 세분화·def-use·agent 도구 자체도 이번 신규성 근거가 아니다. 이번 단순 경계 연결을 완전한 interprocedural slicing이라고 부르지도 않는다. [ARISE v2 §3·§6](https://arxiv.org/html/2605.03117v2)

현재 실제로 추가 구현·검증한 후보는 **원인 주장별 typed 질문→근거 판정→후보 변경·거부 feedback→재질의의 실행 장부**, **본문/반박/반영/최종 정답을 분리한 기전 평가**, **BASE/HEAD 회귀 anchor와 실제 실패 연산을 나눈 보고 평가**다. 외부 구현 바인딩과 이후 소비 경로를 닫아 오원인을 줄이는 방법은 유망한 목표지만, V1에서는 후보 교정 성공0이고 구현 결속 누락도 발견됐다. 이 목표를 검증된 성능 기여로 이미 달성했다고 쓰지 않는다. 두 논문을 실제 재구현한 동일 benchmark 대조군도 이번에는 없으므로 우월성이나 전체 문헌 신규성을 주장하지 않는다.

다음 우선순위는 사용 지점→최종 export/alias/rebinding→정확한 구현 본문·값 선언을 닫는 조회, 관련 없는 metadata 중복 제거와 final 입력 예산 확보, 반박 뒤 다음 consumer를 실제로 읽을 기회의 보장이다. 동결된 V1에 소급 적용하지 않는다. 별도 역사적 Django 재생과 runtime 관측 대조군은 이 기전을 직접 점검하되 한 사례 성공을 일반 성능 향상으로 확대하지 않는다.
