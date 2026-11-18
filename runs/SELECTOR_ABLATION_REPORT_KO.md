# 2×2 evidence selector 실험: endpoint 선택 성공 시 recall이 회복됐다

2026-09-04 · `gpt-5.6-sol` · 96/96 calls · 기존 Click/AnyIO 개발 사례

## 결론

**AnyIO에서는 동일한 800토큰에서도 exact-2-hop unchanged 예약이 핵심 `TrioBackend.run`을 입력에 넣었고, unsafe exact target recall이 0/3에서 3/3으로 올랐다. safe false alarm은 0/3이었다.** Dedup을 함께 켜도 결과는 같았다.

그러나 이 결과를 일반적인 semantic-context contribution으로 확대할 수는 없다. Click에서는 같은 예약 규칙이 `_tempfilepager → echo_via_pager` 두 endpoint를 하나도 선택하지 못했고, fresh control보다 성능이 낮아졌다. 따라서 이번에 지지되는 것은 “문맥이 많으면 좋다”가 아니라 **필요한 revision-specific boundary evidence를 제한된 예산 안에 실제로 선택하면 reviewer의 최종 판정이 달라질 수 있다**는 한 개발 family의 causal-feasibility 신호다.

## 최종 리뷰 결과

각 칸은 `unsafe exact target hit / safe target-path false alarm / paired-correct`, 모두 3회 중 횟수다.

| selector | Click pager | AnyIO options |
|---|---:|---:|
| current paired rank | 3 / 1 / 2 | 0 / 0 / 0 |
| diff-visible seed dedup | 1 / 1 / 1 | 0 / 0 / 0 |
| 2-hop unchanged reserve | 2 / 1 / 1 | **3 / 0 / 3** |
| dedup + reserve | 0 / 0 / 0 | **3 / 0 / 3** |

96회 모두 generation/schema 오류가 없고 model tool call은 0이다. 최종 리뷰 48개에서 issue는 25개였다. 구조화된 evidence ID 위반은 없었다. 작성자가 raw 출력 96개를 모두 읽어 annotation했으며 독립 adjudication은 아니다.

기계 집계, 전체 출력, 작성자 annotation, 완료 기록.

## 왜 AnyIO 결과는 단순 stochastic 차이보다 강한가

Reserve off인 current와 dedup은 safe/unsafe에서 diff와 source context가 완전히 같았다. 두 조건 모두 unsafe target을 최종 단계에서 0/3 놓쳤다. Reserve on인 두 조건은 safe/unsafe 모두 `TrioBackend.run`을 선택했고, 그 source가 실제 정답 상태를 드러냈다.

```python
# safe companion
return trio.run(func, *args, **options)

# unsafe companion
return trio.run(func, *args)
```

그 결과 reserve와 both는 unsafe 3/3을 정확히 지적하면서 safe 0/3을 유지했다. 더욱 구체적으로, AnyIO unsafe에서 exact 가설은 네 조건 모두 proposal 단계에 3/3 등장했다. Current/dedup verifier는 이를 3/3 모두 최종 출력에서 버렸고, reserve/both verifier는 3/3 모두 유지했다. 즉 예약 source는 새 추측을 만들기보다 **이미 제기된 가설을 실제 companion code로 검증·유지하게 한 evidence**로 작동했다.

이 패턴은 동일 입력을 따로 샘플링해 우연히 safe/unsafe가 갈린 경우와 다르다. 다만 family 하나, 각 cell 세 번이므로 효과 크기의 모집단 추정이나 통계적 유의성을 주장하지 않는다.

## 실제 selector 조작

| family | current/dedup에서 전체 endpoint 선택 | reserve/both에서 전체 endpoint 선택 |
|---|---:|---:|
| Click (`_tempfilepager`, `echo_via_pager`) | 0/2 variants | 0/2 variants |
| AnyIO (`TrioBackend.run`) | 0/2 variants | **2/2 variants** |

AnyIO reserve는 400토큰 tranche에 `TrioBackend.run`, pytest runner, synchronization event를 담았다. 최종 source 길이는 reserve safe 791/unsafe 799, both safe 792/unsafe 800이었다. Current는 797, dedup은 796으로 길이 차이가 결과를 설명하기 어렵다.

Click에서는 2-hop/BM25 예약이 `_nullpager`, `isatty`, color default, `strip_ansi`, import/assignment chunks를 먼저 골랐다. `_tempfilepager`와 `echo_via_pager`도 정확히 2-hop 후보였지만 400토큰 예약에 들지 못했고, 나머지 채우기에서도 800토큰 밖으로 밀렸다. 즉 **exact-2-hop이라는 구조 조건만으로는 relevant boundary를 식별하지 못한다.**

선택·예산·endpoint 진단, 전체 rank와 selected key.

## 요인별 해석

AnyIO unsafe hit에서 reserve 주효과는 두 dedup 수준 모두 `+3/3`, dedup 주효과와 상호작용은 0이었다. Safe false alarm 변화도 0이다. 이 family에서는 reserve가 유일한 작동 요인이다.

Click unsafe hit에서는 reserve 평균 `-1/3`, dedup 평균 `-2/3`이었다. 상호작용은 0이지만, safe false alarm에는 작은 상호작용이 있다. Click의 모든 arm은 safe/unsafe 초기 입력이 각각 완전히 동일하므로 이 차이는 hidden state 판별이 아니라 stochastic output 차이다. Fresh current의 3/3도 앞선 paired-SCIP 1/3과 달라, 3회 반복 수치의 변동성을 직접 보여준다.

따라서 두 family를 합쳐 평균을 내 “reserve가 +1”이라고 요약하면 selector가 성공한 경우와 실패한 경우를 숨긴다. primary 해석은 endpoint inclusion으로 층화한 family별 결과다.

## 잘못된 메커니즘

Click final issue 19개 중 exact target item은 9개, wrong mechanism item은 10개였다. 반복되는 오답은 `isinstance(stream, typing.BinaryIO)` 자체가 TypeError를 낸다는 설명이다. 실제 witness runtime에서는 이 검사가 False이고, raw binary tempfile에 `str`을 쓰는 시점에 실패한다. Context가 늘거나 seed를 지운 것만으로 이 언어/runtime 오판은 사라지지 않았다.

AnyIO final issue 6개는 reserve/both unsafe의 exact target item뿐이었다. Current/dedup 최종 출력은 비어 있었다. 이 대비는 retrieval/selection 병목과 reviewer의 언어 추론 병목이 서로 다른 축임을 보여준다.

## 기존 실험 대비 무엇이 추가됐나

앞선 자동 검색은 “SCIP 후보에는 endpoint가 있지만 800토큰 선택에서 빠졌다”까지만 보였다. 이번 실험은 동일 모델·그래프·hop·총 source cap에서 선택 정책만 바꾸어 다음 연결고리를 한 사례에서 완성했다.

`2-hop 후보 존재 → 예약 selector가 endpoint 포함 → safe/unsafe source가 달라짐 → verifier가 unsafe 가설을 유지하고 safe에서는 기각 → 0/3에서 3/3`

이 연결은 향후 논문의 method hypothesis와 ablation 설계를 정당화하는 notable한 내부 결과다. 하지만 새 selector의 일반적 contribution이라고 말하려면 아직 부족하다.

- 두 family 모두 이미 결과를 본 개발 사례다.
- 작동한 것은 AnyIO 한 family뿐이다.
- Click은 구조적으로 같은 2-hop 규칙이 relevance를 못 가렸다.
- 반복 3회는 독립 PR 3개가 아니다.
- target matching은 작성자 단독 annotation이다.

따라서 현재 주장 가능한 문장은 다음 정도다.

> Under a fixed context budget, review recall can depend on selecting the defect-relevant unchanged semantic boundary rather than adding semantic context indiscriminately; in the AnyIO development case, reserving budget for exact-two-hop unchanged evidence exposed the revision-specific backend behavior and changed final review recall from 0/3 to 3/3 without safe-path alarms.

## 다음 confirmatory 설계

다음 단계는 이 selector를 더 조정해 같은 두 사례에 재적합하는 것이 아니다. 새 repository-disjoint 사례를 먼저 동결해야 한다.

1. diff-only로는 상태를 구분할 수 없고 실행 witness가 있는 safe/unsafe 쌍을 여러 repository에서 모집한다.
2. endpoint inclusion을 결과와 함께 보고하고, `current paired rank`, `exact-2-hop reserve`, `path/role-diverse reserve`, `def-use/config-consumer reserve`를 사전등록한다.
3. Click 같은 다수 2-hop 후보를 위해 BM25만 쓰지 말고, 서로 다른 semantic edge/path와 producer-consumer 역할을 덮는 submodular/MMR packing을 비교한다.
4. AnyIO처럼 인자는 받지만 사용하지 않는 consumer에는 SCIP occurrence 기반 parameter-use/argument-forwarding 증거를 별도 요인으로 둔다. 이것은 CodeQL 전체 data flow보다 가벼운 batch semantic-index 후처리로 구현할 수 있다.
5. endpoint가 선택된 strata와 선택되지 않은 strata를 나누어 `P(review hit | endpoint selected)`를 보고하되, oracle endpoint 선택은 upper bound로만 둔다.
6. 최소 수십 개 PR/family와 blinded dual annotation에서 diff-only 대비 strict/semantic recall, false alarms, token/latency를 함께 비교한다.

이 confirmatory 결과가 여러 holdout family에서 반복될 때에만 “base/head semantic index 차이로 최소 evidence subgraph를 선택해 diff-only defect recall을 높인다”는 contribution으로 올릴 수 있다.

## 품질 및 무결성

- 전체 코드 테스트 183개 통과; 신규 selector 77/77 statements, 100% coverage.
- frozen 입력 16개, model calls 96/96, final reviews 48, errors 0, tools 0.
- 첫 setup1/run1은 모델 호출 전 dedup 경계 버그를 발견해 중단했고 삭제하지 않았다. `started.json`이 없으며 호출 0이다.
- 최종 분석은 수정·재동결한 setup2/run2만 사용한다.
- 사전등록된 400/800 예산과 순위는 endpoint 진단 뒤 조정하지 않았다.
- Raw 파일 96개와 prediction/annotation/retrieval diagnostic hash는 `final_integrity.json`에 기록했다.

사전등록, 메소드, 무결성, [annotation 코드](selector_ablation_annotations.py), [집계 코드](selector_ablation_finalize.py).
