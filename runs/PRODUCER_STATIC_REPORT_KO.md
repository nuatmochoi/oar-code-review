# Producer-complete 정적 source vs production runtime: 18회 대조

2026-09-07. 탐색적 개발 run `producer_static_review1`.

## 결론

**누락됐던 producer·선택 경로 source만 보충해도 정상 사례 오경보는 없어졌다. Runtime 관찰은 정적 source보다 결함 탐지를 한 번 더 안정화하고 잘못된 Python 타입 의미 추론을 제거했지만, 사전 정의한 joint incremental gate는 통과하지 못했다.**

| 조건 | Unsafe 정확 메커니즘 | Safe 관련 경로 오경보 | 잘못된 predicate-raises | 같은 반복에서 탐지 + 정상 무경고 |
|---|---:|---:|---:|---:|
| 기존 retrieved source | 2/3 | 1/3 | 1개 | 1/3 |
| + producer·selector 정적 source | 2/3 | 0/3 | 1개 | 2/3 |
| production runtime 타입·분기 | 3/3 | 0/3 | 0개 | 3/3 |

Static은 retrieved 대비 unsafe hit 변화 0, safe alarm -1이다. 따라서 사전 정의한 static producer package gate는 통과했다. Runtime은 static 대비 unsafe hit +1, safe alarm 변화 0, 잘못된 메커니즘 -1이다. 그러나 runtime incremental gate는 ‘unsafe 유지/증가’와 ‘safe alarm 추가 감소’를 동시에 요구했고 이미 static의 safe alarm이 0이어서 통과하지 못했다. 이 gate를 결과를 본 뒤 완화하지 않았다.

## 비교한 근거

세 조건은 P2-A의 같은 answer-source와 P1의 같은 proposal을 공유했다.

- `retrieved_only`: 기존 source만 제공했다. 추가 block은 근거 없음과 길이 padding임을 명시했다.
- `producer_static`: `_pager_contextmanager`와 `_tempfilepager` 함수 전체를 head source에서 AST로 추출해 추가했다. safe producer에는 `NamedTemporaryFile(mode="w")`, unsafe에는 `mode="wb"`가 직접 나타난다.
- `production_runtime`: 실제 production path에서 관찰한 producer 내부 타입, predicate 결과, consumer 타입을 제공했다. object identity boolean은 제외했다.

정적 두 함수는 앞선 실패 분석을 본 사람이 지정했다. 따라서 결과는 **정답 producer source가 주어졌을 때의 진단**이지 자동 SCIP/LSP/AST retrieval 성공이 아니다. Runtime 조건도 이미 관찰한 같은 개발 사례의 head 결과를 사용했다.

## 오류 분석

기존 source의 safe 오경보는 `isinstance(stream, typing.BinaryIO)`가 TypeError를 낸다는 잘못된 설명이었다. Producer source를 주면 safe의 text mode가 분명해져 이 오경보는 사라졌다. 즉 앞선 production runtime 실험에서 safe 오경보가 감소한 효과 전부를 runtime 고유 효과로 볼 수 없다.

하지만 unsafe의 static 세 번째 반복은 binary `mode="wb"`와 선택 경로를 정확히 받았는데도 `typing.BinaryIO` 검사 자체가 예외를 낸다고 잘못 설명했다. 실제 관찰은 검사 결과가 False이고 예외가 없음을 보여준다. Runtime 조건은 세 반복 모두 다음 연결을 올바르게 설명했다.

`binary _TemporaryFileWrapper → has-buffer False → BinaryIO isinstance False → 미변환 → str consumer`

따라서 이번 개발 사례에서는 역할이 나뉜다.

1. **정적 producer provenance**는 어떤 종류의 객체가 실제 경로에서 생성되는지 보여 safe/unsafe를 구분한다.
2. **선택적 runtime semantics**는 Python typing predicate처럼 모델이 반복해서 잘못 아는 연산의 실제 결과를 고정한다.

이 결과는 ‘runtime을 항상 추가’하기보다, producer/consumer source를 먼저 회수하고 정적으로 불확실한 predicate·dispatch·reflection만 작은 probe로 확인하는 adaptive 구성을 지지한다. 다만 한 개발 family의 설계 신호일 뿐 성능 일반화 증거는 아니다.

## 실험 통제

- Click safe/unsafe 2개 × 기존 proposal 3개 × 3조건 = 새 final review 18회.
- 같은 task/repeat 안에서 diff, 기존 source 내용·순서·ID, proposal, verifier를 공유했다.
- 전체 prompt의 o200k reference token 수를 세 조건에서 정확히 맞췄다. 공개 inert padding이며 의미 밀도·문체까지 같은 placebo는 아니다.
- 모델 `gpt-5.6-sol`, 도구 비활성화, timeout 300초, jobs=4, 자동 재시도 없음.
- 입력·schema·prompt·runner·판정 규칙은 모델 호출 전에 동결했다. Generation/schema 오류와 도구 호출은 모두 0이다.
- 18개 응답과 지적 9개를 모두 읽고 저자가 판정했다. 독립·블라인드 adjudication은 아니다.
- `T`는 목표 binary producer→미변환→str consumer 메커니즘 일치다. 출력의 모든 세부 문장이 정확하다는 뜻은 아니다. `W`는 사전 정의한 predicate-raises 오판이다.
- 반복은 독립 PR이 아니다. 이미 반복 관찰한 개발 pair이며 통계적 유의성, AACR 전체 F1, 새 저장소 일반화를 측정하지 않았다.

## 비용

| 조건 | 새 호출 | Prompt reference tokens 합 | 출력 JSON reference tokens 합 | 호출 시간 합(초) |
|---|---:|---:|---:|---:|
| 기존 retrieved source | 6 | 16,408 | 900 | 137.7 |
| Producer static | 6 | 16,408 | 1,022 | 109.9 |
| Production runtime | 6 | 16,408 | 942 | 86.6 |

Reference tokenizer 수치이며 billed/reasoning token은 아니다. 병렬 호출 시간 합은 전체 경과시간이 아니다. 기존 proposal/source/index/probe 비용은 포함하지 않았다.

## 현재 contribution 판단

이제 다음과 같이 좁혀 말할 수 있다.

> 독립적인 관련 코드 또는 runtime 사실을 늘리는 대신, 변경 지점의 실제 producer–predicate–consumer 경로에 근거를 묶으면 관찰된 context-dependent defect pair에서 safe/unsafe 구분이 개선되었다. 정적 producer provenance가 scope 오류를 줄였고, runtime probe는 정적으로 불확실한 predicate 의미를 보완했다.

그러나 아직 논문 contribution으로 확정할 수 없는 부분은 명확하다.

- 수동으로 정답 producer 함수를 선택했으므로 자동 minimal evidence subgraph의 성능이 아니다.
- Runtime joint gate가 미통과했고 한 family뿐이다.
- LSP/SCIP, AST, data flow 중 어느 표현이 producer를 가장 잘 찾는지 분리하지 않았다.
- strongest static analysis가 predicate의 False를 계산할 수 있는지 비교하지 않았다.
- 새 repository-disjoint holdout과 독립 판정이 없다.

## 다음 우선순위

다음 실험은 context를 많이 넣는 방식이 아니라 **adaptive evidence acquisition**으로 고정하는 편이 타당하다.

1. Diff의 변경 predicate/consumer에서 backward slice하여 producer 후보를 자동 회수한다. SCIP definition/reference + AST call/argument binding을 쓰되 동적 dispatch는 unknown으로 둔다.
2. producer source가 타입·mode·상수로 safe/unsafe를 결정하면 정적 evidence만 선택한다.
3. `typing`, reflection, dynamic adapter 선택, alias/rebinding처럼 정적 의미가 불확실할 때만 고정 runtime probe를 추가한다.
4. 새 저장소의 paired holdout에서 `retrieved-only / producer-static / adaptive-probe / oracle producer`를 같은 예산으로 비교한다. Oracle과 adaptive의 차이가 retrieval gap이다.

이 설계가 성공하면 contribution은 ‘LSP/AST/CodeQL을 많이 제공’이 아니라 **production provenance completeness와 semantic uncertainty를 기준으로 증거 종류를 선택하는 code-review context policy**가 된다.

## 산출물과 검증

- 호출 전 고정 계획
- 18개 원본 리뷰
- 항목별 저자 판정
- 최종 집계·비용
- 입력 manifest
- 최종 결과 무결성
- 검증 기록

테스트 우선 절차에서 experiment와 analyzer의 미구현 실패를 먼저 확인한 뒤 구현했다. 기존 동결 run은 수정하지 않았다. 전체 재검증 명령은 `python -m pytest tests -q`이다.
