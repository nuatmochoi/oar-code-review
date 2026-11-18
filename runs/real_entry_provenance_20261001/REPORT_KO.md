# 실제 생성·요청 경로 확인 결과

실제 저장소의 진입 함수와 객체 생성 코드를 연결해 12개 실행 관측을 확인했다. **F04는 설정과 입력을 명시하면 실제 호출 경로로 결함을 재현할 수 있었다. F08의 실제 `/view` 경로에서는 변경 분기에 도달하지 않았다.** 새 모델 호출은 하지 않았다. 이는 컨텍스트 정책의 정확도 비교가 아니라, 다음 리뷰 실험에 사용할 실행 조건의 검증이다.

## RAGFlow: 조건부 객체 타입과 실제 요청

`api/settings.py:134–141`의 `init_settings`는 `DOC_ENGINE`에 따라 전역 `docStoreConn`을 생성한다. 설정이 없으면 Elasticsearch, `infinity`면 InfinityConnection이다. `api/apps/kb_app.py:116–123`의 실제 update 함수는 기존 pagerank와 요청 값이 다르고 요청 값이 0 이하일 때 `{"remove": PAGERANK_FLD}`를 전달한다. `rag/settings.py:59`의 상수는 `pagerank_fea`다. route decorator에서 확인한 경로는 `/update`이며, 전체 URL prefix는 이 검증에 포함하지 않았다.

기존 pagerank=1, 요청 pagerank=0, parser_id=naive인 인증·소유권 통과 fixture에서 다음을 관측했다.

| 선언한 설정·입력 | base | head | 해석 |
|---|---|---|---|
| Infinity, pagerank 0 | DB 제출값 `{"pagerank_fea": 1.0}` | `{"remove":"pagerank_fea","pagerank_fea":1.0}` | head에 스키마에 없는 `remove` 키가 남음 |
| Infinity, pagerank 2 | `{"pagerank_fea":2}` | 동일 | 일반 값 업데이트에는 이 결함 없음 |
| Infinity, pagerank 1 | backend update 미호출 | 동일 | 값이 같으면 대상 분기 미실행 |
| DOC_ENGINE 미설정, pagerank 0 | ESConnection 선택 | 동일 | Infinity 결함을 이 설정에 귀속할 수 없음; ES 동작은 미검증 |

총 8회(base/head × 네 조건). 실제 init_settings의 backend 선택 구문, 실제 kb_app.update 함수 본문, 실제 InfinityConnection.update 본문을 실행했다. 초기 설정 함수 전체, HTTP decorator, ORM, 인증, 실제 DB는 실행하지 않았다. Infinity의 테이블 스키마·update 경계는 recorder로 대체했다. 따라서 `remove`의 잘못된 제출은 관측했지만 실제 DB의 오류 메시지나 사용자 응답을 재현했다고 주장하지 않는다. 배포 환경의 실제 DOC_ENGINE 값도 확인하지 않았다.

## ComfyUI: 요청 경로가 변경 분기를 배제함

`server.py:162,173`에서 cache_control이 web.Application에 등록되어 있고, `server.py:416–518`의 실제 이미지 조회 함수는 `@routes.get("/view")`에 연결된다. 이미지 이름은 query의 filename으로 받는다. `/view?filename=missing.png`의 request.path는 `/view`다.

`middleware/cache_middleware.py:38`은 request.path가 이미지 확장자로 끝나지 않으면 즉시 반환한다. 실제 view_image 본문과 middleware를 연결하고 빈 임시 폴더에서 실행한 결과:

| 입력 | base | head |
|---|---|---|
| path `/view`, query filename=missing.png | 404, headers `{}` | 동일 |
| path `/view`, query 없음 | 404, headers `{}` | 동일 |

총 4회. 네트워크 서버를 띄우지 않았고 다른 middleware나 브라우저 캐시도 실행하지 않았다. 이 관측은 해당 두 입력에서 **cache_control 자체가 헤더를 바꾸지 않는다**는 뜻이다.

기존 F08/P08의 합성 `.png` 경로에서는 헤더 변화가 관측됐지만, 그 결과를 실제 `/view` 이미지 조회의 회귀 증거로 해석할 수 없다. 과거 실험 파일과 점수는 그대로 보존했다. P08의 0/1→1/1은 공개 경로를 고정한 합성 진단의 결과로만 유지한다.

## 다음 실험에 반영할 조건

컨텍스트 수집은 객체 생성·callback 본문에 이어 **분기를 활성화하는 설정과 실제 요청 경로**까지 확보해야 한다. `docStoreConn`은 조건부 receiver로 표시하고, 배포 설정이 없으면 Infinity를 확정하지 않는다. query의 이미지 확장자를 request.path의 확장자로 대체하지 않는다.

후속 비교 사례의 결함 조건은 F04의 실제 `/update` 본문 + 명시한 Infinity 설정 + pagerank 변경 입력으로 구성할 수 있다. F08의 실제 `/view`는 변경 분기가 배제되는 음성 대조군으로 구성할 수 있다. 두 사례는 이미 조사한 저장소의 조건 정제이며 독립 holdout이나 새로운 성능 점수로 세지 않는다. 현재 검색기의 조건부 global 필드 해석 기능을 구현하거나 기본 정책으로 채택했다는 뜻도 아니다.

## 재현·감사

- `validate.py`: 원본 AST 본문 및 선택 구문 실행, 기대 관측 assertion.
- `observations.json`: 12개 실행 입력·출력, 원문 source span과 SHA-256.
- `prepare_evidence.py`, `provenance.json`: 실제 진입 코드가 기존 양쪽 world/base/head 및 pinned snapshot과 동일함을 검증, middleware 등록과 조건부 경로 기록.
- `freeze.json`: 이번 run 산출물 해시. 기존 frozen run은 수정하지 않음.

재현 명령: `tools/venv-parallel-review20260928/bin/python runs/real_entry_provenance_20261001/validate.py`, 이어서 같은 Python으로 `prepare_evidence.py` 실행. 모든 12개 관측이 예상과 일치했다. 결과 채점이나 모델 리뷰는 이번 단계에서 수행하지 않았다.
