# Provenance semantic adapter 및 retrieval-only gate 보고서

## 결론

다음 단계의 공통 semantic adapter와 모델 호출 전 retrieval gate를 구현·실행했다.

- 기존 paired-SCIP bundle은 `EvidenceGraph`로 변환 가능하다.
- SCIP의 `reference/use`는 call 또는 data flow로 승격하지 않고 `scip_*` 구조 edge로 보존한다.
- Python AST는 diff-line seed, import-resolved direct call, call site, positional argument→parameter를 구성한다.
- Backend별 hash ID와 annotator의 논리 ID는 직접 비교하지 않고 file/span/symbol로 매칭한다.
- 같은 seed–endpoint obligation의 중복 path는 선택하지 않는다.
- Oracle path가 없는 기존 17-task run에는 retrieval recall을 계산하지 않았다.

## 구현

| 파일 | 역할 |
|---|---|
| `ace_review/scip_adapter.py` | preserved paired-SCIP bundle/source를 revision별 공통 graph로 변환 |
| `ace_review/python_ast_graph.py` | Python function/call/call-site/argument/parameter graph 구성 |
| `ace_review/adapter_audit.py` | 과거 SCIP run의 manifest-complete structural audit |
| `ace_review/pair_retrieval_audit.py` | eligible Python pair에 대한 gold-aware, model-free retrieval gate |
| `ace_review/retrieval_gate.py` | backend-neutral oracle matching 및 node/edge/path 지표 |
| `ace_review/minimal_subgraph.py` | 중복 obligation 제거가 포함된 complete-path 선택 |

## 기존 paired-SCIP 17-task structural audit

입력은 `runs/delta_scip_v2_final17`의 동결 산출물이다.

| 항목 | 결과 |
|---|---:|
| Task manifest | 17 |
| Preserved bundle 있음 / adapter 성공 | 16 |
| Bundle 없음 | 1 (`aacr-gofr-dev__gofr-1325`) |
| Adapter 실패 | 0 |
| Head graph nodes | 17,028 |
| Head graph edges | 17,662 |
| Source가 붙은 head nodes | 16,918 (99.35%) |
| Base/head에서 정렬된 node IDs | 17,004 / 17,028 (99.86%) |
| Structural candidate paths | 39,974 |
| 1,200-token 선택 complete paths | 138 |
| 선택 node 중 source 보유 | 160 / 192 (83.33%) |
| Task별 선택 token 중앙값 | 1,149.5 (범위 1,071–1,196) |

16개 모두 non-empty path 선택을 만들었지만 이는 **SCIP reference/use 연결성**이다. Oracle causal edge나 defect-relevant complete path가 아니므로 recall 또는 review 성능으로 해석하지 않는다.

## Gold-aware Python controls

### Positive: `smoke_call_provenance`

변경된 `target.normalize`가 unchanged `producer.transform`을 새로 호출한다. Safe producer는 identity를 보존하고 unsafe producer는 lower-case로 바꾼다.

| 지표 | 결과 |
|---|---:|
| Oracle node recall | 1.0 |
| Oracle typed-edge recall | 1.0 |
| Complete-path recall | 1.0 |
| Irrelevant-token ratio | 0.0 |
| 선택 token | 23 |
| Retrieval gate | 통과 |

### Negative: `smoke_case_normalization`

Companion은 module-level policy constant지만 production code에서 target까지 semantic edge가 없다. Witness만 두 사실을 연결한다.

| 지표 | 결과 |
|---|---:|
| Oracle node recall | 0.5 |
| Oracle typed-edge recall | 0.0 |
| Complete-path recall | 0.0 |
| Retrieval gate | 실패 |

이 실패는 AST 구현 오류로 감추지 않는다. 현재 backend가 call/argument만 처리하고 assignment/config/resource read-write 및 test-covers 관계를 만들지 않는다는 구체적 한계다. Production code에 관계가 전혀 없으면 새 정적 backend를 추가해도 `unresolved`가 맞다.

## 구현 중 발견·수정한 실험 오염 가능성

1. Oracle logical ID와 backend hash ID를 직접 비교하던 문제를 file/span/symbol matching으로 수정했다.
2. 같은 seed–endpoint를 direct call과 call-site path로 중복 선택하던 문제를 obligation dedup으로 수정했다.
3. Changed file 안의 모든 함수를 seed로 잡던 문제를 실제 diff-line과 function span이 겹치는 함수만 seed로 삼도록 수정했다.
4. 17-task 명칭과 달리 bundle이 16개뿐인 사실을 숨기지 않도록 `unavailable`을 별도 집계했다.

## 검증

```text
전체 회귀 suite: 313 passed
관련 신규·통합 tests: 37 passed
관련 구현 branch coverage: 85%
변경 범위 Ruff: all checks passed
secret-pattern scan: 발견 없음
reviewer/model calls: 0
```

## 현재 주장 가능 범위

주장 가능:

- 기존 SCIP 산출물을 의미를 과장하지 않고 공통 graph로 변환할 수 있다.
- AST-resolved direct call family에서는 최소 typed path가 oracle를 회수한다.
- Semantic edge가 없는 control은 gate가 실제로 실패한다.

아직 주장 불가:

- C3가 BM25/C2보다 defect recall을 높인다.
- Base/head delta가 head-only보다 유리하다.
- Adaptive dynamic evidence가 always-dynamic보다 효율적이다.
- 16개 과거 task에서 138개 structural path가 defect-relevant하다.

## 다음 우선순위

1. Assignment/alias, field/config/resource read-write adapter를 추가한다.
2. SCIP graph와 AST graph의 동일 symbol/span node merge 규칙을 구현한다.
3. 기존 pilot은 정책 개발용으로만 사용하고, oracle typed path가 있는 development family를 20개까지 확보한다.
4. 그 20개에서 `head-only SCIP`, `paired SCIP`, `SCIP+AST`, `minimal graph` retrieval-only ablation을 수행한다.
5. Validation에서 threshold를 동결하기 전에는 reviewer model을 호출하지 않는다.
