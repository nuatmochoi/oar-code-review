# S2G·CIRAG·QAFD 기반 코드리뷰 개발 실험 결과

2026-09-29. **주 비교 136회·기존 검색기 대조 10회를 완료했다. 이번 축소 구현에서 세 기법 결합이 강한 BM25 기준선을 능가한다는 증거는 얻지 못했다.** 가장 뚜렷한 병목은 질문 생성보다, 질문이 요구하는 구현을 최종 문맥에 넣는 선택 정책이었다. 이를 수정한 사후 실험에서 AnyIO의 판정이 개선됐지만 BM25와 같은 수준이며, Click 후속 호출은 timeout으로 미완료다.

이번 실행은 기획서의 **P0 개발 단계**다. 이미 관찰된 Click·AnyIO 파생 사례 2개와 서로 관련된 synthetic 제어 사례 2개를 사용했다. 계획했던 새 12-family·최소 3개 저장소 P1 파일럿은 아직 실행하지 않았다. 새 사례를 기존 개발 사례로 대체해 holdout 점수로 보고하지 않았다.

## 실행 범위와 무결성

| 항목 | 실제 실행 |
|---|---|
| 모델 | `gpt-5.6-sol`, reasoning `low`, 반복 1회 |
| 주 비교 | 4개 개발 사례 × 7조건; 공통 질문 4 + controller 80 + final 52 = **136회** |
| 기존 검색기 추가 비교 | claim-BM25·reserve·answer-source, **10회** |
| 사후 검색 정책 수정 | 계획 8회 중 6회 시도·4회 성공; Click controller 2회 timeout으로 final 2회 차단 |
| 문맥 예산 | 추가 evidence 1,600 `o200k_base` reference tokens; graph 단계별 800→1,200→1,600 |
| 보존 | 실제 prompt·응답·trace·source/index hash·질문·각 단계 선택·사용량 |
| 주 비교·기존 대조 | 전송/스키마/근거 ID 오류 0, 관찰된 모델 도구 호출 0 |
| 기계적 검사 | 원문행 누적·packet cap 132건 통과; 입력/코드/최종 packet 해시 일치 |
| 코드 검사 | 최초 8개 통과; 우선 배정·ID 제약 추가 후 관련 **10 tests passed** |
| 실행 정답 | 실제 사례 8개 cell 재실행 및 indexed source 일치 확인; synthetic 8개 cell 통과 |

원문행과 경로는 중간 라운드에서 제거하지 않는다. 예산은 유지된 원문과 현재 source header·경로 직렬화를 합산한다. 반복해서 보낸 문맥과 controller 출력은 실제 모델 토큰 비용에 매번 포함한다. 동일 source cap을 동일 총 연산량으로 해석하지 않는다. 모델 도구 목록 자체가 완전히 비어 있었다는 주장은 하지 않으며, trace에서 실제 도구 호출 0건을 확인했다.

처음 배치는 controller schema의 `uniqueItems`가 서버에서 거부됐다. **56회 시도 전체를 비교에서 제외**했고, 그중 성공한 16개 출력도 재사용하지 않았다. 서버용 schema에서 미지원 제약을 제거하되 전체 제약은 로컬 검증으로 유지한 뒤 새 배치를 실행했다. 사후 수정의 첫 배치도 근거 ID 오기 때문에 7회 전체를 제외했다. 이후 visible ID를 enum으로 제한해 네 world를 다시 시도했다. 총 **217회 시도 = 분석 배치 성공 150 + 해당 배치 timeout 2 + 제외 배치 63 + 별도 smoke 2**다. 성공 호출 수에는 controller가 포함되므로 리뷰 사례 수로 해석하지 않는다. 호출 원장, 주 비교 제외 사유, 수정 실험 제외 사유, 무결성 감사, 검증 기록.

## 무엇을 비교했나

| 조건 | 구현 |
|---|---|
| D | diff + 공통 질문; 동일 diff pair 내 출력을 공유 |
| L | 전체 source AST-BM25 |
| R | 균일 가중치 그래프 + 고정 AST 단위 + 반복 검색 |
| S | R에 구조화된 missing-fact query 추가 |
| C | 관계/부분 anchor에서 AST 함수·관련 범위로 확장 |
| Q | 질문 관련성으로 edge를 가중한 50-step PPR 근사 |
| SCQ | S+C+Q 결합 |

S/C에는 원 논문의 학습을 적용하지 않았고 Q도 공식 QAFD flow 구현이 아니다. C의 L1은 whole AST function/statement이며 L2는 정적으로 연결된 1-hop 이웃 전체를 원자적으로 요청한다. 실행 의미를 보장하는 minimal closure는 아니다. 실제 저장소는 SCIP, synthetic은 보수적인 AST 관계를 사용했다. 호출 전 동결한 P0 수정 계획, 원래 실험 기획.

## 실제 저장소 파생 사례 결과

아래 각 셀은 **해당 사례에서 한 번 생성한 결과**다. 독립 PR 여러 개의 성공률이 아니다. `유보`는 `insufficient_evidence`, `지적 없음`은 `no_supported_issue`다.

| 조건 | Click unsafe의 정확한 원인 | Click safe 판정 | AnyIO unsafe의 정확한 원인 | AnyIO safe 판정 |
|---|---|---|---|---|
| D | 못 찾음 | 유보 | 못 찾음 | 유보 |
| L: AST-BM25 | 못 찾음 | 유보 | **탐지** | **지적 없음** |
| R: 기본 그래프 | 못 찾음 | 유보 | 못 찾음 | 유보 |
| S: 부족 사실 | 못 찾음 | 유보 | 못 찾음 | 유보 |
| C: 범위 확장 | 원인 일치, **핵심 근거 누락**¹ | 유보 | 못 찾음 | 유보 |
| Q: 질문 가중 그래프 | 못 찾음 | 유보 | 못 찾음 | 유보 |
| SCQ: 결합 | 못 찾음 | 유보 | **탐지** | 유보 |

¹ C의 Click 답변은 “binary 임시 파일을 text wrapper로 감싸지 않아 이후 str write가 실패한다”는 실제 원인과 일치했다. 그러나 해당 입력에는 `_tempfilepager` 본문이나 `NamedTemporaryFile`의 mode가 없었다. **결정적 producer 사실을 추정한 답변이므로 증거 회수 개선으로 인정하지 않는다.** 원인 일치 점수와 grounding을 각각 보존했다.

SCQ와 L은 둘 다 AnyIO의 `TrioBackend.run`에서 `options`를 사용하지 않는 본문을 회수하고 결함을 찾았다. L은 정상 쌍도 `지적 없음`으로 답했지만 SCQ는 정상 쌍의 일부 본문과 dispatch가 부족하다며 유보했다. 따라서 정상 쌍의 무경고만 세면 두 조건이 같아 보이지만, **두 world 모두 판정을 마친 성공은 L에만 있었다.** 구체적인 backend 본문 획득과 전체 runtime dispatch 증명을 구분한다.

주 실험의 실제 사례 safe 입력에서는 오경보가 없었다. 대부분의 답변이 유보였으므로 이를 정확한 정상 판정으로 해석하지 않는다. 전체 52개 final 중 `reported_issues` 15, `no_supported_issue` 13, `insufficient_evidence` 24였다.

Synthetic 두 제어 사례에서는 L/R/S/C/Q/SCQ가 모두 unsafe 원인을 찾고 safe에서 지적을 내지 않았다. D는 모두 유보했다. 두 제어 사례는 같은 key-preservation 문제 계열이고 너무 쉽게 풀려 새 방법의 차이를 구분하지 못한다.

판정은 arm 이름을 제거한 응답을 작성자가 읽고, 사전에 적은 원인·범위·witness와 대조했다. 독립 평가자 검증은 아니다. 판정 규칙, 익명화 응답, 항목별 판정, 지표.

## 기존 강한 검색기와 비교

| 기존 방법 | Click unsafe | Click safe | AnyIO unsafe | AnyIO safe |
|---|---|---|---|---|
| claim-BM25 | 잘못된 원인 | 지적 없음 | **정확한 원인** | 지적 없음 |
| seed dedup + 2-hop reserve | 잘못된 원인 | 지적 없음 | **정확한 원인** | 지적 없음 |
| answer-source | 지원 범위 밖, 미실행 | — | **정확한 원인** | 지적 없음 |

기존 claim/reserve는 Click producer 문맥도 가져왔지만 `typing.BinaryIO`의 `isinstance` 검사 자체가 TypeError를 낸다고 설명했다. 실제 CPython 3.14.6에서는 이 검사가 **예외 없이 False**를 반환하고, 이후 binary stream에 str을 쓰면서 실패한다. 실행 확인값은 runtime 사실 확인에 저장했으며 reviewer 입력에는 주지 않았다.

따라서 Click에는 **필요한 source 회수 실패**와 **source를 받아도 남는 연산 의미 오판**이라는 서로 다른 병목이 있다. 검색 개선만으로 후자를 해결했다고 주장할 수 없다.

기존 검색기는 자체 60-line chunk/head-source/직렬화 방식을 유지했다. 동일 모델·질문·final instruction·1,600 cap을 사용했지만, 한 가지 요인만 바꾼 비교는 아니다. 새 방법의 우월성을 판단하기 전 반드시 넘어야 할 기존 시스템 기준선이다. 추가 비교 계획, 원본 응답, 판정.

## 실패 원인 감사

**1. 좋은 gap query가 나와도 최종 선택에 연결되지 않았다.** AnyIO S 조건에서 필요한 `TrioBackend.run`의 전체 corpus lexical 최고 순위는 safe 1위, unsafe 2위까지 올라갔지만 최종 packet에는 들어가지 않았다. 이 메서드는 단독 직렬화 시 각각 143/123 tokens다. 메서드 자체가 전체 예산보다 커서 실패한 상황은 아니다.

현재 selector는 graph 후보 3개와 lexical 후보 1개를 순서대로 섞고, 질문마다 2개의 새 경로가 채워지면 멈춘다. Graph 경로가 먼저 들어오면 gap이 찾은 lexical endpoint에 기회가 오지 않을 수 있다. 누적 budget과 이미 선택된 문맥도 이후 교체를 막는다. **질문 품질과 검색 선택 정책을 따로 평가해야 한다.**

첫 S controller 출력과 그 직전 packet을 그대로 고정하고, gap마다 whole-unit lexical 획득 1회를 먼저 시도하는 사후 진단을 수행했다. AnyIO의 필수 구현 회수는 **0/2 → 2/2**로 바뀌었고, 각각 1,120/1,035 tokens로 다음 단계 상한 1,200 안에 들어왔다. Click은 여전히 0/2였다. 이 단일 단계 재생은 **검색만의 진단**이며 reviewer 개선의 증거로 사용하지 않는다. 이후 실제 새 controller·reviewer를 호출한 별도 실험은 다음 절에 기록했다. [실행 가능한 진단 코드](gap_priority_replay.py), 재생 결과.

**2. C의 확장이 필요한 producer보다 부분 문맥과 관계 metadata를 쌓았다.** 주 비교의 Click 조건은 어느 것도 `_tempfilepager`를 회수하지 못했다. 해당 whole unit은 370 tokens다. L2는 모든 1-hop 이웃을 요청하므로 의미 있는 최소 범위보다 크고, 남은 예산에 맞지 않는 요청도 생긴다. 함수별 범위·endpoint 회수는 진단표에 있다.

**3. Q의 독자적 이득을 확인하지 못했다.** 실제 사례의 R/Q에서는 각 safe/unsafe final prompt가 같았다. Click L도 두 world의 prompt가 같았다. 결함 여부를 결정하는 문맥을 전달하지 못한 것이다. 또한 graph 검색 330건 모두 50회 안에 사전 설정한 L1 변화 `1e-6`에 도달하지 않았다(잔차 약 `2.6e-6`~`5.9e-4`). 이번 Q 결과는 **50-step 근사 확산 설정의 결과**로 한정한다. 수렴한 PPR이나 QAFD 공식 알고리즘의 성능으로 해석하지 않는다.

## 사후 수정: gap 검색 결과에 먼저 슬롯 배정

`S_priority`는 모든 질문에 whole-unit lexical 선택 1회를 먼저 배정하고, 그 뒤 질문마다 새 graph 경로 최대 1개를 채운다. 이미 본 원문을 제거하지 않으며 800→1,200→1,600 cap을 유지한다. 정답의 함수 이름이나 witness는 선택기에 주지 않았다. 기존 공통 질문·최초 packet·첫 controller는 입력이 바뀌지 않아 재사용했고, **변경된 문맥을 읽는 두 번째 controller와 final reviewer는 새로 호출**했다.

| 사례 | 기존 S | 수정 S_priority | 비교 해석 |
|---|---|---|---|
| AnyIO unsafe | 유보 | **options 무시 원인 탐지** | 전체 `TrioBackend.run` 본문을 근거로 사용 |
| AnyIO safe | 유보 | **지적 없음** | 정상 forwarding 본문 회수 |
| Click safe/unsafe | 양쪽 유보 | **최종 리뷰 미실행** | 두 번째 controller 모두 240초 timeout |

AnyIO final evidence는 safe 1,596 / unsafe 1,598 tokens다. source 누적·원문·packet hash·근거 ID 검사를 통과했다. 이 사례의 판정은 개선됐지만 **기존 AST-BM25 및 강한 검색기와 같은 수준**이다. 추가 호출이 드는 방법의 우월성은 입증되지 않았다. 필요한 backend 본문은 보였어도 전체 runtime dispatch 증명까지 회수한 것은 아니다.

첫 수정 배치에서 controller가 근거 ID 끝에 한 글자를 추가했고 로컬 검증이 해당 final을 차단했다. 첫 배치 7회는 전체 제외하고, 이미 보이는 ID만 출력하도록 provider schema에 enum을 넣어 재실행했다. 재실행은 계획 8회 중 6회 시도·4회 성공·2회 timeout으로 종료됐으며 재시도하지 않았다. Click의 누락된 final을 정상 판정이나 의미적 실패로 대체하지 않고 **운영 실패·평가 불가**로 남겼다. 이 배치는 전체 완료가 아니며, 완료된 AnyIO만으로 전체 성공률을 계산하지 않는다.

이 결과는 실패를 본 뒤 정책과 schema를 수정한 **개발용 사후 실험**이다. 새 holdout이나 사전 등록한 단일 요인 비교가 아니며, 작성자가 원본 응답을 직접 판정했다. 동결 계획, 원본 응답, 판정, 부분 실행 기록, 무결성·완료 여부 감사.

## 비용

주 실험 전체의 provider 보고 input tokens는 **1,425,457**, output tokens는 **117,783**이다. Cached input 851,456과 reasoning output 29,231은 별도 필드이며 다시 더해 총량을 부풀리지 않는다. 금액으로 환산하지 않았다.

| 조건 | 호출 수 | 보고 input tokens | 보고 output tokens |
|---|---:|---:|---:|
| 공통 질문 | 4 | 36,998 | 1,619 |
| D | 4 | 38,237 | 841 |
| L | 8 | 84,781 | 2,815 |
| R | 24 | 253,403 | 22,139 |
| S | 24 | 253,335 | 22,039 |
| C | 24 | 252,643 | 23,385 |
| Q | 24 | 253,421 | 21,833 |
| SCQ | 24 | 252,639 | 23,112 |

SCQ는 L의 약 3배 호출/input으로 AnyIO 탐지는 같고 safe 판단은 더 유보했다. R/Q의 controller는 기전 비교를 위한 진단 호출이며 실제 운영 구현에서는 제거할 수 있다. SCQ의 controller는 정책 자체에 필요하므로 동일하게 제거할 수는 없다. 출력 cap이 구현돼 있지 않아 총 compute 일치를 주장하지 않는다.

사후 수정 재실행의 보고 usage는 input 44,853 / output 4,023이다. 재사용한 기존 prefix 호출 비용은 주 실험에 이미 포함돼 있으며 다시 더하지 않는다. 성공한 두 AnyIO 입력만의 전체 운영 비용으로 이 추가분을 해석하지 않는다.

제외 배치·추가 비교·수정·smoke까지 보고된 전체 usage는 input **1,838,668** / output **137,493**이다. 스키마 거부 40회와 timeout 2회의 usage는 미보고로 남겼으며 0으로 가정하지 않았다. 전체 호출 원장.

## 새 데이터 준비와 다음 실행 조건

AACR에서 Python repo-level defect comment가 있는 **4개 저장소·10개 PR**를 후보로 추렸다. Comment는 미검증 lead이며 gold가 아니다. Commit에 고정한 관련 파일 40개 요청 중 39개를 확보했다. 전체 저장소 snapshot·동일 diff pair·four-cell witness가 준비된 자료는 아니므로 P1의 12개 사례로 세지 않는다. 후보 목록, 원문·해시 receipt.

그중 Langflow #5896은 원본 `get_result → _get_result → _log_transaction_async` 세 메서드를 추출해 검사했다. flow가 없으면 base/head 모두 완료됐고, flow가 있으면 base는 완료되지만 head는 동일한 비재진입 잠금에서 중첩 대기했다. Coroutine await chain과 lock waiter 상태로 확인했으며 timeout만으로 결함을 판정하지 않았다. 네 조건을 두 번 실행했고 network·파일 쓰기·HOME 읽기를 차단했다. 전체 Vertex 생성·의존성과 persistence는 재현하지 않았으므로 **구성요소 수준의 새 후보 검증**이다. [원본 기반 probe](probe_langflow_5896.py), 실행 결과.

P1로 확대하기 전에 다음을 완료해야 한다.

1. S_priority의 AnyIO 개발 결과를 새 사례에서 검증하고, Click controller timeout의 원인을 확인한다. 사후 수정 결과를 새 데이터 성능으로 사용하지 않는다. 향후 호출은 별도 배치로 동결한다.
2. C의 전체 1-hop 확장을 질문에 필요한 guard/binding/producer 범위로 좁힌다. 이미 읽은 불필요 원문이 후속 예산을 소모하는 현상도 검사한다.
3. Q의 수렴·동점 처리·최종 source 차이를 먼저 확인한다. 현재 결과만으로 Q를 추가할 성능 근거는 없다.
4. 새 12-family의 source·명세·실행 정답과 기존 자료 중복을 검증한다. 자연 PR·파생 pair·synthetic을 구분하고 독립 판정을 확보한다.

현재 완료한 것은 **7조건의 실제 개발 실험, 기존 검색기 대조, 실패 원인 감사 및 다음 데이터 준비**다. 사후 정책 수정은 AnyIO pair의 새 리뷰까지 완료했고 Click은 timeout으로 남았다. 다음 검증 대상은 **질문이 찾은 답의 위치가 선택 단계에서 탈락하지 않도록 연결하는 정책이 새 사례에서도 이득을 주는지**다.

## 재현 파일

- 검색 정책: [context_adaptive.py](../../ace_review/context_adaptive.py)
- 배치 준비/실행: [context_adaptive_run.py](../../ace_review/context_adaptive_run.py)
- 사후 수정 정책: [context_gap_priority.py](../../ace_review/context_gap_priority.py), 동결 사본
- 해당 실행의 코드 사본: 동결 코드
- 입력과 controller/final 출력: 최종 packet, 원본 predictions, `p0_v2/inputs/`, `p0_v2/raw/`
- 감사 재실행: `tools/venv-parallel-review20260928/bin/python runs/context_adaptive_20260929/audit_run.py`

기존 run 디렉터리는 덮어쓰지 않는다. 새 모델 실행은 별도 output 디렉터리를 사용해야 한다.
