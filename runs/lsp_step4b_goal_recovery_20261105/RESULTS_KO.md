# 단계4b: 실패별 후속 조회·위임 함수 탐색 실행

같은12개 개발PR×3반복·같은 실제 초기응답36개. 기존 결과는 읽기 전용 참조. 각조건 공통400k token/최대4모델호출/source12k/native48·180초/file256. 실제사용량은 같지 않다. 비맹검AI·인간검토0. 기존 점수는 교체하지 않는다.

| 조건 | 원인 F1 | 유효 보고 F1 | 원인 정탐 | 재현율 | 확인 오탐 | 의미미해결 / 미판독 |
|---|---:|---:|---:|---:|---:|---:|
| 고정 본문+수정된 원인·조건 판단 | 17.86% | 15.00% | 25 | 10.04% | 0 | 1 / 2 |
| 동일 판단+실패별 실제 재조회·위임 조사 | 21.75% | 16.84% | 31 | 12.45% | 0 | 2 / 0 |

직전단계 대비 정탐 유지23 / 소실2 / 추가8. 판독실패는오탐으로바꾸지않는다. 원인F1은정답표target의원인일치, 유효보고F1은이에위치·근거관련성·인용범위·출력정상까지요구한다. F1=2TP/(전체보고+정답target), 정답표밖참보고를확인오탐으로세지않는다.

신규 물리 API 136호출 / 알려진 token 7,727,025 / usage미상 0. 예산위반 0.

paired bootstrap(저장소단위10,000회; 확인된TP에조건부):
{"contrast": "RECOVER minus BASE", "metric": "cause_f1", "observed_delta": 0.03897243107769424, "percentile_95_ci": [0.011092206091092236, 0.07771996504651348], "usable_draws": 10000, "cluster": "repo", "clusters": 12, "draws": 10000, "seed": 20261103, "interpretation": "Paired interval for observed confirmed-TP scores; with unknown annotations this is not a bound on the unknown true effect."}
{"contrast": "RECOVER minus BASE", "metric": "valid_report_f1", "observed_delta": 0.01842105263157895, "percentile_95_ci": [-0.008937984456379791, 0.05388469467377912], "usable_draws": 10000, "cluster": "repo", "clusters": 12, "draws": 10000, "seed": 20261103, "interpretation": "Paired interval for observed confirmed-TP scores; with unknown annotations this is not a bound on the unknown true effect."}

단계별 차이는 해당 개발조건에서의 추가효과이며 독립 확증/일반화 증거가 아니다. 본문확보·기계적유효성·의미관련성·최종정답을구분한다.


원인/조건 정보 기계적 감사: {'new_audits': 32, 'accepted': 28, 'errors': {'core_condition_must_be_extension_context_not_additional_requirement': 5, 'historical_current_role_conflict': 1}, 'semantic_truth_not_guaranteed': True}. 실제원인교정/정답여부와구분한다.