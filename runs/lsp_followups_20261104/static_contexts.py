"""One source cap; shared AST prefix; no evaluator labels in retrieval."""
import copy,re,json,time,hashlib
from pathlib import Path,PurePosixPath
from collections import Counter,defaultdict
from common import ROOT,BUDGETS,load,write_new,digest,sha,source_tokens
from symbol_context import ContextCollector
from context48 import Limit,SOURCE_KINDS

ARMS=('D','A3','A6','T6','L6','H6')
LABELS={'D':'공개 diff만','A3':'AST 지역 문맥 3k','A6':'AST 지역 문맥 6k','T6':'AST+문자열 검색 6k','L6':'AST+LSP 정의·참조 6k','H6':'AST+문자열/LSP 혼합 6k'}

def pack(prefix,candidates,cap):
    out=copy.deepcopy(prefix);events=[]
    for b in candidates:
        if b['id']in{x['id']for x in out}:continue
        if source_tokens(out+[b])<=cap:out.append(copy.deepcopy(b));events.append(dict(id=b['id'],state='admitted'))
        else:events.append(dict(id=b['id'],state='deferred_whole_unit_source_budget'))
    assert {e['id']for e in prefix}<={e['id']for e in out}
    return out,events

def lexical(case,base,outdir,lsp_cost):
    limits={**BUDGETS,'max_source_tokens':6000}
    for key in ['native_calls','file_reads']:
        limits['max_'+key]=BUDGETS['max_'+key]-lsp_cost.get(key,0)+base['cost'].get(key,0)
    limits['max_native_seconds']=max(1,BUDGETS['max_native_seconds']-lsp_cost.get('native_seconds',0)+base['cost'].get('native_seconds',0))
    tool=ContextCollector(case,outdir,limits,initial_state=base)
    started=time.monotonic();words=Counter();files={}
    # Query all modified-line identifiers, not defect labels or private claims.
    for snap in ['HEAD','BASE']:
        for path,changed in tool.changed[snap].items():
            if path not in tool.snapshots[snap]['files']or not tool.language_for(path):continue
            try:
                data=tool._parse(snap,path)
                for n in changed:
                    if 1<=n<=len(data['rows']):words.update(re.findall(r'\b[A-Za-z_]\w{2,}\b',data['rows'][n-1]))
            except (ValueError,OSError,SyntaxError,Limit)as exc:tool.unresolved.append(dict(reason='lexical_seed_unresolved',detail=str(exc)))
    stop={'return','None','True','False','self','error','func','package','import','from','class','with','this','const','type','string','bool','nil','else','elif','def','async','await'}
    query=[w for w,n in words.most_common()if w not in stop][:64]
    candidates=[];visited=0
    try:
        # A single bounded batch avoids repeated 32-file scans per symbol.
        for snap in ['HEAD','BASE']:
            paths=tool.snapshots[snap]['files'];changed=tool.changed[snap]
            ordered=sorted((p for p in paths if tool.language_for(p)),key=lambda p:(p not in changed,not any(w.lower()in p.lower()for w in query),p))
            for path in ordered:
                if visited>=64:break
                tool.query_started=time.monotonic();data=tool._read(snap,path);visited+=1
                scores=[(sum(min(row.count(w),3)*(1+1/(1+words[w]))for w in query if w in row),n)for n,row in enumerate(data['rows'],1)]
                hits=sorted(((s,n)for s,n in scores if s>0),reverse=True)[:3]
                if hits:files[snap,path]=hits
        for (snap,path),hits in sorted(files.items(),key=lambda item:(-sum(x[0]for x in item[1]),item[0]))[:24]:
            tool.query_started=time.monotonic()
            try:
                data=tool._parse(snap,path);seen=set()
                for score,n in hits:
                    u=tool._unit_at(data,n)
                    if not u or(u['start_line'],u['end_line'])in seen:continue
                    seen.add((u['start_line'],u['end_line']));before=len(tool.evidence)
                    # Candidate construction has a bounded pool larger than final packing.
                    tool.budgets['max_source_tokens']=24000;tool.active_source_cap=24000
                    tool._emit_unit(snap,path,u,'public_diff_lexical_candidate','lexical_match_not_binding_or_execution_proof')
                    for e in tool.evidence[before:]:
                        if e['kind']in SOURCE_KINDS:candidates.append((score,e))
            except (ValueError,OSError,SyntaxError,Limit)as exc:tool.unresolved.append(dict(reason='lexical_body_unresolved',path=path,detail=str(exc)))
    except (ValueError,OSError,SyntaxError,Limit)as exc:tool.unresolved.append(dict(reason='lexical_batch_budget_stop',detail=str(exc)))
    finally:tool.close()
    state=tool.export();ranked=[e for _,e in sorted(candidates,key=lambda v:(-v[0],v[1]['path'],v[1]['start_line']))]
    evidence,events=pack(base['evidence'],ranked,6000)
    state.update(evidence=evidence,source_tokens=source_tokens(evidence),selection_events=events,lexical_query_terms=query,lexical_files_scanned=visited,seconds=time.monotonic()-started,candidate_evidence=ranked)
    return state

def one(case):
    folder=ROOT/'contexts'/case['id'];done=folder/'packets.json'
    if done.exists():return load(done)
    started=time.monotonic();tool=ContextCollector(case,folder/'native',{**BUDGETS,'ast_source_tokens':3000,'lsp_source_tokens':6000,'max_native_calls':32,'max_file_reads':128})
    base=tool.collect('AST');lsp=tool.collect('LSP');tool.close();closed=tool.export();lsp['cost']=closed['cost']
    a6tool=ContextCollector(case,folder/'ast6',{**BUDGETS,'ast_source_tokens':6000})
    a6=a6tool.collect('AST');a6tool.close();a6['cost']=a6tool.export()['cost']
    lex=lexical(case,base,folder/'lexical',lsp['cost'])
    prefix=base['evidence'];prefixids={e['id']for e in prefix}
    lr=[e for e in lsp['evidence']if e['id']not in prefixids]
    tr=lex['candidate_evidence'];ranks={}
    for ranking in [lr,tr]:
        for i,e in enumerate(ranking,1):
            v=ranks.setdefault(e['id'],dict(evidence=e,score=0));v['score']+=1/(60+i)
    # Initial AST evidence is always retained. Rank only additional evidence.
    union=sorted(ranks.values(),key=lambda v:(-v['score'],v['evidence']['id']))
    hybrid,selection=pack(prefix,[v['evidence']for v in union],6000)
    hcost={k:lsp['cost'].get(k,0)+lex['cost'].get(k,0)-base['cost'].get(k,0)for k in set(lsp['cost'])|set(lex['cost'])|set(base['cost'])}
    assert hcost['native_calls']<=BUDGETS['max_native_calls']
    assert hcost['file_reads']<=BUDGETS['max_file_reads']
    packets={
      'D':dict(evidence=[],cost={},unresolved=[]),
      'A3':base,'A6':a6,'T6':lex,'L6':lsp,
      'H6':dict(evidence=hybrid,cost=hcost,unresolved=lsp['unresolved']+lex['unresolved'],selection_events=selection,ranked_union=[dict(id=v['evidence']['id'],score=v['score'])for v in union])}
    for arm,p in packets.items():
        p['source_tokens']=source_tokens(p['evidence']);p['cap']=0 if arm=='D'else 3000 if arm=='A3'else 6000
        assert p['source_tokens']<=p['cap']
        if arm in ['T6','L6','H6']:assert prefixids<={e['id']for e in p['evidence']}
        write_new(folder/f'{arm}.json',p)
    write_new(done,dict(case_id=case['id'],packets={a:dict(path=str(folder/f'{a}.json'),sha256=sha(folder/f'{a}.json'))for a in ARMS},seconds=time.monotonic()-started,AST_prefix_preserved=True,hybrid_effect_includes_candidate_union=True))
    return load(done)
