# 출력 경로 소스의 완전 보존 실험

2026-10-02. 세 출력 경로 함수의 소스 누락을 해결하는 추출기를 구현하고, 기존 O와 개선 C를 새 모델 호출 12건으로 비교했다. 이름 대입·fixture 등록·출력 목록 추가 누락은 해결됐다. 하지만 근거 있는 회귀 검출과 양방향 성공은 두 조건 모두0이며, 전체 fixture 전달 경로의 추가 연결이 필요하다. 기본 B를 대체하지 않는다.

## 구현

`policy.py`의 `complete_bundle`은 이미 선택된 출력 경로 단위를 완전한 AST 소스 묶음으로 먼저 넣는다. 함수별500토큰 제한은 제거하고 전체 소스1600토큰 상한을 유지한다. 묶음과 보호 소스가 상한을 넘으면 부분 코드 대신 미해결로 거부한다. 남은 기존 O 소스는 결정된 정렬 순서로 예산에 들어갈 때만 유지한다. 기존 일반 추출기나 제품 기본 정책을 변경하지 않은 별도 실험 구현이다.

이번 J04에서 필수 묶음은 `_inject_setup_teardown_fixtures`, `FixtureManager.parsefactories`, `_showfixtures_main`이다. 두 방향 모두 세 함수 전체가1346토큰이며, 기존의 관련 없는 다른 O 단위는 예산에 들어가지 않아 제거됐다. whole-source 우선순위와 소스 교체가 함께 바뀐 비교이므로 대입문3개만의 단독 인과 효과는 측정하지 않는다. 자동 def-use 슬라이서나 자동 route 탐색을 구현한 것도 아니다.

| source 입력 | 정상 방향 | 회귀 방향 |
|---|---:|---:|
| 기존 O | 1458토큰 | 1456토큰 |
| 개선 C | 1346토큰 | 1346토큰 |

C에는 `name = marker.name`, `FixtureDef(argname=name, ...)`, `faclist.append/insert(fixture_def)`, `available.append(...fixturedef.argname...)`가 완전하게 보인다. 원래의 필터와 class fixture 부착도 유지된다. 모자랐던 핵심 세 종류의 문장은 토큰 총량 부족이 아니라 함수별 자르기 때문에 빠졌던 것이다.

## 비교 실험

이미 본 pytest-8399 한 PR × 정상/회귀 두 방향 × O/C ×3회=12건. O의 프롬프트는 직전 실험과 동일하지만 응답은 모두 새로 호출했다. C는 같은 공통 DIFF/공개 계약/정적 관측, 같은 이전 native route를 사용했다. gpt-5.6-sol low, workers4, retry0, 도구 호출0, seed 미통제. 각 source1600/obs400 상한은 전체 프롬프트 길이 상한이 아니다.

| 지표 | O | C |
|---|---:|---:|
| 근거 있는 회귀 검출 | 0/3 | 0/3 |
| 정상 방향 오탐 | 0/3 | 0/3 |
| 정상·회귀 모두 근거로 맞힌 쌍 | 0/3 | 0/3 |
| 근거 있는 전체 방향 정답 | 0/6 | 0/6 |
| 근거 부족으로 제외한 회귀 issue | 3 | 3 |
| 근거 부족으로 제외한 정상 확정 | 3 | 3 |
| insufficient_evidence 응답 | 0/6 | 0/6 |
| 호출·schema 유효 | 6/6 | 6/6 |

두 조건 모두 정상에서 no_supported_issue, 회귀에서 reported_issues를 반환해 label 방향은6/6 맞았다. 그러나 근거와 인과 주장을 함께 확인하는 고정 기준에서는 성공으로 인정하지 않았다. 표면적인 label 일치와 근거 있는 판정 성능을 구분해야 한다. 이미 본 단일 PR의 반복이므로 통계 우월성이나 새 사례 일반화를 주장하지 않는다. 작성자 비블라인드 채점이다.

## 왜 아직 성공이 아닌가

O는 이전과 동일하게 이름 대입/등록/출력 목록 연결이 생략되어 있다. C는 이 세 공백을 해결했지만 실제 fixture 이름을 전달하는 다음 부분이 공급되지 않았다.

1. `_make_xunit_fixture`의 전체 데코레이터/반환 문맥과 unittest 수집기에서 injection 및 parsefactories를 호출하는 연결.
2. `fixture` 구현이 name을 `FixtureFunctionMarker`로 넘기고, `FixtureFunctionMarker.__call__`이 함수의 `_pytestfixturefunction` 속성에 marker를 저장하며, `getfixturemarker`가 이를 읽는 연결.
3. `FixtureDef.__init__`이 받은 `argname`을 출력에서 읽는 `self.argname`에 저장하는 연결.

응답들은 이 연결을 실제로 실행된 것으로 단정했다. 완전한 함수3개만으로 완전한 경로가 자동 입증되지는 않는다. 전체 framework 소스를 필수로 요구한 것이 아니라, 이번 판정의 이름 전달에 직접 필요한 사용자 정의 대입/호출 연결이 빠져 있다는 판정이다. 이전 실행 witness는 label 검증용으로 재사용했고 reviewer 근거 공백을 메우는 데 쓰지 않았다. 호출 실패가 아닌 증거 경로 누락이다.

## 검증과 후속 입력

12건 모두 transport/schema 유효, model tool 호출0, 누락0. 동결 파일/hash, O 프롬프트 identity, O/C 관측 및 native 경로 동일성, 소스·관측 상한, 공식 workspace hash, 필수 연결 문장 존재, 전수 검토를 확인했다. 1346토큰의 완전 묶음에 상한1345를 주면 부분 소스 없이 거부하는 경계 동작도 확인했다. issue 파일/행 존재를 확인했으며 localization accuracy를 별도로 측정하지 않았다.

이번 새 LSP 조회0, 새 runtime witness 실행0이다. 기존 common definition24회와 output definition6회의 trace를 두 조건이 함께 재사용했다. AST 전체 단위를 선택하는 추출 개선의 비교이지 native navigation 개선의 실험이 아니다.

`missing_route_candidates.json`에는 사후 공식 fixed workspace를 읽어 위 연결의 선언 위치를 기록했다. 모델에 제공하지 않은 AST 후보이며 새 native 조회나 runtime 인증으로 취급하지 않는다. `fixture`의1236/1254 선언은 overload이고 실제 구현은1271부터이므로 후속 resolver는 실제 구현을 구별해야 한다.

다음 단계는 세 큰 함수를 전부 넣는 방식에서, 이름에 필요한 문장을 원자 단위로 보존하면서 위 marker·저장·수집 연결까지 묶는 방식으로 넘어가는 것이다. 필요한 문장의 전체 대입/호출 인자/반환, enclosing 조건과 분기, 값 정의 연결을 함께 유지하고 상한 초과 시 미해결로 처리해야 한다. 새 native 경로를 추가하는 단계는 이번 extraction 비교와 분리해 동결하고 비교한다. 이 개발 검증 후 자동 선택 규칙을 고정하고 새 PR 평가를 진행할 수 있다.

입력/소스/원응답/판정/검증을 같은 디렉터리에 보존했다. `freeze.json`은 모델 호출 전 파일, `result_seal.json`은 최종 결과 파일을 봉인한다.
