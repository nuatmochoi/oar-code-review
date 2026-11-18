# ACE-Review 추가 factorial ablation 보고서

실행일: 2026-09-02

## 요약

기존 evidence ladder에서 빠졌던 CodeQL-only, build/test-only,
CodeQL+build/test를 추가했다. LSP(L), CodeQL data flow(D), build/test(T)의
8개 조합이 모두 확보되어, 각 evidence의 baseline 대비 효과와 조건부 효과,
2-way/3-way interaction을 계산할 수 있게 되었다.

다만 아래 tool-isolation 문제가 확인되어 **엄격한 confirmatory causal ablation이
아닌 탐색적 factorial 비교**로 취급해야 한다. 단독 arm은 Diff를 넘지 못했고,
일부 조합의 회복/상호작용 신호도 다중비교 보정 후에는 유의하지 않았다.

## 1. 실행 및 재사용 범위

- 동일 코호트: 17 Go PR, 4 repositories, gold 75개.
- 기존 7 fixed arms의 결과는 변경하지 않았다.
- 새 조건: `dataflow`, `buildtest`, `dataflow_buildtest`.
- 새 request: 51개; 조건당 3회, clean reviewer prediction 153개.
- 모델: reviewer `gpt-5.6-sol`, judge `gpt-5.4`.
- reviewer 초기 capacity 오류 2건은 같은 repeat ID로 재시도하여 모두 성공했다.
- judge: 135회 호출, 빈 예측 18개 skipped, 오류 0.
- 생성 issue: 307개. Strict TP 84 / FP 223; semantic TP 130 / FP 177.
- 새 reviewer prompt token 근사치: 948,210; 성공 호출 wall-time 합 9,405.23초.
- 전체 fixed-arm label: 510개 = 17 PR × 10 conditions × 3 repeats.
- evidence는 기존 request에 보존된 동일 블록을 재조합했다. provider를 재실행하지 않았다.
- CodeQL evidence 15/17, build/test 17/17; L/D/T 모두 가능한 complete-case 13 PR.
- evidence budget 4,096 token이며 추가 세 arm의 budget omission은 0이다.

## 2. Factorial 구성

| Condition | LSP | CodeQL | Build/Test |
|---|---:|---:|---:|
| Diff | 0 | 0 | 0 |
| LSP | 1 | 0 | 0 |
| CodeQL | 0 | 1 | 0 |
| Build/Test | 0 | 0 | 1 |
| LSP+CodeQL | 1 | 1 | 0 |
| LSP+Build/Test | 1 | 0 | 1 |
| CodeQL+Build/Test | 0 | 1 | 1 |
| Structured-all | 1 | 1 | 1 |

BM25와 stuffed-all은 factorial 밖의 비교 arm으로 유지했다. 이 설계에서 "주효과"는
다른 두 factor가 없는 baseline에서의 simple effect를 뜻한다. 모든 다른 factor 수준에
평균한 factorial average main effect와는 구분한다.

## 3. Strict 결과

분모는 gold 75 × 3회 = 225이며, 아래 token은 UTF-8 byte/4 근사치다.

| Condition | TP | FP | Precision | Recall | F1 | 평균 prompt token |
|---|---:|---:|---:|---:|---:|---:|
| Diff | 34 | 78 | .304 | .151 | .202 | 5,953 |
| LSP | 29 | 78 | .271 | .129 | .175 | 7,638 |
| CodeQL | 28 | 82 | .255 | .124 | .167 | 6,288 |
| Build/Test | 26 | 75 | .257 | .116 | .160 | 5,981 |
| LSP+CodeQL | 35 | 77 | .313 | .156 | .208 | 7,979 |
| LSP+Build/Test | 33 | 69 | .324 | .147 | .202 | 7,674 |
| CodeQL+Build/Test | 30 | 66 | .313 | .133 | .187 | 6,324 |
| Structured-all | 36 | 64 | .360 | .160 | .222 | 8,016 |

독립적으로 추가한 LSP, CodeQL, build/test는 모두 Diff보다 낮은 point estimate를 보였다.
CodeQL과 build/test를 단독으로 넣는 것보다 LSP와 결합했을 때 점수가 높았다. 그러나
단독/조건부 효과의 PR-bootstrap 95% CI는 모두 0을 포함한다.

## 4. Semantic-anywhere 결과

| Condition | TP | FP | Precision | Recall | F1 | Relocated |
|---|---:|---:|---:|---:|---:|---:|
| Diff | 48 | 64 | .429 | .213 | .285 | 14 |
| LSP | 43 | 64 | .402 | .191 | .259 | 14 |
| CodeQL | 47 | 63 | .427 | .209 | .281 | 19 |
| Build/Test | 40 | 61 | .396 | .178 | .245 | 14 |
| LSP+CodeQL | 48 | 64 | .429 | .213 | .285 | 13 |
| LSP+Build/Test | 46 | 56 | .451 | .204 | .281 | 13 |
| CodeQL+Build/Test | 43 | 53 | .448 | .191 | .268 | 13 |
| Structured-all | 43 | 57 | .430 | .191 | .265 | 7 |

CodeQL-only는 strict F1 .167이지만 semantic F1 .281로 Diff .285에 가깝다.
위치 이동 19건이 가장 많아, "CodeQL이 결함을 전혀 못 찾았다"보다 "비슷한 결함을
다른 줄에서 지적한 경우가 많았다"는 해석이 더 적절하다. 이 해석은 자동 judge에
의존하므로 human audit이 필요하다.

## 5. 조건부 효과와 interaction

5,000회 PR paired bootstrap에서 각 PR의 세 반복을 함께 재표집했다.

`L×D = F1(LD) − F1(L) − F1(D) + F1(Diff)`

이는 aggregate F1 척도 위의 difference-in-differences이며, 모델 내부의 attention이나
reasoning 메커니즘을 직접 관찰한 것은 아니다.

| Contrast | Strict ΔF1 [95% CI] | Semantic ΔF1 [95% CI] |
|---|---|---|
| LSP 추가, baseline | -.027 [-.070, .016] | -.026 [-.075, .012] |
| CodeQL 추가, baseline | -.035 [-.091, .011] | -.004 [-.047, .031] |
| Build/Test 추가, baseline | -.042 [-.142, .033] | -.040 [-.088, .008] |
| CodeQL 추가, LSP 조건 | +.033 [-.008, .079] | +.026 [-.011, .073] |
| Build/Test 추가, LSP 조건 | +.027 [-.020, .070] | +.022 [-.020, .060] |
| Build/Test 추가, CodeQL 조건 | +.020 [-.016, .062] | -.013 [-.054, .020] |
| LSP × CodeQL | +.068 [.004, .145] | +.030 [-.029, .106] |
| LSP × Build/Test | +.069 [-.022, .169] | +.062 [.003, .121] |
| CodeQL × Build/Test | +.062 [-.016, .168] | +.027 [-.035, .087] |
| LSP × CodeQL × Build/Test | -.075 [-.185, .034] | -.069 [-.151, .018] |

Unadjusted interval에서 LSP×CodeQL(strict), LSP×Build/Test(semantic)가 0을 제외한다.
그러나 bootstrap tail-area 근사 p에 10개 contrast별 Holm 보정을 적용하면 각각
.364, .396으로, **보정 후 유의한 F1 contrast는 없다**.

13-PR complete-case에서 LSP×CodeQL strict CI는 [-.019, .148]로 0을 포함한다.
LSP×Build/Test semantic CI는 [.006, .145]이지만 Holm p=.338이다.
Repository-cluster bootstrap도 별도 저장했으나 cluster가 4개뿐이므로 강한 근거가 아니다.

## 6. Adaptive 재평가

후보 arm을 기존 7개에서 10개로 확대하고 동일한 nested repository-disjoint CV를 재실행했다.

- strict: TP 30, FP 72, precision .294, recall .133, F1 .183.
- semantic: TP 42, FP 60, precision .412, recall .187, F1 .257.
- strict ΔF1 vs Diff: -.018, 95% CI [-.068, .030].
- semantic ΔF1 vs Diff: -.028, 95% CI [-.071, .001].
- 선택: structured-all 5, BM25 3, LSP+build/test 3, CodeQL+build/test 3,
  build/test 2, stuffed-all 1.

후보를 추가해도 현 kNN selector는 Diff를 넘지 못했다.

## 7. 중요한 타당성 한계

1. **외부 도구 격리가 검증되지 않았다.** reviewer는 임시 read-only 디렉터리에서 실행됐지만
   tool 자체가 비활성화되지 않았다. capacity 오류 두 건의 stderr에서 추가 파일 탐색과
   웹 검색 시도가 확인됐다. 성공 호출의 전체 trace는 저장하지 않아 외부 문맥 사용 여부를
   판별할 수 없다. 기존 arm도 같은 harness였다. 따라서 이 실행만으로 제공 evidence의
   순수한 인과 효과를 주장할 수 없다. 모든 arm의 tool-disabled 재실행과 trace 보존이 필요하다.
2. 17 PR/4 repositories 및 13-PR complete-case로 검정력이 작다. 세 반복은 독립 PR을
   늘린 것이 아니다.
3. token은 동일 상한만 적용했고 실제 길이를 일치시키지 않았다. natural-size effect와
   evidence 종류의 효과를 완전히 분리하지 못한다.
4. build/test evidence는 13개 no-observed-failure, 4개 shared-failure이며 새 regression
   실패 로그 사례가 없다. build/test 일반의 유효성을 부정할 수 없다.
5. CodeQL은 변경 줄 최대 64개, 반환/호출 인자/타입 변환 sink, 최대 5개 path를 제공했다.
   이는 특정 retrieval 설정의 결과이지 CodeQL 전체 능력의 평가가 아니다. Path query는
   경로를 보여주는 것이며 모든 경로의 실행 가능성을 완전히 검증했다는 뜻이 아니다.
6. automated semantic judge, positive-only PR, 최대 5 issue 제한은 이전 실험과 동일하다.
7. 새 arm은 기존 arm 이후 같은 날 실행됐다. 시간순 실행 및 모델 서비스 변동이 통제되지 않았다.

## 8. 결론과 다음 실행

이번 추가 실행은 빠진 단독 arm을 채웠고, L/D/T의 모든 조합을 비교할 수 있게 했다.
관찰된 패턴은 "CodeQL 단독이 우수하다"가 아니라 "단독 evidence는 Diff를 넘지 못하며,
조합에서 회복되는 탐색적 신호가 있다"이다. 그 신호는 다중비교 보정 후 확정적이지 않다.

다음 엄격한 실행에서는 tool-disabled reviewer/judge, 전체 trace 저장, 동일 evidence hash,
arm 실행 순서 무작위화, token-matched 분석을 적용하고 더 많은 PR/repository로 확장해야 한다.

## 산출물

- `runs/factorial_ablation_manifest.json`
- `runs/aacr_go_pure_17_ablation_requests.jsonl`
- `runs/aacr_go_pure_17_ablation_predictions.jsonl`
- `runs/aacr_go_pure_17_ablation_labels.jsonl`
- `runs/aacr_go_pure_17_factorial_labels.jsonl`
- `runs/aacr_go_pure_17_factorial_analysis.json`
- `runs/aacr_go_pure_17_factorial_contrasts_{strict,semantic}{,_repo}.json`
- `runs/aacr_go_pure_17_factorial_analysis_with_adaptive_cv.json`
