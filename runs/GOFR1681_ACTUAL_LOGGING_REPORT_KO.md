# GoFR #1681: 실제 Logging/backend 교차 재현

2026-09-09 후속 보완: 실제 App은 method 비제한 catch-all을 추가한다. 따라서 아래
GET-only/405 결과는 fallback 없는 fixture의 결과다. 실제 Router+catch-all과 static
route 재현은 `GOFR1681_ROUTING_REPORT_KO.md`를 참조한다.

2026-09-08. 기존 middleware 진입 counter를 실제 request-log JSON 출력으로 교체했다.
추가 claim annotation은 없으며 누적 초안 검토는 13/75 그대로다.

## 사용한 실제 소스

각 base/head git blob에서 아래 파일을 수정 없이 복사했다.

- `pkg/gofr/http/middleware/cors.go`, `logger.go`
- `pkg/gofr/logging/logger.go`, `level.go`
- `pkg/gofr/version/version.go`
- 해당 revision의 원본 `go.mod`, `go.sum`

실제 gorilla/mux, Logging, NewFileLogger를 사용했다. 출력 JSON에서 request의
Method와 Response=200을 검사했다. 각 source와 harness의 SHA256, 실제 JSON 로그,
stdout/stderr, Go 버전을 receipt에 보존했다. 의존성 다운로드는 비활성화했다.

서버 초기화·전체 router wrapper는 실행하지 않았다. 등록 순서는 수동 전사했고
Tracer/Metrics는 제외했다. 실제 application의 route 등록을 자동 재현한 것은 아니다.
따라서 정확한 명칭은 **실제 로깅 구성요소 통합 재현**이며 full-server E2E가 아니다.

## 원인 분리: source revision × order × method × route

Base와 head 소스 각각에서 Logging을 CORS 앞/뒤에 배치했다. 원래 배치는
base=앞, head=뒤다. Head=앞은 수정 순서 대조군, base=뒤는 회귀 순서 대조군이다.

| Route 조건 / 요청 | Logging 앞 | Logging 뒤 |
|---|---|---|
| GET+OPTIONS 매칭 / OPTIONS | 200, 실제 request log 1건 | 200, 실제 request log 0건 |
| GET+OPTIONS 매칭 / GET | 200, log 1건 | 200, log 1건 |
| GET 전용 / OPTIONS | 405, log 0건 | 405, log 0건 |
| GET 전용 / GET | 200, log 1건 | 200, log 1건 |

이 표는 base/head **양쪽 소스에서 동일**했다. 최종 route-control 실행은
2 revision × 2 order × 2 method × 2 route = 16 cell이며 모두 assertion 통과.
이전에 route 조건을 추가하지 않은 8-cell 실행도 별도로 보존했다.
이 수치는 반복 seed 수나 독립 PR 수가 아니다.

## 판정

매칭되는 OPTIONS에 대해 등록 순서만 바꾸어 로그 유무를 바꿀 수 있었다.
Head 소스도 순서를 복원하면 로그가 돌아왔다. 해당 구성요소 하네스에서
회귀 원인을 Logging 내부 변경이 아닌 순서로 좁혔다.

하지만 GET 전용 route에 들어오는 OPTIONS는 원래부터 middleware chain에 도달하지
않아 로그가 없었다. 따라서 "모든 OPTIONS/preflight 로그가 PR 때문에 사라진다"는
주장은 과장이다. 실제 application에서 OPTIONS 또는 method 비제한 route가
어떻게 등록되는지 확인해야 발생 범위를 확정할 수 있다.

## 다음 reviewer 실험 전에 필요한 조건

1. 실제 app route 등록 경로와 OPTIONS 매칭 가능성을 확인해 발생 조건을 동결한다.
2. 독립 검토에서 confirmed/conditional/unresolved와 필수 source span을 확정한다.
3. 개발용 reviewer pilot은 full diff / diff+CORS early-return / placebo로 구성한다.
   Oracle source는 정답 주장·trace의 결론을 포함하지 않는다.
4. 정상 대조군에는 OPTIONS 비매칭 조건과 순서 복원 조건을 포함해, 모든 OPTIONS를
   기계적으로 결함이라고 보고하는 reviewer를 구분한다. 동일 diff 여부를 별도로 검증한다.

현재 reviewer 호출은 0회다. Oracle recall 또는 BM25/SCIP 우위는 아직 측정하지 않았다.
원본 gold/pending annotation은 변경하지 않았다.

## 산출물

- 구현: `ace_review/cors_logging_witness.py`
- 최초 실제 logger 8-cell: `gofr1681_actual_logging/receipt.json`
- 최종 route-control 16-cell: `gofr1681_actual_logging_route_control/receipt.json`
- 각 폴더의 base/head 디렉터리에 실제 소스와 case별 JSONL 로그 보존
- Go 1.25.0 darwin/arm64, 양 revision 실행 종료 0; Ruff 통과
- Python 기존 suite 344 passed. 이 수치는 Go 16-cell 실행과 별개다.

재현: `PYTHONPATH=. python -m ace_review.cors_logging_witness data/repos/gofr-dev__gofr runs/new_actual_logging`

출력은 반드시 새 디렉터리여야 한다. GOPROXY/GOSUMDB off이므로 필요한 의존성이
로컬 cache에 없으면 실패한다. OS network sandbox를 제공하는 것은 아니다.
