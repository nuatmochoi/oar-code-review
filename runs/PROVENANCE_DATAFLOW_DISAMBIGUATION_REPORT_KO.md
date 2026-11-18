# Typed Data-Flow Disambiguation 구현·실험 보고서

> 2026-09-08 해석 정정: 아래의 초기 contribution 해석은 후속 감사에서 축소됐다.
> Call-only 함수 본문에도 oracle 소스 조각이 모두 포함되어 있었다. Untyped arm의
> typed-path recall 0은 지표 정의에 따른 결과이며 reviewer 효용의 증거가 아니다.
> Call-only와 typed-flow는 seed도 다르다. 또한 이 audit는 head graph와 diff line을
> 사용하며 base/head semantic delta 선택 효과는 실험하지 않았다.
> 최신 판단은 [factorial 재감사 보고서](PROVENANCE_FACTORIAL_REAUDIT_KO.md)를 따른다.

## 결론

Call graph만으로 구별할 수 없는 두 입력 중 실제로 변경된 값 경로를 typed data-flow가 식별하는 synthetic 통제 실험을 구현했다. 동일한 candidate graph와 oracle에서 `diff-only → call-only → untyped-flow placebo → typed-flow → oracle`을 비교한 결과, call-only의 complete-path recall은 0.0이고 typed-flow는 1.0이었다. Typed-flow는 oracle과 동일한 6 nodes/5 edges, 23 tokens를 선택했으며 irrelevant-token ratio는 0.0이었다.

이는 **정확한 relation type과 changed-value seed가 retrieval에 증분 정보를 준다는 구성 타당성 증거**다. 아직 자연 PR의 defect review recall이나 reviewer 정확도 향상을 입증한 결과는 아니다.

## 검증 가설

> 동일 diff에서 여러 caller argument와 sink 후보가 call graph상 모두 연결될 때, 변경된 값에서 시작한 typed source–sink path는 call-only 문맥보다 oracle evidence path를 더 정확하게 복원한다.

핵심 비교는 파일 또는 노드 수 증가가 아니다. Untyped placebo는 typed arm과 정확히 같은 6개 노드를 보되 edge label만 제거한다. 따라서 typed arm과 placebo의 차이는 `assigned_to`, `value_flows_to`, `argument_parameter`, `parameter_of` 관계 보존 여부다.

## Four-cell 통제군

Family: `runs/context_pairs_v1/smoke_dataflow_disambiguation`

Target diff는 `selected = primary`를 `selected = secondary`로 바꾸며 safe와 unsafe world에서 byte-for-byte 동일하다. Unchanged caller는 두 인자를 모두 제공한다.

- Safe caller: `consumer("UserID", "UserID")`
- Unsafe caller: `consumer("UserID", "userid")`
- Unchanged sink: 입력을 그대로 반환

| Cell | 사전 선언 | 관측 |
|---|---|---|
| safe base | pass | pass |
| safe head | pass | pass |
| unsafe base | pass | pass |
| unsafe head | target_failure | target_failure |

Static eligibility 22/22, diff reconstruction, 원본 snapshot 불변성, 네 cell witness가 모두 통과했다.

## 구현 변경

Python AST backend를 v4로 올리고 다음 규칙을 추가했다.

1. 실제 diff line과 겹치는 local alias/argument만 `flow_seed_ids`에 넣는다.
2. Unchanged caller에서 changed callee로 전달되는 argument를 provenance boundary producer로 표시한다.
3. Local flow seed에서 containing changed function을 경유해 다른 parameter로 넘어가는 경로를 차단한다. Changed function은 자신이 시작 seed일 때만 확장된다.
4. Call-only arm은 `relation_family=call` edge만 유지하고 function diff seed에서 선택한다.
5. Typed-flow arm은 changed local seed에서 전체 typed graph를 선택한다.
6. Oracle arm은 gold node의 file/span/symbol과 typed edge를 backend graph에 투영해 생성한다.

Reviewer/model 호출은 0회다. Candidate-path cap과 truncation 실패 규칙은 기존 retrieval gate와 동일하게 유지한다.

## Retrieval 결과

| Arm | nodes | edges | tokens | node recall | edge recall | path recall | noise | gate |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| Diff only | 1 | 0 | 5 | 0.167 | 0.0 | 0.0 | 0.0 | 실패 |
| Call only | 7 | 6 | 52 | 0.5 | 0.2 | 0.0 | 0.731 | 실패 |
| Untyped flow placebo | 6 | 5 | 23 | 1.0 | 0.0 | 0.0 | 0.0 | 실패 |
| Typed flow | 6 | 5 | 23 | 1.0 | 1.0 | 1.0 | 0.0 | 통과 |
| Oracle | 6 | 5 | 23 | 1.0 | 1.0 | 1.0 | 0.0 | 통과 |

증분 차이는 다음과 같다.

- Typed − call complete-path recall: +1.0
- Typed − call oracle-node recall: +0.5
- Typed − untyped complete-path recall: +1.0

Call-only가 일부 node와 `argument_parameter` edge 하나를 찾더라도, changed alias를 거쳐 sink까지 이어지는 complete typed path는 만들지 못했다. 반대로 untyped placebo는 모든 관련 node를 포함하지만 관계 의미가 없기 때문에 typed edge/path recall이 0이다.

## 해석 경계와 contribution 상태

이번 결과로 주장 가능한 것은 다음 한 문장까지다.

> Multi-source 호출 문맥에서는 call connectivity만으로 실제 변경 값의 provenance를 구별할 수 없으며, changed-value-seeded typed flow가 동일하거나 더 적은 토큰으로 완전한 evidence path를 복원할 수 있다.

아직 주장할 수 없는 것은 다음과 같다.

- 자연 PR에서 defect review recall이 상승한다.
- AST v4가 SCIP/CodeQL보다 우수하다.
- LLM reviewer가 typed edge를 실제로 이용한다.
- Python identity-alias 결과가 field, heap, dynamic dispatch, 다른 언어로 일반화된다.

따라서 논문 contribution 후보는 “새로운 data-flow 분석”이 아니라 **base/head semantic delta에서 변경 값 seed와 source–sink evidence path를 선택하고, node-equated untyped placebo로 relation contribution을 분리하는 code-review context protocol**이다. 이를 논문 수준의 contribution으로 올리려면 아래의 natural/derived 확장이 필요하다.

## 다음 실험 우선순위

1. 동일한 ablation을 field-sensitive fan-in/fan-out, return-value chain, config/resource write-read의 세 pattern으로 확장한다.
2. 각 pattern에서 call-only가 후보 node는 회수하지만 target path 또는 rank를 놓치는 development family를 최소 5개씩 확보한다.
3. Python AST와 production-grade SCIP/CodeQL 결과를 공통 graph로 정규화해 backend 교차 재현성을 측정한다.
4. Retrieval gate를 통과한 family에서만 C2(call/LSP) 대 C3(typed flow) reviewer 실험을 실행한다.
5. Repository-disjoint test set에서 context-required defect recall, safe-world false-alarm rate, path recall, noise 및 token cost를 함께 보고한다.

## 재현과 산출물

```bash
python3 -m ace_review.paired_benchmark \
  runs/context_pairs_v1/smoke_dataflow_disambiguation/pair.json
python3 -m ace_review.witness_runner \
  runs/context_pairs_v1/smoke_dataflow_disambiguation/pair.json
python3 -m ace_review.disambiguation_audit \
  runs/context_pairs_v1/smoke_dataflow_disambiguation/pair.json \
  runs/provenance_dataflow_disambiguation_v4_reproduction.json \
  --max-depth 6
```

Audit 출력은 기존 파일을 덮어쓰지 않으므로 재실행마다 새 output path를 사용한다.

- `runs/context_pairs_v1/smoke_dataflow_disambiguation/static_validation.json`
- `runs/context_pairs_v1/smoke_dataflow_disambiguation/witness_receipt.json`
- `runs/provenance_dataflow_disambiguation_v4_frozen.json`
- 전체 회귀: 327 passed
- 관련 테스트: 37 passed
- 관련 구현 branch coverage: 87%
- Ruff: all checks passed
- reviewer/model calls: 0
