"""Read-only delivery, dependency and runtime checks for the budget diagnosis."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from bootstrap import ROOT, PARENT, REPAIR, WORKSPACE
import budget_states as bs
import cli_transport as cli
import oar_core as oc

def load(p): return json.loads(p.read_text())
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def output(p,data):
    with p.open('x') as f:json.dump(data,f,ensure_ascii=False,indent=2);f.write('\n')
    print(json.dumps({k:v for k,v in data.items() if k not in ('rows','files')},ensure_ascii=False))

def preservation(dest):
    errors=[];counts={}
    for label,parent in [('historical',PARENT),('repair',REPAIR)]:
        seal=load(parent/'parent_grading/completed_result_seal.json')
        for relative,h in seal['files'].items():
            p=Path(seal['workspace'])/relative
            if not p.is_file() or sha(p)!=h:errors.append({'rule':label,'path':relative})
        counts[label]=len(seal['files'])
    deps=load(REPAIR/'verification/dependency_before.json')['files']
    for relative,h in deps.items():
        if sha(WORKSPACE/relative)!=h:errors.append({'rule':'dependency','path':relative})
    output(dest,dict(passed=not errors,checked_at=datetime.now(timezone.utc).isoformat(),sealed_files=counts,dependencies=len(deps),errors=errors))
    assert not errors

def delivery(dest):
    errors=[];rows=[];tokens=[]
    for block in load(ROOT/'prepared/jobs.json')['blocks']:
        group=block['group_id'];context=bs.context(group)
        for arm in bs.ARMS:
            p=ROOT/'prepared/inputs'/f'{group}_{arm}.json';d=load(p);raw=d['raw']
            text,parts=cli.compose_input(d['order'],**raw)
            state=json.loads(bs.STATE.search(raw['suffix']).group(1));st=bs.STATE.search(raw['suffix']).group(0)
            n=oc.count_reference_tokens(st);tokens.append(oc.count_reference_tokens(text))
            checks=dict(composed=oc._sha(text)==d['composed_sha256'],outside=bs.outside(raw)==bs.outside(context['low_legacy']['raw']),
                        role=state['role']==context['role'],instruction=state['instruction']==context['instruction'],cap=n<=d['state_cap'],
                        exact_count=n==d['state_tokens'],source_ids=d['provided_evidence_ids']==context['low_legacy']['provided_evidence_ids'])
            for name,ref in d['inherited_references'].items():checks['reference:'+name]=sha(Path(ref['path']))==ref['sha256']
            if arm.endswith('1600'):
                checks['low_exact']=raw==context['low_'+arm.split('_')[0]]['raw']
            elif arm=='legacy_full':
                sections=state['sections'];digest=json.loads(sections['previous_response_digest'])
                checks['no_budget_truncation']=not any(state['section_truncated'].values())
                checks['initial_raw_exact']=sections['initial_raw_response']==context['initial']['raw_response']
                checks['digest_unchanged_fields']=set(digest)=={'candidates','findings','context_requests_in_that_response','omitted_items'}
                checks['budget_complete']=d['handoff_manifest']['budget_complete']
            else:
                records=d['handoff_manifest']['records'];sent={r['id']:r['value'] for r in state['records']}
                checks['budget_complete']=d['handoff_manifest']['budget_complete']
                checks['allowed_exact']=all(r['complete'] or r['reason']=='redundant_initial_prose_policy' for r in records)
                checks['initial_findings_exact']=all(sent[f'initial:/findings/{i}']==f for i,f in enumerate(json.loads(context['initial']['raw_response'])['findings']))
                checks['claims_exact']=all(sent[f'claims:/{i}']==c for i,c in enumerate(context['review']['causal_audit']['verification']['claims']))
                checks['probes_exact']=all(sent[f'probes:/{i}']==v for i,v in enumerate(bs.original_adapter.probe_digest(context['review']['causal_audit']['probes'])))
                checks['critic_observations_exact']=all(sent[f'critic:/review/observations/{i}']==o for i,o in enumerate(json.loads(context['critic']['raw_response'])['review']['observations']))
            errors.extend({'id':d['review_id'],'rule':k} for k,v in checks.items() if not v)
            rows.append(dict(review_id=d['review_id'],state_tokens=n,checks=checks))
    assert len(rows)==120
    output(dest,dict(passed=not errors,inputs=120,actual_model_calls=0,max_full_input_reference_tokens=max(tokens),rows=rows,errors=errors))
    assert not errors

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('mode',choices=['preservation','delivery']);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();globals()[a.mode](a.output)
