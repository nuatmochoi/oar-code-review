# ACE-Review 실행 보고서

실행일: 2026-09-01

## 실행 범위

- AACR-Bench 스냅샷 `68a569759289a83654a59d06db2a72910edf0a4a`
- Go LSP artifact 스냅샷 `8f59364e58de906e4ef55c03d8b6eb798fcb19af`
- 변환 결과: Go 1,438개 task, AACR defect/performance PR 173개와 annotation 827개
- 실제 end-to-end pilot: `ollama/ollama#8938`, gold defect 1개
- reviewer: `gpt-5.6-sol`; semantic judge: `gpt-5.4`
- 고정 arm: Diff, BM25, LSP, LSP+CodeQL data flow, LSP+build/test,
  structured-all, stuffed-all
- adaptive: gold를 보지 않는 정적 gate로 pipeline을 smoke test함. 학습 selector는
  8개 hunk에서 입출력 경로만 검증했으며 성능 추정치로 사용하지 않음.

## 방법상 교정

처음에는 hunk diff만 모델에 제공했다. 이 경우 import와 사용처, 호출과 정의가 서로
다른 hunk에 있으면 불완전한 patch가 되어 `unused import`, `undefined function` 같은
가짜 결함이 발생했다. 최종 protocol은 모든 arm에 전체 PR diff를 공통 제공하고,
한 hunk만 report target으로 지정한다. BM25/LSP/CodeQL retrieval seed만 target hunk로
제한한다. AACR와 직접 비교하는 주 결과는 hunk가 아닌 PR 전체를 한 번 리뷰한 결과다.

## 실제 문맥 수집

- BM25: PR-head repository snapshot의 lexical top-k block
- LSP: gopls 0.23.0의 definition/reference와 해당 source window
- Data flow: CodeQL CLI 2.26.2와 동일 tag의 Go query library. added line에서
  call argument, return, type conversion sink로 이어지는 global path를 최대 5개 제공
- Build/test: merge base와 PR head에서 변경 Go package의 테스트를 실행하고 delta 제공
- 모든 provider는 8/8 hunk에서 available이었다. `stuffed_all`의 AACR neighbor cache만
  없었으며 BM25/LSP/data-flow/build-test는 포함됐다.

## PR-level 주 결과

| 조건 | TP | FP | Precision | Recall | 평균 prompt token(추정) |
|---|---:|---:|---:|---:|---:|
| Diff | 1 | 0 | 1.00 | 1.00 | 1,229 |
| BM25 | 1 | 0 | 1.00 | 1.00 | 4,596 |
| LSP | 1 | 0 | 1.00 | 1.00 | 3,284 |
| LSP+data flow | 1 | 0 | 1.00 | 1.00 | 3,671 |
| LSP+build/test | 1 | 0 | 1.00 | 1.00 | 3,321 |
| structured-all | 1 | 0 | 1.00 | 1.00 | 3,708 |
| stuffed-all | 1 | 0 | 1.00 | 1.00 | 5,446 |
| adaptive(static smoke) | 1 | 0 | 1.00 | 1.00 | 3,321 |

이 PR에서는 모든 조건이 `api/types.go:614`의 unchecked `uint -> int` overflow를
찾았다. 따라서 이 한 표본에는 context gain, suppression, induced FP가 모두 0이며,
어느 arm이 더 우수한지 판단할 수 없다.

## Hunk-focused 진단 결과

전체 patch를 공통 제공하고 8개 hunk를 따로 리뷰했을 때 모든 조건의 recall은 1.0이었다.
strict AACR line matching에서 Diff와 LSP는 각각 1 FP, LSP+data flow와
LSP+build/test/structured-all은 각각 2 FP, stuffed-all은 3 FP였다. 그러나 추가 FP의
대부분은 환각이 아니라 동일 overflow를 `envconfig/config.go:171/195`의 입력 검증
지점에 다시 보고한 것이다. 즉 richer context가 defect localization을 gold line에서
원인 line으로 이동시키고, 독립 hunk 호출이 동일 defect 중복을 만들었다.

따라서 후속 실험은 최소 두 지표를 함께 보고해야 한다.

1. AACR-compatible strict path/line±1/semantic 점수
2. PR-level semantic-deduplicated 점수와 duplicate-comment rate

## 결론

CodeQL 기반 path-sensitive data-flow arm과 전체 비교 파이프라인은 실제로 실행됐다.
하지만 현재 pilot은 PR 1개/gold 1개이므로 “문맥이 많을수록 성능이 하락한다”거나
“LSP+data flow가 우월하다”는 결론을 지지하지 않는다. 오히려 중요한 설계 결론은
다음과 같다.

- hunk만 잘라 주면 context 효과와 incomplete-patch 오류가 혼동된다.
- richer context는 발견 여부뿐 아니라 코멘트 위치를 바꾼다.
- hunk 병렬 리뷰에는 PR-level semantic deduplication이 필요하다.
- adaptive selector의 확정 평가는 repository-disjoint nested split과 다중 반복이 필요하다.

## 재현 산출물

- PR 요청: `runs/aacr_ollama_8938_pr_requests.jsonl`
- PR 모델 출력: `runs/aacr_ollama_8938_pr_gpt56_predictions.jsonl`
- PR judge label: `runs/aacr_ollama_8938_pr_gpt56_labels_v2.jsonl`
- PR 분석: `runs/aacr_ollama_8938_pr_gpt56_analysis_v2.json`
- hunk 진단 분석: `runs/aacr_ollama_8938_fullpatch_gpt56_analysis_v2.json`
- selector smoke model: `runs/aacr_ollama_8938_selector_smoke.json`
- 실행 manifest: `runs/pilot_manifest.json`

## Confirmatory run 요구사항

- repository-disjoint outer fold와 inner validation fold
- 조건당 최소 3회 모델 반복 및 paired bootstrap confidence interval
- PR-level semantic deduplication과 duplicate rate
- CodeQL sink/sanitizer 정의 ablation
- build/test 반복으로 flaky 분리
- Go 이외 언어 adapter를 구현하거나 논문 claim을 Go로 제한

## 참고

- [AACR-Bench repository](https://github.com/alibaba/aacr-bench)
- [AACR-Bench paper](https://arxiv.org/abs/2601.19494)
- [CodeQL CLI 2.26.2 release](https://github.com/github/codeql-cli-binaries/releases/tag/v2.26.2)
- [CodeQL path query documentation](https://codeql.github.com/docs/writing-codeql-queries/creating-path-queries/)
