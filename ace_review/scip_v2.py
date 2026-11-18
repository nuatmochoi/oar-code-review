"""Module-aware indexing, revision collapse, and atomic witness ablations."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .conditions import make_request
from .delta_scip import SemanticIndex, build_candidates, changed_ranges, select_evidence
from .io import load_tasks, read_jsonl, write_json
from .repository import RepositoryCache
from .scip_modules import index_modules
from .scip_bundles import collapse_revisions, build_bundles, bundle_sources, select_bundles


def prepare_task(task,args):
    started=time.monotonic()
    repo,status=RepositoryCache(args.repo_cache).ensure(task)
    if repo is None:return {'task_id':task.id,'available':False,'repository_status':status,'requests':[]}
    changes=changed_ranges(task.diff)
    base_commit=task.metadata.get('merge_base_commit') or task.base_commit
    def indexed(version,commit):
        files=[f for f in changes[version] if f.endswith('.go')]
        return index_modules(repo,commit,files,Path(args.index_cache),args.indexer,args.exporter,args.timeout)
    b,bs=indexed('base',base_commit);h,hs=indexed('head',task.head_commit)
    record={'task_id':task.id,'repository':task.repo,'base':bs,'head':hs,'available':b is not None and h is not None,
            'scope_complete':bs['scope_complete'] and hs['scope_complete'],'requests':[],'audits':{}}
    if not record['available']:return record
    base=SemanticIndex(b,bs['module'],'base');head=SemanticIndex(h,hs['module'],'head')
    candidates,audit=build_candidates(base,head,task.diff,max_hops=args.max_hops)
    dedup=collapse_revisions(candidates)
    bundles=build_bundles(base,head,task.diff,candidates)
    flat=bundle_sources(bundles)
    record['audits']['delta']=audit
    record['audits']['revision_collapse']={'before':len(candidates),'after':len(dedup),
        'merged_pairs':sum(b.metadata.get('identical_revisions_collapsed',False) for b in dedup)}
    record['audits']['bundles']={'candidate_bundles':len(bundles),'candidate_sources':len(flat)}
    arms=[('scip_modules',select_evidence(candidates,args.token_budget),'v1_density'),
          ('scip_modules_dedup',select_evidence(dedup,args.token_budget),'v1_density'),
          ('scip_bundle_flat',select_evidence(flat,args.token_budget),'v1_density')]
    for policy in ('density','diversity'):
        selected,selection_audit=select_bundles(bundles,args.token_budget,policy=policy)
        condition='scip_bundles_density' if policy=='density' else 'scip_bundles'
        record['audits'][condition]=selection_audit
        arms.append((condition,selected,'atomic_bundle_'+policy))
    for condition,selected,selector in arms:
        request=make_request(task,condition,selected)
        request['context_status']={'requested':[condition],'present':[condition] if selected else [],
            'unavailable':[] if selected else [condition],'token_budget':args.token_budget,
            'context_tokens':sum(b.token_cost for b in selected),'selector':selector,
            'scope_complete':record['scope_complete'],'base_commit':base_commit,'head_commit':task.head_commit,
            'token_accounting':'UTF8 bytes/4 estimate; not tokenizer-exact'}
        record['requests'].append(request)
    output=Path(args.output_dir)
    for folder in ('candidates','bundles'):(output/folder).mkdir(parents=True,exist_ok=True)
    pools={'delta':candidates,'dedup':dedup,'bundle_sources':flat}
    with gzip.open(output/'candidates'/f'{task.id}.json.gz','wt') as f:
        json.dump({k:[b.to_dict() for b in pool] for k,pool in pools.items()},f)
    with gzip.open(output/'bundles'/f'{task.id}.json.gz','wt') as f:
        json.dump([{k:v for k,v in b.items() if k!='sources'}|{'source_ids':[s.id for s in b['sources']]} for b in bundles],f)
    record['wall_seconds']=time.monotonic()-started
    return record


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',default='data/manifests/aacr_go_pure_17_hydrated.jsonl')
    parser.add_argument('--output-dir',default='runs/delta_scip_v2_pilot17')
    parser.add_argument('--previous-run',default='runs/delta_scip_pilot17_final')
    parser.add_argument('--repo-cache',default='data/repos')
    parser.add_argument('--index-cache',default='runs/scip-index-cache')
    parser.add_argument('--indexer',default='tools/bin/scip-go')
    parser.add_argument('--exporter',default='tools/bin/scip-export')
    parser.add_argument('--timeout',type=int,default=300)
    parser.add_argument('--jobs',type=int,default=2)
    parser.add_argument('--max-hops',type=int,default=2)
    parser.add_argument('--token-budget',type=int,default=4096)
    parser.add_argument('--task-id',action='append')
    args=parser.parse_args();output=Path(args.output_dir)
    output.mkdir(parents=True,exist_ok=True)
    tasks=[t for t in load_tasks(args.manifest) if not args.task_id or t.id in args.task_id]
    if not tasks:raise ValueError('No selected tasks')
    hashes={}
    for name in ('scip_v2.py','scip_modules.py','scip_bundles.py','delta_scip.py','scip_batch.py',
                 'repository.py','conditions.py','schema.py','io.py'):
        payload=Path(__file__).with_name(name).read_bytes()
        target=output/'source'/'ace_review'/name;target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(payload);hashes[name]=hashlib.sha256(payload).hexdigest()
    write_json(output/'config.json',vars(args)|{'method':'DeltaSCIP v2 exploratory','source_sha256':hashes,
        'gold_access_in_retrieval':False,'dependency_repair':False,'minimum_graph_optimality_claim':False,
        'candidate_cap':4000,'endpoint_attempt_cap':200000,'source_window_limits':[60,32,24],
        'evaluation_status':'development_cohort; prior provisional footprints already observed'})
    records=[]
    with ThreadPoolExecutor(max_workers=args.jobs) as executor:
        futures={executor.submit(prepare_task,t,args):t for t in tasks}
        for future in as_completed(futures):
            task=futures[future]
            try:record=future.result()
            except Exception as error:record={'task_id':task.id,'available':False,'scope_complete':False,'error':str(error),'requests':[]}
            records.append(record);write_json(output/'tasks'/f'{task.id}.json',record)
            print(json.dumps({'task_id':task.id,'available':record['available'],'scope_complete':record.get('scope_complete'),
                'anchor_coverage':record.get('audits',{}).get('delta',{}).get('anchor_line_coverage'),
                'bundles':record.get('audits',{}).get('bundles')}),flush=True)
    records.sort(key=lambda r:r['task_id'])
    by_task={t.id:t for t in tasks}
    previous=[]
    for r in read_jsonl(Path(args.previous_run)/'requests.jsonl'):
        if r['task_id'] in by_task:
            if r['diff']!=by_task[r['task_id']].diff:raise ValueError('Baseline diff mismatch')
            previous.append(r)
    requests=previous+[r for rec in records for r in rec['requests']]
    with (output/'requests.jsonl').open('w') as f:
        for r in requests:f.write(json.dumps(r,ensure_ascii=False)+'\n')
    audits=[r['audits']['delta'] for r in records if r['available']]
    changed=sum(a['changed_go_lines'] for a in audits);mapped=sum(a['mapped_changed_go_lines'] for a in audits)
    all_lines=sum(len(lines) for t in tasks for paths in changed_ranges(t.diff).values() for path,lines in paths.items() if path.endswith('.go'))
    summary={'tasks':len(tasks),'paired_index_success':sum(r['available'] for r in records),
        'paired_scope_complete':sum(r.get('scope_complete',False) for r in records),'requests':len(requests),
        'changed_go_lines_successful_pairs':changed,'mapped_changed_go_lines':mapped,
        'anchor_coverage_successful_pairs':mapped/changed if changed else None,
        'all_task_changed_go_lines':all_lines,'anchor_coverage_all_tasks':mapped/all_lines if all_lines else None,
        'candidate_cap_tasks':[r['task_id'] for r in records if r.get('audits',{}).get('delta',{}).get('candidate_cap_reached')],
        'failed_tasks':[r['task_id'] for r in records if not r['available']],
        'oracle_evidence_recall':None,'reviewer_calls':0,'judge_calls':0,'status':'retrieval_diagnostics_only'}
    write_json(output/'summary.json',summary);print(json.dumps(summary),flush=True)


if __name__=='__main__':main()
