"""Seal completed evidence without rewriting any experimental inputs or responses."""
import hashlib,json,sys
from datetime import datetime,timezone
from pathlib import Path
R=Path(__file__).resolve().parent
read=lambda p:json.loads(p.read_text())
def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()
target=R/'result_seal.json'
if '--verify' in sys.argv:
    seal=read(target)
    errors=[name for name,d in seal['files'].items() if not (R/name).is_file() or sha(R/name)!=d['sha256']]
    print(json.dumps({'verified_files':len(seal['files']),'errors':errors}))
    raise SystemExit(bool(errors))
assert not target.exists(),'Existing result seal is immutable; verify it instead.'
audit=read(R/'audit_all.json');scores=read(R/'scores.json')
assert all(audit['checks'].values()),audit['checks']
assert len(scores['rows'])==288 and sum(x['physical_calls'] for x in scores['rows'])==576
assert len(read(R/'author_annotations.json'))==288
assert (R/'REPORT_KO.md').is_file()
excluded={'checkouts','states','workspaces','__pycache__'}
files={}
for p in sorted(R.rglob('*')):
    rel=p.relative_to(R)
    if p.is_file() and not any(part in excluded for part in rel.parts) and p!=target:
        files[str(rel)]={'sha256':sha(p),'bytes':p.stat().st_size}
seal={
    'created_utc':datetime.now(timezone.utc).isoformat(),
    'trajectories':288,'physical_model_calls':576,
    'scope':'All run files except derived checkouts/workspaces, large pickled states, caches, and this manifest itself. Includes raw responses, traces, prompts, annotations, analysis scripts, results, source archives and frozen manifests.',
    'transitive_coverage':{
        'states':'freeze.json records each pickle hash; audit_all.json verifies freeze hashes.',
        'workspaces':'workspace_manifests record production-source hashes and Pyright configuration; audit_all.json verifies actual files.',
        'checkouts':'Derived from the archived repository revisions; provenance.json records base commits and patch/archive hashes.'
    },
    'files':files
}
target.write_text(json.dumps(seal,ensure_ascii=False,indent=2))
print(json.dumps({'sealed_files':len(files),'path':str(target)}))
