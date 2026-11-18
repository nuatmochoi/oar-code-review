# 자동 producer 회수와 선택적 probe: TypeScript-Go 24회 결과

2026-09-07. `adaptive_tsgo_case1`, `adaptive_tsgo_probe1`, `adaptive_tsgo_review1`.

## 결론

**자동으로 회수한 producer source가 diff-only에서 전혀 찾지 못한 결함을 3/3 탐지하게 했고 safe에서는 0/3 오경보를 유지했다. 자동 source는 oracle과 정확히 일치했다. 그러나 runtime probe는 추가 이득이 없어서 현재 uncertainty trigger는 과도하다.**

| 조건 | Unsafe 정확 탐지 | Safe 프로토콜 오경보 | 같은 반복에서 탐지 + safe 무경고 |
|---|---:|---:|---:|
| Diff only | 0/3 | 0/3 | 0/3 |
| Automatic static producer | 3/3 | 0/3 | 3/3 |
| Automatic static + adaptive probe | 3/3 | 0/3 | 3/3 |
| Oracle producer | 3/3 | 0/3 | 3/3 |

자동 retrieval gate, static review gate, probe discrimination gate는 통과했다. Adaptive review gate는 완전 동률을 추가 이득 없음으로 정의했으므로 통과하지 못했다. 결과를 본 뒤 gate를 완화하지 않았다.

## 이 사례가 보여주는 것

Consumer diff는 `manualTests.has(testName)`으로 수동 테스트를 거른다. 여기서 `testName`은 source 파일명에서 `.ts`와 점을 제거하고 첫 글자를 대문자로 만든 값이다.

Unsafe companion의 producer는 CLI로 받은 생성 파일명 자체를 목록에 저장한다.

```text
producer stored key: alpha_test.go
consumer lookup key: Alpha
```

따라서 수동 디렉터리로 옮긴 테스트가 다음 변환에서 다시 생성된다. Safe companion은 저장 전에 같은 canonical key `Alpha`로 변환한다.

Diff-only safe/unsafe prompt는 **byte-level hash까지 동일**하다. 같은 diff만 보는 reviewer는 두 world를 원칙적으로 구분할 정보가 없다. 실제 응답도 두 variant 모두 세 번 전부 빈 결과였다. 반면 companion producer source를 추가하면 unsafe 3/3에서 정확한 key mismatch를 찾고 safe 3/3에서는 경고하지 않았다.

이는 한 파생 개발 pair에 한정되지만, 사용자가 원했던 다음 문장을 가장 직접적으로 보이는 현재 증거다.

> Diff만으로 판별 불가능한 defect가 있고, 변경된 consumer와 실제 unchanged producer를 연결한 context가 safe/unsafe 판별 정보를 제공한다.

## Counterfactual 구성의 경계

원자료는 [microsoft/typescript-go PR 1446](https://github.com/microsoft/typescript-go/pull/1446)과 commit `892b4a3f...`이다. 실제 PR 전체에는 `makeManual.mts`도 새 파일로 포함되어 있으므로 원래 full diff만 봐도 producer를 읽을 수 있다.

이번 쌍은 연구 질문을 분리하기 위해 consumer 변경만 target diff로 유지하고 producer를 base/head에서 변하지 않는 companion으로 재배치했다.

- Unsafe는 실제 producer의 raw filename 저장 방식을 보존한다.
- Safe는 파일 이동에는 원래 filename을 사용하되 목록에는 consumer와 같은 canonical key를 저장하는 합성 companion이다.
- Safe/unsafe의 target base, target head, target diff는 동일하다.
- Gold를 본 뒤 사례를 골랐으므로 blind holdout이 아니다.

따라서 자연 발생 defect의 benchmark 성능이나 AACR 공식 성적으로 세면 안 된다. 기존 Click/AnyIO/Requests와 다른 repository에서 정책의 작동 가능성을 본 author-known development evaluation이다.

## 자동 producer 회수

Selector는 정답 위치나 safe/unsafe label을 읽지 않았다.

1. Diff 추가 줄에서 identifier를 수집한다.
2. resource 이름 형태인 `manualTestsPath`를 우선 anchor로 선택한다.
3. snapshot의 다른 TS/MTS 파일에서 같은 anchor가 `writeFileSync`, `appendFileSync`, `renameSync` write sink에 들어가는 곳을 찾는다.
4. brace-balanced module function을 회수한다.
5. 그 함수가 호출하는 같은 파일의 helper를 한 단계 확장한다.

Unsafe에서는 `makeManual.mts::main`, safe에서는 `normalizeManualTestName`과 `main`을 선택했다. 두 variant 모두 사람이 사전에 지정한 oracle 함수 집합과 file/line/content가 정확히 같았다. Reviewer 결과도 automatic과 oracle이 각각 unsafe 3/3, safe 0/3으로 같았다.

다만 이 selector는 현재 lexical resource anchor + 간단한 syntax extraction prototype이다. SCIP/LSP call graph, base-head semantic index delta, sound data flow는 아니다. 정책도 사례를 선택한 뒤 만들었으므로 다른 이름·write API에 대한 과적합 위험이 크다.

## Probe가 왜 추가 이득이 없었나

현재 uncertainty rule은 producer/consumer 사이에 case conversion, substring, replace, basename 같은 문자열 정규화가 있으면 probe를 실행한다. 실제 sandbox probe는 neutral input에서 다음을 확인했다.

| Variant | 실제 producer 저장 | Consumer replay 조회 | Membership |
|---|---|---|---:|
| Safe | `Alpha` | `Alpha` | True |
| Unsafe | `alpha_test.go` | `Alpha` | False |

Probe 자체는 두 world를 정확히 구분했지만 automatic-static reviewer가 이미 3/3을 맞혔다. 따라서 실행 비용과 context만 추가했고 검출 이득은 0이다.

이번 pure string chain은 source에서 직접 계산 가능하다. 반대로 앞선 Click의 `isinstance(stream, typing.BinaryIO)`는 모델이 source만 보고 실행 의미를 반복 오판했고 runtime 관찰이 2/3→3/3을 만들었다. 두 결과를 합치면 더 좁은 trigger가 필요하다.

- literal/case/suffix/path normalization이 모두 보이면: 정적 symbolic evaluation, probe 생략.
- typing alias의 runtime 의미, reflection, dynamic dispatch, environment-dependent provider, opaque library call이면: bounded probe 후보.
- producer나 consumer가 회수되지 않으면: probe로 답을 꾸미지 않고 provenance unresolved로 남김.

즉 adaptive selection의 핵심은 “문자열 변환이 있다”가 아니라 **static evidence로 contract equality를 결정할 수 있는가**여야 한다.

## 실험 통제

- 2 variants × 3 repeats × 4 arms = 신규 final review 24회. proposal 생성 호출은 없다.
- 네 arm 모두 전체 prompt를 정확히 4,096 o200k reference tokens로 맞췄다.
- Padding은 근거가 아니라고 표시했고 citation enum에서도 제외했다. 의미 밀도·문체·attention까지 동일한 placebo는 아니다.
- Diff-only safe/unsafe는 task id를 제외한 renderer 입력과 최종 prompt hash가 동일하다. Task id와 condition은 renderer에 출력되지 않는다.
- 모델 `gpt-5.6-sol`, 동일 verifier, 도구 비활성화, timeout 300초, jobs=4, 자동 재시도 없음.
- 입력·schema·prompt·runner와 판정 규칙을 호출 전에 동결했다. Generation/schema 오류와 모델 도구 호출은 모두 0이다.
- 24개 응답과 지적 9개를 모두 읽고 저자가 판정했다. 9개 모두 unsafe의 target mechanism이며 다른 지적은 없었다.
- 독립·블라인드 adjudication이 아니다. 반복은 독립 PR이 아니며 통계적 유의성이나 AACR 전체 precision/recall/F1을 뜻하지 않는다.

## 비용

| 조건 | 새 호출 | Prompt reference tokens 합 | 출력 JSON reference tokens 합 | 호출 시간 합(초) |
|---|---:|---:|---:|---:|
| Diff only | 6 | 24,576 | 30 | 61.1 |
| Automatic static | 6 | 24,576 | 756 | 59.4 |
| Adaptive probe | 6 | 24,576 | 755 | 72.0 |
| Oracle producer | 6 | 24,576 | 699 | 65.6 |

Reference tokenizer 수치이며 billed/reasoning token은 아니다. 병렬 호출 시간 합은 wall-clock이 아니다. source 준비와 probe 비용은 포함하지 않았다.

## Contribution 판단 업데이트

현재까지 가장 강한 긍정 결과는 identity field나 runtime 자체가 아니라 **production provenance를 회수한 정적 context**다.

후보 contribution은 다음처럼 정리할 수 있다.

> Base/head semantic evidence graph에서 변경된 consumer가 의존하는 unchanged producer를 resource/call/argument provenance로 회수하고, contract를 결정하는 최소 subgraph만 리뷰에 제공한다. 정적으로 결정 불가능한 edge에만 실행 근거를 요구한다.

이번 결과만으로 논문 contribution이 확정되지는 않는다.

- 사례 선택과 selector 규칙 설계가 분리되지 않았다.
- 자동 selector는 아직 SCIP 기반 semantic index 비교가 아니다.
- 파생 counterfactual 한 family뿐이다.
- Runtime trigger는 이번 사례에서 불필요하게 실행됐다.
- 새 정책을 고정한 뒤 untouched repository/defect에 적용하지 않았다.

## 다음 우선순위

1. `static_decidable / runtime_required / unresolved` 세 상태를 내는 uncertainty classifier를 만든다. 현재 TypeScript 사례는 static_decidable, Click typing 사례는 runtime_required가 되어야 한다.
2. 이름이 달라도 같은 resource를 가리키는 import/definition link를 SCIP로 연결하고, AST로 write/read argument binding을 보강한다.
3. policy를 먼저 동결한 뒤 최소 3개 untouched family에 적용한다: 값 정규화, option forwarding, provider selection 또는 alias/rebinding.
4. `diff-only / generic BM25 / automatic minimal subgraph / subgraph+selective probe / oracle`를 같은 evidence budget으로 비교한다.
5. unsafe recall과 safe false alarm을 paired metric으로 보고, 독립 판정과 repository-disjoint split을 추가한다.

## 산출물

- 사전 계획
- Counterfactual source와 동일 target diff
- 자동 선택·oracle 대조
- 실제 probe 관찰
- 24개 원본 리뷰
- 항목별 판정
- 집계·비용
- 결과 무결성
- 검증 기록

테스트 우선으로 selector, pair builder, sandbox probe, review composer, analyzer의 미구현 실패를 확인한 뒤 구현했다. 기존 동결 run은 수정하지 않았다.
