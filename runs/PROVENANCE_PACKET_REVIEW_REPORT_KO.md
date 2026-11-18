# Evidence packet reviewer pilot — 2026-09-08

24회 reviewer 실행이 모두 정상 완료됐다. Source-only, correct relations,
shuffled relations는 unsafe의 실제 결함 메커니즘을 모두 3/3 설명했다.
사전에 고정한 현재 route 기준 safe alarm은 각각 1/3, 0/3, 2/3이었다.
관계 정보가 판정을 안정시킬 가능성을 보여주는 작은 관측이며, 증분 defect
recall이나 새로운 retrieval 기여를 입증한 결과는 아니다.

## 고정한 실험

- Family: 기존 smoke_dataflow_disambiguation 한 건. 연구자가 알고 있는 synthetic case.
- Model: gpt-5.6-sol, reasoning effort low, 동일 설정, 3회 반복.
- 4개 조건 × safe/unsafe × 3회 = 24 calls. 순서는 고정 seed로 섞었다.
- Call-source와 flow-source는 byte-identical하므로 flow_source 하나로 합쳤다.
- 명시적 명세와 full diff는 동일하다. Correct/shuffled 조건은 source가 같고
  relation label 배치만 다르다. 생성된 prompt는 모두 479 o200k_base reference tokens.
- 임시 빈 작업 디렉터리에서 실행했다. 기존 runner와 같은 도구 disable 목록을
  적용했고 trace에 감지된 tool-call event는 0이다. Raw stdout/stderr를 보존했다.
- 출력은 introduced_defect / no_supported_defect / insufficient_evidence와
  설명·file·line을 포함하는 동일 schema로 제약했다.
- 평가자는 이 실험을 진행한 assistant다. 원문 24개를 읽고 판단했으며
  독립 평가자 또는 blinded adjudication은 아니다.

## 결과

| 조건 | Unsafe positive verdict | 실제 unsafe 메커니즘 설명 | Safe alarm | Paired grounded success |
|---|---:|---:|---:|---:|
| Diff only | 2/3 | 0/3 | 3/3 | 0/3 |
| Source only | 3/3 | 3/3 | 1/3 | 2/3 |
| Source + correct relations | 3/3 | 3/3 | 0/3 | 3/3 |
| Source + shuffled relations | 3/3 | 3/3 | 2/3 | 1/3 |

Paired grounded success는 동일 repeat에서 unsafe 실제 메커니즘을 설명하고
safe에서는 경고하지 않은 경우다. Repeat 번호는 생성 식별자이며 공통 난수에
의한 엄밀한 짝짓기나 독립 family 표본을 뜻하지 않는다.

Unsafe의 grounded mechanism은 실제 routes.py의 서로 다른 두 값,
secondary 선택 변경, unchanged emit의 반환을 연결하여 UserID 대신 userid가
반환됨을 설명한 경우로 판정했다. Positive verdict 자체와 별개다.

Diff-only의 2회 positive는 인자가 다를 수 있다는 일반적 경고였다. 해당 prompt에는
route와 sink 구현이 없으므로 현재 unsafe world의 실제 메커니즘을 근거 있게
확인한 것으로 처리하지 않았다. 나머지 1회는 추가 코드가 없다는 이유로
insufficient_evidence였다. Safe/unsafe diff-only prompt가 동일함에도 판정 비율이
다른 것은 샘플링 변동이며 world 구분 능력의 증거가 아니다.

Unsafe source/relations 조건의 정답 9개는 모두 target.py:4를 지목했다.
관계 label이 잘못돼도 source를 통해 unsafe 결과를 설명했다. 따라서 잘못된
관계가 항상 메커니즘 추론을 망가뜨린다는 주장도 지지하지 않는다.

## Safe alarm의 범위 문제

Source-only review_09와 shuffled review_03, review_11은 현재 route의 두 인자가
같다는 사실을 정확히 인정했다. 그럼에도 명세가 서로 다른 secondary도 허용한다는
문구를 근거로 더 일반적인 계약 위반을 지적했다. 이들은 단순 source 오독이 아니다.

사전 프로토콜은 현재의 고정 route만 평가하므로 이 세 건을 safe alarm으로
유지했다. 다만 명세를 미래 입력이나 consumer 일반 계약까지 넓혀 읽으면
경고에 이유가 있다. 따라서 correct-relations의 0/3 alarm은 관계가 필요한
정보를 새로 제공했기 때문이라고 결론 내릴 수 없다. 범위 해석과 생성 변동이
대안 설명으로 남는다.

후속 실험에서는 명세에 “제공된 현재 revision의 route()만 평가하고, 다른
호출자·미래 입력·일반 consumer API 계약은 판단 대상에서 제외한다”는 범위를
명시해야 한다. 이는 새로운 prompt version으로 동결하고 기존 결과를 대체하지
않아야 한다.

## 토큰과 실행 범위

479는 공개 prompt의 reference tokenizer 길이다. 실제 CLI의 turn.completed에서
보고한 입력은 호출당 12,186–13,775 tokens였다. CLI의 추가 prompt overhead를
포함한 값으로 전체 모델 입력 길이까지 같았다고 주장할 수 없다.

- Reported input tokens 총합: 298,876
- 그중 cached input tokens: 45,056
- Reported output tokens: 3,270
- 별도 reported reasoning output tokens: 858

이 항목들은 trace 필드 그대로이며 output과 reasoning을 별도로 더해 과금량을
추정하지 않았다. 실제 비용 계산은 수행하지 않았다.

## Contribution 판단과 다음 우선순위

1. Source context가 현재 route를 판별하게 돕는 신호는 있다. 사례가 하나이고
   인위적인 명세가 제공됐으므로 일반화 주장은 보류한다.
2. Correct relations는 이번 반복에서 safe alarm을 줄였지만 unsafe recall은
   source-only와 같다. 관계 설명의 안정화 효과를 확정할 근거는 부족하다.
3. Call/flow가 동일 source를 회수하는 사례이므로 automatic retrieval 기여와
   base/head semantic delta의 필요성을 검증한 실험은 아니다.
4. 우선 명세 범위를 고정한 후, 동일 직렬화 budget에서 두 selector가 실제로
   서로 다른 source packet을 만드는 새 family를 검증해야 한다. Graph node
   recall만으로 유효 사례를 선별하지 않는다.

현재 결과로 가장 정확한 표현은 “관련 source를 제공하면 구체적인 결함 설명이
가능해졌고, 관계 설명에 따라 safe 판단의 변동이 관측됐다”이다.

## 산출물

- `runs/packet_review_20260908/manifest.json`: 실행 전 고정 입력·설정·hash
- `runs/packet_review_20260908/review_00.json` 등: 24개 원문 응답
- `runs/packet_review_20260908/review_00.trace.json` 등: raw traces
- `runs/packet_review_20260908/completion.json`: 24/24 성공
- `runs/packet_review_20260908/author_judgments.json`: 응답별 author 평가
- `runs/packet_review_20260908/analysis.json`: 집계·응답 hash·usage
- `ace_review/packet_review.py`: 실행 코드
- `ace_review/packet_review_analysis.py`: 명시적 평가 기반 집계 코드

새 output 디렉터리에만 실행하며 기존 결과는 덮어쓰지 않는다. 실행 오류와
도구 사용 응답을 실패로 보존하는 회귀 테스트를 추가했고 관련 7 tests와 Ruff가
통과했다.
