# Fixture binding v1: 소속 클래스 문맥 누락으로 성공 제외

2026-10-02. 이미 본 pytest-8399의 C/M/D 비교18건은 호출/schema/동결/hash 검증을 통과했다. C1346, M2530, D2542토큰이며 M/D 길이는0.5% 이내로 맞췄다. 새 native definition24회, runtime witness 재실행0이다. 기존1600 대신 source2600 공통 상한을 모델 호출 전에 정했다.

D에는 marker 저장·조회와 생성자 argname 대입이 들어갔지만 file-group rendering에서 생성자/메서드의 소속 클래스 선언 FixtureDef, FixtureManager, UnitTestCase를 생략했다. 준비 AST/native metadata의 소속 정보는 reviewer 입력이 아니다. 이 정보를 사후 보충해서 정답으로 세면 안 되므로 잠정 D 검출3/3·쌍3/3 판정을 취소했다. 최종 grounded 검출과쌍은 C/M/D 모두0/3, 정상오탐0/3이다. 세 조건의 label방향은각6/6 일치하지만 user-defined 연결을 입증한 성공으로 인정하지 않는다.

raw 입력/응답을 변경하지 않고 보존했다. annotations/adjudication에 취소 사유를 기록했고 results_all을 최종 기준으로 재산출했다. source_context_validation.json에 공급 문맥 결함을 별도로 기록했다. validation_results.json의 all_checks_pass는 기존 운영·hash·문장 span 검증이며 소속 문맥까지 입증하는 검사였던 것은 아니다.

교정 실험은 sibling 디렉터리 runs/lsp_fixture_binding_v2_20261002에서 모든 소속 class header를 보존한 입력을 새로 동결하고18건을 모두 새로 호출한다. 이 결과는 v2 성공 입력/응답에 합산하지 않는다. 이번 작업의 비용/조회량에는 포함한다. 기존 응답에 코드/근거를 사후 추가하지 않는다.
