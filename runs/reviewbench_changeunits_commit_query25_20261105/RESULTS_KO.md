# ReviewBench 25개 PR: 변경 단위 진단·커밋 문맥·질문 기반 재조회

**주 비교 A/B/C는 각각25개 PR 채점까지 완료했다. 커밋 문맥 추가와 재조회 모두 대조군 A를 넘지 못했다.** 별도 검토 범주 확대 S는 리뷰25개를 확보했지만 공식 판독이 예산 보호 장치에서 실패해 F1을 발행하지 않았다. 기존 결과를 고치지 않았다.

## 데이터·조건·예산

[ReviewBench](https://github.com/review-bench/ReviewBench)의 기존 공식25개 실행에서 보존한 공개 입력·초기 LSP 본문을 재사용해 새 로컬 비교 파이프라인과 동결된 공식 scorer를 실행했다. 같은 BASE/HEAD, gold TP352개, gpt-5.6-sol low, 같은 보고 schema를 사용했다. 개발용으로 이미 사용한25개이며 인간검토0, 완주한 판독1회 기준 결과다.

| 조건 | 실제 실행 | 효과 비교 |
|---|---|---|
| A · 기존 LSP 본문+계획 후 리뷰 | 전체 diff·기존 본문·변경 목록 → 계획1회 → 새 조회 없이 최종 리뷰1회 | 같은2호출 대조군 |
| B · HEAD 이전 커밋 문맥 추가 | A + 커밋 메시지·최종 변경 파일과 겹치는 동시 변경 경로 | B−A |
| C · 질문에 따른 실제 재조회 | B와 동일 계획 → 필요한 LSP·AST·본문·환경 조회 → 최종 리뷰 | C−B |

A/B/C는 PR당 input+output750k, 계획+최종2호출, source+commit12k, native64회/240초, file256, 논리 active3,600초 공통 상한이다. 전체 diff는 자르지 않았다. 실제 사용량은 상한보다 작다. C는 질문→조회 한 차례이며, 조회 실패를 수정해 다시 계획하는 여러 순환의 agent loop는 이번에 구현하지 않았다. SCIP/index0, runtime probe0, debate0이다. A/S와 B/C는 각각 계획 응답을 공유하고 논리 비용에는 각 조건에 포함한다.

## 주 결과

| 조건 | gold 탐지/352 | gold 재현율 | Grounded F1 | Augmented F1 | A 정탐 보존 | 오탐 보고 | 논리 리뷰+확인 판독 비용 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 과거 LSP6k·리뷰1회 (참고) | 37 | 10.51% | 18.90% | 23.59% | — | 2 | $12.6122 |
| A · 기존 LSP 본문+변경 목록·계획/최종2회 | 45 | 12.78% | 22.60% | 24.12% | 45/45 (100.00%) | 1 | $14.2655 |
| B · A + HEAD 이전 커밋 문맥 | 36 | 10.23% | 18.39% | 20.82% | 30/45 (66.67%) | 3 | $15.0716 |
| C · B + 질문에 따른 실제 LSP·본문 조회 | 42 | 11.93% | 21.00% | 23.24% | 31/45 (68.89%) | 6 | $15.9920 |

과거 참고행은 변경 목록·계획 호출이 추가된 새 A와 같은 조건이 아니다. Grounded F1은 공식 gold에 연결된 보고의 precision과 gold TP 재현율의 조화평균이다. Augmented F1은 novel TP도 반영한다. 과거 자체 원인 F1·유효 보고 F1과 같은 지표로 부르지 않는다. 오탐은 matched_fp+novel_fp 보고 수이고 정탐 보존은 gold 항목 집합 기준이다. 한 보고가 여러 gold를 포괄할 수 있다. 표 비용은 공유 계획을 각 조건에 포함한 논리 리뷰+확인된 물리 판독 비용이다. 미확인 호출 예약과 S 실패 비용은 아래 전역 합계에 별도 포함한다.

| 비교 | gold 보존 / 소실 / 추가 | Grounded F1 변화 | PR 부트스트랩 F1 차이95% 구간 |
|---|---|---:|---|
| B−A | 30 / 15 / 6 | -4.21pp | -7.88 ~ +0.00pp |
| C−B | 25 / 11 / 17 | +2.61pp | -1.84 ~ +6.99pp |
| C−A | 31 / 14 / 11 | -1.60pp | -5.48 ~ +3.48pp |

B는 A의 정탐15개를 잃고6개를 추가했다. C는 B보다17개를 추가했지만11개를 잃었다. C를 A와 직접 비교하면14개 소실·11개 추가다. 따라서 현재는 A를 유지하는 판단이 적절하다. 부트스트랩은 사후 PR 쌍 재표집10,000회(seed20261105)로 모델·판독 재실행 불확실성을 포함하지 않는다. 이 개발 결과로 일반적 우월성·무효성을 확정하지 않는다.

## 변경 단위·커밋 문맥 누락 진단

변경 단위는 텍스트 diff hunk와 metadata-only 파일이다. 전체 1,812개(텍스트1,771·metadata-only41)가 최종 전체 diff에 전달됐다. 추가 완전 HEAD 본문과 연결된 단위185개, 기존 native 질문 앵커가 있는 단위33개였다. 이는 모델의 실제 주의나 이해를 측정한 수치가 아니다.

계획용 별도 목록에는 whole-record 한도 안에서471/1,812개 단위를 제공했다. 추가 완전 본문 연결 비율의 PR 중앙값은 26.32%, native 질문 앵커 비율은 4.55%다. 전체 합계는1,015개 단위를 가진 한 대형 PR의 영향을 크게 받는다.

커밋은 frozen HEAD의 조상 중 merge-base에서 도달하지 않는 범위만 사용했다.313개 중170개 메시지·최종 변경 파일과 겹치는 경로를 제공했고18/25개 PR에서는 모든 커밋 기록이 들어갔다. 커밋별 원시 patch·중간 상태의 실행·최종 변경 밖의 파일 확장 효과는 검증하지 않았다. B−A는 커밋이 계획과 최종 판단 모두에 미친 결합 효과다. C−B는 같은 계획에서 조회 결과를 추가한 효과이며 A에 재조회만 추가한 독립 조건은 없다.

원래 미탐315개 중 위치가 diff에만 있던 항목180개, 추가 완전 본문에도 있던 항목93개, 둘 다에서 확인하지 못한 항목42개였다. 이는 위치 가시성 분류이며 충분한 원인 근거의 존재를 판정한 것은 아니다. private gold는 이 사후 진단·판독에만 사용하고 reviewer·계획·탐색에는 전달하지 않았다. 미탐 중 pre-existing23개는 S에서도 유지한 introduced/exacerbated 조건의 범위 문제와 별개다.

## 실제 조회와 최종 보고 연결

C의 질문225개를 실행했고 deferred0개였다. 계획 단계 거부23개는 파일이 snapshot에 없다는 오류로 원문을 보존했다. 질문82개에 selected evidence가 있었고166개에는 미해결 기록이 있었다. 두 집합은 겹친다.

새 물리 소스 행 확보13/25 PR → 새 행이 있는 구현 본문 확보12/25 PR → 최종 supporting 인용에 새 행 사용4/25 PR → B 대비 추가 gold와 새 인용이 함께 관측된 보고2개였다. 새 소스 행은 공백을 포함한1,075개이며 새 ID만 생성하고 같은 diff 행을 다시 전달한 경우는 새 정보로 세지 않았다.

`node-postgres`에서 A/B는 보고가 없었고, C는 Q3의 완전한 `Client.query` HEAD615–740행 본문 조회로 gold0·4를 탐지했다. timeout이 이전 callback을 직접 호출하는 경로와 callback 없는 custom Query가 tracing에서 빠지는 경로다. 이 연산은 body 조회이므로 LSP definition/references의 독립적인 추가 효과로 계산하지 않는다. C는 AST·LSP·본문 조회를 묶은 조건이다. C−B 추가 gold17개 중 새 인용 연결이 확인된 것은2개이며 나머지15개를 새 본문 효과로 단정하지 않는다.

사후 AI 소스 점검에서는 첫 보고가 호출을 가리킨661행이 빈 줄이고 실제 호출은660행인 오류도 확인했다. 다른 인용과 완전 본문은 주장과 관련됐지만 controller의 유효 범위 결속이 의미적 정확성까지 보장하지 않았다. 원시 보고·공식 점수는 고치지 않았다. 인간검토0, runtime 검증0이다.

| 미해결 기록 유형 | 발생 수 |
|---|---:|
| query_unresolved | 55 |
| ambiguous_AST_symbol_positions | 35 |
| requested_callee_body_not_obtained | 32 |
| new_language_value_flow_not_implemented | 21 |
| native_lookup_empty | 17 |
| literal_search_no_bound_target | 13 |
| environment_record_not_runtime_binding | 9 |
| native_target_not_function_or_class_definition | 6 |
| whole_body_source_token_cap | 5 |
| interprocedural_call_candidate_cap | 5 |
| native_operation_state | 5 |
| callee_value_binding_native_unresolved | 5 |
| interprocedural_value_binding_unresolved | 3 |
| Go_receiver_variadic_or_arity_mapping_unresolved | 2 |
| AST_parse_contains_error_nodes | 2 |
| reference_has_no_enclosing_unit | 1 |
| native_candidate_fanout_cap | 1 |

한 질문에 여러 기록이 가능하므로 표 합계는 실패 질문 수가 아니다. query_unresolved55건은 심볼 토큰이 아닌 column33건·실제 파일 언어 미지원21건·LSP timeout1건이다. 환경 정보만 읽고 runtime binding을 미확정으로 남긴 경우처럼 올바른 유보도 포함된다. 예산만 늘려서 위치·심볼 모호성·미지원 값 전달을 해결할 수는 없다.

## 별도 실험 S: 검토 범주 확대 — 공식 F1 미발행

S는 A와 같은 계획·본문·최종 보고 schema·경고0~6개 제한에서 최종 system의 검토 범주만 테스트·문서·API·접근성·유지보수 등으로 확대했다. 추가 파일 수집 범위나 보고 개수는 확대하지 않았다.

리뷰25개는 모두 생성·보존했다. 공식 strict 판독의 첫 시도는24개 PR 완료 후1개가 timeout·예산 예약 거절로 실패했다. 두 번째 시도에서는180초 이상 응답 헤더가 없던3개 로컬 연결을 닫고 미확인 비용을 보존했다. 세 번째 시도는 cache routing key만 제외하고 모델 입력·도구·schema·생성 설정이 같은 S 요청의 가장 이른 성공 응답을84회 재사용했으나, 정확한 입력 토큰으로 계산해도 남은 요청이$70 잔여 한도를 넘어 실패했다. 마지막 시도는23/25개 PR의 개별 판독을 완료했고 아래 실패를 남겼다.

- `[24/25] vanyastaff_nebula_224-d5ba86dd: FAILED: Model error: OpenAI API error (400): 400 Exact input-token bound still exceeds unchanged $70; no upstream call sent`
- `[21/25] simnaut_emdash_2-41d75006: FAILED: Model error: OpenAI API error (400): 400 Exact input-token bound still exceeds unchanged $70; no upstream call sent`

**실패 PR을 제외한 평균을25개 결과로 발행하지 않았다. S의 gold 재현율·F1·오탐·정탐 보존 전체 수치는 미산출이다.** 성공한 reviewer 응답을 품질에 따라 다시 생성하지 않았고, 응답 없는 요청만 같은 본문으로 복구했다.26개 보관 요청의 본문 동일성을 확인했다. 성공 경로의 논리 토큰은750k 이하였으나 S 실패 시도까지 포함한 물리 토큰 총량은 미확정이다.

## 비용·실제 사용량

| 조건 | 논리 모델 토큰(input+output) | 논리 reviewer 호출 | 성공 active 시간 합 / 최장 PR |
|---|---:|---:|---|
| A | 1,737,153 | 50 | 1763.6s / 101.3s |
| B | 1,802,754 | 50 | 1772.5s / 98.0s |
| C | 1,937,142 | 50 | 1932.0s / 128.4s |

C의 추가 native/LSP 호출141회·109.21초, file read719회, AST parse109회, source read11,220,185bytes였다. 초기 수집은 기존 결과를 재사용하고 그 native/query 비용·stage 시간을 각 논리 예산에 포함했다. 이 표는 reviewer의 소스 수집 비용이며 판독자의 read/grep/find 등은 별도 scoring 로그에 보존했다. 캐시 이득과 쓰기 비용이 포함되므로 cold-cache 알고리즘 비용 비교는 아니다.

전체 새 실험의 **확인 사용량 기준 요율 추정 $57.9846**, usage 미확인10회까지 예약한 **보수적 상한 $69.4756 / $70**이다. S의3차 판독·실패·재시도를 모두 포함한다. 공유 계획을 각 조건에 넣은 표의 논리 비용을 합해 물리 청구 합계로 사용하지 않는다. 계정 invoice 실측은 아니다. 물리 생성 API496회, 무료 입력 계수 API17회, 응답 재사용84회다.

원시 영수증과 byte 예약은 고치지 않고 보정 원장을 분리했다.5xx·timeout·강제 종료된 미확인 호출을0원으로 풀지 않았다. 공식 complete-input 계수에8,192토큰 여유·최대 출력 전체를 추가해 예약했다.503 요청481,217토큰은 같은 입력의 성공 응답 input usage와 일치했다. [입력 계수](https://developers.openai.com/api/docs/guides/token-counting), [무료 계수 안내](https://help.openai.com/en/articles/4936856-understanding-and-counting-tokens), [모델 요율·문맥 한도](https://developers.openai.com/api/docs/models/gpt-5.6-sol).

## PR별 주 비교

| PR | gold TP | 변경 단위 | 계획 단위 | 커밋 제공/전체 | A | B | C |
|---|---:|---:|---:|---|---:|---:|---:|
| PierreJanineh_TechDebtMCP_135-16a54f0f | 9 | 19 | 19 | 8/9 | 0 | 0 | 0 |
| simnaut_emdash_2-41d75006 | 6 | 1015 | 39 | 4/99 | 1 | 1 | 1 |
| konturio_disaster-ninja-fe_1230-d169e9da | 13 | 3 | 3 | 1/1 | 0 | 0 | 0 |
| cmik_apilix_12-0c34439a | 36 | 40 | 27 | 8/8 | 8 | 4 | 5 |
| AA-Factory_aafactory-prototype_17-a1978cb7 | 9 | 1 | 1 | 2/2 | 4 | 3 | 4 |
| DisciplinedSoftware_Codebase-Conversion_1-4b46d68c | 31 | 23 | 23 | 15/23 | 7 | 4 | 6 |
| brianc_node-postgres_3650-b922f0dd | 11 | 11 | 11 | 7/7 | 0 | 0 | 2 |
| PaulStSmith_figlet-comment-generator_32-1d0b6642 | 9 | 47 | 28 | 12/23 | 2 | 2 | 5 |
| brexhq_CrabTrap_15-5ef0884c | 16 | 26 | 25 | 1/1 | 1 | 1 | 0 |
| k1LoW_gh-copilot-review_9-f9828193 | 7 | 3 | 3 | 1/1 | 2 | 2 | 1 |
| scotthamilton77_agents-config_1-1fc78970 | 25 | 47 | 39 | 10/10 | 0 | 0 | 0 |
| ardevd_jadx-collaboration_3-853179dc | 9 | 10 | 10 | 2/2 | 1 | 1 | 1 |
| AY2526S2-CS2103T-W14-3_tp_152-e0a9ce03 | 14 | 51 | 30 | 10/10 | 1 | 0 | 1 |
| vanyastaff_nebula_224-d5ba86dd | 9 | 188 | 28 | 6/18 | 1 | 2 | 1 |
| NCATSTranslator_translator-ingests_336-efb31e9c | 17 | 22 | 22 | 6/6 | 5 | 4 | 4 |
| mheuss_chronicle_14-04ac4c02 | 10 | 57 | 26 | 13/17 | 0 | 0 | 0 |
| superhighfives_pika_200-b227aff0 | 19 | 46 | 27 | 4/4 | 4 | 3 | 4 |
| gagneurlab_PROTRIDER_10-b462b109 | 21 | 108 | 29 | 20/32 | 2 | 2 | 2 |
| allan-mobley-jr_gimmes_468-e1477f8b | 6 | 12 | 12 | 1/1 | 0 | 0 | 0 |
| superyyrrzz_ForgeMap_106-2f3977ed | 15 | 2 | 2 | 12/12 | 0 | 1 | 0 |
| renedierking_LANdalf_88-bc1698f4 | 6 | 4 | 4 | 5/5 | 0 | 0 | 0 |
| scottlz0310_Mcp-Docker_29-e7705d26 | 22 | 8 | 8 | 7/7 | 4 | 4 | 4 |
| wirechat_wirechat_183-6e3156e7 | 12 | 43 | 29 | 9/9 | 1 | 1 | 1 |
| sdsykes_fastimage_165-8fc7cd32 | 14 | 21 | 21 | 5/5 | 1 | 1 | 0 |
| Marvell-Consulting_statswales-frontend_626-30ba27a5 | 6 | 5 | 5 | 1/1 | 0 | 0 | 0 |

## 판단·다음 우선순위

현재 채택 기준은 A 유지다. 먼저 controller가 질문의 파일·심볼·토큰을 정확한 후보에 결속하고 실패한 조회를 수정해 재조회하도록 보완하는 것이 적절하다. 다음은 기존 정탐 후보를 유지한 채 지지·반박을 갱신해 이유 없는 소실을 줄이는 것이다. 커밋별 patch·중간 상태·추가 파일·보고 개수 확대는 각각 별도 조건으로 검증해야 한다. 이 개선안들의 효과는 아직 검증되지 않았다.

모델 실행 전 단위 점검5개·Docker native smoke2개, 예산 복구 테스트2개, 실제 입력 계수 점검3개, cache routing만 제외하고 input/schema 변경을 거절하는 재사용 점검3개를 통과했다. 동결302개·과거 결과277개 파일의 SHA,25개 native prefix 보존,100개 성공 reviewer 경로의 논리 예산을 재검증했다. A/B/C75개 PR은 공식 strict 판독을 완료했고 S 판독 실패는 별도로 유지했다.

동결 명세 · 최종 구조화 결과 · 비용 보정 원장 · 본문·질문·인용 감사 · 사전 테스트 · 복구 테스트 · 입력 동일성 · 원시 조건별 입력·응답·판독 · 보존된 실패 시도
