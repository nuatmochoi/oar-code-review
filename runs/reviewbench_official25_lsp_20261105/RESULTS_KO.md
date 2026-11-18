# ReviewBench 공식 로컬 runner 25개 결과 — LSP 최고 방식의 다국어 확장

**25/25개 리뷰·공식 채점 완료. Augmented F1 23.59%, 정밀도 95.35%, 재현율 13.46%. 43개 경고 중 AI 채점 기준 정탐 41개·오탐 2개. 사용량 기준 합산 비용 $12.6122 / 예산 $70.**

오탐은 적지만 재현율이 낮다. 이번에는 기존 최고 LSP 방식을 공식 runner로 이식해 1개 조건만 평가했으므로, LSP나 언어 확장의 독립적인 추가 효과를 판정할 수 없다. 기존 자체 원인 F1·유효 보고 F1과 아래 공식 지표는 정의가 다르다.

## 1. 무엇을 실행했나

| 항목 | 실행 조건 |
|---|---|
| 데이터 | 공식 corpus/test/test.json 25개 전체, 1반복, 실패 PR 제외 없음 |
| 리뷰어 | 기존 L6: 공개 설명·전체 diff → AST 본문 3k prefix → LSP 정의·참조로 총 본문 6k → 최종 리뷰 1회 |
| 언어 확장 | Python·Go 유지, TypeScript/TSX·JavaScript·C#·Java·Kotlin·Rust·Swift·PHP·Ruby·Shell 추가 |
| 수집 한도 | source 3k→6k, 질문 6개, native 32회/180초, 파일 128회, query 20초, collection 900초 |
| 모델 | 리뷰·판정 모두 gpt-5.6-sol, reasoning low, standard tier, 출력 상한 16,384 |
| 병렬 | 공식 try-agent.sh --pr를 worker 3개·서로 다른 work 디렉터리에서 실행 |
| 제외된 기능 | SCIP·추가 agentic 재탐색·토론·runtime probe 0회 |
| 공식 채점 | upstream npm run judge, strict, ingest 없음. 원본 코드 변경 없음 |
| 인간 검토 | 0. 정탐/오탐·신규 정탐은 공식 gold 및 AI 판독 기준 |

Upstream commit: `03c62fe06ca42b39216402ebbcb9aec765109a59`. Docker image: `sha256:5ca2d36ae91443d988bbfe29edd47c71c67a80485dca9332ee2ecfaee26f817a`. 동결 명세, 417개 파일 동결 기록.

기존 12개와 2개, 기존 48개와 6개가 겹친다. 미사용 holdout이 아니며 공식 25개 개발 점검이다. Linux 다국어 이식이므로 과거 macOS 12개 실행을 그대로 재현한 결과라고 하지 않는다. 분석용 toolchain의 역사적 PR 환경 동등성, macOS SDK, 미설치 외부 dependency는 별도 가정/미해결이다.

## 2. 공식 지표와 F1

| 집계 | Grounded 정밀도 | Grounded 재현율 | 도출 F1 | Augmented 정밀도 | Augmented 재현율 | 도출 F1 |
|---|---:|---:|---:|---:|---:|---:|
| Micro (주표) | 93.55% | 10.51% | 18.90% | 95.35% | 13.46% | 23.59% |
| Macro P/R의 조화평균 | 88.24% | 9.89% | 17.78% | 93.33% | 13.33% | 23.33% |

F1은 공식 scorer가 출력한 precision·recall에 `2PR/(P+R)`를 적용한 후처리다. 공식 CLI가 F1을 직접 출력한 것은 아니다. Macro 행은 PR별 F1의 평균이 아니다.

| 분자·분모 | 수치 |
|---|---:|
| 전체 경고 | 43 |
| Gold의 정탐과 일치한 경고 | 29 |
| Gold의 오탐과 일치한 경고 | 2 |
| Gold와 미일치, AI가 정탐으로 판독 | 12 |
| Gold와 미일치, AI가 오탐으로 판독 | 0 |
| 기존 gold 정탐 | 352 |
| 탐지한 기존 gold 정탐 항목 | 37 |
| 미탐 기존 gold 정탐 항목 | 315 |

Grounded P=29/31, R=37/352. Augmented P=(29+12)/43=41/43, R=(37+12)/(352+12)=49/364. 한 경고가 여러 gold 항목을 포괄할 수 있어 경고 정탐 수와 탐지한 gold 항목 수가 다르다. Grounded precision은 gold와 일치한 경고만 분모에 넣으므로 전체 오탐과 함께 평가할 때 augmented 지표를 우선 본다. 신규 12개는 인간이 확정한 신규 결함 수가 아니다.

## 3. 비용·토큰·완료성

| 단계 | 물리 API 호출 | 입력 토큰 | 출력 토큰 | 캐시 읽기 | 캐시 쓰기 | 사용량 기준 비용 |
|---|---:|---:|---:|---:|---:|---:|
| 리뷰 | 25 | 1,278,451 | 50,853 | 0 | 1,278,376 | $9.8136 |
| 공식 매칭·추가 판독 | 79 | 1,695,593 | 17,059 | 1,308,010 | 383,898 | $2.7986 |
| 합계 | 104 | 2,974,044 | 67,912 | 1,308,010 | 1,662,274 | **$12.6122** |

입력·캐시 쓰기·출력의 가장 비싼 문서화 요율로 정산한 보수 상한은 $36.4044, 실행 중 동시 예약 상한의 최대는 $48.8511였다. 둘 다 $70 이하다. usage 미상 0건, 유료 리뷰 재발급 0건이다.

실제 API usage에 공개 요율을 적용한 비용이며 계정 청구서를 조회한 값은 아니다. cache_write_tokens가 반환돼 기본 30분 TTL의 총 1.25배 쓰기 요율을 반영했다. 원시 findings.usage.cost_usd와 proxy의 초기 base-rate 추정은 캐시 쓰기 가산을 반영하지 않아 보존한 원본 값이며, 최종 비용은 cost.corrected.json을 사용한다. 요청·응답·frozen budget 정책은 수정하지 않았다. [모델 요율](https://developers.openai.com/api/docs/models/gpt-5.6-sol), [캐시 쓰기 과금 및 기본 TTL](https://developers.openai.com/api/docs/guides/prompt-caching).

본문 6k는 추가 source 상한이다. 전체 diff·위치 catalogue·schema 등은 별도 입력이다. simnaut/emdash#2는 입력 475,869토큰으로 긴 문맥 요율이 적용됐으며 전체 입력을 잘라내지 않았다.

## 4. LSP가 실제로 얼마나 작동했나

| 관측 | 결과 |
|---|---:|
| 작은 코드에서 정의 본문을 확보한 언어 서버 | 12/12 (TSX 문법 별도 확인) |
| 실제 PR에서 definition/reference 위치 결과 확보 | 19/25 |
| AST prefix에 없던 LSP 본문 추가 | 15/25 |
| definition/reference 등 실제 textDocument RPC | 162 |
| 전체 native 호출 (AST helper·초기화·종료 포함) | 267 |
| Native 기록 시간 합 | 229.74초 |
| 초기화 timeout | 2회 (Kotlin BASE/HEAD) |
| 종료 오류 | 1회 |
| Controller 변환 실패/무위치 경고 삭제 | 0/43 |

설치·capability·위치·본문 추가·의미적 유효성을 구분한다. 새 본문 0은 조회 실패와 같지 않다. 이미 AST prefix에 있던 본문을 재확인한 경우도 있다. Controller의 위치 ID는 물리적 행·범위 결속을 보장하며 원인의 정답을 보장하지 않는다.

| PR | 실제 변경 코드 확장자 | 경고 | AST 본문 토큰 | LSP 후 본문 토큰 | 증분 | Native 호출 | 위치 반환 RPC | Augmented F1 |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| PierreJanineh/TechDebtMCP#135 | .ts | 1 | 2,158 | 2,158 | +0 | 12 | 0 | 20.00% |
| simnaut/emdash#2 | .js, .mjs, .ts, .tsx | 2 | 1,232 | 1,268 | +36 | 10 | 6 | 40.00% |
| konturio/disaster-ninja-fe#1230 | .ts | 1 | 1,551 | 1,809 | +258 | 12 | 3 | 13.33% |
| cmik/apilix#12 | .js, .ts, .tsx | 5 | 2,882 | 5,218 | +2,336 | 12 | 6 | 31.11% |
| AA-Factory/aafactory-prototype#17 | .py | 1 | 431 | 431 | +0 | 8 | 3 | 20.00% |
| DisciplinedSoftware/Codebase-Conversion#1 | .py | 5 | 2,611 | 2,902 | +291 | 8 | 5 | 31.58% |
| brianc/node-postgres#3650 | .js | 0 | 2,111 | 2,111 | +0 | 12 | 3 | — |
| PaulStSmith/figlet-comment-generator#32 | .ts, .tsx | 3 | 2,973 | 3,256 | +283 | 11 | 5 | 46.15% |
| brexhq/CrabTrap#15 | .go, .ts, .tsx | 0 | 2,998 | 3,953 | +955 | 18 | 6 | — |
| k1LoW/gh-copilot-review#9 | .go | 1 | 999 | 1,024 | +25 | 15 | 6 | 44.44% |
| scotthamilton77/agents-config#1 | .sh, .ts | 0 | 2,750 | 2,984 | +234 | 12 | 3 | — |
| ardevd/jadx-collaboration#3 | .kt | 1 | 1,169 | 1,169 | +0 | 3 | 0 | 20.00% |
| AY2526S2-CS2103T-W14-3/tp#152 | .java | 1 | 2,910 | 2,910 | +0 | 12 | 1 | 13.33% |
| vanyastaff/nebula#224 | .rs | 2 | 2,951 | 2,951 | +0 | 12 | 0 | 30.77% |
| NCATSTranslator/translator-ingests#336 | .py | 4 | 1,495 | 1,495 | +0 | 10 | 0 | 43.48% |
| mheuss/chronicle#14 | .rs | 3 | 1,950 | 1,950 | +0 | 11 | 0 | 26.67% |
| superhighfives/pika#200 | .swift | 4 | 2,998 | 2,998 | +0 | 12 | 1 | 34.78% |
| gagneurlab/PROTRIDER#10 | .py | 3 | 2,992 | 5,248 | +2,256 | 10 | 5 | 25.00% |
| allan-mobley-jr/gimmes#468 | .py | 1 | 2,991 | 3,131 | +140 | 10 | 1 | 0.00% |
| superyyrrzz/ForgeMap#106 | CSS/YAML/docs 등 | 0 | 0 | 0 | +0 | 0 | 0 | — |
| renedierking/LANdalf#88 | .cs | 1 | 1,711 | 5,739 | +4,028 | 12 | 3 | 28.57% |
| scottlz0310/Mcp-Docker#29 | .go | 2 | 2,999 | 3,104 | +105 | 13 | 5 | 30.77% |
| wirechat/wirechat#183 | .php | 1 | 2,274 | 4,395 | +2,121 | 9 | 5 | 15.38% |
| sdsykes/fastimage#165 | .rb | 1 | 2,984 | 3,096 | +112 | 11 | 6 | 13.33% |
| Marvell-Consulting/statswales-frontend#626 | .ts | 0 | 938 | 2,144 | +1,206 | 12 | 5 | — |

위치 결과가 없었던 6개는 TechDebtMCP(TypeScript), jadx-collaboration(Kotlin), nebula·chronicle(Rust), translator-ingests(Python 변경), ForgeMap(CSS/YAML 변경)이다. Kotlin은 BASE/HEAD 초기화 timeout이 기록됐다. ForgeMap은 해당 diff에 C# 변경이 없어 C# 서버 지원 여부와 구분해야 한다. 나머지는 실제 native 조회가 빈 값을 반환했으며, 의존성/프로젝트 준비 부족과 실제 참조 부재 중 어느 것인지는 이번 관측만으로 확정할 수 없다. 서버 초기화 성공을 저장소 의미 분석 성공으로 대신 세지 않았다.

본문을 온전히 담을 수 없어 거절한 source-token cap 이벤트 45건, 빈 native 조회 59건, 참조의 완전한 enclosing unit 미확보 20건이 기록됐다. 이벤트 수는 중복·BASE/HEAD를 포함하며 독립 결함 수가 아니다.

## 5. 공식 category별 보조 결과

| Category | Gold 정탐 | Augmented 정밀도 | Augmented 재현율 | P/R 조화평균 F1 |
|---|---:|---:|---:|---:|
| accessibility | 10 | — | 0.00% | — |
| api-architecture | 12 | 100.00% | 14.29% | 25.00% |
| correctness | 133 | 96.43% | 23.74% | 38.10% |
| documentation | 21 | — | 0.00% | — |
| maintainability | 44 | — | 0.00% | — |
| other | 0 | — | — | — |
| performance | 12 | — | 0.00% | — |
| reliability | 58 | 100.00% | 13.33% | 23.53% |
| security | 27 | 75.00% | 17.24% | 28.04% |
| testing | 35 | 100.00% | 2.86% | 5.56% |

전체 352개 gold 정탐에는 correctness 외에 testing·maintainability·documentation·accessibility 등도 포함된다. 선택한 기존 reviewer는 기능적 결함을 대상으로 하므로 범위 차이가 전체 재현율에 영향을 줄 수 있다. 전체 주표를 유지하고 category별 숫자는 보조 분석으로 제시한다. category별 차이를 이번 데이터에서 새로 최적화하거나 gold를 탐색 입력으로 사용하지 않았다.

## 6. 실패 보존·검증·해석

1. 초기 25개 체크아웃은 사전 부분 clone 재사용 시 누락 blob으로 모델 실행 전에 실패했다. 고정 BASE/HEAD/merge-base 파일을 물질화한 후 같은 image·prompt·budget으로 재실행했다. 원래 로그·상태는 attempts/git-partial-clone-failure에 보존했다. 유료 호출 0이었다.
2. 첫 공식 채점은 custom registry보다 provider 환경 key의 우선순위가 높아 계정 키가 아닌 dummy 값으로 proxy 인증이 거절됐다. 원래 로그·실패·checkpoint를 보존하고 API 인증 변수만 맞췄다. 최초 유료 judge 호출 전에 transport amendment를 기록했다. 이미 완료된 빈 경고 PR 5개는 공식 checkpoint로 재사용했다.
3. 본 리뷰는 25/25 정상, 경고 43개 모두 위치 변환 성공. 공식 judge도 최종 25/25 strict 정상 종료했다. 초기 실패를 정상 무경고로 바꾸거나 failed PR을 점수에서 제외하지 않았다.
4. 모델 실행 전 고정한 417개 파일 및 과거 결과 참조 39개의 hash가 완료 후에도 일치했다. 공식 golden·prompt·scorer를 변경하지 않았다.

**해석:** 정밀도는 높고 재현율이 병목이다. 이번 결과만으로 LSP의 인과적 추가 효과나 기존 자체 F1 대비 개선을 주장하지 않는다. 다음 설계 판단에는 (a) 같은 공식 25개에서 diff-only/AST/LSP 대조, (b) 빈 조회와 초기화·프로젝트/의존성 미준비 구분, (c) 완전 단위 포장 때문에 빠진 큰 함수의 보존 방법 순으로 검증하는 것이 타당하다. 이는 후속 제안이며 이번에 추가 유료 실행하지 않았다.

## 7. 산출물과 공식 결과의 범위

- 원시 findings 25개
- 공식 결과 JSON, 경고별 판독
- API 원시 요청·응답·원장, 보정 비용
- PR별 수집·입력·본문·위치·모델 응답, 실제 native 관측
- 동결·예산·schema 명세, budget 테스트
- 정의 본문 기동 검증, 과거 결과 보존

공식 소프트웨어를 이용한 로컬 평가다. 포털·리더보드에는 등록/제출하지 않았다. 공식 leaderboard의 Claude Sonnet 5 judge 및 219개×3반복 조건과 다르다. [공식 offline 안내](https://github.com/review-bench/ReviewBench/blob/03c62fe06ca42b39216402ebbcb9aec765109a59/docs/OFFLINE_EVAL.md), [공식 scorer](https://github.com/review-bench/ReviewBench/blob/03c62fe06ca42b39216402ebbcb9aec765109a59/scripts/eval/scorer.ts).
