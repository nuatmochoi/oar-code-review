"""Fixed source pools and matched ordinary/rebuttal verifier inputs."""
from __future__ import annotations
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import copy
from datetime import datetime,timezone
import json
from pathlib import Path
import random
import re
import time

from bootstrap import ROOT,GAP,BUDGET
import cli_transport as legacy
import oar_core as oc
from source_context import load,sha,SourceAccount,SOURCE_CAP,CASES
from path_engine import run_retrieval
import verification_stage as vs

ARMS=('current','path','rebuttal')
SEED=2026103031
STATE=re.compile(r'<causal_review_state>(.*?)</causal_review_state>',re.S)

def write(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);legacy.write_new(path,value)

def context(group):
    c=load(ROOT/'prepared/contexts'/f'{group}.json')
    c['critic']={'raw_response':json.dumps(c['critic_payload'],ensure_ascii=False)}
    return c

def policy_freeze():
    assert not list((ROOT/'retrieval').glob('*.json'))
    files=[ROOT/n for n in ('bootstrap.py','source_context.py','gap_selector.py','causal_path_selector.py','native_executor.py','path_engine.py','verification_stage.py','prepare_experiment.py','PROTOCOL_KO.md','VERIFIER_CONTRACT_KO.md','lineage.json')]
    files+=sorted((ROOT/'prepared/contexts').glob('*.json'))
    assert len(list((ROOT/'prepared/contexts').glob('*.json')))==30
    assert load(ROOT/'verification/integration_tests.json')['passed']
    write(ROOT/'policy_freeze.json',dict(created_at=datetime.now(timezone.utc).isoformat(),
        files={str(p.relative_to(ROOT)):sha(p) for p in files},arms=ARMS,groups=[p.stem for p in sorted((ROOT/'prepared/contexts').glob('*.json'))],
        source_cap=5200,state_cap=5120,feedback_cap=1800,new_query_cap=6,chunk_lines=200,
        native_location_cap=8,selector_index_file_cap=12,frontier_index_file_cap=12,
        seed=SEED,primary='rebuttal-path',secondary='path-current',
        fixed_model_calls_per_review=2,verification='source-only model advisory; no executable probes and no verdict gate',
        scope='Outcome-selected five-PR diagnostic; current source replay, one shared path source pool for path/rebuttal.'))

def verify_policy():
    for rel,h in load(ROOT/'policy_freeze.json')['files'].items():assert sha(ROOT/rel)==h,rel

def prepare_group(group):
    if all((ROOT/'prepared/inputs'/f'{group}_{a}.json').exists() for a in ARMS):return dict(group=group,preserved=True)
    c=context(group);base=c['prepared'];started=time.monotonic()
    poolpath=ROOT/'retrieval'/f'{group}_path_pool.json'
    if poolpath.exists():
        pool=load(poolpath);outcome=pool['outcome']
    else:
        outcome=run_retrieval(c,'path')
        assert outcome['trace']['native_executor']['cleanup']['passed']
        pool=dict(group_id=group,policy_freeze_sha256=sha(ROOT/'policy_freeze.json'),
                  elapsed_seconds=time.monotonic()-started,physical_execution_count=1,shared_by=['path','rebuttal'],outcome=outcome)
        write(poolpath,pool)
    oldpath=GAP/'prepared/inputs'/f'{group}_gap.json';old=load(oldpath)
    for arm in ARMS:
        rid=f'{group}_{arm}';dest=ROOT/'prepared/inputs'/f'{rid}.json'
        if dest.exists():continue
        record=copy.deepcopy(base);refs=record['inherited_references']
        refs['sealed_full_prepared_input']=dict(path=str(BUDGET/'prepared/inputs'/f'{group}_repaired_full.json'),sha256=sha(BUDGET/'prepared/inputs'/f'{group}_repaired_full.json'))
        refs['frozen_critic_context']=dict(path=str(ROOT/'prepared/contexts'/f'{group}.json'),sha256=sha(ROOT/'prepared/contexts'/f'{group}.json'))
        if arm=='current':
            selected=old;retrieval_ref=copy.deepcopy(old['retrieval_reference'])
            refs['sealed_current_source_input']=dict(path=str(oldpath),sha256=sha(oldpath))
            pool_id=f'{group}_sealed_gap';reused=True
        else:
            selected=outcome;retrieval_ref=dict(path=str(poolpath),sha256=sha(poolpath));pool_id=f'{group}_path_pool';reused=arm=='rebuttal'
        raw=copy.deepcopy(selected['raw']);composed,_=legacy.compose_input(record['order'],**raw)
        record.update(review_id=rid,group_id=group,arm=arm,raw=raw,composed_sha256=oc._sha(composed),
            protected_sources=c['sources'],extra_sources=selected['extra_sources'],provided_evidence_ids=selected['provided_evidence_ids'],
            composed_visibility=selected['composed_visibility'],retrieval_reference=retrieval_ref,retrieval_pool_id=pool_id,
            retrieval_execution_reused=reused,retrieval_is_historical=arm=='current',
            verification_mode='rebuttal' if arm=='rebuttal' else 'ordinary',inherited_initial_draws=1,inherited_second_judgments=1,
            new_verification_judgments=1,new_final_judgments=1,
            scope='Fixed prefix and source pool; one new source verifier plus one new final in each arm.')
        write(dest,record)
        verifier=vs.build_verifier_input(record,record['verification_mode'])
        text,_=legacy.compose_input(record['order'],**verifier['raw'])
        verifier.update(review_id=rid,group_id=group,arm=arm,mode=record['verification_mode'],order=record['order'],
                        composed_sha256=oc._sha(text),prepared_reference=dict(path=str(dest),sha256=sha(dest)))
        write(ROOT/'prepared/verifier_inputs'/f'{rid}.json',verifier)
    return dict(group=group,queries=outcome['trace']['new_logical_queries'],sources=len(outcome['extra_sources']),
                initial_tokens=outcome['trace']['initial_source_tokens'],final_tokens=outcome['trace']['final_source_tokens'])

def prepare():
    verify_policy();groups=list(load(ROOT/'policy_freeze.json')['groups']);rng=random.Random(SEED);rng.shuffle(groups)
    blocks=[]
    for i,group in enumerate(groups):
        p=context(group)['prepared'];arms=list(ARMS);rng.shuffle(arms)
        blocks.append(dict(group_id=group,case_id=p['case'],direction=p['direction'],repeat=p['repeat'],dataset=p['dataset'],arms=arms,order=i))
    if not (ROOT/'prepared/jobs.json').exists():write(ROOT/'prepared/jobs.json',dict(seed=SEED,arms=ARMS,blocks=blocks,branches=90,model_calls=180))
    if not (ROOT/'prepared/verifier_schema.json').exists():write(ROOT/'prepared/verifier_schema.json',vs.response_schema())
    with ProcessPoolExecutor(max_workers=4) as pool:
        futures=[pool.submit(prepare_group,g) for g in groups]
        for f in as_completed(futures):print(json.dumps(f.result()),flush=True)
    preflight()

def preflight():
    from ace_review.protocol_hypothesis import mask_source
    verify_policy();rows=[];checked_sources={}
    for cp in sorted((ROOT/'prepared/contexts').glob('*.json')):
        c=context(cp.stem);pair=[]
        for arm in ARMS:
            p=ROOT/'prepared/inputs'/f'{cp.stem}_{arm}.json';v=load(p);vi=load(ROOT/'prepared/verifier_inputs'/p.name)
            account=SourceAccount(v['protected_sources']+v['extra_sources'])
            assert account.tokens<=SOURCE_CAP
            assert v['protected_sources']==c['sources']
            assert account.verify_delivery(v['raw'],v['provided_evidence_ids'])==v['composed_visibility']
            for s in v['protected_sources']+v['extra_sources']:
                if s['snapshot']!='head':continue
                key=(c['manifest']['root'],s['file'])
                if key not in checked_sources:
                    file=Path(key[0])/key[1]
                    assert sha(file)==c['manifest']['sources'][s['file']],key
                    checked_sources[key]=mask_source(file.read_text(encoding='utf-8',errors='replace')).splitlines()
                assert all(checked_sources[key][n-1]==text for n,text in s['lines']),(v['review_id'],s['evidence_id'])
            assert STATE.search(v['raw']['suffix']).group(0)==STATE.search(c['raw']['suffix']).group(0)
            assert {k:x for k,x in v['raw'].items() if k!='suffix'}=={k:x for k,x in c['raw'].items() if k!='suffix'}
            assert re.sub(r'<retrieval_evidence>\n.*?</retrieval_evidence>\n','',v['raw']['suffix'],count=1,flags=re.S)==c['raw']['suffix']
            assert sha(v['retrieval_reference']['path'])==v['retrieval_reference']['sha256']
            regenerated=vs.build_verifier_input(v,v['verification_mode'])
            assert vi['raw']==regenerated['raw'] and vi['schema']==vs.response_schema()
            if arm=='current':assert v['raw']==load(GAP/'prepared/inputs'/f'{cp.stem}_gap.json')['raw']
            else:pair.append(v)
            rows.append(dict(review_id=v['review_id'],prepared_sha256=sha(p),source_tokens=account.tokens,source_preserved=True,state_identical=True))
        assert pair[0]['raw']==pair[1]['raw'] and pair[0]['extra_sources']==pair[1]['extra_sources']
        assert pair[0]['retrieval_reference']==pair[1]['retrieval_reference']
    pools=[load(p) for p in (ROOT/'retrieval').glob('*_path_pool.json')];assert len(pools)==30
    for p in pools:
        t=p['outcome']['trace'];assert t['new_logical_queries']==len(t['events'])<=6
        assert t['native_executor']['cleanup']['passed']
        for e in t['events']:
            if e['request']['operation']!='read_lines':
                s=e['result'].get('native_candidate_selection')
                if s:assert s['selected_before_hash_read']<=8
    assert len(rows)==90
    write(ROOT/'verification/preflight.json',dict(passed=True,prepared_inputs=90,prefixes=30,source_integrity_passed=True,
        cleanup_passed=True,model_calls=0,shared_source_pairs=30,current_exact_replays=30,path_pools=30,
        logical_queries_physically_executed=sum(p['outcome']['trace']['new_logical_queries'] for p in pools),
        administrative_head_source_files_rechecked=len(checked_sources),rows=rows))
    print(json.dumps(dict(preflight='passed',inputs=90,path_pools=30,matched_verifier_final_calls=180)),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=['policy_freeze','prepare','preflight']);a=p.parse_args();globals()[a.command]()
