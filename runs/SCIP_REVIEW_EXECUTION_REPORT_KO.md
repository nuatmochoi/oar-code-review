# SCIP 실제 리뷰 실행 결과와 후속 기여 판단

2026-09-03. **탐색 실험**이다. 기존 개발 코호트에서 실제 reviewer를 실행했으며, 독립 holdout 검증이나 context-required 결함에 대한 인과 효과 입증은 아니다.

## 1. 결론

문맥을 넣은 일부 조건의 점수는 Diff보다 높았지만, **base/head delta와 evidence bundle이 더 단순한 검색보다 우수하다는 증거는 얻지 못했다.** Bundle은 Diff 대비 semantic TP가 10 → 12로 늘었으나 BM25와 head-only SCIP는 각각 14였다. 주 비교의 ΔF1 95% CI는 모두 0을 포함했다.

Bundle의 순증가 2건은 새롭게 잡은 4건에서 기존 Diff가 잡던 2건을 잃은 결과다. 새로 잡은 4건은 모두 benchmark의 File Level이며, Repo Level 탐지는 Diff와 bundle 모두 2/9였다. Repo Level 자체가 독립 검증된 context-required 집합은 아니다.

따라서 현재는 “base/head semantic index의 최소 evidence subgraph가 diff-only로 판단 불가능한 결함 recall을 높인다”는 주장을 하지 않는다. 구현의 retrieval 개선과 downstream defect detection은 다른 결과다.

## 2. 실행 범위와 통제

- 기존 17개 중 selected-module scope_complete인 **15 PR / 4 repositories / gold 70건**을 고정했다.
- GoFR #1325(인덱싱 실패), #1355(부분 성공)는 제외 목록으로 공개했다. 성공 사례 조건부 결과이며 전체 17개 운영 성능이 아니다.
- 7조건 × 15 PR × 1회 = **105 reviewer 호출**.
- 보조: 동일 bundle source에서 manifest만 제거한 15회, 과거 잠정 증거를 직접 넣은 4회 = **19회**.
- 위 124회는 전부 정상 완료. 실제 외부 도구 호출 0, 자동 judge 호출 116회/오류 0. 빈 리뷰 8개는 judge 호출 없이 처리.
- 이후 동일 입력 A/A 재실행 15회와 judge 14회도 완료했다. **이번 전체 reviewer 139회 / judge 130회, 오류·외부 도구 호출 0**이다. 총 빈 리뷰 9개는 judge 호출 없이 처리했다.
- Reviewer `gpt-5.6-sol`, judge `gpt-5.4`. Prompt/schema 동일, 매 리뷰 최대 5개 finding. 채점 prompt에는 조건명을 전달하지 않는다.
- 주요 7조건 요청 순서는 seed 20260903으로 섞었다. Gold를 이용한 신규 retrieval이나 결과 기반 PR 제외는 하지 않았다.
- 요청, prompt, 실행 소스 해시, 원본 예측, 평가 labels를 보존했다. 과거 reviewer 출력을 가져와 섞지 않았다.
- 예산은 기존 content UTF8 bytes/4 추정 상한 4096. 전체 prompt의 정확한 token 일치가 아니다. LSP 평균은 약 1708, BM25 3508, SCIP 조건 약 3749–4053으로 실제 길이가 다르다.

LSP는 GoFR #1681에서, v1 SCIP-head는 #1617에서 추가 문맥이 비었다. 전체 15개 비교에서는 이 실패를 유지했다. 이 두 경우의 reviewer 입력은 해당 Diff와 정확히 같았다.

## 3. 주요 결과

아래 FP는 benchmark gold와 매칭되지 않은 예측 수다. 실제로 모두 잘못된 리뷰인지 사람 검증을 마쳤다는 뜻이 아니다. Semantic은 같은 파일 내 결함 의미 매칭, strict는 여기에 기존 ±1줄 위치 조건을 적용한다.

| 조건 | Semantic TP / FP | Recall | Precision | Semantic F1 | Strict F1 |
|---|---:|---:|---:|---:|---:|
| Diff | 10 / 21 | 14.3% | 32.3% | .198 | .139 |
| BM25 | 14 / 18 | 20.0% | 43.8% | .275 | .176 |
| LSP | 10 / 19 | 14.3% | 34.5% | .202 | .141 |
| SCIP-head | 14 / 17 | 20.0% | 45.2% | .277 | .158 |
| Paired module-aware + dedup | 13 / 19 | 18.6% | 40.6% | .255 | .196 |
| Witness flat | 12 / 17 | 17.1% | 41.4% | .242 | .141 |
| Witness bundle | 12 / 19 | 17.1% | 38.7% | .238 | .158 |

PR 단위 5000회 paired bootstrap, seed 20260903:

- Bundle − Diff: ΔRecall **+.029 [−.043, +.114]**, ΔF1 **+.040 [−.058, +.145]**.
- SCIP-head − Diff: ΔF1 **+.079 [−.024, +.159]**.
- BM25 − Diff: ΔF1 **+.076 [−.003, +.165]**.

단일 반복·소표본·다중 비교의 탐색 CI이다. 0을 포함한다는 것이 효과 부재의 증명은 아니며, 최고 평균 조건을 승자로 확정하지 않는다.

## 4. 보조 조건에서 관찰한 내용

### 4.1 같은 소스에서 관계 manifest를 빼기

| 조건 | Semantic TP / FP | Recall | F1 |
|---|---:|---:|---:|
| 소스 + bundle manifest | 12 / 19 | 17.1% | .238 |
| **동일 소스·순서**, manifest 제외 | 14 / 16 | 20.0% | .280 |

두 번째가 수치상 좋지만 ΔF1은 **+.042 [−.062, +.138]**로 불확실하다. Manifest는 평균 약 1148 content-token 추정 비용을 차지했다. 제거 조건은 그만큼 짧으므로 이것은 순수한 관계 표현 효과나 token-matched ablation이 아니다.

후속 가설: graph는 내부 검색·선택에 사용하고 reviewer에는 필요한 소스와 짧은 관계 설명만 주는 구성을 확인할 가치가 있다. “설명을 제거하면 좋아진다”는 결론이나 별도의 알고리즘 기여가 입증된 것은 아니다.

### 4.2 과거 잠정 증거를 직접 넣기

독립 인간 Oracle 대신, 예전 AI-assisted 주석에서 valid/sufficient로 표시했고 source 좌표를 검증할 수 있는 4 PR을 사용했다. 이번 reviewer 결과를 기준으로 4건을 선택하지 않았다. 원본 head source와 다시 대조했고 정답 설명/주석은 reviewer에게 주지 않았다.

| 대상 gold | Diff | 잠정 증거 직접 주입 | Bundle |
|---|---:|---:|---:|
| TypeScript-Go #691, DOM guard | 미탐 | 미탐 | 미탐 |
| GoFR #1681, CORS/logging 순서 | 미탐 | 미탐 | 미탐 |
| GoFR #1696, 공유 logger | 탐지 | 탐지 | 탐지 |
| Ollama #8938, uint → int | 탐지 | 탐지 | 탐지 |

대상 gold 탐지는 **2/4 → 2/4**, 새로 회수한 target은 없었다. 해당 4 PR의 전체 gold 11건 기준도 semantic TP 3 → 3이었다. 이 4건을 verified context-required defect 또는 최소 Oracle이라고 부르지 않는다.

특히 #691을 재검토하니 함수의 unchanged caller는 true일 때 “dom을 추가하라”는 진단을 내므로 기존 inversion gold와 과거 valid 주석 자체를 재검토해야 한다. 주 점수는 그대로 보존했다. 별도 유효성 점검.

### 4.3 동일 입력에서도 달라진 결과

- GoFR #1681: 문맥이 빈 LSP와 Diff의 prompt SHA-256 동일. LSP 표기 실행에서는 CORS gold가 매칭됐으나 Diff 실행에서는 미매칭.
- GoFR #1617: 문맥이 빈 SCIP-head와 Diff도 prompt SHA-256 동일. SCIP-head 표기 실행에서 추가 gold가 매칭됨.

**이 이득은 LSP/SCIP가 공급한 문맥 효과일 수 없다.** 동일 입력의 생성·평가 변동이 있으므로 작은 TP 차이를 곧바로 방법 효과로 해석하면 안 된다.

이를 확인하기 위해 15 PR 전체의 동일 Diff-only 입력을 한 번 더 실행했다. 두 실행의 합계는 우연히 TP 10 / FP 21 / F1 .198로 같았지만, **새롭게 매칭된 gold 2건과 사라진 gold 2건**이 있었다. 재실행에서 새로 매칭된 NATS `aacr-22-1`은 문맥이 비었던 SCIP-head 실행에서도 매칭된 사례다.

이는 동일 입력에서도 정답별 gain/loss가 발생함을 보여준다. 재실행을 유리한 baseline으로 교체하지 않았으며, 한 번의 A/A가 분산을 안정적으로 추정하거나 모든 context 효과를 반박하는 것은 아니다. A/A 원본 분석.

## 5. 실제 사례가 가리키는 병목

- TypeScript-Go #691: 잠정 source footprint는 후보 풀에 있지만 SCIP 최종 선택에는 없음. 그러나 gold 자체에 의문이 있어 이 누락을 실제 결함 검색 실패로 확정할 수 없음.
- GoFR #1681: 잠정 test 블록 전체를 SCIP 후보가 포함하지 않고, 최종 선택에도 없음. 직접 주입도 이번 실행에서는 target을 회수하지 못함.
- GoFR #1696: 잠정 shared-owner target 줄을 bundle이 회수하지 못했지만 Diff도 gold를 탐지. 이 결과만으로 해당 줄이 필수라고 보기 어려움.
- Ollama #8938: witness flat/bundle이 반환 타입 target 줄을 포함했으나 Diff도 이미 탐지. 문맥이 유용한 정보를 제공한 것과 새 결함을 발견하게 한 것은 다름.
- Bundle의 새 TP 4건은 converters 전달, NATS 필드 경쟁, timeout 후 logger, progress visibility 관련 gold였다. 모두 File Level이고, 고정 자동 judge의 매칭 결과다. 각 사례의 제공 evidence ID와 원본 claim은 `gold_cases.json`에 보존했다.

## 6. 어떤 contribution을 다음에 노릴 것인가

**권고: 더 큰 graph보다는, “변경된 보장 ↔ 그대로 남은 소비자 요구”를 연결하는 증거 선택과 후보별 검증을 우선한다.**

1. **Obligation-directed selection:** 변경 전후 값/오류 처리/early-return/shared-state 효과와 unchanged consumer의 가정을 함께 찾는다. 구조적 거리·변경 유형 규칙보다 좋아야 하며, base 또는 consumer 증거를 뺀 대조군에서 효과가 줄어야 한다.
2. **후보 생성과 검증 분리:** Diff와 문맥 후보를 분리 생성한 뒤, 각 후보의 미확인 사실에 필요한 지지/반박 증거만 검색한다. 추가 LLM 재검토·무관 문맥·기존 후보 통합/pruning과 같은 총비용에서 비교한다.
3. **조건부 실행 witness:** 상태 전이·동시성·지원 환경 결함에만 fault injection, schedule, 경계값 테스트를 붙인다. base/head 동작 차이를 요구 위반과 구분하고 Testora/DiffTestGen 계열과 비교한다.

이 중 어느 것도 아직 notable contribution이 확정된 것은 아니다. 특히 [Go context 연구](https://arxiv.org/html/2606.01859v1)는 양쪽 AST 변경 유형 기반 13개 LSP 규칙과 후보 통합을 이미 다뤘고, [DiffTestGen](https://arxiv.org/html/2607.16024v1)은 graph-guided 변경 테스트를 다룬다. 단순 도구 조합 이상의 기여가 필요하다. 상세 관련 연구·방법·비교 설계.

## 7. 완료 / 남은 작업

완료: 15 PR의 실제 7조건 비교, 소스 동일/manifest 제거 진단, 잠정 증거 4건 주입, Diff A/A 재실행, paired bootstrap, gold별 gain/loss 및 citation 감사. `ecc-codex-playbook` 절차에 따라 새로운 실행/진단/분석 코드의 테스트를 먼저 작성하고 검증했다. 전체 **88개 테스트 통과**, 새 3개 모듈 statement/branch 합산 coverage **93%**이다.

미완료: 독립 인간 Oracle 주석, tokenizer 기준 동일 예산/placebo, paired-no-delta, dependency-repaired 2 PR, 전체 조건 반복, 신규 holdout. 기존 인덱싱 gate를 통과로 바꾸지 않았고 원본 repo 의존성을 수정하지 않았다.

## 자료와 재현

- 실행 전 계획
- 주 실험 설정
- 주 결과
- 주 semantic bootstrap
- 진단 결과
- 정답별 출력·증거
- 입력과 코드 해시
- 테스트 coverage

기존 출력은 보존하고 새 디렉터리를 지정해 준비한다. 준비 명령은 이미 존재하는 출력 디렉터리를 거부한다.

```bash
python3 -m unittest discover -s tests -q
python3 -m ace_review.scip_screening --output runs/scip_review_screening_new
python3 -m ace_review.runner run-codex \
  --requests runs/scip_review_screening_new/requests.jsonl \
  --output runs/scip_review_screening_new/predictions.jsonl \
  --prompt runs/scip_review_screening_new/source/ace_review/prompts/reviewer.txt \
  --schema runs/scip_review_screening_new/source/ace_review/schemas/review_output.schema.json \
  --model gpt-5.6-sol --repeat-id 1 --timeout 300 --jobs 6
python3 -m ace_review.runner judge-batch \
  --manifest runs/scip_review_screening_new/manifest.jsonl \
  --predictions runs/scip_review_screening_new/predictions.jsonl \
  --output runs/scip_review_screening_new/labels.jsonl \
  --schema runs/scip_review_screening_new/source/ace_review/schemas/batch_semantic_match.schema.json \
  --model gpt-5.4 --timeout 180 --jobs 6
```

기존 완료 결과의 분석 재생성:

```bash
python3 -m ace_review.scip_review_results \
  --screening runs/scip_review_screening15 \
  --diagnostics runs/scip_review_diagnostics15
```
