# 같은 PR에서 이전 자동 A0와 예약 A1 직접 비교

A1은 같은 두 PR에서 A0보다 근거 있는 탐지가 많았다(2/6 → 4/6). 양방향 성공은 0/6 → 2/6이다. 제한된 개선 신호이며 예약 정책의 인과 효과나 통계적 우월성을 확정하지 않는다. 기존 B를 유지하고 A1을 후속 검증 후보로 둔다.

## 비교 결과

| 지표 | 이전 자동 A0 | 예약 A1 |
|---|---:|---:|
| 근거 있는 unsafe 탐지 | 2/6 | 4/6 |
| 근거 있는 safe 판단 | 1/6 | 3/6 |
| 양방향 모두 성공 | 0/6 | 2/6 |
| 근거 있는 개별 판단 | 3/12 | 7/12 |
| safe 오탐 | 0/6 | 0/6 |
| insufficient_evidence | 8/12 | 5/12 |
| 근거 부족 issue | 0 | 0 |
| 원시 정답 방향 일치 | 4/12 | 7/12 |
| 유효 응답 | 12/12 | 12/12 |

원시 방향 일치는 reported_issues/unsafe 또는 no_supported_issue/safe이며 근거 충족과 구분한다. 보류를 정답으로 고치지 않았고 실패·보류를 분모에 유지했다. 작성자가 정답을 아는 상태로 채점했다. PR2개의3반복이며 독립PR6개가 아니다.

| 사례 | A0 탐지 / 정상 / 양방향 | A1 탐지 / 정상 / 양방향 |
|---|---:|---:|
| V01: ndarray mock patch 수집 | 2/3 · 1/3 · 0/3 | 2/3 · 3/3 · 2/3 |
| V02: directory symlink 수집 | 0/3 · 0/3 · 0/3 | 2/3 · 0/3 · 0/3 |

V01 결함 탐지는 같다. V02에서 A0 세 응답 모두 보류했으며 A1은 두 응답에서 계약 위반을 보고했다. V02 정상 방향은 두 정책 모두 보류해 해결되지 않았다.

## 신규 실행과 재사용

이번에는 A0에만 새 모델12회를 호출했다. A1의 이전12회응답·prompt·packet·정적관측·채점을 그대로 재사용했다. 논리리뷰24개, 비교에 포함된 고유 물리호출24개, 이번 신규물리호출12개이다. 복사나재사용을 새 호출로 세지 않았다. A1은 재채점하지 않았으며 원본annotations를 A0호출전에동결했다. A1의12개 점수필드가 이전결과와모두같은지 추가대조했다.

A0의 실제 native definition/references는 4workspace×12=48회를 새 실행했다. A1의 이전48회 trace/audit는 재사용하며 이번신규조회에 포함하지 않는다. 새 witness실행은0회이고 이전검증된8개의original/fixed결과를 재사용했다. 이전run의13회시도·4회의존성실패·환경보정기록은 그대로 보존한다.

A0는 lsp_auto_routes_20261002/policy.py를 그대로 import했다. A1는 lsp_reserved_routes_20261002의 frozen policy/결과이다. 두 정책 모두 이PR들의 최초선정전에seal했고 이후수정0이다. 새PR일반화평가가 아니라 이미평가한pytest5631/7982의 정책직접대조다. 공개문제/공식patch를보고사람이선정한 두표본이며 datasetblind/random/repositoryholdout이 아니다.

동일한 공개고정입력·diff·schema·productioncorpus·Pyright1.1.414·최대source2600·관측400·실제nativequery12·depth3·3반복·gpt-5.6-sol low를 사용했다. A0새배치는workers4/tools0/retry0/seed미통제다. A1은이전배치이므로 실행시각/provider상태와seed를동시에통제하지못했다. 반복번호는비교단위일뿐pairedrandomseed가아니다.

## 어떤 코드가 달랐는가

| 입력 | A0 source 토큰 / 최대 깊이 / 선언 수 | A1 source 토큰 / 최대 깊이 / 선언 수 |
|---|---:|---:|
| V01_a | 1135 / 1 / 5 | 1247 / 2 / 7 |
| V01_b | 1151 / 1 / 5 | 1263 / 2 / 7 |
| V02_a | 2372 / 1 / 4 | 2556 / 2 / 7 |
| V02_b | 2366 / 1 / 4 | 2550 / 2 / 7 |

source렌더러는같으나실제길이·선택함수·관측내용은다르다. 예약외에builtin/literal/반복syntacticcallee생략과반환식우선·후속reference순서도함께바꿨으므로 정책묶음비교이다. 예약하나만의ablation은아니다.

V01은양쪽모두 getfixtureinfo → getfuncargnames → num_mock_patch_args 연결을확보했다. A1은추가로FuncFixtureInfo/Failed를가져왔지만, 그추가선언이리뷰향상의원인이라는증거는없다. V02도양쪽모두 visit → Session.collect/Package.collect를확보했다. A0는Config.getoption, A1은parts/path_matches_patterns/collect_one_node/check_interactive_exception 등을추가했다. 어느쪽도Session._recurse 구현을공급하지못했다.

A1은더깊게탐색하고corpus대상을반환한조회도많았지만, A0도핵심인과연결을이미가졌다. 따라서A1성과차이를핵심경로복구효과로단정할수없다. 추가컨텍스트와모델의판단보류경향·호출배치변동이함께영향을줬을가능성이있다. 이설명은관측결과에서의추론이다.

## 근거 판정과 경계 사례

A0에도이전A1과같은기준을적용했다. 공개unittest.mock의표준metadata,명시한2-element ndarray truth semantics,DirEntry의symlink동작은활용가능하다. 사용자정의collection연결은제공코드로확인한다. 계약위반을보이는좁은인과가있으면정확한exit관측이나전체framework실행인증은필요하지않다. 숨긴witness로코드공백을메우지않는다.

A0의V01unsafe r2/r3는공급된getfixtureinfo/getfuncargnames호출연결과ndarray membership예외를설명해탐지를인정했다. safe r1은identity비교의count0과zero-argument test를연결해정상판정으로인정했다.

safe r3는no_supported_issue를제출했지만ledger의comparison을unknown으로두고, helper진입·patching값·공개수집연결을미해결로남긴채로컬identity표현만확인했다. 주지표에서는정상판단성공으로세지않았다. 단순히독립관측한exitcode가없다는이유는아니다. A1의safe r2는같은exit관측한계를밝히면서도getfuncargnames/count0/zero-argument연결과changed비교를확정했으며그동결점수를유지했다. A0 r3의정답방향과보류서술도그대로보존했고status를사후변환하지않았다.

이경계판정을완화해A0 r3의로컬수정판정을정상성공으로허용하면A0개별판단은4/12,양방향은1/6이다. 탐지2/6은변하지않고A1은7/12·2/6·4/6으로같다. 이민감도는주지표를바꾼것이아니며authorgrading의영향을보여준다. 독립블라인드평가가필요하다.

A0의V02unsafe 세응답은symlink재귀차단을일부정확히관찰했지만전부insufficient_evidence였다. 이를사후reported_issues로바꾸지않았다. A1두issue는false판정이recurse호출전에short-circuit하고Session.collect가non-file entry를건너뛰어targettest를수집하지못한다는계약위반을보고했다. exactexit는unknown이어도그좁은위반을인정하는기준은동일하다.

## 검증 및 결론

기존두run seal,동결정책/input,공급source전체·ownerheaders·실제rendering,workspacehash,동일sourcecorpus/caps,48개의실제semanticrequest,24logical/12new/12reused 호출수,transport/schema,tools0,issuefile/line존재,A1prompt/annotations/점수보존을모두검증했다. 위치존재는localization accuracy측정이아니다. 이전run파일을수정하지않았다.

이번신규A0 inputtokens143071(cached99840),output9349. 재사용A1 input144694(cached91520),output9112는이번과금/실행으로세지않는다. 비용우월성은산출하지않았다.

A1의개선신호는남았으나2PR와서로다른배치·작은반복으로교체를결정할수없다. 기존B유지,A1후속검증후보로둔다. 다음작업은일반callback 인수의definition을따라Session._recurse 같은수집허용조건을공급하는개선이다. 이미본사례에서그경로를개발검증한뒤다시seal하고추가미사용PR로평가한다. 현재PR을개발에쓰면다음일반화평가에서는제외한다.
