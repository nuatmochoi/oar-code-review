"""Bind the predeclared corpus to immutable raw/recovered SCIP batches."""
from pathlib import Path
from common import ROOT,load,sha,digest,write_new
def reference(case):
    manifest=ROOT/'prepared/scip_selection.json'
    if manifest.exists():
        r=load(manifest)['cases'][case['id']]
        assert sha(r['path'])==r['sha256'];return r
    p=ROOT/'prepared/scip'/case['id']/'batch.json'
    return dict(path=str(p),sha256=sha(p))
def select():
    rows={}
    for c in load(ROOT/'prepared/cases.json'):
        raw=ROOT/'prepared/scip'/c['id']/'batch.json'
        recovered=ROOT/'prepared/scip_recovered'/c['id']/'batch.json'
        selected=recovered if recovered.exists() else raw
        b=load(selected)
        identity=digest(dict(language=c['language'],snapshots=c['snapshots'],environment_reference=c.get('environment_reference')))
        assert b['case_manifest_sha256']==identity,(c['id'],'source identity')
        rows[c['id']]=dict(path=str(selected),sha256=sha(selected),raw_path=str(raw),raw_sha256=sha(raw),
            selection='source-only parser normalization repair from identical original index/export; no additional indexing' if selected==recovered else 'original native index attempt including incompleteness/failure',
            snapshots={s:v['status'] for s,v in b['snapshots'].items()})
    out=dict(cases=rows,labels_or_model_outcomes_consumed=False,selection_scope='Offline path normalization bug repair only, original failures and native costs retained.')
    write_new(ROOT/'prepared/scip_selection.json',out);return out
if __name__=='__main__':print(select())
