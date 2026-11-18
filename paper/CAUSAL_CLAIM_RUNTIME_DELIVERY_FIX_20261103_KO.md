# 동일 계획의 runtime 관측 전달 보충

이번 대응 실행에서는 계획만 받은 조건은 최종 경고를 내지 않았고, 실제 분석환경 관측을 받은 조건은 고정 입력의 올바른 실패 원인을 최종 경고 1건으로 냈다. **의미상 최종 설명은 0→1이었지만 유효한 typed 보고는 두 조건 모두 0이었다.** 이는 선택한 역사적 개발 사례 1개·반복 1회의 기전 관찰이다. 일관된 성능 향상이나 F1 개선, 원래 PR 배포 환경의 결함 입증으로 확대하지 않는다.

| 조건 | 실제 관측 전달 | 최종 경고 | 분석환경 고정 입력의 정확한 최종 원인 | 유효 typed 보고 | 신규 모델 토큰 | 공유 포함 논리 토큰 |
|---|---:|---:|---:|---:|---:|---:|
| 동일 계획 + 추가 검토 | 없음 | 0 | 0 | 0 | 27,949 | 90,812 |
| 동일 계획 + 관측 + 추가 검토 | 있음 | 1 | 1 | 0 | 29,755 | 92,618 |

표의 0/1은 이 한 사례의 실제 최종 출력 판독이며 F1이 아니다. 계획만 받은 응답에도 조건부로 올바른 원인 후보가 있었지만 `findings`가 비어 있어 최종 탐지로 올리지 않았다. 관측 조건은 실제 경고를 냈고, 원래 배포 환경의 불확실성을 명시했다.

부모 재생 실험은 probe가 로컬 dict 기록 대입을 거부해 관측이 발급되지 않았고, 최종 세 조건 모두 경고가 없었다. 그 결과와 봉인을 그대로 보존했다. 이번 보충은 부모의 실제 공통 검토 2회, 정적 조회 R1–R3, planner 1회, 공개 계약·diff·source pool을 SHA로 공유하고 두 final만 새로 호출했다. 부모 final의 답은 새 reviewer 입력에 넣지 않았다. 기존 static 결과는 참고 자료이며 이번 두 조건에 다시 섞지 않았다.

**원인을 처음 식별한 단계와 관측의 역할**

역사적 원문은 `wraps(f)`가 이름을 읽다가 `AttributeError`를 낸다고 설명했다. 실제 실행 경로는 다음과 같았다.

| 실제 단계 | 공급된 근거와 모델의 다음 행동 |
|---|---|
| R1 → 공통 검토 1 | `wraps` 65–77행을 보고 직접 이름을 읽는다는 설명을 반박했다. 다음 위임 대상 `update_wrapper`의 본문을 질문했다. |
| R2 → 공통 검토 2 | 실제 `update_wrapper` 35–63행이 전달됐다. 누락 속성의 `AttributeError`를 잡는 51–54행을 근거로 두 번째 API 예외 가설을 반박하고, 공개 계약의 반환 wrapper가 캡처한 `f.__name__`을 읽는 다음 소비를 올바른 실패 원인으로 설명했다. |
| R3 | scoped runtime 관측을 요청했지만 정적 backend는 `runtime_probe_disabled_static_condition` 미해결로 기록했다. 반환된 evidence는 없었고 repository callback 본문을 새로 확보한 단계도 아니다. |
| 이번 보충 | 같은 계획 코드를 실행한 분석 fixture의 관측을 한 조건에만 전달한 뒤, 각 조건에서 final을 한 번씩 새로 생성했다. |

따라서 올바른 downstream 원인의 첫 식별은 runtime 이전의 공통 검토 2에서 이미 일어났다. 이번 차이는 이미 식별된 원인을 최종 경고로 내보내는 판단에서 나타났다. 동일한 계획·본문을 공유하는 대조군을 두었지만 반복 1회로 관측의 순수 효과나 일관성을 확정할 수는 없다.

**실제 관측과 구현 바인딩**

부모 planner가 만든 코드의 SHA는 `eacb4ba44c335f703030d7d52eeb9bdeeff9c5ec7b06fba3b566e7d280f8fc6c`이며 한 글자도 바꾸지 않았다. 실행기는 literal로 만든 로컬 dict의 기록과 기존 허용 metadata의 literal getter를 지원하도록 보충했다. 동적 getter, getter alias, 금지 속성, module export 대입은 계속 거부한다. trusted child에는 fixture exception event 최대 32건만 기록하는 관측을 추가했다.

고정 CPython 3.11.13 분석 subprocess는 0.02272초에 완료됐다. 결과는 `bound_method_has_name=false`, 이름 값 `null`, 호출 결과 `AttributeError`였다. 실제 객체 identity는 `functools.partial is _functools.partial`이고 실행 전후 export가 바뀌지 않았다. 예외는 generated fixture의 `wrapper:11 → method_wrapper:20 → <module>:30`으로 전파됐다. locals와 외부 파일 trace는 수집하지 않았다.

이는 공개 계약을 구체화한 **generated analysis fixture** 실행이다. 원래 Django 모듈 전체나 원래 PR 배포 환경을 실행한 결과가 아니다. `analysis_probe_origin`의 repository snapshot/path는 null이다. 기존 Python fallback class 본문도 실제 native C `partial` 구현의 본문 증거로 바뀌지 않는다. 직접 관측한 분석환경 API 속성과 원래 배포 구현의 불확실성을 분리했다.

두 final의 실제 stdin을 확인했다. 동일한 repository 본문 147행·878 reference tokens와 동일한 계획이 전달됐고, 관측 ID `R-f920fe7ab58d1296`은 관측 조건에만 존재했다. 실제 source SHA·mask와 전체 본문 frame 일치를 확인했다. 새로운 repository 본문 행은 0이고 신규 native 호출도 0이다. 실제 관측의 전달을 새 repository 본문 확보로 세지 않았다.

**원인 내용과 보고·상태의 남은 문제**

관측 final은 메타데이터 복사 제거 → 이름 없는 partial 전달 → 반환 wrapper의 `f.__name__` 읽기 → `AttributeError`라는 고정 분석 입력의 원인을 정확히 설명했다. `wraps` 또는 `update_wrapper`가 누락 이름 때문에 직접 실패한다는 옛 설명을 반복하지 않았다. 다만 다음 문제는 그대로 남았다.

- 원문이 선택한 실패 연산은 callback 내부의 `f.__name__` 읽기다. 해당 callback의 물리적 repository 본문은 제공되지 않아 응답이 그 슬롯을 정직하게 unresolved로 뒀다. 이 위치 손실에는 자료 구성의 제약도 포함된다. 평가 규칙이 실제 실패 호출 자체를 표시하는 대안까지 금지한 것은 아니므로, 이 사례 전체가 typed 보고 불가능하다고 단정하지 않는다. 원문 위치를 다른 줄로 옮기거나 재채점하지 않았다.
- 관측 ID를 source-location-only `supporting_evidence`에 넣어 `location_is_not_operation_source` 오류 1건이 발생했다. 실제 관측 내용은 맞지만 이 슬롯의 역할은 잘못됐다. runtime 전용 finding 근거 채널이 필요한 지점이다. 두 손실은 같은 경고에 겹치며 경고 2건으로 부풀리지 않는다.
- 실제 core update는 수락됐고 새 P6은 `runtime_observation` 근거로 supported가 됐다. 그러나 옛 오원인 P1/P2가 여전히 refuted이면서 모든 조건을 요구하는 route에 남아, 후보 verdict는 `refuted_in_scope`였다. 가설을 수정할 때 과거 반박 기록과 현재 원인의 필요조건 연결을 분리해야 한다. 이번 실행에서는 controller나 route를 고치지 않았다.

이 때문에 **정확한 최종 설명 1건과 유효 보고 성공은 구분된다.** 관측이 직접 해결한 분석환경 속성, 미해결인 원래 배포 바인딩, 제공되지 않은 callback 소스, 잘못된 근거 슬롯, 오래 남은 필수조건을 하나의 성공/실패로 합치지 않았다.

**비용과 보존**

신규 물리 모델 호출은 2회·57,704 tokens다(input 52,209 + output 5,495). cached input 8,960과 reasoning output 929는 각 input/output의 부분 집합으로 중복 합산하지 않았다. 공유한 과거 common2+planner1은 물리 3회·62,863 tokens이며 재실행하지 않았다. 두 조건의 논리 비용은 각각 90,812·92,618로 동일 cap 120,000 아래이고 논리 호출도 각각 4회다. 관련 공유 이력과 이번 신규 호출을 물리적으로 한 번씩 세면 5회·120,567 tokens이며, 이는 조건당 cap과 다른 합계다.

신규 native는 0회, probe는 1회다. 이전 정적 조회 비용은 봉인된 부모 receipt에 별도로 남아 있다. 이번 물리 wall time은 60.25초, 공유 이력을 포함한 active time은 계획 조건 115.36초·관측 조건 109.33초다. 다른 조건을 기다린 시간을 논리 active 비용에 넣지 않았다. 이 두 호출의 시간 차이를 속도 향상 증거로 해석하지 않는다. 모델 usage 미상·전송 실패·재호출은 없었고 기존 결과나 원시 응답을 수정하지 않았다.

직접 판독 JSON, 물리·논리 사용량, 실제 probe receipt, 계획만 받은 actual final, 관측을 받은 actual final에 원문·SHA·진단을 보존했다. 이 판독은 AI의 실제 원문 검토이며 인간·맹검 평가라고 부르지 않는다. 추가 모델·native 실험은 수행하지 않았다.

19개 테스트와 root의 독립 19개 테스트가 통과했고, 모델 전 내부 36개·외부 13,416개 파일을 동결했다. CPU 2초·parent wall 4초 제한을 적용했으며, macOS가 거부한 메모리 256MB 제한은 미적용으로 receipt에 기록했다. 테스트, 독립 감사, 동결 명세를 보존했다.
