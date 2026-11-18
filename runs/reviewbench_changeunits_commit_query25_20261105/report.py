from pathlib import Path
import collections,json,time
from experiment import ROOT,load,write,sha,source_tokens,commit_packet,unit_packet
from budget_proxy import estimated_cost
OLD=ROOT.parent/'reviewbench_official25_lsp_20261105'
LABELS={'A':'기존 LSP 본문+변경 목록·계획/최종2회','B':'A + HEAD 이전 커밋 문맥','C':'B + 질문에 따른 실제 LSP·본문 조회','S':'A와 동일 입력·계획 + 최종 검토 범주 확대'}
def f1(m,k):
 p,r=m[k+'_precision'],m[k+'_recall']
 return 2*p*r/(p+r)if p is not None and r is not None and p+r else 0 if p==r==0 else None
def covered(details):
 out=set()
 for row in details:
  gold=load(ROOT/'upstream/golden'/f'{row["pr_key"]}.json')['findings']
  for finding in row['candidate_findings']:
   for n in finding['covered_golden_indices']:
    if gold[n]['tp_fp']=='tp':out.add((row['pr_key'],n))
 return out
def cost_of_usage(response):return estimated_cost(response.get('usage')or{})['usd_list_rate_estimate']
def fmt(v):return '—'if v is None else f'{100*v:.2f}%'
def update():
 old=load(OLD/'scoring/results.json');oldset=covered(load(OLD/'scoring/results.details.json'));assert len(oldset)==37
 diag=load(ROOT/'audit/change_unit_diagnostic.json');arms={};sets={};costs=[]
 for p in sorted((ROOT/'api').glob('*/admission.json')):
  adm=load(p);receipt=load(p.parent/'receipt.json')if(p.parent/'receipt.json').exists()else adm
  request=load(p.parent/'request.json');u=receipt.get('usage');c=estimated_cost(u or{})
  costs.append(dict(id=adm['id'],phase=adm['phase'],slot=request.get('metadata',{}).get('experiment_slot'),status=receipt['status'],usage=u,seconds=receipt.get('finished_at',time.time())-adm['admitted_at'],usd=c['usd_list_rate_estimate']if c else None,upper=receipt.get('settled_upper',receipt['reserved_usd'])))
 total=dict(known_usd=sum(c['usd']or 0 for c in costs),unknown_calls=sum(c['usd']is None for c in costs),upper=sum(c['upper']for c in costs),physical_calls=len(costs))
 for arm in ('A','B','C','S'):
  folder=ROOT/'arms'/arm/'scoring';p=folder/'results.json'
  if not p.exists():continue
  score=load(p);assert score['pr_count']==25;assert score['eval_config']['golden_hash']==old['eval_config']['golden_hash'];details=load(folder/'results.details.json');cs=covered(details);sets[arm]=cs;m=score['micro']['overall'];statuses=collections.Counter(f['status']for r in details for f in r['candidate_findings']);previous=oldset if arm=='A'else sets['A']if arm in('B','S')else sets['B']
  logical=0.;tokens=0;native=[];cp=[];plans=[];allcap=True
  for ref in load(ROOT/'public/manifest.json'):
   packet=load(ref['path']);i=packet['index'];review=ROOT/'arms'/arm/'review'/f'{i:02d}';pdir=ROOT/'plans'/('history'if arm in('B','C')else'current')/f'{i:02d}'
   for response in (load(review/'response.json'),load(pdir/'response.json')):logical+=cost_of_usage(response)
   complete=load(review/'complete.json');tokens+=complete['logical_tokens'];allcap&=complete['token_cap_respected']
   plans.append(dict(index=i,supplied_units=len(unit_packet(packet)['supplied']),total_units=len(packet['units']),unit_hypotheses=len(load(pdir/'parsed.json')['unit_assessments']),valid_questions=len(load(pdir/'validation.json')['valid_questions']),validation_rejections=len(load(pdir/'validation.json')['rejected'])))
   if arm in('B','C'):
    c=commit_packet(packet);cp.append(dict(index=i,total_commits=c['total_commits'],supplied_commits=len(c['supplied']),omitted=c['omitted_commit_count']))
   if arm=='C':native.append(dict(index=i,**load(ROOT/'native'/f'{i:02d}'/'complete.json')))
  judgeids=set(load(folder/'exit.json')['api_ids']);judgecost=sum(c['usd']or 0 for c in costs if c['id']in judgeids)
  arms[arm]=dict(label=LABELS[arm],PRs=25,grounded_recall=m['grounded_recall'],grounded_F1=f1(m,'grounded'),augmented_F1=f1(m,'augmented'),augmented_precision=m['augmented_precision'],covered=len(cs),gold=m['golden_tp_count'],reports=m['candidate_count'],false_findings=statuses['matched_fp']+statuses['novel_fp'],novel_tp=statuses['novel_tp'],reference='historical'if arm=='A'else'A'if arm in('B','S')else'B',retained=len(cs&previous),lost=len(previous-cs),added=len(cs-previous),retained_vs_historical=len(cs&oldset),lost_vs_historical=len(oldset-cs),added_vs_historical=len(cs-oldset),logical_review_usd=logical,judge_physical_usd=judgecost,logical_review_plus_judge_usd=logical+judgecost,logical_tokens=tokens,all_token_caps_respected=allcap,plans=plans,commit_packets=cp,native=native)
 audit=dict(updated_at=time.time(),arms=arms,physical_cost=total,original_results_preserved=all(sha(p)==h for p,h in load(ROOT/'audit/preservation.json')['files'].items()),human_review=0)
 assert audit['original_results_preserved'];(ROOT/'comparison.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2))
 (ROOT/'audit/cost.json').write_text(json.dumps(dict(**total,rows=costs),ensure_ascii=False,indent=2))
 lines=['# 25개 PR: 변경 단위 진단·커밋 문맥·질문 기반 재조회','','기존 결과를 보존한 새 개발 비교다. 같은 공식25개·고정BASE/HEAD·동일gold352·gpt-5.6-sol low·공식 scorer를 사용한다. 인간검토0,1반복이며 리더보드 제출은 아니다.','',
 '## 변경 단위 누락 진단','',f'전체 {diag["units"]:,}개 단위(텍스트 diff hunk + 메타데이터만 변경된 파일). 전체 diff에는 전부 전달됐다. 추가 완전 HEAD 본문과 연결된 단위 {diag["complete_head_body_units"]}, 기존 native 질문 앵커가 있는 단위 {diag["native_question_units"]}. 모델의 실제 읽기/주의를 관측한 수치가 아니다. 25개 중20개에 복수 커밋이 존재한다.','',
 '## 주 비교: 커밋과 실제 조회의 추가 효과','','모든 새 조건은 계획+최종2회, PR당750k 모델 토큰·source/context12k 공통 상한이다. A/S와 B/C는 각 계획 응답을 공유한다. 과거1회 결과는 별도 참고행이며 새 A와 동일 방법이 아니다.','',
 '| 조건 | gold 탐지/352 | gold 재현율 | Grounded F1 | Augmented F1 | 기준 정탐 유지/소실/추가 | 오탐 | 논리 리뷰+판독 비용 |',
 '|---|---:|---:|---:|---:|---|---:|---:|',
 '| 과거 LSP6k·리뷰1회 (참고) | 37 | 10.51% | 18.90% | 23.59% | — | 2 | $12.6122 |']
 for a in ('A','B','C'):
  if a not in arms:lines.append(f'| {a} · {LABELS[a]} | 진행 중 | — | — | — | — | — | — |');continue
  v=arms[a];lines.append(f'| {a} · {v["label"]} | {v["covered"]} | {fmt(v["grounded_recall"])} | {fmt(v["grounded_F1"])} | {fmt(v["augmented_F1"])} | {v["reference"]}: {v["retained"]}/{v["lost"]}/{v["added"]} | {v["false_findings"]} | ${v["logical_review_plus_judge_usd"]:.4f} |')
 lines+=['','보존/소실/추가는 B−A, C−B의 gold 항목 기준이다. A는 과거1회 결과와 비교한다. 한 보고가 여러 gold를 포괄할 수 있다.','',
 '## 별도 검토 범위 확대','','S는 A와 같은 계획·본문·호출·보고개수 제한에서 최종 system의 검토 범주만 확대한다. 커밋/재조회 효과와 합치지 않는다.','',
 '| 조건 | gold 탐지 | gold 재현율 | Grounded F1 | Augmented F1 | A 정탐 유지/소실/추가 | 오탐 | 논리 리뷰+판독 비용 |','|---|---:|---:|---:|---:|---|---:|---:|']
 if 'S'in arms:
  v=arms['S'];lines.append(f'| S · 검토 범주 확대 | {v["covered"]} | {fmt(v["grounded_recall"])} | {fmt(v["grounded_F1"])} | {fmt(v["augmented_F1"])} | {v["retained"]}/{v["lost"]}/{v["added"]} | {v["false_findings"]} | ${v["logical_review_plus_judge_usd"]:.4f} |')
 else:lines.append('| S | 진행 전/중 | — | — | — | — | — | — |')
 lines+=['','## 실제 비용·한계','',f'새 실험 물리 API {total["physical_calls"]}회, 확인 비용 ${total["known_usd"]:.4f}, usage 미확인 {total["unknown_calls"]}회, 미확인 예약 포함 상한 ${total["upper"]:.4f} / $70. 각 행의 논리 비용에는 공유 계획을 각각 포함하므로 행 합계를 물리 청구 합계로 더하지 않는다. 캐시 쓰기를 포함한 동결 요율 추정이며 invoice 실측은 아니다.', '',
 '본문 수집·미확인 사실 발견·실제 추가 조회·새 본문·최종 인용·정답 증가는 다른 단계다. 단위/커밋 목록의 whole-record 한도와 미포함 항목은 계획 입력에 명시한다. 커밋 메시지나 co-change는 동작 증명이 아니며 HEAD 이후 수정·리뷰 댓글·private gold는 reviewer 입력에 없다. 참조는 실행 edge가 아니다. 과거 source 수집 native 비용은 각 조건의 논리 사용량에 포함하되 물리 재수집 비용과 분리한다.', '',
 f'[동결 명세]({ROOT}/PROTOCOL_KO.md) · [단위별 진단]({ROOT}/audit/change_unit_diagnostic.json) · [조건별 원시 결과·계획·조회 요약]({ROOT}/comparison.json) · [실제 비용 원장]({ROOT}/audit/cost.json)', '']
 report='\n'.join(lines);(ROOT/'RESULTS_KO.md').write_text(report);(ROOT.parents[1]/'paper/REVIEWBENCH_CHANGEUNITS_COMMIT_QUERY25_20261105_KO.md').write_text(report)
 return audit
if __name__=='__main__':print(json.dumps(update()['physical_cost']))
