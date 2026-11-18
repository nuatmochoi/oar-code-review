# Same-Diff / missing companion edit: 실행 결과

2026-09-03 · Requests 기반 **독립 defect family 1개** · reviewer 60회 완료.

## 결론

**동일 Diff라도 외부 문맥에 따라 특정 회귀의 정답이 달라지는 실행 사례는 확보했다. 그러나 문맥을 주면 모델이 이를 안정적으로 발견한다는 주장은 아직 입증하지 못했다.**

처음 30회에서는 모든 조건이 목표 직렬화 결함을 놓쳤다. 실행 환경을 공통으로 명시한 사후 탐색 30회에서는 자동 클래스·상속 문맥과 Oracle이 각각 **3회 중 1회** 정상/결함 쌍을 올바르게 구분했다. 첫 반복에서만 성공했고 두 번째·세 번째 반복은 놓쳤다. BM25도 필요한 class를 검색했지만 대상 결함을 보고하지 않았다.

따라서 이번 결과는 **실행으로 검증한 context-dependent 평가 사례 + 제한적인 모델 활용 신호**다. 일반적인 recall 개선, AST/LSP/SCIP 우위 또는 논문 contribution 확정이 아니다.

## 1. 무엇을 새로 만들었나?

[Requests #5856](https://github.com/psf/requests/pull/5856)의 `Response.json()` 예외 변환과 import 변경을 anchor로 사용했다. 표준 JSON 오류 대신 Requests wrapper 오류를 전달하는 변경이다. [후속 버그 #6628](https://github.com/psf/requests/issues/6628)은 JSONDecodeError 역직렬화 실패를 보고했고, [수정 #6629](https://github.com/psf/requests/pull/6629)는 해당 class에 JSON backend의 `__reduce__`를 명시적으로 위임하는 구현을 추가했다.

이 class의 수정 전/후 구현을 **unchanged context**로 놓고, 두 문맥에 같은 anchor Diff를 적용했다. 2021/2024 revision을 결합한 **실제 PR 유래 파생 반사실 사례**다. 실제 원 PR 전체나 자연 발생 독립 사례 두 개라고 세지 않는다. 기존 Go/AACR 15 PR와는 다른 프로젝트다.

| unchanged companion 문맥 | base | 동일 Diff 적용 후 head |
|---|---|---|
| JSON backend의 `__reduce__` 위임 있음 | 통과 | 통과 |
| 위임 없음 | 통과 | **복원 시 `doc`, `pos` 인자 누락으로 실패** |

공통 witness는 실제 `Response.json()`를 실행한 뒤 발생한 예외를 pickle round-trip한다. 유효 JSON도 검사했다. invalid payload 3개 × protocol 0~5의 18검사에서, 세 통과 cell은 각각 18/18 성공하고 결함 head만 18/18 대상 실패를 보였다. 총 72검사는 **한 family 내부 실행 검사**이지 표본 수 72가 아니다. multiprocessing 자체를 구동한 것은 아니고 그 예외 전달에 필요한 pickle round-trip을 직접 검사했다.

두 Diff의 SHA-256은 `84a1cf8e5a1beaf3918917c468ccfda7ebf8835b291368a1973b82bd520adb0c`로 같다. 각 문맥 내 base→head에서 models.py 외 파일은 바뀌지 않는다. base의 모든 경로나 package 전체가 무결함이라는 뜻은 아니며, 이번 target API와 witness의 회귀 여부만 확인했다.

## 2. 모델 비교

`gpt-5.6-sol`, 조건별 두 문맥 × 독립 호출 3회. 도구 없이 공개형 코드 리뷰를 요청했다. pickle이라는 목표, 정답 라벨, PR 번호/후속 설명, witness 출력은 제공하지 않았다. 추가 소스는 2,000 reference-token 상한이며 정확히 같은 길이는 아니다.

표의 점수는 **결함 쪽에서 목표 오류를 지적하고 정상 쪽에서는 같은 오류를 지적하지 않은 반복 수 / 3**이다. 전체 code-review accuracy나 전체 precision이 아니다.

| 조건 | 추가 문맥 토큰: safe / unsafe | 주 실험: 환경 미명시 | 사후 탐색: 환경 명시 |
|---|---:|---:|---:|
| Diff-only | 0 / 0 | 0/3 | 0/3 |
| BM25 | 1,986 / 1,934 | 0/3 | 0/3 |
| 자동 클래스·상속 문맥 | 507 / 412 | 0/3 | **1/3** |
| Source Oracle | 898 / 804 | 0/3 | **1/3** |
| 무관한 status-code 소스 | 1,201 / 1,201 | 0/3 | 0/3 |

두 실험 모두 모든 조건에서 safe 문맥에 대한 **목표 직렬화 오류 주장은 0/3**이었다. 이는 모든 리뷰가 실제 무오탐이라는 뜻이 아니다. 다른 예외 처리·호환성 지적은 전체 정오를 판정하지 않았다.

### 주 실험과 사후 탐색을 섞지 않은 이유

주 실험의 첫 10개 출력을 본 뒤, 모델이 주로 Python 2/simplejson 문제에 집중한다는 사실을 관찰했다. 하지만 witness의 실행 환경은 CPython 3.14.6 + stdlib json이었다. 추가 실험에는 이 환경과 Python 2 미지원이라는 공통 사실만 명시했다. Diff·소스·조건·모델은 그대로 유지했다.

이 추가 조건은 **결과를 본 뒤 정한 탐색**이다. 최초 실패를 성공으로 재채점한 것이 아니며, 독립 test set에서 environment-context 효과를 확정한 것도 아니다.

- 주 실험: 30 리뷰, 31개 지적, target 일치 0개.
- 환경 명시: 30 리뷰, 5개 지적, target 일치 2개, 빈 리뷰 25개.
- 두 target 일치는 모두 첫 반복의 unsafe 문맥이다. 두 번째·세 번째 반복은 target을 놓쳤다.

## 3. 성공한 출력은 실제로 무엇을 말했나?

자동 클래스·상속 조건은 `requests/exceptions.py:34`를 지적했다. class의 다중 상속 순서 때문에 OSError의 reduction이 선택되고, `doc`와 `pos`를 잃어 pickle 복원에 실패한다는 시나리오를 설명했다. companion class에 `__reduce__`를 추가해 `CompatJSONDecodeError.__reduce__(self)`로 위임하라고 제안했다. 이는 실제 실행 실패 및 후속 수정의 메커니즘과 일치한다.

Oracle도 같은 메커니즘을 지적했다. 다만 위치는 exceptions.py:25로, JSONDecodeError 선언 자체보다 앞선 RequestException 생성자 부근이었다. 이번 목표 내용 매칭은 이 위치 차이로 제외하지 않았다. Strict ±1줄 점수로 재포장하지 않는다.

자동 조건의 구현은 **추가된 raise → import alias → class → 동일 파일의 base class**를 따라가는 작은 Python AST baseline이다. 실제 LSP, SCIP, CodeQL, data-flow, 최소 evidence subgraph 최적화는 이번에 사용하지 않았다.

## 4. 여기서 어느 방향이 더 유망한가?

이번 한 사례에서는 BM25도 exceptions.py의 필요한 class를 제공했다. 따라서 **찾아왔는가만으로 실패를 설명할 수 없다.** Oracle조차 환경 명시 후 1/3만 성공했다는 점은, 가설 생성·프로토콜 경로 인식·불필요한 후보 억제도 함께 점검해야 한다는 신호다. 원인을 확정하는 분해 실험은 아니다.

다음 가설은 다음처럼 좁히는 편이 타당하다.

> 배포 환경에서 실제 사용되는 프로토콜을 기준으로, 변경된 객체/값이 통과해야 하는 보존 조건을 생성하고, 필요한 companion 구현과 실행 witness를 검토에 제공한다.

예를 들어 이번 사례의 흐름은 `변경된 예외 타입 → pickle 전달 → 재구성 인자 보존 → unchanged class의 reduce 구현`이다. 캐시에서는 `변경된 key 생성 → 기존 invalidation 경로`, 설정에서는 `새 옵션 → 기존 검증/배포 소비 경로`가 후보가 될 수 있다. 이 확장은 아직 실행하지 않았다.

단, missing companion changes 자체는 기존 change recommendation 계열과 겹친다. [CMSuggester](https://www.sciencedirect.com/science/article/pii/S0164121219302158)는 multi-entity edit를 보완할 method change를 추천한다. version 간 의미 차이를 review에 제공하는 방향도 [GETTY, ASE 2017](https://par.nsf.gov/servlets/purl/10061392)와 경계를 설명해야 한다. 신규성은 별도의 선행연구 검토가 필요하다.

### 다음 실험을 한다면

1. **새 family 확보:** 서로 다른 저장소의 serialization, cache invalidation, configuration-consumer 사례를 수집한다. 3개 이상 프로젝트·서로 다른 10개 이상 family의 feasibility set부터 만들고, 이후 별도 holdout을 둔다. 같은 bug의 payload/protocol 변형은 독립 표본으로 늘리지 않는다.
2. **환경 정보는 처음부터 공통 제공:** 모델 결과를 보기 전에 runtime/config/dependency 범위를 고정한다. `runtime facts 있음/없음`은 독립 ablation으로 분리한다.
3. **같은 증거로 가설 생성만 비교:** 일반 리뷰, protocol-obligation checklist, 자동 obligation 생성, obligation+실행 witness를 동일 호출 예산으로 비교한다. 사람이 target pickle 가설을 직접 준 조건은 Oracle-hypothesis 진단으로 별도 표기한다.
4. **댓글 설명 의존성 제거:** safe class에 있는 수정 설명 docstring을 제외한 code-only ablation, 동일 토큰 placebo, 관계가 끊긴 문맥을 포함한다. 이번에는 하지 않았다.
5. **발견과 검증 분리:** 사전에 목표 가설을 알려주면 가능한데 공개형 리뷰에서는 못 찾는지 확인한다. 그 경우 contribution 후보는 검색량 증가보다 protocol-aware hypothesis generation에 가깝다.

좋은 조건을 찾을 때까지 같은 한 사례에서 프롬프트를 바꾸는 방식은 이번 2개 실험에서 중단했다. 다음 성능 주장은 새 family와 고정 계획을 바탕으로 해야 한다.

## 5. 검증과 한계

- 모델 60회 성공, 실행 오류 0, reviewer 도구 호출 0, 잘못된 evidence ID 0.
- 선택 소스 span 20개를 snapshot 원문과 대조: 오류 0. Diff-only와 무관 문맥은 각 쌍의 모델 입력도 byte-equivalent hash다.
- 별도 새 prepare 실행에서 requests가 동일하게 재생성됨을 확인했다. 이 validation 실행은 모델 평가 표본에 추가하지 않았다.
- 핵심 builder는 테스트 실패를 먼저 확인하고 구현했다. `ecc-codex-playbook`에 따라 테스트/실행 검증과 재현 문서를 추가했다. 신규 테스트 19개, 전체 130개 통과. builder와 runtime-followup 모듈의 unit+실제 CLI 실행 statement coverage는 합계 98%다.
- 대상 내용 매칭은 이 작업의 작성 에이전트가 60개 응답을 읽어 판정했다. 독립 사람 또는 별도 judge 모델의 검증은 하지 않았다. 원문과 매칭 근거를 남겼다.
- 독립 defect family 1개, 과거 공개 코드, 미래 수정으로 만든 safe context, 환경 추가의 사후 선택, 설명 docstring 잔존, token 길이 불일치가 주요 한계다.
- 따라서 **원리 검증은 가능하지만, 안정적 성능 향상과 신규 contribution은 보류**한다.

## 산출물

- 실행 gate·72개 검사, 동일 anchor Diff
- 주 실험 집계, 환경 명시 집계
- 주 실험 원문, 환경 명시 원문
- 주 실험 target annotation, 환경 명시 annotation
- 선택/토큰 기록, 입력 검증, 최종 coverage
- 방법 및 실행 명령, 최초 계획, 사후 탐색 계획

기존 raw 결과는 수정하지 않았다. 경고가 있던 첫 준비본 `counterfactual_requests_pilot1`도 보존했으며 그 준비본에는 모델을 실행하지 않았다. historical dependency venv는 network-free witness 전용이며 production 배포용 추천 환경이 아니다.
