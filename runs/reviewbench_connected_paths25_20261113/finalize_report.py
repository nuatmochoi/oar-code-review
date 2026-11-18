"""Build the completed report from immutable scores and post-hoc audits."""
import collections, json
from pathlib import Path
ROOT = Path(__file__).resolve().parent

def load(p):
    return json.loads(Path(p).read_text())

def main():
    c = load(ROOT / 'comparison.json')
    done = load(ROOT / 'completion.json')
    post = load(ROOT / 'audit/posthoc-analysis.json')
    breadth = load(ROOT / 'audit/exploration-breadth.json')['sums_over_PR']
    inc = load(ROOT / 'audit/incremental-findings.json')['comparisons']
    assert done['all_conditions_complete'] and len(c['arms']) == 3
    base = (ROOT / 'RESULTS_KO.md').read_text().split('\n<!-- COMPLETED ANALYSIS -->')[0]
    notice = '**완료: 세 조건 각각 25개 PR의 리뷰·공식 판독을 마쳤다. 연결 탐색 또는 같은 본문의 배치 변경이 F1을 높였다는 근거는 얻지 못했다. 이번 개선안을 성능 향상 method로 채택하지 않는다.** 과거 A는 LSP 본문을 사용하는 참고 조건이며 diff-only baseline이 아니다.\n\n'
    if notice not in base:
        title, rest = base.split('\n\n', 1)
        base = title + '\n\n' + notice + rest
    lines = ['\n<!-- COMPLETED ANALYSIS -->', '## 결과가 말해 주는 범위', '', 'FLAT은 A보다 정답표 밖 추가 정탐(novel TP)이 4→10으로 늘어 Augmented F1은 24.12→25.18%가 됐다. 그러나 gold 탐지는 45→43, 오탐은 1→5다. 재현율과 오탐을 함께 개선했다고 볼 수 없다. PATH는 FLAT의 gold 정탐 43개 중 31개를 유지하고 12개를 잃었으며 8개를 추가했다. GROUPED는 FLAT의 28개를 유지하고 15개를 잃었으며 11개를 추가했다.', '', f"세 조건 모두 기존 source prefix를 보존했지만 정탐은 보존하지 못했다. 원문 보존과 모델 판단 보존은 다른 측정 대상이다. GROUPED의 실제 요청 25쌍을 대조해 본문·관계·trace·위치 ID·schema가 동일하고 렌더링 순서 외에는 차이가 없음을 확인했다. 실제 순서가 바뀐 PR은 {done['GROUPED_order_changed_PRs']}/25개다. 나머지도 독립 모델 호출이므로 생성 변동이 포함된다.", '', '공식 점수는 micro 집계다. Grounded precision은 gold와 매칭된 보고 중 TP 비율, recall은 gold 352개 중 탐지한 비율이다. 매칭되지 않은 보고는 Grounded precision에서 제외된다. Augmented는 추가 판독된 novel TP/FP도 반영한다. 표의 오탐은 matched FP와 novel FP의 합계다. 한 보고가 여러 gold를 덮을 수 있으므로 보고 개수와 gold 개수를 혼용하지 않는다.', '', '## 새 본문과 기존 사실 재판단을 분리한 진단', '', '| 비교 | 추가 탐지 gold | 추가 gold와 새 비공백 원문 인용이 함께 있는 보고 | 해석 |', '|---|---:|---:|---|']
    interpretations = {'FLAT-A': '새 원문을 실제 인용한 사례가 있으나 전체 gold 순증은 음수', 'PATH-FLAT': '연결 확장으로 늘어난 정보가 추가 탐지로 이어진 관측은 제한적', 'GROUPED-FLAT': '새 사실 0개. 기존 사실의 재판단·배치·생성 변동에 해당'}
    for x in inc:
        reports = [r for r in x['reports'] if r['mechanism_category'] == 'new_nonblank_source_cited' and 'fp' not in r['official_status'].lower()]
        lines.append(f"| {x['comparison']} | {x['added_gold_count']} | {len(reports)} | {interpretations[x['comparison']]} |")
    lines += ['', '이 표는 인용과 추가 탐지의 **동시 관측**이다. 그 본문을 제거한 재실행이 없으므로 원문 때문에 정탐이 늘었다는 인과 증명은 아니다. 새 원문을 인용하지 않았다는 이유만으로 새 정보의 영향이 전혀 없었다고 단정하지 않는다. 공식 FP 보고가 일부 gold와 겹친 경우에도 FP 상태를 유지했다.', '', f'FLAT에서 새 원문을 인용한 추가 TP에는 layer update 중복 실행, timeout query의 tracing 종료 누락, Visual Studio 배포 workflow의 변경 경로 누락, 특정 설치 선택에서 불필요한 jq 요구가 있다. PATH−FLAT에서는 채팅 상호작용 대신 의존성을 직접 mock·호출하는 테스트 문제가 해당했다. [보고별 근거 행·공식 판정]({ROOT}/audit/incremental-findings.json).', '', '## 더 깊이 보았지만 더 넓게 보지는 못했다', '', '| 실행 관측 · PR별 개수의 합 | FLAT | PATH |', '|---|---:|---:|']
    for key, label in [('executed_followup_queries', '공통 질문 이후 실제 조회'), ('seed_queries', '초기 후보 조회'), ('deeper_queries', '새 본문에서 이어 간 조회'), ('distinct_query_files', '조회 대상 파일'), ('actual_delivered_source_files', '실제 전달 source 파일'), ('deferred_seed_queries', '미방문 초기 후보')]:
        lines.append(f"| {label} | {breadth['FLAT'][key]:,} | {breadth['PATH'][key]:,} |")
    lines += ['', '공통 계획의 유효 질문 228개는 모두 실행했다. PATH는 후속 448회 조회를 실제 수행했으나 일반 초기 후보 방문은 931→494, 조회 대상 파일은 146→131로 줄었다. 이는 연결 후보2:일반 후보1 배분이 깊이와 폭을 교환했다는 관측이다. 새 소스 행은 더 많았지만 더 많은 정탐을 보장하지 않았다. 이 배분이 F1 하락의 유일한 원인이라는 주장은 별도 ablation이 필요하다.', '']
    bounds = {}
    for arm in ('FLAT', 'PATH'):
        totals = collections.Counter()
        rows = []
        for p in sorted((ROOT / 'native').glob('*/' + arm + '/context.json')):
            ctx = load(p)
            cost = ctx['cost']
            flags = dict(native_stop_guard=cost['native_calls'] >= 62, file_stop_guard=cost['file_reads'] >= 248, source_stop_guard=ctx['source_tokens'] >= 9936)
            totals.update({k: int(v) for k, v in flags.items()})
            rows.append(dict(index=int(p.parent.parent.name), final_counters=cost, flags=flags))
        bounds[arm] = dict(counts=dict(totals), per_PR=rows)
    (ROOT / 'audit/final-budget-guards.json').write_text(json.dumps(dict(arms=bounds, interpretation='Post-hoc final counters satisfying frozen stop guards; overlapping, not a mutually exclusive causal stop attribution.'), indent=2))
    lines += [f"수집 종료 시 native 호출 guard에 해당한 PR은 FLAT {bounds['FLAT']['counts']['native_stop_guard']}/25, PATH {bounds['PATH']['counts']['native_stop_guard']}/25개였다. file-read guard는 각각 {bounds['FLAT']['counts']['file_stop_guard']}, {bounds['PATH']['counts']['file_stop_guard']}개였다. 이 수치는 겹칠 수 있는 종료 시 counter 진단이며 단일 종료 원인 판정이 아니다. 모델 토큰을 줄이는 것만으로 native 조회 제한이 해소되지는 않는다.", '', '본문이 큰 경우에는 함수 중간을 조용히 자르지 않고 전체 단위의 수용을 거부했다. whole_body_source_token_cap 미해결 사유 기록은 FLAT 65회, PATH 79회다(고유 함수 수가 아님). 반면 native_lookup_empty도 각각 462회, 459회 기록됐다. 따라서 작은 근거 묶음은 본문 예산 문제를 겨냥할 수 있지만, 잘못된 연결·빈 조회·환경 미해결까지 해결하는 수단은 아니다.', '', '## 불확실성', '', '| 비교 | Grounded F1 차이 | PR bootstrap 95% 구간 |', '|---|---:|---:|']
    for x in post['paired_PR_bootstrap']:
        if x['metric'] == 'grounded_F1':
            lo, hi = x['percentile_95']
            lines.append(f"| {x['comparison']} | {x['observed'] * 100:+.2f}pp | {lo * 100:+.2f} ~ {hi * 100:+.2f}pp |")
    lines += ['', '사후 paired PR bootstrap 10,000회, seed20261113이다. 모든 구간이 0을 포함한다. 한 번의 생성·AI 판독만 실행했으므로 모델/판독 반복 변동을 포함한 유의성 검정으로 확대하지 않는다. 이미 사용한 개발 PR 25개이며, 일반화 및 논문 최종 성능 근거로 삼지 않는다.', '', '## 비용·실패·보존 확인', '', f"- 신규 물리 API 호출 {done['physical_cost']['physical_calls']}회 중 319회 완료, HTTP520 1회는 usage 미확인. 확인 usage 기반 ${done['physical_cost']['known_usd']:.4f}, 미확인 예약 포함 ${done['physical_cost']['upper']:.4f}로 $70 이내다. 청구서 확정액은 아니다.", '- 실패 요청은 입력을 그대로 한 번만 재시도했다. 원래 실패·타이밍·예약을 보존했으며 성공 출력의 품질에 따른 재발급은 없다.', f"- 신규 물리 수집 controller 계측: native RPC {post['collector_new_physical_cost']['native_calls']:,}회, native 시간 {post['collector_new_physical_cost']['native_seconds']:.3f}초, 파일 읽기 {post['collector_new_physical_cost']['file_reads']:,}회, 읽은 source {post['collector_new_physical_cost']['source_bytes_read']:,}bytes, AST parse {post['collector_new_physical_cost']['ast_parses']:,}회. snapshot 준비·hash는 이 counter 바깥이다. SCIP/index/probe 신규 호출0회. native 컴퓨팅의 달러 비용은 별도 가격을 부여하지 않았다.", f"- 동결 파일 {done['frozen_files_verified']}개, 과거 산출물 {done['old_files_unchanged']:,}개 SHA-256 일치. 75개 리뷰·75개 공식 PR 점수 완료. 미종료 admission0, 비용 proxy 종료.", '- 실행 전 native/binding/queue7개, budget2개, 동일 사실 배치2개 점검 및 실제 native smoke를 통과했다. 최종 요청75개 admission 사전 검증, 복구 guard3개 점검을 보존했다.', '- 동결 report의 중복 deferred 키 오류는 별도 report_recovery.py에서 읽기 시 처리했다. 신규 source 집계에서 diff를 빠뜨린 부분도 실제 전달된 diff 행을 포함하도록 집계만 교정했다. 모델 입력·출력과 공식 판독 점수는 변경하지 않았다.', '', '추가 점검에서 PR 2개(index01·07)의 인용 handle 결함을 발견했다. Markdown hunk 안의 `---` 때문에 BASE 행 대응이 틀어진 diff handle8개, 마지막 빈 행 제거로 선언 범위146행과 전달 범위145행이 달라진 body handle1개다. 기존 validator가 해당 handle을 배제했으므로 잘못된 ID를 유효하다고 고친 것은 아니다. 전체 diff 원문·145행 본문은 보였지만 해당 handle의 선택 범위가 제한됐다. 이번 결과에는 소급 수정하지 않았고 F1 하락 전체를 이 문제 탓으로 돌릴 수 없다.', '', f'[완료 검증]({ROOT}/completion.json) · [인용 범위 진단]({ROOT}/audit/diff-handle-validation.json) · [사후 통계·판독 원시 세션 보존]({ROOT}/audit/posthoc-analysis.json).', '', '## 다음 설계: 핵심을 보되 근거를 끊지 않기', '', '우선 인용 범위 오류를 수정한 공통 토대에서, 동일 원문·동일 관계를 유지한 위치 목록 압축을 단독 비교한다. 오프라인 exact round-trip으로 catalogue 466,678→104,307 reference tokens, 기본 prompt 합계 1,525,261→1,162,890(23.76% 절감)을 확인했다. 추가 해독 지침·전체 최종 요청·실제 API 비용과 F1은 아직 검증하지 않았다.', '', '그다음 큰 함수를 통째로 넣거나 버리는 선택을 완화한다. 변경 연산·값의 정의/대입·필요 분기·소비 연산을 실제 source 범위로 묶고, 빠진 조건과 전체 함수의 위치를 명시해 다시 조회할 수 있게 한다. 전체 후보 목록은 남기고 여러 변경 영역에 먼저 방문 기회를 주되, 예산 때문에 못 본 항목은 deferred로 표시한다. 요약·정의 위치·reference edge만으로 실행 경로를 확인했다고 처리하지 않는다.', '', '[후속 설계와 무손실 인코딩 진단](../../paper/PROGRESSIVE_EVIDENCE_BUDGET_DESIGN_20261113_KO.md). 후속 설계는 현재 동결 실행에 반영하지 않았으며 새 F1을 주장하지 않는다.', '']
    text = base + '\n'.join(lines)
    (ROOT / 'RESULTS_KO.md').write_text(text)
    (ROOT.parents[1] / 'paper/REVIEWBENCH_CONNECTED_PATHS25_20261113_KO.md').write_text(text)
    print(json.dumps(dict(report=str(ROOT / 'RESULTS_KO.md'), characters=len(text), new_model_calls=0)))
if __name__ == '__main__':
    main()
