# 콜백 인수 LSP 연결: 개발 검증과 새 PR 비교

콜백 인수 연결 A2를 구현했지만, 새 SymPy PR 두 개에서 리뷰 성능 개선은 확인하지 못했다. A1/A2 모두 24개 응답 전체가 판단을 보류했다. 기존 B를 유지하며 A2를 채택하지 않는다. 다음 작업은 콜백을 더 넓히기 전에 삭제 전용 패치의 변경 함수 진입점과 연결에 필요한 소스 예산을 바로잡는 것이다.

## 결과

| 지표 | 기존 예약 A1 | 콜백 A2 |
|---|---:|---:|
| 근거 있는 결함 탐지 | 0/6 | 0/6 |
| 근거 있는 정상 판단 | 0/6 | 0/6 |
| 양방향 성공 | 0/6 | 0/6 |
| insufficient_evidence | 12/12 | 12/12 |
| 안전 방향 오탐 | 0/6 | 0/6 |
| 호출·schema 유효 | 12/12 | 12/12 |

오탐 0은 정상 판단 성능을 뜻하지 않는다. 모든 응답이 보류였으므로 개별 판단과 원시 정답 방향 일치도 양쪽 0/12이다. 모델이 조건부로 로컬 문제를 설명한 응답도 보류를 탐지 성공으로 바꾸지 않았다. 작성자 전수 비블라인드 채점이며 PR2개의3반복을 독립PR6개로 해석하지 않는다.

## 구현 및 이미 본 사례의 검증

실제 native definition 또는 references가 반환한 정확한 호출 위치에서, 대상 함수의 매개변수와 positional/keyword 인수를 매핑한다. 매개변수가 함수 본문에서 직접 호출되거나 Callable annotation을 가지면 콜백 후보로 분류한다. Name/Attribute 인수에 추가 native definition을 요청하고 전체 함수·소속 class header·관련 import를 보존한다. 임의 함수명으로 AST target을 찾아 native 성공처럼 처리하지 않는다.

*args의 positional 매핑, lambda 인수, 미제공 caller는 추측하지 않는다. bound self/cls와 staticmethod는 일반 Python 문법 규칙으로 처리한다. 콜백 후보 판정에는 AST가 쓰이지만 정의 target은 실제 서버 반환 위치로만 확정한다. 정적 연결이며 runtime 인수 전달·동적 호출 그래프 인증은 아니다.

기존 pytest symlink/bytes/fixture 사례에서 두 차례 개발 검증했다. 첫 라운드 후 불확실한 *args 매핑을 제외했고 원본 기록을 보존했다. 실제 native 조회 총72회, 모델0회이다. symlink 사례에서는 visit의 recurse 인수로 전달된 self._recurse를 따라 Session._recurse와 Package._recurse를 모두 실제 native definition으로 확보했다. positional 및 keyword 형태를 확인했고 소스2511토큰·조회12회 상한을 지켰다. 설정·훅의 후속 연결은 아직 미해결이며 전체 경로 완성은 주장하지 않는다. 기존 bytes 사례는1138토큰, fixture 사례는2508토큰으로 cap/owner/함수 전체 보존 검증을 통과했지만 fixture 큰 package 차단4회는 남았다.

이 상태에서 algorithm_seal.json을 만들고 새 PR 선정에 들어갔다. 이후 A1/A2 정책 변경은0회이다.

## 새 PR와 공개 재현

미사용 SymPy17139/23262를 공개 문제·공식 patch·짧은 재현 및 콜백 관련 경로를 보고 사람이 선정했다. 이전 selected/selected_initial ID와 겹치지 않지만 SymPy 저장소는 이전 실험에서 본 대상이다. 무작위·dataset blind·repository holdout은 아니다.

- V01 / 17139: x=Symbol("x"), expr=cos(x)**I에 simplify(expr)를 호출한다. 동일 표현을 반환하고 complex I 비교 TypeError가 없어야 한다. 원본은 TypeError("Invalid comparison of complex I"), 수정본은 cos(x)**I 정상 반환을 각2회 확인했다. a=safe 정방향, b=unsafe 역방향이다.
- V02 / 23262: f=lambdify([], (1,), modules="math"); f()가 Python tuple (1,)을 반환해야 한다. 원본은 scalar int1과 생성 코드 return (1), 수정본은 tuple(1,)과 return (1,)을 각2회 확인했다. a=unsafe 역방향, b=safe 정방향이다.

Witness는 새 subprocess8회 모두 유효했고 실패·추가 의존성 설치는 없었다. Python3.11에서 collections/inspect 호환 alias를 사용하고 공식 snapshot source를 먼저 import했다. 생산 source는 공식 patch 외에 수정하지 않았다. witness 결과·labels·test_patch는 reviewer/selector 입력에 공급하지 않았으며 코드 공백을 채우는 데 사용하지 않았다.

## 같은 상한에서 드러난 공백

각 arm은 최대 source2600토큰, 관측400토큰, 실제 native semantic 조회12회, 깊이3이다. 같은 전체 생산 Python package corpus와 full workspace, Pyright1.1.414, 같은 renderer를 사용했다. 실제 source 길이·내용·조회 순서는 다르다.

| 입력 | A1 source / 조회 | A2 source / 조회 | A2 콜백 조회 / target 확보 조회 |
|---|---:|---:|---:|
| V01_a safe | 1514 / 12 | 2462 / 12 | 7 / 6 |
| V01_b unsafe | 0 / 0 | 0 / 0 | 0 / 0 |
| V02_a unsafe | 2492 / 12 | 2518 / 12 | 3 / 1 |
| V02_b safe | 2495 / 12 | 2521 / 12 | 3 / 1 |

이번 비교의 실제 semantic 조회는 A1 36+A2 36=72회이다. 개발72회까지 이번 작업의 합계는144회이다. server initialize/notification은 semantic 조회로 세지 않았다. 실제 trace request와 audit jumps 수를 대조했다.

V01_b는 삭제만 있는 diff다. 기존 edited_lines는 head의 추가 행만 변경 함수 root에 사용하므로 두 정책 모두 roots=[]가 됐다. 코드 소스0토큰과 semantic 조회0회이며 diff·공개 계약만 제공됐다. 양쪽 prompt가 정확히 같은 이 조건의 6개 리뷰도 새로 호출했고 분모에 유지했다. source cap 초과나 호출 실패가 아니라 공통 진입점 탐지 실패다. 모델 호출 전에 발견했지만 seal 이후 정책을 고쳐 미사용 평가처럼 보고하지 않았다.

V01_a에서 A2는 콜백 g에 전달된 sin/cos/cot/tan/sec/csc 클래스 패키지를 확보했다. 고정 입력 I에서는 관련 없는 가지도 포함된다. 이 패키지들이 예산을 사용한 뒤 bottom_up 전체 package의 trial2695토큰이 cap을 넘어 두 번 차단됐다. A1에는 bottom_up이 포함됐다. 콜백 target 수가 많아진 것이 필요한 인과 연결의 완성을 뜻하지 않았다. simplify 공개 entrypoint의 실제 dispatch 연결도 두 조건 모두 확보하지 못했다.

V02는 _recursive_to_string과 _EvaluatorPrinter.doprint/lambdastr를 확보했으나 public lambdify의 생성 경로·_preprocess·실제 printer binding에 공백이 남았다. A2는 lambdarepr을 실제로 찾았지만 doprint 매개변수와 self._exprrepr 필드의 definition은 callable 선언이 아닌 parameter/assignment 위치로 반환돼 현재 mapper가 제외했다. lambda 및 nested 함수도 같은 선언 범위 제한을 받는다. CodePrinter.doprint 전체 package는 cap 때문에 차단됐다. 양쪽 reviewer는 (1)/(1,)의 로컬 문법 차이를 설명해도 실제 f() 반환의 연결을 확정하지 못해 보류했다.

## 준비 과정과 운영 검증

처음 전체 Corpus/BM25 구축이 SymPy 크기에서 오래 걸려, 두 정책이 사용하지 않는 어휘 검색 인덱스 생성을 runner에서 제거했다. 동일한 source hash와 전체 head source의 AST declaration mapping만 사용하도록 했다. A1/A2 정책·query·source 선택 규칙은 바꾸지 않았다. 첫 준비는 workspace manifest·native client·모델 호출 전에 중단했고, 일부 staged source와 labels/schema 기록은 보존했다. 재시작 시 기존 디렉터리/메타데이터와의 충돌2회를 해결했다. 이 과정의 semantic/model 호출은0회이며 preparation_optimization.json에 기록했다.

완성한 비교는 2PR×2방향×A1/A2×3반복=24개의 새 물리 모델 호출이다. gpt-5.6-sol low, workers4, tools0, retry0, seed 미통제, 응답 재사용0이다. 입력·기준 freeze 후 실행했다.

호출/schema24/24, 동결 정책·input·workspace hash, 동일 소스 범위, 실제 render, 전체 admitted function/owner, source/관측/query 상한, callback의 실제 callsite/visible argument, native 오류0 검증은 모두 통과했다. 이것은 운영·무결성 검증이며 경로 품질 검증이 아니다. 별도 coverage_quality.json의 all_changed_roots_available=False와 complete_runtime_paths=False를 함께 남겼다. 이전 run은 수정하지 않았다.

API inputtokens A1 139667(cached91520), A2 142742(cached74880); output A1 6640, A2 5849이다. 과금액·비용 개선은 산출하지 않았다.

## 다음 작업 순서

1. 삭제 전용 hunk를 head의 남은 위치에 매핑해 변경 함수 진입점을 확보한다. 전체 함수 삭제처럼 head에 대응 함수가 없으면 base evidence와 미해결 상태를 명시한다. 빈 context는 모델 호출 전에 탐지하고 준비 실패를 분모에 남기되 불필요한 API 호출은 줄인다.
2. parameter/field assignment/nested function에 대한 native 반환을 구분하고, 실제 별칭 공급 코드를 보존하며 추적한다. AST 이름으로 native 미해결을 덮지 않는다.
3. 콜백 class package를 늘리기 전에 필수 producer/callback/consumer 연결의 예산을 보존한다. 이미 본 사례에서 검증하고 다시 seal한 뒤 추가 미사용 PR에서 비교한다.

이번 두 SymPy PR은 이제 개발에 사용할 수 있지만 후속 일반화 평가에서는 제외한다. 현재 결과로 A2를 제품 리뷰 기본 정책에 반영하지 않는다.
