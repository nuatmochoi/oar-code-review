"""Post-hoc reporting only. No review, query, or judge calls."""
import collections,hashlib,json,random,statistics,time
from pathlib import Path
from experiment import ROOT,load,write,sha,unit_packet,commit_packet
from freeze_run import verify
from report import covered,f1
import postprocess_analysis

def pct(x):return f'{100*x:.2f}%'
def quantile(xs,p):
 xs=sorted(xs);k=(len(xs)-1)*p;lo=int(k);hi=min(lo+1,len(xs)-1)
 return xs[lo]+(xs[hi]-xs[lo])*(k-lo)
def aggregate(rows,indices):
 keys=('matched_tp_count','matched_count','golden_covered_count','golden_tp_count')
 s={k:sum(rows[i][k]for i in indices)for k in keys}
 p=s['matched_tp_count']/s['matched_count']if s['matched_count']else 0
 r=s['golden_covered_count']/s['golden_tp_count']if s['golden_tp_count']else 0
 return r,2*p*r/(p+r)if p+r else 0

def main():
 verify()
 configurations=[]
 for a in 'ABCS':
  p=ROOT/'arms'/a/'scoring'
  assert load(p/'exit.json')['returncode']==0
  score=load(p/'results.json');assert score['pr_count']==25
  assert all(not r.get('error')for r in score['per_pr'])
  configurations.append(score['eval_config'])
 assert all(x==configurations[0]for x in configurations)
 postprocess_analysis.main()
 comparison=load(ROOT/'comparison.json');arms=comparison['arms'];assert set(arms)==set('ABCS')
 raw=load(ROOT/'audit/cost.json');corrections={}
 if(ROOT/'audit/reservation-corrections.json').exists():corrections.update(load(ROOT/'audit/reservation-corrections.json')['corrections'])
 for p in (ROOT/'audit/input_token_counts').glob('*/audit.json'):
  x=load(p);rid=x['generation_request_id'];assert x['original_request_sha256']==sha(ROOT/'api'/rid/'request.json')
  corrections[rid]=dict(corrected_upper=x['unknown_cost_upper_usd'],basis='Official free complete-input count, plus 8192 input tokens and full output cap',count_audit=str(p))
 rows=[]
 for row in raw['rows']:
  c=corrections.get(row['id'])if row['usage']is None else None
  rows.append(dict(**row,corrected_upper=min(row['upper'],c['corrected_upper'])if c else row['upper'],correction=c))
 cost=dict(known_usd=raw['known_usd'],unknown_calls=raw['unknown_calls'],upper=sum(r['corrected_upper']for r in rows),physical_calls=raw['physical_calls'],global_cap=70,rows=rows,raw_ledger_unchanged=True,invoice_verified=False,free_input_count_requests=len(list((ROOT/'audit/input_token_counts').glob('*/response.json')))+len(list((ROOT/'audit/admission_input_counts').glob('*/response.json'))))
 assert cost['upper']<=70, cost['upper']
 write(ROOT/'audit/cost.corrected.json',cost)
 prior_judge_cost=0.;prior=ROOT/'attempts/S/scoring-first-pass/exit.json';prior_attempts=list((ROOT/'attempts/S').glob('scoring-*-pass/exit.json'))
 if prior_attempts:
  prior_ids={rid for p in prior_attempts for rid in load(p)['api_ids']};prior_judge_cost=sum(r['usd']or 0 for r in rows if r['id']in prior_ids)
  arms['S']['judge_physical_usd']+=prior_judge_cost;arms['S']['logical_review_plus_judge_usd']+=prior_judge_cost
  arms['S']['prior_failed_judge_known_usd']=prior_judge_cost;arms['S']['judge_attempts']=len(prior_attempts)+1
 comparison['physical_cost_raw']=comparison['physical_cost'];comparison['physical_cost']=dict((k,cost[k])for k in ('known_usd','unknown_calls','upper','physical_calls'));comparison['accounting_correction_manifest']=str(ROOT/'audit/cost.corrected.json')
 write(ROOT/'comparison.final.json',comparison)
 packets=[load(ref['path'])for ref in load(ROOT/'public/manifest.json')]
 sets={a:covered(load(ROOT/f'arms/{a}/scoring/results.details.json'))for a in 'ABCS'}
 data={a:{r['pr_key']:r['metrics']['overall']for r in load(ROOT/f'arms/{a}/scoring/results.json')['per_pr']}for a in 'ABCS'}
 ordered=sorted(data['A']);vectors={a:[data[a][k]for k in ordered]for a in 'ABCS'}
 for a in 'ABCS':
  rr,ff=aggregate(vectors[a],range(25));assert abs(rr-arms[a]['grounded_recall'])<1e-12 and abs(ff-arms[a]['grounded_F1'])<1e-12
 rng=random.Random(20261105);draws=[[rng.randrange(25)for _ in range(25)]for _ in range(10000)];pairs=[]
 for before,after in [('A','B'),('B','C'),('A','C'),('A','S')]:
  ds=[tuple(y-x for x,y in zip(aggregate(vectors[before],d),aggregate(vectors[after],d)))for d in draws]
  pairs.append(dict(before=before,after=after,retained=len(sets[before]&sets[after]),lost=len(sets[before]-sets[after]),added=len(sets[after]-sets[before]),retention=len(sets[before]&sets[after])/len(sets[before]),recall_delta=arms[after]['grounded_recall']-arms[before]['grounded_recall'],F1_delta=arms[after]['grounded_F1']-arms[before]['grounded_F1'],recall_CI95=[quantile([d[0]for d in ds],p)for p in [.025,.975]],F1_CI95=[quantile([d[1]for d in ds],p)for p in [.025,.975]]))
 write(ROOT/'audit/paired_bootstrap.json',dict(posthoc=True,seed=20261105,replicates=10000,resampling_unit='PR paired across arms',not_model_or_judge_repeat_uncertainty=True,comparisons=pairs))
 counts=collections.Counter();reasons=collections.Counter();details=collections.Counter();native=collections.Counter();plan_rejections=collections.Counter()
 for p in packets:
  i=p['index'];q=load(ROOT/'native'/f'{i:02d}'/'query_ledger.json');n=load(ROOT/'native'/f'{i:02d}'/'complete.json')
  counts['deferred']+=len(q['deferred'])
  for item in q['queries']:
   counts['executed']+=1;counts['with_selected_evidence']+=bool(item.get('selected_evidence_ids'));counts['with_unresolved']+=bool(item.get('unresolved'))
   for u in item.get('unresolved',[]):reasons[u.get('reason','unknown')]+=1;details[u.get('detail','unspecified')]+=1
  for k,v in n['incremental_cost'].items():
   if isinstance(v,(float,int)):native[k]+=v
  for rej in load(ROOT/'plans/history'/f'{i:02d}'/'validation.json')['rejected']:plan_rejections[str(rej.get('reason','unknown'))]+=1
 mechanism=load(ROOT/'audit/posthoc_mechanism.json')
 audit=dict(query_counts=dict(counts),unresolved_reason_occurrences=dict(reasons),unresolved_detail_occurrences=dict(details),plan_rejections=dict(plan_rejections),incremental_native_cost=dict(native),mechanism=mechanism,AI_source_audit=dict(human_review=0,reviewer='Codex post-hoc source reading; no scorer or output edits',examples=[dict(index=6,gold_indices=[0,4],body_id='E-a236757894b7a6e45178',query_id='Q3',operation='body',specific_native_LSP_increment_demonstrated=False,claim_support='Complete Client.query body shows timeout callback capture and custom-query overload before the new tracing gate',citation_error='First finding cites line661 as the invocation, but line661 is blank; invocation is line660. Original output retained. Other cited nonblank lines and body support the explanation.',runtime_verified=False)]))
 write(ROOT/'audit/final_mechanism.json',audit)
 # Re-verify every logical budget and the identity of preserved old artifacts.
 checks=[]
 for a in 'ABCS':
  for p in packets:
   x=load(ROOT/'arms'/a/'review'/f'{p["index"]:02d}'/'complete.json')
   assert x['token_cap_respected']and x['logical_tokens']<=750000 and x['logical_active_wall_seconds']<=3600
   checks.append(dict(arm=a,index=p['index'],tokens=x['logical_tokens'],active_wall_seconds=x['logical_active_wall_seconds']))
 for p,h in load(ROOT/'audit/preservation.json')['files'].items():assert sha(p)==h
 for p in packets:
  n=load(ROOT/'native'/f'{p["index"]:02d}'/'complete.json');assert n['prefix_preserved']
  assert n['cost']['native_calls']<=64 and n['cost']['native_seconds']<=240 and n['cost']['file_reads']<=256 and n['source_tokens']<=12000
 equality=[]
 for old in (ROOT/'attempts/S').glob('*/*/request.json'):
  i=int(old.parents[1].name);current=ROOT/'arms/S/review'/f'{i:02d}'/'request.json'
  assert load(old)==load(current),(old,current)
  assert not(old.parent/'response.json').exists(),'A returned response was archived for a retry'
  equality.append(dict(index=i,archive=str(old.parent),exact_body_equal=True))
 write(ROOT/'audit/all-recovery-input-equality.json',dict(rows=equality,all_equal=True,no_returned_response_resampled=True))
 for p in (ROOT/'audit').glob('*input-equality*.json'):assert load(p)['all_equal']
 verify()
 text=(ROOT/'RESULTS_KO.md').read_text()
 if prior_judge_cost:
  text='\n'.join(line.rsplit('$',1)[0]+f'${arms["S"]["logical_review_plus_judge_usd"]:.4f} |'if line.startswith('| S · 검토 범주 확대 |')else line for line in text.split('\n'))
 text=text.replace(f'미확인 예약 포함 상한 ${raw["upper"]:.4f} / $70',f'공식 입력 토큰 수로 예약을 보정한 상한 ${cost["upper"]:.4f} / $70')
 text=text.replace(f']({ROOT}/comparison.json)',f']({ROOT}/comparison.final.json)')
 text=text.replace('[실제 비용 원장]', '[보정 전 원시 비용 원장]')
 intro='\n\n주 비교에서 A가 가장 높았다. 커밋 문맥만 추가한 B는 정탐을 잃었고, C의 재조회는 B보다 일부 회복했으나 A를 넘지 못하고 오탐도 늘었다. 이번 결과로 B/C를 채택할 근거는 부족하다. 검토 범주 확대 S는 아래 별도 결과로 해석한다.\n'
 text=text.replace('\n\n기존 결과',intro+'\n기존 결과',1)
 text=text.replace('기존 결과를 보존한 새 개발 비교다.', '기존 결과를 보존한 새 개발 비교다. [ReviewBench](https://github.com/review-bench/ReviewBench)의 기존 공식25개 실행에서 보존한 입력·초기 본문을 재사용해, 새 로컬 비교 파이프라인에서 reviewer와 공식 judge를 실행했다.')
 text=text.replace('물리 API', '물리 생성 API')
 extra=['','## 정탐 보존과 변화의 크기','','| 비교 | gold 재현율 변화 | Grounded F1 변화 | 앞 조건 정탐 보존 | 소실 / 추가 | PR 부트스트랩 F1 차이 95% 구간 |','|---|---:|---:|---:|---:|---|']
 for p in pairs:
  extra.append(f'| {p["after"]}−{p["before"]} | {100*p["recall_delta"]:+.2f}pp | {100*p["F1_delta"]:+.2f}pp | {p["retained"]}/{len(sets[p["before"]])} ({pct(p["retention"])}) | {p["lost"]} / {p["added"]} | {100*p["F1_CI95"][0]:+.2f} ~ {100*p["F1_CI95"][1]:+.2f}pp |')
 extra+=['','부트스트랩은 사후 진단으로 25개 PR을 쌍으로 10,000회 재표집했다. 모델·판독자 재실행 불확실성은 포함하지 않으며, 새 데이터셋에 대한 일반화·확증적 유의성 근거로 사용하지 않는다.','', '## 질문 실행에서 막힌 지점','',f'유효 질문 {counts["executed"]}개를 실행했고 도구 예산 때문에 deferred된 질문은 {counts["deferred"]}개다. 질문 {counts["with_selected_evidence"]}개에 selected evidence가 있었고, {counts["with_unresolved"]}개에는 하나 이상의 unresolved가 있었다. 두 집합은 겹친다. 계획 단계의 거부 {sum(plan_rejections.values())}개도 원문과 이유를 보존했다.','', '| 미해결 기록 유형 | 발생 수 |','|---|---:|']
 for k,v in reasons.most_common():extra.append(f'| {k} | {v} |')
 extra+=['','한 질문에 여러 기록이 가능하므로 이 표의 합은 실패 질문 수가 아니다. query_unresolved55건의 세부 원인은 요청 column이 심볼 토큰을 가리키지 않음33건, 실제 파일 언어 미지원21건, LSP 응답 timeout1건이다. 이55건을 모두 LSP 서버 조회 실패로 부르지 않는다. 환경 정보만 읽어 runtime binding을 확정하지 않은 경우처럼 올바르게 미해결을 남긴 기록도 포함된다. 예산을 늘리는 것만으로 심볼 모호성·미지원 값 전달·잘못 지정한 토큰 위치는 해결되지 않는다. 현재 구현은 한 번의 질문→조회이므로 실패한 조회를 고쳐 다시 묻는 두 번째 순환은 없다.','', '## 새 본문이 실제 보고와 연결된 사례','', '`node-postgres` PR에서 A/B는 보고가 없었고, C는 `Client.query`의 완전한 HEAD 본문을 요청한 Q3(body 조회)로 2개 gold를 탐지했다. read-timeout에서 이전 callback을 직접 호출하는 경로, callback 없는 custom Query가 새 tracing 조건에서 빠지는 경로가 해당한다. 두 보고 모두 기존 전체 diff·기존 본문에 없던 행을 supporting evidence로 인용했다.','', '이는 질문 기반의 완전 함수 본문 조회가 유용했던 구체 사례다. 이 Q3의 연산은 body 조회이므로 LSP definition/references의 독립적인 추가 효과로 계산하지 않는다. C는 AST·LSP·본문 조회를 묶은 조건이다. C−B 추가 gold17개 중 이 기계적 인용 연결로 설명된 것은2개이며, 나머지15개를 새 본문 효과로 단정할 수 없다.','', '사후 AI 소스 점검에서는 첫 보고의 인용661행이 빈 줄이고 실제 호출은660행인 오류도 확인했다. 다른 인용과 완전 본문은 주장과 관련됐지만, controller의 유효 범위 결속이 인용의 의미적 정확성까지 보장하지는 않았다. 원시 보고·공식 점수는 고치지 않았다. 인간검토0, runtime 검증0이다.','', '## 비용·호출·수집 사용량','', '| 조건 | 논리 모델 토큰(input+output) | 논리 호출 | active 시간 합 / 최장 PR | 추가 native 호출 / 초 |','|---|---:|---:|---|---|']
 for a in 'ABCS':
  subset=[x for x in checks if x['arm']==a]
  extra.append(f'| {a} | {arms[a]["logical_tokens"]:,} | 50 | {sum(x["active_wall_seconds"]for x in subset):.1f}s / {max(x["active_wall_seconds"]for x in subset):.1f}s | '+(f'{native["native_calls"]} / {native["native_seconds"]:.2f}s'if a=='C'else'0 / 0s')+' |')
 extra += ['', f'성공 응답이 있는100개 논리 경로는750k 토큰·3,600초 이하였다. A/B/C의 reviewer는 각 계획+최종2호출로 완료했다. S의 미확인 실패·재시도까지 합친 물리 토큰 수는 확정할 수 없어, S가 동일한 실제 총 토큰을 썼다고 주장하지 않는다. S의 물리 벽시계에도 실패 대기 시간이 추가됐다. 이 한계 때문에 S는 별도 운영 복구를 포함한 기술 결과로 본다. 공유 계획은 논리상 각 조건에 포함하며 실제 청구는 한 번이다. C의 추가 file read{native["file_reads"]}회, source bytes{native["source_bytes_read"]:,}, AST parse{native["ast_parses"]}회다. 초기 수집은 기존 결과를 재사용했고 그 native/query 비용과 stage 시간은 각 논리 PR 예산에 포함했다. SCIP/index0, probe0이다.','',f'새 실험의 확인된 API 사용량 요율 추정은 **${cost["known_usd"]:.4f}**, usage 미확인{cost["unknown_calls"]}회의 보수적 예약까지 포함한 상한은 **${cost["upper"]:.4f}**다. $70을 올리지 않았다. 실패·재시도 비용은 이 전역 합계에 포함하며, 표의 정상 응답 논리 비용과는 구분한다. 원시 영수증의 byte 기준 예약은 보존하고 [보정 원장]({ROOT}/audit/cost.corrected.json)에만 계산 근거를 추가했다.','', '검토 범주 S의 HTTP503/무응답 및 로컬 예산 거절은 모든 원문을 보존했다. 반환된 응답을 품질에 따라 재발급하지 않고, 응답을 받지 못한 요청만 같은 입력으로 복구했다. 내부 reviewer $55 분할 한도는 전체 $70 공동 상한 안에서 대기로 바꿨다. 모델·prompt·schema·gold·수집 정책은 변경하지 않았다. 이 운영 보정은 사후 amendment로 분리했다.','', '503 요청은 미청구라고 가정하지 않았다. 공식 무료 input-token counting으로 같은 입력481,217토큰을 확인하고8,192토큰의 추가 여유와 최대 출력 전체를 예약했다. 이 계수값은 같은 입력으로 성공한 응답의 input usage481,217과 일치했다. [입력 계수 문서](https://developers.openai.com/api/docs/guides/token-counting), [무료 계수 안내](https://help.openai.com/en/articles/4936856-understanding-and-counting-tokens), [모델 요율·문맥 한도](https://developers.openai.com/api/docs/models/gpt-5.6-sol). 비용은 계정 invoice 대조 전 추정이다.','', '## PR별 gold 탐지와 문맥 전달','', '| PR | gold | 변경 단위 | 계획에 제공한 단위 | 커밋 제공/전체 | A | B | C | S |','|---|---:|---:|---:|---|---:|---:|---:|---:|']
 for p in packets:
  u=unit_packet(p);c=commit_packet(p);k=p['key'];values=[data[a][k]['golden_covered_count']for a in 'ABCS']
  extra.append(f'| {k} | {data["A"][k]["golden_tp_count"]} | {u["total"]} | {len(u["supplied"])} | {len(c["supplied"])}/{c["total_commits"]} | '+' | '.join(str(v)for v in values)+' |')
 extra+=['','## 해석과 다음 우선순위','','이번 커밋 문맥은 메시지·최종 변경 경로만 추가했다. 따라서 커밋별 patch·중간 상태·추가 파일 확장까지 무효라고 결론 내릴 수 없다. B−A는 커밋 입력이 계획과 최종 판단 모두에 미친 결합 효과다. C−B는 동일 계획에서 조회 결과를 추가한 효과이며 A에 조회만 추가한 독립 조건은 이번에 없다.','', '우선순위는 (1) 질문의 파일·토큰·심볼을 controller가 정확한 후보에 결속하고 조회 실패를 수정해 재조회하기, (2) 기존 정탐 후보를 유지한 채 새 근거로 지지·반박을 갱신해 이유 없는 소실을 줄이기, (3) 별도 조건으로 커밋별 patch 또는 검토 범위·보고 제한의 영향을 검증하기다. 모두 아직 성능이 검증된 개선안은 아니다.','', 'Grounded F1은 공식 gold에 연결된 보고의 precision과 gold TP352개의 recall을 조화평균한 공식 지표다. Augmented F1은 gold 밖의 novel TP도 반영한다. 과거 자체 원인 F1·유효 보고 F1과 서로 같은 지표로 바꾸어 부르지 않는다. 오탐은 matched_fp+novel_fp 보고 수이고, 정탐 보존은 gold 항목 집합의 교집합이다.','', f'[동결 명세]({ROOT}/PROTOCOL_KO.md) · [최종 구조화 결과]({ROOT}/comparison.final.json) · [질문·본문·인용 감사]({ROOT}/audit/final_mechanism.json) · [PR별 원시 계획·응답·판독]({ROOT}/arms) · [보존·예산 검증]({ROOT}/completion.json)','']
 extra += ['', 'native/AST/file 비용 표는 reviewer의 소스 수집에 해당한다. 판독자의 read/grep/find 등 도구 호출과 세션은 scoring/judge.log 및 classifier-output/sessions에 별도로 보존했다. 무료 입력 계수 API는 생성 호출 수에 합치지 않고 cost.corrected.json의 free_input_count_requests에 별도로 기록했다.','']
 if prior_attempts:extra += [f'S는 첫 공식 판독에서24개 PR을 완료하고1개가 네트워크 timeout·byte 예약 거절로 실패해 strict 점수가 발행되지 않았다. 두 번째 판독에도 응답 헤더 없는 호출3개가 생겨 해당 로컬 연결을180초 이상의 대기 후 닫고 모든 미확인 비용을 보존했다. 마지막 복구는 cache routing key만 제외하고 모델 입력·설정·도구·schema가 모두 같은 S 요청에 대해 가장 먼저 완료된 응답을 재사용했다. 실제 재사용은 {len(list((ROOT/"audit/judge_replays").glob("*.json")))}회였다. S의 표 비용에는 앞선 실패 판독의 확인 비용 ${prior_judge_cost:.4f}도 포함했다. 전체 판독 시도는 {len(prior_attempts)+1}회이며, 부분 점수에서 유리한 값을 고르지 않고 완주한 마지막 공식 판독 결과만 사용했다. reviewer25개 응답은 다시 생성하지 않았다.','']
 extra += ['## 실행·보존 검증','', '모델 실행 전 단위 점검5개와 Docker native smoke2개를 통과했다. 예산 복구의 미확인 비용 보존·전역 상한 테스트2개 및 실제 입력 계수 보정 점검3개도 통과했다. 동결302개 파일과 과거 결과277개 파일의 SHA를 재검증했다. 전체100개 조건별 PR은25개씩 채점됐고 실패 PR을 제외해 점수를 내지 않았다.','', f'[사전 검증]({ROOT}/audit/preflight.json) · [복구 테스트]({ROOT}/audit/recovery-unit-tests.log) · [입력 계수 검증]({ROOT}/audit/exact-accounting-check.json) · [모든 복구 요청의 입력 동일성]({ROOT}/audit/all-recovery-input-equality.json) · [최초 실패 상태]({ROOT}/audit/first-pass.status.json)','']
 text+='\n'.join(extra)
 (ROOT/'RESULTS_KO.md').write_text(text);(ROOT.parents[1]/'paper/REVIEWBENCH_CHANGEUNITS_COMMIT_QUERY25_20261105_KO.md').write_text(text)
 result=dict(phase='complete',completed_at=time.time(),PRs=25,arms=list('ABCS'),scored_logical_PRs=100,frozen_files_verified=len(load(ROOT/'freeze.json')['files']),old_artifacts_SHA_verified=len(load(ROOT/'audit/preservation.json')['files']),all_native_prefixes_preserved=True,successful_path_budgets_only=True,scope_failed_attempt_physical_tokens_unknown=True,budgets=checks,known_usd=cost['known_usd'],cost_upper=cost['upper'],global_cost_cap=70,complete_review_repetitions=1,complete_judge_repetitions=1,scope_judge_attempts=len(prior_attempts)+1,human_review=0,report_sha256=sha(ROOT/'RESULTS_KO.md'),report=str(ROOT.parents[1]/'paper/REVIEWBENCH_CHANGEUNITS_COMMIT_QUERY25_20261105_KO.md'),original_failed_status=str(ROOT/'audit/first-pass.status.json'))
 write(ROOT/'completion.json',result)
 (ROOT/'status.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
 print(json.dumps(dict(cost={k:cost[k]for k in ['known_usd','upper','unknown_calls']},results={a:{k:arms[a][k]for k in ['covered','grounded_F1','false_findings']}for a in 'ABCS'},pairs=pairs),ensure_ascii=False))
if __name__=='__main__':main()
