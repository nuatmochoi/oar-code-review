# P2-A 결과: 답 구현 선택

2026-09-07. `answer_source1`. 추가 모델 호출 **0회**; retrieval-only **36개 조건** 완료.

## 결론

새 인자 전달 기반 selector는 AnyIO의 `TrioBackend.run`을 6/6회 회수했다. 기존 question 검색은 0/6회였다. 다만 claim BM25도 이미 6/6회 회수했으므로 **BM25보다 좋은 검색이나 높은 review recall을 입증한 결과가 아니다.**

이번 진전은 키워드가 많은 호출부 대신 downstream 구현을 찾고, 왜 선택했는지 actual/formal 연결과 불확실성을 기록할 수 있게 됐다는 것이다. 실제 backend 선택은 아직 해결하지 못했다. 사전 gate에 따라 모델 비교 실험은 확대하지 않았다.

## 실행 범위

- 앞선 P1과 같은 Click/AnyIO 개발 두 family의 safe/unsafe 네 snapshot.
- P1의 12개 frozen proposal 재사용 × claim/question/answer-source 세 조건 = 36개 검색 결과.
- 동일 paired-SCIP 후보 풀, source 800-token 상한, 동일 source 정렬 방식.
- 기존 baseline 24개의 source 선택을 그대로 재현했다.
- 새 source 및 input/code snapshot을 각각 동결한 후 endpoint를 평가했다.
- 테스트 우선 절차로 실패를 확인한 뒤 구현했다. 20개 분석 테스트 및 3개 실제 결과 회귀 검증을 추가했다. Branch 포함 module coverage 94%.
- 최종 프로젝트 테스트 215개 및 새 코드 lint가 통과했다. Gate·검증 기록.

여기서 `/6`은 safe/unsafe × 기존 proposal 반복 세 개다. 독립 PR 6개가 아니다. 평가용 endpoint 이름은 결과 동결 이후 진단에만 사용했다.

## 검색 결과

| 방법 | AnyIO adapter 포함 | Click producer 포함 | Click consumer 포함 |
|---|---:|---:|---:|
| Claim BM25 | 6/6 | 1/6 | 4/6 |
| 기존 question 검색 | 0/6 | 0/6 | 0/6 |
| 새 answer-source | 6/6 | 1/6 | 4/6 |

AnyIO adapter는 함수 일부가 아니라 전체 body가 6/6회 출력에 포함됐다. Source budget을 늘리지 않았다. AnyIO source 길이는 claim 781–799, question 792–797, answer-source 779–797 reference tokens다. 정확히 길이를 맞춘 placebo 대조는 아직 없다.

Click의 새 결과는 claim과 동일한 선택 집합을 보인다. 대부분의 질문은 아직 지원하지 않는 representation/guard 계열이어서 fallback했다. 한 proposal에서 `_has_binary_buffer`의 인자 사용을 찾았지만 producer의 반환 타입이나 소비자의 계약을 찾은 것은 아니다. 이것을 Click 문제 해결로 해석하면 안 된다.

## 실제로 찾은 관계와 남은 불확실성

AnyIO에서 diff의 `backend_options` 인자가 `anyio.run`의 같은 이름 formal로 전달되고, 내부 `async_backend.run`의 `options` formal 사용으로 연결되는 source 후보를 회수했다. SCIP의 interface 구현 관계를 통해 Trio와 AsyncIO 양쪽 구현까지 확장했다.

| 관찰 대상 | Source 관찰 | 아직 확정하지 못한 것 |
|---|---|---|
| Safe Trio adapter | `options`가 downstream call에 keyword expansion으로 사용됨 | 실제 실행 경로와 전달 값의 동등성 |
| Unsafe Trio adapter | 본문에서 `options`가 문법적으로 사용되지 않음 | 그 미사용이 실제 호출의 계약 위반인지 |
| AnyIO 중간 dispatcher | 분기·try/정규화가 존재해 `unknown_control_flow` | 지정 backend로 분기하는 실제 경로 |
| SCIP implementation 관계 | Trio/AsyncIO 구현 후보를 제공 | 어떤 구현이 runtime에 선택되는지 |

AnyIO의 매 선택마다 Trio, AsyncIO, abstract 구현 세 후보가 생겼고, 연결 경로의 불확실성이 세 후보 모두에 전파됐다. 총 18개 후보가 모두 uncertain이다. 후보를 넓게 선택한 것이며 ‘최소 evidence subgraph’라고 주장할 수 없다.

## 모델 호출 gate 판정

- 합성 positive/negative fixture 및 실제 결과 회귀 검사: 통과.
- AnyIO 핵심 source 회수: 통과.
- 실제 backend 선택과 중간 값 전달의 핵심 불확실성 제거: **미통과**.
- Click producer/consumer 질문 지원: **미완료**.

따라서 새 모델 리뷰 호출은 0회다. 이전 모델의 3/3 탐지 수치를 이번 방법의 성능으로 복사하지 않는다. 이번 기록에 semantic/strict F1, defect recall 향상, novelty 입증은 없다.

## 다음 실행 단위

다음 우선순위는 임의 모델 코드 실행이 아닌 고정 템플릿의 작은 runtime 관찰이다.

1. 실제 source snapshot/runtime에서 `backend="trio"`의 선택 결과를 관찰한다.
2. 중간 호출 인자를 격리된 프로세스에서 관찰해 supplied option이 adapter 경계까지 전달되는지 확인한다. Interception을 쓰면 원 프로그램 전체 behavior를 실행한 것이 아님을 명시한다.
3. Click의 타입 predicate가 예외인지 boolean인지 관찰한다. 이 사실과 producer 타입·consumer write 실패는 별개의 evidence로 둔다.
4. 관찰 가능한 범위·안전한 템플릿·timeout·network/filesystem 격리를 먼저 테스트한 뒤 source-only/source+probe를 비교한다. Source 선택 개선과 probe 효과를 하나의 treatment에 섞지 않는다.

이 runtime 단계는 아직 실행하지 않았다. Slicing은 필요한 source가 실제로 budget에서 탈락하는 경우에만 이후 적용한다.

## 산출물

- 구현 설명
- 동결 계획
- 36개 source 선택 결과
- 인자 연결·불확실성 audit
- 후처리 endpoint 진단
- 완료 기록

프로젝트 검증 명령: `python -m pytest tests -q`. 외부 CodeQL fixture까지 수집하는 루트 전체 pytest 실행은 사용하지 않는다.
