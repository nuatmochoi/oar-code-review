# 문맥 효과 인과성 실험: Oracle, Placebo, Co-change, Targeted Test/Race

## 최종 결론

현재 관찰된 semantic 개선을 **관련 저장소 문맥의 정보 효과**라고 해석할 근거는 없다.

관련 history와 test-source는 Diff보다 semantic F1이 `+0.025` 높았지만, 동일 token 수의 다른 저장소 문맥(placebo)과 직접 비교하면 history의 차이는 `+0.010`, test-source의 차이는 오히려 `−0.015`였다. 모든 95% CI가 0을 포함했다.

따라서 가장 타당한 현재 해석은 다음과 같다.

> 추가 문맥은 reviewer를 보수적으로 만들어 FP를 줄일 수 있지만, 자동 검색된 문맥의 관련 정보가 결함 탐지를 개선했다는 증거는 아직 없다.

## Token-matched shuffled placebo

각 history/test-source 요청의 context token 수를 정확히 유지하면서, 다른 저장소에서 생성된 같은 종류의 블록으로 교체했다. donor는 항상 다른 repository이며 seed는 `20260902`다.

17 PR × 5조건 × 3회의 최종 비교 결과는 다음과 같다.

| 조건 | Strict TP/FP/F1 | Semantic TP/FP/F1 |
|---|---:|---:|
| Diff | 28 / 85 / **.166** | 38 / 75 / **.225** |
| History | 26 / 77 / **.159** | 41 / 62 / **.250** |
| History placebo | 28 / 81 / **.168** | 40 / 69 / **.240** |
| Test-source | 27 / 76 / **.165** | 41 / 62 / **.250** |
| Test-source placebo | 26 / 73 / **.160** | 43 / 56 / **.265** |

직접적인 관련 문맥 대 placebo의 semantic 차이는 다음과 같다.

| 비교 | ΔPrecision | ΔRecall | ΔF1 [95% CI] |
|---|---:|---:|---:|
| History − History placebo | +.031 | +.004 | +.010 [−.043, .064] |
| Test-source − Test placebo | −.036 | −.009 | −.015 [−.064, .027] |

즉 history는 placebo보다 아주 조금 높고 test-source는 더 낮지만, 둘 다 효과가 불확실하다. Test placebo의 높은 semantic F1도 사전 가설 없는 탐색 결과이며 유의한 독립 결과로 해석하지 않는다.

## Similar co-change screening

구현한 co-change retriever는 다음 절차를 사용한다.

1. merge-base 이전 최대 30개 commit에서 changed file과 함께 수정된 파일을 계산한다.
2. PR changed file 자체는 제외한다.
3. co-change 빈도가 높은 최대 24개 파일의 head-revision 코드를 chunking한다.
4. changed identifiers를 query로 BM25 검색해 상위 4개를 4K token 안에 제공한다.

17개 PR 모두 문맥이 생성됐다. 1회 screening 결과는 strict F1 `.177`, semantic F1 `.230`이었다. 같은 반복의 Diff는 각각 `.139`, `.226`이었다. 위치 점수에는 변동이 있었지만 semantic 차이는 `+.004 [−.076, .084]`에 불과해 추가 반복으로 확대하지 않았다.

이 결과는 최근 Go 연구에서 보고한 similar co-change의 이득과 다르다. 가능한 차이는 해당 연구가 no-context/context 후보를 **통합한 뒤 refinement-guided pruning**했다는 점이다. 본 실험은 동일 reviewer prompt에 한 종류의 문맥을 추가하는 방식이다. 따라서 다음 비교는 context-in-prompt가 아니라 독립 후보 생성 후 evidence 기반 reranking이어야 한다.

## Targeted test/race pilot

Repo Level gold가 포함된 10개 PR에서 다음 bounded 실행을 base와 patch에 각각 수행했다.

- changed LOC가 가장 많은 Go package 1개 선택
- PR이 변경한 `Test*` 함수가 있으면 해당 테스트만 `-run`으로 선택
- 일반 `go test -count=1`
- `go test -race -count=1`
- revision당 명령 timeout 90초

| 분류 | 일반 테스트 | Race 테스트 |
|---|---:|---:|
| No observed failure | 8 | 6 |
| Shared failure | 2 | 3 |
| Improvement | 0 | 1 |
| Regression | **0** | **0** |

기존 테스트와 race detector는 gold 결함을 새로 입증하는 regression trace를 만들지 못했다. 그러므로 이 pass/fail summary를 reviewer에게 추가하는 arm은 실행하지 않았다. 현재 필요한 것은 전체/패키지 테스트가 아니라 정적 의심점에서 assertion과 schedule을 생성하는 **targeted reproducer**다.

## Provisional Oracle annotation

Repo Level gold 11개를 대상으로 단일 AI-assisted 연구자 1차 주석을 만들었다. 논문 최종 수치가 아니며, 두 명의 인간 annotator 검증이 필요하다.

- 후보만으로 검증 가능: 9/11
- 추가 저장소 블록이 실제로 필요: 6/11
- Repo Level로 표시됐지만 Diff 자체로 충분: 3/11
- 필요한 증거가 retrieval 후보에서 누락: 1/11
- gold defect mechanism이 성립하지 않을 가능성: 1/11
- validity 판정: valid 8, uncertain 2, likely invalid 1
- 최소 증거 provider: LSP 4, test-source 1, history 1

중요한 사례는 다음과 같다.

- middleware order: CORS의 OPTIONS short-circuit를 보여주는 `TEST-02`가 최소 증거였다.
- old connserver compatibility: JWT를 항상 쓰던 이전 protocol을 보여주는 `HIST-02`가 필요했다.
- NATS retry state: `checkClient` 정의가 후보에서 빠져 있어 retrieval 실패로 판정했다.
- `errgroup.Wait`/deferred Close race: Wait는 모든 goroutine 종료 후 반환하므로 gold 설명이 likely invalid다.

## 연구 기여로 전환할 수 있는 지점

새 provider가 F1을 높였다는 주장은 현재 어렵다. 대신 다음 평가 프레임워크는 기존 연구와 구분되는 기여가 될 수 있다.

1. **Oracle evidence coverage**: 결함 검증에 필요한 최소 증거가 repository에 존재하는가.
2. **Evidence Recall@Budget**: 자동 retriever가 그 최소 증거를 budget 안에 회수하는가.
3. **Context utilization**: reviewer가 실제 증거 ID를 사용하며, 그 블록을 제거하면 claim이 사라지는가.
4. **Localization transfer**: 의미 인식 개선이 changed-line 지적으로 이어지는가.
5. **Token placebo effect**: 관련 문맥이 동일 길이의 무관 문맥보다 나은가.
6. **Dynamic discriminativeness**: 실행 문맥이 base/patch를 실제로 구분하는가.

이 구조에서는 “문맥이 많으면 성능이 내려간다”를 넘어, **문맥 실패가 retrieval, utilization, localization, non-specific token effect 중 어디에서 발생하는지** 설명할 수 있다.

## 다음 구현 우선순위

1. Oracle annotation을 인간 2인 독립 주석으로 확정한다.
2. 출력 schema에서 실제 제공된 evidence ID만 허용하고 잘못된 ID를 자동 reject한다.
3. Oracle block을 하나씩 제거하는 sufficiency/minimality counterfactual을 실행한다.
4. 각 provider가 별도 issue list를 생성하게 하고, Oracle evidence coverage와 confidence로 merge/prune한다.
5. concurrency/state 문제에는 `go test -race`만 실행하지 말고 정적 slice에서 goroutine schedule과 assertion을 생성한다.
6. gold validity가 valid인 context-needed subset과 독립 PR 표본에서 사전등록 확인 실험을 수행한다.

## 산출물

- 최종 placebo 포함 labels SHA-256: `592a5a6c3db5936c1702c67ffaff7a736564c893f1c07c41d5cf681c9253393a`
- 최종 placebo 분석 SHA-256: `5e70cc3c5a35d46966a187bd539c8a796a14cda87eee55cc45ec2752a315def1`
- Targeted test 요청 SHA-256: `44758784ad33ce7542e924fc34c6e48a989d2c6140b1efc8a8a7b0f3be4cba4b`
- Provisional Oracle annotation SHA-256: `0f7a69da3d80088be5aa29a52adebb57c15e134af91c2a020bc482d84fe8adb9`
