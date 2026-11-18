# GoFR #1681: 실제 router와 catch-all 등록 검증

2026-09-09. 원본 Router.NewRouter/Add/AddStaticFiles와 실제 Logging/CORS/backend를
사용해 라우팅 조건을 확장했다. Claim 검토 수는 13/75 유지. Reviewer 호출 0회.

## 앞선 해석의 보완

`pkg/gofr/rest.go`는 GET 등의 method를 Router.Add로 전달한다. 하지만 실제 App의
`pkg/gofr/gofr.go:91–109` httpServerSetup은 staticFiles 등록 후 method 제한 없는
`PathPrefix("/")` catch-all도 추가한다. 따라서 단순 GET-only mux fixture의 405를
완전한 GoFr App의 동작으로 일반화하면 안 된다.

확인한 head는 `c1a26ade6db7da96622c9bdca7674ef7094c122f`이다. 기존 `router.go`의
AddStaticFiles도 method를 제한하지 않는 PathPrefix route를 생성한다.
공개 App.AddStaticFiles는 경로를 절대 경로로 정규화한 후 설정에 보존한다.

## 최종 24-cell 실행

2 source revision × 2 middleware 순서 × 2 method × 3 route 조건.
표의 결과는 base/head 소스에서 동일했다. 정상 순서는 base=Logging 앞, head=뒤다.

| Route / 요청 | Logging이 CORS 앞 | Logging이 CORS 뒤 |
|---|---|---|
| 실제 AddStaticFiles / OPTIONS | 200, request log 1 | 200, request log 0 |
| 실제 AddStaticFiles / GET | 200, request log 1 | 200, request log 1 |
| 실제 Router.Add GET, fallback 생략 / OPTIONS | 405, log 0 | 405, log 0 |
| 실제 Router.Add GET, fallback 생략 / GET | 200, log 1 | 200, log 1 |
| 실제 Router.Add GET + App 방식 catch-all / OPTIONS | 200, log 1 | 200, log 0 |
| 실제 Router.Add GET + App 방식 catch-all / GET | 200, log 1 | 200, log 1 |

단순히 하네스에서 OPTIONS route를 발명한 것이 아니라 실제 repository의 static
registration과 GET+catch-all 구조에서도 회귀를 관측했다. Head 순서 복원은 로그를 회복한다.

## 실험 범위

Router와 middleware/logger 소스, go.mod/go.sum은 각 revision에서 그대로 가져왔다.
App.httpServerSetup 자체를 실행한 것은 아니며 해당 catch-all 등록 구문을 수동 전사했다.
Fallback handler는 404 stub이다. OPTIONS는 CORS에서 먼저 종료되므로 그 body는 실행되지 않는다.
Tracer/Metrics 및 전체 App 부팅은 여전히 제외했다. 따라서 full-server E2E라고 부르지 않는다.

첫 actual-router 실행은 상대 static 경로를 직접 전달해 GET에서 403이 발생했다.
실제 App.AddStaticFiles의 절대 경로 정규화를 반영해 새 디렉터리에서 재실행했다.
실패 파일은 보존했다. 이를 target regression으로 세지 않는다.

## context 실험에 주는 구체적 근거

현재 후보 evidence chain은 다음과 같다.

1. Diff: Logging 등록 위치가 CORS 뒤로 이동.
2. Diff 밖 CORS: OPTIONS를 처리한 뒤 next 호출 없이 종료.
3. Diff 밖 route 구성: static 또는 catch-all이 OPTIONS를 매칭시켜 chain 진입을 보장.

특히 3번은 직전 405 대조군의 해석을 바꾼 추가 근거다. Source context가 실제 판단을
수정한 작성자 관측이지만, 모델의 review recall 상승이나 retrieval 알고리즘 기여를
입증한 통계는 아니다. 독립 annotator가 필수 span과 충분성·최소성을 확인해야 한다.

다음 reviewer pilot은 full diff / +CORS / +CORS+route / token-matched placebo로
개발용 비교가 가능하다. 정답 claim·재현 결과는 reviewer 입력에서 제외한다.
공통 후보풀 선택 결과와 대조하기 전에 evidence 조건을 동결해야 한다.

## 산출물

- 최종 receipt: `gofr1681_actual_router_20260909_v2/receipt.json`
- 최초 실패: `gofr1681_actual_router_20260909/receipt.json`
- 각 revision 디렉터리: 원본 router/middleware/logger, main.go, 실제 case별 JSONL
- 구현: `ace_review/cors_logging_witness.py --actual-router`
- 최종 base/head 모두 종료 0. Ruff 통과.

재현: `PYTHONPATH=. python -m ace_review.cors_logging_witness data/repos/gofr-dev__gofr runs/new_actual_router --actual-router`
