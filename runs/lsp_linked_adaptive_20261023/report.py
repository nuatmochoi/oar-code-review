"""Render the completed experiment only, after audit and explicit annotation."""
import collections,json
from pathlib import Path
R=Path(__file__).resolve().parent
read=lambda p:json.loads(p.read_text())
s=read(R/'scores.json');a=read(R/'audit_all.json');nav=read(R/'retrieval_analysis.json')
assert a['checks']['complete_trajectories'] and len(s['rows'])==288
assert all(a['checks'][k] for k in ['algorithm_hashes','freeze_hashes','workspace_hashes','checkpoint_artifacts_preserved'])
names={'P0Q0':'기본 소스 + 고정 조회','P1Q0':'관계 설명 + 고정 조회','P0Q1':'기본 소스 + 요청 우선 조회','P1Q1':'관계 설명 + 요청 우선 조회'}
calls=sum(x['physical_calls'] for x in s['rows']);statuses=dict(collections.Counter(x['status'] for x in a['rows']))
z=s['summary'];b=z['P0Q0'];c=z['P1Q1'];delta=s['contrasts']['grounded_detection']['combined_vs_baseline']
out=['# Python LSP 관계 설명·후속 조회 실험 결과', '', '2026-10-23. 완료된 12 PR 실험. 원본과 공식 수정본을 양방향으로 비교했다.', '',
 f"조합 구성(P1Q1)의 근거가 충분한 결함 탐지는 **{c['grounded_detection']}/36**, 기본 구성(P0Q0)은 **{b['grounded_detection']}/36**이었다. 차이는 {delta['delta_percentage_points']:.1f}%p이며, PR 단위 bootstrap 95% 구간은 {delta['PR_cluster_percentile_95CI_pp'][0]:.1f}~{delta['PR_cluster_percentile_95CI_pp'][1]:.1f}%p다. 이 작은 목적 표본에서 관계 설명과 요청 우선 조회의 결합이 새 결함 탐지를 안정적으로 높였다고 결론낼 수 없다.", '',
 '## 비교 결과', '', '| 구성 | 결함 지적 원응답 /36 | 근거 충분 탐지 /36 | 정상판단 원응답 /36 | 근거 충분 정상 /36 | 정상본 오탐 /36 | 보류 /72 | 양방향 성공 /36 |', '|---|---:|---:|---:|---:|---:|---:|---:|']
for arm,n in names.items():
 q=z[arm];out.append(f"| {arm} {n} | {q['raw_unsafe_reported']} | {q['grounded_detection']} | {q['raw_safe_no_issue']} | {q['safe_grounded']} | {q['false_positive']} | {q['abstained']} | {q['correct_pair_repeats']} |")
out+=['', '관계 설명만 붙인 P1Q0의 수치가 가장 높지만, 기본 대비 +2회는 decorator 원인 설명 보정 1회와 Flask 검증 사례의 반복 간 차이 1회다. 요청 우선 P0Q1의 +1회도 Flask 사례에서 발생했으며, 해당 세 리뷰는 첫 판단부터 결함을 보고했고 요청은 모두 0개였다. 그 차이를 추가 조회의 탐지 효과로 볼 수 없다. 이번 실행에서 새로 확보한 근거가 처음 놓친 결함의 최종 탐지를 가능하게 했다고 확인된 사례는 없었다.']
out+=['','분모 36은 독립 PR 36개가 아니라 **12 PR × 3회 반복**이다. 원응답은 모델이 지적/정상이라고 답한 횟수이며, 근거 충분 판정은 실제 공급한 코드와 고정 입력으로 원인·분기·base/head 차이를 설명했는지 추가 검토한 값이다. 올바른 의심 위치를 찾아도 잘못된 예외 경로나 빠진 필수 타입 연결은 구분했다. 오탐 0이 나와도 일반적인 오탐률 0을 입증하지 않는다.','',
 '## 무엇이 확인됐는가', '',
 '- **유효한 추가 조회 사례:** `N05_b_P0Q1_r1`은 처음에 숫자 포맷의 정확한 반환값을 보류했다. 실제 definition 조회로 `formats.number_format`, `numberformat.format`을 추가한 뒤 새 근거를 인용하며 정상 수정본의 결과를 확인했다. 이는 정상 여부 확인의 개선이며 새 결함 최초 발견과 구분해야 한다.',
 '- **잘못된 후보 철회:** `N05_a_P1Q1_r2`는 처음 찾은 Decimal 결함을 재판단에서 철회했다. `m=0`인데 조건식의 반대 항을 사용해 `prec=0`을 `prec=4`로 잘못 계산했다. 모델 요청은 0개였으므로 새로운 반박 근거가 나온 결과가 아니다.',
 '- **원인 설명의 수정:** `N03_a_P1Q0_r1`은 처음에 `functools.wraps`가 예외를 던진다고 설명했다가, 최종적으로 decorator wrapper의 `f.__name__` 접근이 예외를 던진다고 바로잡았다. 이미 찾은 결함의 인과 설명 보정이며 최초 탐지가 아니다.',
 '- **고정 조회에서도 새 탐지:** `N05_a_P0Q0_r2`는 처음에 `prec=4`라고 계산해 결함을 놓쳤다가 두 번째 판단에서 `prec=0`으로 바로잡아 지적했다. 결정적인 `floatformat` 본문은 처음부터 공급됐다. 따라서 재판단의 효과와 요청 우선 조회의 효과를 혼동하면 안 된다.',
 '- **추가 조회 후에도 부분적인 확인:** `N04_a_P0Q1_r2`와 `N04_a_P1Q1_r1`은 실제 조회로 `as_view`, `view_is_async`를 확보하고 정상 판정을 냈지만, `http_method_names`, 상속된 handler 구성과 response의 await 가능 여부는 빠져 있었다. 좁은 코드 연결이 늘어난 것과 필요한 분기 증거가 완성된 것을 구분했다.',
 '- **LSP 결과와 공급 소스의 단절:** 배열 사례에서는 native references가 모듈의 import/alias 위치를 반환했지만 현재 선택기는 함수·클래스 밖이라는 이유로 제외했다. 실제 LSP를 사용해도 필요한 연결이 리뷰 입력에 도달하지 않을 수 있다.',
 '- **전체 함수 삭제의 한계:** dict printer 삭제 사례는 base-only 본문을 보존했지만 현재 버전의 대체 dispatch/fallback으로 연결하지 못했다. 이 방향의 head 소스와 native 조회는 0개여서 요청 우선 정책도 확장할 진입점이 없었다.', '',
 '## 실제 조회 변화', '', '| 구성 | 모델의 조회 요청 | 수락 | 실제 요청 우선 조회 | 초기 대비 소스 변경 리뷰 | 고정 조회 대비 소스 변경 리뷰 | 평균 소스 토큰 |', '|---|---:|---:|---:|---:|---:|---:|']
for arm in names:
 q=nav['summary'][arm];out.append(f"| {arm} | {q['requested']} | {q['accepted']} | {q['executed_guided']} | {q['source_changed_vs_initial']} | {q['source_changed_vs_fixed']} | {q['mean_source_tokens']:.0f} |")
out+=['','Q0에서도 첫 리뷰는 조회 요청을 생성하지만 고정 정책이므로 실행 우선순위에 반영하지 않는다. 수락은 대기열 등록, 실제 조회는 중복을 제외한 실행 횟수다. 소스가 늘었다는 사실만으로 탐지 개선의 원인이라고 하지 않았다.','',
 '요청 우선 구성은 각각 72개 중 37개 리뷰에서 고정 정책과 다른 소스를 공급했다. 그러나 요청 우선 native 결과가 비어 있던 조회도 22/92회, 23/91회였다. 평균 소스량은 약 1,410에서 1,760/1,748 reference tokens로 증가했다. 조회 결과가 실제 필요한 연결을 완성하는지 측정하는 지표가 다음 실험에 필요하다.', '',
 '## PR별 근거 충분 탐지', '', '| 사례 | 공개 benchmark PR | P0Q0 /3 | P1Q0 /3 | P0Q1 /3 | P1Q1 /3 |', '|---|---|---:|---:|---:|---:|']
labels=read(R/'labels.json')
for cid,rr in s['per_case'].items():out.append('| '+cid+' | '+labels[cid]['instance_id']+' | '+' | '.join(str(rr[arm]['grounded_detection']) for arm in names)+' |')
out+=['', '## 실행과 검증', '',
 f'- 288개 리뷰 궤적, 실제 모델 호출 **{calls}회**. 상태: `{json.dumps(statuses,ensure_ascii=False)}`. 기존 개발 입력의 transport smoke 1회는 본실험에서 제외했다.',
 f"- 본실험 native definition/references 실제 실행 **{a['actual_native_semantic_requests']}회**. 초기·고정 조회는 입력별로 수행하고 구성/반복 사이에서 재사용했다. 이를 매 리뷰마다 새 native 호출한 것으로 세지 않았다. 모델 응답은 각 단계마다 새로 생성했다.",
 '- 모든 PR의 원본/공식 수정본 계약을 각각 2회씩, 최종 48회 실행해 원본 실패·수정본 통과를 확인했다. 사전 후보 2개는 공식 수정본도 재현 계약을 통과하지 못해 새 모델 호출 전에 교체했으며 초기 시도도 보존했다. 총 14 PR을 스크리닝했다.',
 '- N05의 사전 공동 재현 코드는 첫 입력의 예외에서 멈추므로 원본의 두 번째 입력은 따로 관측하지 못했다. 본실험 종료 후 문자열/Decimal 입력을 각각 분리해 8회 추가 실행했고 모두 원본 실패·수정본 통과였다(`witness_n05_individual.json`). 동결 입력·라벨·모델 응답은 바꾸지 않았다.',
 '- `gpt-5.6-sol`, low, 최대 동시4개, 모델 자체 도구 호출0. 실제 Pyright 1.1.414 조회는 runner가 수행했다. 모든 구성에서 모델 판단2회, LSP 최대12회, 깊이3, 소스 최대5200 reference tokens와 관측800을 적용했다.',
 '- 삭제 전용 diff를 살아 있는 동명 함수에 대응하고 전체 함수 삭제는 base-only로 보존하도록 공통 개선했다. 기존 SymPy 개발 사례의 source0/query0 문제가 source2613/query12로 바뀐 것은 수집 경로의 검증이며, 새 사례 탐지율 개선으로 계산하지 않았다.',
 '- 진행 중 AST 역직렬화의 cyclic GC 병목을 확인해 호출이 없는 지점에서 런타임만 재개했다. 79회 호출·39개 완료 리뷰와 한 리뷰의 첫 응답을 해시로 보존했다. 같은 리뷰의 저장된 첫 응답에서 두 번째 단계만 이어갔으며 중복 모델 호출은 없다. 정책·프롬프트·입력은 변경하지 않았다.',
 f"- 해시·준비·조회·모델 도구·인용 검사는 `audit_all.json`에 기록했다. 검사 결과: `{json.dumps(a['checks'],ensure_ascii=False)}`. `E-ID:줄번호` 형식은 ID와 실제 제공 줄을 확인해 정규화했다.", '',
 '## 다음 개선 우선순위', '',
 '1. **필수 연결을 입력에 보존:** native 결과의 module import/alias, 생성자 `__new__`, 속성 소비 함수, 상속·override 유무를 표현할 클래스 구조를 추가한다. 전체 호출 참조를 무작정 늘리기보다 공개 진입점→변경 분기→관측 결과의 빠진 연결을 우선한다.',
 '2. **후보 철회에 값 근거 요구:** 후보별 입력값·조건식 결과·반환/예외 지점을 유지하고, 새 근거 또는 올바른 재계산 없이 후보를 지우지 않게 한다. `m=0`, `units=-1`, `prec=0`처럼 검사 가능한 값이 대상이다.',
 '3. **삭제 후 대체 경로 연결:** 삭제된 메서드의 소속 클래스와 살아 있는 dispatch/fallback을 현재 버전에서 탐색한다. base 본문 보존만으로는 부족하다.',
 '4. **Python AST 구조 설명은 별도 비교:** 위 연결을 먼저 보완한 뒤 분기·반환·예외 구조 설명의 추가 효과를 따로 측정한다. 이번 실행에는 RL 학습이나 AST 실행 개요 요인이 들어가지 않았다.', '',
 '## 해석의 범위', '',
 '공개 SWE-bench Verified에서 문제와 production patch를 읽고 고른 12개 목적 표본이다. Django5, SymPy5, pytest1, Flask1이며 무작위·블라인드·저장소 holdout이 아니다. 이 프로젝트의 이전 선정 목록과 겹치지 않는다는 뜻의 신규 사례이며 모델 학습 데이터에서 미노출이라는 뜻은 아니다. 원본/수정본의 공개 계약을 주고 특정 입력에 대한 위반만 보도록 했으므로 일반 MR 전체 리뷰 성능과 다르다.', '',
 '근거 검토는 **실험을 작성한 AI 어시스턴트의 비블라인드 검토**이며 독립 전문가 채점이 아니다. 각 응답을 읽은 기록과 이유는 `author_annotations.json`에, 판정 수정은 `annotation_revisions_01.json`, 설명 문구 수정은 `annotation_revisions_02.json`에 보존했다. hidden witness로 리뷰어에게 없는 근거를 채우지 않았다. 좁은 코드 인과와 일반 Python 의미는 허용하고, 분기를 바꾸는 필수 subtype/override/compile 연결은 별도로 요구했다. 따라서 근거 불충분으로 분류한 원응답을 모두 실제로 존재하지 않는 버그라고 해석하면 안 된다. 예컨대 Point의 zip 손실을 찾았어도 이전 코드의 타입 조건을 입증하지 못한 응답은 별도로 남겼다.', '',
 'P는 동일 소스 정책에 붙이는 관계 설명의 효과이고 Q는 두 번째 조회 우선순위의 효과다. unresolved 후보 보존과 두 번 판단은 네 구성의 공통 조건이어서 그 자체의 독립 효과는 이번 비교로 알 수 없다. 상한은 같지만 실제 입력 토큰 수는 다르다. 이전2600토큰 실험보다 소스 상한을 늘렸으므로 과거 수치와 직접 개선율을 계산하지 않는다.', '',
 '참고한 다른 채팅은 “정리 MR 리뷰 memory 설계”(01a08a0d-0ed4-79a3-9ab9-ae55eebf2f3e)다. 그 작업의 명시적 값 전달 연결과 후보별 보강을 실험 요인으로 단순화했다. 이 실행이 해당 제품 전체를 재현한 것은 아니다. “특히 Python을 잘 찾았다”는 현장 피드백은 구체 MR/비교 도구가 확인되지 않은 정성 정보이며 이번 성과 수치에 포함하지 않았다.', '',
 '재현 자료: `PROTOCOL_KO.md`, `algorithm_seal.json`, `freeze.json`, `witness_validation.json`, `inputs/`, `raw/`, `retrieval/`, `author_annotations.json`, `scores.json`, `retrieval_analysis.json`, `runtime_checkpoint.json`.', '']
(R/'REPORT_KO.md').write_text('\n'.join(out));print(str(R/'REPORT_KO.md'))
