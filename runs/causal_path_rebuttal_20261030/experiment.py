"""Matched two-call source verification and final review experiment."""
from __future__ import annotations
import argparse
from concurrent.futures import ProcessPoolExecutor,as_completed
import copy
from datetime import datetime,timezone
import fcntl
import hashlib
import json
from pathlib import Path
import re

from bootstrap import ROOT,GAP
import cli_transport as legacy
import reliable_transport as transport
import oar_core as oc
from source_context import SourceAccount
import verification_stage as vs

ARMS=('current','path','rebuttal')
WORKERS,INTERVAL=4,3
MODEL='gpt-5.6-sol'
STATE=re.compile(r'<causal_review_state>(.*?)</causal_review_state>',re.S)

def load(p):return json.loads(Path(p).read_text())
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def require(ok,message):
    if not ok:raise ValueError(message)
def write(p,v):
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);legacy.write_new(p,v)
def same_or_new(p,v):
    if Path(p).exists():require(load(p)==v,f'preserved artifact changed: {p}')
    else:write(p,v)
def reference(ref):
    require(Path(ref['path']).is_absolute() and sha(ref['path'])==ref['sha256'],f'reference SHA: {ref["path"]}')
    return load(ref['path'])
def ref(p):return dict(path=str(p),sha256=sha(p))

def expected_jobs(jobs):
    expected={}
    require(jobs['branches']==90 and len(jobs['blocks'])==30,'job count')
    for b in jobs['blocks']:
        require(len(b['arms'])==3 and set(b['arms'])==set(ARMS),'job arms')
        for arm in b['arms']:
            rid=b['group_id']+'_'+arm;require(rid not in expected,'duplicate review')
            expected[rid]=dict(review_id=rid,group_id=b['group_id'],case=b['case_id'],direction=b['direction'],repeat=b['repeat'],dataset=b['dataset'],arm=arm)
    require(len(expected)==90,'expected 90 reviews')
    return expected

def parent_refs():
    lineage=load(ROOT/'lineage.json')
    return [dict(name=k,path=str(Path(lineage[k])/'parent_grading/completed_result_seal.json'),sha256=lineage[k+'_seal_sha256']) for k in ('parent','repair','budget','gap')]

def verify_parents(refs,full=True):
    checked={}
    for r in refs:
        seal=reference(r)
        if not full:continue
        workspace=Path(seal['workspace'])
        for rel,digest in seal['files'].items():
            p=(workspace/rel).resolve();require(p.is_relative_to(workspace.resolve()),'unsafe sealed relative path')
            if p in checked:require(checked[p]==digest,'conflicting inherited seal')
            else:require(sha(p)==digest,f'inherited file changed: {p}');checked[p]=digest
    return dict(seals=len(refs),unique_sealed_files_verified=len(checked),full=full)

def validate_prepared(p,expected=None):
    if expected:require(all(p.get(k)==v for k,v in expected.items()),'prepared identity')
    mode='rebuttal' if p['arm']=='rebuttal' else 'ordinary';require(p['verification_mode']==mode,'verification mode')
    for r in p['inherited_references'].values():reference(r)
    trace=reference(p['retrieval_reference'])
    if p['arm']=='current':
        old=load(GAP/'prepared/inputs'/f'{p["group_id"]}_gap.json')
        require(p['raw']==old['raw'] and p['extra_sources']==old['extra_sources'],'current source replay changed')
        require(p['retrieval_is_historical'] is True,'current new query mislabeled')
    else:
        require(trace['group_id']==p['group_id'] and trace['shared_by']==['path','rebuttal'],'shared pool identity')
        outcome=trace['outcome'];require(p['raw']==outcome['raw'] and p['extra_sources']==outcome['extra_sources'],'path pool composition')
        tr=outcome['trace'];require(tr['new_logical_queries']==len(tr['events'])<=6,'query cap')
        require(tr['native_executor']['cleanup']['passed'],'native cleanup')
    account=SourceAccount(p['protected_sources']+p['extra_sources'])
    require(account.tokens<=5200,'source cap')
    require(account.verify_delivery(p['raw'],p['provided_evidence_ids'])==p['composed_visibility'],'prepared visibility')
    c=load(ROOT/'prepared/contexts'/f'{p["group_id"]}.json')
    require(p['protected_sources']==c['sources'],'protected source change')
    require(STATE.search(p['raw']['suffix']).group(0)==STATE.search(c['raw']['suffix']).group(0),'state change')
    require(all(p['raw'][k]==c['raw'][k] for k in ('system','shared','prompt')),'raw prefix change')
    require(re.sub(r'<retrieval_evidence>\n.*?</retrieval_evidence>\n','',p['raw']['suffix'],count=1,flags=re.S)==c['raw']['suffix'],'outside-source change')
    text,_=legacy.compose_input(p['order'],**p['raw']);require(oc._sha(text)==p['composed_sha256'],'base composition hash')
    vp=load(ROOT/'prepared/verifier_inputs'/f'{p["review_id"]}.json');regenerated=vs.build_verifier_input(p,mode)
    require(vp['raw']==regenerated['raw'] and vp['schema']==vs.response_schema(),'verifier prompt change')
    require(vp['schema']==load(ROOT/'prepared/verifier_schema.json'),'schema change')
    return account,vp

def freeze():
    require(not (ROOT/'freeze.json').exists(),'freeze exists')
    pre=load(ROOT/'verification/preflight.json')
    require(pre['passed'] and pre['prepared_inputs']==90 and pre['shared_source_pairs']==30,'preflight failed')
    for name in ('integration_tests.json','audit_aggregate_tests_v2.json'):
        v=load(ROOT/'verification'/name);require(v['passed'],f'{name} failed')
        for path,h in v.get('tested_source_sha256',{}).items():require(sha(ROOT/path)==h,f'tested source changed: {path}')
    policy=load(ROOT/'policy_freeze.json')
    for path,h in policy['files'].items():require(sha(ROOT/path)==h,f'policy source changed: {path}')
    from causal_adapter import validate_strict_schema
    validate_strict_schema(vs.response_schema());validate_strict_schema(load(ROOT/'prepared/response_schema.json'))
    expected=expected_jobs(load(ROOT/'prepared/jobs.json'))
    for rid,ident in expected.items():validate_prepared(load(ROOT/'prepared/inputs'/f'{rid}.json'),ident)
    refs=parent_refs();preservation=verify_parents(refs)
    files=set(ROOT.glob('*.py'))|set(ROOT.glob('*.md'))|{ROOT/'lineage.json',ROOT/'policy_freeze.json'}
    for folder in ('prepared','retrieval','verification','parent_grading'):
        files.update(p for p in (ROOT/folder).rglob('*') if p.is_file() and not any(x in ('__pycache__','.pytest_cache','.pycache') for x in p.parts) and p.suffix not in ('.pyc','.pyo'))
    write(ROOT/'freeze.json',dict(created_at=datetime.now(timezone.utc).isoformat(),files={str(p.relative_to(ROOT)):sha(p) for p in sorted(files)},
        parent_seals=refs,parent_preservation=preservation,policy_freeze_sha256=sha(ROOT/'policy_freeze.json'),
        model=MODEL,reasoning_effort='low',workers=WORKERS,min_start_interval=INTERVAL,new_calls=180,new_reviews=90,
        verification_stage='update_1',final_stage='update_2',feedback_cap=1800,arms=ARMS))
    return dict(frozen_files=len(files),sha256=sha(ROOT/'freeze.json'),parent_preservation=preservation)

def verify_freeze(full=True):
    frozen=load(ROOT/'freeze.json');require(frozen['new_calls']==180 and frozen['model']==MODEL,'frozen policy mismatch')
    for rel,h in frozen['files'].items():require(sha(ROOT/rel)==h,f'frozen file changed: {rel}')
    immutable={str(p.relative_to(ROOT)) for p in ROOT.glob('*.py')}
    for name in ('prepared','retrieval'):immutable|={str(p.relative_to(ROOT)) for p in (ROOT/name).rglob('*') if p.is_file()}
    previous={x for x in frozen['files'] if '/' not in x and x.endswith('.py') or x.startswith(('prepared/','retrieval/'))}
    require(immutable==previous,'immutable runtime tree changed')
    require(parent_refs()==frozen['parent_seals'],'lineage changed')
    return verify_parents(frozen['parent_seals'],full)

def complete_stage(folder_root,rid,stage,raw,schema,order,gate):
    folder=folder_root/rid;result=folder/f'{stage}.result.json'
    if not result.exists():
        if (folder/f'{stage}.claim.json').exists():raise transport.ClaimedIncomplete(f'{rid}/{stage}: claimed incomplete, never re-issued')
        client=transport.CliCompletionClient(out_dir=folder_root,review_id=rid,schema=schema,model=MODEL,order=order,gate=gate,max_calls=1,timeout=300)
        for s in legacy.STAGES:
            client.begin_stage(s)
            if s==stage:break
        try:client.complete(**raw)
        except transport.CallFailed:pass
    record=load(result);inp=load(folder/f'{stage}.input.json')
    require(record.get('review_id')==rid and record.get('stage')==stage and record.get('status') in ('ok','error'),'canonical stage identity/status mismatch')
    require(type(record.get('physical_attempt_count')) is int and 1<=record['physical_attempt_count']<=2 and record.get('retry_count')==record['physical_attempt_count']-1,'canonical stage attempt accounting')
    text,parts=legacy.compose_input(order,**raw)
    require(inp['raw']==raw and inp['composed_text']==text and inp['parts']==parts,'actual completion input changed')
    require(inp['composed_sha256']==oc._sha(text)==record['input_sha256'],'actual hash mismatch')
    return record,inp

def invalid_citations(findings,ids):
    out=[]
    for i,f in enumerate(findings):
        evidence=f.get('evidence',[]) if isinstance(f,dict) else None
        if not isinstance(evidence,list) or any(not isinstance(e,dict) or e.get('evidence_id') not in ids for e in evidence):out.append(i)
    return out

def execute_one(group,arm):
    rid=group+'_'+arm;folder=ROOT/'results/reviews'/rid;dest=folder/'review.result.json'
    if dest.exists():
        old=load(dest);require(old['freeze_sha256']==sha(ROOT/'freeze.json'),'preserved freeze mismatch')
        require(old['prepared_sha256']==sha(ROOT/'prepared/inputs'/f'{rid}.json'),'preserved prepared changed')
        for key,filename in [('final_result_sha256','update_2.result.json'),('final_input_sha256','update_2.input.json')]:require(old[key]==sha(folder/filename),'preserved final changed')
        for key in ('verifier_reference','verifier_input_reference','feedback_reference','final_prepared_reference','final_actual_delivery_audit'):reference(old[key])
        return dict(review_id=rid,preserved=True,status=old['status'],verifier_status=old['verifier_status'])
    gate=transport.AdmissionGate(ROOT/'results/gate',max_live_calls=4,min_interval=3)
    require(not gate.is_stopped(),'STOP active')
    prepared_path=ROOT/'prepared/inputs'/f'{rid}.json';p=load(prepared_path);account,vp=validate_prepared(p)
    same_or_new(folder/'prefix.reference.json',{k:v for k,v in p.items() if k!='raw'})
    vr,vi=complete_stage(ROOT/'results/verifiers',rid,'update_1',vp['raw'],vp['schema'],vp['order'],gate)
    # A configuration/resource error has already stopped the common gate. It
    # must not admit a final just to fill an accounting denominator.
    if gate.is_stopped():raise transport.GlobalStop('STOP after verification; preserved before final')
    feedback=vs.compose_feedback(vr.get('raw_response'),p,p['verification_mode'],cap=1800,transport_status=vr['status'])
    require(oc.count_reference_tokens(feedback['block'])<=1800,'feedback block cap')
    same_or_new(folder/'verification_feedback.json',feedback)
    raw=vs.append_feedback(p['raw'],feedback)
    require(all(raw[k]==p['raw'][k] for k in ('system','shared','prompt')) and raw['suffix']==p['raw']['suffix']+feedback['block'],'feedback prefix change')
    require(STATE.search(raw['suffix']).group(0)==STATE.search(p['raw']['suffix']).group(0),'causal state changed')
    final_prepared=copy.deepcopy(p);final_prepared['raw']=raw
    text,_=legacy.compose_input(p['order'],**raw);final_prepared['composed_sha256']=oc._sha(text)
    final_prepared['verification_feedback_reference']=ref(folder/'verification_feedback.json')
    same_or_new(folder/'final.prepared.json',final_prepared)
    fr,fi=complete_stage(ROOT/'results/reviews',rid,'update_2',raw,load(ROOT/'prepared/response_schema.json'),p['order'],gate)
    feedback_delivery=vs.verify_feedback_delivery(fi['raw'],p['raw'],feedback)
    same_or_new(folder/'verification_feedback_delivery.json',feedback_delivery)
    observed=account.verify_delivery(fi['raw'],p['provided_evidence_ids'])
    require(observed==p['composed_visibility'],'actual source delivery changed')
    same_or_new(folder/'final_actual_delivery_audit.json',dict(stage='explicitly_actual_model_input',review_id=rid,
        input_reference=ref(folder/'update_2.input.json'),observed=observed,source_union_tokens=account.tokens,
        protected_source_hash=oc._sha(p['protected_sources']),extra_source_hash=oc._sha(p['extra_sources']),
        feedback_is_source=False,feedback_delivery=feedback_delivery,prepared_base_sha256=sha(prepared_path),final_prepared_reference=ref(folder/'final.prepared.json')))
    try:payload=json.loads(fr.get('raw_response') or '')
    except ValueError:payload={}
    findings=payload.get('findings',[]) if isinstance(payload,dict) else []
    if not isinstance(findings,list):findings=[]
    result={k:p[k] for k in ('review_id','group_id','case','direction','repeat','dataset','arm','prefix_arm')}
    result.update(status='completed' if fr['status']=='ok' else 'output_failure',verifier_status='completed' if vr['status']=='ok' else 'output_failure',
        payload=payload,findings_model_raw=findings,freeze_sha256=sha(ROOT/'freeze.json'),prepared_sha256=sha(prepared_path),
        final_result_sha256=sha(folder/'update_2.result.json'),final_input_sha256=sha(folder/'update_2.input.json'),
        verifier_reference=ref(ROOT/'results/verifiers'/rid/'update_1.result.json'),verifier_input_reference=ref(ROOT/'results/verifiers'/rid/'update_1.input.json'),
        feedback_reference=ref(folder/'verification_feedback.json'),final_prepared_reference=ref(folder/'final.prepared.json'),
        final_actual_delivery_audit=ref(folder/'final_actual_delivery_audit.json'),
        physical_attempt_count=fr['physical_attempt_count']+vr['physical_attempt_count'],retry_count=fr['retry_count']+vr['retry_count'],
        final_physical_attempt_count=fr['physical_attempt_count'],verification_physical_attempt_count=vr['physical_attempt_count'],
        final_retry_count=fr['retry_count'],verification_retry_count=vr['retry_count'],new_logical_calls=2,allocated_logical_judgments=4,
        inherited_references=p['inherited_references'],provided_evidence_ids=p['provided_evidence_ids'],
        inherited_source_exposure=p['inherited_source_exposure'],inherited_queries=p['inherited_queries'],
        retrieval_reference=p['retrieval_reference'],retrieval_pool_id=p['retrieval_pool_id'],
        invalid_citation_indices=invalid_citations(findings,p['provided_evidence_ids']))
    write(dest,result)
    return dict(review_id=rid,status=result['status'],verifier_status=result['verifier_status'],attempts=result['physical_attempt_count'])

def execute_block(b):
    for arm in b['arms']:
        if (ROOT/'results/gate/STOP').exists():break
        print(json.dumps(execute_one(b['group_id'],arm)),flush=True)

def status():
    rows=[load(p) for p in (ROOT/'results/reviews').glob('*/review.result.json')]
    finals=list((ROOT/'results/reviews').glob('*/update_2.result.json'));verifiers=list((ROOT/'results/verifiers').glob('*/update_1.result.json'))
    attempts=[load(p) for p in (ROOT/'results').glob('**/attempts/*/attempt_*/result.json')]
    return dict(reviews=len(rows),expected=90,finals=len(finals),verifiers=len(verifiers),completed=sum(r['status']=='completed' for r in rows),
        failed=sum(r['status']!='completed' for r in rows),verifier_failed=sum(r['verifier_status']!='completed' for r in rows),
        physical_attempts_finished=len(attempts),retries=sum(r.get('attempt',1)>1 for r in attempts),
        STOP=load(ROOT/'results/gate/STOP') if (ROOT/'results/gate/STOP').exists() else None)

def run():
    with (ROOT/'scheduler.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);preservation=verify_freeze()
        jobs=load(ROOT/'prepared/jobs.json');expected_jobs(jobs)
        gate=transport.AdmissionGate(ROOT/'results/gate',max_live_calls=4,min_interval=3);require(not gate.is_stopped(),'STOP exists')
        print(json.dumps(dict(event='scheduler_start',parent_preservation=preservation,workers=4)),flush=True)
        first=execute_one(jobs['blocks'][0]['group_id'],jobs['blocks'][0]['arms'][0]);print(json.dumps(dict(first=first)),flush=True)
        if first['status']!='completed' or first['verifier_status']!='completed':
            gate.stop('fixed_first_branch_failed_before_fanout',first['review_id']);return status()
        with ProcessPoolExecutor(max_workers=4) as pool:
            futures={pool.submit(execute_block,b):b['group_id'] for b in jobs['blocks']}
            for future in as_completed(futures):
                try:future.result()
                except Exception as exc:
                    gate.stop(f'harness_error:{type(exc).__name__}:{exc}',futures[future]);raise
        return status()

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=['freeze','verify_freeze','run','status']);a=p.parse_args();print(json.dumps(globals()[a.command](),ensure_ascii=False))
