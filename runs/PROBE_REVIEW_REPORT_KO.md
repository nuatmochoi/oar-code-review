# Runtime 근거 추가의 리뷰 효과: 36회 대조 실험

2026-09-07. `probe_review1`. 새로운 final review **36회 완료**.

## 결론

**Click에서 정확한 원인 탐지는 늘었지만, 정상 사례 경고도 늘었다.** 따라서 runtime 근거가 원인 추론에 영향을 준다는 신호는 얻었으나, 더 신뢰할 수 있는 code review 방법이라는 주장은 아직 성립하지 않는다. AnyIO는 source-only부터 이미 3/3을 탐지하여 추가 이득이 없었다.

## 결과

각 `/3`은 같은 개발 사례의 세 proposal/모델 응답이다. 독립 PR 세 개가 아니다.

| 조건 | Click unsafe 정확 탐지 | Click safe 경로 경고 | Click 잘못된 predicate-raises 설명 | AnyIO unsafe 정확 탐지 | AnyIO safe 경로 경고 |
|---|---:|---:|---:|---:|---:|
| Source-only | 0/3 | 1/3 | 1개 | 3/3 | 0/3 |
| 환경 관찰 대조 | 0/3 | 2/3 | 2개 | 3/3 | 0/3 |
| Targeted 관찰 | 3/3 | 2/3 | 0개 | 3/3 | 0/3 |

Targeted는 source-only보다 unsafe 탐지를 늘렸지만 safe 경고가 1/3→2/3으로 늘었다. 환경 대조와는 safe 경고가 2/3으로 같았다. 두 대조 모두에 대해 safe 경고를 늘리지 않는다는 사전 성공 조건은 충족하지 못했다.

같은 repeat에서 unsafe 정확 탐지와 safe 무경고를 동시에 만족한 횟수는 Click targeted 1/3, 두 대조 0/3이다. AnyIO는 세 조건 모두 3/3이다. 다만 safe/unsafe는 독립 생성된 proposal을 재사용하므로 완전히 동일한 가설의 counterfactual 쌍은 아니다.

전체 issue는 17개다. 36개 raw 응답을 모두 읽고 명시적 항목별 author annotation을 작성했다. 독립·블라인드 adjudication이나 AACR 전체 semantic/strict F1 측정은 아니다. `T`는 target mechanism에 해당하는 설명을 뜻하며 safe에서의 T는 오경고이지 true positive가 아니다.

## 무엇이 달라졌나

### Click: 원인 교정과 실행 경로 확인은 다르다

Source-only와 환경 대조의 경고 세 개는 모두 `typing.BinaryIO`를 넣은 isinstance 자체가 예외를 낸다고 잘못 설명했다. Targeted 관찰을 받은 경고 다섯 개는 모두 predicate가 False인 뒤 binary stream에 str을 쓰는 실패 형태로 설명을 바꿨다. 다섯 개 모두 runtime evidence를 인용했다.

하지만 이 중 두 개는 safe 입력의 오경고다. Probe는 별도로 만든 binary TemporaryFile을 검사했을 뿐, production pager가 그 객체를 생성했다고 관찰하지 않았다. Safe companion은 text 임시 파일을 만든다. 모델이 **probe의 입력 객체를 실제 production 경로의 객체로 확대 적용**한 것이다. 모든 조건에 scope 제한 지침을 넣고 targeted block에도 전체 Click 경로를 실행하지 않았다고 표시했지만 이 오류를 막지 못했다.

따라서 ‘틀린 runtime 상식 수정’과 ‘해당 failure scenario가 실제 경로에서 도달 가능한지 확인’은 별도 단계다. 전자를 해결해도 후자를 생략하면 정확한 결함 설명 형태의 false alarm이 생길 수 있다. 이는 원문 기반의 사후 실패 분석이며, 새 검출 지표를 사전등록했다고 주장하지 않는다.

### AnyIO: 이미 source에서 판별 가능

이번 source에는 adapter body가 들어 있었다. 세 조건 모두 unsafe 옵션 미전달을 3/3 탐지하고 safe에서는 경고하지 않았다. Targeted 관찰은 실제 backend 선택·옵션 경계 전달을 보강하지만 이 사례에서는 정답률을 더 높이지 못했다. 추가 근거가 항상 추가 성능으로 이어지지 않는 사례다.

## 통제한 것과 남은 한계

- P2-A의 source 선택 12개를 그대로 사용했다. 각 task/repeat에서 source 내용·순서·ID가 세 조건에 동일하다.
- P1의 proposal 12개도 그대로 공유했다. 새 proposal 생성은 없다.
- 환경 대조와 targeted는 **전체 최종 prompt의 o200k reference token 수까지 일치**한다. Source-only는 추가 block이 없어 더 짧다.
- 환경 대조는 같은 기록의 Python/Trio 버전만 제공하며 명시적 inert padding을 사용한다. 의미적 밀도·문체·entropy까지 맞춘 placebo는 아니다.
- 모든 arm은 동일 verifier 지침이다. Runtime 사실의 제한 범위를 명시하고 HYPOTHESES는 citation enum에서 제외했다.
- 모델 gpt-5.6-sol, 동시 호출 4, timeout 300초, 자동 재시도 없음. Generation/schema 오류 0, 도구 호출 0.
- 코드·prompt·schema·36개 입력을 모델 호출 전에 동결했다. 모델 응답을 본 뒤 입력·정책을 조정하지 않았다.
- 이미 여러 번 관찰한 두 개발 family다. 자동 probe 생성, SCIP 고유 이득, 새로운 repository 일반화 또는 novelty 검증이 아니다. 동일 입력의 표본 수가 작아 유의성이나 안정적인 효과 크기를 주장하지 않는다.

## 비용

| 조건 | 신규 호출 | 전체 prompt reference tokens 합 | 출력 JSON reference tokens 합 | 호출 소요시간 합(초) |
|---|---:|---:|---:|---:|
| Source-only | 12 | 21,277 | 928 | 114.6 |
| 환경 대조 | 12 | 23,908 | 1,204 | 153.1 |
| Targeted | 12 | 23,908 | 2,266 | 116.7 |

Prompt 및 출력 JSON 수치는 reference tokenizer 기준이며 billed/reasoning token을 뜻하지 않는다. 기존 driver의 문자 기반 입력 추정량도 analysis에 별도로 남겼으며 exact token matching 판정에는 사용하지 않았다. 병렬 호출의 시간 합은 전체 경과시간이 아니다. 과거 proposal 12회, index/source 선택, runtime probe 실행 비용은 신규 36회 비용에 포함되지 않는다.

## 다음 우선순위

더 많은 독립적인 runtime 사실을 추가하기보다 **관찰값과 production 객체의 연결 근거**를 검증하는 것이 우선이다.

1. Producer가 반환한 객체 → 실제 predicate 인자 → consumer에 전달한 동일 객체를 연결하는 제한된 관찰을 만든다.
2. 같은 predicate 결과만 제공하는 조건과 실제 producer의 타입/identity 연결을 제공하는 조건을 비교한다. Safe/unsafe를 구분하는 근거가 실제로 달라지는지 모델 호출 전에 확인한다.
3. Targeted 근거에서 생산자 연결을 제거하는 ablation을 두어 scope 오류가 다시 생기는지 본다.
4. 개발 사례에서 정책을 고정한 뒤, 다른 저장소의 context-dependent defect에서 검증한다.

위 후속은 아직 실행하지 않았다. 현재 결과를 ‘문맥을 보강하면 더 잘 리뷰한다’는 일반적 주장으로 확대하지 않는다. 더 좁게는 **isolated predicate 관찰은 recall을 높일 수 있지만 production 경로 연결이 없으면 정상 사례에도 같은 원인을 적용할 수 있다**는 탐색적 관찰이다.

## 재검증·산출물

```bash
python -m pytest tests/test_probe_review.py tests/test_probe_review_analysis.py -q
python -m pytest tests -q
```

테스트 우선 절차로 비교 코드의 실패를 먼저 확인했고, mock 36-call 실행과 실제 동결 입력의 source/proposal/길이 일치를 검증했다. Runner statement coverage는 94%다.

최종 프로젝트 테스트 **233개**와 새 코드 lint가 통과했다. 검증 기록.

- 사전 계획
- 36개 원본 결과
- 항목별 판정
- 집계·비용
- 입력 manifest
- 결과 무결성 해시
