# 새 4개 PR 계열 A/B/C 확인 평가

고정된 동일 LSP 정책을 기존 11개와 겹치지 않는 4개 PR 계열에 적용했다. B의 결함 검출은 2/4로 A·C의 1/4보다 많았지만, 양 방향 모두 성공은 A·B 1/4, C 0/4에 그쳤다. 새 표본이 작고 조건당 1회이므로 B의 통계적 우위를 주장하지 않는다.

| 지표 | A | B | C |
|---|---:|---:|---:|
| 근거 있는 결함 검출 / 4 | 1 | 2 | 1 |
| 수정 방향 오탐 / 4 | 0 | 0 | 0 |
| 양 방향 모두 성공 / 4 | 1 | 1 | 0 |
| 근거 있는 방향 판정 / 8 | 2 | 3 | 2 |
| 기권 / 8 | 6 | 5 | 6 |
| 인용·스키마·전송 검증 / 8 | 8 | 8 | 8 |

24 논리 평가에 22 실제 모델 호출. H04 양 방향의 B/C 입력이 각각 완전히 같아 해당 응답만 공유했다. 모든 조건의 실제 native 조회는 별도로 수행했으며 8 snapshot × 3조건 × 12 = 288 요청이다. 질의 오류 응답 0회, 모든 source/observation cap과 고정 입력·workspace 해시 검증을 통과했다. 원본 출력이나 인용을 수정하거나 실패 재호출하지 않았다. 작성자가 정답 라벨을 보며 24개 논리 응답 모두 검토했다.

| 새 사례 | 실제 코드 실행에서 확인한 차이 (각 버전 2회) | 리뷰 관찰 |
|---|---|---|
| Requests 1142 | GET/HEAD 기본 Content-Length: '0' → 미설정 | 모든 조건 기권. public get→prepare 경로·body/method 값이 리뷰 컨텍스트에 부족 |
| pytest 10081 | class skip + --pdb에서 tearDown 호출 True → False | B는 skip-helper 본문을 제공해 양방향 성공. A는 양쪽 기권, C는 수정 방향 성공·결함 방향 기권 |
| SymPy 15875 | e=-2*I+(1+I)**2: is_zero False → None, simplify(e).is_zero는 True | 모든 조건 기권. 실제 Add 인자 분류·assumption 호출 연결이 부족 |
| SymPy 19637 | kernS('(2*x)/(x-1)'): UnboundLocalError → 표현식 반환 | 모든 조건 결함 방향 검출. A는 수정 방향 성공, B/C는 이후 sympify 결과를 모른다는 이유로 기권 |

Witness는 실제 각 checkout의 모듈을 import했으며 모듈 위치를 확인했다. Python 3.11에서 오래된 코드의 collections/inspect 호환 alias를 적용했고 pytest의 generated version 모듈을 제공했다. pytest witness는 tearDown을 마커 기록으로 바꾸어 호출 여부를 관찰했으며 외부 plugin 자동 로드를 끄고 실제 --pdb 실행을 사용했다. 이 관찰은 원본 issue의 모든 환경·NameError/PDB 화면이나 전체 suite를 재현했다는 뜻은 아니다. Requests는 네트워크 없이 Request.prepare의 헤더를 관찰했으며 Amazon의 HTTP 응답은 재검증하지 않았다. 숨긴 witness를 리뷰의 부족한 근거를 보완하는 데 쓰지 않았다.

최초 Requests 1766 후보는 서버 challenge qop 목록과 클라이언트 Authorization qop 단일 토큰을 혼동하는 계약 문제가 있어 모델 호출 전에 Requests 1142로 교체했다. [RFC 2617 §3.2.2](https://www.rfc-editor.org/rfc/rfc2617#section-3.2.2)는 클라이언트 qop 선택을 단일 토큰으로 규정한다. selected_initial.json과 selection_revision.json에 초기 후보 및 제외 이유를 보존했다. 리뷰 결과를 보고 후보를 교체하지 않았다.

| 실제 모델 usage | A 8회 | B 8회 | C 6회 |
|---|---:|---:|---:|
| input tokens | 95244 | 95222 | 71885 |
| cached input tokens (input에 포함) | 66560 | 66560 | 49920 |
| output tokens | 5727 | 5506 | 4477 |

C의 낮은 물리 사용량은 같은 입력의 B 응답 공유 때문이지 C 자체의 더 낮은 비용을 증명하지 않는다. 같은 3개 repository 내 새 PR 사례이며 새로운 repository holdout은 아니다. 정방향 공개 수정과 역방향 파생 대조군이고 자연 결함 도입 PR 표본이 아니다. 기존 33 반복 쌍 결과와 합산하지 않는다.

자료: PROTOCOL_KO.md, freeze.json, witnesses.json, audit_all.json, annotations_all.json, results_all.json, validation.json. packets/·lsp_traces/·inputs/·raw/에 재현 자료를 보존했다. 다음 우선순위는 B 기본 + 변경 branch/인자 연결의 명시적 부분 코드 추출이며, C의 조건부 사용은 별도 ablation이 필요하다.
