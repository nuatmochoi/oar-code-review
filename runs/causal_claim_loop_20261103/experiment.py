"""Prospective A/B/C/D static causal-claim experiment; isolated model calls.

A is the shared initial response. B receives the same source and two further
reviews. C/D receive at most two actual query rounds; D additionally receives
paired SCIP candidates inside the identical allowlisted source universe.
"""
import argparse,copy,json,os,random,time
from concurrent.futures import ProcessPoolExecutor,as_completed
from pathlib import Path
from common import ROOT,MODEL,REPEATS,ARMS,BUDGETS,load,sha,digest,write_new,source_tokens,normalize_block
from claim_controller import ClaimController,public_diff_evidence
from budget_transport import complete,accepted_payload,total_usage,raw_review_payload
from prompts import raw_prompt
from schemas import REVIEW
from index_selection import reference as index_reference

def initial_evidence(case):
    blocks=[];p=ROOT/'prepared/initial_body_annotations_final'/f'{case["id"]}.json'
    annotations=load(p) if p.exists() else {}
    if annotations:
        from prepare_initial import block_key
    for x in case['initial_blocks']:
        x=copy.deepcopy(x)
        if annotations:x['mechanical_checks']={**x.get('mechanical_checks',{}),**annotations[block_key(x)]}
        blocks.append(normalize_block(x))
    return blocks+public_diff_evidence(case['diff'])

def freeze():
    assert not (ROOT/'results').exists(), 'no model before freeze'
    assert load(ROOT/'verification/tests_final.json')['passed']
    assert load(ROOT/'audit/pre_model_audit.json')['passed']
    cases=load(ROOT/'prepared/cases.json')
    for c in cases:
        assert Path(index_reference(c)['path']).exists()
        assert (ROOT/'prepared/ast_deltas'/f'{c["id"]}.json').exists()
        assert (ROOT/'prepared/initial_body_annotations_final'/f'{c["id"]}.json').exists()
    files={}
    for p in ROOT.rglob('*'):
        rel=p.relative_to(ROOT)
        if not p.is_file() or any(x in ('__pycache__','.pytest_cache','.venv','node_modules','.git','git_cache','workspaces','source_pools','staged','results') for x in rel.parts):continue
        if p.name=='RUN_STATE_KO.md':continue
        if p.suffix in ('.pyc','.pyo','.log'):continue
        files[str(rel)]=sha(p)
    old=ROOT.parent/'location_binding_20261103/completed_result_seal.json'
    write_new(ROOT/'freeze.json',dict(created_at=time.time(),files=files,model=MODEL,reasoning_effort='low',budgets=BUDGETS,
        cases=len(cases),repeats=REPEATS,arms=ARMS,workers=4,start_interval=3,
        parent_seal=dict(path=str(old),sha256=sha(old)),
        model_upper_bound_semantics='Controller call/token admission, CLI rollout tracking; no verified server max_output_tokens. Unknown usage/overrun is a failure, never zero. All reported input+output, including cached/reasoning subsets only once.',
        initial_response='One fresh shared initial response per case/repeat, A result and identical initial state for B/C/D. Report both logical per-arm and deduplicated physical costs.',
        runtime_probes=False))
    return dict(frozen=len(files),cases=len(cases),repeats=REPEATS)

def verify_freeze():
    f=load(ROOT/'freeze.json')
    for rel,h in f['files'].items():assert sha(ROOT/rel)==h,('frozen changed',rel)
    if (ROOT/'dependency_freeze.json').exists():
        for p,h in load(ROOT/'dependency_freeze.json')['files'].items():assert sha(p)==h,('dependency changed',p)
    assert sha(f['parent_seal']['path'])==f['parent_seal']['sha256']
    return dict(passed=True,files=len(f['files']))

def stage_payload(case,evidence,label,folder,previous,controller,used,calls,tools=None,scip=None):
    feedback=dict(state=controller.ledger.export(),last_update=controller.updates[-1] if controller.updates else None,
        rejection_history=controller.ledger.rejections,mechanical_truth_only=True)
    raw=raw_prompt(case,evidence,label,previous,feedback,tools,scip)
    result=complete(folder,label,raw,used,calls)
    return result,accepted_payload(result)

def fallback_questions(case,payload,backend):
    if payload['questions']:return payload['questions'],False
    # An empty candidate set never disables exploration of changed operations.
    unresolved=any(p['status']=='unresolved' for c in payload['claims'] for p in c['conditions'])
    challenged=any(h['state']=='pending' for c in payload['claims'] for h in c['challenges'])
    if not payload['claims'] or unresolved or challenged:return backend.seed_questions(),True
    return [],False

def summarize_query(q,r):
    return dict(question=q,evidence=[dict(id=e['id'],kind=e.get('kind'),snapshot=e['snapshot'],path=e['path'],start_line=e['start_line'],end_line=e['end_line'],binding=e.get('binding',{})) for e in r.get('evidence',[])],
        unresolved=r.get('unresolved',[]),cost=r.get('cost',{}))

def perform_queries(case,backend,payload,controller,folder,round_index,index):
    questions,seeded=fallback_questions(case,payload,backend)
    original_order=[q['id'] for q in questions]
    if index is not None:
        # Rank the already requested questions. Never turn an arbitrary symbol
        # reference into a uniquely resolved callee or synthesize a true path.
        def priority(q):
            hits=[c for c in index.get('candidates',[]) if c.get('snapshot')==q['snapshot'] and
                (c.get('path')==q['file'] or (q['symbol'] and q['symbol'] in c.get('symbol','')))]
            return (-len(hits),original_order.index(q['id']))
        questions=sorted(questions,key=priority)
    # Do not silently query the same invalid target forever; preserve attempts.
    prior_ids=set(controller.evidence);out=[];budget_events=[]
    for i,q in enumerate(questions):
        evidence=list(controller.evidence.values())
        if source_tokens(evidence)>=BUDGETS['max_source_tokens']:
            out.append(dict(question=q,evidence=[],unresolved=['source_token_budget_exhausted'],cost={}))
            continue
        backend.budgets['source_tokens_used']=source_tokens(evidence)
        began=time.monotonic()
        try:r=backend.query(copy.deepcopy(q))
        except Exception as exc:
            r=dict(evidence=[],unresolved=[dict(kind='backend_exception',message=f'{type(exc).__name__}: {exc}')],cost={'measurement_status':'unknown_after_exception'})
        admitted=[];already_provided=[];fetched=[]
        for e in r.get('evidence',[]):
            b=normalize_block(e)
            fetched.append(b['id'])
            if b['id'] in controller.evidence:
                already_provided.append(controller.evidence[b['id']]);continue
            if source_tokens(list(controller.evidence.values())+admitted+[b])>BUDGETS['max_source_tokens']:
                event=dict(question=q['id'],evidence_id=b['id'],reason='full_body_exceeds_remaining_source_tokens',body_not_truncated=True)
                budget_events.append(event);r.setdefault('unresolved',[]).append(event)
                continue
            admitted.append(b)
        try:controller.add_evidence(admitted)
        except Exception as exc:
            # Source conflicts are operational failures, never merged away.
            r.setdefault('unresolved',[]).append(dict(kind='evidence_store_rejection',message=str(exc)))
            admitted=[]
        r['evidence']=already_provided+admitted
        r['fetched_evidence_ids']=fetched;r['newly_provided_evidence_ids']=[b['id'] for b in admitted]
        r['already_provided_evidence_ids']=[b['id'] for b in already_provided]
        r['controller_query_seconds']=time.monotonic()-began
        write_new(folder/f'round_{round_index}_query_{i+1:03}.json',dict(question=q,result=r))
        out.append(summarize_query(q,r))
    record=dict(round=round_index,queries=out,controller_seed_used=seeded,empty_candidate_seed_used=seeded and not payload['claims'],body_budget_events=budget_events,
        before_evidence_ids=sorted(prior_ids),after_evidence_ids=sorted(controller.evidence),source_tokens=source_tokens(list(controller.evidence.values())),
        scip_candidates_available=index is not None,original_question_order=original_order,
        actual_question_order=[q['id'] for q in questions],index_added_questions=0)
    astpath=ROOT/'prepared/ast_deltas'/f'{case["id"]}.json'
    if astpath.exists():
        from ast_delta import summary
        record['base_head_ast_changes']=summary(load(astpath))
        record['ast_change_reference']=dict(path=str(astpath),sha256=sha(astpath))
    write_new(folder/f'query_round_{round_index}.json',record)
    return record

def initial_one(case,repeat):
    key=f'{case["id"]}_r{repeat}'
    folder=ROOT/'results/initial'/key;dest=folder/'initial_summary.json'
    if dest.exists():return load(dest)
    began=time.time();ev=initial_evidence(case);controller=ClaimController(ev)
    r,p=stage_payload(case,ev,'initial',folder,None,controller,0,0)
    raw_payload=raw_review_payload(r)
    update=controller.update(p,0) if p else None
    value=dict(case_id=case['id'],repeat=repeat,cohort=case['cohort'],information_condition=case['information_condition'],
        status='ok' if p else 'error',payload=raw_payload,raw_initial_payload=raw_payload,accepted_initial_payload=p,
        actual_raw_initial_response_present=r.get('raw_response') is not None,raw_findings_count_measurable=raw_payload is not None,
        raw_findings_count=len(raw_payload['findings']) if raw_payload else 0,update=update,evidence=ev,
        model_result=str(folder/'initial/initial.result.json'),actual_model_tokens=total_usage(r),
        attempts=r.get('physical_attempt_count',0),usage_unknown=total_usage(r) is None,
        duration_seconds=time.time()-began,controller=controller.export(),binding=safe_binding(controller,raw_payload))
    write_new(dest,value);return value

def branch(case,repeat,arm):
    key=f'{case["id"]}_{arm}_r{repeat}';folder=ROOT/'results/reviews'/key;dest=folder/'review.result.json'
    if dest.exists():return dict(review_id=key,status=load(dest)['status'],preserved=True)
    first=load(ROOT/'results/initial'/f'{case["id"]}_r{repeat}'/'initial_summary.json')
    began=time.time();ev=copy.deepcopy(first['evidence']);controller=ClaimController(ev)
    previous=first.get('accepted_initial_payload',first['payload']) if first.get('status','ok')=='ok' else None
    raw_initial=first.get('raw_initial_payload',first['payload'])
    raw_final=raw_initial if arm=='A' else None
    accepted_final=previous if arm=='A' else None
    used=first['actual_model_tokens'];calls=1
    final_produced=raw_final is not None
    raw_final_response_present=first.get('actual_raw_initial_response_present',raw_initial is not None) if arm=='A' else False
    updates=[];stages=[dict(stage='initial',reference=first['model_result'],physically_shared=True,actual_model_tokens=used,status=first.get('status','ok'))]
    native=[];failures=[];backend=None;index=None;closed=None
    if previous is not None:updates.append(controller.update(previous,0))
    else:failures.append('initial_model_failure')
    if used is None:failures.append('unknown_initial_usage')
    if not failures and arm!='A':
        if arm in ('C','D'):
            if case['language']=='Python':
                from python_tools import PythonTools as Backend
            else:
                from go_tools import GoTools as Backend
            backend=Backend(case,folder/'tools',dict(BUDGETS,source_tokens_used=source_tokens(ev)))
        if arm=='D':index=load(index_reference(case)['path'])
        try:
            for rnd,label in ((1,'verify'),(2,'final')):
                if used>BUDGETS['max_model_tokens']:
                    failures.append('model_token_budget_overrun');break
                if time.time()-began+first['duration_seconds']>BUDGETS['max_wall_seconds']:
                    failures.append('wall_budget_exhausted');break
                tools=None
                if backend is not None:
                    tools=perform_queries(case,backend,previous,controller,folder,rnd,index)
                    native.append(tools)
                r,p=stage_payload(case,list(controller.evidence.values()),label,folder,previous,controller,used,calls,tools,index)
                if label=='final':
                    raw_final=raw_review_payload(r);accepted_final=p;final_produced=raw_final is not None
                    raw_final_response_present=r.get('raw_response') is not None
                calls+=int(r.get('physical_attempt_count',0)>0)
                u=total_usage(r)
                stages.append(dict(stage=label,reference=str(folder/label/'initial.result.json'),status=r['status'],actual_model_tokens=u))
                if u is None:
                    failures.append('unknown_usage' if r['status']!='unattempted' else 'model_budget_exhausted');break
                used+=u
                if p is None:failures.append('model_call_failure');break
                previous=p;updates.append(controller.update(p,rnd))
        except Exception as exc:failures.append(f'controller_exception:{type(exc).__name__}:{exc}')
        finally:
            if backend is not None:
                try:closed=backend.close()
                except Exception as exc:closed=dict(error=f'{type(exc).__name__}: {exc}');failures.append('backend_shutdown_failure')
    if used is not None and used>BUDGETS['max_model_tokens']:failures.append('model_token_budget_overrun')
    usage_unknown=used is None or 'unknown_usage' in failures or 'unknown_initial_usage' in failures
    if updates and not updates[-1]['accepted']:failures.append('invalid_final_claim_update')
    if not final_produced:failures.append('final_not_produced')
    final_payload=raw_final if final_produced else None
    binding=safe_binding(controller,final_payload)
    value=dict(review_id=key,case_id=case['id'],repeat=repeat,arm=arm,cohort=case['cohort'],language=case['language'],information_condition=case['information_condition'],
        status='error' if failures else 'ok',failures=failures,payload=final_payload,last_available_payload=previous,
        raw_final_payload=raw_final,accepted_final_payload=accepted_final,
        actual_raw_final_response_present=raw_final_response_present,raw_findings_count_measurable=raw_final is not None,
        final_produced=final_produced,raw_findings_count=len(final_payload['findings']) if final_payload else 0,
        initial_reference=str(ROOT/'results/initial'/f'{case["id"]}_r{repeat}'/'initial_summary.json'),
        stages=stages,updates=updates,controller=controller.export(),evidence=list(controller.evidence.values()),binding=binding,
        queries=native,backend_close=closed,actual_model_tokens=None if usage_unknown else used,
        known_model_tokens=used,model_usage_unknown=usage_unknown,logical_model_calls=calls,
        source_tokens=source_tokens(list(controller.evidence.values())),active_wall_seconds=time.time()-began+first['duration_seconds'],
        index_reference=index_reference(case) if arm=='D' else None,
        index_status={s:{k:v.get(k) for k in ('status','documents','source_file_count','indexed_source_file_count','scope_complete')} for s,v in index.get('snapshots',{}).items()} if index is not None else None,
        budgets=BUDGETS,freeze_sha256=sha(ROOT/'freeze.json'),runtime_probe_calls=0,
        semantic_guarantee=False,source_pool_hash=digest(case['snapshots']))
    write_new(dest,value)
    return dict(review_id=key,status=value['status'],findings=value['raw_findings_count'],calls=calls)

def safe_binding(controller,payload):
    if payload is None:return dict(errors=['no_final_payload'],bindings=[],unresolved=[],findings_filtered=0)
    try:return controller.bind_locations(payload)
    except (TypeError,KeyError,ValueError,AttributeError) as exc:
        return dict(errors=[dict(kind='raw_schema_invalid_for_binding',message=f'{type(exc).__name__}: {exc}')],bindings=[],unresolved=[],findings_filtered=0)

def run():
    verify_freeze();cases=load(ROOT/'prepared/cases.json')
    initial_jobs=[(c,r) for c in cases for r in range(1,REPEATS+1)]
    random.Random(2026110303).shuffle(initial_jobs)
    c,r=initial_jobs[0]
    first=initial_one(c,r);print(json.dumps(dict(sentinel=(c['id'],r),status=first['status'])),flush=True)
    if first['status']!='ok':return dict(stopped='initial_transport_or_output_sentinel_failure',case_id=c['id'],repeat=r)
    with ProcessPoolExecutor(max_workers=4) as pool:
        futures={pool.submit(initial_one,c,r):(c['id'],r) for c,r in initial_jobs[1:]}
        for f in as_completed(futures):
            try:v=f.result();print(json.dumps(dict(initial=futures[f],status=v['status'])),flush=True)
            except Exception as e:print(json.dumps(dict(initial=futures[f],error=str(e))),flush=True)
    jobs=[(c,r,a) for c in cases for r in range(1,REPEATS+1) for a in ARMS]
    random.Random(2026110304).shuffle(jobs)
    with ProcessPoolExecutor(max_workers=4) as pool:
        futures={pool.submit(branch,c,r,a):(c['id'],r,a) for c,r,a in jobs}
        for f in as_completed(futures):
            try:print(json.dumps(f.result()),flush=True)
            except Exception as e:print(json.dumps(dict(job=futures[f],error=str(e))),flush=True)
    return dict(initial_jobs=len(initial_jobs),review_jobs=len(jobs),results=len(list((ROOT/'results/reviews').glob('*/review.result.json'))))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=('freeze','verify','run'));a=parser.parse_args()
    print(json.dumps({'freeze':freeze,'verify':verify_freeze,'run':run}[a.action]()),flush=True)
