# B 기본 + 변경 분기·호출 인자 부분 코드 + 조건부 조회

**이번 D/E를 기본 정책으로 채택할 근거는 부족하다. B를 유지한다.** D는 양 방향 성공에서 B와 같았지만 결함 검출을 22/33에서 19/33으로 낮췄다. E는 양 방향 성공도 11/33에서 7/33으로 낮췄다. D가 개선한 개별 사례는 있지만 기존 유용한 근거가 밀리거나 여전히 필요한 분기가 빠지는 손실이 있었다.

## 구현

| 조건 | 정책 |
|---|---|
| B | 기존 변경 함수·공개 진입 후보 우선 LSP 입력. 이전 응답을 재사용하지 않고 새로 호출한 대조군 |
| D | B 우선순위 + 함수 서명·변경 줄 주변·조건문·관련 대입/default 추출·동명 호출부 인자의 부분 코드 |
| E | D + native call definition이 미해결인 receiver 또는 공급된 줄에서 선언이 안 보이는 global 의존성에 대한 조건부 추가 조회 |

D는 변경 분기 근거를 초기600 reference tokens 안에, caller 후보를 prefix800 안에 넣는다. 큰 함수나 전체 단위가 예산에 안 들어가는 경우 unit당300토큰 이내 부분 코드를 선택한다. caller 선택은 AST의 호출명 일치에 기반한 후보이며 실제 target·인자 값·dispatch 증명이 아니다. 함수/파일·행 번호·생략 표시를 유지한다. E의 provenance는 source 여유80토큰 이상에서 최대4회, 총12회에 포함한다. E는 extra query에만 non-callable native 위치·관련 import 근거를 추가한다.

전체 source1600 + static observation400 reference tokens, native 질의12회/snapshot, 깊이 cap3을 유지했다. 실제 조회는 Pyright1.1.414 stdio의 textDocument/definition이고 실제 최대 깊이는 B/D/E 모두1이었다. source expression·초기 선택을 바꾸면 lexical fallback query도 달라지므로 D를 단일한 source 표시 방식만의 순수 ablation으로 주장하지 않는다. AST는 위치/후보/소스 범위를 정하고 실제 정의 결과를 대체하지 않는다.

부분 코드·부족한 import를 동작 부재의 증거로 쓰지 않는다. 정적 후보를 런타임 보장으로 쓰지 않는다. native query가 unresolved인 것은 안전 증명이 아니다.

## 동일 사례의 새 호출 비교

기존11PR계열22방향 × B/D/E × 3회 = 198논리 평가. 앞서 평가한 별도4PR계열8방향 × B/D/E × 1회 = 24논리 평가. 총222논리 평가, 동일 반복 내 같은 입력20개를 공유해202물리 모델 호출을 수행했다. B 모델 출력도 모두 새로 생성했고 과거 응답·반복 간 응답 공유는 없다. 모델 gpt-5.6-sol low, 리뷰 도구0, 재시도0, provider seed 통제 없음.

| 기존11계열, 각3회 | B | D | E |
|---|---:|---:|---:|
| 근거 있는 결함 검출 / 33 | 22 | 19 | 19 |
| 수정 방향 오탐 / 33 | 1 | 0 | 0 |
| 양 방향 모두 정답인 반복 쌍 / 33 | 11 | 11 | 7 |
| 근거 있는 방향 판정 / 66 | 34 | 30 | 28 |
| 기권 / 66 | 29 | 31 | 33 |
| 인용·ledger 검증까지 통과 / 66 | 65 | 63 | 64 |

B의 오탐은 newline=""의 효과를 반대로 설명한 응답이다. source와 Python 의미를 검토하여 unsupported로 기록했다. D/E에서는 이 오탐은 없었지만 검출/기권/양방향 성공의 이득으로 이어지지 않았다. 이번 새 B와 비교하며 이전 실행의 B12/33을 대조군 점수로 가져오지 않는다.

| 별도4계열, 각1회 | B | D | E |
|---|---:|---:|---:|
| 근거 있는 결함 검출 / 4 | 1 | 1 | 1 |
| 수정 방향 오탐 / 4 | 0 | 0 | 0 |
| 양 방향 모두 정답 / 4 | 1 | 1 | 1 |
| 기권 / 8 | 5 | 4 | 4 |
| 인용 검증까지 통과 / 8 | 8 | 7 | 7 |

4건은 이미 앞선 평가에서 본 사례이고 현재 정책 개발에 사용한 실패 분석과 독립된 holdout이 아니다. 이전11계열과 합산하지 않는다. 4건에서는 E의 extra query가 예산 내 실제 선택되지 않아 D/E의8개 입력이 모두 같았고 D 응답을 공유했다. 이는 모든 gap이 해결되었다거나 E가 더 저렴한 정책이라는 증거가 아니다.

## 사례별 결과와 손실 원인

| 계열 | B 양방향 성공 /3 | D /3 | E /3 | 관찰 |
|---|---:|---:|---:|---|
| E01 None 헤더 | 0 | 0 | 0 | prepare_request→merge_setting 인자는 일부 공급됐지만 request.headers 값/후속 처리·wire 연결이 부족 |
| E02 선행 점 URL | 0 | 0 | 0 | parsed host 및 public get 경로가 미확정. E의 categorical 안전 응답3건과 D1건은 근거 부족 |
| E03 binary payload | 0 | 1 | 0 | D에서 bytes decoder 제거와 caller data 근거로 수정 방향1회 성공. 별도 응답의 actual params=None 단정은 인정하지 않음 |
| E04 bytes method | 0 | 0 | 0 | 실제 import/runtime alias 연결이 부족. 미해결 import를 NameError로 단정하지 않음 |
| E05 wrapper mode | 3 | 3 | 3 | 모든 조건 안정적 |
| E06 CR 보존 | 1 | 1 | 0 | D는 오탐0이나 결함 방향1회 ledger comparison 오류. E 수정 방향은 모두 기권 |
| E07 list identity | 3 | 3 | 2 | E1회는 공유 리스트 binding 부족을 이유로 기권 |
| E08 handler level 복원 | 3 | 2 | 2 | D/E에서 실제 caplog fixture→_finalize 호출 본문이 빠짐. callback 연결 근거를 보존할 필요 |
| E10 Contains.as_set | 0 | 0 | 0 | D/E는 Piecewise/constructor 경로 불확실성을 더 자주 기권 사유로 사용 |
| E11 symbols cls | 0 | 1 | 0 | D/E에 실제 cls=Symbol 서명과 재귀 전달부가 공급됐으나 문자열→객체 생성 분기는 여전히 생략 |
| E12 vector sum | 1 | 0 | 0 | sum의 integer 시작값0 검출은 가능하나 zero Vector의 후속 더하기까지 확정하지 못해 수정 방향 기권 |

E11의 실제 코드 서명은 `symbols(names, *, cls=Symbol, **args)`다. 이 사례를 args.pop("cls") 구현으로 설명하지 않는다. default extraction 통합 테스트의 args.pop은 별도의 합성 fixture다. E12의 0*N.x는 zero Vector이며 scalar0은 sum의 기본 시작값이다.

새4계열 중 kernS의 UnboundLocalError 정방향/역방향은 모두 성공했다. Requests 기본 Content-Length와 SymPy is_zero는 여전히 public-call/default/assumption 연결이 부족했다. pytest class skip은 이번 호출에서 모두 기권했다.

## 검토 기준과 검증

222개 논리 응답의 issue claim/scenario, no-issue branch/head, 기권 head observation을 작성자가 모두 검토했고 인용·schema·ledger는 자동 검사했다. 공개 계약/재현 입력과 직접 보이는 변경 코드·Python 의미로 판단하며, 숨긴 witness가 부족한 reviewer context를 보완하지 않게 했다. 공개 reproduction과 바뀐 내부 구현을 연결하는 판단이 들어가는 비블라인드 작성자 채점이며 독립적인 런타임 binding 인증이 아니다. 특히 E08의 fixture callback gap은 위 표에 별도로 공개한다. omitted code unknown과 parsed value 가정을 구분한다. 관련 계약의 관측 자체가 unknown인데 categorical no_supported_issue를 반환한 경우 성공으로 세지 않았다. 자세한 기준은 GRADING_KO.md와 각 annotations_all.json에 있다.

모든202물리 호출은 전송·스키마 성공, 도구0이다. 인용 오류7논리 응답과 issue의 ledger comparison unknown1논리 응답을 invalid로 유지했다. 오류를 교정하거나 재호출하지 않았다. invalid·기권은 분모에 남는다. 수정 방향의 valid issue 보고는 오탐이며, unsupported 추가 issue가 있으면 양방향 성공이 아니다. 보고된65 issue 위치는 실제 head 파일·행 범위에 있음을 확인했으나 precise localization 점수는 별도로 측정하지 않았다.

고정 코드·입력·분석 프로토콜 해시, 원본 workspace 소스 해시, source/observation cap,222논리/202물리 기록·검토 ID 수를 검증했다. 기존11계열의33반복 쌍은11계열의 반복 측정이다. 통계적 우위, 자연 결함 도입 PR 검증, 논문 완전 재현/RL을 주장하지 않는다.

## 조회 및 비용

B의360회 native trace는 이전 고정 run에서 참조했고 D/E는 각각360회 새로 요청했다. final new 요청720회, 질의 오류 응답0. E의 extra query는 기존11계열에서52회/264회였고 별도4계열에서는0회/96회였다. 총 질의 예산은 줄지 않았다.

준비 단계에서 AST 탐색·선택 그룹을 캐시했다. 모델 호출0회에서 초기 자료를 보존하고, 먼저 완성된16개 packet의 source/observation B/D/E가 최적화 전후 동일함을 확인했다. 초기 준비 overhead의 저장된 요청384회와 중단 시 저장되지 않은 in-flight 요청이 별도로 존재한다. 이를 final720회에 숨기지 않는다. 통합 점검 요청은 별도다.

| 실제 모델 사용량 | B74회 | D74회 | E54회 |
|---|---:|---:|---:|
| input tokens | 889672 | 889874 | 652598 |
| cached input tokens (input에 포함) | 574080 | 607360 | 449280 |
| output tokens | 53446 | 52571 | 40412 |

E의 낮은 물리 사용량은 동일 입력 공유 때문이다. reference evidence 예산과 provider usage는 서로 다른 단위다. 기존 공유 LSP config의 autoSearchPaths warning은 trace에 보존되며 클라이언트 python.analysis 설정도 전달했다.

## 다음 개선의 구체적 우선순위

1. **B 기본 유지.** D/E의 이번 전체 적용 결과는 채택 보류한다.
2. **기존 근거 보호 후 부족 부분만 보충.** B의 fixture callback, import, terminal return/constructor를 보호하고 큰 함수 전체가 거절된 경우에만 부분 선택을 추가하는 별도 ablation이 필요하다. 기존 source1600 안에서 어떤 근거를 대체하는지 명시해야 한다.
3. **실제 호출 인자 연결을 우선한다.** `__init__` 같은 흔한 동명 caller를 넓게 넣는 대신 native 반환 위치가 지지하는 caller/argument 후보와 본문을 우선한다. E01/H01은 entry→Request defaults→prepare→helper, E11은 재귀 string branch→cls constructor를 닫는 것을 목표로 한다.
4. **E provenance는 핵심 근거를 밀어내지 않을 때만 실행.** 현재 남은80토큰과 gap gate만으로는 성공이 늘지 않았다. context 교체로 잃는 근거를 기록하는 기준이 필요하다.

이 다음 정책은 이번 결과에 근거한 제안이며 아직 구현·평가했다고 주장하지 않는다. 이번 구현, 통합 테스트, B/D/E 비교는 완료했다.

재현 자료: policy.py, fragments.py, experiment.py, check_policy.py, policy_tests.json, PROTOCOL_KO.md, freeze.json, precall_validation.json, preparation_summary.json, audit_all.json, adjudication_notes.json, annotations_all.json, results_all.json, validation.json. packets/·lsp_traces/·inputs/·raw/에는 고정 소스와 실제 응답이, preflight_preopt/에는 초기 준비 자료가 있다. 과거 run은 수정하지 않았다.
