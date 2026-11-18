# Reviewer packet 감사 — 2026-09-08

Safe/unsafe × 5개 조건의 reviewer 입력 10개를 생성했다. 동일 full diff,
동일 명시적 요구사항, 동일 source closure 규칙을 사용하고 전체 prompt 길이를
o200k_base 기준 479 tokens로 맞췄다. 모델 호출은 0회다.

## 결과

| 조건 | Safe 원문 prompt tokens | Unsafe 원문 prompt tokens | Padding 후 |
|---|---:|---:|---:|
| Diff only | 213 | 213 | 479 |
| Call source | 297 | 296 | 479 |
| Flow source | 297 | 296 | 479 |
| Flow source + correct relations | 463 | 462 | 479 |
| Flow source + shuffled relations | 463 | 462 | 479 |

소스 자체는 safe 87, unsafe 86 reference tokens다. Call-source와 flow-source는
각 world에서 소스뿐 아니라 padding을 포함한 최종 prompt도 byte-for-byte 같다.
Graph node-content 합계인 65→23 추정 토큰 감소가 이 packet 표현에서는 사라졌다.
Correct relations는 source-only에 166 reference tokens를 추가했다.

이는 현재 파일 단위 closure에서의 결과다. 더 정교한 statement slicing에서도
압축 효과가 없다는 뜻은 아니다. 이 control은 파일이 매우 작으며 선택된 파일을
통째로 제공하면 두 방식이 같은 코드가 된다.

## 입력 구성

- 선택은 기존 head graph와 diff-line seed로 한다. Gold로 source를 선택하지 않는다.
- 선택 node가 속한 파일을 정렬·중복 제거하고 head의 실제 전체 파일을 줄 번호와
  함께 제공한다. 작은 control에서 import와 signature 누락을 막는 보수적 규칙이다.
- 모든 조건에 같은 full diff를 제공한다. Diff-only safe/unsafe prompt도 동일하다.
- Correct/shuffled 조건은 source·endpoint·relation label 빈도가 같고 label 배치만
  다르다. 모든 label이 원래와 달라지는 회전을 사용한다. 이것은 단순 순서 변경이
  아니라 의도적으로 잘못된 relation을 주는 실험 조건이다.
- Padding은 evidence가 아닌 length control로 표시한다. Arm/world 이름, gold,
  witness 결과, snapshot 경로는 prompt에 넣지 않는다. 실행자는 JSON의 prompt
  필드만 모델에 제공해야 한다.
- Exact token matching은 o200k_base reference tokenizer 기준이다. 실제 모델의
  hidden/system overhead나 API 청구 토큰을 측정한 값은 아니다.
- SHA-256으로 pair, source, diff, contract, 최종 prompt를 추적한다.

## 요구사항의 출처

기존 repository source만으로는 “반드시 첫 번째 인자의 대소문자를 보존해야 한다”는
계약을 알 수 없다. Target 변경이 의도적인 기능 변경일 가능성을 배제할 수 없으며,
witness의 기대값만을 숨긴 채 일반 review를 시키면 평가 조건이 불충분하다.

따라서 연구자 작성 명세를 별도 파일 `runs/disambiguation_supplied_requirement.txt`에
두고 모든 조건에 동일하게 제공했다. 이는 명세가 주어진 review task로 범위를
바꾸는 것이므로 과거의 명세 없는 review 결과와 같은 실험으로 합산하면 안 된다.
명세는 first-argument 보존 규칙만 말하며 safe/unsafe 라벨이나 구체적 실패값은
공개하지 않는다. Source를 읽으면 현재 호출이 그 계약을 위반하는지 확인할 수 있다.

## 후속 reviewer 비교에서 고정할 것

Call-source와 flow-source의 입력이 같으므로 둘을 독립적인 retrieval 방법 차이로
평가하지 않는다. Source-only 하나로 합쳐 다음 네 조건을 비교할 수 있다.

1. Diff-only: 두 world의 추가 정보가 없는 기준선.
2. Source-only: 명세와 코드로 판단 가능한 정도.
3. 동일 source + correct relations: 관계 설명의 증분 효과.
4. 동일 source + shuffled relations: 잘못된 관계 설명에 대한 취약성.

평가 단위는 family의 safe/unsafe 쌍이다. Unsafe target mechanism correctness와
safe false alarm을 함께 기록하고, 위치 정확도는 별도로 측정한다. Unsafe에서는
선택 변경 때문에 두 번째 인자가 반환되어 첫 번째 인자 보존 계약을 어기는
메커니즘을 설명해야 한다. 단순히 primary→secondary 변경을 지적하는 것만으로
정답 처리하지 않는다. Safe에서는 두 값이 같은 현재 호출의 target defect를
보고하면 false alarm이다. Diff-only는 두 world를 구분할 정보가 없다는 점을
해석에 반영하며 반복 생성 성공을 독립 표본으로 세지 않는다.

이 control에서 새 retrieval contribution을 주장할 근거는 없다. 의미 있는 다음
retrieval 사례는 동일 chunk·직렬화 budget 아래 call-source가 필요한 source를
누락하고 flow-source가 그 source를 회수하는 경우여야 한다. Source packet의
실제 내용과 명세를 먼저 동결한 뒤 reviewer 평가를 실행해야 한다.

## 재현

```bash
PYTHONPATH=. python -m ace_review.evidence_packets \
  runs/context_pairs_v1/smoke_dataflow_disambiguation/pair.json \
  runs/disambiguation_supplied_requirement.txt \
  runs/provenance_evidence_packets_reproduction.json
```

생성 파일은 exclusive-create한다. 기존 frozen JSON을 덮어쓰지 않는다.
산출물: `runs/provenance_evidence_packets_20260908.json`.

검증: 10개 packet의 동일 diff, 동일 reference-token 길이, source 일치, label
derangement, world/gold metadata 누출 방지를 자동 검사했다.
