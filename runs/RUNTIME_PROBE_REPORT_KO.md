# P2-B 실행 결과: 작은 runtime 관찰

2026-09-07. `runtime_probe1`. 모델 호출 0회. 기능 관찰 5개와 격리/timeout 검사 2개를 완료했다. 기능 관찰마다 별도 격리 self-test도 수행한다.

## 결과

| 관찰 | 결과 | 해석 범위 |
|---|---|---|
| CPython 3.14.6, binary TemporaryFile에 `isinstance(..., typing.BinaryIO)` | False, 예외 없음 | 해당 타입 predicate 자체가 TypeError를 낸다는 기존 설명을 반박 |
| AnyIO backend 선택 | 네 snapshot 모두 TrioBackend | 지정 `backend="trio"` 입력에서 실제 선택된 구현 확인 |
| AnyIO→adapter 경계 | 네 snapshot 모두 동일 mapping과 clock sentinel 도달 | 정규화·분기 이후 고정 nonempty 입력의 전달 확인 |
| Safe adapter→하위 Trio 호출 | base/head 모두 동일 clock sentinel 전달 | 고정 인자에 대한 kwargs forwarding 확인 |
| Unsafe adapter→하위 Trio 호출 | base/head 모두 clock 항목 누락 | 이 adapter body의 옵션 미전달 관찰 |

Unsafe base도 누락하는 이유는 **client diff 전체가 아니라 AnyIO adapter 자체를 검사했기 때문**이다. Base client가 원래 Trio를 직접 호출하는 경로까지 이번 probe가 재실행한 것은 아니다. 이것을 ‘base에서도 동일 client 결함이 존재했다’는 결과로 해석하면 안 된다.

## 이전 불확실성 중 해결된 부분

P2-A의 SCIP 연결은 Trio/AsyncIO 양쪽을 가능한 구현으로 남겼다. 이번 관찰은 지정 backend literal과 runtime에서는 Trio 구현이 선택되며, 고정 옵션 mapping이 그 경계까지 도달한다는 사실을 추가한다. Safe/unsafe adapter의 서로 다른 전달 동작도 원래 method를 사용해 관찰했다.

이는 **불확실한 정적 연결을 특정 입력의 실행 관찰로 보완할 수 있다는 구현상의 결과**다. 모든 입력·분기·동적 환경에서 연결을 확정한 것이 아니다. P2-A의 동결 audit를 소급 수정하지 않고 별도의 runtime evidence로 저장했다.

Click은 기존 잘못된 predicate-raises 설명을 반박할 수 있게 됐다. 그러나 동일 predicate 결과가 safe/unsafe 양쪽에 적용되므로 이 관찰만으로 두 상태를 구별할 수 없다. Producer의 실제 반환 객체와 consumer의 쓰기 계약은 여전히 추가 source/관찰이 필요하다.

## 하지 않은 것

- 실제 사용자 job, Trio scheduler, 유효한 custom clock 설치/시간 동작 실행.
- Click 전체 pager 경로 실행.
- 완전한 client base/head behavior 비교.
- 모델 리뷰 성능 측정, semantic/strict F1 또는 recall 향상 검증.
- 모델 질문에서 자동으로 일반적인 실행 검사를 생성하는 시스템.

AnyIO-specific fixed template와 Python type predicate template이다. 실제 clock 대신 sentinel 객체를 사용하고 하위 호출을 가로챘으므로, 전달 여부와 clock의 의미적 유효성을 구분해야 한다. 기존 개발 사례에 맞춘 탐색이며 독립 holdout이나 신규성 입증이 아니다.

## 검증과 환경

- CPython 3.14.6, Trio 0.32.0, 기존 `venv-context-runtime` 사용.
- 네트워크 connect·scratch 밖 쓰기·child process 생성을 OS 수준에서 EPERM으로 차단했고 scratch 쓰기는 허용됨을 확인했다.
- 60초 sleep self-test를 0.5초 timeout으로 종료했다. 이 timeout은 예상된 성공 조건이며 기능 검사 실패가 아니다.
- 12개 새 테스트를 추가했다. 테스트 우선 절차의 최초 실패, 구현 후 통과를 확인했다.
- 최종 프로젝트 테스트 **227개**와 새 코드 lint가 통과했다. 검증 기록.
- 부모 테스트와 실제 subprocess line trace를 합친 module statement coverage는 81%다. 보수적으로 미계측 worker entry/일부 예외 경로는 uncovered로 남겼다. Branch coverage와 별개다.
- 실행 초기에 indexing venv의 Trio 부재와 Anaconda의 C locale 초기화 오류를 발견했다. 기존 실험 runtime과 UTF-8 locale로 수정한 후 formal 입력을 동결했다. 격리 정책을 완화하지 않았다.
- Same-snapshot 개발 테스트를 formal 동결 전에 실행했음을 provenance에 명시했다. Blind 실험으로 주장하지 않는다.

파일 읽기는 허용된 정책이므로 완전한 보안 sandbox가 아니다. Probe 입력에 gold/witness 설명을 전달하지 않았고, 코드에서 이를 읽지 않는다. Host에서 source manifest·runtime provenance를 확인하는 작업과 기능 probe 자체를 구분한다.

## 다음 비교 실험

다음은 **동일한 source와 proposal을 고정하고 runtime evidence만 추가하는 대조**다. 새 source selection 효과와 probe 효과를 섞지 않는다.

1. Source-only.
2. 같은 source + 일반 환경 관찰 대조.
3. 같은 source + 이번 targeted 관찰.

추가 evidence 길이와 실행/리뷰 비용을 통제하고, AnyIO exact-mechanism hit와 Click wrong-mechanism 경고·safe-path 경고를 함께 측정한다. 네 입력 × 기존 proposal 3개 × 세 조건이면 final 리뷰 36회이며, 실제 실행 전에 조건·prompt·최종 입력을 다시 동결한다. 현재는 이 모델 비교를 아직 실행하지 않았다.

## 산출물

- 전체 관찰과 원본 stdout/stderr
- 평가 label을 제외한 runtime evidence
- 완료 기록
- 동결 실행 계획
- 구현 설명
