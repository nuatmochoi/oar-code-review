# GoFR1681: 최초 reviewer context pilot 결과

2026-09-09. 이전 관측 PR 1개, 4조건 × 3반복. gpt-5.6-sol low.
12/12 생성 성공, trace 감사상 도구 호출 0. 원본 response/trace/prompt hash 보존.
이것은 독립 PR 12개나 독립 oracle 평가가 아니다.

## 결과: 목표 회귀의 최우선 보고

Prompt는 단일 최우선 functional defect만 요청했다. 아래는 모든 결함 recall이 아니라
"OPTIONS 로깅 순서 회귀를 top-1으로 보고한 횟수"다. 작성자가 응답을 읽고 분류했다.

| 조건 | 추가 source tokens | 목표 회귀 top-1 | 다른 probe panic 주장 |
|---|---:|---:|---:|
| Full diff | 0 | 0/3 | 3/3 |
| +CORS | 164 | 1/3 | 2/3 |
| +CORS+route | 385 | 3/3 | 0/3 |
| Placebo | 382 | 0/3 | 3/3 |

목표 회귀를 보고한 4응답 모두 CORS의 OPTIONS early-return과 Logging 순서 변경을
연결했다. 다른 8응답은 probe 경로에서 logging wrapper를 우회하면서 panic recovery도
우회한다는 별도 주장을 보고했다. 그 주장을 이 실험에서 참/거짓 판정하지 않았으므로
false positive로 세지 않는다. 전체 12응답의 verdict는 introduced_defect였다.

## 관측한 것과 관측하지 못한 것

이번 개발 표본에서 관련 source 추가 후 목표 회귀를 우선 보고하는 빈도가 높아졌다.
단순히 verdict만 세면 조건 차이가 사라지므로 target-specific 평가가 필요하다.

그러나 이를 review recall 0→100% 또는 route context의 필수성 입증으로 부르면 안 된다.

- 한 개만 보고하라는 출력 제한 때문에 결함 탐지 능력과 우선순위 효과가 섞였다.
  Diff-only가 목표 회귀를 전혀 추론할 수 없는지 확인하지 않았다.
- +CORS+route의 응답도 catch-all 매칭 구조를 명시적으로 설명하지 않았다.
  Route 내용을 실제로 활용했다고 단정할 수 없다. Context 길이·주의 유도 효과와
  route의 의미적 효과를 분리하려면 route-only 및 CORS+길이맞춤 placebo 대조가 필요하다.
- Placebo는 logging/level.go의 whole-line prefix이며 3-token 차이가 있다.
  단일 placebo와 3반복만으로 길이 효과를 완전히 배제할 수 없다.
- 정상 PR 대조군 없음. False-alarm rate·precision·F1 계산 불가.
- 작성자 선택 evidence와 작성자 판정. 신규 holdout·독립 annotation·유의성 주장 불가.
- Location은 별도이며 이번에는 strict ±1-line metric을 계산하지 않았다.
- 이 결과는 BM25/SCIP/CodeQL 비교가 아니며 해당 선택기의 기여가 아니다.

## 다음 실험

1. 출력 제한을 복수 이슈로 바꾸어 목표 회귀의 검출과 우선순위를 분리한다.
2. Diff / CORS / route-only / CORS+route / CORS+placebo를 같은 기준으로 비교한다.
3. 실제 순서 복원 또는 route 조건 대조에서 과도한 OPTIONS 경고가 발생하는지 확인한다.
4. 위 개발 분석을 마친 후 evidence를 동결하고 독립 PR로 검증한다.

현재 표현 가능한 결론: **한 개발 PR에서 관련 문맥 추가가 검증된 회귀의 top-1 보고를
늘리는 관측을 얻었다. 일반적인 결함 recall 향상이나 route 근거의 인과적 기여는 미입증.**

## 재현 및 검증

- 입력: `gofr1681_review_packets_20260909.json`
- 실행 명세: `GOFR1681_REVIEW_PILOT_PLAN_KO.md`
- 원본 출력: `gofr1681_review_20260909/`
- 작성자 분류: `gofr1681_review_20260909/author_judgments.json`
- 공통 full diff 해시와 4개 prompt 해시 재검증. Gold IDs 없음, claim/witness 결과는 builder 입력에 미포함.
- Python suite 346 passed, 변경 범위 Ruff 통과.

기존 packet runner의 기본 24회는 유지하면서 명시적인 expected-calls=12를 지원하도록 확장했다.
