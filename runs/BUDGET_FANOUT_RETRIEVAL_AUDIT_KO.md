# Fanout retrieval 감사

12개 unchanged audit 호출을 추가한 synthetic control에서 source retrieval을
실제 직렬화 예산으로 비교했다. Call/full-function seed와 flow/value seed는
같은 file closure·budget 적용을 사용한다. Graph ranking은 path 거리,
serialized source 비용, filename 순이다. BM25는 diff+현재 route 명세를 query로 쓴다.

모든 source는 파일 이름·줄 번호를 포함한다. Target 전체 17줄은 full diff에
공통 포함하므로 추가 source에서 중복 제외한다. 본 구현의 target 제외 규칙은
이 control처럼 diff가 target 전체를 제공할 때에만 closure가 완전하다.

| Source budget | Call: route 회수 | Flow: route+sink | BM25: route+sink |
|---|---|---|---|
| 64 | 아니오 | 예 | 예 |
| 128 | 아니오 | 예 | 예 |
| 256 | 아니오 | 예 | 예 |
| 512 | 예 | 예 | 예 |

128 예산에서 call은 sink+audit 5개, flow는 route+sink, BM25는 route+sink+audit
4개를 선택했다. Unsafe 소스 token 수는 각각 111, 48, 124다. Safe는 111,49,125다.
Reviewer packet 전체는 공통 diff·명세·padding을 포함해 518 reference tokens다.

후속 비모델 진단에서 BM25 top-2는 safe/unsafe 모두 routes.py와 sink.py를
선택했다. 즉 flow와 같은 소스를 49/48 tokens로 제공할 수 있다. 이 진단은
reviewer main arm을 바꾸지 않는 post-hoc sensitivity check다. 따라서
flow의 48 vs BM25의 124만 보고 구조적 압축 우위를 주장할 수 없다.
BM25가 예산까지 채우느냐 top-k에서 멈추느냐에 의한 차이도 포함되어 있다.

이 실험은 짧은 callee를 우선하는 특정 call baseline에 불리하게 구성됐다.
실제 LSP 제품 또는 semantic index 일반의 한계라고 해석하지 않는다.
Flow와 call은 seed도 바뀌므로 typed-edge 단독 효과가 아니다.
Base/head delta 선택도 이 실험에 포함되지 않는다.

기존 case의 literal 값·계약을 재사용한 파생 control이며 독립 자연 PR 표본이
아니다. Four-cell witness는 safe base/head pass, unsafe base pass/head
target_failure를 확인했다. Static gate도 통과했다.

실행 입력: runs/budget_fanout_packets_{64,128,256,512}.json
Source budget sweep 결과를 본 뒤 128 결과의 모델 입력을 변경하지 않았다.
24-call 사전 조건: BUDGET_FANOUT_PILOT_PLAN_KO.md.
