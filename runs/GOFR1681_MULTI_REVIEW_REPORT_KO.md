# GoFR1681 복수 이슈 ablation 결과

2026-09-14. 기존 개발 PR 1개, 6조건 × 3반복, 이슈 상한 5개.
gpt-5.6-sol low, 생성 18/18 성공, trace 감사상 도구 사용 0회.
작성자 판정이며 독립 oracle·holdout·benchmark recall 결과가 아니다.

## 핵심 결과

| 조건 | 추가 source tokens | 목표 회귀를 issues에 보고 | 그중 top-1 |
|---|---:|---:|---:|
| Diff-only | 0 | 2/3 | 0/3 |
| CORS | 164 | 3/3 | 0/3 |
| CORS+route | 385 | 3/3 | 1/3 |
| Placebo | 382 | 0/3 | 0/3 |
| Route-only | 221 | 2/3 | 0/3 |
| CORS+placebo | 381 | 2/3 | 0/3 |

목표는 OPTIONS/preflight 조기 반환과 등록 순서 변경에 의한 로그 누락을 연결한
이슈다. Limitations에만 가능성을 언급하면 issues 검출로 세지 않았다.
Placebo의 review_09는 일반적인 short-circuit 로그 누락을 경고하지만 OPTIONS/preflight를
특정하지 않아 주 지표에서 제외했다. 이를 넓은 메커니즘 부분 일치로 포함하면 placebo는
1/3이 된다. 해당 경계 사례를 숨기지 않고 별도로 남긴다.

## 앞선 top-1 결과의 재해석

9월 9일 단일 이슈 출력에서 diff 0/3, CORS 1/3, CORS+route 3/3이었다.
이번 복수 출력에서 diff도 2/3회 목표 회귀를 2~3번째에 보고했다. 이는 이전 차이에
우선순위 효과가 섞였음을 시사한다. 실행 날짜가 달라 backend 변화도 배제하지 못한다.
"Diff-only는 이 결함을 판단할 수 없다"는 주장은 지지되지 않는다.

CORS-only와 CORS+route가 모두 3/3이므로 route 추가의 고유한 any-issue 검출 이득은
이번에 관측되지 않았다. CORS+placebo 2/3과 CORS+route 3/3의 한 응답 차이를
route 의미 효과라고 단정할 수 없다. 3반복과 단일 placebo의 한계가 크다.

## 근거의 질

- Diff-only 2개와 route-only 2개의 목표 보고는 CORS 구현 없이 일반적인 동작을 가정했다.
- CORS가 포함된 조건에서 목표를 보고한 8개 응답은 실제 제공된 OPTIONS early-return을
  순서 변경과 연결했다. 다만 모든 조건에 router Use의 조합 구현이 없어 여전히 순서를 가정한다.
- CORS+route의 세 응답 모두 catch-all 매칭 구조를 명시적으로 활용하지 않았다.
  따라서 route의 인과적 활용을 입증하지 못했다.
- 전체 18응답은 1~4개 이슈를 보고했고 5개 상한에 도달한 응답은 없었다.
  상한이 차지 않았다고 모든 결함을 탐색했다는 의미는 아니다.
- 타 이슈(특히 probe panic, exported API, bool casing)는 이번 실험에서 참/거짓 검증하지
  않았으며 false positive로 세지 않았다. Safe PR이 없어 precision/F1/false-alarm 계산 불가.

## 현재 허용되는 결론

**CORS source는 이 개발 PR에서 이미 추측 가능했던 회귀의 보고를 코드 근거로 뒷받침했다.
그러나 diff-only 대비 강한 recall 향상이나 route context의 고유 이득은 확립하지 못했다.**

SCIP/BM25가 이 source를 선택하는 능력은 이번에 비교하지 않았다. 자동 selection 기여나
base/head semantic-index 방법의 신규성을 이 수치로 주장할 수 없다.

## 다음 진행 방향

1. 같은 PR에 반복을 더 쌓기보다 나머지 62개 claim의 annotation 감사를 이어간다.
2. 실제 외부 근거가 판단을 뒤집는 새로운 PR 후보를 확보한다.
3. 필요하다면 본 PR은 추가 source에 의해 추측이 근거 있는 판단으로 바뀌는 사례로 남긴다.
   이 방향은 검증된 safe/unsafe 대조 및 반대 context에서의 조건부 판단 실험이 필요하다.
4. 독립 표본과 annotation 확보 뒤 source selection 비교를 수행한다.

## 재현과 산출물

- 실행 전 명세: `GOFR1681_MULTI_REVIEW_PLAN_KO.md`
- 입력: `gofr1681_multi_packets_20260914.json` (부모 packet 해시 포함)
- 응답/trace/manifest/completion: `gofr1681_multi_review_20260914/`
- 판정: `gofr1681_multi_review_20260914/author_judgments.json`
- 입력 검증: 6 prompt SHA256, 동일 full diff, 단일 이슈 지시 제거 확인
- Python suite 347 passed, 변경 범위 Ruff 통과

Packet runner의 기본 single-issue schema는 유지하고 --multi-issue를 명시할 때만
새 schema를 적용했다. 이전 산출물은 수정하지 않았다.
