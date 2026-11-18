"""Before any reviewer call, preserve jump observations with the same metadata cap."""
import json,sys
from pathlib import Path
R=Path(__file__).resolve().parent;ROOT=R.parents[1];sys.path.insert(0,str(ROOT))
from ace_review.context_adaptive import digest,token_count
read=lambda p:json.loads(p.read_text())
assert not list((R/'raw').glob('*.json'))
original=read(R/'freeze.json')
assert all(digest((ROOT/p).read_text())==h for p,h in original['files'].items())
assert not (R/'freeze_source_only_before_observations.json').exists()
(R/'freeze_source_only_before_observations.json').write_text(json.dumps(original,indent=2)+'\n')
CAP=400
for pp in sorted((R/'packets').glob('*.json')):
 p=read(pp)
 for arm in ['AST','LSP']:
  lines=[];selected=[]
  for i,jump in enumerate(p[arm+'_audit']['jumps'],1):
   eid=f'E-NAV-{i:02d}';targets='; '.join(q+'::'+symbol for q,symbol in jump['targets']) or 'unresolved in this analysis'
   line=f'[{eid}] head {jump["source_file"]}:{jump["line"]}, UTF16 column {jump["utf16_character"]}, symbol {jump["called"]}: static definition candidates -> {targets}.'
   proposed='\n'.join(lines+[line])
   if token_count(proposed)>CAP:break
   lines.append(line);selected.append(eid)
  text='\n'.join(lines);p[arm+'_observations']=text;p[arm+'_observation_audit']={'reference_tokens':token_count(text),'cap':CAP,'visible_query_ids':selected,'total_queries':len(p[arm+'_audit']['jumps']),'note':'Prefix of original query order, atomic records; same format/selection rule in both arms. No runtime-dispatch assertion and no label/witness information.'}
 pp.write_text(json.dumps(p,ensure_ascii=False,indent=2)+'\n')
# No reviewer results are observed and all source bodies/policies/labels remain unchanged.
p=R/'experiment.py';s=p.read_text();old="+'\\n[EVIDENCE]\\n'+p[arm.upper()];row=";new="+'\\n[EVIDENCE]\\n'+p[arm.upper()]+'\\n[STATIC DEFINITION OBSERVATIONS — NOT RUNTIME PROOF]\\n'+p[arm.upper()+'_observations'];row=";assert old in s;s=s.replace(old,new);p.write_text(s)
p=R/'audit.py';s=p.read_text();s=s.replace("text=p[arm.upper()];state=", "text=p[arm.upper()];observations=p[arm.upper()+'_observations'];state=");s=s.replace("re.findall(r'E-[A-Za-z0-9_-]+',text)","re.findall(r'E-[A-Za-z0-9_-]+',text+'\\n'+observations)");s=s.replace("+'\\n[EVIDENCE]\\n'+text", "+'\\n[EVIDENCE]\\n'+text+'\\n[STATIC DEFINITION OBSERVATIONS — NOT RUNTIME PROOF]\\n'+observations");p.write_text(s)
p=R/'PROTOCOL_KO.md';p.write_text(p.read_text()+'''\n## 모델 호출 전 jump 관측 포함\n\n소스 본문만 주면 정의가 이미 검색된 경우 native jump의 호출 위치→정의 관계가 모델에 보이지 않으므로, 첫 모델 호출 전에 양 arm의 정적 질의 관측을 추가한다. 동일한 형식의 E-NAV-* 기록을 원래 query 순서의 prefix로 최대400 reference tokens 제공한다. 기록 하나는 source 호출 위치·UTF-16 column·symbol·in-corpus static definition 후보만 포함한다. 위치가 다른 함수라도 기록 형식은 동일하다. unresolved는 분석 미해결이지 함수 부재/안전의 증명이 아니다. 여러 후보는 runtime 확정이 아니다. 원문소스1600 + 질의metadata400을 양 arm에 동일하게 적용한다. metadata도 인용 감사 대상이다. 전체 native request/response는 trace에 남지만 모델에는 제한된 기록만 제공한다. 관련 instruction은 동일하게 static-not-runtime-proof로 표기한다.\n\n이 변경은 모델 호출0회에서 source preparation 후 반영했다. source-only 준비 freeze는 별도 보존하며 source/labels/schema/질의 backend/탐색 순서는 변경하지 않는다. 최종 physical sharing은 source와 관측metadata가 모두 동일한 world에만 적용하고 reviewer 호출 전에 다시 freeze한다.\n''')
packets=[read(p) for p in (R/'packets').glob('*.json')];shared={p['case_id']+'_'+p['world']+'_lsp':p['case_id']+'_'+p['world']+'_ast' for p in packets if p['AST']==p['LSP'] and p['AST_observations']==p['LSP_observations']}
f=original;f['shared_identical_inputs']=shared;f['planned_physical_calls']=len(packets)*2-len(shared);f['observation_cap']=CAP;f['total_context_cap']=1600+CAP;f['observation_change_before_model_calls']=True
for name in ['experiment.py','PROTOCOL_KO.md','add_observations.py','audit.py']:
 p=R/name;f['files'][str(p.relative_to(ROOT))]=digest(p.read_text())
for p in (R/'packets').glob('*.json'):f['files'][str(p.relative_to(ROOT))]=digest(p.read_text())
(R/'freeze.json').write_text(json.dumps(f,indent=2)+'\n')
print(json.dumps({'logical_reviews':len(packets)*2,'planned_physical_calls':f['planned_physical_calls'],'identical_full_inputs':len(shared),'source_changed_worlds':sum(p['AST']!=p['LSP'] for p in packets),'observation_changed_worlds':sum(p['AST_observations']!=p['LSP_observations'] for p in packets),'reviewer_calls_so_far':0}))
