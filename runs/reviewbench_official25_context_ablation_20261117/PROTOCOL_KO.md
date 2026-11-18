# 공식 25 PR 문맥 추가 비교 — 실행 전 동결 명세

## 질문과 조건

동일 25개 공개 PR에서 제공 문맥만 달리했을 때 공식 Grounded·Augmented F1, 총 오탐, 비용이 어떻게 변하는가?

| 조건 | 계획·최종 리뷰에 제공하는 추가 문맥 |
|---|---|
| B0 | 없음. 공개 PR 제목·설명·diff 기반 변경 단위만 사용 |
| B1 | B0 + 기존 AST 단계가 확보한 주변 코드 본문 |
| B2 | B1 + 기존 LSP 단계가 확보한 본문·정의/참조 메타데이터 |

모두 계획 1회 → 최종 리뷰 1회의 2단계 리뷰다. 계획의 질문을 추가 실행하지 않는다. planner에는 공통 diff 단위 요약(최대 3,500 기준 토큰), final에는 전체 original diff를 전달한다. 새 응답과 새 공식 채점을 생성한다. 기존 최종 보고·판정·private gold는 reviewer에게 제공하지 않는다. SCIP·추가 탐색·runtime probe는 없다. 기능 결함 중심의 공통 프롬프트이며 스타일 등 모든 gold 유형을 포괄하는 정책은 아니다.

## 공통 모델·예산·출력

- 모델: gpt-5.6-sol, reviewer reasoning low, standard tier. alias이므로 과거 실행과 동일 가중치를 보장하지 않는다.
- PR·조건당 planner 1회(출력 최대 6,144), final 1회(최대 16,384). 입력+출력 합계 상한 750,000. final 전 실제 입력 token count로 점검. 자동 prompt 잘림 금지.
- 리뷰 1건 active wall 상한 3,600초. 기존 native 수집 시간과 judge 시간은 별도 집계한다. 리뷰 동시성 총 6, 공식 judge 동시성 조건당 2.
- 원문 상한 6,000 기준 토큰. 저장된 동일 스냅샷을 재사용하며 원래 AST 수집은 3,000, LSP 누적은 6,000 상한이었다. 동일 source ceiling이지만 동일 실제 본문 양이나 AST 재수집 정책 비교는 아니다.
- 공통 reporting schema, location controller, 최대 6개 finding 지시. controller의 물리적 위치 결속은 의미적 정확성 보장이 아니다.
- 이번 리뷰+judge 비용 추정 상한 합계 $70, 조건당 $70/3. 실제 usage와 과거 실험의 고정 단가로 추정하며 invoice 확정액이 아니다. 비용 불명 호출은 최대 예약액을 계속 차감한다.
- reviewer는 임의 재시도하지 않는다. 네트워크·schema·미완료를 보존한다. 공식 judge의 자체 checkpoint/재시도 경로는 세 조건 공통이며 모든 API 시도를 비용에 포함한다. 실패 PR을 정상 무경고로 대체하거나 분모에서 몰래 제외하지 않는다.

## 공식 채점과 보고

ReviewBench 고정 commit 03c62fe06ca42b39216402ebbcb9aec765109a59의 judge/scorer/schema/gold를 그대로 사용한다. matcher reasoning low, classifier medium, 모두 gpt-5.6-sol. 조건별 writable repo/cache/checkpoint를 분리한다. 동일 모델 producer명, 동일 공개 입력과 gold manifest. 평가자에게만 gold를 제공한다.

25 PR 전체 micro precision/recall로 F1=2PR/(P+R). Grounded는 gold에 대응한 경고만 precision에 포함한다. Augmented는 미대응 경고를 공식 AI가 추가 판단한다. 총 오탐은 matched_count−matched_tp_count+unmatched_fp_count이며 인간/실행 검증과 구분한다. 각 조건의 보고 수·gold coverage·총 FP·새 model 비용과 과거 native 비용을 함께 남긴다. gold ID 집합으로 정탐 보존/추가/손실도 비교한다.

B1→B2를 새 물리행 있음(15 PR), metadata만 추가(4), 동일 근거(6)로 층화한다. 동일 근거에서의 차이는 도구 효과가 아닌 단일회 모델·채점 변동이다. LSP 추가 본문 57개 중 10개는 환경 가정에 따른 외부 구현으로 실제 PR 환경 바인딩 확정이 아니다.

## 정보 경계·해석 제한

반복 개발에 사용한 25개이며 미사용 test set이 아니다. 결과 확인 후 사례를 선정하지 않는다. 단일회, 비결정적 모델/AI judge이므로 통계적 유의성·일반화를 주장하지 않는다. 새 B0/B1/B2끼리 비교하며 과거22.60 등은 paired baseline으로 섞지 않는다. 측정 대상은 AST/LSP로 수집한 문맥 추가의 복합 효과(본문 양·메타데이터·선택 가능 위치·계획 변화)이며 LSP 엔진만의 순수 효과는 아니다.

모델 실행 전 코드·prompt·schema·75개 plan 입력·25개 public packet·예산·official scorer를 freeze.json SHA256으로 동결한다. raw 요청/응답/실패/위치 catalog/변환 누락/API 비용 ledger/공식 judge 로그와 상세 결과를 보존한다. 이후 발견된 구현 문제는 원본을 덮지 않고 수정·재실행 여부를 별도 기록한다.
