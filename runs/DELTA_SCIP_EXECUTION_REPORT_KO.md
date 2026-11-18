# DeltaSCIP 실행 결과 — 2026-09-03

## 결론

**base/head SCIP 인덱싱 → 변경 anchor → 후보 생성 → 예산 선택 → 검색 진단을 구현하고 17개 PR에서 실행했다. 사전 진행 기준은 통과하지 못했으며, LLM reviewer 본 실험은 실행하지 않았다.**

이번 결과는 “SCIP로 리뷰 recall이 높아졌다/낮아졌다”가 아니다. 현재 프로토타입의 인덱싱 범위와 선택 방식에서 먼저 해결할 문제가 확인된 것이다.

## 최종 실행 수치

최종 결과 폴더는 `runs/delta_scip_pilot17_final`이다. 다른 smoke/retry/pilot 폴더는 디버깅 이력이며 아래 수치와 섞지 않았다.

| 항목 | 결과 | 사전 기준 |
|---|---:|---:|
| base/head 모두 비어 있지 않은 인덱스 | 15/17 PR, 88.2% | ≥90%, 미통과 |
| 변경 anchor까지 존재하는 pair | 14/17 PR | 별도 범위 진단 |
| 변경 Go 줄 매핑, 성공한 15 pair 기준 | 3,105/4,024, 77.2% | ≥80%, 미통과 |
| 실패 PR도 분모에 포함한 변경 줄 매핑 | 3,105/4,335, 71.6% | 보조 진단 |
| 후보 4,000개 상한 도달 | 5 PR | 모두 TypeScript-Go |
| 독립 Oracle candidate recall | 미측정 | ≥70%, 확인 불가 |
| 요청 준비 | 96개 | reviewer 실행 수 아님 |
| 이번 LLM reviewer / judge 호출 | 0 / 0 | gate 미통과로 보류 |
| Python tests | 50개 통과 | 기존 19개 + 신규 31개 |
| 신규 검색·실행·진단 모듈 statement coverage | 93% | ≥80% |

요청 구성은 Diff/BM25/LSP 각 17개, SCIP-head/SCIP-delta-unranked/SCIP-delta 각 15개다. SCIP 조건 중 NATS PR의 3개 요청에는 추가 문맥이 없으며 `unavailable`로 표시된다. 서로 다른 성공 PR 집합의 평균을 직접 성능 비교에 사용하면 안 된다.

## 발견한 병목

### 1. 패키지 로딩 및 중첩 모듈

- GoFR #1325, #1355: scip-go는 exit 0이지만 문서 0개였다. 별도 `go list`에서 `updates to go.mod needed`를 확인했다. 이를 정상 인덱스로 처리하지 않았다.
- #1355 head는 PR의 명시 toolchain인 Go 1.22.10을 사용한 read-only 진단에서도 같은 의존성 갱신 요구가 나왔다. 단순히 최신 Go 버전 때문이라고 결론 내리지 않는다.
- GoFR #1617: root module 인덱스는 생성됐지만 변경 파일 4개가 NATS nested module에 있어 base/head 변경 줄 492개가 모두 빠졌다. 인덱스 생성 성공과 review target coverage는 다르다.
- Ollama #12185에서도 head 변경 파일 `ml/backend/ggml/ggml.go`가 인덱스에 없었다. 구체적 패키지/build 구성 원인은 추가 진단 대상이다.

원본 PR의 go.mod/go.sum은 자동 수정하지 않았다. NATS PR을 사후 제외하면 매핑률은 87.9%지만, 이는 원인 분석용 값일 뿐 gate 통과 수치로 사용하지 않았다.

### 2. 후보 풀에 들어와도 budget 선택에서 빠진다

이전 AI-assisted 주석에서 골랐던 5개 source 블록을 head snapshot과 대조했다. 5개 모두 기존 좌표의 source가 일치했다. 그중 인덱스 생성에 성공한 4개 PR에서 다음 overlap을 관찰했다.

| 기존 잠정 증거 블록 | Delta 후보 풀의 해당 줄 포함률 | Delta 선택 결과의 해당 head 줄 포함률 |
|---|---:|---:|
| TypeScript-Go #691, LSP-07 | 100% | 0% |
| GoFR #1681, TEST-02 | 43% | 0% |
| GoFR #1696, LSP-01 | 97% | 0% |
| Ollama #8938, LSP-07 | 100% | 0% |

이 표는 **기존 블록 전체 footprint의 overlap**이지 Oracle recall이 아니다. 대체 증거나 동일한 base source로도 검증할 수 있는지는 아직 평가하지 않았다. 따라서 0%를 “해당 결함의 증거가 전혀 없다”로 해석하면 안 된다.

선택 결과를 직접 보면 짧은 helper/getter와 간접 참조가 많이 선택된다. 현재 cost-density heuristic이 중요한 긴 source보다 짧은 블록을 선호할 가능성이 있다. 이것은 다음 selector 실험의 가설이며, 이 4개 사례의 정답에 맞춰 가중치를 수정하지 않았다.

### 3. base/head 중복이 예산을 소모한다

Delta 선택 결과는 요청당 평균 동일 source base/head 쌍 5.6개를 포함했다. 줄 번호를 제거한 source text가 완전히 같은 경우만 센 보수적 진단이다.

- 평균 추가 문맥: 약 3,767 추정 token.
- 중복 packet 비용: 약 924 추정 token, 약 24.5%.
- Unranked 조건의 중복 packet 비용: 약 1,756 추정 token.

현재 selector는 동일 revision 내 source 중복만 제거한다. cross-version 동일 source를 합치고, 실제 달라진 부분에만 양쪽 snapshot을 제공하는 대조가 필요하다.

## 구현 및 검증

- global symbol 버전 정규화, revision-specific local ID, 삭제 줄의 base 매핑, literal-only 변경의 enclosing definition 매핑.
- definition/body/signature/reference-owner/dependency/typed relation 변화 추출. 문자열 내부 공백도 body 변경에서 보존한다.
- seed/revision round-robin 후보 생성, source-span 중복 병합, token budget 선택, 상한·역할·누락 파일 audit.
- 병렬 Git worktree basename 충돌 수정 및 재현 테스트.
- 공식 protobuf export, tool/commit/environment cache key, 최종 실행 코드 사본과 SHA-256 보존.
- 최종 request 중복 없음, SCIP 블록 비용 재계산 일치, 모든 SCIP 요청의 예산 ≤4,096 추정 token, head-only에 base 블록 없음, retriever/request에 gold annotation 입력 없음 확인.
- Go exporter `go test ./...` 성공. 이 도구에는 별도 Go unit test가 없으며 실제 SCIP 파일 파싱과 Python 통합 테스트로 검증했다.

`ecc-codex-playbook`의 테스트 우선 절차에 따라 정렬·매핑·실패 처리의 회귀 테스트와 coverage 검사를 추가했다.

## 다음 순서

1. **Module-aware indexing**: 변경 파일의 가장 가까운 go.mod 기준으로 base/head 모듈별 인덱스를 생성한다. 서로 다른 모듈의 dependency version을 근거 없이 동일 심볼로 합치지 않는다. 의존성 복구가 필요하면 원본 조건과 별도 repair 조건으로 분리한다.
2. **Evidence packet 단위 선택**: 짧은 endpoint의 점수 합이 아니라 변경 anchor–관계–검증 source 묶음을 선택한다. 동일 base/head source를 합치고, 중간 경로가 생략된 현재 endpoint packet의 한계를 보완한다.
3. **독립 Oracle 주석**: 최소 evidence set과 동등한 대체 증거를 인간 2인이 확인한다. 현재 footprint proxy를 ground truth로 승격하지 않는다.
4. **그다음 LLM screening**: 고정 reviewer로 Diff/BM25/LSP/SCIP-head/paired-no-delta/Delta/선택 ablation을 비교하고, 동일 실제 token placebo를 추가한다. 현재 17 PR은 개발용이며 본 주장에는 별도 holdout이 필요하다.

## 재현 자료

- 방법·제약·실행 명령
- 최종 검색 진단 표
- 기계 판독 audit
- 최종 설정·source hash
- 준비된 reviewer 입력
- Coverage 결과
