"""Source-only preparation of paired indexes/AST deltas before model freeze."""
import argparse,time
from concurrent.futures import ProcessPoolExecutor,as_completed
from pathlib import Path
from common import ROOT,BUDGETS,load,sha,digest,write_new
from ast_delta import build_delta,summary

def prepare_one(case):
    from scip_tools import build_pair
    folder=ROOT/'prepared/scip'/case['id'];dest=folder/'batch.json'
    if dest.exists():batch=load(dest)
    else:
        batch=build_pair(case,folder,dict(BUDGETS))
        if not dest.exists():write_new(dest,batch)
        else:assert digest(load(dest))==digest(batch), 'persisted batch must have identical canonical JSON facts'
    astpath=ROOT/'prepared/ast_deltas'/f'{case["id"]}.json'
    if not astpath.exists():write_new(astpath,build_delta(case))
    return dict(case_id=case['id'],index_status={k:v.get('status') for k,v in batch.get('snapshots',{}).items()},ast_errors=load(astpath)['errors'])

def prepare(cases_path=None,receipt_name='index_preparation.json'):
    assert not (ROOT/'freeze.json').exists()
    cases=load(cases_path or ROOT/'prepared/cases.json');began=time.time();results=[]
    with ProcessPoolExecutor(max_workers=4) as pool:
        futures={pool.submit(prepare_one,c):c['id'] for c in cases}
        for f in as_completed(futures):
            try:r=f.result()
            except Exception as e:r=dict(case_id=futures[f],error=f'{type(e).__name__}: {e}')
            results.append(r);print(r,flush=True)
    write_new(ROOT/'prepared'/receipt_name,dict(started_at=began,duration_seconds=time.time()-began,results=results,model_calls=0))
    return dict(cases=len(cases),results=len(results),errors=[r for r in results if 'error' in r])

def mirror():
    from scip_tools import mirror_candidates
    cases=load(ROOT/'prepared/cases.json');selected=[]
    for language in ('Python','Go'):
        c=sorted((c for c in cases if c['language']==language),key=lambda c:c['id'])[0]
        if language=='Python':
            from python_tools import PythonTools as Backend
        else:
            from go_tools import GoTools as Backend
        out=ROOT/'verification/lsp_scip_mirror'/c['id']
        backend=Backend(c,out/'tools',dict(BUDGETS))
        try:r=mirror_candidates(c,load(ROOT/'prepared/scip'/c['id']/'batch.json'),backend,out,limit=3)
        finally:closed=backend.close()
        selected.append(dict(case_id=c['id'],language=language,result=r,backend_close=closed))
    write_new(ROOT/'verification/lsp_scip_mirror.json',dict(selection='First sorted public case in each language, first sorted eligible candidates; no gold or outcomes used.',results=selected,model_calls=0))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=('prepare','mirror'))
    p.add_argument('--cases');p.add_argument('--receipt',default='index_preparation.json');a=p.parse_args()
    print(prepare(Path(a.cases) if a.cases else None,a.receipt) if a.action=='prepare' else mirror(),flush=True)
