# 필요한 문맥이 실제 리뷰 판단을 개선하는가: 72회 완료

2026-09-03 · `gpt-5.6-sol` · 두 신규 family의 탐색적 비교

## 결론

**이번에는 관련 문맥을 제공했을 때 두 사례 모두 정확한 목표 탐지가 늘었다.** 같은 길이의 무관 문맥에서는 늘지 않았다. 다만 실제 production 변경 한 건과 합성 migration 한 건의 작은 결과이며, 수작업으로 고른 문맥이다. LSP/SCIP 기반 자동 검색이나 새로운 review 알고리즘의 contribution을 입증한 결과는 아니다.

| 입력 조건 — reviewer는 모두 일반 2단계 | Click pager | AnyIO 옵션 전달 |
|---|---:|---:|
| Diff-only | 1/3 | 1/3 |
| Diff + 길이를 맞춘 무관 문맥 | 0/3 | 0/3 |
| Diff + 관련 문맥 | **2/3** | **3/3** |

숫자는 unsafe의 목표를 **정확한 메커니즘으로 탐지하면서**, 같은 반복의 safe에서는 그 목표 메커니즘을 잘못 주장하지 않은 횟수다. 모델을 세 번 반복한 것이지 독립 PR 세 개가 아니다. 정확한 원인을 설명하지 못한 “비슷한 오류”는 목표 정답으로 세지 않았다.

기계 집계, 72회 완료·실행 오류 0 기록, 전체 annotation.

## 1. 이번 비교가 이전과 다른 점

이전 실험은 같은 source에서 일반 가설과 protocol 가설을 비교해 특화 방식의 이득을 보이지 못했다. 이번에는 **일반 가설 생성→공통 verifier를 고정하고, 추가 repository source만 변경**했다. Requests 직렬화와 requests-cache namespace 사례는 재사용하지 않았다.

두 family × 두 companion 상태 × 세 문맥 조건 × 세 반복 × 두 stage = 72회 호출이다. 최종 리뷰는 36회다. Source/계획/prompt/schema를 결과 전에 동결했고, 실행 witness와 수정 PR 설명은 reviewer에게 주지 않았다. 관련 source는 target을 아는 상태의 수작업 선택이므로 자동 retrieval이 아닌 증거 활용 실험이다.

관련/무관 문맥의 reference tokens는 Click 772/769, AnyIO 관련 safe 647·unsafe 629/무관 646이다. 최대 길이 차이는 약 2.7%다. Native token이나 모델 내부 reasoning 비용까지 맞춘 것은 아니다. 관련 source는 여러 함수 block, 무관 source는 하나의 파일 prefix여서 block 수/완결성도 완전히 같지는 않다.

실행 전 계획, 메소드와 재현, 입력·준비 이력.

## 2. 실제 실행으로 확보한 정답

### Click: 실제 production 변경 + 구성한 safe companion

실제 [`1aa2d53`](https://github.com/pallets/click/commit/1aa2d53d63ff68bf14b35931177aac9270e39713) 변경은 stream의 binary/text 판별을 바꾼다. Unchanged `_tempfilepager`가 binary temporary file을 만들면 새 판별이 이를 text wrapper로 감싸지 못하고, `echo_via_pager`의 문자열 쓰기가 실패한다. Safe에서는 이후 [#3739](https://github.com/pallets/click/pull/3739)의 text-mode companion 수정을 두 시점에 이식했다.

두 base와 safe head는 문자열 세 종류를 정상 기록했고, unsafe head만 `bytes-like object`가 필요한데 `str`을 썼다는 TypeError를 냈다. 이 commit의 production 변경은 모두 포함했지만 테스트 파일 변경은 제외했다. 전체 PR benchmark가 아니며 safe 쌍은 구성한 것이다.

macOS에서 WIN/TTY dispatch를 강제해 실제 tempfile 경로를 실행하고, 표시 subprocess만 stub으로 바꿨다. **Native Windows 검증은 아니다.** 4-cell 실행 증거, 원문 production Diff.

### AnyIO: 실제 adapter 결함 + 합성 migration

`trio.run(..., **backend_options)`를 `anyio.run(..., backend="trio", backend_options=...)`로 바꾸는 합성 client 변경이다. [#1161](https://github.com/agronholm/anyio/pull/1161) 전의 실제 `TrioBackend.run`은 전달받은 options를 사용하지 않고, 수정된 구현은 이를 `trio.run`에 넘긴다.

주입한 MockClock의 객체 identity와 시간을 세 설정에서 검사했다. 두 base와 safe head는 요청한 clock을 사용했고, unsafe head만 다른 기본 clock을 사용했다. 실제 adapter를 실행했지만 **자연 bug-introducing client PR은 아니다.** 4-cell 실행 증거, 합성 migration Diff.

두 사례의 payload 세 개는 각각 같은 결함 내부 검사다. 환경 오류나 import 실패를 정답 양성으로 세지 않았다. AnyIO의 중간 준비본은 문맥 길이/binding 검증 과정에서 보존했고, 모델 호출은 마지막 동결 입력에서만 했다.

## 3. 정확한 원인과 틀린 설명을 분리했다

Click에서 반복된 잘못된 설명은 “`isinstance(stream, typing.BinaryIO)` 자체가 TypeError를 낸다”였다. 실제 CPython 3.14.6 검사는 예외 없이 **False**를 반환한다. 오류는 그다음 **감싸지 않은 binary stream에 str을 쓸 때** 발생한다.

이 차이는 실행 전 목표 정의에 포함된 메커니즘이다. 같은 TypeError 단어, 비슷한 위치, 비슷한 수정 제안만으로 정답을 인정하지 않았다. 출력 이후 추가한 독립 runtime probe는 잘못된 설명을 확인하기 위한 진단이며 모델 입력으로 제공하지 않았다.

최종 지적 17개 중 7개는 이 타입 검사 예외를 잘못 주장했고, 1개는 AnyIO가 clock을 무시하는 대신 startup TypeError로 거부한다고 잘못 주장했다. 이 8개를 정확한 목표 메커니즘 탐지와 분리했다. 나머지 9개는 목표 메커니즘을 언급했지만, 그중 2개는 safe에서의 오주장이다. 따라서 실제 unsafe 목표 탐지는 총 7개다.

관련 문맥의 Click 최종 출력은 첫째/셋째 반복에서 잘못된 가설을 명시적으로 반박하고 실제 str-write 원인으로 고쳤다. 둘째 반복은 고치지 못했다. Source가 있어도 언어/runtime 사실에 대한 잘못된 믿음이 남을 수 있다는 결과다.

## 4. 안전한 문맥에서의 잘못된 경고

주 지표의 “정확한 목표 메커니즘”만 세면 잘못된 원인으로 낸 경고가 빠진다. 이를 숨기지 않기 위해, 실행으로 정상임을 확인한 같은 target 경로가 실패한다고 주장한 경고를 보조 진단으로 함께 세었다. 여기에는 위 타입 검사/옵션 거부 오류도 포함한다.

| 조건 | Click safe의 경로 오경고 | AnyIO safe의 경로 오경고 |
|---|---:|---:|
| Diff-only | 2/3 | 1/3 |
| 무관 문맥 | 1/3 | 1/3 |
| 관련 문맥 | 0/3 | 0/3 |

이것은 임의의 다른 버그까지 모두 판단한 전체 review precision이 아니라, witness로 확인한 target 경로의 경고 진단이다. 이번 관련 문맥의 개선은 탐지뿐 아니라 **안전한 companion을 보고 잘못된 경고를 내지 않은 것**에도 나타났다. 보조 진단·출력 위치.

## 5. 병목은 어디에 있었나

| Family / 문맥 | unsafe의 정확한 목표 가설 생성 | 전달된 목표를 최종 유지 | 정확한 목표 가설 없이 최종 발견 |
|---|---:|---:|---:|
| Click / Diff-only | 3/3 | 1/3 | 0 |
| Click / 무관 | 0/3 | 0/0 | 0 |
| Click / 관련 | 0/3 | 0/0 | 2 |
| AnyIO / Diff-only | 2/3 | 0/2 | 1 |
| AnyIO / 무관 | 2/3 | 0/2 | 0 |
| AnyIO / 관련 | 3/3 | 3/3 | 0 |

가설 payload 예산 때문에 탈락한 항목은 0개다. Click 관련 문맥의 0/3은 “아무 가설도 없었다”가 아니라 **동일 경로에 대한 틀린 isinstance 예외 가설만 있었다**는 뜻이다. 그중 두 번은 verifier가 원인을 수정했다. 반대로 Diff-only는 정확한 가설을 세 번 만들고도 두 번 잘못된 설명으로 바뀌었다.

AnyIO는 Diff-only/무관 문맥에서 옵션 가설이 있어도 최종 탐지로 유지되지 않은 경우가 있었다. 관련 문맥은 실제 options 수신→미사용→`trio.run` 호출을 보여줘 세 번 모두 최종 지적으로 이어졌다. 다만 verifier가 빈 결과를 낸 내부 이유는 출력되지 않아, 확신 부족/기각/누락 중 무엇인지 확정하지 않는다.

## 6. 동일 입력 대조군의 해석 주의

Diff-only와 무관 문맥에서 safe/unsafe의 proposal prompt와 static schema는 각각 **완전히 동일**하다. 독립적인 모델 호출 결과가 달라져 Diff-only에서도 우연히 1/3 쌍을 맞혔다. 이것을 보이지 않는 companion을 모델이 구분했다는 증거로 해석하면 안 된다. Verifier 입력은 각 호출의 proposal 때문에 달라질 수 있지만 그것도 처음에는 동일 정보에서 출발한 변동이다.

따라서 단순히 “1/3에서 3/3이 되었으니 일반 recall이 3배 향상”이라고 쓰면 안 된다. 이번에 관찰한 것은 **해당 실행으로 구분되는 작은 사례에서, 관련 source가 있을 때 목표 판단이 더 잘 맞고 경로 오경고가 줄었다**는 신호다. 통계적 일반화의 단위는 독립 family/PR이어야 한다.

## 7. 실행·품질 기록

- 모델 호출 72/72 완료, 실행 오류 0, 도구 호출 0, 출력 JSON schema 오류 0.
- 구조화된 `evidence_ids`의 잘못된 ID 0개. 다만 자유 서술에서 가설을 근거처럼 참조한 출력 1건이 있었다. Enum만으로 모든 근거 사용 오류가 사라지지는 않는다.
- 코드 테스트 **165개 통과**. 신규 세 모듈 statement coverage **322/348, 약 93%**, branch coverage **101/118, 약 86%**. `ecc-codex-playbook`의 테스트 우선 개발·코드 검토·문서 검증 절차를 적용했다.
- Source spans 22개, snapshot source 파일 14개의 masking/AST를 확인했다. 종료 시 source hash 변경 0, 동결 파일 49개 변경 0.
- Annotation은 이 세션 작성자의 전체 출력 검토다. 독립 인간 평가나 별도 judge 합의는 아니다.

statement coverage, branch coverage, source 검증, 종료 무결성, 전체 원문 출력.

## 8. Contribution 판단과 다음 단계

이 결과는 사용자가 원한 “Diff만으로 확정할 수 없는 결함을 필요한 context로 더 정확히 리뷰한다”는 방향에 **작은 긍정적 근거**를 준다. 하지만 다음 한계가 남는다.

1. 두 개의 새 family뿐이며, safe는 구성했고 AnyIO anchor도 합성했다. 자연 PR holdout 일반화와 모델 학습 데이터 비오염은 검증하지 않았다.
2. Source를 사람이 골랐다. BM25/LSP/SCIP나 paired-index selector가 이 증거를 실제로 찾아낼 수 있는지는 측정하지 않았다.
3. 길이만 근접하게 맞췄고 snippet 구조까지 같지는 않다. 관련성만의 순수 효과로 분해한 것은 아니다.
4. Click에서 보듯 필요한 source가 있어도 runtime 의미를 틀릴 수 있다. Source retrieval과 reasoning 검증은 별도 병목이다.

다음 우선순위는 **동일 reviewer/예산으로 자동 검색이 수작업 relevant source의 이득을 얼마나 회수하는지** 보는 것이다. 이번 두 사례는 이제 개발용으로 두고, 독립 자연 변경을 추가한 뒤 Diff-only → BM25 → point-query LSP/SCIP → paired-index evidence selection을 비교해야 한다. 가능하면 같은 수/길이의 무관 block 대조군과 독립 annotation을 포함한다. Runtime 의미 오류에는 작은 실행 사실을 추가하는 별도 조건을 둘 수 있지만, 그것을 source-only 방식의 성과로 합치면 안 된다.

현재 주장 가능한 범위는 **“고정된 2단계 reviewer에서, 두 선택된 context-dependent 사례의 관련 source가 무관 source보다 더 정확한 목표 판단을 유도했다”**이다. 자동 context 선택 방법의 독자적 contribution은 아직 보류다.
