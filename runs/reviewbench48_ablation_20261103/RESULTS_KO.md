# 48개 PR 단계별 코드 리뷰 비교

고정 48개 PR × 6개 조건 × 2회, 총 576개 리뷰를 포함한다. 주 평가 정답은 PR이 도입·악화한 기능 결함 366개이며 원문 372건의 중복 원인을 모델 실행 전에 통합했다. 모든 기능 결함 385개는 보조 평가로 남긴다. 실행 실패와 미판독 결과도 계획 분모에 포함한다.

## 주 결과

| 실제 기능 | 원인 F1 | 유효 보고 F1 | 원인 재현율 | 확인 TP / 정답 노출 | 실제 출력 보고 | 원인 오판·확인 오탐 | 의미 미해결 / 미판독 | 리뷰 출력 실패 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 변경 diff만 리뷰 | 9.66% | 6.76% | 5.46% | 40 / 732 | 96 | 2 | 11 / 26 | 9 |
| 변경 diff + AST 본문 | 9.22% | 7.77% | 5.19% | 38 / 732 | 92 | 1 | 11 / 24 | 9 |
| 변경 diff + AST + LSP 조회 | 10.79% | 10.31% | 6.15% | 45 / 732 | 102 | 4 | 12 / 20 | 8 |
| 변경 diff + AST + LSP + SCIP 후보 | 10.94% | 9.48% | 6.15% | 45 / 732 | 91 | 2 | 11 / 16 | 10 |
| 같은 SCIP 본문으로 추가 검토 | 8.23% | 7.48% | 4.51% | 33 / 732 | 70 | 1 | 7 / 13 | 22 |
| SCIP 본문에서 원인 질문·반박·재탐색 | 5.98% | 5.48% | 3.28% | 24 / 732 | 71 | 1 | 12 / 20 | 23 |

원인 F1 = 2 × 확인된 정답 원인 보고 수 / (실제 최종 보고 수 + 고정 정답 수). 유효 보고 F1은 원인이 맞고, 회귀 위치·실패 연산·인용이 실제 전달된 코드 및 의미에 부합하며 보고 출력 계약을 통과한 경우만 TP로 센다. 보조 가설 ledger의 수용 여부는 주 지표의 게이트가 아니다.

정답에 매칭되지 않은 보고를 곧바로 오탐으로 세지 않았다. 독립적으로 확인된 다른 결함, 기존 결함, 중복, 미해결과 실제 반박된 오탐을 분리했다. 이 데이터에는 확정적인 무결함 PR 집합이 없으므로 clean-PR FPR을 계산하지 않는다. 미해결·미판독이 있으면 표의 F1은 확인 TP만 센 보수적 하한이며, 그 미확인 보고를 거짓이라고 판정했다는 뜻이 아니다.

이전 12개 실험의 유효 보고 지표는 auxiliary claim update 거절을 보고 실패와 혼합했다. 이번 주 지표는 report_status를 분리했으므로 이전 headline과 직접 비교하지 않는다. 동일 보고에 예전 auxiliary 게이트를 추가한 compatibility 지표는 아래에 별도로 남겼다.

## 사전 소스 검증과 보조 결과

리뷰 실행 전에 소스로 지지되고 회귀 범위도 확인된 정답 116개를 보조 집합으로 동결했다. 원본 정답 전체를 쓰는 주 분모는 유지했다. Gold 판독 상태: `{"judge_status_counts": {"judge_failed": 3, "complete": 45}, "source_verdict_counts": {"supported": 125, "unresolved": 207, "refuted": 8}, "failed_gold_cases": ["RB_DisciplinedSoftware__Codebase-Conversion-1", "RB_basecamp__basecamp-cli-415", "RB_fentas__b-136"], "supported_subset_canonical_targets": 116}`.

| 실제 기능 | 소스 지지 정답 기준 원인 F1 | 전체 기능 정답 기준 원인 F1 | 기존 aux 게이트 포함 유효 F1 | 별도 확인된 다른 결함 |
|---|---:|---:|---:|---:|
| 변경 diff만 리뷰 | 11.59% | 9.24% | 0.24% | 17 |
| 변경 diff + AST 본문 | 9.88% | 8.82% | 3.64% | 18 |
| 변경 diff + AST + LSP 조회 | 11.98% | 10.32% | 4.56% | 21 |
| 변경 diff + AST + LSP + SCIP 후보 | 13.62% | 10.45% | 3.40% | 17 |
| 같은 SCIP 본문으로 추가 검토 | 10.60% | 7.86% | 6.23% | 16 |
| SCIP 본문에서 원인 질문·반박·재탐색 | 6.60% | 5.71% | 3.99% | 14 |

## 단계별 변화와 불확실성

| 비교 | 원인 F1 변화 | 95% 저장소 cluster 구간 | 유효 보고 F1 변화 | 95% 저장소 cluster 구간 |
|---|---:|---:|---:|---:|
| B1 minus B0 | -0.44%p | -2.78% ~ 2.06%p | 1.00%p | -0.81% ~ 2.96%p |
| B2 minus B1 | 1.57%p | -1.58% ~ 4.76%p | 2.54%p | -0.58% ~ 5.66%p |
| B3 minus B2 | 0.14%p | -2.59% ~ 2.71%p | -0.83%p | -3.93% ~ 2.06%p |
| C minus B3 | -2.71%p | -5.52% ~ -0.17%p | -2.00%p | -4.84% ~ 0.52%p |
| B4 minus C | -2.25%p | -4.71% ~ 0.25%p | -2.00%p | -4.23% ~ 0.25%p |
| B4 minus B3 | -4.96%p | -7.89% ~ -1.96%p | -4.00%p | -7.06% ~ -0.94%p |

동일 PR·반복을 유지한 저장소 단위 bootstrap 10,000회이며 PR 단위 구간은 aggregate.json에 별도 보존한다. 판독 미확인이 있으면 구간은 확인 TP 점수의 차이에 대한 구간이며, 미확인된 실제 효과의 상·하한을 보장하지 않는다. 두 번의 반복은 모델 샘플 변동을 충분히 추정하는 대규모 반복 실험으로 해석하지 않는다.

## 실제 파일 언어별 결과

| 실제 기능 | 정답/보고 파일 언어 | 원인 F1 | 유효 보고 F1 | TP / 정답 노출 | 보고 |
|---|---|---:|---:|---:|---:|
| 변경 diff만 리뷰 | Go | 13.33% | 8.00% | 15 / 202 | 23 |
| 변경 diff만 리뷰 | JavaScript | 0.00% | 0.00% | 0 / 12 | 2 |
| 변경 diff만 리뷰 | Python | 9.30% | 7.24% | 18 / 338 | 49 |
| 변경 diff만 리뷰 | TypeScript | 7.69% | 5.13% | 3 / 74 | 4 |
| 변경 diff만 리뷰 | YAML | 11.43% | 11.43% | 2 / 30 | 5 |
| 변경 diff만 리뷰 | other | 4.60% | 2.30% | 2 / 76 | 11 |
| 변경 diff만 리뷰 | unknown | 해당 없음 | 해당 없음 | 0 / 0 | 2 |
| 변경 diff + AST 본문 | Go | 12.50% | 11.61% | 14 / 202 | 22 |
| 변경 diff + AST 본문 | JavaScript | 13.33% | 13.33% | 1 / 12 | 3 |
| 변경 diff + AST 본문 | Python | 9.77% | 7.71% | 19 / 338 | 51 |
| 변경 diff + AST 본문 | TypeScript | 5.13% | 5.13% | 2 / 74 | 4 |
| 변경 diff + AST 본문 | YAML | 11.76% | 5.88% | 2 / 30 | 4 |
| 변경 diff + AST 본문 | other | 0.00% | 0.00% | 0 / 76 | 5 |
| 변경 diff + AST 본문 | unknown | 해당 없음 | 해당 없음 | 0 / 0 | 3 |
| 변경 diff + AST + LSP 조회 | Go | 16.67% | 14.91% | 19 / 202 | 26 |
| 변경 diff + AST + LSP 조회 | JavaScript | 14.29% | 14.29% | 1 / 12 | 2 |
| 변경 diff + AST + LSP 조회 | Python | 9.74% | 9.74% | 19 / 338 | 52 |
| 변경 diff + AST + LSP 조회 | TypeScript | 2.53% | 2.53% | 1 / 74 | 5 |
| 변경 diff + AST + LSP 조회 | YAML | 5.88% | 5.88% | 1 / 30 | 4 |
| 변경 diff + AST + LSP 조회 | other | 9.09% | 9.09% | 4 / 76 | 12 |
| 변경 diff + AST + LSP 조회 | unknown | 해당 없음 | 해당 없음 | 0 / 0 | 1 |
| 변경 diff + AST + LSP + SCIP 후보 | Go | 15.86% | 14.10% | 18 / 202 | 25 |
| 변경 diff + AST + LSP + SCIP 후보 | JavaScript | 0.00% | 0.00% | 0 / 12 | 0 |
| 변경 diff + AST + LSP + SCIP 후보 | Python | 11.89% | 10.34% | 23 / 338 | 49 |
| 변경 diff + AST + LSP + SCIP 후보 | TypeScript | 5.13% | 2.56% | 2 / 74 | 4 |
| 변경 diff + AST + LSP + SCIP 후보 | YAML | 12.12% | 12.12% | 2 / 30 | 3 |
| 변경 diff + AST + LSP + SCIP 후보 | other | 0.00% | 0.00% | 0 / 76 | 7 |
| 변경 diff + AST + LSP + SCIP 후보 | unknown | 해당 없음 | 해당 없음 | 0 / 0 | 3 |
| 같은 SCIP 본문으로 추가 검토 | Go | 14.98% | 14.10% | 17 / 202 | 25 |
| 같은 SCIP 본문으로 추가 검토 | JavaScript | 0.00% | 0.00% | 0 / 12 | 1 |
| 같은 SCIP 본문으로 추가 검토 | Python | 5.45% | 4.90% | 10 / 338 | 29 |
| 같은 SCIP 본문으로 추가 검토 | TypeScript | 7.69% | 5.13% | 3 / 74 | 4 |
| 같은 SCIP 본문으로 추가 검토 | YAML | 18.18% | 18.18% | 3 / 30 | 3 |
| 같은 SCIP 본문으로 추가 검토 | other | 0.00% | 0.00% | 0 / 76 | 6 |
| 같은 SCIP 본문으로 추가 검토 | unknown | 해당 없음 | 해당 없음 | 0 / 0 | 2 |
| SCIP 본문에서 원인 질문·반박·재탐색 | Go | 14.98% | 13.22% | 17 / 202 | 25 |
| SCIP 본문에서 원인 질문·반박·재탐색 | JavaScript | 0.00% | 0.00% | 0 / 12 | 2 |
| SCIP 본문에서 원인 질문·반박·재탐색 | Python | 1.64% | 1.64% | 3 / 338 | 28 |
| SCIP 본문에서 원인 질문·반박·재탐색 | TypeScript | 5.13% | 5.13% | 2 / 74 | 4 |
| SCIP 본문에서 원인 질문·반박·재탐색 | YAML | 6.06% | 6.06% | 1 / 30 | 3 |
| SCIP 본문에서 원인 질문·반박·재탐색 | other | 2.47% | 2.47% | 1 / 76 | 5 |
| SCIP 본문에서 원인 질문·반박·재탐색 | unknown | 해당 없음 | 해당 없음 | 0 / 0 | 4 |

매칭된 보고는 실제 gold 파일 언어, 미매칭 보고는 failure_operation의 파일 언어로 분류했다. 파일이 불명확하면 unknown으로 유지했다. 저장소의 주 언어별 결과도 JSON에 보존하지만 언어 자체의 인과 효과로 해석하지 않는다.

## 사전 구동·지원 범위·입장 조건별 결과

| 사전 분류 | 조건 | 실제 기능 | 원인 F1 | 유효 보고 F1 | PR 수 | 정답 노출 |
|---|---|---|---:|---:|---:|---:|
| LSP·SCIP 양 snapshot 구동 | both_snapshot_ready | 변경 diff만 리뷰 | 11.89% | 7.64% | 32 | 410 |
| LSP·SCIP 양 snapshot 구동 | both_snapshot_ready | 변경 diff + AST 본문 | 11.49% | 9.79% | 32 | 410 |
| LSP·SCIP 양 snapshot 구동 | both_snapshot_ready | 변경 diff + AST + LSP 조회 | 12.97% | 12.13% | 32 | 410 |
| LSP·SCIP 양 snapshot 구동 | both_snapshot_ready | 변경 diff + AST + LSP + SCIP 후보 | 11.91% | 9.79% | 32 | 410 |
| LSP·SCIP 양 snapshot 구동 | both_snapshot_ready | 같은 SCIP 본문으로 추가 검토 | 10.75% | 9.46% | 32 | 410 |
| LSP·SCIP 양 snapshot 구동 | both_snapshot_ready | SCIP 본문에서 원인 질문·반박·재탐색 | 8.17% | 7.31% | 32 | 410 |
| LSP·SCIP 양 snapshot 구동 | partial_or_not_both_ready | 변경 diff만 리뷰 | 6.72% | 5.60% | 16 | 322 |
| LSP·SCIP 양 snapshot 구동 | partial_or_not_both_ready | 변경 diff + AST 본문 | 6.21% | 5.08% | 16 | 322 |
| LSP·SCIP 양 snapshot 구동 | partial_or_not_both_ready | 변경 diff + AST + LSP 조회 | 7.87% | 7.87% | 16 | 322 |
| LSP·SCIP 양 snapshot 구동 | partial_or_not_both_ready | 변경 diff + AST + LSP + SCIP 후보 | 9.63% | 9.07% | 16 | 322 |
| LSP·SCIP 양 snapshot 구동 | partial_or_not_both_ready | 같은 SCIP 본문으로 추가 검토 | 4.75% | 4.75% | 16 | 322 |
| LSP·SCIP 양 snapshot 구동 | partial_or_not_both_ready | SCIP 본문에서 원인 질문·반박·재탐색 | 2.96% | 2.96% | 16 | 322 |
| 변경된 .py/.go 여부 | changed_py_or_go | 변경 diff만 리뷰 | 9.47% | 6.69% | 40 | 640 |
| 변경된 .py/.go 여부 | changed_py_or_go | 변경 diff + AST 본문 | 9.50% | 8.10% | 40 | 640 |
| 변경된 .py/.go 여부 | changed_py_or_go | 변경 diff + AST + LSP 조회 | 11.34% | 10.79% | 40 | 640 |
| 변경된 .py/.go 여부 | changed_py_or_go | 변경 diff + AST + LSP + SCIP 후보 | 11.44% | 10.04% | 40 | 640 |
| 변경된 .py/.go 여부 | changed_py_or_go | 같은 SCIP 본문으로 추가 검토 | 7.78% | 7.20% | 40 | 640 |
| 변경된 .py/.go 여부 | changed_py_or_go | SCIP 본문에서 원인 질문·반박·재탐색 | 5.77% | 5.19% | 40 | 640 |
| 변경된 .py/.go 여부 | no_changed_py_or_go | 변경 diff만 리뷰 | 10.91% | 7.27% | 8 | 92 |
| 변경된 .py/.go 여부 | no_changed_py_or_go | 변경 diff + AST 본문 | 7.41% | 5.56% | 8 | 92 |
| 변경된 .py/.go 여부 | no_changed_py_or_go | 변경 diff + AST + LSP 조회 | 7.21% | 7.21% | 8 | 92 |
| 변경된 .py/.go 여부 | no_changed_py_or_go | 변경 diff + AST + LSP + SCIP 후보 | 7.55% | 5.66% | 8 | 92 |
| 변경된 .py/.go 여부 | no_changed_py_or_go | 같은 SCIP 본문으로 추가 검토 | 11.11% | 9.26% | 8 | 92 |
| 변경된 .py/.go 여부 | no_changed_py_or_go | SCIP 본문에서 원인 질문·반박·재탐색 | 7.27% | 7.27% | 8 | 92 |
| 모델 전 full diff 입장 | B0_initial_admitted | 변경 diff만 리뷰 | 10.61% | 7.43% | 44 | 658 |
| 모델 전 full diff 입장 | B0_initial_admitted | 변경 diff + AST 본문 | 10.13% | 8.53% | 44 | 658 |
| 모델 전 full diff 입장 | B0_initial_admitted | 변경 diff + AST + LSP 조회 | 11.84% | 11.32% | 44 | 658 |
| 모델 전 full diff 입장 | B0_initial_admitted | 변경 diff + AST + LSP + SCIP 후보 | 12.02% | 10.41% | 44 | 658 |
| 모델 전 full diff 입장 | B0_initial_admitted | 같은 SCIP 본문으로 추가 검토 | 9.07% | 8.24% | 44 | 658 |
| 모델 전 full diff 입장 | B0_initial_admitted | SCIP 본문에서 원인 질문·반박·재탐색 | 6.58% | 6.04% | 44 | 658 |
| 모델 전 full diff 입장 | full_diff_initial_unadmitted | 변경 diff만 리뷰 | 0.00% | 0.00% | 4 | 74 |
| 모델 전 full diff 입장 | full_diff_initial_unadmitted | 변경 diff + AST 본문 | 0.00% | 0.00% | 4 | 74 |
| 모델 전 full diff 입장 | full_diff_initial_unadmitted | 변경 diff + AST + LSP 조회 | 0.00% | 0.00% | 4 | 74 |
| 모델 전 full diff 입장 | full_diff_initial_unadmitted | 변경 diff + AST + LSP + SCIP 후보 | 0.00% | 0.00% | 4 | 74 |
| 모델 전 full diff 입장 | full_diff_initial_unadmitted | 같은 SCIP 본문으로 추가 검토 | 0.00% | 0.00% | 4 | 74 |
| 모델 전 full diff 입장 | full_diff_initial_unadmitted | SCIP 본문에서 원인 질문·반박·재탐색 | 0.00% | 0.00% | 4 | 74 |

이 분류는 모델 실행 전에 정해졌다. 양쪽 native 구동 32개/그 외 16개, 실제 .py/.go 변경 40개/그 외 8개, B0 full diff 입장 가능 44개/상한 초과 4개를 각각 분리한다. 상한 초과 사례의 diff를 자르거나 표본에서 제외하지 않았다. 구동 여부는 관측한 정의·참조·index 사용성이고 결함 경로의 의미적 정확성을 보장하지 않는다.

## 비용과 실패

| 비용 종류 | 최종 수신 기록 | 사용량 확인 토큰 합 | 사용량 미확인 기록 | 확정 미발급 |
|---|---:|---:|---:|---:|
| reviewer | 590 | 17551484 | 4 | 57 |
| gold_judge | 48 | 1759978 | 0 | 0 |
| review_judge | 383 | 22241989 | 0 | 0 |

리뷰어와 정답/최종 리뷰 판독 모델의 사용량을 분리했다. 미보고 usage를 0으로 합산하지 않았다. 달러 청구액은 이 토큰 합만으로 확정하지 않는다.

| 실제 기능 | logical 호출 | 확인 모델 토큰 | Native 호출 | Native 초 | BASE·HEAD index 초 | 평균 active wall 초 | 평균 수집 source 토큰 | 최종 입력 source 토큰 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 변경 diff만 리뷰 | 88 | 2449677 | 0 | 0.00 | 0.00 | 37.92 | 0.00 | 0.00 |
| 변경 diff + AST 본문 | 88 | 2720054 | 168 | 5.24 | 0.00 | 32.52 | 2075.67 | 1903.62 |
| 변경 diff + AST + LSP 조회 | 88 | 3029349 | 1080 | 102.08 | 0.00 | 34.34 | 3349.21 | 3003.46 |
| 변경 diff + AST + LSP + SCIP 후보 | 88 | 3479107 | 1442 | 107.36 | 1315.06 | 53.55 | 5572.56 | 4973.92 |
| 같은 SCIP 본문으로 추가 검토 | 179 | 6392091 | 1442 | 107.36 | 1315.06 | 74.34 | 5572.56 | 4124.82 |
| SCIP 본문에서 원인 질문·반박·재탐색 | 178 | 6439420 | 2149 | 143.13 | 1315.06 | 77.31 | 5806.88 | 4265.82 |

실제 공통 문맥 준비: 48개, native 721회/53.68초. Source 획득 snapshot 96개, primary navigation 준비 69개. 원 수집 실패·uniform recovery·의존성 환경 준비는 실제 preflight 비용으로 남겼다.

조건별 logical 비용은 공유 B3 initial을 C/B4에도 포함하며 실제 발행 비용과 다르다. 전체 physical model·native preflight·실패·usage 미상·receipt SHA는 usage.json과 cost_receipt_references.json에 보존한다. Runtime probe 0회.

## 판독과 해석 범위

Nonblind AI direct/source judgment. Human-reviewed findings: 0. Benchmark-reference primary denominators retained even if source gold judge refuted/unresolved/failed.

조건 이름을 가린 AI 판독을 사용했지만 결과와 private gold를 보는 평가이므로 nonblind AI source adjudication이다. 기계 검증은 ID·snapshot·경로·물리 범위·본문 hash의 일치 범위만 보장하며 원인의 의미적 진위를 보장하지 않는다.

단계별 비교는 본문 양과 탐색 정책이 함께 바뀌는 source augmentation 비교다. AST·LSP·SCIP 엔진 자체만의 순수 효과로 분리해 주장하지 않는다. 추가 검토 대조군은 같은 본문에서 모델 호출을 늘린 효과를 확인한다. 예산 상한이 같아도 실제 호출·토큰·수집량 차이는 비용 원장으로 함께 확인해야 한다.

미사용 PR로 선정했더라도 48개는 LSP·SCIP 실행 가능성과 복잡한 문맥 조건을 목표로 선별한 표본이다. 전체 언어·전체 코드 리뷰 PR에 대한 일반화는 별도 외부 평가가 필요하다. 현재 결과를 바탕으로 원인 질문·반박·재탐색의 효과를 채택하려면 B4와 같은 본문의 추가 검토 대조군 차이 및 실제 실패 로그를 함께 검토해야 한다.

원시 입력·응답·최종 finding·source packet·source 판독·기계 인용 검증·실패·비용 원문은 immutable JSON 기록과 aggregate.json의 SHA 참조에 남겼다.
