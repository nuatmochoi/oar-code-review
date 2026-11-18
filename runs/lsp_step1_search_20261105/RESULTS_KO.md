# 단계1: 검색 입력 분리·복수 후보·페이지 재개

같은12개 개발PR×3반복·같은 실제 초기응답36개. 기존 결과는 읽기 전용 참조. 각조건 공통400k token/최대4모델호출/source12k/native48·180초/file256. 실제사용량은 같지 않다. 비맹검AI·인간검토0. 기존 점수는 교체하지 않는다.

| 조건 | 원인 F1 | 유효 보고 F1 | 원인 정탐 | 재현율 | 확인 오탐 | 의미미해결 / 미판독 |
|---|---:|---:|---:|---:|---:|---:|
| BASE | 16.84% | 14.04% | 24 | 9.64% | 0 | 2 / 3 |
| SEARCH | 18.57% | 14.29% | 26 | 10.44% | 0 | 0 / 0 |

직전단계 대비 정탐 유지22 / 소실2 / 추가4. 판독실패는오탐으로바꾸지않는다. 원인F1은정답표target의원인일치, 유효보고F1은이에위치·근거관련성·인용범위·출력정상까지요구한다. F1=2TP/(전체보고+정답target), 정답표밖참보고를확인오탐으로세지않는다.

신규 물리 API 150호출 / 알려진 token 7,950,719 / usage미상 0. 예산위반 0.

paired bootstrap(저장소단위10,000회; 확인된TP에조건부):
{"contrast": "SEARCH minus BASE", "metric": "cause_f1", "observed_delta": 0.017293233082706777, "percentile_95_ci": [-0.013521210946436315, 0.047240551120440025], "usable_draws": 10000, "cluster": "repo", "clusters": 12, "draws": 10000, "seed": 20261103, "interpretation": "Paired interval for observed confirmed-TP scores; with unknown annotations this is not a bound on the unknown true effect."}
{"contrast": "SEARCH minus BASE", "metric": "valid_report_f1", "observed_delta": 0.002506265664160401, "percentile_95_ci": [-0.04514771984551397, 0.05980099252218302], "usable_draws": 10000, "cluster": "repo", "clusters": 12, "draws": 10000, "seed": 20261103, "interpretation": "Paired interval for observed confirmed-TP scores; with unknown annotations this is not a bound on the unknown true effect."}

단계별 차이는 해당 개발조건에서의 추가효과이며 독립 확증/일반화 증거가 아니다. 본문확보·기계적유효성·의미관련성·최종정답을구분한다.
