# Obligation-slot retrieval + 후보별 검증: 실행 결과

2026-09-03 · `runs/obligation_review15` · 15 PR, 4개 저장소, 70 gold, 조건당 1회.

## 결론

**실험은 완료했다. 그러나 ‘base/head 보장 변화와 unchanged consumer를 연결하면 Diff-only로 판단하기 어려운 결함의 recall이 높아진다’는 핵심 기여는 아직 입증하지 못했다.**

- 동일 후보·동일 2회 호출의 Diff 검증 7 TP → obligation 검증 9 TP. 수치상 +2이지만 F1 차이 CI가 0을 포함한다.
- base 소스를 빼면 10 TP, consumer 휴리스틱 소스를 빼도 9 TP다. 양쪽 revision과 consumer 증거의 필요성은 나타나지 않았다.
- benchmark의 Repo Level gold 9개에서는 **모든 검증 조건이 동일한 1개만 탐지**했다. 이 분류 자체도 독립적으로 검증한 context-required 라벨은 아니다.
- 공유 후보에는 gold와 일치하는 14개가 있었지만 obligation 최종 리뷰는 그중 8개를 유지하고 다른 gold 1개를 추가했다. 검증이 회수율을 떨어뜨리는 병목도 있다. 다만 의심 gold가 있어 ‘삭제된 gold 일치 후보’를 모두 실제 참 결함의 손실로 단정하면 안 된다.

전체적인 주장은 보류하되, **숨겨진 callee 구현 확인 1건**, **필요한 사실의 선택 실패**, **gold 후보의 실패 전제에 대한 반증**을 구체적인 후속 방향으로 남긴다.

## 무엇을 실행했는가

Diff-only에서 최대 5개 가설과 before/after/consumer/trigger 검색어를 생성하고, 그 JSON을 모든 검증 조건에 공유했다. Stage B는 후보마다 confirmed/refuted/unresolved를 판정하고, 기존 후보 외 `NEW` 결함도 허용했다. 후보 JSON은 증거가 아니다.

공통 검색 풀은 보존된 paired-SCIP `bundle_sources`다. obligation은 후보별 슬롯 BM25와 revision/owner 휴리스틱을 결합한다. **실제 계약 추출, SSA/data-flow, 경로 타당성 증명, 최소 subgraph 최적화는 구현하지 않았다.** before/after는 해당 revision을 갖는 소스이고, consumer는 변경 anchor 밖 owner의 base/head 동일 소스 조각이다. call 방향이나 사용 전제까지 보장하는 역할 분류가 아니다.

`verify_placebo`는 동일 PR 풀의 무작위 문맥이다. 관련 소스가 포함될 수 있어 엄격한 무관 placebo라고 해석하지 않는다. no-base/no-consumer는 제외 후 다른 소스로 예산을 채운 정책 ablation이며, 단순 동일 입력 삭제 실험은 아니다. Diff의 제거 행과 공유 후보에 들어 있는 이전 동작 정보는 no-base에서도 유지된다.

실행 전 고정 계획: OBLIGATION_EXPERIMENT_PLAN_KO.md. 구현 및 명령: OBLIGATION_METHOD_KO.md.

## 주 결과

Semantic은 동일 파일에서 결함 내용이 일치하면 위치가 달라도 인정한다. Strict는 기존 경로·위치 ±1 규칙을 추가한다. FP는 **benchmark gold에 매칭되지 않은 예측 수**이며 독립 사람이 실제 오탐이라고 확인한 수가 아니다.

| 조건 | Semantic TP / 70 | FP | Precision | Recall | Semantic F1 | Strict TP | Strict F1 |
|---|---:|---:|---:|---:|---:|---:|---:|
| Diff + 공유 후보 재검증 | 7 | 17 | 29.2% | 10.0% | .149 | 4 | .085 |
| 일반 SCIP 구조 점수 | 5 | 20 | 20.0% | 7.1% | .105 | 3 | .063 |
| 후보 통합 BM25 | 8 | 17 | 32.0% | 11.4% | .168 | 5 | .105 |
| **Obligation 슬롯 선택** | **9** | **19** | **32.1%** | **12.9%** | **.184** | **6** | **.122** |
| Obligation − 추가 base | 10 | 22 | 31.3% | 14.3% | .196 | 5 | .098 |
| Obligation − consumer 휴리스틱 | 9 | 19 | 32.1% | 12.9% | .184 | 5 | .102 |
| 무작위 동일-PR 문맥 | 5 | 15 | 25.0% | 7.1% | .111 | 3 | .067 |

후보 생성 출력은 별도 참고값: 48개 후보 중 14 gold 일치, 34 비매칭, recall 20.0%, F1 .237. 이 프롬프트는 불확실한 가설도 제안하도록 했으므로, 검증 완료 리뷰와 동일한 의미의 baseline으로 취급하지 않는다. 주 baseline은 위 표의 Diff 재검증이다.

## Paired 비교와 ablation

PR 단위 paired bootstrap 5,000회, seed 20260903. 아래는 탐색적 95% CI이며 다중 비교 보정이나 독립 test set 검정이 아니다.

| 비교: 앞 조건 − 뒤 조건 | Δ semantic F1 | 95% CI | 해석 |
|---|---:|---:|---|
| Obligation − Diff 재검증 | +.0347 | [−.0063, +.0998] | +2 TP, +2 FP; 확정적 개선 아님 |
| Obligation − 후보 BM25 | +.0153 | [−.0755, +.1157] | 슬롯 구조의 우위 미입증 |
| No-base − Obligation | +.0124 | [−.0493, +.0661] | base 제거로 악화되지 않음 |
| No-consumer − Obligation | .0000 | [−.0506, +.0581] | consumer 필요성 미입증 |

Obligation의 Diff 대비 semantic recall 차이는 +2.86%p, CI [0.00, 7.69]%p다. 하한이 0이므로 이 역시 엄격한 양의 개선으로 읽지 않는다. 소비자 제거 조건과 전체 TP/FP가 같아도 gold identity가 완전히 같은 것은 아니다.

| 원 benchmark 분류 | gold 수 | 후보 단계 | Diff 재검증 | Obligation | No-base |
|---|---:|---:|---:|---:|---:|
| Diff Level | 29 | 4 | 4 | 5 | 5 |
| File Level | 32 | 6 | 2 | 3 | 4 |
| Repo Level | 9 | 4 | 1 | 1 | 1 |

## 어떤 사실이 드러났는가

### 1. 문맥이 도움을 준 사례는 있지만 paired-index 기여는 아니다

TypeScript #1000, `aacr-15-0`: 변경 helper가 `toLSPDiagnostic(diag, nil)`을 호출한다. Diff에 보이지 않는 함수 본문은 `converters.ToLSPRange(...)`를 사용한다. Diff 재검증은 보류했지만 obligation은 해당 소스 `CTX-281b3e27c21e`를 인용하며 탐지했다.

다만 no-base, no-consumer도 탐지했고, 이전 실험의 BM25와 head-index 조건도 이미 탐지했다. 따라서 **숨겨진 callee 구현의 유용성을 보여주는 사례이지, 새 base/head+consumer 방법의 우위를 보여주지는 않는다.** 본 턴의 사후 Git 점검에서 `ToLSPRange` → `PositionToLineAndCharacter` → receiver 필드 접근까지 확인했지만, 그 추가 점검 소스는 모델 입력으로 소급하지 않았다.

다른 추가 탐지인 TypeScript #831, `aacr-12-3`은 benchmark Diff Level이고 리뷰도 DIFF만 인용했다. 새 리뷰가 나왔다는 사실만으로 외부 문맥의 필수성을 주장할 수 없다.

### 2. ‘저장소에 없음’이 아니라 ‘필요한 구현이 선택되지 않음’

Ollama #8938, `aacr-165-0`: verifier는 `Uint`가 MaxInt보다 큰 값을 허용하는지 알 수 없어 overflow 후보를 unresolved로 남겼다. 실제 head의 `envconfig/config.go:198–210`에는 `strconv.ParseUint(s, 10, 64)` 후 `uint(n)`을 반환하는 구현이 있다. 이 signature를 포함한 블록은 공통 후보 풀에 6개 있었지만 obligation 문맥에는 들어가지 않았다.

이 관찰은 원래 overflow 후보의 전체 유효성을 증명한 결과가 아니라, **verifier가 필요하다고 명시한 사실이 풀에는 있는데 선택 단계에서 빠졌다는 직접적인 실패 진단**이다. overflow가 실제 지원 환경·소비자 동작에서 결함이 되는지와 실행 witness는 별도 확인해야 한다. 결과를 본 뒤 해당 블록을 끼워 넣고 주 실험을 재채점하지 않았다.

GoFr #1681의 CORS/logging 후보도 순서 의미와 short-circuit 경로가 없다는 이유로 unresolved였다. 단순 owner/anchor 슬롯과 유사 테스트 조각은 실제 middleware 합성 순서나 호출 종료 조건을 대체하지 못했다.

### 3. Gold recall 저하가 항상 잘못된 억제는 아니다

GoFr #1617, `aacr-22-1`: 기존 gold는 Connect 성공 뒤 JetStream 획득 실패라는 경로를 전제로 한다. obligation은 `ConnectionManager.Connect()`가 `jetStreamCreator.New()` 오류를 먼저 반환하며, 성공 경로에서는 `jStream`을 저장한다는 소스를 찾아 해당 후보를 refuted로 판정했다.

실제 커밋에서도 이 제어 순서를 확인했다. 그러나 creator가 `(nil, nil)`을 반환할 수 있는지, 병행 실행으로 상태가 바뀔 수 있는지까지 독립 검증한 것은 아니다. **gold를 자동 폐기하지 않았고, 순차 정상 경로의 실패 전제에 재검토가 필요하다고만 표시한다.** 별개의 publication race는 여전히 가능하며 verifier도 별도 후보로 보고했다.

이는 annotation 재검토 신호이지 새로운 벤치마크 기여나 실제 precision 향상의 증명은 아니다. 기존 TypeScript #691 의심 gold도 그대로 보존했다.

## 다음 방법을 어떻게 좁힐 것인가

우선순위는 **슬롯 이름을 늘리는 것보다 필요한 사실의 충족 여부를 추적하는 것**이다.

| 좁힌 방향 | 이번 관찰과 연결 | 필요한 다음 비교 / 통과 기준 |
|---|---|---|
| Missing-fact-directed retrieval | `Uint` body가 풀에 있지만 최종 문맥에서 누락 | verifier가 남긴 missing fact를 심볼·predicate·effect로 매핑해 추가 검색. 같은 3회 호출의 Diff 재검토 및 일반 iterative BM25와 비교하고, missing-fact 해소율과 context-required recall이 함께 증가해야 함 |
| Callee precondition/범위·nullness 보장 변화 | #1000의 hidden callee 사용, #8938의 수치 범위 | before/head의 실제 값 범위·nullness·실패 반환을 추출하고, 소비자의 조건까지 이어지는 짧은 dependency chain을 선택. 단순 head definitions, caller/callee 확장, 동일 source flat 조건을 이겨야 함 |
| Protocol/state/조기 종료의 실행 witness | NATS 초기화 전제와 middleware 순서가 코드 조각만으로 불확실 | base/head에 동일 입력을 실행해 구체 경로·상태·로그 차이를 기록. 소스-only와 trace, 관계를 제거한 trace/placebo를 구분하고 의도된 동작 변화는 제외 |

Missing-fact 검색이나 differential testing 자체가 새롭다는 주장은 하지 않는다. Go review 논문은 이미 context 후보 생성·통합·pruning 및 변경 유형 기반 LSP 질의를 비교한다. [원문](https://arxiv.org/html/2606.01859v1)

Testora는 변경 의도와 base/head 동작 차이를 결합한다. DiffTestGen은 call graph 기반 진입점과 변경 커버리지 피드백으로 differential test 생성을 유도한다. 따라서 ‘테스트를 하나 더 돌린다’만으로도 새 기여가 아니다. [Testora](https://arxiv.org/abs/2503.18597), [DiffTestGen](https://arxiv.org/abs/2607.16024)

주장 가능한 후보는 **변경된 보장이 만드는 proof obligation을 식별하고, 그 판단에 필요한 사실을 충족하는 최소 증거를 선택해서, 독립적으로 확인된 context-required defect를 같은 비용에서 더 잘 검토한다**는 것이다. 아직은 연구 가설이다. 다음 실험은 새 프로젝트·PR에서 사례 포함 기준을 먼저 정하고, 이번 15 PR을 계속 test set으로 재사용하지 않는 것이 필요하다.

## 실행·재현·품질

- Planner/reviewer: gpt-5.6-sol. Judge: gpt-5.4.
- 후보 생성 15회 + 검증 105회 = 120회 성공. Judge 98회 성공, 빈 리뷰 22개는 모델 판정 생략. reviewer/judge 실행 오류 0. reviewer 도구 호출 0.
- 모델 입력으로 선택한 고유 소스 835개, revision 구간 1,425개를 실제 Git과 대조: 오류 0.
- 전체 rendered evidence 평균: generic 4,063 / claim BM25 4,076 / obligation 4,069 / no-base 4,068 / no-consumer 4,056 / random 4,062 reference tokens. 모든 입력이 4,096 이하. 정확히 같은 길이도, 모델-native 토큰 비용도 아니다.
- 모델의 잘못된 CTX ID 표기 2건: Ollama #12185 no-base, GoFr #1696 obligation. 출력과 점수는 그대로 유지했다. 두 경우 모두 실제 제공 ID에 문자가 덧붙은 형태이며, 자동으로 정상 인용으로 수정하지 않았다. 누락·중복 assessment 및 미확정 후보 보고 오류는 0.
- 테스트 우선 구현: 새 테스트 23개, 전체 111개 통과. 새 두 모듈의 단위·통합 테스트 statement coverage 98%.
- 실제 runtime test/race trace와 독립 사람의 Oracle/context-required annotation은 **이번 실행 범위에 포함하지 않았다**.

주요 산출물:

- 집계, 비교·gain/loss·층화 진단
- gold별 예측, 인용·후보 판정 감사
- 입력 소스 검증, 선택 기록
- 사후 소스 점검, 환경·입력 해시
- 원본 리뷰, 원본 판정, 테스트 coverage
