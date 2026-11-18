# 새 저장소 3곳·실제 수정 PR12개로 확장한 LSP 비교

2026-10-01. Requests·pytest·SymPy의 실제 수정 PR12개를 추가하고, 실제 Pyright LSP와 기존 AST backend 비교를 완료했다. **기능 회귀11쌍의 엄격한 쌍 성공률은 양쪽 모두4/11, 근거 있는 결함 검출은7/11이었다.** pytest에서는 LSP가 한 쌍을 더 맞혔지만 SymPy에서의 기권·잘못된 실패 경로 설명 때문에 전체 이득은 확인되지 않았다.

기존 ComfyUI 구조 대조에서 보인2/4→3/4 개선을 새 저장소 전체에 일반화할 수는 없다. 정의 탐색과 리뷰 정확도를 구분해야 한다.

## 무엇을 확장했는가

[SWE-bench Verified 공식 데이터](https://huggingface.co/datasets/SWE-bench/SWE-bench_Verified)의 공개500개 test 사례에서3개 저장소의 PR을4개씩 선정했다. 공식 dataset 설명은 [SWE-bench 문서](https://www.swebench.com/SWE-bench/guides/datasets/)에서 확인할 수 있다. 해당 base commit의 소스를 내려받고 공식 code patch가 정확히 적용되는지 검증했다. 원본 dataset 응답, archive·patch 해시, commit·PR 링크를 저장했다.

각 PR에 원래 수정 방향과 되돌림 방향을 적용했다. **실제 버그 수정 PR12개 + 파생 되돌림 대조12개**이며, 자연 상태의 결함 도입 PR12개를 수집한 것은 아니다. safe/unsafe는 해당 회귀 계약의 재도입 여부를 뜻한다. 전체 PR 안전 보증도 아니다.

| 사례 | 실제 PR | 다룬 동작 | AST 쌍 정답 | LSP 쌍 정답 |
|---|---|---|---:|---:|
| E01 | [Requests1921](https://github.com/psf/requests/pull/1921) | 세션 헤더의 None 제거 | 실패 | 실패 |
| E02 | [Requests5414](https://github.com/psf/requests/pull/5414) | 점으로 시작하는 잘못된 URL | 실패 | 실패 |
| E03 | [Requests2931](https://github.com/psf/requests/pull/2931) | 바이너리 요청 본문 | 성공 | 성공 |
| E04 | [Requests2317](https://github.com/psf/requests/pull/2317) | bytes HTTP method 정규화 | 실패 | 실패 |
| E05 | [pytest5262](https://github.com/pytest-dev/pytest/pull/5262) | 텍스트 캡처 스트림의 mode | 성공 | 성공 |
| E06 | [pytest7521](https://github.com/pytest-dev/pytest/pull/7521) | CR·CRLF 줄바꿈 보존 | 실패 | 실패 |
| E07 | [pytest10051](https://github.com/pytest-dev/pytest/pull/10051) | 로그 목록 clear와 공유 참조 | 실패 | **성공** |
| E08 | [pytest7571](https://github.com/pytest-dev/pytest/pull/7571) | fixture 종료 후 로그 레벨 복원 | 성공 | 성공 |
| E09 | [SymPy22914](https://github.com/sympy/sympy/pull/22914) | Min/Max 코드 출력 형식 진단 | 별도 진단 | 별도 진단 |
| E10 | [SymPy23950](https://github.com/sympy/sympy/pull/23950) | Contains.as_set | 실패 | 실패 |
| E11 | [SymPy23534](https://github.com/sympy/sympy/pull/23534) | 재귀 symbols 호출의 cls 유지 | **성공** | 실패 |
| E12 | [SymPy14711](https://github.com/sympy/sympy/pull/14711) | 벡터와 sum 초기값0 | 실패 | 실패 |

실패에는 기권도 포함한다. E04·E06 등은 결함 버전을 검출했으나 수정 버전에서 기권해 쌍 정답이 아니다. 모델을 보기 전 로컬 재현 가능성과 작은 patch를 기준으로 선택했으며, 결과를 보고 유리한 사례로 교체하지 않았다. 무작위 표본·블라인드 평가·공개 데이터 암기에서 자유로운 holdout은 아니다.

E09는 준비 단계에서 고정 base가 이미 Min/Max를 조건식으로 렌더링한다는 사실을 확인했다. min/max 호출 문자열로의 출력 변화는 있었지만 공개 issue가 말한 실행 불가 결함은 재현되지 않았다. 따라서 모델 호출 전에 **기능 회귀11쌍 + 출력 형식 진단1쌍**으로 분리했다. 모델 입력의 공개 문제 설명과 diff는 변경하지 않았다. 양 arm 모두 E09의 양 버전에서 기권했다.

## 같은 조건에서 비교했는가

이전 실험의 backend.py와 lsp_client.py를 그대로 사용했다. 모델·프롬프트·스키마도 동일하다. Python AST는 문법 위치·소스 범위 추출에 사용하며 LSP 정의 요청 실패를 AST로 대체하지 않는다.

- 실제 Pyright1.1.414, stdio JSON-RPC `textDocument/definition`.
- 소스1600 + 정적 정의 관측400 reference tokens, BM25 prefix800, 최대12질의, 깊이3.
- `gpt-5.6-sol`, low, 모델 도구0, 재시도0.
- 같은 public issue 계약·diff·후보 위치 선택 규칙·예산을 양 arm에 적용했다. 정적 후보는 실행 경로의 확정이 아니라고 명시했다.
- LSP workspace에는 해당 package의 전체 production Python 소스를 배치했다. 검색 corpus는 변경 파일에서 정적 import2hop 및 조상 package initializer를 따라 양 버전의 합집합으로 결정했다. corpus 밖 native 정의는 필터링했다. 이 파일 선택 정책도 모델 호출 전에 고정했다.

이번 확장에서는 getter·전역 상수 조회 개선을 추가하지 않았다. 이전 사례와 corpus 구성이 다르므로 이전 점수와 합쳐 단일 개선율을 만들지 않는다.

24개 버전 ×2arm =48개 논리 리뷰다. 최종 입력이 같은15개 버전은 한 실제 응답을 공유했고, 다른9개는 별도 호출했다. **실제 모델 호출33회: AST24회 + 추가 LSP9회.** 입력이 같은 응답을 독립 표본으로 취급하지 않는다. 소스 본문이 달라진 버전은5개, 관측까지 포함해 입력이 달라진 버전은9개다.

## 주 평가 결과

쌍 정답은 되돌림 버전에서 근거 있는 결함을 검출하고 수정 버전에서 근거 있는 무결함 판정을 하는 경우다. 기권·근거 없는 경고·잘못된 실패 시나리오를 정답으로 계산하지 않는다. 분모에서 실패를 빼지 않는다.

| 기능 회귀11쌍 | AST | LSP |
|---|---:|---:|
| 근거 있는 결함 검출 | 7/11 | 7/11 |
| 실제 결함 경고 출력 수 | 7/11 | 8/11 |
| 수정·되돌림 양 버전 모두 정답 | 4/11 | 4/11 |
| 개별 버전 정답 | 13/22 | 13/22 |
| 수정 버전의 결함 오탐 | 0/11 | 0/11 |
| 기권한 버전 수 | 9/22 | 8/22 |

| 저장소별 기능 회귀 쌍 정답 | AST | LSP |
|---|---:|---:|
| Requests | 1/4 | 1/4 |
| pytest | 2/4 | **3/4** |
| SymPy | **1/3** | 0/3 |

### LSP가 개선한 부분

E07에서 `caplog.clear()`가 실제 `LogCaptureHandler.clear/reset` 중 어떤 구현으로 연결되는지와 그 본문이 드러났다. reset은 handler.records를 새 목록으로 바꾸므로 기존 call-stage 목록의 참조를 남긴다. clear는 기존 목록을 비운다. AST는 구현 연결 근거가 부족해 기권했지만 LSP는 해당 결함을 검출했다. 두 방식 모두 수정 버전은 맞혔다.

### 개선이 상쇄된 부분

E11은 되돌림에서 `symbols(..., cls=Function)`의 재귀 호출이 cls를 누락하는 결함을 양쪽 모두 검출했다. 수정 버전은 AST가 무결함 판정을 했고 LSP는 기권했다. 예산 안에서 확보한 본문 구성이 달라졌으며, 더 많은 정의 확보가 항상 더 나은 최종 컨텍스트를 뜻하지 않았다.

E12의 AST 결함 리뷰는 sum의 초기 정수0을 실패 원인으로 정확히 설명했다. LSP는 결함 위치를 찾았지만 `0*N.x`가 정수0을 만들어 넘긴다고 설명했다. 실제 값은 **0을 표현하는 Vector 객체**이고, 실패하는 정수는 sum의 기본 초기값0이다. 이 설명을 사후 실제 package 실행으로 확인했다. 잘못된 인과 설명 때문에 LSP 경고는 엄격한 검출로 인정하지 않았다. 위치 적중만 완화하면 LSP는8/11, 쌍 성공은5/11이 되지만, 이는 근거까지 맞은 리뷰 지표가 아니다. 사후 확인은 입력·라벨·주 witness를 변경하지 않았다.

E01·E02·E10은 실행 witness로 결함이 확인돼도, 모델에 전달된 소스에서는 public entry→내부 구현 연결이나 값이 닫히지 않아 기권했다. 비공개 witness를 모델이 본 근거처럼 사용해 정답을 부풀리지 않았다.

## 정의 탐색과 실제 실행 검증

Native LSP 정의 요청은288회, 서버 오류0회다. AST는287개의 후보 질의를 수행했다. 같은 선택 규칙이더라도 해석한 다음 대상에 따라 후속 탐색 경로가 달라지므로 총 질의 위치까지 강제로 같게 만들지는 않았다.

- 공통284개 질의 위치에서 저장소 내 함수·클래스 후보 확보: **AST42/284, LSP56/284**.
- arm 전체 기준: AST42/287, LSP59/288. 서로 다른 분모를 공통 위치 비교로 혼동하지 않는다.
- native 위치가 있는 응답242회, 빈 응답46회. 위치가 있어도 외부/비호출/미색인 정의여서 필터링한 요청183회다. 이183회를 서버 호출 실패라고 부르지 않는다.
- 조회 대기 시간 합계9.86초,24개 서버 시작 시간 합계2.67초. 모델 및 소스 준비 시간은 제외하며 AST 대비 속도 주장은 하지 않는다.

실제 package 코드로12개 사례의 원본/수정본을 각각 두 번 호출해 **48회 성공 관측**을 확보했다. 네트워크는 실행하지 않았다. Requests의 request preparation 경계, pytest 실제 캡처 클래스 및 실제 fixture/handler와 최소 item 저장소 경계, SymPy 실제 API를 사용했다. E12는 추가 사후2회로 실제 zero operand 타입과 전체 sum 결과도 확인했다. 공식 SWE-bench Docker harness 전체 통과로 해석해서는 안 된다.

오래된 코드가 Python3.11에서 import되도록 삭제된 collections/inspect 이름에 호환 alias를 제공했다. pytest의 생성 빌드 metadata는 메모리에 주입했으며 checkout 코드는 바꾸지 않았다. 누락 의존성 준비 실패와 수정 이력을 별도 보존했다. 의존성 목록, witness별 경계·import된 module 경로·출력·반복 결과를 남겼다.

모델33회는 transport·응답 스키마 모두 통과했고 사용량은 입력396,301 tokens(그중 cached239,104), 출력23,447이다. 이는 공유를 고려한 물리 호출 합계이며 LSP 전체 논리 arm 비용과 같지 않다.

### 감사 코드의 수정 이력

호출 전 고정했던 원래 감사기는 하나의 issue가 여러 checks에서 참조되면 잘못 실패로 처리했다. E06 AST는 stdout/stderr 두 재현을 같은 issue0에 연결해 정확히 이 경우였다. 스키마는 이를 금지하지 않는다. 모델 재호출 없이 **issue 인덱스의 합집합이 전체 issue를 덮고 모든 인덱스가 유효한지** 검사하도록 사후 보정했다. 원래 audit.py와 audit_original.json은 그대로 보존했고 보정 코드는 audit_v2.py로 분리했다. 최종 audit.json에는 이 한 건의 감사기 오판만 해소했다. 원래 고정 파일 해시는 모두 유지된다.

## 다음 실험의 우선순위

사례 확장만으로 LSP 전체 우위를 주장할 근거는 생기지 않았다. 관찰된 개선은 특정 호출 연결을 컨텍스트에 포함했을 때 발생했다. 반면 12회 질의 예산이 관련 없는 검색 함수에 소진되는 사례도 있었다. E11에서는 symbols 재귀 계약을 검토하는데 Integral 관련 질의가 포함됐다.

다음 단계는 **변경 함수와 public entry를 먼저 조회하고, receiver의 getter·재수출·전역 설정을 함께 확보하는 정책**을 양 arm에 동일하게 적용하는 것이다. 그 뒤 여러 seed로 반복해 컨텍스트 정책 효과와 단일 모델 출력 변동을 구분해야 한다. 순수한 자연 결함 도입 PR 및 새 저장소 holdout도 추가해야 한다. 현재 결과는 작성자 채점·단일 호출·공개 데이터·파생 reverse 대조라는 한계를 가진다.

## 결과 파일

- `PROTOCOL_KO.md`, `selected.json`, `provenance.json`: 사전 조건·공개 사례·실제 commit과 PR.
- `freeze.json`, `freeze_pre_audit_files.json`: 모델 호출 전 고정 해시와 초기 freeze 이력.
- `checkouts/`, `workspace_manifests/`, `lsp_traces/`, `packets/`: 공식 원본/수정 snapshot, LSP 원시 요청·응답, 실제 컨텍스트.
- `inputs/`, `raw/`: 물리 호출33회의 입력·응답. 공유 LSP 응답을 별도 호출처럼 복제하지 않았다.
- `witnesses.json`, `runtime_dependencies.txt`, `preparation_attempts.json`:48회 실행 증거와 환경 준비 이력.
- `vector_dispatch_check.json`: E12 실패 경로의 사후 실제 실행 확인.
- `audit_original.json`, `audit.json`, `audit_v2.py`: 원래 감사 결과 및 보정.
- `annotations.json`, `results.json`, `repository_summary.json`: 판정 이유 및 저장소별 점수.
- `validation.json`, `validate_results.py`, `result_seal.json`: 고정 파일·소스·응답·집계 무결성 검증.
