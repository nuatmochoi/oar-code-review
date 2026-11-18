# 48개 공개 PR의 단계별 탐색 비교: 모델 실행 전 명세

## 목적과 표본

기존 12개 개발 사례의 결과를 보존하고, 미사용으로 선정한 ReviewBench 48개 원래 방향 PR에서 diff-only부터 소스 보완·원인 질문 탐색의 증분 효과를 검증한다. 같은 48개를 모든 조건에서 두 번 실행한다. 소스·native 실패나 입력 초과를 보고 사례에서 제외하지 않는다. 총 576개 논리 리뷰이다.

선정은 기존 benchmark의 기능 결함·복잡한 문맥 조건에 따른 것으로, 이번 모델 결과에 따른 선택이 아니다. 저장소 주 언어는 Python 30/Go 18개이나 실제 변경·결함 파일에는 다른 언어가 포함된다. 실제 파일 언어별 결과와 native 구동 준비 층화를 병기한다. 이 표본은 무결함 PR의 오경고율이나 언어 자체의 효과를 추정하기 위한 표본이 아니다.

## 조건

| 조건 | 실제 제공 기능 | 모델 호출 |
|---|---|---|
| B0 | 원래 공개 PR 제목·본문과 전체 고정 diff | 최종 리뷰 1회 |
| B1 | B0 + AST로 선정한 온전한 지역 코드 단위 | 최종 리뷰 1회 |
| B2 | B1 + native LSP 질문 조회 결과와 실제 본문 | 최종 리뷰 1회 |
| B3 | B2 + BASE/HEAD SCIP 정의·참조 후보를 이용한 본문 | 최종 리뷰 1회 |
| C | B3와 같은 고정 본문에서 추가 검토 | 총 2~3회 |
| B4 | B3 초기 리뷰 뒤 원인 질문→실제 조회→검증·반박→필요 시 다음 조회→최종 리뷰 | 총 2~3회 |

공개 제목·본문·diff는 모든 호출에 동일하게 전체 제공하며 diff 본문은 한 번만 넣는다. DIFF 인용 ID는 실제 diff에 보이는 물리 행만 결속한다. 큰 입력을 잘라 성공으로 처리하지 않으며 예산에 못 들어가면 계획 실패로 기록한다. private gold·문맥 난이도·선정 이유·소스 gold 판독은 reviewer 입력에 포함하지 않는다. PR 텍스트는 명령이 아닌 데이터이다.

모델 전 정확한 초기 입력 admission 감사에서 192개 조건별 입력 중 176개가 예산에 들어갔다. 네 PR(artifacts#24, span#207, gonzo#129, gh-aw#26515)은 원래 full diff만으로도 120,000 토큰 상한을 넘는다. 이 네 PR의 주 원인 37개를 전체 분모에서 유지하고, reviewer 미발급·후속 미도달로 기록한다. 사전에 확인된 full-diff 입력 가능 44개 결과도 보조 층화로 제공하며 headline 48개를 대체하지 않는다. 정답이나 모델 결과를 본 뒤 입력을 줄여 성공으로 바꾸지 않는다.

AST→LSP→SCIP의 추가 소스 상한은 각각 3,000→6,000→9,000 reference 토큰이다. B4는 같은 허용 소스 풀에서 총 12,000까지 조회한다. 온전한 함수/코드 단위가 남은 상한에 들어가지 않으면 전체 단위를 거절하고 미해결 사유를 남긴다. 본문을 임의 길이로 잘라 확보했다고 세지 않는다. 따라서 이 비교는 소스 양과 탐색 정책을 함께 변화시킨다. 순수한 index 형식만의 인과 효과라는 주장을 하지 않는다.

B3의 실제 초기 호출은 C/B4와 물리적으로 공유하되 각 논리 조건에 동일하게 비용을 부과한다. C/B4 추가 호출 수는 공유 초기 결과의 동일한 입력 크기 예측으로 미리 정한다. 3회가 가능하다고 예측되면 중간 검증 및 최종을, 아니면 최종 추가 검토만 계획한다. 각 실제 호출 직전 다시 입력 및 최종 예약분을 검사한다. C는 새 도구 조회를 하지 않는다. B4의 추가 소스 때문에 최종 예약이 부족해지는 경우도 실패로 보존한다. 중간 검토를 최종으로 승격하지 않는다.

## 공통 예산·전송

모델은 gpt-5.6-sol, reasoning low이며 격리된 CLI에서 도구 없이 호출한다. 공통 상한은 논리 리뷰당 provider input+output 120,000 토큰, 모델 3회, 추가 소스 union 12,000 reference 토큰, native 48회/180초, 파일 읽기 256회, 질문당 20초, active wall 900초이다. paired BASE/HEAD SCIP 준비에는 별도로 180초 상한을 적용한다. 선택된 실제 언어 profile들을 모두 합산하며 이번 48개는 최대 69.61초여서 모두 상한 안이다. index/export 실제 비용을 B3/C/B4에 부과한다. 초기 수집과 shutdown 비용도 각 논리 조건에 부과한다.

전역 동시 모델 호출은 4개, 최소 시작 간격은 3초이다. stage당 실제 CLI 발급은 한 번이고 자동 retry·JSON 수리·모델 판단의 대체 생성은 없다. 정확한 composed stdin·실제 pretty schema·developer instruction을 reference tokenizer로 계산하고, CLI overhead 2,048 및 output 8,000 토큰을 예약한다. 중간 검증에는 미래 최종 입력·출력 예약과 16,000 토큰의 향후 입력 증가 예상분을 추가한다. 이는 admission 계산이며 server 측 강제 max-output 보장을 뜻하지 않는다. 실제 provider 사용량으로 초과 여부를 별도 검증하며 usage 미보고는 0으로 취급하지 않는다. 동일 예산 상한과 실제 비용을 함께 보고한다.

독립 소스 gold 판독 48개와 최종 리뷰 판독 최대 576개는 reviewer 예산 밖 평가 비용으로 분리한다. 판독에도 같은 모델·전송·단일 호출 정책을 쓴다. 최종 보고가 없으면 의미적 finding 판독을 만들지 않고 기계적 출력 실패/빈 보고로 남긴다. Runtime probe는 이 정적 실험에서 0회이다.

## 실제 실행 경로와 도구 한계

기존 oar_core/claim_controller의 후보·필수조건·반박 및 현재/과거 route 구분을 실제 update/bind 호출로 연결한다. ledger 수용 여부는 의미적 원인 진위를 보장하지 않으며 별도 보조 상태로 기록한다. 새 모델 입력에 실제 코드와 후보 변화·반박 수용 여부를 전달한다. 초기 후보가 없어도 바뀐 호출·대입·반환·조건을 seed로 조회할 수 있다. 소스 실패·다중 대상·동적 연결·미지원 문법은 unresolved로 남긴다.

SCIP symbol reference는 호출·dataflow로 자동 재명명하지 않는다. AST의 문법적 분류와 native LSP 정의·참조·구현 결과를 구분한다. LSP capability와 미지원 호출을 원형으로 보존한다. 외부 본문은 PR 분석 interpreter/workspace/lock 설정 및 실제 symbol authority에 연결된 허용 경로에서만 읽는다. 호스트 stdlib의 존재를 배포 환경의 동작 증거로 간주하지 않는다. version/origin/hash/가정을 보존한다. BASE/HEAD의 관계가 같아도 인자·조건 변경이 있음을 AST/diff로 검토한다. 정적 코드의 의미적 지지·반박은 모델 판정이며 실행 증명이 아니다.

소스/native 사전 검증은 48 PR·96 snapshot 전부를 시도했다. 최초 archive 160MB 제한 실패 2개는 원 실패를 보존하고 동일한 320MB archive/1.2GB unpack 재시도 규칙으로 복구했다. 최종 source 96/96, 공개 diff 바인딩 48/48이다. 양쪽 snapshot native 준비 사례는 주 언어 31/48, 지원 보조 언어 포함 32/48이다. 준비됨은 일부 seed에서 정의/참조 및 index가 동작했다는 뜻이며 필요한 결함 경로가 모두 해소됐다는 뜻이 아니다. 기술 preflight의 per-snapshot 제한·cold 환경 구축·실패·복구 비용과 reviewer 논리 비용은 별도 원장에 기록한다. preflight 문서에 기술된 100MB wheel admission은 미구현이었고 pip cache는 전역이어서 네트워크 byte 및 고립된 설치 비용을 보장하지 못했다. 해당 제약은 원형 discrepancy로 보존한다.

## 평가 동결

private gold 원문 기능 결함 391건 중 도입 361·악화 11건이 주 대상, 기존 19건은 보조 대상이다. 모델 실행 전에 같은 인스턴스 5개 묶음의 중복 6건을 합쳐 주 원인 366개·전체 기능 원인 385개를 동결한다. 미사용 48개 PR도 원본 gold 자체가 완벽하다고 가정하지 않으며, frozen source packet으로 reviewer 이전에 소스 지지/반박/미해결을 판독한다. 원본 366개 주 분모는 이 판독으로 삭제하지 않는다. 소스 지지된 회귀 원인 subset만 reviewer 전에 별도 seal해 보조 평가로 사용한다. 이 소스 판독의 모델 결론을 최종 finding 판독의 정답 oracle로 넣지 않는다.

원인 F1은 2×일대일 매칭된 올바른 원인 보고/(실제 최종 보고 수+고정 정답 노출 수)이다. 유효 보고 F1은 원인 정확성에 더해 regression_anchor/failure_operation 관련성·실제 전달된 인용 ID/행의 기계적 유효성·본문 의미 관련성 및 provider/schema/report_status를 요구한다. 보조 ledger update 거절은 주 보고 validity의 전역 게이트가 아니다. 예전 aux gate를 더한 compatibility 점수도 함께 남긴다. 이 차이 때문에 예전 12개 headline과 직접 수치 비교하지 않는다.

정답 매칭 실패를 곧바로 실제 오탐으로 분류하지 않는다. 별도 확인된 다른 결함·기존 결함·중복·의미 미해결·판독 실패·반박된 오탐을 구분한다. judge 실패와 reviewer 실패를 계획 분모에서 제거하지 않는다. raw final JSON 자체를 해석 못하면 보고 수를 추측하지 않으며 해당 원인은 미탐으로 남긴다. 판독 미확인 때의 점수는 관측 가능한 parseable 보고에 대한 확인 TP 점수이며 해석 불가능한 원문 속 가상의 보고 수까지 포함한 수학적 하한이라고 주장하지 않는다.

조건명을 가린 AI source 판독이며 private gold를 보는 nonblind 평가이다. 인간 검토는 0건이다. evidence 기계 검증은 snapshot/path/행/hash 일치만 보장한다. 수정 target·초기 원인의 교정과 악화·미해결/보류·실패 원인을 부가 기록하되 finding 삭제만으로 교정 성공을 주지 않는다. 2회 반복의 paired 비교 및 저장소 cluster bootstrap 10,000회 구간을 보고한다. 일부 결과만으로 F1을 산출하지 않는다.

## 동결 및 산출물

전체 코드·prompt·schema·예산·평가 기준·prepared input·canonical gold·source packet·단계별 context 및 source authority 참조를 freeze.json SHA manifest로 잠근 뒤 첫 모델을 호출한다. 동결 후 변경이 필요하면 기존 원형을 보존하고 새 버전에서 재개한다. raw stdin/response·실제 provider token·도구 RPC·source 범위/hash·후보/조건 변화·위치 바인딩·실패를 보존한다. 과거 결과의 불변 SHA 점검, complete-only aggregate, 비용 원장과 RESULTS_KO.md를 최종 작성한다. 아직 수행하지 않은 native 경로/원인 교정/효과를 구현 파일 존재만으로 contribution으로 주장하지 않는다.
