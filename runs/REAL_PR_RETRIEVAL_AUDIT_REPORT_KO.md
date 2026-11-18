# 실제 PR 17건: 공통 후보풀 source 선택 감사

2026-09-08. 기존 Go 개발 PR 17건 전체를 사용했다. 새 holdout이 아니며
이번 reviewer 호출은 0회다. 결함 recall·필수 evidence recall은 측정하지 않았다.

## 완료한 검증

- 17건 중 16건 실행. GoFR #1325는 보존 bundle 부재, #1355는 index scope partial.
  나머지 15건은 구조적 비교 가능하다.
- 소스가 붙은 graph node 16,918개 모두 해당 PR head git blob의 줄 번호·텍스트와 일치.
  동일 chunk 중복은 이후 제거했다. 소스 검증이지 semantic edge 정확성 검증은 아니다.
- 현재 head-edge/depth-3 열거에서 path truncation 0건. 과거 다른 graph 감사 결과를 대체하지 않는다.
- 완전히 diff-visible한 chunk는 0개지만 일부 줄이 diff와 겹치는 chunk는 많았다.
- 모든 16건 × 4예산에서 두 선택 chunk 집합이 달랐고 예산 위반은 없었다.

## 구조적 적격 15건의 기술통계

PR별 동일 가중 평균이다. 추가 source의 o200k_base token이며 동일 상한이지
정확히 동일한 실제 token 수를 맞춘 비교는 아니다.

| 상한 | 실제 token BM25 / semantic | 소스 줄 Jaccard | diff 중복 줄 비율 BM25 / semantic |
|---:|---:|---:|---:|
| 512 | 498.4 / 468.6 | 3.22% | 33.82% / 2.86% |
| 1024 | 1013.6 / 985.1 | 2.11% | 33.72% / 1.81% |
| 2048 | 2039.5 / 2013.1 | 1.56% | 33.16% / 2.81% |
| 4096 | 4089.3 / 4073.3 | 3.41% | 25.02% / 3.37% |

중첩 snippet을 다른 근거처럼 세지 않도록 `(file, line)` 집합 기준을 추가했다.
줄 수에는 빈 줄·주석도 포함되므로 정보량·관련성의 척도는 아니다.

Semantic 선택은 diff 밖 코드를 더 많이 가져오지만 유용성은 미확인이다.
GoFR #1617의 512-token packet에는 interface/config·wrapper 및 중첩 interface
snippet이 있다. 짧은 경로·낮은 비용 우선 정책의 선언 선호 효과를 배제할 수 없다.

## 해석 경계

- BM25는 repo-wide 검색이 아닌 SCIP 공통 후보풀 내부 reranking이다.
- SCIP use/reference를 검증된 call/data flow로 간주하지 않는다.
- paired bundle의 anchor 합집합과 head edge/source를 사용했다. base→head delta
  자체를 처치로 비교하지 않았으므로 원래 delta-subgraph 가설 검증은 아직 아니다.
- AACR defect 주석은 필수 context 정답이 아니다. 이번에는 선택 차이·소스 진위만 확인했다.
- 4개 repo의 기존 개발 표본이다. 일반화·신규성·review 개선을 주장하지 않는다.

## 다음 실험의 진입 조건

1. 선택 결과를 보기 전에 실제 결함 여부, diff-only 판정 가능성, 필수 외부 근거
   `(revision, file, lines)`와 인과적 역할을 annotation한다. 저장된 selection JSON은
   annotator에게 먼저 보여주지 않는다. 코드 줄을 임의로 oracle로 지정하지 않는다.
2. 이중 검토와 불일치 조정 후 context-dependent PR을 명시한다. 누락·불명확 PR도 기록한다.
3. 후보풀 coverage를 먼저 측정하고 내부 retrieval recall과 분리한다. Repo-wide BM25를 추가한다.
4. Head proximity / changed-relation / changed-relation+obligation을 동일 chunk·예산에서
   비교한다. Diff 중복 제거 및 비용 우선순위 ablation을 포함한다.
5. Oracle이 diff-only보다 도움이 되는지 검증한 뒤 선택기의 이득 회수를 reviewer recall과
   safe false alarm으로 측정한다.

현재 판정: 실제 PR의 선택 차이는 확보됐지만 review 개선 기여는 미입증.

## 산출물

- 실행 규칙: `REAL_PR_RETRIEVAL_AUDIT_PLAN_KO.md`
- Source/chunk/선택/해시: `real_pr_retrieval_audit17_20260908.json`
- 줄 중복 보정 통계: `real_pr_retrieval_audit17_20260908_summary.json`
- 구현: `ace_review/real_pr_retrieval.py`, `ace_review/real_pr_retrieval_summary.py`

원본 JSON은 내부 감사 패키지이며 독립 oracle annotation을 완료한 데이터셋이 아니다.
