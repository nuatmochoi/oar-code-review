# Serialized-budget fanout reviewer 결과

128 reference-token 소스 예산에서 필요한 소스를 회수한 flow와 BM25는
unsafe 메커니즘 3/3, safe 정상 판별 3/3을 기록했다. Diff-only와 구현한
call 기반 selector는 두 world 모두에서 근거 부족을 답했다.

이 사례에서는 context 보강으로 판단이 가능해진다는 관측이 있다. 그러나
BM25와 flow가 같은 결과를 얻고 BM25 top-2가 동일 source까지 회수하므로
flow만의 contribution이나 paired semantic index의 효과는 입증되지 않았다.

## 실험 입력과 범위

기존 same-diff 사례에 12개 unchanged audit 호출을 추가한 synthetic 파생
control이다. 자연 PR이나 독립적인 결함 family로 세지 않는다. 두 world의
full diff가 같고 route의 literal 두 번째 값만 다르며, 각 world 내부에서는
companion이 base/head 동안 변하지 않는다. Static gate와 four-cell witness는
safe base/head pass, unsafe base pass/head target_failure를 검증했다.

명세는 현재 revision의 route()와 실제 literal 호출로 범위를 한정했다.
다른 호출자·미래 입력·일반 consumer 계약을 평가하지 않도록 명시했다.
따라서 이전 pilot의 넓은 명세와 같은 조건으로 합산하지 않는다.

조건은 diff-only, call-source, flow-source, BM25(diff+명세 query)다.
동일 file closure를 사용하고 target.py 전체 17줄은 공통 full diff로 제공한다.
Graph 조건은 shortest path, serialized bundle cost, filename 순으로 선택한다.
Call은 function seed와 call edges, flow는 value seed와 full graph를 사용한다.
Seed와 edge set을 함께 바꿨으므로 typed edge만의 효과는 아니다.
Head graph와 diff line만 사용하며 base/head index delta는 처리 변수가 아니다.

## Retrieval 확인

| 조건 | Unsafe source tokens | 회수한 소스 |
|---|---:|---|
| Diff-only | 0 | 추가 소스 없음 |
| Call-source | 111 | sink + audit 5개, route 누락 |
| Flow-source | 48 | route + sink |
| BM25 | 124 | route + sink + audit 4개 |

Safe는 flow 49, BM25 125 tokens다. 모든 값은 파일명·줄 번호가 포함된
직렬화 source의 o200k_base reference tokens이며, 예산 128 이하다.
공통 명세·diff와 padding을 포함한 공개 prompt는 모든 cell에서 518 tokens다.

64/128/256 예산에서 call은 route를 놓쳤고 512에서는 회수했다. Flow와
BM25는 64부터 두 파일을 회수했다. 사후 비모델 top-k 진단에서 BM25 top-2는
flow와 정확히 같은 route+sink를 49/48 tokens로 회수했다. 따라서 48 대 124는
intrinsic compression 차이로 주장할 수 없고, BM25의 채우기/중단 규칙도 영향을 준다.

## Reviewer 결과 — 수정된 격리 설정 실행

gpt-5.6-sol, reasoning low, 4조건 × 2world × 3회 = 24 calls.

| 조건 | Unsafe grounded mechanism | Safe 정상 판별 | Safe 오탐 | 근거 부족 safe/unsafe | Paired success |
|---|---:|---:|---:|---:|---:|
| Diff-only | 0/3 | 0/3 | 0/3 | 3/3, 3/3 | 0/3 |
| Call-source | 0/3 | 0/3 | 0/3 | 3/3, 3/3 | 0/3 |
| Flow-source | 3/3 | 3/3 | 0/3 | 0/3, 0/3 | 3/3 |
| BM25 | 3/3 | 3/3 | 0/3 | 0/3, 0/3 | 3/3 |

근거 부족을 safe 정상 판별로 세지 않았다. Unsafe grounded 응답 6개는 실제
routes.py의 UserID/userid 인자, secondary 선택, sink의 identity 반환을 연결했고
모두 target.py:4를 지목했다. Safe 정상 응답은 두 인자가 현재 동일함을 설명했다.
Diff/call 입력은 world 사이에서 동일하며, route가 없다는 이유로 판별을 유보했다.

실험을 진행한 assistant가 24개 설명을 모두 읽고 명시적 응답별 judgment를
기록했다. Independent/blind adjudication은 아니다. 3회 반복은 생성 안정성
관측이지 3개의 독립 family 표본이 아니다. 통계적 유의성은 주장하지 않는다.

## 실행 격리 오류와 수정

최초 24회 실행에서 review_17의 웹 검색 사용이 trace에 나타나 1건이
실패 처리됐다. 기존 browser 관련 feature disable만으로는 별도 웹 검색을
차단하지 못했다. 최초 run의 23개 정상·1개 실패와 raw traces를 그대로 보존했다.

OpenAI docs 스킬의 공식 manual 확인을 통해 `web_search="disabled"` 옵션을
명시적으로 추가했다. 이 옵션은 웹 검색을 제거하는 별도 설정이다.
[Codex 공식 manual](https://developers.openai.com/codex/codex-manual.md).

동일 24개 prompt를 수정 설정으로 전부 재실행했다. 최종 표는 재실행만
사용하며 원 실행과 합치지 않는다. 재실행 24/24 성공, 관측 tool events 0이며
모든 item event가 agent_message였다. 빈 작업 디렉터리와 read-only 실행,
기존 도구 disable 목록도 유지했다. 도구 사용을 실패로 보존하는 테스트에
명시적 웹 검색 차단 옵션 확인을 추가했다.

이전 packet pilot은 관측 도구 이벤트가 0이었지만 웹 검색이 설정상 제거됐다고
볼 수는 없다. 향후 실험은 수정 runner를 사용해야 한다.

## 비용 기록

이번 단계는 최초 24회 + 격리 수정 후 24회, 총 48 generation을 실행했다.
최종 집계에 사용한 것은 후자의 24회다.

- 최초 run reported input tokens: 310,625
- 재실행 reported input tokens: 244,475 (그중 cached 71,424)
- 재실행 reported output tokens: 2,756
- 재실행 별도 reasoning output tokens: 376
- 재실행 호출별 reported input: 9,769–19,714

공개 prompt의 518 reference tokens와 CLI가 보고한 실제 전체 입력은 다르다.
시스템 overhead와 복수 message 처리 등이 포함되므로 전체 API 입력 길이까지
일치했다고 주장하지 않는다. 비용 금액은 계산하지 않았다.

## 다음 판단

“필요한 context가 있으면 diff-only에서 판단하지 못하던 구체적 결함을
리뷰할 수 있다”는 구성 타당성은 이 control에서 관측했다. 그러나 새로운
semantic retrieval contribution으로 올리려면 강한 BM25 baseline보다 나아야 한다.

Synthetic distractor를 더 늘려 약한 call selector를 이기는 실험의 우선순위는
낮춘다. 다음은 기존 관측 사례와 분리된 실제 PR에서, 동일 chunk·전체 packet
budget·명세 조건을 고정하고 BM25/top-k 및 semantic 경로 선택을 비교해야 한다.
결과를 보기 전에 표본·oracle evidence·평가 규칙을 고정하고 성공 사례만 골라
전체 성능으로 보고하지 않는다. 현 결과는 사례 선정의 필요성을 보여줄 뿐
자동 reviewer의 일반 성능 향상을 입증하지 않는다.

## 산출물

- `runs/context_pairs_v1/smoke_budget_fanout/`: pair, snapshots, static/witness receipts
- `runs/budget_fanout_packets_128.json`: 모델에 제공한 고정 입력
- `runs/budget_fanout_packets_64.json`, `_256.json`, `_512.json`: 비모델 sensitivity
- `runs/budget_fanout_review_128/`: 최초 실행, 격리 실패와 제외 사유 보존
- `runs/budget_fanout_review_128_isolated/`: 최종 실행, manifest, raw traces, 응답
- `runs/budget_fanout_review_128_isolated/author_judgments.json`: 명시적 평가
- `runs/budget_fanout_review_128_isolated/analysis.json`: 최종 집계와 응답 hash
- `ace_review/budget_packets.py`: 실제 source budget selector와 packet 생성

Source budget 단위·closure·ranking 한계는 BUDGET_FANOUT_RETRIEVAL_AUDIT_KO.md,
사전 실행 조건은 BUDGET_FANOUT_PILOT_PLAN_KO.md에 기록했다.
