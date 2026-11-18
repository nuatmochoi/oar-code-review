# 역사적 오원인 재생 — 정적 반박은 전달됐지만 최종 교정은 남지 않음

**한 역사적 Django 사례에서 ‘잘못된 API 설명 → 본문 조회 → 반박 → 다음 소비자 설명’은 실제로 진행됐다. 그러나 세 최종 응답은 모두 경고를 보류했고, 최종 원인 교정 성공은 없었다.** 관측 조건도 probe가 실행 전에 거부되어 실제 관측을 받지 못했다. 이 결과로 runtime 관측의 효과를 판단할 수 없다.

이 문서는 `causal_claim_runtime_replay_20261103` 부모 실행만 다룬다. 과거 오류를 선택한 개발용 사례 1개·반복 1회이며 F1이나 일반화 성능을 계산하지 않는다. V1·V2 및 별도 delivery-fix 실행과 합치지 않는다. 판독은 실제 응답과 소스를 읽은 AI 판독이며 인간 판독·별도 judge 모델 호출은 0이다.

직접 판독 JSON, 실제 비용·실패 장부, 사전 명세, 판독 기준를 함께 보존했다.

**무엇을 재생했는가**

과거 실제 응답은 “`wraps(f)`가 `partial`의 없는 `__name__`을 읽어 `AttributeError`를 낸다”라고 단정했다. 원문 confidence는 99였다. 실제 잘못 지목한 연산은 `wraps` 자체의 이름 읽기다. 원래 경고의 mechanism·trigger·impact·수정 제안은 바꾸지 않고 원시 응답·입력·trace를 복사해 SHA를 확인했다. 이를 이번 모델이 새로 만든 initial 오류라고 부르지 않는다.

현재 schema에 맞추는 과정에서 condition은 아직 검증되지 않은 상태로 시작한다. 모델에 노출되던 adapter metadata의 `wrong` 표현은 실행 전에 중립 표현으로 바꿨고, 최초 adapter와 수정 이력도 보존했다. 정답인 대체 원인을 adapter에 넣지는 않았다. 변환 기록, 독립 설계 감사

공개 고정 입력에는 `deco(f)`가 `wraps(f)`를 적용한 wrapper에서 `(f.__name__, f(*args, **kwargs))`를 반환한다는 설명이 이미 있다. 이 callback은 **공개 prose이며 저장소 함수 본문은 아니다.** 따라서 후속 응답이 `f.__name__`을 지목한 것을 새로운 repository consumer 본문 확보로 세지 않는다.

**실제로 진행된 반박·후속 조회**

| 단계 | 실제 입력·응답 | 판독 |
|---|---|---|
| 역사적 seed | `wraps(f)` 자체가 이름을 읽으며 실패한다는 경고 1개 | 제공된 분석-profile 구현에서는 틀린 API 메커니즘 |
| 조회 R1 → 검토 1 | `wraps` 65–77행과 Python `partial` fallback 본문 공급. 검토 1은 경고를 보류하고 `update_wrapper` 본문을 질문 | 위임 관계를 읽고 다음 API consumer를 실제로 요청함 |
| 조회 R2 → 검토 2 | 실제 `update_wrapper` 35–63행 공급. 검토 2는 `except AttributeError: pass`를 반박으로 사용 | 즉시 예외 가설 두 가지를 반박하고, 공개 callback의 이후 `f.__name__` 접근을 설명한 중간 finding 1개 생성 |
| 조회 R3 | 검토 2가 제안한 runtime 질문. 정적 backend는 관측 미지원으로 기록 | 추가 repository consumer 본문과 runtime 결과 모두 없음 |
| 공통 planner | 고정 입력을 구체화한 probe 코드 제안 | 실행 전 validator에서 거부 |
| 최종 3조건 | 모두 `uncertain`, `findings: []` | 새 정확한 최종 결함 설명으로 교정한 성공 없음 |

검토 1은 `wraps`가 `partial(update_wrapper, ...)`를 반환함을 읽었다. 검토 2는 `update_wrapper`가 누락된 속성의 `AttributeError`를 잡아 건너뛰는 분기를 읽었다. 따라서 **공급된 분석-profile 소스 안에서는 실제 반박을 확보했고, 모델이 그 반박을 사용했다.** 단순한 ledger 상태 변경만을 근거로 내린 판단이 아니다. 검토 1 원문, 검토 2 원문

검토 2의 다음 소비자 설명은 고정 입력에서 캡처된 callable에 이름이 없다는 전제하에 올바른 실패 경로다. 하지만 실제 imported `partial` 구현을 증명한 것은 아니다. 받은 276–339행은 Python fallback이며 같은 파일 341–344행에서 `_functools.partial`로 덮어쓴다. 그 export/rebinding 구간이나 실제 객체 identity는 모델에 증거로 전달되지 않았다. “파일 본문이 진짜다”, “일반적인 API 명제가 맞다”, “이 본문이 실제 imported 구현의 증거다”를 구분해야 한다.

실제 전달된 source frame은 제출 stdin과 frozen 파일의 SHA·mask·행을 대조했다. 역사적 초기 입력의 중복 제거된 물리 본문 41행·207 reference tokens에서, 검토 1에는 118행·781 tokens, 검토 2와 각 final에는 147행·878 tokens가 전달됐다. 초기 이후 늘어난 106행에는 공백·마스킹된 행도 포함된다. 이는 필요한 모든 의미 근거의 확보율이 아니다. R3 이후 새로운 본문 행은 0이다. 새 ID 개수를 새 본문 확보로 세지 않았다. 단계별 실제 전달·판독

**최종 결과와 core 상태는 따로 보아야 한다**

| 최종 조건 | 실제 추가 입력 | 최종 finding | 최종 원인 교정 | core verdict |
|---|---|---:|---|---|
| static | 공통 정적 조회·검토 결과 | 0 | 없음, 구현 binding 미확정으로 보류 | `refuted_in_scope` |
| plan_no_observation | 같은 정적 결과 + 공통 probe plan | 0 | 없음, 같은 이유로 보류 | `refuted_in_scope` |
| observation | 같은 plan + 관측 실패 상태. 실제 관측 데이터 없음 | 0 | 없음, 조건부 가설만 유지 | `unresolved` |

세 final의 transport·실행 status는 정상이고 실제 final도 모두 생성됐다. 따라서 이번 보류는 final 미발급이나 호출 실패가 아니다. 수정된 가설을 candidates나 reasoning에서 유지해도 실제 최종 findings에 내지 않았으므로, 중간 finding을 최종 교정 성공으로 올리지 않는다. static, plan-only, 관측 미전달 조건

`refuted_in_scope`도 새 원인 설명이 거짓이라는 판독 결과가 아니다. 수정된 claim에 과거 즉시 예외 가설 P1/P2가 `required=True`인 채 같은 단일 AND route에 남아 있다. 이 조건들이 반박되면 이후 소비자의 가설까지 core에서 반박 상태가 된다. 과거 가설의 감사 기록과 현재 후보의 필요조건을 구분하는 구조가 필요하다. 이번 원문·core·판독은 수정하지 않았다.

검토 2는 실패 연산의 물리 위치를 unresolved로 남겼다. callback의 실제 소스가 없기 때문이다. generated fixture 코드에 줄번호가 생기더라도 그것을 PR HEAD의 failure operation으로 바꿀 수 없다. 세 final에는 finding 자체가 없으므로 “위치 결속이 유효한 새 보고를 냈다”는 성공도 없다.

**관측 비교가 성립하지 않은 이유**

planner는 실제 미해결 질문을 바탕으로 probe 1개를 제안했다. `partial`과 `wraps`를 import하고, 공개 callback과 변경된 callable 구성을 분석용으로 구체화했다. 이것은 원래 저장소 caller나 실제 배포 코드가 아니다. 실제 첫 거부 사유는 로컬 dict의 subscript 대입을 막는 `no attribute/subscript mutation in probe`였다. 코드의 `getattr`도 부모 validator가 금지하는 이름이지만, 실제 먼저 발생한 거부 사유와 구분한다. plan·거부 원형

**실제 probe subprocess 0회, 성공 관측 0개, 모델에 전달한 runtime record 0개**다. 이는 API 실행에서 예외가 났거나 나지 않았다는 관측이 아니다. observation 조건의 이름만으로 runtime 처치가 실현됐다고 볼 수 없다. plan-only와 비교해 runtime 효과가 없었다고 결론 내리는 것도 불가능하다. 관측 개입 자체가 없었기 때문이다.

**실제 비용과 실패**

| 논리 조건 | 공통 호출 포함 모델 횟수 | 실제 input+output tokens | active wall(초) |
|---|---:|---:|---:|
| static | 3 | 71,124 | 97.37 |
| plan_no_observation | 4 | 90,702 | 109.24 |
| observation | 4 | 90,754 | 117.93 |

공통 검토 2회와 planner 1회는 물리적으로 공유했다. 실제 모델 호출은 **6회**, input 132,427 + output 13,586 = **146,013 tokens**, retry·usage 미상·model admission 거절은 모두 0이다. 논리 합 252,580에서 공유 중복 106,567 tokens를 빼면 물리 장부와 일치한다. cached input 44,800과 reasoning output 4,149는 각각 input·output의 부분집합이라 다시 더하지 않는다. 모델 process 시간 합은 166.21초, 전체 실행 경과시간은 172.38초다.

새 정적 query 시도는 4회, 실제 본문 읽기는 3회, 명시적 파일 읽기는 11회, AST parse는 2회다. Pyright의 implementation capability가 미지원이어서 본문은 기존 AST/source fallback으로 얻었다. wire RPC 5회는 초기화·capability·종료를 포함하며 실제 symbol lookup RPC는 0회다. backend native 시간은 약0.89초다. backend 누적 wall 70.05초에는 공통 모델 응답을 기다린 시간이 포함되므로 순수 native 처리 시간처럼 더하지 않는다.

최종 status 실패 0, final 미생성 0, probe 사전 검증 거부 1이다. 각 논리 조건의 120,000-token 상한 초과는 없었다. reference source cap과 provider token은 다른 측정이며, 서버 hard output cap을 검증한 것은 아니다. 원형 복사·자료 준비·사전 테스트·사후 판독·AI 작업 전체 비용까지 계측했다고 주장하지 않는다.

**이 사례에서 확인한 것과 남은 개선**

실제 반박 본문을 다음 검토에 전달하고, 반박 뒤 위임 API 본문을 질문하여 이후 소비자 설명으로 이동하는 경로는 확인했다. 완전한 최종 원인 교정이나 runtime 관측 효과, 일반 코드리뷰 성능 향상은 확인하지 못했다.

다음 개선은 imported symbol의 최종 export/rebinding까지 닫는 source 조회, 허용되는 probe 문법과 planner 지침의 일치, 분석-profile 관측과 배포 binding의 분리, 과거 반박 가설과 현재 필요조건 route의 분리다. 별도 delivery-fix는 새 실행으로 평가하며 이 부모 결과를 고치거나 합산하지 않는다. 어느 개선도 한 사례 결과만으로 논문 성능 기여라고 채택하지 않는다.

이번 감사는 동결된 66개 내부 파일과 13,228개 외부 source/dependency 파일의 SHA를 다시 확인했다. 이전 V1/V2·과거 seed 원문·부모 runtime/prompt/schema/source/results는 변경하지 않았다. 동결과 비용 검증
