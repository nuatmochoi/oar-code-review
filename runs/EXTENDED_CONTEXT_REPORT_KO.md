# AACR-Bench 확장 문맥 실험: test-source, pre-change history, Oracle Context

## 결론

현재 결과만으로 **“Diff만으로 찾지 못한 결함을 저장소 문맥이 더 잘 리뷰한다”**고 일반화할 수는 없다.

- Strict F1은 Diff `0.166`, history `0.159`, test-source `0.165`로 개선되지 않았다.
- 위치를 무시한 semantic F1은 Diff `0.225`에서 history/test-source `0.250`으로 `+0.025` 상승했다.
- 두 문맥 조건 모두 semantic TP가 `38 → 41`, FP가 `75 → 62`로 바뀌었지만, paired bootstrap 95% CI는 0을 포함한다.
- 따라서 현재 관찰은 **결함 의미 인식과 오탐 억제에는 약한 개선 신호가 있지만, 정확한 changed-line localization은 개선되지 않았다**로 해석해야 한다.
- reviewer가 정확히 provider ID를 인용한 비율도 낮아, 개선을 해당 문맥의 내용에 직접 귀속하기 어렵다. Oracle evidence annotation과 placebo 실험이 필요하다.

## 연구 질문

- RQ1. 기존 테스트 코드는 변경된 코드의 암묵적 계약을 제공해 결함 탐지를 개선하는가?
- RQ2. 변경 이전의 파일 이력은 설계 의도나 호환성 제약을 제공해 결함 탐지를 개선하는가?
- RQ3. 개선이 있다면 결함 의미 인식과 changed-line localization 중 어디에서 발생하는가?
- RQ4. 자동 retrieval이 실패한 것인지, 적절한 증거가 있어도 reviewer가 사용하지 못한 것인지 분리할 수 있는가?

## 조건

| 조건 | 입력 | 선택 규칙 |
|---|---|---|
| Diff | 전체 PR diff | 추가 문맥 없음 |
| Test-source | Diff + 기존 Go 테스트 | head revision의 변경되지 않은 `*_test.go`를 changed identifiers로 BM25 검색, 상위 3개 100-line chunk |
| History | Diff + 변경 이전 이력 | merge-base에서 changed files의 과거 커밋만 조회, 제목의 symbol overlap과 recency로 정렬, 상위 3개 bounded patch |
| Test+History | 위 두 문맥 | 1회 screening만 수행 |

누수 방지 규칙은 다음과 같다.

- Test-source에서 PR이 수정한 테스트 파일은 제외했다. 해당 내용은 이미 Diff에 존재한다.
- History 검색의 시작점은 각 PR의 merge-base다. PR commit과 이후 commit은 검색할 수 없다.
- gold claim, gold 위치, human review comment는 retrieval query에 사용하지 않았다.
- context token budget은 조건당 4,096이다. median은 test-source 2,165, history 1,631, 결합 3,601 token이다.

## 실험 통제

- 데이터: pure-Go AACR-Bench PR 17개, gold issue 75개.
- 주 조건: 17 PR × 3조건 × 3회 = reviewer 153회.
- screening 결합 조건: 17회. 총 reviewer 실행 170회.
- reviewer: `gpt-5.6-sol`; semantic judge: `gpt-5.4`.
- reviewer의 `shell_tool`, `unified_exec`, browser, computer-use, apps를 feature level에서 비활성화했다.
- 모든 reviewer 실행은 JSONL event trace를 저장했고, 170/170 성공, tool-call event 0회였다.
- judge는 161회 호출, 오류 0회였다. 빈 예측 9개는 판정을 생략했다.
- 1회 screening 결과를 보고 history/test-source를 추가 반복했으므로 본 결과는 exploratory다. 확인 실험의 유의확률로 해석하면 안 된다.

공식 OpenAI Responses API는 `tool_choice: none`을 제공하지만, 현재 harness는 Codex CLI feature disable과 trace audit을 사용했다. 관련 API 동작은 [OpenAI Responses API 문서](https://developers.openai.com/api/reference/cli/resources/responses/methods/create)에 명시돼 있다.

## 전체 결과

세 번의 반복을 합쳐 집계했다. Gold 225는 75개 gold를 세 번 평가한 값이다.

| 조건 | Strict TP/FP | Strict P/R/F1 | Semantic TP/FP | Semantic P/R/F1 |
|---|---:|---:|---:|---:|
| Diff | 28 / 85 | .248 / .124 / **.166** | 38 / 75 | .336 / .169 / **.225** |
| History | 26 / 77 | .252 / .116 / **.159** | 41 / 62 | .398 / .182 / **.250** |
| Test-source | 27 / 76 | .262 / .120 / **.165** | 41 / 62 | .398 / .182 / **.250** |

Diff 대비 paired task bootstrap 10,000회의 F1 차이는 다음과 같다.

| 조건 | Strict ΔF1 [95% CI] | Semantic ΔF1 [95% CI] |
|---|---:|---:|
| History | −.007 [−.051, .043] | +.025 [−.010, .073] |
| Test-source | −.001 [−.048, .047] | +.025 [−.014, .070] |

Semantic precision 차이는 history `+.062 [−.007, .152]`, test-source `+.062 [−.001, .128]`로 0에 매우 가깝지만 여전히 포함한다.

## “Diff가 못 찾은 결함” 전이

각 `(PR, repeat)`에서 gold set을 직접 비교했다.

| 비교 | Strict 새 탐지 / 손실 | Semantic 새 탐지 / 손실 |
|---|---:|---:|
| History vs Diff | 6 / 8 | 9 / 6 |
| Test-source vs Diff | 9 / 10 | 13 / 10 |

Semantic 기준으로는 순증가가 각각 +3이므로 문맥 조건만 맞힌 사례가 존재한다. 그러나 같은 gold가 다른 반복에서는 Diff도 맞히거나 문맥 조건이 놓치는 경우가 있어, 이 전이만으로 문맥의 인과 효과를 주장할 수 없다.

특히 Repo Level gold의 반복 합산 recall은 다음과 같다.

| 조건 | Strict recall | Semantic recall |
|---|---:|---:|
| Diff | 6/33 = .182 | 6/33 = .182 |
| History | 5/33 = .152 | 6/33 = .182 |
| Test-source | 7/33 = .212 | 7/33 = .212 |

즉 우리가 가장 개선하고 싶었던 Repo Level에서 history는 개선이 없고, test-source는 33회 중 1회만 증가했다. 반대로 File Level strict recall은 Diff `.118`, history `.088`, test-source `.069`로 하락했다.

## 결합 조건 screening

1회 screening에서 semantic F1은 Diff `.226`, history `.255`, test-source `.252`, test+history `.230`이었다. 두 문맥을 단순히 합치면 개별 조건보다 나빠졌다. 이는 AACR-Bench의 “문맥이 많으면 오히려 성능이 하락할 수 있다”는 결과를 그대로 확장한다. 문맥의 양보다 관련 증거 선택과 예산 배분이 중요하다.

## 문맥을 실제로 사용했는가

Semantic matched comment 41개 중 정확한 provider block ID를 인용한 수는 다음과 같다.

- History: 8/41 (19.5%)
- Test-source: 2/41 (4.9%)

인용하지 않아도 내용을 사용했을 가능성은 있으므로 이는 사용률의 하한이다. 다만 Repo Level 순증가 사례를 확인하면 다수 comment가 provider ID가 아니라 Diff evidence만 인용했다. 따라서 현재의 `+0.025 semantic F1`을 “history/test가 원인을 밝혀 주었다”는 근거로 쓰면 안 된다. 토큰 추가가 생성 분포나 보수성만 바꿨을 가능성도 남는다.

## Oracle Context 설계와 현재 상태

Oracle은 모델에 gold claim을 주는 조건이 아니다. 각 gold별로 자동 provider 후보 중 결함을 검증하는 **최소 충분 증거 집합**을 사람이 선택하는 진단용 상한선이다.

Repo Level 11개에 대해 BM25, LSP, CodeQL data-flow, build/test, test-source, pre-change history의 후보 합집합을 생성했다. 모든 gold에 후보는 존재했지만, 후보가 실제로 충분한지는 아직 주석되지 않았다.

Oracle 실험은 다음 네 값을 분리한다.

1. Oracle evidence coverage: 후보 집합에 충분한 증거가 실제로 존재하는 비율.
2. Evidence Recall@4K: 자동 selector가 Oracle 최소 집합을 token budget 안에 회수한 비율.
3. Oracle reviewer recall: 최소 증거가 주어졌을 때 모델이 gold를 찾는 비율.
4. Retrieval gap: Oracle reviewer recall − 자동 retrieval reviewer recall.

이 분해를 해야 `retriever가 못 찾음`, `증거는 찾았지만 reviewer가 사용하지 않음`, `충분한 저장소 증거 자체가 없음`을 구분할 수 있다.

## LSP/AST/CodeQL 이외의 방향

이번에 실행한 test-source와 pre-change history 외에 우선순위가 높은 방향은 다음과 같다.

1. **Similar co-change context**: 과거에 changed file/symbol과 함께 수정된 파일의 현재 코드를 검색한다. 단순 최근 history보다 설계상 관련 파일을 직접 회수할 가능성이 높다. 2026년 Go 연구는 neighboring, LSP, IR-based similar co-change를 비교하고, no-context와 context 후보를 통합·pruning하는 구성이 가장 좋았다고 보고한다([논문](https://arxiv.org/abs/2606.01859)).
2. **Review exemplar / rationale memory**: 유사 변경과 과거 리뷰 지적을 함께 검색한다. LAURA는 review exemplar retrieval, context augmentation, systematic guidance의 결합을 제안한다([논문](https://arxiv.org/abs/2512.01356)). 동일 저장소의 merge-base 이전 리뷰만 허용해야 한다.
3. **Contract/config/schema context**: OpenAPI, protobuf, JSON schema, CLI help, README의 명시적 계약을 symbol과 endpoint로 연결한다. 현재 BM25 source-only가 놓치는 외부 동작 계약을 겨냥한다.
4. **Targeted dynamic evidence**: 전체 build/test 성공 여부가 아니라 changed symbol과 연결된 테스트만 실행하고, race detector, sanitizer, property test 또는 실패 trace를 증거로 사용한다.
5. **Dependency/call slice**: CodeQL security-flow query와 별도로, changed value의 backward/forward slice, call chain, state-write/read relation을 작은 그래프로 직렬화한다.

관련 최근 연구도 semantic repository search와 실제 개발 workflow의 결합을 별도 문제로 다루고 있다([Rethinking Code Review Workflows](https://arxiv.org/abs/2505.16339)). 따라서 다음 실험은 provider를 무작정 더 합치기보다 Oracle 최소 증거를 기준으로 retrieval을 학습·평가해야 한다.

## 다음 확인 실험

1. Repo Level 11개 Oracle 후보를 두 명이 독립 주석하고 합의한다.
2. `Diff`, `Oracle`, `auto-retrieval`, `token-matched shuffled context` 네 조건을 비교한다.
3. 출력의 `evidence_ids`를 실제 제공 ID로 제한하고, 인용 블록 제거 시 claim이 유지되는지 counterfactual 검사를 한다.
4. Oracle coverage가 높은 gold만 대상으로 retrieval recall과 reviewer recall을 따로 보고한다.
5. Similar co-change와 targeted test trace를 추가하되, 각각 단독 arm으로 먼저 평가한 뒤 결합한다.
6. 독립 PR 표본에서 조건과 분석을 사전등록해 confirmatory experiment를 수행한다.

## 산출물 무결성

- 요청 SHA-256: `9eb26f616f21a0b7dc9d17c0cd67bac988954182c598d96113a9cf48e1e3b873`
- 반복 1/2/3 예측 SHA-256: `ab14608ed6bbec0bf5c3fc68fbb230e50064c9e95eb2c1eed1e14fefc2c0a348`, `8b1815c2dd4120be8905c7c620cb63b776b2f3b9268bb267543f278cd4084dc5`, `b1b76e670bae87bdceb40a07c6429dccb8a0c4679b0b5367110ec6eecebf5ca7`
- 주 조건 label SHA-256: `1e43dc0755faa7ca5825035d50938672be7beb7e844bd6d1cbd9be94b5badf6b`
- Oracle 후보 SHA-256: `412b11a008a2c2407139cf2a56d6394ba1852a9619e83799909e3fb3d0bbb47e`
