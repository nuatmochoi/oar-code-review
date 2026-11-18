# Fixture 이름 전달 경로 보충: 교정본 결과

2026-10-02. 클래스 소속 문맥을 포함한 이름 전달 경로 입력을 새로 동결하고18건을 비교했다. 좁은 generated-fixture 이름/출력 필터 계약에 대한 근거 있는 회귀 검출은 기존 C0/3, 길이 대조군 M0/3, 경로 보충 D3/3이었다. 양방향 정답 쌍도0/3,0/3,3/3이다. 그러나 모든 조건의 답 방향은 이미 label과6/6 일치했으므로, 예측 정확도 향상을 확인한 결과는 아니다. 필요한 코드 연결을 공급했을 때 기존 답을 뒷받침할 근거가 확보되는지 확인한 개발 결과다. 기본 B를 유지한다.

## 구현과 비교 조건

- C: 직전 완전한 출력 함수 묶음의 소스1346토큰과 정적 관측을 그대로 쓰고 모델 응답은 새로 생성했다.
- M: C source lines를 보존하면서 D의 이름 전달에 필요한 선언 영역을 제외한 head production AST 단위를 public scope 점수 순으로 추가했다. 모든 추가 단위는 whole AST unit이며, source2580토큰이다. 모든 filler가 모든 동작에 무관하다고 인증한 것은 아니다.
- D: 생성 이름 → marker 생성·저장·조회 → FixtureDef.argname 저장 → fixture manager 등록 → available 목록 → underscore 출력 필터의 대입·호출·반환 문장을 제공했다. 메서드와 생성자의 소속 클래스 선언도 보존했다. source2590토큰으로 M과 차이는10토큰, 약0.39%다.

D source는 사람이 정한 경로를 AST로 선택한 문장 묶음이다. 모든 선택 statement/signature는 전체 span을 보존하고, 생략한 코드는 명시적으로 unknown으로 표시한다. 자동 def-use/route 탐색이나 전체 runtime graph 완성을 구현한 것은 아니다. 파일별 묶음으로 렌더링한 D와 기존 unit 방식의 C/M은 source framing도 달라, 길이 일치만으로 모든 confound를 제거한 것은 아니다.

필요한 완전한 선택 문장 묶음은 기존1600토큰을 넘었다. 공통 source 상한을 모델 호출 전2600, 정적 관측 상한은400으로 정했다. 전체 프롬프트 토큰 상한이 아니다. 따라서 이 결과를 기존1600 예산에서의 개선으로 제시할 수 없다. M과 D의 실제 source 길이를 맞춘 비교가 주 비교이며 C는 작은 기존 입력의 보조 비교다.

## 실험

이미 본 pytest-dev__pytest-8399 한 PR × 정상/회귀 두 방향 ×C/M/D ×3회=18 logical/18 fresh physical calls. gpt-5.6-sol low, workers4, tools0, retry0, seed 미통제. 같은 반복 번호는 같은 난수 seed가 아니다. 정적 관측은 세 조건에서 동일하다. 이전 모델 응답 재사용0, 이전에 검증한 label/runtime witness는 재사용했으며 새 runtime 실행은0이다.

| 지표 | C | M | D |
|---|---:|---:|---:|
| 근거 있는 회귀 검출 | 0/3 | 0/3 | 3/3 |
| 정상 방향 오탐 | 0/3 | 0/3 | 0/3 |
| 정상·회귀 모두 근거로 맞힌 쌍 | 0/3 | 0/3 | 3/3 |
| 근거 있는 전체 방향 정답 | 0/6 | 0/6 | 6/6 |
| 근거 부족으로 제외한 회귀 issue | 3 | 3 | 0 |
| 근거 부족으로 제외한 정상 확정 | 3 | 3 | 0 |
| insufficient_evidence 응답 | 0/6 | 0/6 | 0/6 |
| 호출·schema 유효 | 6/6 | 6/6 | 6/6 |
| raw 답 방향과 label 일치 | 6/6 | 6/6 | 6/6 |

M 대비 D의 양방향 성공 증가가 반복1/2/3 모두에서 나타났고 손실 쌍은 없었다. 단일 이미 본 PR의 반복3개이지 독립3PR이 아니다. 통계 우월성, 새 사례/저장소 일반화, 모델의 보류/신뢰도 보정 개선을 주장하지 않는다. 작성자가 비블라인드로 전수 채점했다.

## 어떤 연결을 확보했는가

D 입력은 public unittest class를 인식하는 factory와 UnitTestCase.collect의 injection/parse 호출을 보여준다. `_make_xunit_fixture`는 주어진 setup_name으로 explicit name을 만들고 decorated fixture를 반환한다. public `pytest.fixture` export, 실제 fixture 구현, FixtureFunctionMarker의 name 필드와 함수 속성 `_pytestfixturefunction` 저장, getfixturemarker의 동일 속성 조회를 제공했다. FixtureDef의 생성자 소속과 `self.argname = argname`, parsefactories의 marker.name 선택/목록 append·insert, available 목록의 fixturedef.argname 전달과 filter를 함께 보여준다.

C/M에는 marker 생성·저장·조회와 FixtureDef.argname 저장의 구현이 없다. 응답들은 그 연결을 추정했으므로 같은 기준에서 성공으로 인정하지 않았다. D에서는 소속 클래스와 해당 사용자 정의 값 전달이 공급되어, 주어진 공개 fixture 예제의 leading-underscore 변화와 필터 효과라는 좁은 계약에 한정해 인정했다. 숨긴 witness와 native 준비 metadata로 reviewer 소스 공백을 메우지 않았다.

전체 inherited from_parent/PyobjMixin object resolution, framework hook/bootstrap dispatch, 외부 attr 라이브러리 내부 구현, 다른 fixture 입력, 실제 이번 실행의 출력까지 인증한 결과는 아니다. 생략된 코드는 unknown이며 이 점을 전체 runtime 인증으로 확장하지 않는다.

## 실제 LSP 조회와 workspace 개선

각 방향에서 선언11개와 실제 `pytest.fixture` decorator reference1개, 총24 native definition 조회를 했다. declaration target 확인 후 사람/AST가 문장을 선택했으며, 자동 route 발견이나 pure LSP 인과 효과 실험은 아니다. 실제 decorator 위치의 조회가 `_pytest/fixtures.py::fixture`를 반환하는 것을 확인했다. overload 선언 대신 실제 fixture 구현을 선택했다.

이전 staging의 source 수집 규칙은 `_pytest`와 pytest.py만 포함해, 해당 snapshot의 public pytest package를 빠뜨리고 있었다. 이번에는 공식 src/pytest의 production Python 파일까지 별도 workspace에 포함하고 `from _pytest.fixtures import fixture` export를 공급했다. 원래 공식 checkout, 이전 workspace, production 코드를 수정하지 않았다. 실제 snapshot의 source를 복사했고 hash를 검증했다.

## 중간 결함과 교정 이력

첫 binding 입력(v1)은 이름 저장·조회 문장을 포함했지만 file-group rendering에서 FixtureDef, FixtureManager, UnitTestCase의 소속 class header를 빠뜨렸다. 모델18건과 native 조회24회를 완료한 뒤 최종 공급 근거 검토에서 이를 발견했다. 준비 AST/native metadata만으로 reviewer가 보지 못한 소속을 보충할 수 없으므로 잠정 D3/3 판정을 취소하고 v1 최종 grounded 성과를0으로 처리했다. 원입력/응답과 판정을 sibling `runs/lsp_fixture_binding_20261002`에 봉인해 보존했다.

교정본은 class header를 추가한 새 입력을 동결하고 모든18건을 새로 호출했다. v1 응답에 source를 사후 추가하거나 응답을 고치지 않았다. 따라서 위 최종 표는 교정본18건만 포함한다. 이번 작업의 총 신규 모델 호출은36건, native definition 조회는48회, runtime witness 재실행0이다.

교정본 freeze 포장 중 이전 result seal 저장 경로 오류로 dependency 파일이 아직 없어 한번 실패했다. 앞서 만든 source/input/native trace는 유지하고 seal 저장을 복구한 뒤 freeze 포장만 재개했다. 이 포장 복구는 입력 재생성, 추가 native 조회, 추가 모델 호출을 하지 않았다. 이 실패도 preparation_packaging_failure.json에 기록했다.

## 검증과 후속 작업

호출/schema, 모든 동결 파일, 공식 workspace hash, 기존 C 프롬프트 identity, 동일 정적 관측, source/obs 상한, M의 C 소스 보존, filler의 binding 선언 제외, M/D 길이±2% 조건, 선택 statement 전체 span, 소속 class header, native target과 decorator export, 입력 identity, 전수 검토, issue 파일/행 존재를 검증했다. 모두 통과했다. 별도 localization accuracy는 측정하지 않았다. v1 result seal도 다시 검증했다.

이번 구현을 기본 B에 바로 적용하지 않는다. 다음은 소속 클래스·대입·호출 인자·반환·등록을 포함한 필요한 연결을 자동 선택하고, 누락된 연결이나 budget 초과를 미해결로 남기는 규칙을 만드는 일이다. 현재2600에 들어간 이름 전달 묶음이1600 안에 들어가지 않는 이유를 별도로 다뤄야 한다. 규칙과 예산을 고정한 뒤 사용하지 않은 PR에서 이름 전달/직렬화/보고 위치 등 서로 다른 경로를 평가해야 한다. 현재 성공은 사람이 필요한 경로를 정한 단일 seen-case 개발 검증에 한정된다.

`PROTOCOL_KO.md`, `GRADING_KO.md`, `freeze.json`, `preparation_validation.json`, `audit_all.json`, `results_all.json`, `annotations_all.json`, `adjudication_notes.json`, `analysis_details.json`, `validation_results.json`, `inputs/`, `packets/`, `lsp_traces/`, `raw/`, `workspace_manifests/`에 근거를 보존했다. `result_seal.json`으로 최종 파일을 봉인한다.
