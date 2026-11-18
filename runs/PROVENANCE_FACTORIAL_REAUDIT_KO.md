# Disambiguation factorial 재감사 — 2026-09-08

지난 결과에서 “typed relation의 증분 정보 효과를 분리했다”는 해석을 정정한다.
확인된 것은 synthetic graph 경로 구성과 선택량 감소다. Call-only 소스만으로
판단할 수 없는 결함을 typed flow가 새로 판단하게 했다는 결과는 아직 없다.

## 발견된 세 가지 혼동

1. Untyped graph를 typed-edge 정답에 대조하면 edge/path recall은 정의상 0이다.
   이는 label 제거가 적용됐다는 검사이며, LLM이 관계 정보를 유용하게 쓴다는
   인과 증거가 아니다. Source text 자체에서 관계를 추론할 수도 있다.
2. Call-only에 target.consumer, routes.route, sink.emit의 함수 본문이 있다.
   따라서 별도 local/parameter node를 못 찾았더라도 필요한 소스는 포함된다.
   73.1% noise는 oracle node ID/역할 단위 집계다. 함수 본문 안의 관련 코드를
   통째로 irrelevant로 세므로 실제 무관한 코드 비율로 해석할 수 없다.
3. 기존 두 arm은 relation set과 seed granularity를 동시에 바꿨다. 이번에
   function/value seed × call/full graph를 교차해 이 혼동을 확인했다.

추가로 현재 audit는 head graph와 diff-line seed를 사용한다. Base graph와의
semantic delta 비교는 이 실험의 처리 변수가 아니므로 paired-index contribution
주장도 이 결과에서 도출할 수 없다.

## 추가 구현과 관측

동일 파일·revision에서 oracle node의 content가 선택 node 본문에 문자 그대로
들어 있는지 검사하는 source-visibility 진단을 추가했다. 이는 정보 누락을
의심할 때 쓰는 보조 지표다. 동일한 이름의 우연한 일치도 인정할 수 있으므로
semantic sufficiency나 실제 review correctness로 쓰지 않는다.

Budget 1200, depth 6, unsafe world 결과:

| Seed / graph | Typed-path recall | Oracle snippet containment | Node-content 추정 tokens |
|---|---:|---:|---:|
| Function / call | 0 | 1 | 52 |
| Function / full | 1 | 1 | 65 |
| Value / call | 0 | 0 | 0 |
| Value / full | 1 | 1 | 23 |
| Value / full 선택 후 label 제거 | 0 | 1 | 23 |

Value/call은 local alias seed에 incident call edge가 없어 빈 선택을 반환했다.
Function/full에서도 oracle path를 복원했으므로 value seed 자체가 경로 복원에
필수인 것은 아니다. 이 사례에서는 value seed가 선택량을 줄였다.

선택된 typed snippets에는 단독 parameter 이름 등 주변 signature가 생략된
조각도 있다. 23 tokens짜리 소스 집합만으로 안전하게 review 가능한지는 아직
확인하지 않았다. Edge, symbol, 위치, diff 및 prompt 직렬화 비용도 집계에서
빠지므로 65→23을 실제 LLM 입력 토큰 절감률이라고 부르면 안 된다.

## 36-cell 민감도 실험

Safe/unsafe 두 world × budget {16,24,32,64,128,1200} × depth {3,4,6}를 실행했다.
하나의 재사용 synthetic family에서 나온 설정 조합이며 독립 표본 36개가 아니다.

Unsafe, depth 6에서:

| Budget | Call path / snippet | Function-full path / snippet | Value-full path / snippet |
|---|---|---|---|
| 16 | 0 / 0 | 0 / 0 | 0 / 0.667 |
| 24 | 0 / 0 | 0 / 0 | 1 / 1 |
| 32 | 0 / 0.667 | 0 / 0.667 | 1 / 1 |
| 64 | 0 / 1 | 0 / 1 | 1 / 1 |
| 128 | 0 / 1 | 1 / 1 | 1 / 1 |
| 1200 | 0 / 1 | 1 / 1 | 1 / 1 |

전체 설정에서 value/full은 30/36, function/full은 4/36에서 typed path가 완전했다.
Call arm은 18/36 설정에서 모든 oracle source snippet을 이미 포함했다.
선택기, 함수/statement chunk granularity와 depth의 영향을 포함한 관측이며
통계적 우월성이나 자연 PR 일반화 주장은 하지 않는다.

## 다음 검증의 구체적 조건

1. 동일 diff를 모든 reviewer packet에 항상 포함하고 location/signature를 포함한
   실제 직렬화 packet의 토큰을 센다. 현재 diff-only graph projection은 full diff가 아니다.
2. Chunk granularity를 고정한 function/value seed 비교와 head-only/delta seed 비교를
   교차한다. 관계 제거 실험에서도 같은 seed, endpoint 규칙, budget을 유지한다.
3. 같은 선택 소스에 대해 source-only, correct typed relations, token-matched shuffled
   relations를 비교한다. 평가는 typed-path 지표가 아니라 safe/unsafe의 target
   mechanism correctness와 false alarm이다. Shuffled relation은 실험용 오정보
   control임을 기록하고 실제 reviewer 기능에는 사용하지 않는다.
4. 부족한 source를 찾아오는 retrieval 효과와 이미 있는 source를 압축하는 효과를
   분리한다. 반환값 chain, field alias, resource write-read 사례는 actual source packet을
   먼저 검사하고 annotation을 고정한 후 평가한다.

현재 contribution 후보는 예산 제약 아래의 evidence 압축 및 선택이다. Defect recall
향상이나 base/head index delta의 필요성은 위 비교에서 입증해야 한다.

## 재현

```bash
PYTHONPATH=. python -m ace_review.disambiguation_sweep \
  runs/context_pairs_v1/smoke_dataflow_disambiguation/pair.json \
  runs/provenance_factorial_sweep_reproduction.json
```

기존 output을 덮어쓰지 않으므로 새 경로를 지정한다.

- 단일 결과: `runs/provenance_disambiguation_factorial_20260908.json`
- 전체 설정 결과: `runs/provenance_factorial_sweep_20260908.json`
- Model calls: 0
- 과거 frozen 결과는 보존했고 해석 정정을 원 보고서 상단에 추가했다.
