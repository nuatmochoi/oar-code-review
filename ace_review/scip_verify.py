"""Read-only verification of emitted source provenance and bundle closure."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import subprocess
from functools import lru_cache
from pathlib import Path

from .delta_scip import _diff_maps
from .io import load_tasks, read_jsonl, write_json
from .schema import estimate_tokens

V2_CONDITIONS={'scip_modules','scip_modules_dedup','scip_bundle_flat','scip_bundles_density','scip_bundles'}


def _spans(block):
    return block.get('metadata',{}).get('source_spans',[{
        'version':block.get('metadata',{}).get('version','head'),'file':block.get('file'),
        'start_line':block.get('start_line'),'end_line':block.get('end_line')}])


def verify_source_block(block,read_source):
    errors=[]
    if block['token_cost']!=estimate_tokens(block['content']):errors.append('token_cost_mismatch')
    if block['kind']=='scip_bundle_manifest':return errors
    version=block['metadata']['version'];marker=f'Source ({version}):\n'
    if marker not in block['content']:return errors+['missing_source_marker']
    raw=re.sub(r'(?m)^\d+: ','',block['content'].split(marker,1)[1])
    for span in _spans(block):
        actual='\n'.join(read_source(span['version'],span['file']).splitlines()[span['start_line']-1:span['end_line']])
        if actual!=raw:errors.append('source_mismatch:'+json.dumps(span,sort_keys=True))
    return errors


def verify_bundle_closure(blocks,bundles,changes,visible):
    errors=[];ids={b['id'] for b in blocks}
    spans=[s for b in blocks for s in _spans(b) if s.get('start_line') is not None]
    for bundle in bundles:
        if not set(bundle['source_ids'])<=ids:errors.append(bundle['id']+':missing_source')
        for entry in bundle['entry_sites']:
            if entry['line'] not in changes.get(entry['version'],{}).get(entry['file'],set()):
                errors.append(bundle['id']+':entry_not_changed')
        for edge in bundle['edges']:
            if 'file' not in edge:continue
            in_diff=edge['line'] in visible.get(edge['version'],{}).get(edge['file'],set())
            covered=any(s['version']==edge['version'] and s['file']==edge['file'] and
                        s['start_line']<=edge['line']<=s['end_line'] for s in spans)
            if not in_diff and not covered:errors.append(bundle['id']+':missing_use_site')
    return errors


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir',required=True,type=Path)
    parser.add_argument('--manifest',default='data/manifests/aacr_go_pure_17_hydrated.jsonl')
    parser.add_argument('--repo-cache',default='data/repos',type=Path)
    args=parser.parse_args();root=args.run_dir
    tasks={t.id:t for t in load_tasks(args.manifest)}
    requests=[r for r in read_jsonl(root/'requests.jsonl') if r['condition'] in V2_CONDITIONS]
    errors=[];count=0;span_count=0;bundle_count=0;keys=set()
    @lru_cache(maxsize=2048)
    def read(task_id,version,file):
        t=tasks[task_id];commit=t.head_commit if version=='head' else (t.metadata.get('merge_base_commit') or t.base_commit)
        return subprocess.check_output(['git','show',f'{commit}:{file}'],cwd=args.repo_cache/t.repo.replace('/','__'),text=True)
    @lru_cache(maxsize=32)
    def records(task_id):
        with gzip.open(root/'bundles'/f'{task_id}.json.gz','rt') as f: bundles=json.load(f)
        return json.loads((root/'tasks'/f'{task_id}.json').read_text()),{b['id']:b for b in bundles}
    for request in requests:
        task_id=request['task_id'];condition=request['condition'];key=(task_id,condition)
        if key in keys:errors.append({'key':key,'error':'duplicate_request'})
        keys.add(key)
        blocks=request['contexts'];ids=[b['id'] for b in blocks]
        if len(set(ids))!=len(ids):errors.append({'key':key,'error':'duplicate_source_ids'})
        if sum(b['token_cost'] for b in blocks)>request['context_status']['token_budget']:
            errors.append({'key':key,'error':'budget_overflow'})
        for b in blocks:
            count+=1;span_count+=len([s for s in _spans(b) if s.get('start_line') is not None])
            for error in verify_source_block(b,lambda v,f:read(task_id,v,f)):
                errors.append({'key':key,'block':b['id'],'error':error})
        if condition in {'scip_bundles','scip_bundles_density'}:
            record,available=records(task_id)
            chosen=[available[i] for i in record['audits'][condition]['selected_bundle_ids']]
            bundle_count+=len(chosen)
            changes,visible=_diff_maps(tasks[task_id].diff)
            for error in verify_bundle_closure(blocks,chosen,changes,visible):errors.append({'key':key,'error':error})
    config=json.loads((root/'config.json').read_text())
    hashes_ok=all(hashlib.sha256((root/'source'/'ace_review'/name).read_bytes()).hexdigest()==digest
                  for name,digest in config['source_sha256'].items())
    result={'requests_verified':len(requests),'blocks_verified':count,'source_spans_verified':span_count,
        'selected_bundles_verified':bundle_count,'source_snapshot_hashes_valid':hashes_ok,
        'errors':errors,'passed':not errors and hashes_ok,
        'note':'Verifies source coordinates and static use-site inclusion, not defect validity or dynamic path feasibility.'}
    write_json(root/'verification.json',result)
    print(json.dumps({k:v for k,v in result.items() if k!='errors'}))
    if errors:raise SystemExit(1)


if __name__=='__main__':main()
