# 판별 질문 검색 실험 결과

2026-09-07. `question_review1`: 60회 실제 모델 호출 완료.

## 결론

새 `question_search`는 성공 gate를 통과하지 못했다. AnyIO의 필요한 질문은 생성됐지만 답을 담은 adapter 구현을 선택하지 못했다. 기존 reserve/claim 검색은 그 구현을 포함했고 unsafe 결함을 3/3 탐지했다. 따라서 **질문을 더 자세히 쓰는 것만으로 context 선택이 개선된다는 주장은 이 실험에서 지지되지 않는다.**

다음 우선순위는 단순 slicing 확대가 아니라 **질문에 등장하는 코드와 질문의 답을 담은 구현을 구분하는 선택 기준**이다. Click의 반복적 런타임 오판에는 별도의 작은 실행 검사가 필요하다. 이는 후속 가설이며 아직 개선 성능을 측정하지 않았다.

## 설계·완료 범위

- 기존에 관찰한 Click/AnyIO 두 family의 safe/unsafe 네 입력. 새 PR holdout이 아니다.
- 각 입력 3회 반복, diff-only 질문 생성 12회 + final review 48회 = 60회.
- 모델 `gpt-5.6-sol`. 생성 실패 0, schema 오류 0, 모델 도구 호출 0.
- 동일 task/repeat의 후보·질문 payload를 네 arm에 공유했다. 후보 17개, 최종 issue 20개.
- Context arm은 공통 paired-SCIP 후보 풀, source 800 o200k_base-token 상한. 원문 whole chunk를 선택했다.
- 최종 source는 file/start/key 순으로 정렬하고 ID를 재부여했다. 동일 길이 대조는 아니다.
- 첫 12개 proposal을 검증한 뒤 최종 48개 입력을 모두 동결하고 리뷰를 시작했다. 결과 관찰 후 selector를 수정하지 않았다.
- 동결 코드·입력 및 raw/aggregate 일치 검증을 통과했다. 실행 전 190 tests, 추가 평가 코드 포함 최종 프로젝트 `tests/` 192 tests가 통과했다.

사전 계획은 동결본, 구현 설명은 method에 있다.

## 결과: 실제 원인 탐지와 safe 경고를 구분

각 셀은 같은 입력의 모델 반복 횟수 `/3`이다. 독립 PR 세 개를 뜻하지 않는다.

| 조건 | AnyIO unsafe 정확한 원인 탐지 | AnyIO safe 경로 경고 | Click unsafe 정확한 원인 탐지 | Click safe 경로 경고 |
|---|---:|---:|---:|---:|
| Diff 재검토 | 0/3 | 1/3 | 0/3 | 2/3 |
| 기존 reserve | 3/3 | 0/3 | 0/3 | 3/3 |
| Claim 검색 | 3/3 | 0/3 | 0/3 | 0/3 |
| Question 검색 | 0/3 | 0/3 | 1/3 | 2/3 |

`safe 경로 경고`는 실제 원인을 언급한 경고뿐 아니라 같은 경로에 잘못된 원인으로 낸 경고도 포함한다. 예를 들어 Click reserve의 safe 경고 3개는 모두 `isinstance(..., typing.BinaryIO)` 자체가 TypeError를 낸다는 잘못된 설명이다. 정확한 원인 경고만 세면 이를 0/3으로 오해하게 된다.

Click의 실제 unsafe 원인은 nominal type check가 False여서 binary 파일이 text wrapper 없이 전달되고, 이후 str write가 실패하는 것이다. Safe companion은 해당 실행 범위에서 text 임시 파일을 생성한다. 전체 코드에 다른 결함이 없다는 의미의 safe는 아니다.

Click question-search safe r2는 올바른 실패 형태를 가정하면서도 predicate 자체가 거부될 수 있다는 잘못된 대안을 섞었다. 이를 mixed-mechanism으로 따로 표시하고 safe 경고에 포함했다. 최종 결과 전체에서 잘못된 predicate-raises 설명만 낸 issue는 8개, 혼합 설명은 1개다.

동일 repeat에서 unsafe 정확 탐지와 safe 무경고를 함께 만족한 횟수는 AnyIO reserve/claim 각각 3/3, 나머지 AnyIO 0/3이다. Click은 question 1/3, 나머지 0/3이다. 단, safe/unsafe는 proposal을 독립 생성했으므로 동일 가설을 사용한 엄격한 paired-context intervention은 아니다.

평가는 원문 60개를 읽은 세션 작성자의 명시적 annotation이다. 독립 두 평가자 검증이나 AACR 전체 semantic/strict F1 측정이 아니다. 항목별 판정, 집계.

## 실패 지점

### 1. AnyIO: 질문 생성보다 answer-source 선택 실패

AnyIO의 6개 proposal 모두 custom clock 옵션이 adapter에서 전달되는지 묻는 후보를 포함했다. 이는 정답을 이미 확정했다는 뜻이 아니라 실제 원인을 가능한 설명 중 하나로 포함했다는 뜻이다.

`TrioBackend.run`은 공통 후보 풀에 존재했다. Reserve와 claim 검색은 safe/unsafe 합쳐 6/6회 포함했고, question 검색은 0/6회 포함했다.

대표적으로 unsafe r2에서 question 검색은 52-token `client.py::run_job` 하나를 선택한 직후 두 질문의 정규화 relevance proxy가 모두 1.0이 됐다. 이 코드는 옵션 전달을 요청하는 호출부이지, adapter가 실제로 옵션을 전달하는지 답하는 구현이 아니다. 이후 greedy 질문 점수 이득이 없어져 기본 paired 순위로 채웠고 adapter를 놓쳤다.

따라서 단순한 ‘문맥 길이 초과’만이 아니라 **질문의 전제가 등장하는 위치를 질문 해결로 오인하는 점수 설계**가 관찰된 병목이다. AST에 parameter와 call이 함께 있다는 특징도 실제 인자 전달의 증거는 아니다. BM25+operation+packing 전체를 한꺼번에 비교했으므로 어느 한 항의 독립적 효과를 입증한 것은 아니다.

### 2. Click: 후보 원인의 편향과 runtime 사실 오판

Click의 6개 proposal은 실제 `False → raw binary → str write` 원인을 정확한 가설로 생성하지 못했다. 5개 proposal에 predicate가 예외를 낼 수 있다는 잘못된 후보가 포함됐다. 최종 리뷰에서 원인을 새로 도출한 경우가 있으므로 proposal은 엄격한 최종 출력 제한도 아니었다.

Question 검색은 producer `_tempfilepager`와 consumer `echo_via_pager`를 모두 0/6회 선택했다. Claim 검색은 각각 1/6, 4/6회 포함했으나 unsafe 정확 탐지는 0/3이었다. 단지 consumer가 포함된다는 것만으로 충분하지 않았다.

Click question-search r1은 safe/unsafe의 source blocks가 동일한데도 unsafe만 정확한 원인을 보고했다. 두 입력의 proposal은 달랐다. 따라서 이 1/3 결과를 source가 safe/unsafe를 구별하게 했다는 증거로 해석할 수 없다.

## 비용과 문맥 길이

| 단계 | 호출 | 입력 token driver 추정 합 | 출력 JSON o200k token 합 | 호출 소요시간 합(초) |
|---|---:|---:|---:|---:|
| 공통 proposal | 12 | 7,062 | 4,710 | 314.2 |
| Diff final | 12 | 12,897 | 1,022 | 227.4 |
| Reserve final | 12 | 21,727 | 1,718 | 187.6 |
| Claim final | 12 | 21,965 | 1,222 | 191.9 |
| Question final | 12 | 22,078 | 1,262 | 187.1 |
| 합계 | 60 | 85,729 | 9,934 | 1,108.2 |

입력 수치는 기존 driver 추정량, 출력은 JSON을 재직렬화한 reference tokenizer 수치로 서로 같은 회계 기준이 아니다. 청구 token, 숨겨진 reasoning token, 달러 비용을 뜻하지 않는다. 병렬 실행이므로 소요시간 합은 전체 경과시간이 아니며 index 구축·검색 시간도 포함하지 않는다. 단독 한 arm 운영 비용에는 그 arm의 final 12회 외에 proposal 12회를 모두 더해야 한다. 이번 비교에서는 proposal을 공유해 총 60회만 사용했다.

Source token 범위: Click reserve 754, claim 779–800, question 783–795; AnyIO reserve 791–799, claim 781–799, question 792–797. Diff는 source 0이지만 공통 가설 payload를 추가하므로 최종 prompt가 diff 원문만인 single-pass baseline은 아니다.

## 다음 실행 gate

1. **답을 담은 source 판별**: 정답 endpoint 이름을 입력하지 않고, 호출 대상 해석·actual/formal 인자 연결·생산/소비 관계를 근거로 후보를 분류한다. 무관한 이름 중복, 중간 forwarding wrapper, 옵션 소모/제거, alias, 분기 fixture로 검증한다. 미해결은 unknown으로 남긴다.
2. **작은 runtime 검사**: 잘못된 predicate 주장과 forwarding 사실을 구별하는 allowlisted probe. 결과는 관찰한 값·환경 범위에 한정한다. Gold witness를 probe 생성에 제공하지 않는다.
3. **문장 구성은 조건부**: 답을 담은 source를 찾았으나 전체 함수가 budget에 안 들어갈 때만 수행한다. Guard/alias/cleanup 누락 검증 없이 sound slice라고 부르지 않는다.
4. 개발 사례에서 정책을 고정한 뒤 새 repository-disjoint cohort, 같은 budget, 결정적 source 제거, token-matched placebo, head-only/paired 대조로 검증한다.

수정된 우선순위와 미실행 범위는 진행 상태에 명시했다. 이번 결과는 context 선택의 중요성을 일부 재현하지만 question 검색, paired index 또는 새로운 방법의 일반적 contribution을 입증하지 않는다.

## 재검증

기존 실행 디렉터리는 재실행/덮어쓰기를 막는다. 추가 모델 호출 없이 평가 및 freeze 검사를 재검증하려면:

```bash
python -m pytest tests/test_question_context.py tests/test_question_review_analysis.py -q
python -m pytest tests -q
```

저장소 루트에서 대상 없이 `pytest -q`를 실행한 검사는 내려받은 CodeQL test fixture까지 수집해 중단했다(`SystemExit: wrong case` 등 collection 오류). 프로젝트 검증은 명시적으로 `tests/`를 지정한 192개 결과다.

raw 결과, 완료 기록, source 선택 audit, 결과 무결성 해시.
