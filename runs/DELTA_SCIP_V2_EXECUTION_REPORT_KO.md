# DeltaSCIP v2 실행 결과

후속 업데이트: 이 문서는 검색 단계 실행 기록으로 보존한다. 이후 실제 reviewer 비교는
[SCIP 실제 리뷰 결과](SCIP_REVIEW_EXECUTION_REPORT_KO.md)에 별도로 기록했다.

## 요약

요청한 **중첩 모듈 인덱싱, base/head 동일 source 통합, 증거 묶음 단위 선택**을 구현하고 기존 17개 PR에서 실행했다. 검색 범위와 예산 중복은 개선됐다. 그러나 묶음 선택 고유의 결함 탐지 이득은 아직 확인하지 못했다.

최종 실행은 `runs/delta_scip_v2_final17`이다. `delta_scip_v2_smoke3`는 예비 검증, `delta_scip_v2_pilot17`은 느린 선택 루프 때문에 중단한 개발 실행으로 구분한다. 원본 PR의 의존성 파일은 수정하지 않았다.

## 1. 검색 범위

| 지표 | v1 | v2 |
|---|---:|---:|
| 양쪽 revision에 source가 있는 PR | 15/17 | **16/17** |
| 선택한 모듈 전체 성공 | root scope 15/17 | **15/17** |
| 변경 Go 줄 매핑: 고정된 17 PR 전체 분모 | 3,105/4,335 = 71.6% | **3,803/4,335 = 87.7%** |
| source가 있는 PR만의 조건부 매핑 | 3,105/4,024 = 77.2% | **3,803/4,248 = 89.5%** |
| NATS #1617 변경 줄 매핑 | 0/492 | **481/492 = 97.8%** |
| 후보 4,000개 상한 도달 | 5 PR | 5 PR |

조건부 매핑의 분모는 15 PR과 16 PR로 다르므로 전체 17 PR 고정 분모도 함께 보고했다.

- **GoFR #1617:** NATS 모듈을 별도로 인덱싱하여 이전에 전부 누락됐던 변경 파일을 확보했다.
- **GoFR #1355:** Mongo 모듈의 source를 확보했다. 다만 root module 로딩은 여전히 실패해 부분 성공이다. 이것을 전체 저장소 인덱싱 성공으로 부르지 않는다.
- **GoFR #1325:** 의존성 파일 갱신 요구로 실패가 남아 있다.
- 서로 다른 모듈의 dependency version은 임의로 정렬하지 않았다. 모듈 경계를 가로지르는 완전한 semantic graph는 보장하지 않는다.

## 2. 중복 제거와 선택 조건

기존 요청 96개를 그대로 보존하고, 새 5조건 × 16 PR = **80개**를 추가하여 총 **176개 요청을 준비**했다. 요청 준비와 모델 호출은 다르다.

아래 새 조건은 모두 같은 16 PR이다. Token은 블록 content의 UTF-8 bytes/4 추정치이며 실제 tokenizer 값이나 전체 prompt 길이가 아니다.

| 조건 | 평균 content token 추정 | 동일 base/head source 중복 packet 비용 |
|---|---:|---:|
| 모듈 확장만 | 4,033 | 1,035, **25.7%** |
| 모듈 확장 + 보수적 revision 통합 | 4,031 | **0** |
| Witness source 개별 선택 | 4,055 | 87 |
| Witness 묶음 + density 선택 | 4,032 | **0** |
| Witness 묶음 + diversity 선택 | 4,046 | **0** |

같은 file·owner·정확한 source text이며 base/head 대응이 유일한 경우만 통합한다. 변경된 코드, 문자열 내부 공백 차이, 다른 심볼, 대응이 모호한 반복 source는 지우지 않는다. 두 revision의 좌표를 함께 보존했다. 따라서 이 값은 전체 의미적 중복 제거율이 아니라 보수적인 exact-source 진단이다. 서로 일부만 겹치는 window는 별도 한계로 남는다.

## 3. 기존 잠정 증거 블록이 선택됐는가

이전 AI-assisted 주석의 source 블록 전체 범위와 비교했다. 다음은 **head-source 줄 포함률**이다. 최소 Oracle evidence recall이나 결함 recall이 아니다.

| 사례 | v1 Delta | 모듈 확장 | 중복 제거 | Witness flat | Bundle density | Bundle diversity |
|---|---:|---:|---:|---:|---:|---:|
| TypeScript-Go #691, LSP-07 | 0% | 0% | 0% | 0% | 0% | 0% |
| GoFR #1681, TEST-02 | 0% | 0% | 0% | 0% | 0% | 0% |
| GoFR #1355, LSP-01 | N/A | 0% | 0% | 26% | 0% | 0% |
| GoFR #1696, LSP-01 | 0% | 0% | 0% | 0% | 0% | 29% |
| Ollama #8938, LSP-07 | 0% | 0% | 0% | **45%** | **45%** | **45%** |

Ollama에서는 기존 LSP target line도 실제 포함됐다. 다만 같은 개선이 **flat 대조군에도 나타나므로 묶음 선택 고유의 효과로 해석할 수 없다.** GoFR #1696의 29%는 기존 LSP target line을 포함하지 않는다. 단순 overlap 증가를 충분한 증거 확보로 승격하지 않는다.

TypeScript-Go #691과 CORS #1681의 기존 블록은 여전히 선택되지 않았다. 동등한 대체 증거나 base source의 충분성은 이 footprint 지표가 판정하지 않는다. 새 구조가 일부 증거를 입력에 넣는 데 도움이 됐지만, 중요한 증거를 안정적으로 고르는 문제는 해결되지 않았다.

## 4. 실제 출력 검증

- Python **69개 테스트 통과**. `ecc-codex-playbook`의 테스트 우선 절차로 모듈 경계, revision 정렬, 공백/literal, 모호한 중복, 예산, 중간 참조, typed relation, 실패 처리 테스트를 추가했다.
- 검색·실행·진단 관련 7개 모듈의 statement/branch 합산 coverage **92%**.
- 새 요청 **80개**, 출력 블록 **1,814개**, revision별 source 범위 **2,941개**를 검사했다.
- 선택한 묶음 **304개**에서 필요한 source ID, 실제 변경 entry 줄, source-level 참조 위치의 포함 여부를 검사했다.
- 모든 source를 실제 Git snapshot과 비교했고 오류 **0개**였다. Static relation의 실행 가능성이나 결함의 사실성을 검증했다는 뜻은 아니다.
- 모든 새 요청이 4,096 content-token 추정 예산 이내이며, 출력 token 비용 재계산이 일치했다.
- 기존 96개 요청의 값이 보존됐고, 테스트한 현재 retriever 코드와 실행 시 보존한 source hash가 일치했다.
- 선택기 성능 수정 전후, 예비 3 PR × 2 bundle 조건의 출력 블록 전체가 일치했다. 결과를 바꾸지 않고, 거절 후보마다 반복하던 전체 순위 탐색을 줄였다.

## 5. LLM 본 실험 상태

**이번 reviewer/자동 judge 호출은 0회다. 새 semantic/strict F1이나 defect recall 결과는 없다.**

| 진행 기준 | 상태 |
|---|---|
| 비어 있지 않은 paired source ≥90% | 16/17, 통과 |
| 선택한 모듈 전체 성공 ≥90% | 15/17, 미통과 |
| 변경 줄 매핑 ≥80% | 조건부 89.5%, 전체 분모 87.7%, 통과 |
| 독립 Oracle candidate recall ≥70% | 독립 주석 미완료, 확인 불가 |

현재는 **모듈별 인덱싱으로 검색 누락을 줄이고, 동일 source 중복 비용을 제거했다**고 말할 수 있다. **“Diff-only로 판단할 수 없는 결함의 review recall을 높였다”**는 주장은 아직 할 수 없다.

다음 관문은 남은 module loading 실패의 복구 정책, 최소·대체 증거에 대한 독립 주석, 그리고 그 뒤의 고정 reviewer 비교다. 특히 충분한 증거가 들어간 사례에서만 utilization을 따져야 하며, 단순히 모델 반복 수를 늘려 현재 선택 실패를 가리면 안 된다. 이번 17 PR은 개발 중 관찰한 cohort이므로 최종 주장에는 별도 holdout이 필요하다.

## 자료

- v2 방법·제약·재현 명령
- 검색 진단 상세 표
- 기계 판독 검색 결과
- 소스·묶음 검증 결과
- 코드 snapshot·실행 설정
- Coverage
