# 타깃 경로 보충 결과: xfail은 개선, 기본 정책은 B 유지

부족한 연결을 사람이 지정한 T는 새로 호출한 B 대비 양방향 성공이 6/18에서 10/18로 증가했다. 증가한 4회는 dynamic xfail 3회와 printer 1회다. 같은 6사례의 3반복이므로 독립 PR 18개나 통계적 우월성으로 해석하지 않는다. 이전 결과를 보고 경로를 지정한 개발 결과이며, 새 PR에 대한 일반화를 확인한 결과는 아니다.

| 지표 | B | T |
|---|---:|---:|
| 근거 있는 회귀 검출 | 9/18 | 10/18 |
| 정상 방향 오탐 | 0/18 | 0/18 |
| 양방향 모두 성공한 반복 | 6/18 | 10/18 |
| 정답 방향 판정 | 16/36 | 23/36 |
| 기권 | 20/36 | 13/36 |
| operational valid | 36/36 | 36/36 |

아래 각 셀은 회귀 검출 · 정상 방향 성공 · 양방향 성공이며 각각 최대 3이다.

| 사례 | B | T |
|---|---|---|
| N01 인증 URL helper | 0 · 0 · 0 | 0 · 0 · 0 |
| N02 클래스 MRO mark | 3 · 3 · 3 | 3 · 3 · 3 |
| N03 dynamic xfail | 0 · 0 · 0 | 3 · 3 · 3 |
| N04 nested evalf | 0 · 1 · 0 | 0 · 2 · 0 |
| N05 printer settings | 3 · 0 · 0 | 1 · 2 · 1 |
| N06 partition alias | 3 · 3 · 3 | 3 · 3 · 3 |

N03: B는 보고서 소비 코드가 없어 양방향 모두 3회 기권했다. T는 실제 xfailed_key references로 pytest_runtest_makereport를 찾아, post-yield 상태 갱신과 rep.outcome/wasxfail 전환을 함께 보여주었다. 양방향 성공 0→3/3. 상태가 바뀌는 위치와 그 상태를 최종 결과로 소비하는 위치를 같이 공급하는 정책이 가장 우선이다. 전체 pluggy 실행을 독립적으로 인증한 결과는 아니며 고정 public pytest 계약에 대한 판정이다.

N04: Float 생성자, EvalfMixin.evalf와 evalf dispatcher를 넣어 정상 방향 성공 1→2/3이지만 회귀 검출은 양쪽 0/3이다. 미평가 g(2)**2를 Float에 넘긴 뒤 외부 mpmath 변환이 거부되는지, 이후 fallback이 무엇을 반환하는지 reviewer source만으로 확정하지 못한다. 내부 callee만 더 넣는 것으로는 해결되지 않았다. 다음 보완은 실제 심볼 표현의 숫자 변환 실패 경로와 fallback 반환까지 닫는 것이다.

N05: sstr/설정 constructor/dispatch/Rational 및 Eq/Limit source를 공급해 정상 방향 성공 0→2/3, 양방향 성공 0→1/3. 회귀 검출은 3→1/3으로 줄었다. T의 나머지 회귀 응답은 raw interpolation에서 호출되는 객체의 기본 문자열 변환을 확인하지 못해 기권했다. 다음 보완은 설정된 printer 경로와 ordinary __str__의 기본 설정 경로를 함께 공급하는 것이다. 개선이 모든 방향에서 일관되지는 않는다.

N01/N02/N06: T 입력을 변경하지 않은 대조군이다. 동일 반복의 완전히 같은 B/T prompt는 하나의 새 호출을 공유하며 두 arm의 차이는 0이다. N01은 양방향 기권, N02/N06은 양방향 모두 3/3 성공. 대조군 결과는 독립적인 T 호출 반복으로 세지 않는다. 타깃 3사례만 보면 양방향 성공은 B 0/9, T 4/9이다. 전체 paired gain은 N03 r1/r2/r3 및 N05 r2이며 paired loss는 0이다. N05 회귀 검출 자체의 감소와 양방향 pair loss는 다른 지표다.

72 logical reviews, 54 새 물리 호출(B36/T18), 실패/누락/재시도/모델 도구 호출 0. source≤1600, observation≤400, 공통 최대12 definition+추가 최대3 query, 입력/공식 source/hash/보존 head root/preparation seal 검증 모두 통과. 72 응답은 작성자가 비블라인드로 검토했다. 기존 witness를 재사용했고 새 runtime witness 실행은 하지 않았다. 이전 모델 응답은 재사용하지 않았다. B 숫자는 직전 보고서의 B가 아니라 이번 새 호출 결과다. provider seed는 통제하지 않았다.

LSP 공통 query135개는 기존 trace를 재사용했다. T 준비 native query14개, 제외된 v1 준비14개, preflight7개까지 이번 전체 준비 비용은35개다. v1은 부족한 source가 있음을 모델 호출 전에 확인하여 입력을 보존하고 호출하지 않았다. 반환 클래스의 constructor/method 및 statement 선택은 AST와 사람이 정한 scope-specific 규칙이다. LSP 실패를 AST target 추론으로 대체하지 않았다. 이 실험은 AST 대비 LSP 자체의 효과나 자동 gap 탐지 능력을 분리해 입증하지 않는다. unrelated source와 diff에 중복된 base 전체 함수를 교체했으므로 모든 B prefix 보존 비교도 아니다.

채점은 직전과 같다. N04 정상 방향은 공개 고정 lambda binding과 직접적인 recursive argument evaluation을, N05 회귀는 visible raw interpolation에 의한 active setting bypass를 좁은 contract 위반으로 인정했다. 그 점수는 전체 외부 변환/ordinary __str__ 경로와 exact output을 독립적으로 인증했다는 의미가 아니다. 모델의 insufficient_evidence는 정답/검출로 세지 않으며, hidden witness로 reviewer source의 빈틈을 메우지 않았다. 이 기준은 양쪽 arm에 동일하게 적용했다. 세부 근거는 adjudication_notes.json에 기록했다.

결정: 기본 B를 유지한다. 상태 producer→최종 consumer 보충은 첫 번째 자동화 후보로 삼고 새로운 PR에서 확인해야 한다. 숫자 변환은 외부 변환 실패→fallback 반환, printer는 active 설정 경로와 기본 문자열 변환 경로를 먼저 완결해야 한다. 이번 개선만으로 범용 정책이나 기본 arm을 교체하지 않는다.

파일: freeze.json/inputs/packets/lsp_traces는 호출 전 고정, raw는 새 호출 원문, audit_all.json은 schema/ledger/인용 감사, adjudication_notes.json/annotations_all.json은 판정, results_all.json/paired_comparison.json은 지표, validation_results.json은 최종 검증이다. issue 파일·행 존재를 확인했으며 localization 정확도를 측정한 것은 아니다.
