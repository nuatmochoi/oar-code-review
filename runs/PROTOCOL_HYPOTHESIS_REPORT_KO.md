# Protocol hypothesis ablation: 완료 결과

2026-09-03 · 탐색적 실험 · 모델 `gpt-5.6-sol`

## 결론

**기존 직렬화 사례에서는 일반 리뷰보다 가설 생성→검증의 2단계 처리가 잘 탐지했지만, 프로토콜 특화 가설 생성은 일반 가설 생성보다 좋아지지 않았다.** 새 캐시 사례는 모든 방식이 맞혀 추가 이득을 구분하지 못했다. 현재 결과로 새로운 protocol-aware reviewer, LSP/SCIP retrieval, 최소 evidence subgraph의 우위를 주장할 수 없다.

총 84회 호출(최종 리뷰 60회 + 가설 생성 24회)을 완료했다. 실행 오류와 모델의 도구 호출은 각각 0회다. 다만 **최종 지적 33개 중 28개에 잘못된 인용 ID가 포함**되어 있어 내용 탐지 결과를 곧바로 사용 가능한 리뷰 출력의 성공률로 해석하면 안 된다.

근거: 집계, 완료 기록, 출력별 내용 매칭.

## 1. 무엇을 비교했나

같은 Diff, 같은 source context, 같은 runtime 정보를 주고 리뷰 사고 절차만 바꿨다. safe/unsafe는 대상 결함에 대해서만 쓰는 명칭이다. 두 variant는 Diff가 같고 unchanged companion의 구현이 다르다.

| 조건 | 호출 수/variant/반복 | 차이 |
|---|---:|---|
| 일반 리뷰 | 1 | 주어진 코드에서 구체적 regression 탐지 |
| 공통 checklist | 1 | 일반 리뷰 + 6종 protocol 질문 |
| 일반 2단계 | 2 | 일반 correctness 가설 → 공통 verifier |
| 프로토콜 2단계 | 2 | 변경→protocol→보존 조건→companion→반증 가설 → 같은 verifier |
| Oracle 가설 | 1 | 사람이 지정한 대상 가설 → 같은 verifier; 특권 정보 진단 |

Checklist는 직렬화, cache identity, callback, resource lifecycle, configuration consumer, concurrent state를 포함한다. 자동 가설은 최대 5개/1,200 reference tokens이고 source는 2,000 reference tokens 이하이다. 이번에는 예산 때문에 탈락한 가설이 없었다. 동일 호출 수 비교는 일반 vs checklist, 일반 2단계 vs 프로토콜 2단계이다. 1단계와 2단계는 동일 비용 비교가 아니며 native reasoning/output token도 맞추지 않았다.

주석과 docstring 설명을 가린 code-only 표현을 사용했다. 실제 source 사용량은 직렬화 safe/unsafe 319/299, 캐시 955/937 reference tokens다. 일반 literal과 행 좌표는 보존했다. 실행 witness와 정답 label은 일반 조건에 제공하지 않았다. 이 실험은 **고정된 증거가 주어진 뒤의 추론 비교**이며 자동 검색 비교가 아니다.

근거: 실행 전 고정 계획, 메소드·재현 명령, 입력.

## 2. 실행으로 확인한 두 결함 family

### A. 예외 직렬화: 기존 Requests 사례 재사용

Malformed JSON에서 새 Requests 예외를 발생시키는 변경이, 호환되는 `__reduce__`가 없는 companion class에서는 pickle 복원 실패를 일으킨다. 호환되는 구현에서는 성공한다. 두 base와 safe head는 pass, unsafe head만 목표 실패다. CPython 3.14.6 + stdlib json에서 확인했다.

Malformed JSON 3개 × pickle protocol 6개는 한 family 안의 18개 검사이지 독립 결함 18개가 아니다. 이전 결과를 보고 다시 구성한 같은 family라 **새 holdout이 아니다**. 설명 masking과 새 baseline 때문에 지난 실험 수치와 직접 전후 비교하지 않는다.

기존 4-cell 실행 증거.

### B. 캐시 namespace와 URL 삭제: 새 구성 사례

`BaseCache(key_fn=create_key)`를 namespace를 붙이는 callback으로 변경한다. 저장은 새 key를 사용하지만, 오래된 `delete_url()`은 instance의 key function을 우회한다. 따라서 URL 삭제 후에도 저장된 응답이 남는다. 수정된 companion은 `self.create_key()`를 사용해 정상 삭제한다.

[requests-cache #381](https://github.com/requests-cache/requests-cache/pull/381)의 실제 base/head 구현을 사용했지만, client 변경은 합성했고 오래된 method/helper를 조합했다. **실제 자연 발생 PR 결함이라고 주장하지 않는다.** 전체 package의 실제 저장·조회·삭제를 네트워크 없이 실행했다. URL 3개에서 두 base/safe head는 정상 삭제, unsafe head만 응답 잔존을 확인했다.

Python 3.14에서 오래된 cattrs import가 실패한 것은 환경 오류로 제외했다. 별도 Python 3.9.6 환경에서 Git snapshot의 package를 로딩해 통과했다. 실패 환경과 원자료도 보존했다. Context 함수들은 목표 메커니즘을 아는 상태에서 고정 선택했으므로 자동 retrieval의 성공 사례로 셀 수 없다.

4-cell 실행 증거, 같은 Diff, 환경 오류 기록.

## 3. 최종 내용 탐지 결과

숫자는 **unsafe에서 목표를 탐지하면서 같은 반복의 safe에서는 그 목표를 잘못 주장하지 않은 횟수 / 3회**다. 모든 조건의 safe 목표 오주장은 0/3이어서 unsafe 탐지 횟수와 일치한다. 다른 지적의 정오, 전체 precision, 위치 ±1 strict F1은 평가하지 않았다.

| 조건 | 예외 직렬화 | 캐시 namespace/삭제 |
|---|---:|---:|
| 일반 리뷰 | 0/3 | 3/3 |
| 공통 checklist | 1/3 | 3/3 |
| 일반 2단계 | 3/3 | 3/3 |
| 프로토콜 2단계 | 3/3 | 3/3 |
| Oracle 가설 | 2/3 | 3/3 |

직렬화 사례의 일반 리뷰는 앞선 encoding 경로 등 다른 문제에 집중하거나 빈 결과를 냈다. Checklist는 마지막 반복에만 목표를 찾았다. 두 2단계 방식은 세 반복 모두 목표를 찾았다. 다만 단계 분리, 추가 호출/추론량, 중간 가설 표현의 효과를 이 비교만으로 분해할 수 없다.

캐시 사례는 일반 리뷰부터 3/3이다. 두 개의 key 경로 불일치가 주어진 source에서 명확한 사례여서 protocol 특화가 필요한 난도를 제공하지 못했다. 일반 2단계 대비 프로토콜 2단계의 목표 탐지 이득은 두 family 모두 0이다.

최종 지적은 총 33개이고, 이 중 24개를 목표 내용으로 매칭했다. 나머지 9개는 이번 평가의 대상 밖이며 모두 오탐이라는 뜻이 아니다. 특히 안전 variant에서도 custom decoder의 subtype/state 손실 같은 별도 지적이 있어, **목표 오주장 0을 전체 false-positive 0으로 읽으면 안 된다.**

## 4. 가설 생성이 병목이었나

| Family | 가설 생성 방식 | unsafe 목표 가설 생성 | verifier에 전달 | 최종 목표 유지 |
|---|---|---:|---:|---:|
| 예외 직렬화 | 일반 | 3/3 | 3/3 | 3/3 |
| 예외 직렬화 | 프로토콜 | 3/3 | 3/3 | 3/3 |
| 캐시 삭제 | 일반 | 3/3 | 3/3 | 3/3 |
| 캐시 삭제 | 프로토콜 | 3/3 | 3/3 | 3/3 |

가설 생성·전달·유지 어느 단계에서도 프로토콜 특화의 차이가 없었다. 가설 생성으로 빠진 목표를 verifier가 새로 추가한 경우도 없다. 자동 가설에서 safe 목표 질문은 두 방식 모두 0회였다. 이는 모든 안전 가설의 타당성을 검증했다는 뜻이 아니다.

Oracle는 직렬화의 두 번째 반복에서 짧은 목표 질문을 받았는데도 빈 결과를 냈다. 그러므로 “가설만 주면 해결되고 생성만이 유일한 병목”이라고 결론낼 수 없다. 또한 Oracle는 자동 생성 payload보다 짧고 상세도가 다르다. **성능의 절대 상한이 아니라 표적 질문 제공 진단**이다. Verifier 출력에는 기각/미해결 사유가 없어 왜 빠졌는지 확정하지 못한다.

## 5. 인용 ID 문제와 민감도

허용된 `DIFF` 대신 `DIFF-client.py-L10` 같은 ID를 생성하는 문제가 최종 지적 28/33개에서 발생했다. 가설 36개 중 1개에도 CTX ID 오기가 있다. 원문과 내용 매칭 결과를 수정하거나 ID를 사후 정규화하지 않았다.

아래는 보조 진단으로, 내용상 목표를 찾았더라도 **해당 지적의 모든 evidence ID가 허용 집합에 속해야 인정**할 때의 unsafe 탐지 수다. 이는 AACR strict 점수가 아니고, ID가 맞는다고 근거의 entailment/위치까지 검증된 것도 아니다.

| 조건 | 예외 직렬화 | 캐시 namespace/삭제 |
|---|---:|---:|
| 일반 리뷰 | 0/3 | 0/3 |
| 공통 checklist | 0/3 | 0/3 |
| 일반 2단계 | 0/3 | 1/3 |
| 프로토콜 2단계 | 1/3 | 1/3 |
| Oracle 가설 | 0/3 | 1/3 |

따라서 이번 성공은 주로 **내용상 결함 식별**이다. 인용까지 유효한 자동 리뷰 pipeline이 완성되었다는 증거가 아니며, 이 작은 형식 차이를 protocol 방법의 우위로 주장하지 않는다. 다음 run에서는 인용 ID의 schema enum 또는 고정 renderer/validator를 모든 조건에 동일 적용하고, 현재 결과는 그대로 보존해야 한다.

## 6. 품질 검증과 한계

- 테스트 우선 개발 후 전체 테스트 150개 통과. 신규 3개 모듈 statement coverage 312/337, 약 93%다. 코드 검증 절차에 ecc-codex-playbook을 적용했다.
- Source spans 24개와 고유 source 파일 8개를 검사해 mismatch 0. Docstring 값을 정규화한 AST 비교이며 `__doc__` 관찰까지 포함한 의미 동등성 주장은 아니다.
- 실행 전 동결한 파일 15개의 종료 시 hash 변경 0. raw response 15개 파일 hash와 84개 annotation을 보존했다.
- 내용 매칭은 이 세션 작성자가 전체 출력을 검토한 것이다. 독립 인간 평가/별도 judge 합의가 아니다.
- 두 family와 같은 입력의 3회 반복은 독립 PR 표본이 아니다. 유의확률, 일반 recall 개선, 모델 간 일반화, 논문 novelty를 주장하지 않는다.
- 이 run에는 Diff-only/retriever ablation이 없다. **문맥을 보강해서 diff-only보다 좋아졌다는 원래 주장을 직접 입증한 실험이 아니다.** 같은 Diff에서 companion에 따라 실행 정답이 달라짐은 확인했지만, 모델의 context 활용·검색 이득은 별도의 통제 비교가 필요하다.

근거: source 검증, 종료 무결성, coverage, 최종 리뷰 원문, 가설 원문.

## 7. 다음 검증의 우선순위

이번 84회 비교는 완료했고, 같은 사례에서 추가 prompt 탐색은 하지 않았다. 다음 연구 단계는 다음 순서가 적절하다.

1. **독립 사례 확보:** 실제 버그 수정·회귀 테스트로 확인되는 protocol/consumer 결함을 고정 기준으로 모은다. 자연 발생 사례와 구성 사례를 분리하고, 모델 성공 여부로 사례를 골라내지 않는다. Repo 또는 defect family를 나눈 holdout을 둔다.
2. **Context 필요성 재검증:** 같은 reviewer로 Diff-only, 고정 크기의 무관 context, target-relevant source를 비교한다. 같은 Diff의 safe/unsafe 쌍을 유지하고, target witness는 평가에만 사용한다. 이전 Requests 사례는 개발셋으로만 둔다.
3. **검색 기여와 추론 기여 분리:** relevant-source 조건에서 이득이 확인된 사례에 한해 BM25/point-query LSP/paired-index selector를 같은 예산으로 비교한다. Reviewer는 일반 2단계로 고정해 retriever 차이를 본다. Protocol 특화는 일반 2단계를 반드시 대조군으로 유지한다.
4. **출력 신뢰성 통제:** ID validator를 공통 적용하고 판정 이유/미해결을 별도 구조로 저장한다. 독립 매칭 평가와 전체 non-target 지적 검토를 추가한다. 추가 비용 효과를 분해하려면 일반 reviewer 2회 집계 대조군도 필요하다.

현재 쓸 수 있는 가장 좁은 주장은 “구성된 context-dependent regression에서 증거가 이미 주어졌을 때, 일반 가설 생성·검증을 분리한 절차가 한 기존 사례의 직접 리뷰 실패를 줄였다”이다. **아직 protocol 전용 기법이나 semantic-index 기반 context 구성의 독자적 contribution은 입증되지 않았다.**
