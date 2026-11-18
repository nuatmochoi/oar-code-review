"""Freeze all prospective code, public contexts and evaluator-only references."""
from pathlib import Path
import argparse,json,shutil,subprocess,time
from common import ROOT,PROJECT,BUDGETS,ARMS,REPEATS,MODEL,load,sha,write_new

def freeze():
 assert not list((ROOT/'evaluation').glob('**/initial.result.json'))
 assert not list((ROOT/'results').glob('**/initial.result.json'))
 assert load(ROOT/'contexts/collection_summary.json')['completed_cases']==48
 assert load(ROOT/'private/source_packets_manifest.json')['complete']
 assert load(ROOT/'native_validation/completion_manifest.json')['source_integrity_passed']
 assert load(ROOT/'audit/preservation_before.json')['passed']
 dependencies=load(PROJECT/'runs/causal_route_separation_20261103/dependency_freeze.json')['files'].copy()
 for p,h in dependencies.items():assert sha(p)==h,('Read-only dependency changed',p)
 binary=shutil.which('codex');assert binary
 dependencies[str(Path(binary).resolve())]=sha(binary)
 launcher=Path(binary).resolve()
 # This host's observed npm entry point spawns an optional platform binary.
 # Resolve from that launcher, rather than assuming the symlink is executable
 # closure or accepting a different globally installed package.
 node=shutil.which('node');assert node
 dependencies[str(Path(node).resolve())]=sha(node)
 program="const{createRequire}=require('node:module');const r=createRequire(process.argv[1]);process.stdout.write(JSON.stringify({platform:process.platform,arch:process.arch,metadata:r.resolve('@openai/codex-darwin-arm64/package.json')}));"
 resolved=json.loads(subprocess.check_output([node,'-e',program,str(launcher)],text=True))
 assert resolved['platform']=='darwin' and resolved['arch']=='arm64'
 platform_metadata=Path(resolved['metadata']).resolve()
 native_binary=platform_metadata.parent/'vendor/aarch64-apple-darwin/bin/codex'
 for p in (launcher.parent.parent/'package.json',platform_metadata,native_binary):
  assert p.is_file();dependencies[str(p)]=sha(p)
 native=load(ROOT/'native_validation/completion_manifest.json')
 for key in ('authority_probe_references','artifact_references'):
  refs=native[key]
  assert isinstance(refs,dict)
  for p,h in refs.items():
   if isinstance(h,dict):h=h['sha256']
   assert sha(p)==h,('Native artifact changed',p)
   dependencies[p]=h
 for row in native['source_checks']:dependencies[row['manifest']]=row['manifest_sha256']
 write_new(ROOT/'dependency_freeze.json',{'files':dependencies,'scope':'Read-only imported tool/runtime closure from prior frozen run plus Codex executable and selected native authority/index/command receipts. Source content is hash-checked on read against snapshot manifests.','recorded_at':time.time()})
 files={}
 paths=list(ROOT.glob('*.py'))+list(ROOT.glob('*.md'))
 for folder in ('tests','scripts','post','prepared','private','contexts','audit'):
  paths.extend(p for p in (ROOT/folder).rglob('*') if p.is_file() and p.suffix in ('.py','.json','.md'))
 paths.extend([ROOT/'dependency_freeze.json',ROOT/'native_validation/completion_manifest.json',ROOT/'native_validation/accounting_summary.json',ROOT/'native_validation/cases_external.json'])
 for p in sorted(set(paths)):
  if p.name in ('RUN_STATE_KO.md','RESULTS_KO.md'):continue
  if 'preservation_before' in p.name and p.suffix!='.json':continue
  files[str(p.relative_to(ROOT))]=sha(p)
 write_new(ROOT/'freeze.json',{'created_at':time.time(),'files':files,'cases':48,'arms':list(ARMS),'repeats':REPEATS,'planned_reviews':576,
  'budgets':BUDGETS,'model':MODEL,'reviewer_model_calls_before_freeze':0,'judge_model_calls_before_freeze':0,
  'scope':'All implementations, prompts, schemas, public inputs, source contexts and evaluator criteria/targets/packets frozen before first model call.'})
 return verify_freeze()

def verify_freeze():
 f=load(ROOT/'freeze.json')
 for p,h in f['files'].items():assert sha(ROOT/p)==h,('Frozen file changed',p)
 deps=load(ROOT/'dependency_freeze.json')['files']
 for p,h in deps.items():assert sha(p)==h,('Frozen dependency changed',p)
 return {'frozen_files':len(f['files']),'external_references':len(deps),'freeze_sha256':sha(ROOT/'freeze.json')}

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('action',choices=('freeze','verify'));a=p.parse_args();print(freeze() if a.action=='freeze' else verify_freeze())
