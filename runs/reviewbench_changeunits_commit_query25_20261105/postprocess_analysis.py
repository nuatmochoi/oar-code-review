"""Post-hoc mechanical analysis only. Never imported by the reviewer pipeline."""
import collections,json,statistics,time
from experiment import ROOT,load,write,unit_packet,commit_packet,source_tokens
from diff48 import diff_evidence
from warning_tracker import physical_rows
from report import update,covered
from freeze_run import verify
def rowkeys(evidence):
 result=set()
 for e in evidence:
  rows,errors=physical_rows(e)
  if not errors:result.update((e['snapshot'],e['path'],n,text)for n,text in rows.items())
 return result
def main():
 verify();comparison=update();diag=load(ROOT/'audit/change_unit_diagnostic.json');private=load(ROOT/'private/old_gold_coverage.json');packets=[load(ref['path'])for ref in load(ROOT/'public/manifest.json')]
 public=[]
 for p in packets:
  u=unit_packet(p);c=commit_packet(p);public.append(dict(index=p['index'],unit_count=u['total'],plan_units=len(u['supplied']),commits=c['total_commits'],supplied_commits=len(c['supplied'])))
 detail=[];linked=[]
 if set(('B','C'))<=set(comparison['arms']):
  bset=covered(load(ROOT/'arms/B/scoring/results.details.json'));cset=covered(load(ROOT/'arms/C/scoring/results.details.json'));added=cset-bset
  scoring={r['pr_key']:r for r in load(ROOT/'arms/C/scoring/results.details.json')}
  for p in packets:
   i=p['index'];native=load(ROOT/'native'/f'{i:02d}'/'context.json');ledger=load(ROOT/'native'/f'{i:02d}'/'query_ledger.json');base=rowkeys(p['prefix']['evidence']+diff_evidence(p['case']['diff']));after=rowkeys(native['evidence']+diff_evidence(p['case']['diff']));new=after-base
   bodies=[e for e in native['evidence']if e['kind']=='implementation_body'and rowkeys([e])&new]
   decoded=load(ROOT/'arms/C/review'/f'{i:02d}'/'decoded.json');ev={e['id']:e for e in load(ROOT/'arms/C/review'/f'{i:02d}'/'delivered_evidence.json')}
   official=[f for f in decoded['findings']if f['regression_anchor'].get('status')=='bound'and f['regression_anchor'].get('snapshot')=='HEAD']
   used_rows=set();findings_with_fresh_citations=0
   for n,f in enumerate(official):
    fresh=[]
    for loc in f['supporting_evidence']:
     e=ev.get(loc.get('evidence_id'))
     if not e:continue
     keys={k for k in rowkeys([e])if loc['start_line']<=k[2]<=loc['end_line']};novel=keys&new
     if novel:fresh.append(dict(location=loc,new_lines=sorted(k[2]for k in novel)));used_rows|=novel
    if fresh:
     findings_with_fresh_citations+=1
     grade=scoring[p['key']]['candidate_findings'][n]
     newgold=[j for j in grade['covered_golden_indices']if(p['key'],j)in added]
     if newgold:linked.append(dict(index=i,key=p['key'],finding_index=n,title=f['title'],explanation=f['explanation'],fresh_citations=fresh,new_gold_indices=newgold,mechanical_cooccurrence_only=True))
   detail.append(dict(index=i,key=p['key'],questions=len(ledger['queries']),deferred=len(ledger['deferred']),new_physical_rows=len(new),new_implementation_bodies_with_novel_rows=len(bodies),findings_with_fresh_support_citations=findings_with_fresh_citations,used_novel_rows=len(used_rows),source_tokens=source_tokens(native['evidence']),unresolved_records=sum(len(q.get('unresolved',[]))for q in ledger['queries'])))
 result=dict(posthoc=True,generated_at=time.time(),public_packets=public,old_missed_anchor_diagnostic=private['summary'],body_fraction_PR_median=statistics.median(r['units_with_complete_head_body']/r['units']for r in diag['rows']),question_fraction_PR_median=statistics.median(r['units_with_native_question']/r['units']for r in diag['rows']),query_mechanism=detail,new_gold_with_novel_support_citations=linked,causal_or_semantic_truth_proven=False)
 (ROOT/'audit/posthoc_mechanism.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
 lines=['','## 미탐·본문·질문 연결의 사후 진단','',f'원래 미탐315개 중 위치가 diff에만 있던 항목180개, 추가 완전 본문 안에도 있던 항목93개, 둘 다에서 위치를 확인하지 못한 항목42개였다. 이는 위치 가시성의 분류이며 충분한 원인 증거의 유무를 판정한 것은 아니다. 미탐 중 기존 결함(pre-existing)23개는 이번 S의 범주 확대와도 별개로 원래 introduced/exacerbated 제한이 유지된다.','',f'PR별 중앙값으로 보면 추가 완전 본문 연결 단위 비율은 {100*result["body_fraction_PR_median"]:.2f}%, native 질문 앵커 단위 비율은 {100*result["question_fraction_PR_median"]:.2f}%다. 전체 합계는 변경 단위1,015개인 한 대형 PR의 영향을 크게 받는다.','',f'계획 입력은 whole-record 한도 안에서 {sum(r["plan_units"]for r in public)}/{sum(r["unit_count"]for r in public)}개 변경 단위를 별도 목록으로 제공했다. 전체 diff는 최종 reviewer에 모두 전달했다. 커밋은 {sum(r["supplied_commits"]for r in public)}/{sum(r["commits"]for r in public)}개 전체 메시지·파일목록을 제공했고 {sum(r["supplied_commits"]==r["commits"]for r in public)}/25개 PR에서 전체 커밋 문맥이 들어갔다. 이 비교는 커밋별 원시 patch나 AST 차이를 추가한 실험은 아니다. 미포함 항목을 품질 기준으로 삭제하지 않았으며 기록된 토큰 한도에 따른 누락이다.','']
 if detail:
  lines += [f'C의25개 중 새 물리 소스 행을 얻은 PR은 {sum(r["new_physical_rows"]>0 for r in detail)}개, 새 행이 있는 구현 본문을 얻은 PR은 {sum(r["new_implementation_bodies_with_novel_rows"]>0 for r in detail)}개, 최종 supporting 인용에서 새 행을 쓴 PR은 {sum(r["findings_with_fresh_support_citations"]>0 for r in detail)}개다.', '',f'B 대비 추가 gold 탐지와 새 supporting 행 인용이 함께 관측된 보고는 {len(linked)}개다. 기계적 동시 관측이며 새 행의 의미적 필요성이나 인과적 효과를 자동 보장하지 않는다. 새 ID만 생기고 같은 diff 행을 재제공한 경우를 새 정보로 세지 않았다.','']
 lines += ['커밋별 파일 목록은 최종 변경 파일과 겹치는 경로로 제한했다. 커밋 메시지와 이 동시 변경 경로의 효과를 검증한 것이며, 커밋별 전체 patch·중간 상태의 실행·최종 변경 밖의 파일 확장 효과는 검증하지 않았다.','', '실제 비용에는 실행 순서에 따른 prompt cache 이득과 쓰기 비용이 포함된다. B/C의 청구액이 낮더라도 알고리즘 자체가 그만큼 저렴하다는 cold-cache 비교는 아니다. 논리 토큰에는 cached input도 포함한다.','',f'[사후 기계적 전이·인용 감사]({ROOT}/audit/posthoc_mechanism.json). 이 분석 파일은 동결 후 생성했으며 모델 입력·수집 정책·scorer·원시 결과는 변경하지 않았다.','']
 path=ROOT/'RESULTS_KO.md';text=path.read_text()+'\n'.join(lines);path.write_text(text);(ROOT.parents[1]/'paper/REVIEWBENCH_CHANGEUNITS_COMMIT_QUERY25_20261105_KO.md').write_text(text)
 print(json.dumps(dict(arms=list(comparison['arms']),query_PRs=len(detail),linked_new_gold_reports=len(linked)),ensure_ascii=False))
if __name__=='__main__':main()
