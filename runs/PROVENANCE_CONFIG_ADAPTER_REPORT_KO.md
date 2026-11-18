# Python AST v2 Config-Provenance 구현·검증 보고서

## 결론

Python AST backend를 v2로 올리고, 변경 함수가 읽는 unchanged module-level config binding을 최소 evidence path로 회수하도록 구현했다. Synthetic four-cell positive control에서는 oracle node, typed edge, complete path를 모두 회수했고, production-code edge가 없는 negative control은 계속 실패했다.

이 결과는 **표현 및 retrieval 가능성 검증**이다. 자연 PR에서 BM25/LSP 대비 defect recall이 개선됐다는 결과는 아니다.

## v2 의미

- module-level `Assign`/`AnnAssign` 중 이름이 한 번만 정의된 binding만 인덱싱
- `from module import NAME`, `import module`, alias 및 same-module name을 정적으로 해석
- 함수 parameter/local assignment에 의해 shadow된 이름은 global/config read로 연결하지 않음
- binding에서 reader function 방향의 `binding_read_by` typed edge 생성
- 재할당된 ambiguous binding과 star import는 증거로 만들지 않음
- unresolved 또는 confidence-zero edge는 graph에는 감사 목적으로 보존하지만 evidence path 선택에서는 제외
- seed는 diff line과 겹치는 function 또는 module binding으로만 제한

`binding_read_by`는 정적 이름 해석이지 runtime value-flow 증명은 아니다. Backend receipt에도 `static_name_resolution_not_runtime_value_proof`로 기록한다.

## Four-cell config control

Family: `runs/context_pairs_v1/smoke_config_provenance`

동일한 target diff가 `policy.CASE_INSENSITIVE`를 읽기 시작한다. Safe companion은 `False`, unsafe companion은 `True`이며 companion은 각 world의 base/head에서 불변이다.

| Cell | 관측 |
|---|---|
| safe base | pass |
| safe head | pass |
| unsafe base | pass |
| unsafe head | target_failure (`UserID` → `userid`) |

Static eligibility 22/22 및 witness 네 cell이 모두 통과했다.

## Retrieval-only control 결과

| Family | Node recall | Edge recall | Complete-path recall | Noise | Gate |
|---|---:|---:|---:|---:|---|
| Config positive | 1.0 | 1.0 | 1.0 | 0.0 | 통과 |
| Direct-call positive | 1.0 | 1.0 | 1.0 | 0.0 | 통과 |
| No-edge negative | 0.0 | 0.0 | 0.0 | 0.0 | 실패 |

Config positive에서는 5-node/5-edge candidate graph에서 `config → consumer` 2-node/1-edge path만 선택했고 token cost는 29였다. Unresolved method-call edge 2개는 선택에서 제외됐다.

## 품질 확인

```text
전체 회귀 suite: 317 passed
관련 단위·통합 테스트: 41 passed
관련 구현 branch coverage: 85%
변경 범위 Ruff: all checks passed
reviewer/model calls: 0
```

## 구현 중 수정한 오염 가능성

1. Changed function 내부의 call/argument/parameter까지 seed로 승격되던 문제를 function/binding seed로 제한했다.
2. Confidence 0의 unresolved external call이 endpoint evidence로 선택되던 문제를 차단했다.
3. 선택 경로가 0개일 때 noise ratio가 undefined가 되어 negative control 자체가 중단되던 문제를 0.0으로 정의했다. 이 경우 gate는 complete-path recall로 실패한다.
4. Backend 의미 변경을 기존 v1 이름으로 덮지 않고 `python_ast_graph_v2`로 버전 상승했다.

## 산출물

- `runs/provenance_ast_config_smoke_retrieval_v2.json`
- `runs/provenance_ast_call_smoke_retrieval_v2.json`
- `runs/provenance_ast_negative_smoke_retrieval_v2.json`
- `runs/context_pairs_v1/smoke_config_provenance/static_validation.json`
- `runs/context_pairs_v1/smoke_config_provenance/witness_receipt.json`

## 현재 주장 가능 범위

주장 가능:

- Diff 외부의 unchanged imported config를 typed path로 회수할 수 있다.
- Shadowing·재할당·unresolved call을 보수적으로 제외하는 negative behavior가 동작한다.
- 동일 selector가 direct-call과 config-read 두 관계 family를 처리한다.

아직 주장 불가:

- Config provenance가 실제 code-review recall을 높인다.
- AST v2가 LSP, SCIP, CodeQL 또는 BM25보다 우수하다.
- Python 외 언어, field-sensitive alias, external resource, runtime dispatch를 처리한다.
- Synthetic control 결과가 자연 PR에 일반화된다.

다음 gate는 local assignment/alias 및 interprocedural argument value-flow를 추가한 뒤, 적어도 20개의 typed-oracle development family에서 retrieval-only ablation을 수행하는 것이다.
