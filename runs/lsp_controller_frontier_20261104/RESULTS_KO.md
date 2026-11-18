# LSP 탐색 시작점·자동 경로 확장: 개발 비교

같은4개 PR의5문맥을 비교했다. 실제 초기 응답과 첫 모델 요청 조회는 그대로 재사용하고 원본 비용을 차감했다. 새 조건은 동일한 원인·영향 분리 지침에 controller의 자동 탐색을 추가했다. 모델이 질문하지 않아도 현재 주장의 인용 코드, 변경 식별자, 호출·반환값 소비 후보, 타입·상수 선언을 따라간다. 이는 이미 결과를 본 개발 사례이며 미사용/일반화 평가가 아니다.

| 실제 기능 | 원인 F1 | 유효 보고 F1 | 원인 재현율 | 원인 정탐 | 보고 | 확인 오탐 | 의미 미해결 / 미판독 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| AST·LSP 기존 수집 후 초기 리뷰 1회 | 10.26% | 10.26% | 6.06% | 2 | 6 | 2 | 0 / 0 |
| 모델 질문에 따른 재탐색·원인 범위 분리 | 5.56% | 5.56% | 3.03% | 1 | 3 | 0 | 0 / 0 |
| 현재 주장·변경 식별자에서 자동 경로 확장 추가 | 5.56% | 5.56% | 3.03% | 1 | 3 | 0 | 1 / 0 |

과거 초기 정탐3회 중 동일 benchmark 타깃 유지 1/3. 같은 claim ID에 원인이 정확한 보고가 남은 경우 2/3이며, 이는 동일 타깃 보존과 별개다. 회귀 gate 통과 False. 무보고·보류·삭제를 교정 성공으로 세지 않는다.

| 문맥·반복 | 과거 초기 판정 | 초기 재판독 같은 타깃 | 기존 재탐색 같은 타깃 | 자동 확장 같은 타깃 |
|---|---|---|---|---|
| RB_obot-platform__obot-6351 r1 | false  | false | 무보고 | 무보고 |
| RB_Mirkiux__featkit-17 r2 | target_correct G027 | True | True | True |
| RB_sirosfoundation__go-wallet-backend-76 r1 | target_correct G003 | False | False | False |
| RB_sirosfoundation__go-wallet-backend-76 r2 | target_correct G003 | False | False | False |
| RB_tile-ai__TileOPs-864 r2 | false  | false | 무보고 | 무보고 |

## 실제 추가 실행

현재 주장에 연결된 소스·첫 조회 응답을 우선 구조 분석하고, 변경된 AST 식별자도 독립적으로 시작점으로 만든다. LSP definition으로 타입·상수·변수의 실제 선언/초기화 구문을 읽고, 읽은 본문의 직접 호출·단순 반환 대입→소비 인자 후보를 다음 조회에 넣는다. 후보와 미해결 상태는 단계 사이에 보존한다. source ID·snapshot·범위·해시·다중 후보를 기록하며 정적 연결 후보를 실제 실행이나 원인 입증으로 간주하지 않는다.

| 문맥·반복 | 구조 분석 단계 | backend 조회 | 실제 LSP 문서 RPC | 새 전달 소스 단위 / 선언 | 종료 사유 |
|---|---:|---:|---:|---:|---|
| RB_obot-platform__obot-6351_r1 | 7 | 37 | 11 | 35 / 5 | before_replan: native_budget_reserved_or_exhausted; before_final: native_budget_reserved_or_exhausted |
| RB_Mirkiux__featkit-17_r2 | 7 | 31 | 19 | 19 / 3 | before_replan: file_read_budget_reserved_or_exhausted; before_final: file_read_budget_reserved_or_exhausted |
| RB_sirosfoundation__go-wallet-backend-76_r1 | 6 | 0 | 1 | 1 / 0 | before_replan: native_budget_reserved_or_exhausted; before_final: native_budget_reserved_or_exhausted |
| RB_sirosfoundation__go-wallet-backend-76_r2 | 14 | 1 | 3 | 3 / 0 | before_replan: native_budget_reserved_or_exhausted; before_final: native_budget_reserved_or_exhausted |
| RB_tile-ai__TileOPs-864_r2 | 8 | 33 | 31 | 10 / 7 | before_replan: native_budget_reserved_or_exhausted; before_final: native_budget_reserved_or_exhausted |

backend 조회에는 직접 본문 읽기와 검색도 포함하며, AST 구조 단계·LSP RPC와 구별했다. 새 소스 전체가 자동 조회의 효과라는 뜻은 아니다. 모델의 후속 질문도 새 소스를 수집하므로 세부 원시 기록으로 구분한다. source 확보→모델의 반박 반영→최종 원인 정답은 별도 단계이다.

## 비교 범위와 비용

같은 모델·최대200,000 provider토큰·3모델호출·소스12,000 reference토큰·native48호출/180초·파일256읽기·활성900초 상한이다. 신규 reviewer는 최대10호출이며 초기 실제1호출을 논리 비용에 포함한다. worker4, 시작 간격3초. 판독 비용은 별도다.

| 기능 | 논리 모델 호출 | 확인 모델 토큰 | native 호출 / 초 | 파일 읽기 | 활성 초 합계 |
|---|---:|---:|---:|---:|---:|
| AST·LSP 기존 수집 후 초기 리뷰 1회 | 5 | 107222 | 82 / 10.19 | 298 | 162.34 |
| 모델 질문에 따른 재탐색·원인 범위 분리 | 15 | 419488 | 121 / 18.53 | 673 | 568.64 |
| 현재 주장·변경 식별자에서 자동 경로 확장 추가 | 15 | 450310 | 215 / 26.09 | 908 | 619.10 |

자동 탐색은 재계획 전 native34, 최종 전46의 부분 상한으로 후속 모델 질문·종료 비용을 남긴다. 재계획 전에는 남은 소스의 절반(최대2k)을 남긴다. 기존 prefix가 이미 부분 상한을 썼으면 자동 탐색을 보류하고 pending을 다음 단계로 넘긴다. 전체 예산을 늘리지 않았다.

이번 변경은 자동 탐색 정책, 타입·상수 AST/LSP 결속, 해시/파일 상태를 확인하는 collector 내부 AST 메모리 재사용, 누락된 Go 모듈 pin을 사전 허용·해시 검증된 환경 receipt로 복구하는 실행 경로를 함께 포함한다. 소스 허용 root는 같지만 실제 전달 소스·native 사용량은 달라진다. 따라서 순수한 LSP 기능 하나나 탐색 정책만의 효과로 분리 해석할 수 없다. 호스트 기본 stdlib를 PR 구현으로 대체하지 않는다. 동적 dispatch·heap alias·일반 분기간 dataflow는 unresolved이며 runtime probe·SCIP는0이다.

15개 리뷰 terminal 후 같은 PR의 모든 조건·선정 반복에 공통 source packet을 봉인하고 새 AI 판독했다. 인간 검토0, 비맹검이며 실패·모순은 재시도/수리하지 않았다. 원인 F1=2×primary 정탐/(보고+정답 노출33회); 유효 보고는 원인·anchor·실패 연산·의미 근거·실제 범위·예산을 함께 요구한다. 정답표 밖 실제 결함은 오탐으로 합치거나 사후 primary 정답으로 편입하지 않는다. 재판독 변동은 원시 과거 판정과 함께 보존했다.

동결 SHA: 7f51c1187f32f1592122460e7c11675c138537a55d8fa8cdf1ca2cbdd305eb08.
