# 공식 ReviewBench 25개 PR: diff → AST → LSP 비교

세 조건의 새 리뷰·공식 채점 75건이 모두 완료됐다. 같은 25개 PR·BASE/HEAD와 정답표의 실제 결함 항목 352개를 비교했다. AST 추가의 Grounded F1 차이는 +1.35pp, AST 위에 LSP를 추가한 차이는 -3.14pp다.

## 같은 조건에서 무엇을 추가했는가

| 조건 | 제공한 정보 | 동일한 모델 처리 |
|---|---|---|
| B0 · 공개 PR·diff만 | PR 제목·설명, 변경 목록, 최종 리뷰의 전체 diff | 계획 1회 → 최종 리뷰 1회 |
| B1 · AST 주변 본문 추가 | B0 + 변경 위치를 감싸는 함수·클래스의 실제 BASE/HEAD 본문 | 계획 1회 → 최종 리뷰 1회 |
| B2 · LSP 근거 추가 | B1을 그대로 보존 + 정의·참조 등의 위치/관계 후보와 연결해 확보한 실제 본문 | 계획 1회 → 최종 리뷰 1회 |

모든 조건은 `gpt-5.6-sol`, reasoning low, 동일 보고 schema·최종 reviewer·공식 scorer다. 계획은 각 조건의 입력으로 새로 생성했다. 계획 단계에는 변경 목록·hunk 표본과 해당 조건의 근거, 최종 단계에는 전체 diff를 전달한다. 계획에서 만든 질문을 실제 도구로 재조회하는 agentic 실험은 아니다. PR당 reviewer 총 750,000 토큰 상한과 6,000 source reference-token 상한을 적용했으며 judge 비용·토큰은 별도다.

## 공식 평가 결과: micro 집계

| 조건 | gold 탐지/352 | gold 재현율 | Grounded F1 | Augmented F1 | 보고 수 | 오탐 합계 | 리뷰+채점 비용 추정 |
|---|---:|---:|---:|---:|---:|---:|---:|
| B0 · 공개 PR·diff만 | 39 | 11.08% | 19.71% | 22.02% | 44 | 5 | $12.7403 |
| B1 · diff + AST 주변 본문 | 42 | 11.93% | 21.06% | 24.35% | 50 | 6 | $15.2134 |
| B2 · diff + AST + LSP 근거 | 35 | 9.94% | 17.92% | 20.31% | 39 | 4 | $14.9160 |

F1은 전체 PR의 계수를 먼저 합친 micro 정밀도·재현율의 조화평균이다. 보고 하나가 여러 gold 항목과 대응할 수 있어 ‘정탐 보고 수’와 ‘gold 탐지 수’는 다르다. 오탐 합계는 정답표의 오탐 항목에 대응한 경고(matched FP)와 정답표 밖에서 채점 AI가 오탐으로 판정한 경고(unmatched FP)의 합이다.

Grounded 정밀도는 **정답표와 대응된 보고만** 분모에 넣고, Grounded 재현율은 정답표의 실제 결함 중 찾은 비율이다. 정답표 밖 보고는 Grounded 정밀도에 들어가지 않는다. Augmented는 정답표 밖 보고도 공식 채점 AI가 추가 판단하여 정밀도·재현율에 반영한다. AI가 인정한 새 결함은 실행·인간 검증으로 확정한 결함이 아니다. 두 지표는 자체 원인 F1/유효 보고 F1과 동일한 지표가 아니다.

| 조건 | Grounded 정밀도 | Augmented 정밀도 | Augmented 재현율 | 대응 정탐 보고 | AI 인정 정답표 밖 보고 | matched FP + unmatched FP |
|---|---:|---:|---:|---:|---:|---:|
| B0 | 89.19% | 88.64% | 12.57% | 33 | 6 | 4 + 1 |
| B1 | 89.74% | 88.00% | 14.13% | 35 | 9 | 4 + 2 |
| B2 | 90.62% | 89.74% | 11.45% | 29 | 6 | 3 + 1 |

## 단계별 정탐 보존·추가·손실

| 새 결과끼리 비교 | 이전 gold 정탐 | 보존 | 추가 | 손실 | 보존율 | 순증 | Grounded F1 차이 | Augmented F1 차이 | 오탐 차이 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| B0 → B1 | 39 | 29 | 13 | 10 | 74.36% | +3 | +1.35pp | +2.33pp | +1 |
| B1 → B2 | 42 | 30 | 5 | 12 | 71.43% | -7 | -3.14pp | -4.03pp | -2 |
| B0 → B2 | 39 | 25 | 10 | 14 | 64.10% | -4 | -1.79pp | -1.70pp | -1 |

보존·추가·손실은 같은 `(PR, gold index)` 집합의 교집합·차집합이다. 정답표 밖 AI 인정 보고는 이 paired 집합에 넣지 않았다. 과거 22.60% 결과는 호출·입력 등이 다른 이전 실험이며 이번 B0나 paired 기준으로 사용하지 않았다.

## LSP가 실제로 추가한 정보에 따른 진단

| LSP 추가 정보 | PR 수 | B0 Grounded F1 | B1 Grounded F1 | B2 Grounded F1 | B1→B2 차이 | gold 보존/추가/손실 | 오탐 B1→B2 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 새 본문·물리행 추가 | 15 | 18.56% | 20.71% | 15.84% | -4.87pp | 17/3/10 | 5 → 3 |
| 위치·관계 메타데이터만 추가 | 4 | 26.23% | 29.03% | 26.23% | -2.80pp | 7/1/2 | 0 → 0 |
| AST와 LSP 입력 근거 동일 | 6 | 18.42% | 15.75% | 18.14% | +2.39pp | 6/1/0 | 1 → 1 |

새 본문 15개 PR에서 추가된 물리행은 총 1,757행이다. LSP 추가 근거 177개는 본문 57개와 위치·관계 메타데이터 120개로 구성된다. 새 본문 층의 정답 증가도 그 본문이 직접 원인이었다는 증명은 아니다. 근거가 같은 6개 PR에서의 차이는 새 소스 확보 효과로 해석할 수 없으며, 독립 계획/최종 생성과 AI 채점의 변동을 포함한다. 메타데이터는 정적 후보 관계이며 실행 경로를 확정하지 않는다.

## 비용·실패·입력 가용성

| 조건 | 리뷰 API 비용 추정 | 채점 API 비용 추정 | usage 미확인 시도 | 미확인 예약 포함 상한 | 과거 native 시간 | 이번 native 재수집 |
|---|---:|---:|---:|---:|---:|---:|
| B0 | $10.9593 | $1.7810 | 0 | $12.7403 | 0.000초 | 0회 |
| B1 | $12.4556 | $2.7578 | 0 | $15.2134 | 0.725초 | 0회 |
| B2 | $12.6443 | $2.2717 | 0 | $14.9160 | 229.740초 | 0회 |

실제 API usage에 동결한 과거 단가를 적용한 전체 추정은 **$42.8696**, 미확인 호출의 예약까지 포함한 상한은 **$42.8696 / $70.00**다. 청구서 확정 금액이 아니다. 캐시 읽기·쓰기와 실패/usage 미확인 시도는 `runtime_cost.summarize` 원장을 따른다. API usage 미확인 0회는 비용 0으로 확정하지 않는다.

| 조건 | 논리 리뷰 호출 | 실제 리뷰 모델 토큰 합계 | PR별 토큰 상한 준수 | raw→공식 보고 | anchor 매핑 제외 | 리뷰 active 시간 합계 |
|---|---:|---:|---|---:|---:|---:|
| B0 | 50 | 1,387,182 | 예 | 44 → 44 | 0 | 1501.7초 |
| B1 | 50 | 1,660,850 | 예 | 50 → 50 | 0 | 1575.2초 |
| B2 | 50 | 1,740,263 | 예 | 39 → 39 | 0 | 1528.8초 |

active 시간 합계는 병렬 작업들의 소요시간 합이며 전체 실험의 벽시계 시간이 아니다. 과거 native 시간에는 Go AST 외부 프로세스도 포함되므로 전부 LSP 서버 시간으로 해석하지 않는다. B2의 과거 비용은 AST+LSP 누적값이며 B1 비용을 다시 더하지 않는다. 지원하지 않는 언어·미해결 위치는 입력 가용성 한계로 보존했고 해당 PR를 평가에서 제외하지 않았다.

## 해석 범위

- 기존 수집은 AST 3,000, AST+LSP 누적 6,000 reference-token cap으로 동결돼 있다. 이번 6,000 공통 상한은 최대치이며 실제 본문량을 동일하게 맞춘 비교가 아니다. AST 탐색에도 새로 6,000을 사용했을 때의 결과나 동일 사실을 다른 도구로 제공했을 때의 효과는 측정하지 않았다.
- 이 25개 PR는 앞선 실험에서 반복 사용한 개발 cohort다. 독립 미사용 PR 일반화, 통계적 유의성, 리더보드 성능을 주장하지 않는다. 조건당 1회 생성·AI 채점이며 인간 추가 검토나 runtime probe는 수행하지 않았다.
- 요청한 모델명은 alias `gpt-5.6-sol`다. 응답에 기록된 모델명은 `gpt-5.6-sol`다. alias는 장래 동일 가중치/동일 출력을 보장하지 않는다.
- 이번 검증은 같은 reviewer에 제공한 diff/AST/LSP 근거의 효과다. 질문별 재탐색·원인 검증/철회·SCIP·다중 에이전트 debate의 효과로 확대 해석하지 않는다.
- 최종 보고의 anchor·인용이 기계적으로 결속됐다는 사실만으로 원인 설명의 의미적 정확성을 보장하지 않는다. 이번 F1은 공식 채점기 판정이며 자체 유효 보고 F1을 대신하지 않는다.

## PR별 결과

각 셀의 세 숫자는 B0/B1/B2 순서다. 보존/추가/손실은 B1→B2다.

| PR | 언어 | LSP 새 물리행 | gold 탐지 B0/B1/B2 | 보고 수 B0/B1/B2 | 오탐 B0/B1/B2 | gold 보존/추가/손실 |
|---|---|---:|---:|---:|---:|---:|
| [PierreJanineh/TechDebtMCP#135](https://github.com/PierreJanineh/TechDebtMCP/pull/135) | Mixed | 0 | 0/0/0 | 0/1/0 | 0/1/0 | 0/0/0 |
| [simnaut/emdash#2](https://github.com/simnaut/emdash/pull/2) | Mixed | 8 | 1/1/1 | 3/3/3 | 1/1/1 | 1/0/0 |
| [konturio/disaster-ninja-fe#1230](https://github.com/konturio/disaster-ninja-fe/pull/1230) | Mixed | 57 | 0/0/0 | 0/0/1 | 0/0/0 | 0/0/0 |
| [cmik/apilix#12](https://github.com/cmik/apilix/pull/12) | Mixed | 263 | 3/4/3 | 5/6/5 | 0/0/0 | 3/0/1 |
| [AA-Factory/aafactory-prototype#17](https://github.com/AA-Factory/aafactory-prototype/pull/17) | Mixed | 0 | 4/3/3 | 2/2/2 | 0/0/0 | 3/0/0 |
| [DisciplinedSoftware/Codebase-Conversion#1](https://github.com/DisciplinedSoftware/Codebase-Conversion/pull/1) | Mixed | 34 | 7/7/4 | 6/6/4 | 0/0/0 | 3/1/4 |
| [brianc/node-postgres#3650](https://github.com/brianc/node-postgres/pull/3650) | Mixed | 0 | 0/0/0 | 0/0/0 | 0/0/0 | 0/0/0 |
| [PaulStSmith/figlet-comment-generator#32](https://github.com/PaulStSmith/figlet-comment-generator/pull/32) | Mixed | 32 | 2/2/2 | 4/4/2 | 1/1/0 | 1/1/1 |
| [brexhq/CrabTrap#15](https://github.com/brexhq/CrabTrap/pull/15) | Mixed | 104 | 0/1/0 | 0/1/0 | 0/0/0 | 0/0/1 |
| [k1LoW/gh-copilot-review#9](https://github.com/k1LoW/gh-copilot-review/pull/9) | Mixed | 3 | 1/2/2 | 1/1/1 | 0/0/0 | 2/0/0 |
| [scotthamilton77/agents-config#1](https://github.com/scotthamilton77/agents-config/pull/1) | Mixed | 30 | 0/0/0 | 2/2/1 | 1/1/1 | 0/0/0 |
| [ardevd/jadx-collaboration#3](https://github.com/ardevd/jadx-collaboration/pull/3) | Mixed | 0 | 1/1/1 | 1/1/1 | 0/0/0 | 1/0/0 |
| [AY2526S2-CS2103T-W14-3/tp#152](https://github.com/AY2526S2-CS2103T-W14-3/tp/pull/152) | Mixed | 0 | 0/1/1 | 0/1/1 | 0/0/0 | 0/1/1 |
| [vanyastaff/nebula#224](https://github.com/vanyastaff/nebula/pull/224) | Mixed | 0 | 1/1/1 | 1/3/2 | 0/0/0 | 1/0/0 |
| [NCATSTranslator/translator-ingests#336](https://github.com/NCATSTranslator/translator-ingests/pull/336) | Mixed | 0 | 4/4/4 | 3/3/3 | 0/0/0 | 4/0/0 |
| [mheuss/chronicle#14](https://github.com/mheuss/chronicle/pull/14) | Mixed | 0 | 1/0/1 | 1/1/2 | 0/0/1 | 0/1/0 |
| [superhighfives/pika#200](https://github.com/superhighfives/pika/pull/200) | Mixed | 0 | 4/5/4 | 5/5/4 | 0/0/0 | 4/0/1 |
| [gagneurlab/PROTRIDER#10](https://github.com/gagneurlab/PROTRIDER/pull/10) | Mixed | 240 | 3/3/3 | 3/3/3 | 0/0/0 | 3/0/0 |
| [allan-mobley-jr/gimmes#468](https://github.com/allan-mobley-jr/gimmes/pull/468) | Mixed | 16 | 0/1/0 | 1/1/0 | 1/1/0 | 0/0/1 |
| [superyyrrzz/ForgeMap#106](https://github.com/superyyrrzz/ForgeMap/pull/106) | Mixed | 0 | 0/0/0 | 0/0/0 | 0/0/0 | 0/0/0 |
| [renedierking/LANdalf#88](https://github.com/renedierking/LANdalf/pull/88) | Mixed | 510 | 1/1/0 | 1/1/0 | 0/0/0 | 0/0/1 |
| [scottlz0310/Mcp-Docker#29](https://github.com/scottlz0310/Mcp-Docker/pull/29) | Mixed | 19 | 4/4/3 | 2/2/2 | 0/0/1 | 3/0/1 |
| [wirechat/wirechat#183](https://github.com/wirechat/wirechat/pull/183) | Mixed | 289 | 1/0/1 | 2/2/1 | 1/1/0 | 0/1/0 |
| [sdsykes/fastimage#165](https://github.com/sdsykes/fastimage/pull/165) | Mixed | 18 | 1/1/1 | 1/1/1 | 0/0/0 | 1/0/0 |
| [Marvell-Consulting/statswales-frontend#626](https://github.com/Marvell-Consulting/statswales-frontend/pull/626) | Mixed | 134 | 0/0/0 | 0/0/0 | 0/0/0 | 0/0/0 |

## 재현 자료

[동결 명세](PROTOCOL_KO.md) · 수치·paired 항목·PR별 결과 · 입력·수집 provenance · 기존 파일 보존 해시

공식 scorer의 `results.json`·`results.details.json`은 각 `arms/B0`, `arms/B1`, `arms/B2`의 `scoring`에 보존된다. raw request/response, 계획, 실제 전달 근거, 위치 변환, API usage·실패 원장도 각각 보존한다. gold는 이 사후 보고 집계에만 사용하며 reviewer 입력으로 전달하지 않는다.
