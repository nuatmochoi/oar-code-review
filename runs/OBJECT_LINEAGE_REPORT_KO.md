# 실제 production 객체 관찰: 18회 후속 대조

2026-09-07. 개발 run `object_lineage1`, `lineage_review1`.

## 결론

**실제 production 경로의 타입·분기 관찰을 주면 결함 탐지를 유지하면서 정상 사례 오경보가 줄었다. 그러나 명시적 객체 동일성 필드는 추가 이득이 없었다.**

| 조건 | Unsafe 목표 메커니즘 탐지 | Safe 관련 경로 오경보 | 같은 반복에서 탐지 + 정상 무경고 |
|---|---:|---:|---:|
| 별도 파일 predicate 관찰 | 3/3 | 2/3 | 1/3 |
| Production 타입·분기 관찰 | 3/3 | 0/3 | 3/3 |
| 위 관찰 + 객체 동일성 필드 | 3/3 | 0/3 | 3/3 |

각 `/3`은 **같은 Click 개발 사례의 세 proposal/모델 응답**이다. 독립 PR 3개가 아니며, 새로운 holdout도 아니다. 사전 계획의 production package gate는 통과했고 explicit identity gate는 통과하지 못했다. 18개 원문 전체, 지적 11개를 저자가 직접 읽고 항목별 판정했다. 독립·블라인드 평가 또는 AACR 전체 F1 결과는 아니다.

## 실제로 관찰한 것

검증된 source snapshot에서 실제 `echo_via_pager`를 호출했다. 실제 `_tempfilepager`가 반환한 객체를 그대로 분기와 consumer에 전달하며 proxy로 치환하지 않았다. AST로 첫 `pager.write(text)`의 실행 위치를 찾고 **그 문장 직전에 중단**했다. 실제 쓰기 실패·성공 결과를 reviewer에게 주지 않았다.

| Snapshot | 실제 producer의 내부 파일 타입 | Consumer 타입 | Consumer가 producer와 동일 객체인가 |
|---|---|---|---|
| Safe base | TextIOWrapper | MaybeStripAnsi | 아니오 |
| Safe head | TextIOWrapper | MaybeStripAnsi | 아니오 |
| Unsafe base | BufferedWriter | MaybeStripAnsi | 아니오 |
| Unsafe head | BufferedWriter | _TemporaryFileWrapper | 예 |

네 producer의 외부 타입은 모두 `_TemporaryFileWrapper`다. 기존 probe가 따로 만든 `TemporaryFile(mode='w+b')`의 `BufferedRandom`과는 다르다. **파일 종류뿐 아니라 실제 반환 wrapper를 확인한 점**이 중요하다.

Head의 실제 분기 관찰:

- Safe: producer의 `.buffer` 검사가 True → MaybeStripAnsi 변환 → str consumer 도달. nominal BinaryIO 검사는 실행되지 않았다.
- Unsafe: `.buffer` 검사 False, `isinstance(stream, typing.BinaryIO)` False → 원래 binary wrapper가 그대로 str consumer에 도달.
- Consumer 인자 타입은 모두 str이다. 쓰기는 실행하지 않았으므로 이후 TypeError는 관찰 자체가 아니라 source/런타임 타입 계약으로부터의 추론이다.

base/head 4개는 probe 검증에 사용했고 **이번 모델 입력에는 head 관찰만 제공했다**. base/head runtime delta 선택의 추가 효과를 실험한 것이 아니다.

## 비교 통제

- Click safe/unsafe × 기존 proposal 3개 × 3조건 = 새 final review 18회. 새 proposal 생성 없음.
- 기존 answer-source의 source 내용·순서·ID와 proposal을 각 task/repeat에서 공유했다.
- 모델 gpt-5.6-sol, 같은 verifier, 도구 비활성화, jobs=4, timeout=300초, 자동 재시도 없음.
- 전체 prompt reference token 수를 **세 조건 모두 동일하게** 맞췄다. 공개 inert padding이며 entropy/문체까지 같은 placebo는 아니다.
- Production-types는 lineage payload에서 consumer/producer 동일성 두 필드와 predicate 인자의 동일성 필드만 제거했다. 역할별 타입과 실행 범위는 유지한다. 따라서 이 대조는 provenance 전체 제거가 아니라 **명시적 identity boolean의 추가 효과**를 본다.
- 별도 predicate와 production 관찰의 비교는 타입·분기·도달 경로가 함께 달라지는 package 비교다.
- Probe 코드·계획은 정식 probe 실행 전, reviewer 입력·schema·코드·평가 규칙은 18회 호출 전에 동결했다. 개발 snapshot 테스트는 동결 전에 수행했음을 provenance에 표시했다.
- Generation/schema 오류 0, 도구 호출 0. 인용 ID enum 검증은 의미적 entailment를 보장하지 않는다.

## 오류 분석과 해석 경계

별도 predicate 조건의 safe 경고 두 개는 별도 binary TemporaryFile 관찰을 production 파일에 확대 적용했다. Unsafe 세 경고도 binary/str 메커니즘은 맞지만 생산자를 TemporaryFile/BufferedRandom으로 잘못 묘사했다. `T`는 이 **목표 메커니즘 일치**를 뜻하며 세부 사실 전부가 정확하다는 점수는 아니다. W는 사전에 정한 predicate 자체가 예외를 낸다는 오류만 세므로 W=0을 모든 설명 오류 0으로 읽으면 안 된다.

Production 두 조건은 unsafe 실제 wrapper를 올바르게 설명하고 safe에서 지적을 내지 않았다. 하지만 types와 lineage가 동일하므로 **identity 추적 고유 기여**, 최소 evidence subgraph의 우위, SCIP/CodeQL 고유 이득을 주장할 수 없다.

또한 기존 source 선택의 대표 입력에는 `_tempfilepager` producer body가 빠져 있었다. 따라서 현재 개선은 **빠진 producer 코드를 정적 검색으로 보충하는 것만으로도 얻을 수 있는 효과**일 수 있다. 이번에는 strongest static-source 대조도, diff-only 대조도 새로 실행하지 않았다. ‘runtime이 반드시 필요하다’ 또는 ‘diff-only 대비 일반 recall 개선’은 아직 입증되지 않았다.

## 실행 제한과 검증

macOS CPython 3.14.6에서 WIN=True, isatty=True, PAGER=/bin/cat, color=False로 경로를 고정했다. 네이티브 Windows 테스트가 아니다. OS sandbox의 network/scratch 밖 쓰기/fork 차단을 각 probe 전에 실제 negative self-test로 확인했다. 파일 읽기는 허용되므로 비밀정보 격리나 임의 악성 저장소용 sandbox가 아니다. 중단 후 정리/flush는 일어나지만 consumer write와 pager subprocess는 실행하지 않는다.

ecc-codex-playbook의 테스트 우선 절차로 새 모듈 미구현 실패(RED)를 확인한 뒤 구현·실행(GREEN)했다. 기존 동결 코드는 수정하지 않았다. 전체 프로젝트 테스트 **247개**, 새 코드 lint 통과. Statement coverage는 probe 82%, runner 98%, analyzer 88%로, 부모 coverage와 실제 worker line trace를 합산했다. 실제 실행되었어도 trace callback 자체 등 미계측 부분은 보수적으로 uncovered 처리했으며 branch coverage가 아니다.

## 비용

| 조건 | 새 호출 | Prompt reference tokens 합 | 출력 JSON reference tokens 합 | 호출 시간 합(초) |
|---|---:|---:|---:|---:|
| 별도 predicate | 6 | 13,158 | 1,423 | 82.7 |
| Production types | 6 | 13,158 | 911 | 116.9 |
| Production lineage | 6 | 13,158 | 980 | 83.2 |

Reference tokenizer 수치이며 billed/reasoning token은 아니다. 병렬 호출 시간의 합은 전체 경과시간이 아니다. 과거 proposal/source/index 준비 비용은 포함하지 않았다.

## 다음 우선순위

1. **강한 정적 대조부터**: 동일 producer body와 경로 선택 body를 budget 내에서 추가한 static source vs production-types. Producer를 수동 지정한 진단과 자동 retrieval 성공을 구분한다. 여기서 차이가 없으면 runtime 추가보다 producer 회수 실패가 핵심이다.
2. Runtime 추가가 실제 필요한 조건만 분리: 같은 타입이지만 값·선택 분기가 달라지는 alias/rebinding, adapter 선택, 옵션 필터링. 이미 이력을 본 사례를 새 holdout으로 쓰지 않는다.
3. 정책과 예산을 고정한 뒤 새 저장소 safe/unsafe 쌍에서 평가. 같은 diff에 companion만 바뀌는 쌍의 구분, unsafe 탐지와 safe 오경보를 함께 측정하고 독립 판정을 추가한다.

위 추가 실험은 아직 미실행이다. 현재 주장 가능한 좁은 관찰은 **독립적인 runtime 상식보다 실제 production 경로에 묶인 근거가 이 개발 사례의 과도한 일반화를 줄였다**는 것이다. 특정 graph/identity 방법의 contribution은 보류한다.

## 산출물

- 호출 전 고정 계획
- 실제 4-cell 관찰
- 18개 원본 리뷰
- 항목별 저자 판정
- 집계와 비용
- 검증 기록
- 결과 무결성 해시

재검증: `python -m pytest tests -q`. 이미 실행된 run에 prepare/run을 다시 수행하면 exclusive-write 보호로 거부된다. 새로운 모델 재실행은 별도 run 경로와 새 계획이 필요하다.
