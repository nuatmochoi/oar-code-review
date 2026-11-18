# 단계3b: 같은 원인 객체에 검증·조건 역할 결속

같은12개 개발PR×3반복·같은 실제 초기응답36개. 기존 결과는 읽기 전용 참조. 각조건 공통400k token/최대4모델호출/source12k/native48·180초/file256. 실제사용량은 같지 않다. 비맹검AI·인간검토0. 기존 점수는 교체하지 않는다.

| 조건 | 원인 F1 | 유효 보고 F1 | 원인 정탐 | 재현율 | 확인 오탐 | 의미미해결 / 미판독 |
|---|---:|---:|---:|---:|---:|---:|
| 같은 수집 본문+기존 후보별 판단 | 20.64% | 14.95% | 29 | 11.65% | 0 | 1 / 0 |
| 이전 분리된 scope 출력·감사 | 19.22% | 15.66% | 27 | 10.84% | 0 | 1 / 2 |
| claim·condition 내부 검증 정보 결속 | 18.57% | 17.14% | 26 | 10.44% | 0 | 0 / 0 |

직전단계 대비 정탐 유지26 / 소실3 / 추가0. 판독실패는오탐으로바꾸지않는다. 원인F1은정답표target의원인일치, 유효보고F1은이에위치·근거관련성·인용범위·출력정상까지요구한다. F1=2TP/(전체보고+정답target), 정답표밖참보고를확인오탐으로세지않는다.

신규 물리 API 92호출 / 알려진 token 5,443,455 / usage미상 0. 예산위반 0.

paired bootstrap(저장소단위10,000회; 확인된TP에조건부):
{"contrast": "REPORT2 minus BASE", "metric": "cause_f1", "observed_delta": -0.020691408235892217, "percentile_95_ci": [-0.04600796491013884, -0.0010670814477711263], "usable_draws": 10000, "cluster": "repo", "clusters": 12, "draws": 10000, "seed": 20261103, "interpretation": "Paired interval for observed confirmed-TP scores; with unknown annotations this is not a bound on the unknown true effect."}
{"contrast": "REPORT2 minus BASE", "metric": "valid_report_f1", "observed_delta": 0.021962379257752934, "percentile_95_ci": [-0.039075945902811576, 0.12132475503129816], "usable_draws": 10000, "cluster": "repo", "clusters": 12, "draws": 10000, "seed": 20261103, "interpretation": "Paired interval for observed confirmed-TP scores; with unknown annotations this is not a bound on the unknown true effect."}
{"contrast": "REPORT2 minus PRIOR_REPORT", "metric": "cause_f1", "observed_delta": -0.0064565327910523496, "percentile_95_ci": [-0.019934584561239463, 0.002290621718877214], "usable_draws": 10000, "cluster": "repo", "clusters": 12, "draws": 10000, "seed": 20261103, "interpretation": "Paired interval for observed confirmed-TP scores; with unknown annotations this is not a bound on the unknown true effect."}
{"contrast": "REPORT2 minus PRIOR_REPORT", "metric": "valid_report_f1", "observed_delta": 0.014844941535333, "percentile_95_ci": [-0.02708093041138024, 0.08365143075063364], "usable_draws": 10000, "cluster": "repo", "clusters": 12, "draws": 10000, "seed": 20261103, "interpretation": "Paired interval for observed confirmed-TP scores; with unknown annotations this is not a bound on the unknown true effect."}
{"contrast": "PRIOR_REPORT minus BASE", "metric": "cause_f1", "observed_delta": -0.014234875444839867, "percentile_95_ci": [-0.04048667561398427, 0.0], "usable_draws": 10000, "cluster": "repo", "clusters": 12, "draws": 10000, "seed": 20261103, "interpretation": "Paired interval for observed confirmed-TP scores; with unknown annotations this is not a bound on the unknown true effect."}
{"contrast": "PRIOR_REPORT minus BASE", "metric": "valid_report_f1", "observed_delta": 0.007117437722419934, "percentile_95_ci": [-0.022474022133986648, 0.04037706993266389], "usable_draws": 10000, "cluster": "repo", "clusters": 12, "draws": 10000, "seed": 20261103, "interpretation": "Paired interval for observed confirmed-TP scores; with unknown annotations this is not a bound on the unknown true effect."}

단계별 차이는 해당 개발조건에서의 추가효과이며 독립 확증/일반화 증거가 아니다. 본문확보·기계적유효성·의미관련성·최종정답을구분한다.


원인/조건 정보 기계적 감사: {'new_audits': 36, 'accepted': 31, 'errors': {'core_condition_must_be_extension_context_not_additional_requirement': 3, 'refuted_scope_without_declared_refutation': 1, 'historical_current_role_conflict': 3}, 'prior_audits_accepted': 0, 'prior_audits_total': 36, 'semantic_truth_not_guaranteed': True}. 감사통과를의미정답/성능향상으로세지않는다. 과거판독/실패는보존하며이번공통패널점수로교체하지않는다.