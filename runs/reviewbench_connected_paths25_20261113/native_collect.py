"""Paired collection inside the frozen image; common source read only once."""
import copy,json,time
from pathlib import Path
from entrypoint import snapshot,command
from connected_context import Collector,candidates,expand
from common import BUDGETS,source_tokens,write_new

ROOT=Path('/work/out/agent')


def ledger(records):
    return [dict(question=r['question'],selected_evidence_ids=r['answer'].get('selected_evidence_ids',[]),
                 already_available_evidence_ids=r['answer'].get('already_available_evidence_ids',[]),
                 unresolved=r['answer'].get('unresolved',[]),callee_binding=r['answer'].get('callee_binding'),
                 receipt=r['answer'].get('query_receipt')) for r in records]


def main():
    start=time.monotonic();x=json.loads((ROOT/'input.json').read_text());p=x['packet'];case=copy.deepcopy(p['case'])
    repo=Path('/work/repo');base=command(['git','-C',str(repo),'merge-base',p['pr']['base'],p['pr']['head']]).decode().strip()
    assert base==case['snapshots']['BASE']['commit']
    for side,commit in [('HEAD',p['pr']['head']),('BASE',base)]:
        actual=snapshot(repo,commit,None if side=='HEAD' else ROOT/'base')
        assert actual['files']==case['snapshots'][side]['files']
        case['snapshots'][side]=actual
    prefix=p['prefix'];prior_wall=sum(s.get('seconds',0) for s in prefix.get('stages',{}).values())
    caps={**BUDGETS,'max_source_tokens':10000,'lsp_source_tokens':10000,'max_metadata_tokens':1600,
          'max_native_calls':64,'max_native_seconds':240,'max_file_reads':256,'max_wall_seconds':900}
    common_caps=dict(caps)
    for key,used in [('max_native_calls',prefix['cost']['native_calls']),('max_file_reads',prefix['cost']['file_reads'])]:
        common_caps[key]=int(used+(caps[key]-used)/2)
    common_caps['max_native_seconds']=prefix['cost']['native_seconds']+(240-prefix['cost']['native_seconds'])/2
    common_caps['max_source_tokens']=int(source_tokens(prefix['evidence'])+(10000-source_tokens(prefix['evidence']))/2)
    tool=Collector(case,ROOT/'common/queries',common_caps,initial_state=prefix);tool.started-=prior_wall
    common_start=time.monotonic();records=[];deferred=[]
    try:
        for i,q in enumerate(x['questions']):
            if (tool.stats['native_calls']>=common_caps['max_native_calls']-2 or
                tool.stats['file_reads']>=common_caps['max_file_reads']-8):
                deferred=x['questions'][i:];break
            req={**q,'claim_id':q['unit_id']};records.append(dict(question=q,answer=tool.query(req)))
    finally:tool.close()
    common=tool.export();protected={e['id'] for e in prefix['evidence']}
    assert protected<={e['id'] for e in common['evidence']}
    write_new(ROOT/'common/context.json',common);write_new(ROOT/'common/raw_queries.json',records)
    write_new(ROOT/'common/bindings.json',tool.binding_audit)
    write_new(ROOT/'common/query_ledger.json',dict(queries=ledger(records),deferred=deferred))
    common_seconds=time.monotonic()-common_start
    # Enumerate a shared seed catalog once, including its measured read cost.
    enum=Collector(case,ROOT/'enumeration',caps,initial_state=common)
    enum.started-=prior_wall+common_seconds
    seeds,errors=candidates(enum,common['evidence'])
    common=enum.export();enum.close()
    write_new(ROOT/'seed_catalog.json',dict(candidates=seeds,errors=errors))
    common_seconds=time.monotonic()-common_start
    for policy in ('FLAT','PATH'):
        out=ROOT/policy;tool=Collector(case,out/'queries',caps,initial_state=common)
        tool.started-=prior_wall+common_seconds;begin=time.monotonic()
        try:result=expand(tool,seeds,policy)
        finally:tool.close()
        context=tool.export();assert protected<={e['id'] for e in context['evidence']}
        write_new(out/'context.json',context);write_new(out/'raw_queries.json',result)
        write_new(out/'bindings.json',tool.binding_audit)
        write_new(out/'query_ledger.json',dict(queries=ledger(records)+ledger(result['records']),
            deferred=deferred+result['deferred'],enumeration_errors=errors+result['enumeration_errors'],
            policy=policy,semantic_truth_guaranteed=False,runtime_probe_calls=0))
        write_new(out/'complete.json',dict(complete=True,common_seconds=common_seconds,
            collection_seconds=common_seconds+time.monotonic()-begin,common_queries=len(records),
            extra_queries=len(result['records']),deferred=len(deferred)+len(result['deferred']),
            source_tokens=source_tokens(context['evidence']),cost=context['cost'],
            incremental_cost={k:context['cost'].get(k,0)-prefix['cost'].get(k,0) for k in context['cost']},
            prefix_preserved=True,common_source_shared=True))
    write_new(ROOT/'complete.json',dict(complete=True,seconds=time.monotonic()-start))


if __name__=='__main__':main()
