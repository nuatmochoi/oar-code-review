import json
from pathlib import Path
R=Path(__file__).resolve().parent
read=lambda p:json.loads(p.read_text())
r=read(R/'results.json');a=read(R/'audit.json');t=['# 실제 배포 입력을 확인하는 리뷰 — 후속 실험 결과','','동일한 정의 탐색(D) 증거로 기존 리뷰(baseline)와 짧은 사실 근거표를 작성한 리뷰(grounded)를 비교했다. gpt-5.6-sol / low, 한 번 호출, 증거 1,600 reference tokens를 동일하게 적용했다. grounded는 실제 입력·실행 분기·변경 전후 관측값을 명시한다. prompt와 schema를 함께 변경하므로 개별 효과를 분리한 실험은 아니다.','']
t+=['**전면 교체는 보류한다.** 회귀 검사에서 오탐 3건을 제거하고 쌍 성공을 5/10에서 7/10으로 늘렸지만, 실행 witness상 결함인 F04를 기권으로 바꿔 검출이 9/10에서 8/10으로 줄었다. 새 4쌍은 두 방식 모두 4/4여서 추가 개선을 확인하지 못했다.','','다음 개선의 우선순위는 추가 재검토보다 receiver의 생성·주입 지점과 고차 함수의 handler 연결을 입력에 포함하는 것이다. F04는 connection.update의 실제 메서드 연결이 없고, F08은 전달된 missing_image의 producer 헤더가 없다. 근거표가 unknown으로 남긴 사실을 해결해야 한다. 실제 연결이 미확정이라는 이유만으로 원래 발견한 결함을 자동 삭제하는 기본 정책으로 쓰지 않는다.','']
for split,title in [('regression_10','이미 관측한 10쌍: 회귀 검사'),('fresh_N4','새 함수 4쌍: 통제 변경')]:
 t+=['## '+title,'','| 조건 | 근거 있는 결함 검출 | 안전 조건 오탐 | 기권 | 모두 맞힌 쌍 | 최종 리뷰 |','|---|---:|---:|---:|---:|---:|']
 for arm,c in r['summary'][split].items():
  n=c['families'];t.append(f"| {arm} | {c.get('grounded_detection',0)}/{n} | {c.get('false_alarm',0)}/{n} | {c.get('abstained',0)}/{n*2} | {c['complete_correct_pairs']}/{n} | {c['final_reviews']}/{n*2} |")
 t+=['','고정한 실제 배포 조건까지 증명해야 검출로 인정했다. 일반적 결함 원인만 맞힌 조건부 경고는 별도 기록했다. 기권은 안전 판정이 아니다.','']
t+=['## 사례별 결과','','| 사례 | baseline 안전 / 결함 | grounded 안전 / 결함 |','|---|---|---|']
names={'safe_no_issue':'이슈 없음','grounded_detection':'근거 있는 검출','false_alarm':'오탐','abstained':'기권','ungrounded_alarm':'조건부 경고','blocked':'무효/차단','missed_no_issue':'검출 놓침'}
for cid in sorted({x['case_id'] for x in r['rows']}):
 vals=[]
 for arm in ['baseline','grounded']:
  xs=[x for x in r['rows'] if x['case_id']==cid and x['arm']==arm];vals.append(' / '.join(names[x['outcome']] for label in ['safe','unsafe'] for x in xs if x['label']==label))
 t.append('| '+cid+' | '+' | '.join(vals)+' |')
t+=['','## 호출 비용과 검사','','| 구분·조건 | 호출 | 입력 tokens | cached 입력 tokens | 출력 tokens |','|---|---:|---:|---:|---:|']
for stage,s in a['operational'].items():
 for arm,c in s['arms'].items():t.append(f"| {stage} · {arm} | {c['calls']} | {c['input_tokens']:,} | {c['cached_input_tokens']:,} | {c['output_tokens']:,} |")
t+=['','전체 출력 tokens는 baseline 5,464 → grounded 11,985로 약 2.2배였다. 호출 평균 소요 시간도 약 9.1초 → 12.9초였다. 같은 호출 수가 같은 비용이나 지연을 뜻하지 않는다.','','provider usage에는 Codex 시스템 입력이 포함되며, cached 입력은 입력 tokens의 일부다. 한 번 호출의 조건을 맞췄지만 출력 길이와 token 비용까지 동일하지는 않다. 금액은 계산하지 않았다.','','새 witness 32회에서 원본 AST 함수 본문을 실행해 실제 base/head 관측값을 검증했다. 모델에는 witness·라벨·정답을 제공하지 않았다. 저장 디렉터리를 미리 만들고 casefold 파일 이름 충돌을 검사했다. 입력·정책 해시, 응답 schema, 인용 ID, status/list, 근거표의 이슈 번호와 changed 표기 관계를 검사했다. 근거표의 사실적 정확성은 작성자가 읽고 판정했다. 자동 관계 검사만으로 사실의 정확성이 보장되지는 않는다.','','56/56 호출과 최종 리뷰가 모두 유효했고 관측된 도구 호출은 0회였다. 입력·정책 해시는 유지됐으며 schema·인용·이슈 번호 관계 검사에 실패가 없었다. 근거표에는 사실과 반대인 관측값은 발견하지 않았지만, F04 안전 world의 정확한 receiver 경로와 F08 결함 world의 이미지 경로 분기를 증거보다 확정적으로 쓴 중간 표현 두 건을 별도로 기록했다. 근거표가 존재한다고 모든 사실이 입증된 것은 아니다.','','## 해석 범위','','10쌍은 이미 관측한 사례를 다시 실행한 회귀 검사이며 개선 개발의 영향을 받았다. 새 4쌍도 이전 corpus의 동일한 ComfyUI 파일에서 다른 함수에 간단한 변경을 주입했다. 같은 함수가 아닌 target을 사용했지만 독립 저장소·자연 PR·고난도 benchmark가 아니다. 단일 모델·단일 시행·작성자 판정이므로 일반 성능 향상이나 통계적 유의성을 주장하지 않는다.','','동일한 과거 D prompt를 다시 호출했는데 F07 안전 판정이 이전 실행과 달라졌다. 이 결과 역시 한 시행이며 반복 실행으로 변동성을 확인해야 한다. baseline은 과거에 호출한 prompt를 재사용했고 grounded는 새 prompt여서 cache 이력이 비용 비교에 영향을 줄 수 있다.','','새 scope는 arguments()의 고정값을 명시적으로 제한했다. 두 조건에 똑같이 적용했지만 이전 H4와 scope 표현이 다르므로 과거 H4 결과와 새 N4를 직접 비교하지 않는다. 실제 제품 전체 리뷰에서는 다른 지원 입력도 유효한 범위일 수 있으므로 고정 배포 기준의 오탐 분류를 그대로 적용하지 않는다.','','## 자료','','- [정책](PROTOCOL_KO.md), [freeze](freeze.json), [실행 검사](witnesses.json)','- [결과](results.json), [작성자 판정](annotations.json), [감사](audit.json)','- [프롬프트·schema·실행기](experiment.py)','']
(R/'REPORT_KO.md').write_text('\n'.join(t));print(R/'REPORT_KO.md')
