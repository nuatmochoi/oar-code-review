# ACE-Review Go 확증실험 보고서

실행일: 2026-09-02

추가 검증 안내: `runs/FACTORIAL_ABLATION_REPORT_KO.md`에 CodeQL-only,
build/test-only, CodeQL+build/test의 추가 결과가 있다. 추가 실행의 오류 로그에서
reviewer의 외부 파일 탐색/웹 검색 시도가 확인되었다. 성공 호출의 tool trace는 보존되지
않아 외부 문맥 사용 여부를 확인할 수 없으므로, 이 문서의 결과 역시 엄격한 tool-isolated
확증실험이 아닌 탐색적 evidence 비교로 해석해야 한다.

## 1. 연구 질문

AACR-Bench가 보고한 “문맥이 많다고 항상 좋아지지 않는다”는 관찰을 다음 evidence
ladder로 직접 확장했다.

`Diff → BM25 → LSP → LSP+data flow → LSP+build/test → structured-all → stuffed-all → adaptive`

주 질문은 단순 token 증가가 아니라 구조화된 문맥 종류가 defect recall, precision,
위치 정확도, 중복/환각 및 비용에 미치는 영향이다.

## 2. 코호트와 실행 조건

- AACR-Bench positive samples에서 Go로 분류된 PR: 21개
- 주 코호트: 모든 gold path가 `.go`인 17 PR, 4 repositories, gold annotation 75개
- 제외: TypeScript/C++/go.mod gold가 섞인 4개 PR. Go 전용 gopls/CodeQL 비교의 공정성을
  위해 사전에 적용한 기계적 규칙이다.
- inference unit: 전체 PR diff. hunk-only incomplete-patch confound를 제거했다.
- 조건당 3회, 총 reviewer 357회; `gpt-5.6-sol`; 실패 0회
- review당 최대 5개 독립 issue, evidence budget 4,096 token
- automatic judge: `gpt-5.4`, 323회 호출, 오류 0회
- strict metric: path + line ±1 + semantic equivalence
- 보조 metric: 같은 파일 내 line을 무시한 semantic equivalence, relocation/duplicate/hallucination 분해
- CI: PR paired bootstrap 5,000회. repository cluster bootstrap도 병기하지만 cluster가 4개뿐이므로
  보조 근거로만 사용한다.

## 3. Evidence 가용성

| Provider | 성공 PR | 비고 |
|---|---:|---|
| BM25 | 17/17 | PR-head repository snapshot |
| build/test | 17/17 | no observed failure 13, shared failure 4 |
| gopls LSP | 15/17 | GoFr PR 2개에서 미가용 |
| CodeQL data flow | 15/17 | 대형 TypeScript-Go PR 2개 timeout |
| neighbor cache | 0/17 | 따라서 stuffed-all은 neighbor를 제외한 available evidence로 실행 |

CodeQL은 added line을 gold-blind risk ranking하여 PR당 최대 64개 source line으로 제한했다.
각 미가용 provider는 요청의 `context_status.unavailable`에 보존했다. 주 분석은
intention-to-treat이며, availability-complete 결과를 별도로 산출했다.

## 4. 주 결과: AACR-compatible strict matching

75 gold를 세 번 평가하므로 분모는 225다.

| Condition | TP | FP | Precision | Recall | F1 | ΔF1 vs Diff (PR-bootstrap 95% CI) | 평균 prompt token* |
|---|---:|---:|---:|---:|---:|---|---:|
| Diff | 34 | 78 | .304 | .151 | .202 | baseline | 5,953 |
| BM25 | 28 | 81 | .257 | .124 | .168 | -.034 [-.080, .000] | 9,571 |
| LSP | 29 | 78 | .271 | .129 | .175 | -.027 [-.069, .016] | 7,638 |
| LSP+build/test | 33 | 69 | .324 | .147 | .202 | +.000 [-.050, .046] | 7,674 |
| LSP+data flow | 35 | 77 | .313 | .156 | .208 | +.006 [-.036, .045] | 7,979 |
| structured-all | 36 | 64 | .360 | .160 | .222 | +.020 [-.024, .076] | 8,016 |
| stuffed-all | 32 | 72 | .308 | .142 | .195 | -.007 [-.065, .033] | 10,003 |
| adaptive-CV | 30 | 75 | .286 | .133 | .182 | -.020 [-.063, .018] | 8,291 |

\* tokenizer 기반 실제 billing token이 아니라 UTF-8 byte/4의 고정 근사치다.

structured-all의 point estimate가 가장 높지만 CI가 0을 포함한다. LSP+data flow의
증분도 작고 불확실하다. BM25는 더 많은 token을 쓰면서 악화되는 방향이 가장 일관됐다.

## 5. Line을 무시한 semantic-anywhere 결과

| Condition | TP | FP | Precision | Recall | F1 | Relocated | Hallucination | ΔF1 vs Diff (95% CI) |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| Diff | 48 | 64 | .429 | .213 | .285 | 14 | 62 | baseline |
| BM25 | 41 | 68 | .376 | .182 | .246 | 13 | 68 | -.039 [-.064, -.013] |
| LSP | 43 | 64 | .402 | .191 | .259 | 14 | 63 | -.026 [-.074, .013] |
| LSP+build/test | 46 | 56 | .451 | .204 | .281 | 13 | 56 | -.004 [-.056, .040] |
| LSP+data flow | 48 | 64 | .429 | .213 | .285 | 13 | 64 | +.000 [-.028, .021] |
| structured-all | 43 | 57 | .430 | .191 | .265 | 7 | 57 | -.020 [-.074, .022] |
| stuffed-all | 45 | 59 | .433 | .200 | .274 | 13 | 58 | -.011 [-.066, .029] |
| adaptive-CV | 43 | 62 | .410 | .191 | .261 | 13 | 62 | -.024 [-.060, .004] |

전체적으로 strict TP 227개와 semantic-anywhere TP 314개의 차이 87개가 location
relocation이다. 즉 line-based 평가만 보면 실제로 같은 defect를 다른 원인 줄에 보고한
코멘트를 FP로 처리한다. 다만 richer context의 strict 이득이 semantic coverage 이득으로
이어지지는 않았다. structured-all은 gold line 정렬에는 유리했지만 고유 defect coverage는
Diff보다 낮았다.

## 6. 문맥 수준별 strict recall

| Condition | Diff-level | File-level | Repo-level |
|---|---:|---:|---:|
| Diff | .178 | .098 | .242 |
| BM25 | .144 | .088 | .182 |
| LSP | .122 | .098 | .242 |
| LSP+build/test | .133 | .127 | .242 |
| LSP+data flow | .144 | .137 | .242 |
| structured-all | .144 | .127 | .303 |
| stuffed-all | .144 | .108 | .242 |

structured-all의 strict 증가분은 주로 Repo-level annotation에서 발생했다. 반면
LSP+data flow는 File-level recall을 높이는 방향이었다. 분모가 작으므로 탐색적 해석이다.

Category별로는 Code Defect만 발견됐고 Performance gold는 모든 조건에서 0/15였다.

## 7. Context gain과 suppression

Diff 대비 세 반복의 gold-ID 전이를 합산하면 다음과 같다.

| Condition | Gain | Suppression | Context-induced FP |
|---|---:|---:|---:|
| BM25 | 4 | 10 | 17 |
| LSP | 6 | 11 | 17 |
| LSP+build/test | 11 | 12 | 15 |
| LSP+data flow | 7 | 6 | 19 |
| structured-all | 8 | 6 | 10 |
| stuffed-all | 7 | 9 | 12 |

문맥은 단순히 기존 발견에 더하는 것이 아니라 발견 집합을 교체했다. 특히 BM25/LSP는
gain보다 suppression이 컸다. structured-all과 LSP+data flow만 gain이 suppression보다
많았지만 F1 CI는 여전히 불확실하다.

## 8. 반복 안정성

조건별 strict F1은 다음처럼 변동했다.

| Repeat | Diff | BM25 | LSP | LSP+build/test | LSP+DF | Structured | Stuffed | Adaptive |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | .177 | .091 | .165 | .214 | .232 | .262 | .195 | .179 |
| 2 | .232 | .183 | .180 | .170 | .177 | .173 | .148 | .171 |
| 3 | .196 | .226 | .179 | .220 | .214 | .228 | .241 | .195 |

동일 gold detection set이 세 반복에서 완전히 같았던 PR은 조건별 8–10/17에 불과했다.
단일 실행 결과를 비교하는 것은 신뢰하기 어렵다는 것을 확인했다.

## 9. Adaptive selection

외부 fold는 repository 전체를 holdout하고, training repositories 안에서 다시
leave-one-repository-out validation으로 k∈{1,3,5,7}을 선택했다. 선택 분포는
structured-all 7, BM25 3, LSP+DF 3, 나머지 각 1이었다.

Adaptive-CV strict F1은 .182로 Diff .202보다 낮았다. 현 kNN selector는 표본 17개와
repository 4개에서 일반화하지 못했다. adaptive selection이라는 아이디어의 부정이라기보다,
현재 feature와 학습 규모가 불충분하다는 결과다.

## 10. 해석

1. **BM25 문맥은 명확한 개선책이 아니다.** 가장 많은 token 중 하나를 사용하면서 strict와
   semantic 점수가 모두 낮아졌다.
2. **LSP 단독도 자동 개선이 아니다.** symbol context가 모델의 attention을 분산시킬 수 있다.
3. **Data flow와 build/test는 서로 다른 효과를 보였다.** LSP+DF는 recall, LSP+build/test는
   precision 쪽 point estimate가 높았지만 확정적이지 않다.
4. **구조화된 결합이 무차별 stuffing보다 낫다.** structured-all은 strict F1 최고였고
   stuffed-all은 token을 더 쓰면서 Diff보다도 낮았다.
5. **“문맥이 많으면 하락”은 평균 token 양만의 문제가 아니다.** evidence의 종류, 위치 이동,
   suppression, provider failure, 모델 확률성이 함께 작용한다.

## 11. 한계

- 17 PR, 4 repositories로 작다. repository-cluster CI는 cluster 수가 너무 적다.
- defect-positive PR만 사용했으므로 clean-PR false alarm rate는 측정하지 못했다.
- review당 최대 5 issue이므로 gold가 많은 PR의 recall 상한이 존재한다.
- automatic judge는 단일 pilot에서 pair judge와 7/7 일치했지만 인간 blinded audit은 아직 없다.
- LSP/CodeQL 미가용 PR을 포함한 주 결과는 intention-to-treat다.
- stuffed-all의 neighbor evidence가 모든 PR에서 미가용이어서 완전한 AACR stuffed arm이 아니다.
- token 값은 근사치이며 실제 API 과금 token이 아니다.
- Performance annotation은 한 건도 발견하지 못해 별도 prompt/evidence 설계가 필요하다.

## 12. 산출물

- `runs/confirmatory_manifest.json`
- `runs/aacr_go_pure_17_requests.jsonl`
- `runs/aacr_go_pure_17_predictions_r{1,2,3}.jsonl`
- `runs/aacr_go_pure_17_labels.jsonl`
- `runs/aacr_go_pure_17_analysis_with_adaptive_cv.json`
- `runs/aacr_go_pure_17_bootstrap*.json`
- `runs/aacr_go_pure_17_stratified.json`
- `runs/aacr_go_pure_17_labels_with_adaptive_cv.json.decisions.json`

총 reviewer prompt token 근사치는 2,898,474이며 reviewer wall-time 합은 19,823초다.
