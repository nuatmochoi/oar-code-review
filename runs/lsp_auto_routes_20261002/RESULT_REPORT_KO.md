# 자동 LSP 경로 선택: 미사용 PR 비교 결과

자동 선택 A의 개선은 이번 비교에서 확인하지 못했다. 기존 B를 유지한다. 호출 실패 때문에 성과가 사라진 것이 아니라, 리뷰에 제공한 코드가 고정 출력 계약까지 연결되지 않았고 다수 응답이 판단을 보류했기 때문이다.

## 결과

| 지표 | 기존 B | 자동 A |
|---|---:|---:|
| 유효 응답 | 12/12 | 12/12 |
| 근거 있는 결함 탐지 | 0/6 | 0/6 |
| 안전 방향 오탐 | 0/6 | 0/6 |
| 양방향 모두 근거 있는 판단 | 0/6 | 0/6 |
| 근거 있는 개별 판단 | 0/12 | 0/12 |
| 판단 보류 | 9/12 | 11/12 |
| 근거가 부족한 issue | 2 | 1 |
| 원시 정답 방향 일치 | 3/12 | 1/12 |

보류는 성공으로 바꾸지 않았다. 원시 방향 일치는 unsafe에서 reported_issues, safe에서 no_supported_issue인 응답 수이며 코드 근거의 충족을 뜻하지 않는다. 오탐 0은 안전 판단 성능의 증명이 아니다. 두 조건 모두 안전 방향의 근거 있는 정상 판정은 0이다. 작성자가 입력과 정답을 아는 상태에서 전수 채점했으며, 두 PR의 각 3반복은 6개의 독립 PR이 아니다.

## 실험 대상과 동결

알고리즘을 기존 fixture 사례에서 개발·검증한 뒤 algorithm_seal.json을 만들고 새 PR을 선정했다. 이후 policy.py 수정은 없다. 기존 선정 26개 ID와 겹치지 않는 pytest-dev__pytest-6202 및 pytest-dev__pytest-7205를 사용했다. 같은 pytest 저장소와 일부 파일은 기존에 본 대상이며 저장소 홀드아웃은 아니다. 공개 문제·patch를 보고 재현 가능한 계약을 사람이 선정했으므로 무작위 또는 데이터셋 블라인드 표본도 아니다.

- V01 / 6202: 실패 테스트의 TestReport.head_line가 test_boo[..[]를 정확히 보존해야 한다. assert 0의 실패와 exit 1은 정상이다. original은 test_boo[.[]로 이름을 변형하며 fixed는 보존한다.
- V02 / 7205: b"Hello World" 파라미터를 Python -bb와 --setup-show로 출력해 BytesWarning 없이 1 pass / exit 0이어야 한다. original은 setup/teardown에서 BytesWarning, exit 1이며 fixed는 통과한다.

두 PR 모두 a=unsafe 역방향, b=safe 정방향이다. 모델에는 공개 계약·diff·각 조건의 코드와 정적 관측만 제공했다. 숨긴 witness 결과, 정답 labels, SWE-bench test_patch는 제공하지 않았다.

2 PR × 2 방향 × B/A × 3 반복 = 24회의 새 물리 호출. gpt-5.6-sol low, tools 0, workers 4, retry 0, seed 미통제. 응답 재사용은 없다. 모델 호출·schema 검증 24/24, 실제 LSP 오류 0, frozen 입력·source·workspace 해시 및 전체 함수/소속 class header 보존 검증 모두 통과했다.

## 자동 선택 방식과 실제 탐색

B는 기존 priority/lexical 정책의 총 source 상한을 2,600토큰으로 적용했다. A는 변경 함수에서 실제 Pyright definition 및 references 요청을 하고 반환 위치를 AST의 소속 선언에 매핑한다. 함수 전체, 소속 class header, 관련 import를 보존한다. 클래스 대상으로는 전체 __init__/__call__을 포함하는 일반 Python protocol 규칙을 쓴다. 특정 사례나 프로젝트 함수명으로 경로표를 작성하지 않았다. native 미해결을 AST 이름 검색 성공으로 대신하지 않는다. 정적 후보를 실제 실행 callgraph로 인증하지 않는다.

각 조건은 source 최대 2,600토큰, 관측 최대 400토큰, 실제 native semantic 조회 최대 12회로 같다. 실제 source 양과 표시 형식은 다르므로 길이·프레이밍을 통제한 단일 변수 실험은 아니다. B_audit.limits.source_tokens의 1600은 이전 코드의 고정 metadata이며 이번 실행의 global CAP·protocol·freeze·실제 검증 상한은 2600이다. 기존 코드를 소급 수정하지 않고 이 불일치를 기록한다.

| 입력 | B source 토큰 | A source 토큰 | A definition / references | 유효 corpus 대상이 있는 조회 | A 탐색 대기 |
|---|---:|---:|---:|---:|---:|
| V01_a | 2594 | 415 | 10 / 2 | 1 | 6 |
| V01_b | 2582 | 403 | 10 / 2 | 1 | 5 |
| V02_a | 2594 | 553 | 11 / 1 | 1 | 16 |
| V02_b | 2578 | 683 | 11 / 1 | 2 | 20 |

각 입력·arm에서 12회씩, 새 비교 준비의 실제 native 조회는 B 48 + A 48 = 96회다. 기존 사례 개발 검증 12회까지 합하면 이번 작업은 108회다. 새 입력 A의 source 상한 초과 package 차단은 모두 0이다. 소스 공간이 남아도 조회 상한을 먼저 소진했다. null 반환 외에 builtin typeshed 또는 corpus 밖 대상도 탐색에 포함되어 있어 null 개수만으로 전체 미해결률을 해석하면 안 된다.

V01의 native references로 getmodpath → reportinfo는 찾았지만, 이름의 parts 구성과 reportinfo/location → TestReport.head_line 소비 경로는 확보하지 못했다. V02는 _show_fixture_action의 참조로 pytest_fixture_setup/post_finalizer를 확보해 cached_param 생산 코드를 포함했다. 안전 방향에서는 saferepr 정의도 찾았지만 다음 단계 SafeRepr 구현에 도달하지 못했다. 특히 V02는 12회 전부 깊이 0 조회였고 탐색 대기가 16~20개 남았다. V01은 깊이 1까지 일부 진행했으나 최종 소비 연결은 미해결이었다.

## 근거 판정 사례

B의 V02 unsafe 두 응답은 bytes 파라미터가 cached_param에 들어간다고 가정했다. B 입력에는 해당 할당 및 ids 분기 코드가 없어 두 issue를 근거 부족으로 판정했다. A는 그 생산 코드를 확보했지만 V02 unsafe 세 응답 모두 insufficient_evidence로 제출했다. Python bytes 변환의 좁은 인과 관찰이 들어 있어도 보류 응답을 사후 탐지 성공으로 고치지 않았다.

A의 V01 unsafe 한 응답은 로컬 replace와 reportinfo tuple만으로 TestReport.head_line 변형 및 exit 동작까지 확정했다. 숨긴 witness에서는 같은 방향이 재현되지만 제공된 코드에는 출력 소비 연결이 없으므로 탐지 성공으로 인정하지 않았다. B의 V01 safe 한 응답도 직접 join만으로 최종 head_line 보존을 확정해 정상 판단 성공으로 인정하지 않았다. 양쪽에 같은 기준을 적용했다. annotations_all.json과 adjudication_notes.json에 전수 판정 및 예외 이유를 기록했다.

## 재현 및 환경

최종 original/fixed를 각 2회 검증해 8개 유효 witness를 확보했다. 초기 8회 중 V02 original 2회는 오류 표시용 wcwidth 의존성이 없어 실패했다. 실험용 venv에 wcwidth 0.2.13을 설치하고 V02의 4회를 다시 실행했다. 총 12회 subprocess 시도, 초기 의존성 실패 2회, 최종 유효 witness 8회다. 초기 로그를 witnesses_initial.json에 보존했으며 생산 코드에는 추가 수정이 없다. Python 3.11에서 오래된 pytest 실행을 위한 collections/inspect aliases, 메모리상 _pytest._version, assert=plain 및 외부 plugin 비활성 설정을 사용했다.

API 입력 토큰 합계는 B 150,848 / A 127,020이고 cached input은 각각 91,520 / 83,200이다. 출력 토큰은 B 8,576 / A 8,599다. 과금 금액이나 유의한 비용 개선은 산출하지 않았다.

## 다음 실험의 변경점

B를 유지하고 A의 조회 배분을 개발 단계에서 개선한다. 반복되는 builtin/동일 호출 탐색을 줄이고 새로 찾은 사용자 정의 함수의 다음 깊이 탐색에 조회 몫을 예약한다. 공개 계약의 반환값·필드 소비 연결도 일반 규칙으로 우선 탐색한다. 재조회·대기 큐·미해결 원인을 명시하고 source 여유와 query 부족을 구분한다.

먼저 이미 본 사례에서 SafeRepr 또는 출력 소비 경로까지 자동 연결되는지 검증한 후 알고리즘을 다시 동결하고 추가 미사용 PR에서 비교한다. 이번 두 PR 결과는 그대로 보존하며 이 결과를 보고 조정한 정책을 같은 표본의 새로운 일반화 성과로 발표하지 않는다. 학회 논문의 정확한 재현이나 LSP 자체의 우월성을 이번 실험으로 주장하지 않는다.
