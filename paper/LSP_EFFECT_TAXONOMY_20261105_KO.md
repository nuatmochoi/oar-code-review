# LSP의 F1 향상 유형: 새 본문·재노출·판독 차이 분리

기존 결과를 바꾸지 않은 사후 기전 감사이다. 12개 개발 PR(Python6·Go6)×3반복의 AST6k와 AST+LSP6k를 비교했다. 새 모델·도구 조회는 실행하지 않았다. 기존 AI 판독과 Codex의 코드·로그 분석이며 인간 검토는 0건이다.

**핵심: LSP 조건의 관측 F1은 높지만, 상승분 전체가 새 구현 조회의 효과로 확인된 것은 아니다.** 16회 관측 추가 정탐 중 실제 LSP 응답→신규 본문→최종 인용의 연결은 3회다. 그중 1회는 AST 판독 실패여서 추가 탐지 여부를 확정할 수 없다. 비교측 판독이 완료된 직접 연결 사례는 2회다. 16회 중 3회는 AST 판독 실패와의 비교다.

| 조건 | 원인 TP | 원인 F1 | 유효 TP | 유효 보고 F1 | 보고 수 | 평균 추가 본문 토큰 |
|---|---:|---:|---:|---:|---:|---:|
| AST 지역 본문 6k | 33 | 22.07% | 28 | 18.73% | 50 | 4574.4 |
| AST+LSP 정의·참조 6k | 41 | 26.80% | 33 | 21.57% | 57 | 4394.9 |

원인 F1 +4.72%p, 유효 보고 F1 +2.84%p. 같은 6k **상한**이며 실제 본문량·내용·metadata·계산량은 같지 않다. 원인 정탐 유지25/추가16/소실8, 유효 보고 정탐 유지19/추가14/소실9이다. 전체 정답 노출 분모는 249개다. 미매칭을 일괄 오탐으로 세지 않았다.

## 효과 유형과 확인 수준

| 유형 | 관측 추가 정탐 | 확인한 실행 연결 | 해석 범위 |
|---|---:|---|---|
| 다른 상태 갱신 경로 확보 | 1 | references → `_probe_device` 본문 → 최종 인용 | 동시 갱신에 의한 덮어쓰기 설명을 구체화 |
| 입력·호출자 경로 확보 | 1 | references → HTTP 등록 핸들러 → 최종 인용 | 외부 입력이 검증 실패·로그까지 도달하는 경로 확인 |
| 변경 전 구현 확보 | 1 | definition → BASE `printError` → 최종 인용 | 기존 오류 출력과 새 분기의 차이를 설명. AST 판독 실패라 탐지 증가 확정 불가 |
| 공통 AST 본문 배치·재노출 | 3 | LSP 전의 AST3k prefix에 있던 `applyPinsYAML`/`addPath` | public diff에도 같은 코드가 있음. LSP 검색으로 새 사실을 얻은 사례 아님 |
| 기존 근거 재판단 / 직접 귀속 미확인 | 10 | 핵심 supporting 인용 행이 AST+전체 diff에도 있음 | 주의·metadata·전체 문맥·독립 응답 차이를 분리하지 못함 |

횟수는 PR×반복×target의 노출 수이며 고유 결함 수가 아니다. 위 분류는 16회 관측 추가 정탐의 배타 분류다. 카테고리별 F1 기여도나 인과효과로 더하지 않는다. 특히 공통 diff까지 포함해 신규성을 검사했으며, source ID만 새롭다는 이유로 새 근거로 세지 않았다.

## 실제 LSP 연결 3개

### 변경 전 구현 확보: RB_cli__cli-13175 / r3 / G000

The new handler checks whether the error begins with "unknown command", but then searches every expanded argument for an official extension name without verifying that the matching argument is the command Cobra reported as unknown. For example, an invocation such as `gh nonexistent stack` can fail because `nonexistent` is unknown, yet the presence of the later argument `stack` causes the CLI to offer or prompt to install `gh-stack`. A global flag value can produce the same false match, such as `gh --repo stack nonexistent`. BASE instead passes the original error to the generic error printer, so the misleading extension path is introduced by this PR.

AST 판독 상태: `judge_failed`. 기존 LSP 원인 TP는 유지하되, 비교측 미판독을 결함 부재로 확정하지 않는다.

- 신규 인용: `BASE internal/ghcmd/cmd.go:209–209` / `E-59265380c695db7d1059`. BASE handled unknown-command errors through the generic printer and did not perform this argument-wide extension match.
  - 원시 조회: `definition` / 요청 심볼 `printError`. 참조 위치는 후보이며 실행 경로 판정은 본문을 읽은 의미적 해석이다.

LSP 원시 결과 · AST 원시 결과

### 다른 상태 갱신 경로 확보: RB_gtronset__tablo-legacy-m3u-30 / r1 / G000

The watch worker constructs a full `DeviceStatus` replacement from the current object while changing only `tuners`. A periodic probe independently assigns a full new `DeviceStatus`. If the probe assignment occurs after the watch worker reads the old object but before it assigns the replacement, the watch worker restores all old non-tuner fields, including probe timestamps, errors, server information, hard-drive data, and guide data. The documented atomic reference assignments prevent torn references but do not make this read-modify-write operation atomic.

AST 판독 상태: `complete`. 기존 LSP 원인 TP는 유지하되, 비교측 미판독을 결함 부재로 확정하지 않는다.

- 신규 인용: `HEAD tablo_legacy_m3u/main.py:65–65` / `E-1d92b8d869c4e88a0b67`. The periodic probe independently starts constructing and assigning a complete `DeviceStatus`.
  - 원시 조회: `references` / 요청 심볼 `AppState`. 참조 위치는 후보이며 실행 경로 판정은 본문을 읽은 의미적 해석이다.
- 신규 인용: `HEAD tablo_legacy_m3u/main.py:72–72` / `E-1d92b8d869c4e88a0b67`. The probe's complete status includes a newly generated `last_probe`, which can be reverted by the competing watch assignment.
  - 원시 조회: `references` / 요청 심볼 `AppState`. 참조 위치는 후보이며 실행 경로 판정은 본문을 읽은 의미적 해석이다.

LSP 원시 결과 · AST 원시 결과

### 입력·호출자 경로 확보: RB_sirosfoundation__go-wallet-backend-76 / r1 / G003

The new verification-error branch logs the complete request credential JSON, full authenticator data in hexadecimal, attestation-statement byte values in base64, and every x5c certificate in full. The registration endpoint accepts the credential from a public JSON request and reaches this branch whenever parsing succeeds but CreateCredential rejects it. With debug logging enabled, a remote caller can repeatedly submit invalid registrations to inject and amplify large binary payloads into application logs, increasing log volume and potentially exposing authenticator metadata to anyone with log access. BASE only emitted the verification error, so this logging behavior is introduced by the PR. The supplied evidence does not establish an HTTP body-size limit, so the maximum amplification and denial-of-service severity remain uncertain.

AST 판독 상태: `complete`. 기존 LSP 원인 TP는 유지하되, 비교측 미판독을 결함 부재로 확정하지 않는다.

- 신규 인용: `HEAD internal/api/handlers.go:202–202` / `E-5fb7dd82a41b461f6665`. The HTTP handler passes the JSON-bound request directly to FinishRegistration, establishing the public-code trigger.
  - 원시 조회: `references` / 요청 심볼 `FinishRegistration`. 참조 위치는 후보이며 실행 경로 판정은 본문을 읽은 의미적 해석이다.

LSP 원시 결과 · AST 원시 결과

## 유효 보고 F1은 탐지 증가와 인용 정확화가 섞여 있다

유효 TP는 기존28−소실9+추가14=33(유지19)이다. 추가14 중13회는 원인 정탐 추가에 동반됐고, 1회는 이미 맞던 원인의 인용이 유효해진 경우다. go-wallet-backend#76 r3/G001에서 AST는 base64 연산 대신 닫는 괄호 행을 인용해 탈락했고 LSP 조건은 관련 인용으로 통과했다. 이 인용 개선을 LSP 자체의 필연적 효과로 확정할 수 없다.

## 소실과 미입증 효과

원인 소실 8회 중, 기존 supporting 인용 행 전체가 LSP 입력에도 남은 경우는 8회이고 일부 인용 행이 빠진 경우는 0회다. 행이 남아 있다는 사실은 전체 문맥·순서가 동등하다는 뜻이 아니며, 행 소실 자체도 오판의 원인이라는 증명은 아니다.

외부 API 구현으로 잘못된 가정을 반박하고 실제 원인으로 교정한 추가 정탐은 이번 16회 감사에서 확인하지 못했다. 타입/동명 심볼의 바인딩 교정, 동적 dispatch의 해결, 오탐 제거에 따른 F1 개선도 별도 입증하지 못했다. 원래 두 조건의 확인 오탐은 모두0이며 미해결이 있어 무오탐을 보장하지 않는다.

## 모든 관측 추가·소실 기록

| 구분 | PR / 반복 / target | 유형 | 비교측 판독 |
|---|---|---|---|
| 추가 | RB_Mirkiux__featkit-17 / r2 / G027 | 기존 근거 재판단 / 직접 귀속 미확인 | complete |
| 추가 | RB_Mirkiux__featkit-17 / r3 / G027 | 기존 근거 재판단 / 직접 귀속 미확인 | complete |
| 추가 | RB_allan-mobley-jr__gimmes-468 / r1 / G004 | 기존 근거 재판단 / 직접 귀속 미확인 | complete |
| 추가 | RB_brexhq__CrabTrap-15 / r2 / G001 | 기존 근거 재판단 / 직접 귀속 미확인 | complete |
| 소실 | RB_brexhq__CrabTrap-15 / r2 / G002 | 인용 행 전체 유지 | complete |
| 소실 | RB_cli__cli-13175 / r1 / G000 | 인용 행 전체 유지 | complete |
| 추가 | RB_cli__cli-13175 / r3 / G000 | 변경 전 구현 확보 | judge_failed |
| 추가 | RB_fentas__b-136 / r1 / G021 | 공통 AST 본문 배치·재노출 | complete |
| 소실 | RB_fentas__b-136 / r1 / G020 | 인용 행 전체 유지 | complete |
| 추가 | RB_fentas__b-136 / r2 / G021 | 공통 AST 본문 배치·재노출 | judge_failed |
| 추가 | RB_fentas__b-136 / r3 / G021 | 공통 AST 본문 배치·재노출 | judge_failed |
| 추가 | RB_gtronset__tablo-legacy-m3u-30 / r1 / G000 | 다른 상태 갱신 경로 확보 | complete |
| 소실 | RB_gtronset__tablo-legacy-m3u-30 / r2 / G015 | 인용 행 전체 유지 | complete |
| 추가 | RB_kutanti__litmusai-67 / r1 / G000 | 기존 근거 재판단 / 직접 귀속 미확인 | complete |
| 추가 | RB_kutanti__litmusai-67 / r2 / G002 | 기존 근거 재판단 / 직접 귀속 미확인 | complete |
| 소실 | RB_kutanti__litmusai-67 / r3 / G002 | 인용 행 전체 유지 | complete |
| 소실 | RB_muir__libschema-353 / r3 / G031 | 인용 행 전체 유지 | complete |
| 소실 | RB_obot-platform__obot-6351 / r1 / G002 | 인용 행 전체 유지 | complete |
| 추가 | RB_obot-platform__obot-6351 / r3 / G002 | 기존 근거 재판단 / 직접 귀속 미확인 | complete |
| 추가 | RB_obot-platform__obot-6351 / r3 / G005 | 기존 근거 재판단 / 직접 귀속 미확인 | complete |
| 추가 | RB_shartma2__TTP_v2-33 / r2 / G000 | 기존 근거 재판단 / 직접 귀속 미확인 | complete |
| 추가 | RB_shartma2__TTP_v2-33 / r2 / G001 | 기존 근거 재판단 / 직접 귀속 미확인 | complete |
| 추가 | RB_sirosfoundation__go-wallet-backend-76 / r1 / G003 | 입력·호출자 경로 확보 | complete |
| 소실 | RB_sirosfoundation__go-wallet-backend-76 / r1 / G001 | 인용 행 전체 유지 | complete |

## 다음에 확인할 인과 비교

새로 확보한 `_probe_device`·HTTP 핸들러·BASE 오류 처리 본문만 순서대로 제거/추가하는 reviewer 비교가 필요하다. 같은 source·metadata·예산에서 본문 유무를 바꿔야 해당 유형의 F1 기여를 말할 수 있다. 이번에는 기존 실행 로그의 사후 감사까지만 수행했다.

원형과 기계 검사: 24개 전이·인용·조회 원장 · 기존 집계 · 기존 해석·구간

입력 참조 92개 SHA 보존 확인: 통과. Frozen 실험의 코드·입력·출력·채점은 수정하지 않았다.
