from pathlib import Path
import collections,hashlib,json,time
from cost_postprocess import summarize
from freeze_run import verify
ROOT=Path(__file__).resolve().parent;OLD=ROOT.parent/'reviewbench_official25_lsp_20261105'
def load(p):return json.loads(Path(p).read_text())
def f1(m,k='augmented'):
 p=m[k+'_precision'];r=m[k+'_recall'];return 2*p*r/(p+r)if p is not None and r is not None and p+r else 0 if p==r==0 else None
def pct(x):return '—'if x is None else f'{100*x:.2f}%'
def save(p,v):
 p.parent.mkdir(parents=True,exist_ok=True)
 with p.open('x')as f:f.write(v)if isinstance(v,str)else json.dump(v,f,ensure_ascii=False,indent=2)
def main():
 verify();a=load(OLD/'scoring/results.json');b=load(ROOT/'scoring/results.json');assert a['pr_count']==b['pr_count']==25;assert a['eval_config']['golden_hash']==b['eval_config']['golden_hash']
 da=load(OLD/'scoring/results.details.json');db=load(ROOT/'scoring/results.details.json');cost=summarize();prior=load(OLD/'audit/cost.corrected.json');assert cost['unknown_calls']==0 and cost['known_usd']<=50 and cost['known_usd']+prior['known_usd']<=70
 preservation=load(ROOT/'audit/preservation.json')
 for p,h in preservation['files'].items():assert hashlib.sha256(Path(p).read_bytes()).hexdigest()==h,p
 def covered(details):
  out=set()
  for row in details:
   gold=load(ROOT/'upstream/golden'/f"{row['pr_key']}.json")['findings']
   for f in row['candidate_findings']:
    for n in f['covered_golden_indices']:
     if gold[n]['tp_fp']=='tp':out.add((row['pr_key'],n))
  return out
 ca,cb=covered(da),covered(db);assert len(ca)==a['micro']['overall']['golden_covered_count'];assert len(cb)==b['micro']['overall']['golden_covered_count']
 statuses=collections.Counter(f['status']for row in db for f in row['candidate_findings']);false=statuses['matched_fp']+statuses['novel_fp'];am=a['micro']['overall'];bm=b['micro']['overall'];prefix_rows=[]
 for p in sorted((ROOT/'work').glob('*/out/*/agent/lsp.context.json')):
  c=load(p);prefix=load(p.parent/'protected.prefix.json');assert c['protected_initial_ids_retained'];assert {x['id']for x in prefix['evidence']}<={x['id']for x in c['evidence']}
  prefix_rows.append(dict(case=c['case_id'],prefix_source_tokens=prefix['source_tokens'],final_source_tokens=c['source_tokens'],added_source_tokens=c['source_tokens']-prefix['source_tokens'],protected=True,queries=len(c['request_records']),deferred=len(c['deferred_requests']),cost=c['cost'],incremental_cost=c['physical_incremental_cost']))
 assert len(prefix_rows)==25
 score=dict(complete=True,PRs=25,model='gpt-5.6-sol',baseline_grounded_F1=f1(am,'grounded'),new_grounded_F1=f1(bm,'grounded'),baseline_augmented_F1=f1(am),new_augmented_F1=f1(bm),F1_delta=f1(bm)-f1(am),baseline_precision=am['augmented_precision'],new_precision=bm['augmented_precision'],baseline_recall=am['augmented_recall'],new_recall=bm['augmented_recall'],baseline_false_findings=2,new_false_findings=false,retained_gold_targets=len(ca&cb),lost_gold_targets=len(ca-cb),added_gold_targets=len(cb-ca),known_usage_usd=cost['known_usd'],combined_usage_usd=cost['known_usd']+prior['known_usd'],conservative_upper=cost['settled_upper'],physical_calls=sum(v['calls']for v in cost['by_phase'].values()),unknown_calls=0,prefix_preserved=25,human_review=False,official_leaderboard=False,scoring='official P/R harmonic mean',accepted_for_recall=bool(f1(bm)>=f1(am)and bm['augmented_recall']>am['augmented_recall']and false<=2))
 save(ROOT/'audit/cost.corrected.json',cost);save(ROOT/'audit/prefix-comparison.json',prefix_rows);save(ROOT/'comparison.json',score);save(ROOT/'completion.json',score)
 lines=['# 공식25개: 기존 LSP6k vs 핵심·일반 병행12k','','## 가설','', '기존 LSP 근거를 모두 보존하고 변경 statement·조건·호출에서 정의·참조 본문을 더 찾으면, 재현율과 F1을 높일 수 있는가?','','## 과정','', '동일25개·동일commit·동일 public diff·동일 모델 gpt-5.6-sol low·동일 최종 prompt/schema·리뷰1회·동일 공식 judge/gold다. 기존 publicly collected prefix만 재사용하고 이전 finding/정답을 입력하지 않았다. 중요 후보2회 후 일반 후보1회를 교대하고 모든 후보/보류를 기록했다. 추가 본문 상한12k, native48, file256, query20초, native180초. 본문상한·조회정책·native상한이 함께 바뀐 묶음 비교이며 각 요소의 독립 효과가 아니다.','','## 결과','', '| 조건 | Grounded F1 | Augmented F1 | 정밀도 | 재현율 | 오탐 | 사용량 비용 |','|---|---:|---:|---:|---:|---:|---:|',f"| 기존 AST/LSP6k·리뷰1회 | {pct(f1(am,'grounded'))} | {pct(f1(am))} | {pct(am['augmented_precision'])} | {pct(am['augmented_recall'])} | 2 | ${prior['known_usd']:.4f} |",f"| 기존본문 보존+핵심·일반12k·리뷰1회 | {pct(f1(bm,'grounded'))} | {pct(f1(bm))} | {pct(bm['augmented_precision'])} | {pct(bm['augmented_recall'])} | {false} | ${cost['known_usd']:.4f} |",'',f"원 gold 정탐 보존 {len(ca&cb)}/{len(ca)}, 소실 {len(ca-cb)}, 추가 {len(cb-ca)}. source prefix 보존25/25와 결함 정탐 보존을 구분한다. AI matcher/판독 변동을 포함한1반복 개발 결과이며 인간검토0이다.",'',f"이번 API 사용량 기반 ${cost['known_usd']:.4f}, 이전 ${prior['known_usd']:.4f}와 합산 ${score['combined_usage_usd']:.4f}. 이번 보수 정산상한 ${cost['settled_upper']:.4f}, usage미상0. 캐시 쓰기 포함한 공개요율 후처리이며 계정 invoice는 조회하지 않았다.",'',f"F1·재현율 증가 및 오탐 비증가를 동시에 관측했는가: {score['accepted_for_recall']}. 이 규칙은 성공 선언의 근거를 명시하기 위한 요약이며 통계적 유의성/일반화 채택 판정이 아니다.",'','## 가설에 대한 판단','',('관측 결과는 재현율 개선 방향을 지지한다. 본문량과 탐색 순위 효과를 별도로 분리하고 반복·미사용 PR에서 확인해야 한다.'if score['accepted_for_recall']else'재현율·F1·오탐 조건을 모두 만족하지 못했다. 추가 본문 확보와 최종 정탐의 유지/소실을 구분해 원인을 분석해야 한다.'),'','## 원시 산출물','',f'- [새 공식 채점]({ROOT}/scoring/results.json)',f'- [경고별 판독]({ROOT}/scoring/results.details.json)',f'- [본문 보존·수집비용]({ROOT}/audit/prefix-comparison.json)',f'- [API 사용량·비용]({ROOT}/audit/cost.corrected.json)',f'- [동결 명세]({ROOT}/PROTOCOL_KO.md)',f'- [기존 결과]({OLD}/RESULTS_KO.md)','', '공식 소프트웨어의 로컬25개 AI 판독이며219개×3회 leaderboard와 다르다. 이전 자체 원인/유효보고F1과 공식 Grounded/Augmented는 정의가 다르다.','']
 report='\n'.join(lines);save(ROOT/'RESULTS_KO.md',report);save(ROOT.parents[1]/'paper/REVIEWBENCH_GUARDED12K_25_COMPARISON_20261105_KO.md',report);print(json.dumps(score),flush=True)
if __name__=='__main__':main()
