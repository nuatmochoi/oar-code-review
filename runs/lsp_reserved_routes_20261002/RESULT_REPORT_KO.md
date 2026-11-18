# 후속 LSP 탐색 예약: 미사용 PR 비교 결과

예약 A는 새 두 PR에서 기존 B보다 근거 있는 리뷰를 더 많이 생성했다. 결함 탐지는 B 0/6 → A 4/6, 양방향 모두 맞힌 비교는 0/6 → 2/6이다. 유망한 후보로 유지하되 기존 B를 대체하지 않는다. 이전 자동 A와 같은 표본에서 비교하지 않았으므로 예약 변경의 독립 효과는 아직 증명하지 못했다.

## 결과

| 지표 | 기존 B | 예약 A |
|---|---:|---:|
| 호출·schema 유효 | 12/12 | 12/12 |
| 근거 있는 unsafe 탐지 | 0/6 | 4/6 |
| safe 방향 오탐 | 0/6 | 0/6 |
| 근거 있는 safe 판단 | 0/6 | 3/6 |
| 양방향 모두 근거 있는 비교 | 0/6 | 2/6 |
| 근거 있는 개별 판단 | 0/12 | 7/12 |
| insufficient_evidence | 8/12 | 5/12 |
| 근거 부족 issue | 2 | 0 |
| 원시 정답 방향 일치 | 4/12 | 7/12 |

원시 방향 일치는 unsafe의 reported_issues 또는 safe의 no_supported_issue이며 제공 코드의 근거 충족과 다르다. 보류를 사후 성공으로 바꾸지 않았고 실패·보류도 분모에 유지했다. 작성자가 정답을 아는 상태에서 전수 채점했다. PR 2개의 3반복은 독립 PR 6개가 아니며 통계적 우월성을 주장하지 않는다.

| 사례 | A 탐지 | A 정상 판단 | A 양방향 성공 |
|---|---:|---:|---:|
| V01: 배열 mock patch 수집 | 2/3 | 3/3 | 2/3 |
| V02: directory symlink 수집 | 2/3 | 0/3 | 0/3 |

V02는 결함 방향은 찾았으나 정상 방향 모두 보류했다. 오탐 0만으로 양방향 리뷰가 완성됐다고 볼 수 없다.

## 무엇을 바꿨는가

A는 변경 root 호출 → root의 native references → 새로 확보한 함수의 native definition → 나머지 root 호출 → 후속 references 순서로 조회한다. 후속 정의를 root의 일반 호출보다 앞세우는 우선순위 예약이며 고정 6/6 예산 분할은 아니다. 반환식 호출을 먼저 따라가고, 반환 필드 접근도 native definition 후보로 제출한다. 반복되는 syntactic callee, Python builtin 이름, literal receiver 호출은 생략한다. builtin shadowing이나 같은 표현의 서로 다른 런타임 receiver를 놓칠 수 있다.

특정 사례·함수명 route table 없이 실제 Pyright 1.1.414 definition/references 반환 위치를 AST 소속 선언에 매핑한다. 함수 전체, 소속 class header와 관련 imports를 보존한다. class 대상은 Python 일반 protocol의 __init__/__call__ 전체를 포함한다. source cap을 넘는 package는 자르지 않고 차단한다. native 미해결을 AST 이름 검색 성공으로 대체하지 않는다. 정적 연결이며 실행 callgraph 인증이 아니다.

이전 사례 V01_b/V02_b/J04_a에서 두 차례 개발 검증했다. 총 72회의 실제 native 조회, 모델 호출 0이다. 첫 개발 라운드에서는 광범위한 references가 관련 없는 소비자를 많이 가져왔다. 두 번째 라운드에서 후속 references보다 반환식·정의 연결을 우선했다. 기존 V02_b에서 saferepr → SafeRepr.repr → _ellipsize 연결까지 확보했다. 기존 V01의 최종 head_line 소비 연결과 J04의 큰 fixture marker package는 여전히 미해결이다. 모든 개발 라운드 기록을 보존했다.

두 번째 개발 결과 후 algorithm_seal.json을 만들고 미사용 PR을 선정했다. 이후 policy.py 수정은 없다. 새 표본 결과를 보고 정책을 튜닝하지 않았다.

## 새 표본과 실제 재현

cached SWE-bench의 pytest-dev__pytest-5631과 pytest-dev__pytest-7982는 기존 selected/selected_initial ID 목록과 겹치지 않는다. 같은 pytest 저장소와 파일은 기존에 본 대상이다. 공개 문제·공식 patch를 보고 짧게 재현 가능한 계약을 사람이 선정했으며 무작위 표본, dataset-blind 또는 저장소 홀드아웃은 아니다.

- V01 / 5631: zero-argument test에 unittest.mock.patch("test_sample.VALUE", new=np.array([-5.5, 3.0]))를 적용한다. test body는 patched VALUE.tolist()를 확인한다. 계약은 ambiguous ndarray truth ValueError 없이 1 pass / exit 0이다. 원본은 collection ValueError / exit 2 / 0 pass, 수정본은 1 pass / exit 0을 각 2회 검증했다.
- V02 / 7982: suite/linked가 absolute external 디렉터리를 가리키는 symlink이고 external/test_link.py 하나만 존재한다. ignore 패턴·cycle은 없다. 계약은 linked/test_link.py를 수집해 1 pass / exit 0이다. 원본은 0 test / exit 5, 수정본은 suite/linked/test_link.py::test_link의 1 pass / exit 0을 각 2회 검증했다.

두 사례 모두 a는 unsafe 역방향, b는 safe 정방향이다. API에 제공한 것은 공개 고정 계약, diff, 각 조건의 source/정적 관측이다. witness 결과·정답 labels·test_patch는 reviewer/selector 입력에 포함하지 않았다. 출력 계약의 성공 여부는 실행 witness로 검증했지만 reviewer의 코드 공백을 채우는 데 사용하지 않았다.

## 같은 상한에서의 탐색

B/A 모두 최대 source 2600토큰, 관측 400토큰, 실제 native semantic query 12회, 동일 생산 source corpus와 full workspace를 사용한다. B는 기존 scheduling/lexical/prefix800을 유지하고 global CAP만 2600으로 설정한다. B_audit의 legacy limits.source_tokens=1600 metadata는 runtime cap을 반영하지 않는다. freeze/protocol/실제 token 검증은 2600이다.

| 입력 | B source | A source | A definition / references | corpus 대상 반환 조회 | A 대기 조회 |
|---|---:|---:|---:|---:|---:|
| V01_a | 2581 | 1247 | 10 / 2 | 6 | 14 |
| V01_b | 2581 | 1263 | 10 / 2 | 6 | 14 |
| V02_a | 2600 | 2556 | 11 / 1 | 7 | 51 |
| V02_b | 2600 | 2550 | 11 / 1 | 7 | 51 |

새 입력 모두 깊이 2까지 조회했고 cap 초과 package 차단은 0이다. source 공간과 후속 연결이 늘어도 조회는 12회 상한을 소진했다. 비교 준비는 B 48 + A 48 = 96회, 개발 72회까지 이번 작업의 native 조회 총 168회다. JSONRPC trace의 실제 semantic request 수와 각 audit jumps 수를 별도 대조해 일치함을 확인했다.

A의 V01 references는 num_mock_patch_args → getfuncargnames → FixtureManager.getfixtureinfo 연결을 확보했다. V02는 visit의 실제 references로 Session.collect/Package.collect를 확보했다. V02의 _recurse 구현은 여전히 제공되지 않았으며 정상 방향에서 그 값이 미해결이다.

실제 source 길이·내용·표시 형식은 다르다. 같은 최대 예산 비교이며 길이와 framing을 맞춘 단일 변수 비교가 아니다. 예약 외에 생략 규칙·반환식 우선·후속 참조 순서도 함께 바꿨다. 이전 자동 정책 A0를 같은 PR에 실행하지 않았으므로 scheduling 하나의 인과 효과나 이전 run 대비 일반화 향상을 주장하지 않는다.

## 근거 판정의 범위

B의 V01 unsafe 2개 issue는 ndarray membership 실패를 정확히 설명했지만 getfuncargnames/getfixtureinfo 호출 연결이 공급되지 않아 수집 실패 판단에 사용자 정의 code gap이 남았다. B의 V01 safe 2개 no_supported_issue도 helper의 identity 비교만으로 최종 계약을 확정해 성공으로 인정하지 않았다.

A는 그 연결을 확보했다. 공개 unittest.mock.patch의 표준 wrapper/patching semantics와 명시된 2-element ndarray truth semantics를 사용해 argument discovery의 예외를 설명할 수 있어 unsafe 2개를 인정했다. NumPy·Python stdlib 전체 source나 실제 실행 인증을 요구하지 않았다. safe 3개는 identity-only count=0이 zero declared arguments를 유지하는 인과를 보여 scoped 정상 판정으로 인정했다. safe r2는 독립적으로 관측한 종료 코드가 없다는 한계를 명시했지만, 관련 코드 연결을 제공하고 no_supported_issue를 제출했다. 인과 근거가 없는 B와 이 점이 다르며 r2를 insufficient_evidence로 바꾸거나 witness로 확정하지 않았다.

V02 unsafe A의 2개 issue는 follow_symlinks=False가 symlink에서 false를 반환해 recurse를 호출하지 않는다는 점을 설명했다. Session.collect가 visit을 직접 호출하고 non-file linked 항목을 건너뛰므로 target file을 수집할 수 없다. 정확한 exit code 또는 미제공 _recurse의 결과를 알 필요 없이 head의 계약 위반이 성립한다. base의 실제 recurse 허용과 최종 출력까지 정적으로 인증한 것은 아니며 해당 표현을 별도 측정 사실로 채점하지 않았다. safe는 recurse의 반환이 필요해 모두 보류했다. A unsafe의 나머지 두 insufficient_evidence 응답은 좁은 관찰이 맞아도 탐지 성공으로 교정하지 않았다.

adjudication_notes.json 및 annotations_all.json에 이 기준과 전수 예외 사유를 기록했다. 과도한 전체 runtime 증명을 요구하는 대신 고정 계약의 변경 인과를 보는 작성자 판정이며 독립 블라인드 검증이 남아 있다.

## 실행·보존 검증

2 PR × 2 방향 × B/A × 3 반복 = 새 물리 모델 호출 24회. gpt-5.6-sol low, tools0, workers4, retry0, seed 미통제, 응답 재사용0. 호출/schema24/24, source/observation/query상한, frozen algorithm/input, 전체 package/owner, workspace해시, issuefile/line 존재, nativeerror0 검증 모두 통과했다. issue 위치 존재 검증은 localization accuracy 측정과 다르다.

Witness는 Python3.11에서 오래된 pytest를 위한 collections/inspect alias, in-memory generated version, assertplain, external plugin off를 사용했다. 이번에는 -bb 없이 실행했다. NumPy2.2.6을 실험용 venv에 설치했다. 초기 8회 중5631의4회는 importlib_metadata 미설치로 실패해 importlib-metadata8.7.0/zipp4.1.0을 추가하고 해당4회를 재실행했다. symlink 예비1회 + 초기8회 + 수리4회 = 실제 subprocess 시도13회, 최종 유효 witness8회다. 초기 실패 로그를 보존했다. 생산 source는 공식 patch 외에 추가 수정하지 않았다.

API input tokens: B152088 / A144694; cached input B58240 / A91520; output B7646 / A9112. 과금액·비용 우월성은 산출하지 않았다.

## 다음 단계

먼저 동결된 이전 자동 A0를 같은 두 PR·동일 상한에서 비교해 정책 묶음의 직접 차이를 확인한다. 현재 A/B 응답은 동결해 보존하고, 어떤 응답을 재사용하는지 새 비교의 논리·물리 호출 수를 구분한다. A0 정책은 새 결과를 보고 수정하지 않는다. 이렇게 해야 표본 차이와 정책 변경을 분리할 수 있다.

이후 _recurse 같은 callback/필드 전달을 따라가는 일반 규칙과 큰 source package의 예산 배분을 이미 본 사례에서 개발하고, 다시 seal한 뒤 추가 미사용 PR에 검증한다. 이번 두 PR을 개발에 사용하면 후속 일반화 평가에서는 제외한다. 기존 B 유지, 예약 A는 확장 검증 후보로 둔다.
