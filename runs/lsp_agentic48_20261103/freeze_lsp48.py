"""Prospective freeze; result and writable-cache paths are never input members."""
from pathlib import Path
import argparse,json,shutil,subprocess,time
from common import ROOT,PROJECT,PREVIOUS,ARMS,REPEATS,MODEL,BUDGETS,load,sha,write_new

def freeze():
 if(ROOT/'freeze.json').exists():return verify_freeze()
 assert not list((ROOT/'results').glob('**/initial.result.json'))
 assert not list((ROOT/'evaluation').glob('**/initial.result.json'))
 for name in('pipeline_lsp48.py','post/usage_lsp.py','post/mechanism_lsp.py','post/complete_lsp.py','PROTOCOL_KO.md'):
  assert(ROOT/name).is_file(),('Implementation incomplete',name)
 assert len(load(ROOT/'prepared/cases.json'))==48
 native=load(ROOT/'audit/native_backend_ready.json');assert native['ready']and native['model_calls']==0
 assert load(ROOT/'audit/preservation_before.json')['passed']
 resume=load(ROOT/'audit/preservation_resume_20261104.json');assert resume['passed']
 from evaluate_lsp48 import verify_reused
 verify_reused()
 deps=load(PREVIOUS/'dependency_freeze.json')['files'].copy()
 deps.update(native['owner_artifact_references']);deps.update(native['readonly_pinned_Go_binary_references'])
 sandbox=native['native_write_sandbox_reference'];deps[sandbox['path']]=sandbox['sha256']
 # Reuse the observed launcher/platform closure, and assert the current CLI and
 # Node resolve to it rather than freezing only a symlink or JS wrapper.
 cli=Path(shutil.which('codex')).resolve();node=Path(shutil.which('node')).resolve()
 for p in (cli,node):deps[str(p)]=sha(p)
 program="const{createRequire}=require('node:module');const r=createRequire(process.argv[1]);process.stdout.write(r.resolve('@openai/codex-darwin-arm64/package.json'));"
 meta=Path(subprocess.check_output([str(node),'-e',program,str(cli)],text=True)).resolve()
 for p in (cli.parent.parent/'package.json',meta,meta.parent/'vendor/aarch64-apple-darwin/bin/codex'):
  assert p.is_file();deps[str(p)]=sha(p)
 for c in load(ROOT/'prepared/cases.json'):
  for n in (1,2):
   p=PREVIOUS/'results/reviews'/f'{c["id"]}_B2_r{n}'/'review.result.json';deps[str(p)]=sha(p)
   for s in load(p)['stages']:
    p2=s.get('reference')or s.get('path')
    if p2:assert sha(p2)==s['sha256'];deps[p2]=s['sha256']
  for p in(PREVIOUS/'contexts'/c['id']/'raw').glob('query-*.json'):deps[str(p)]=sha(p)
 for p,h in deps.items():assert sha(p)==h,('Read-only dependency changed',p)
 if(ROOT/'dependency_freeze.json').exists():assert load(ROOT/'dependency_freeze.json')['files']==deps
 else:
  write_new(ROOT/'dependency_freeze.json',{'files':deps,'recorded_at':time.time(),
   'scope':'Prior read-only runtime/tool closure (including unused historical modules), selected native owner receipts and sandbox, pinned Go and CLI platform binaries, actual initial finals/provider receipts, fixed lookup receipts. Presence in closure is not tool use. Runtime source bytes are checked against declared manifests on read; new cache is mutable and excluded.'})
 paths=list(ROOT.glob('*.py'))+list(ROOT.glob('*.md'))
 for folder in ('tests','post','prepared','private','contexts','audit'):
  paths.extend(p for p in(ROOT/folder).rglob('*')if p.is_file()and'__pycache__'not in p.parts and p.suffix in('.py','.json','.md'))
 paths.append(ROOT/'dependency_freeze.json')
 files={str(p.relative_to(ROOT)):sha(p)for p in sorted(set(paths))if p.name not in('RUN_STATE_KO.md','RESULTS_KO.md')}
 write_new(ROOT/'freeze.json',{'recorded_at':time.time(),'files':files,'cases':48,'arms':ARMS,'repeats':REPEATS,'planned_reviews':288,
  'budgets':BUDGETS,'model':MODEL,'reviewer_model_calls_before_freeze':0,'judge_model_calls_before_freeze':0,
  'scope':'Prospective source/prompts/schemas/budgets/evaluation frozen before all new model calls. Writable cache/results/evaluation outputs are excluded.'})
 return verify_freeze()

def verify_freeze():
 f=load(ROOT/'freeze.json');assert f['planned_reviews']==288
 for p,h in f['files'].items():assert sha(ROOT/p)==h,('Frozen input changed',p)
 deps=load(ROOT/'dependency_freeze.json')['files']
 for p,h in deps.items():assert sha(p)==h,('Frozen dependency changed',p)
 return {'frozen_files':len(f['files']),'external_references':len(deps),'freeze_sha256':sha(ROOT/'freeze.json')}

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('action',choices=('freeze','verify'));a=p.parse_args();print(json.dumps(freeze()if a.action=='freeze'else verify_freeze()))
