"""Freeze retrieval policy before dataset tools; compose immutable final inputs."""
from __future__ import annotations
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import random
import re
import time

from bootstrap import ROOT, PARENT, BUDGET, REPAIR
import cli_transport as legacy
import oar_core as oc
from source_context import load, sha, SourceAccount, CASES, SOURCE_CAP
from retrieval_engine import run_retrieval, ARMS, QUERY_CAP

SEED=2026103021
STATE=re.compile(r'<causal_review_state>(.*?)</causal_review_state>',re.S)

def write(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    legacy.write_new(path,value)

def policy_freeze():
    assert not any((ROOT/'retrieval').glob('*.json')), 'freeze must precede dataset tool execution'
    files=[ROOT/n for n in ('bootstrap.py','source_context.py','retrieval_engine.py','native_executor.py','gap_selector.py','prepare_retrieval.py','PROTOCOL_KO.md','lineage.json')]
    files+=list((ROOT/'prepared/contexts').glob('*.json'))
    assert len(list((ROOT/'prepared/contexts').glob('*.json')))==30
    assert load(ROOT/'verification/integration_tests.json')['passed']
    write(ROOT/'policy_freeze.json',dict(created_at=datetime.now(timezone.utc).isoformat(),
        files={str(p.relative_to(ROOT)):sha(p) for p in sorted(files)},arms=ARMS,
        groups=[p.stem for p in sorted((ROOT/'prepared/contexts').glob('*.json'))],
        query_cap=QUERY_CAP,source_cap=SOURCE_CAP,state_cap=5120,chunk_lines=200,seed=SEED,
        primary='gap-frontier',tool_phase='one sequential pre-final phase, zero additional LLM judgments',
        scope='outcome-selected five-PR fixed-prefix diagnosis, not a holdout or pure-priority ablation'))

def verify_policy():
    for path,expected in load(ROOT/'policy_freeze.json')['files'].items():assert sha(ROOT/path)==expected,path

def context(group):
    c=load(ROOT/'prepared/contexts'/f'{group}.json')
    c['critic']={'raw_response':json.dumps(c['critic_payload'],ensure_ascii=False)}
    return c

def prepare_one(group,arm):
    rid=f'{group}_{arm}';dest=ROOT/'prepared/inputs'/f'{rid}.json'
    if dest.exists():return dict(review_id=rid,preserved=True)
    c=context(group);base=c['prepared'];started=time.monotonic()
    outcome=run_retrieval(c,arm);trace=outcome['trace']
    trace.update(review_id=rid,elapsed_seconds=time.monotonic()-started,
                 policy_freeze_sha256=sha(ROOT/'policy_freeze.json'),actual_model_calls=0)
    cleanup=trace['native_executor']['cleanup']
    assert cleanup.get('passed') or (arm=='replay' and cleanup.get('not_started')),cleanup
    assert trace['new_logical_queries']==len(trace['events'])<=QUERY_CAP
    assert trace['final_source_tokens']<=SOURCE_CAP
    assert trace['initial_source_tokens']==base['inherited_source_exposure']['union_reference_tokens']
    # Exact prefix/source preservation, including original role/instructions.
    raw=outcome['raw'];old=base['raw']
    for part in raw:
        if part!='suffix':assert raw[part]==old[part],(rid,part)
    marker='<retrieval_evidence>\n'
    stripped=re.sub(r'<retrieval_evidence>\n.*?</retrieval_evidence>\n','',raw['suffix'],count=1,flags=re.S)
    assert stripped==old['suffix'],rid
    assert STATE.search(raw['suffix']).group(0)==STATE.search(old['suffix']).group(0)
    tracepath=ROOT/'retrieval'/f'{rid}.json';write(tracepath,trace)
    record=copy.deepcopy(base)
    refs=record['inherited_references']
    oldpath=BUDGET/'prepared/inputs'/f'{group}_repaired_full.json'
    refs['sealed_full_prepared_input']=dict(path=str(oldpath),sha256=sha(oldpath))
    refs['frozen_critic_context']=dict(path=str(ROOT/'prepared/contexts'/f'{group}.json'),sha256=sha(ROOT/'prepared/contexts'/f'{group}.json'))
    composed,_=legacy.compose_input(record['order'],**raw)
    record.update(review_id=rid,arm=arm,raw=raw,composed_sha256=oc._sha(composed),
        retrieval_reference=dict(path=str(tracepath),sha256=sha(tracepath)),
        protected_sources=c['sources'],extra_sources=outcome['extra_sources'],
        provided_evidence_ids=outcome['provided_evidence_ids'],composed_visibility=outcome['composed_visibility'],
        scope='Five outcome-selected PRs, fixed historical critic prefix, common repaired_full state; one fresh final after optional bounded body retrieval.')
    write(dest,record)
    return dict(review_id=rid,queries=trace['new_logical_queries'],added_sources=len(outcome['extra_sources']),
                before=trace['initial_source_tokens'],after=trace['final_source_tokens'])

def prepare():
    verify_policy()
    schema=REPAIR/'prepared/response_schema.json'
    if not (ROOT/'prepared/response_schema.json').exists():write(ROOT/'prepared/response_schema.json',load(schema))
    rng=random.Random(SEED);groups=list(load(ROOT/'policy_freeze.json')['groups']);rng.shuffle(groups)
    blocks=[]
    for i,g in enumerate(groups):
        c=context(g);p=c['prepared'];arms=list(ARMS);rng.shuffle(arms)
        blocks.append(dict(group_id=g,case_id=p['case'],direction=p['direction'],repeat=p['repeat'],dataset=p['dataset'],arms=arms,order=i))
    if not (ROOT/'prepared/jobs.json').exists():write(ROOT/'prepared/jobs.json',dict(seed=SEED,arms=ARMS,blocks=blocks,branches=90,scope='five_PR_gap_body_diagnosis'))
    with ProcessPoolExecutor(max_workers=4) as pool:
        futures=[pool.submit(prepare_one,b['group_id'],a) for b in blocks for a in b['arms']]
        for f in as_completed(futures):print(json.dumps(f.result()),flush=True)
    preflight()

def preflight():
    verify_policy();rows=[]
    for cp in sorted((ROOT/'prepared/contexts').glob('*.json')):
        c=context(cp.stem)
        for arm in ARMS:
            p=ROOT/'prepared/inputs'/f'{cp.stem}_{arm}.json';v=load(p);tr=load(v['retrieval_reference']['path'])
            assert sha(v['retrieval_reference']['path'])==v['retrieval_reference']['sha256']
            account=SourceAccount(v['protected_sources']+v['extra_sources'])
            audit=account.verify_delivery(v['raw'],v['provided_evidence_ids'])
            assert account.tokens==tr['final_source_tokens']<=SOURCE_CAP
            assert v['protected_sources']==c['sources'] and v['state_cap']==5120
            assert STATE.search(v['raw']['suffix']).group(0)==STATE.search(c['raw']['suffix']).group(0)
            cleanup=tr['native_executor']['cleanup']
            assert cleanup.get('passed') or (arm=='replay' and cleanup.get('not_started'))
            assert len(tr['events'])==tr['new_logical_queries']<=QUERY_CAP
            # Every admitted line must be exactly a masked HEAD line returned by a real body request.
            returned={(ev['request'].get('path'),n,t) for ev in tr['events'] if ev['request']['operation']=='read_lines' for n,t in ev['result'].get('lines',[])}
            for s in v['extra_sources']:
                assert all((s['file'],n,t) in returned for n,t in s['lines'])
            if arm=='replay':assert v['raw']==c['raw'] and not tr['events']
            rows.append(dict(review_id=v['review_id'],prepared_sha256=sha(p),queries=tr['new_logical_queries'],
                             old_source_preserved=True,state_identical=True,source_tokens=account.tokens,
                             added_sources=len(v['extra_sources']),cleanup_passed=True))
    assert len(rows)==90
    write(ROOT/'verification/preflight.json',dict(passed=True,prepared_inputs=90,prefixes=30,actual_model_calls=0,
        source_integrity_passed=True,cleanup_passed=True,policy_freeze_sha256=sha(ROOT/'policy_freeze.json'),
        metadata_added=0,rows=rows,scope='composition coverage only; actual transport inputs audited after calls'))
    write(ROOT/'prepared/manifest.json',dict(count=90,cases=CASES,branches=[dict(review_id=r['review_id'],input_sha256=load(ROOT/'prepared/inputs'/f'{r["review_id"]}.json')['composed_sha256']) for r in rows]))
    print(json.dumps(dict(preflight='passed',prepared=90,queries=sum(r['queries'] for r in rows),source_additions=sum(r['added_sources'] for r in rows))),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=['policy_freeze','prepare','preflight']);a=p.parse_args();globals()[a.command]()
