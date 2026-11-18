# Python AST v3 Data-Flow 구현·검증 보고서

## 결론

Python AST backend v3에서 다음 정적 may-flow를 구성했다.

`caller parameter → single identity alias → call argument → callee parameter → unchanged callee`

Synthetic four-cell control에서는 6개 oracle node, 4개 typed edge 및 complete flow path를 모두 회수했다. Call-only와 call+data-flow evidence family도 같은 endpoint에 대해 별도 path로 보존됐다.

이 결과는 **data-flow 표현과 retrieval 동작 검증**이다. Direct call만으로도 unchanged callee를 회수할 수 있는 control이므로 data-flow의 증분 review contribution은 아직 입증하지 않는다.

## 보수적 분석 규칙

- 함수 안에서 정확히 한 번 쓰인 `name = other_name`만 identity alias로 인정
- 재할당, augmented assignment, tuple unpacking, comprehension target 및 transformation RHS는 제외
- positional, positional-only, keyword 및 keyword-only binding mode를 구분
- `*args`/`**kwargs`, dynamic dispatch, field-sensitive flow는 해석하지 않음
- alias와 simple-name argument flow는 path-insensitive `may` edge이며 confidence 0.8
- unresolved/confidence-zero edge는 graph에 감사용으로 남기되 evidence path에서는 제외
- 최대 candidate path 10,000개를 deterministic하게 제한하고 실제 10,001번째 경로가 발견될 때 truncation으로 기록
- Truncation이 발생한 pair는 recall 수치와 무관하게 retrieval gate 실패

주요 edge는 `assigned_to`, `value_flows_to`, `argument_parameter`, `parameter_of`다. `assigned_to`와 `value_flows_to`는 runtime causal proof가 아니라 정적 may-flow다.

## Four-cell control

Family: `runs/context_pairs_v1/smoke_dataflow_provenance`

Safe와 unsafe에 동일한 target diff를 적용한다. 변경 consumer는 입력을 `alias`에 저장해 unchanged `producer.enforce`로 전달한다. Safe producer는 identity를 보존하고 unsafe producer는 lower-case로 바꾼다.

| Cell | 관측 |
|---|---|
| safe base | pass |
| safe head | pass |
| unsafe base | pass |
| unsafe head | target_failure (`UserID` → `userid`) |

Static eligibility 22/22와 four-cell witness가 모두 통과했다.

## Retrieval 결과

| 지표 | 결과 |
|---|---:|
| Head candidate graph | 9 nodes / 12 edges |
| Selected graph | 6 nodes / 6 edges |
| Selected complete evidence paths | 2 |
| Selected token cost | 38 |
| Oracle node recall | 1.0 |
| Oracle typed-edge recall | 1.0 |
| Complete-path recall | 1.0 |
| Irrelevant-token ratio | 0.0 |
| Candidate-path truncation | false |
| Retrieval gate | 통과 |

선택된 두 path는 다음과 같다.

1. `consumer → producer`의 call-only path
2. `consumer ↔ caller parameter → alias → argument → callee parameter → producer`의 call+data-flow path

## 오염 방지 수정

1. `alias += value` 등 추가 Store가 있는 이름을 identity alias로 잘못 인정하지 않도록 전체 local Store 횟수를 검사한다.
2. Keyword-only에 positional argument를, positional-only에 keyword argument를 연결하지 않도록 Python signature mode를 분리했다.
3. 같은 endpoint라도 call-only와 call+data-flow를 같은 obligation으로 삭제하지 않도록 relation-family를 selection key에 포함했다.
4. Depth 증가에 따른 path 폭발을 제한하고 cap 도달 여부를 결과에 기록한다.

## 품질 확인

```text
전체 회귀 suite: 324 passed
관련 단위·통합 테스트: 48 passed
관련 구현 branch coverage: 85%
변경 범위 Ruff: all checks passed
secret-pattern scan: 발견 없음
reviewer/model calls: 0
```

## 산출물

- `runs/provenance_ast_dataflow_smoke_retrieval_v3_frozen.json`
- `runs/provenance_ast_config_smoke_retrieval_v3_frozen.json`
- `runs/provenance_ast_call_smoke_retrieval_v3_frozen.json`
- `runs/provenance_ast_negative_smoke_retrieval_v3_frozen.json`
- `runs/provenance_adapter_audit17_path_cap.json`
- `runs/context_pairs_v1/smoke_dataflow_provenance/static_validation.json`
- `runs/context_pairs_v1/smoke_dataflow_provenance/witness_receipt.json`

과거 paired-SCIP 17-task 구조 감사에서는 16건 adapter 성공, 1건 bundle unavailable, 0건 adapter failure였다. 다만 `aacr-microsoft__typescript-go-831`은 candidate path가 10,000개를 넘어 truncated되었으므로 이 task의 선택 결과를 complete retrieval로 해석하면 안 된다. Synthetic data-flow control은 후보 5개로 truncation이 없었다.

## 다음 입증 단계

다음 control은 call graph에는 여러 후보 producer/sink가 동일하게 나타나지만 value-flow가 실제 결함 관련 source–sink 한 쌍만 구별하도록 설계해야 한다. 그때 다음 retrieval-only ablation을 비교한다.

1. Diff only
2. Call/definition context
3. Call + untyped flow nodes
4. Call + typed data-flow path
5. Oracle path

주요 지표는 complete-path recall, irrelevant-token ratio, 선택 token 및 target source–sink rank다. 이 disambiguation gate를 통과한 뒤 자연 development family로 확장해야 data-flow의 증분 contribution을 논의할 수 있다.
