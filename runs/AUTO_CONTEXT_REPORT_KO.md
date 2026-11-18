# 자동 context 검색 비교: 96회 완료

2026-09-03 · `gpt-5.6-sol` · 기존 Click/AnyIO 개발용 사례 재사용

## 결론

**AnyIO에서는 BM25가 수작업 문맥의 긍정적 신호를 재현했다. 하지만 SCIP 및 paired-index의 추가 우위는 입증하지 못했다.** Click에서는 자동 검색으로 안전한 companion을 구분하지 못했다.

핵심 발견은 “SCIP가 필요한 함수를 못 찾았다”가 아니다. **필요한 함수가 후보 안에는 있었지만, 800토큰으로 최종 문맥을 구성할 때 탈락했다.** 현재 병목은 인덱싱 성공 여부보다 후보 순위와 예산 배분에 가깝다.

### 실제 리뷰 결과

아래는 **unsafe에서 정확한 목표 메커니즘을 탐지한 횟수 / 3회**다. 다른 예외 원인을 주장한 출력은 정답으로 세지 않았다.

| 같은 generic 2단계 reviewer | Click pager | AnyIO 옵션 누락 |
|---|---:|---:|
| Fresh Diff-only | 2/3 | 1/3 |
| BM25 | 1/3 | **3/3** |
| Head-only SCIP, 2-hop | 1/3 | 0/3 |
| Base/head SCIP, 2-hop | 1/3 | 0/3 |

**안전한 companion에 대한 경로 오경고는 Click에서 네 조건 모두 3/3, AnyIO에서 네 조건 모두 0/3이었다.** 따라서 Click의 위 탐지 수치를 safe/unsafe 판별 성공률로 읽으면 안 된다.

사전 주 지표인 “unsafe 목표 탐지 + 같은 반복 safe에서 동일 목표 메커니즘 주장 부재”도 이번에는 위 표와 같은 숫자가 나온다. 하지만 이 지표는 safe에서 *다른 틀린 메커니즘*으로 한 경고를 제외하므로, 안전성을 잘 판단했다는 뜻은 아니다. 그 허점을 숨기지 않기 위해 경로 오경고를 함께 제시했다.

기계 집계, 경로 오경고·오원인 진단, 전체 96개 출력, 작성자 annotation.

## 1. 무엇을 비교했나

두 family × safe/unsafe × 네 입력 조건 × 세 반복 × 가설/검증 두 stage = **96회 모델 호출, 최종 리뷰 48회**다. 실행 오류 0, 출력 schema 오류 0, 모델 도구 호출 0이다. Reviewer 코드와 두 prompt는 직전 72회 실험과 hash까지 같다. Source 상한은 모든 검색 조건에서 800 `o200k_base` reference tokens다.

실제 source 길이는 Click BM25 792, 두 SCIP 782; AnyIO BM25 safe 791/unsafe 796, 두 SCIP 797이다. 상한만 같고 정확한 token/block 수 매칭은 아니다. Diff-only는 이번에 새로 실행했다.

직전 manual relevant 결과인 Click 2/3·AnyIO 3/3은 **historical reference**다. 이번 자동 검색은 AnyIO에서 같은 수치를 보였지만 동시 실행·동일 source 길이의 oracle 비교는 아니다. 두 사례 모두 이미 관찰한 개발용 사례이고, 새 holdout이 아니다. Click은 실제 production 변경 + 구성한 safe companion, AnyIO는 실제 adapter 구현 + 합성 client migration이다.

동결 계획, 메소드·재현 절차, 완료 기록.

## 2. 실제 SCIP를 실행했나

그렇다. [공식 `scip-python`](https://github.com/sourcegraph/scip-python) 0.6.6을 실행해 Click 17개, AnyIO client 포함 44개 production Python 파일을 각 4개 스냅샷에서 인덱싱했다. 총 8개 primary index이며 원본 source 244 file-instances의 hash를 확인했다. AST는 chunk/owner 경계에만 쓰고 실제 심볼 연결은 SCIP definition/reference/typed relationship에서 얻었다. LSP 서버를 실시간 호출한 비교는 아니다.

`src/` import 연결 누락 가능성을 고려해 경로를 명시했고, AnyIO `client.py → anyio._core._eventloop.run`의 실제 심볼 연결도 확인했다. [해당 src-layout 문제 보고](https://github.com/sourcegraph/scip-python/issues/221).

첫 Python 3.13 설정/3.14 interpreter 준비본은 미지원 경고 때문에 사용하지 않았다. 모델 호출 0회로 보존했다. Python 3.11.13으로 다시 인덱싱한 뒤 실험했다. 추가 portability 검사에서 Python <3.13 전용 `typing_extensions` 누락을 발견해 4.15.0을 설치하고 별도 디렉터리에 다시 인덱싱했다. **모든 16개 primary 입력 문맥이 동일하게 재현됐고**, 두 family의 실행 정답도 Python 3.11에서 재현됐다. 원래 CPython 3.14.6 정답과 모델 입력은 바꾸지 않았다.

Index provenance, 의존성 보완 민감도와 실행 증거. 첫 실패 로그와 별도 재검증 결과 모두 남겼다. 인덱스 문서가 모두 있다는 것은 sound call/data-flow 분석이나 모든 reference의 완전성을 뜻하지 않는다.

## 3. 실패 지점: 후보 생성이 아니라 최종 선택

| 핵심 evidence endpoint | BM25의 800토큰 입력 | SCIP 후보 | SCIP의 800토큰 입력 |
|---|---|---|---|
| Click `_tempfilepager`: binary/text 생산 방식 | 없음 | 있음, head 순위 12 | 없음 |
| Click `echo_via_pager`: 문자열 소비 | 없음 | 있음, head 순위 15 | 없음 |
| AnyIO `TrioBackend.run`: options 실제 소비 | **있음, 순위 3** | 있음, head 순위 16 | 없음 |

두 SCIP 조건은 Click에서 19개, AnyIO에서 96개 reachable 후보를 생성했다. 그러나 변경 함수·가까운 wrapper·import가 먼저 예산을 차지해 중요한 unchanged companion이 밀렸다. AnyIO의 구체적 backend 구현까지 후보에 있으므로 이 실패를 단순히 “동적 dispatch여서 SCIP에 edge가 없다”라고 설명하는 것도 맞지 않는다.

Source만 사후 재선택한 budget 진단에서는 다음을 확인했다. **추가 모델 호출은 하지 않았다.**

- SCIP 1,600 상한: Click producer와 AnyIO options consumer가 들어왔다. Click text consumer는 여전히 빠졌다.
- SCIP 3,200 상한: Click 두 endpoint가 모두 들어왔고 전체 후보를 담은 실제 source 길이는 1,862였다.
- BM25 3,200 상한: Click producer는 들어왔지만 text consumer는 여전히 빠졌다.

따라서 “큰 문맥에서 리뷰 성능이 개선됐다”는 결과는 아니다. 다음 selector 실험의 병목 위치를 특정한 검색 진단이다. 기존 manual Click 문맥은 772토큰으로 필요한 체인을 담았으므로, 단순한 최대 길이 부족만으로 설명할 수도 없다.

전체 selected spans·순위·budget 탈락, 검색 진단과 budget sweep. Target endpoint는 이 사후 evaluator에서만 사용했고 selector의 입력에는 없었다.

## 4. Paired-index 조건이 왜 차이를 만들지 못했나

두 사례 모두 **head-only와 paired가 선택한 source 집합이 같았다.** AnyIO는 순서와 prompt까지 같았고, Click은 순서만 달랐다. Base가 추가한 유용한 증거가 이번 최종 입력에는 없다.

현재 paired 우선순위는 base/head에서 body 또는 이웃 집합이 달라진 chunk를 앞세운다. 이 두 사례의 중요한 producer/consumer는 unchanged companion이다. 변경 지점 자체를 다시 우선하면, 이미 diff에 있는 정보가 문맥 예산을 차지하는 방향으로 작용할 수 있다.

이것은 **이번 selector와 두 사례에서 paired 이득이 없었다**는 결과다. Base 정보가 유용할 수 있는 모든 사례를 반증한 것은 아니다. 또한 여기서는 paired index로 *head source를 선택*했으며 base code 자체를 reviewer에게 추가하는 효과는 실험하지 않았다.

## 5. 리뷰 추론 병목도 별도로 남았다

AnyIO unsafe는 모든 조건에서 정확한 옵션 누락 가설이 3/3회 나왔다. 그 가설을 verifier가 최종 지적으로 유지한 것은 Diff 1/3, BM25 3/3, 두 SCIP 0/3이었다. BM25는 실제 `options`를 받고도 `trio.run(func, *args)`만 호출하는 코드를 제공했다. 반면 SCIP의 최종 입력에는 그 코드가 없었다. 빈 최종 출력의 내부 이유가 기각인지 누락인지는 확정하지 않는다.

Click의 최종 지적 22개 중 13개는 “`isinstance(stream, typing.BinaryIO)` 자체가 TypeError를 낸다”는 잘못된 설명이었다. 실제 목표는 nominal check가 **False**라 wrapping이 생략되고, 이후 **binary file에 str을 쓰면서** 실패하는 것이다. 나머지 9개가 그 목표 메커니즘을 언급했지만 4개는 safe에서의 오주장이었다. AnyIO의 최종 지적 4개는 모두 unsafe 목표 탐지였다. 전체 최종 지적은 26개이며 다른 원인의 최종 지적은 없었다.

앞선 runtime type-check probe, 최종 원인별 집계. 이 실험은 AACR strict/semantic F1이나 전체 review precision 평가가 아니다.

## 6. 동일 입력의 우연한 성공을 제외해서 해석해야 한다

**AnyIO BM25를 제외한 모든 조건에서 safe와 unsafe의 초기 prompt가 완전히 같다.** 선택된 source에 companion 상태 차이가 없기 때문이다. 두 상태의 모델 호출을 따로 실행해 출력이 달라지는 것은 stochastic variation이지 보이지 않는 상태를 구분했다는 증거가 아니다.

특히 Click Diff의 2/3을 BM25·SCIP의 1/3보다 일반적으로 우수하다고 주장할 수 없다. 안전한 Click에 대해 네 조건 모두 3/3 잘못 경고했고, 정보론적으로 companion 상태를 구분할 입력도 없었다. AnyIO BM25만 실제로 다른 consumer source를 받아 unsafe 3/3 탐지·safe 0/3 오경고를 보였다.

## 7. Contribution 판단과 다음 검증

현재 지지되는 주장은 **“기존 lexical retrieval도, 이 AnyIO 사례에서 실제 옵션 소비 코드를 제공하면 같은 reviewer의 목표 판단을 개선할 수 있다”**는 작은 feasibility 신호다. 한 합성 migration 사례와 세 번의 모델 반복만으로 일반화·통계적 유의성·새 방법의 독창성을 주장하지 않는다. BM25는 기존 baseline이므로 이것만으로 새 retrieval contribution은 아니다.

다음 검증의 우선순위는 두 가지다.

1. **같은 800토큰에서 endpoint를 놓치지 않는 선택법.** Diff 중복 source 제거 여부 × unchanged boundary/consumer에 예산을 예약하는지 여부를 2×2 ablation으로 분리한다. 현재 BM25/SCIP 순위와 비교하고 token/block 구조 대조도 맞춘다. Target 이름을 하드코딩하지 않고 diff의 type/config/return 변화 및 실제 static-use/implementation 경계로 선택해야 한다. 개선 여부는 아직 미실험이다.
2. **정말 base가 필요한 별도 사례군.** 이번 head graph가 이미 핵심 후보를 포함하므로 paired 검색의 고유 이득을 보기 어렵다. 삭제·참조 재결합 등에서 base 경로가 head만으로 누락되는 companion을 추가하는 사례를 독립적으로 구축하고, 동일 hop/budget의 head-only 및 확장 head-only 대조와 비교한다. Base source 제공 효과도 selection 효과와 별도 ablation으로 분리한다.

더 큰 예산이나 실행 type-check facts는 별도 조건이어야 한다. Source-only selector의 성과에 합쳐서는 안 된다. Click의 언어/runtime 오판은 retrieval과 구분되는 추론 병목으로 남겨둔다.

## 실행·품질 기록

- 96/96 호출 완료, 실행/schema/tool 오류 0. 가설 52개, 최종 지적 26개. Hypothesis 예산 탈락 0.
- 구조화된 evidence ID 오류 0. 이는 인용 source가 모든 주장을 실제로 뒷받침한다는 뜻은 아니다.
- 코드 테스트 **174개 통과**. 신규 selector statement **179/179**, branch **82/86** 커버리지. `ecc-codex-playbook`의 테스트 우선·코드 검토 절차를 적용했다.
- 선택된 source spans **105개**가 원본 masked head와 일치. 원본 source **244 file-instances** 재해시 결과 변경 0. 기존 run과 이번 동결 파일 모두 보존.
- Annotation은 세션 작성자가 96개 출력을 모두 읽어 작성했다. 독립 인간/별도 judge 합의가 아니다.
- 모델 호출별 실행 시간 합은 약 2,239초, 중앙값 약 21.2초다. 병렬 wall-clock 시간이나 과금액이 아니다. Primary indexing 호출 8개의 시간 합은 약 26.2초이며 전체 준비 비용을 뜻하지 않는다.

Coverage, 종료 무결성, [annotation 작성 코드](auto_context_annotations.py), [집계 코드](auto_context_finalize.py).
