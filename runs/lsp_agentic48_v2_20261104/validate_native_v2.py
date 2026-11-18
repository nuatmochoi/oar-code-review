"""Preparation and two real native smoke queries, without model/gold access."""
from pathlib import Path
from unittest.mock import patch
import copy,json,os,subprocess,sys,time
from cache_isolation import ROOT,PREVIOUS,CACHE,sha,put,isolation_environment,sandbox_command
from context48 import ContextCollector,SCIPDisabled
INPUT_PREVIOUS=ROOT.parent/'lsp_agentic48_20261103'
def load(p):return json.loads(Path(p).read_text())

def boundary():
 output=ROOT/'audit/cache_write_boundary_v2.json'
 if output.exists():r=load(output);assert r['passed'];return r
 targets=[PREVIOUS/'freeze.json',INPUT_PREVIOUS/'freeze.json',next((INPUT_PREVIOUS/'cache/go-build').rglob('*-d'))]
 code="import os,json,sys;rows=[]\nfor p in sys.argv[1:]:\n try:\n  f=os.open(p,os.O_WRONLY);os.close(f);denied=False\n except PermissionError:denied=True\n rows.append({'path':p,'write_open_denied':denied})\nprint(json.dumps(rows));sys.exit(0 if all(r['write_open_denied'] for r in rows) else 9)"
 before={str(p):sha(p)for p in targets};command=sandbox_command([sys.executable,'-B','-c',code,*map(str,targets)])
 proc=subprocess.run(command,text=True,capture_output=True,timeout=20,env={**os.environ,**isolation_environment()})
 after={str(p):sha(p)for p in targets}
 r={'passed':proc.returncode==0 and before==after,'command':command,'returncode':proc.returncode,'stdout':proc.stdout,'stderr':proc.stderr,
  'target_hashes_before':before,'target_hashes_after':after,'scope':'Read-only open-for-write denial; no bytes truncated or written even if a denial test fails.','model_calls':0}
 put(output,r);assert r['passed'];return r

def guard_proofs():
 obj=object.__new__(ContextCollector);obj.events=[];rows=[]
 for label,call in[('collect_SCIP',lambda:obj.collect('SCIP')),('_scip_paths',lambda:obj._scip_paths('HEAD')),('_scip_stage',lambda:obj._scip_stage())]:
  with patch.object(Path,'open',side_effect=AssertionError('SCIP guard performed filesystem IO')),patch.object(Path,'read_bytes',side_effect=AssertionError('SCIP guard performed filesystem IO')),patch.object(Path,'is_file',side_effect=AssertionError('SCIP guard performed filesystem IO')):
   try:call()
   except SCIPDisabled:rows.append({'entrypoint':label,'SCIP_disabled_before_filesystem_IO':True})
   else:raise AssertionError('SCIP guard absent')
 return rows

def checks(cases):
 rows=[];pins={};native_env_paths=('GOCACHE','GOMODCACHE','GOPATH','GOPLSCACHE','XDG_CACHE_HOME','XDG_CONFIG_HOME','GOTMPDIR','TMPDIR','PYTHONPYCACHEPREFIX')
 old={c['id']:c for c in load(INPUT_PREVIOUS/'prepared/cases.json')}
 for c in cases:
  before=old[c['id']]
  assert (ROOT/'contexts'/c['id']/'context_LSP.json').read_bytes()==(INPUT_PREVIOUS/'contexts'/c['id']/'context_LSP.json').read_bytes()
  for k in('diff','diff_reference','public_contract','title','body','base','head','changed_files'):assert c[k]==before[k]
  for sn,s in c['snapshots'].items():
   prior=before['snapshots'][sn]
   for k in('root','commit','files','allowed_external_roots','external_files'):
    if k in prior:assert s[k]==prior[k]
   assert s['scip_usage_allowed']is False and not s['scip_exports']
   for lang,p in s['native_profiles'].items():
    oldp=prior['native_profiles'][lang]
    assert p['scip_usage_allowed']is False and not p['scip_exports']
    assert p.get('python_executable')==oldp.get('python_executable')and p['allowed_external_roots']==oldp['allowed_external_roots']
    assert all(Path(p['native_env'][k]).is_relative_to(ROOT)for k in native_env_paths)
    if lang=='Go':
     assert p['pinned_toolchains']==oldp['pinned_toolchains']
     for pin in p['pinned_toolchains'].values():pins[pin['go_binary']]=pin['go_binary_sha256']
  rows.append({'case_id':c['id'],'public_input_source_authority_initial_context_exact':True,'native_writable_paths_new_run_only':True,'SCIP_disabled':True})
 for p,h in pins.items():assert sha(p)==h
 return rows,pins

def smoke(cases):
 out=ROOT/'audit/backend_smoke_v2.json'
 if out.exists():r=load(out);assert r['passed'];return r
 specs=load(INPUT_PREVIOUS/'audit/backend_smoke_v2.json')['cases'];byid={c['id']:c for c in cases};rows=[]
 for spec in specs:
  c=byid[spec['case_id']];ctx=load(ROOT/'contexts'/c['id']/'context_LSP.json');folder=ROOT/'audit/backend_smoke'/c['id']
  backend=ContextCollector(c,folder,dict(max_source_tokens=12000,max_native_calls=48,max_native_seconds=180,max_file_reads=256,max_query_seconds=20,max_wall_seconds=900,scip_source_tokens=0,max_scip_candidates=0),initial_state=ctx)
  q=copy.deepcopy(spec['native_body_query']['question']);q.update(id='native_v2_smoke',reason='Technical source/environment projection smoke; no defect premise or private labels.')
  try:
   answer=backend.query(q)
   bodies=[e for e in answer['evidence']if e.get('mechanical_checks',{}).get('complete_unit')and e.get('kind')=='implementation_body']
   assert bodies,('Native query did not obtain a complete body',c['id'],answer['unresolved'])
   assert set(spec['new_complete_body_IDs'])<={e['id']for e in bodies},('Canonical source identities changed',c['id'])
   module=backend._module_root('HEAD',q['file']);env=backend._environment('HEAD',c['language'],module)
   if c['language']=='Go':
    pin=backend._profile('HEAD','Go')['pinned_toolchains'][str(module.relative_to(Path(c['snapshots']['HEAD']['root'])))];assert env['GOROOT']==pin['GOROOT']
   else:pin=None
   rows.append({'case_id':c['id'],'language':c['language'],'passed':True,'query':answer,'new_complete_body_IDs':[e['id']for e in bodies],
    'old_technical_smoke_body_IDs_retained':True,'effective_environment':{k:env.get(k)for k in('GOCACHE','GOMODCACHE','GOPATH','GOPLSCACHE','GOENV','GOFLAGS','GOTOOLCHAIN','GOPROXY','GOSUMDB','GOROOT','PYTHONPYCACHEPREFIX')},'pinned_Go':pin})
  finally:
   close=backend.close();put(folder/'state.json',backend.export())
   if rows and rows[-1]['case_id']==c['id']:rows[-1]['close']=close
 result={'complete':True,'passed':True,'cases':rows,'queries':2,'model_calls':0,'private_gold_semantic_reads':0,'target_runtime_probe_calls':0,'scope':'Technical native/environment checks only; no fresh initial context collection or effectiveness evidence.'}
 put(out,result);return result

def main():
 assert load(ROOT/'audit/preservation_before.json')['passed']
 assert not(ROOT/'audit/native_backend_ready.json').exists(),'Native readiness is immutable; do not re-run smoke.'
 cases=load(ROOT/'prepared/cases.json');assert len(cases)==48
 rows,pins=checks(cases);b=boundary();guards=guard_proofs();s=smoke(cases)
 paths=[ROOT/n for n in('context48.py','cache_isolation.py','prepare_v2.py','native_preservation_v2.py','validate_native_v2.py','prepared/cases.json','prepared/manifest.json','contexts/collection_summary.json','audit/cache_clone.json','audit/cache_source_before.json','audit/cache_clone_go-build.json','audit/cache_clone_go-mod.json','audit/cache_write_boundary_v2.json','audit/backend_smoke_v2.json','audit/selected_source_permissions.json')]
 paths.extend(ROOT/'contexts'/c['id']/'context_LSP.json'for c in cases)
 refs={str(p):sha(p)for p in paths}
 sandbox=ROOT/'cache/native-no-old-writes.sb';refs[str(sandbox)]=sha(sandbox)
 ready={'complete':True,'ready':True,'cases':48,'source_snapshots':96,'byte_identical_frozen_LSP_contexts':48,'checks':rows,
  'owner_artifact_references':refs,'readonly_pinned_Go_binary_references':pins,'native_write_sandbox_reference':{'path':str(sandbox),'sha256':sha(sandbox)},
  'final_smoke_reference':{'path':str(ROOT/'audit/backend_smoke_v2.json'),'sha256':sha(ROOT/'audit/backend_smoke_v2.json')},
  'SCIP_guard_proofs':guards,'cache_clone_reference':{'path':str(ROOT/'audit/cache_clone.json'),'sha256':sha(ROOT/'audit/cache_clone.json')},
  'preservation_before_reference':{'path':str(ROOT/'audit/preservation_before.json'),'sha256':sha(ROOT/'audit/preservation_before.json')},
  'model_calls':0,'private_gold_semantic_reads':0,'new_initial_LSP_collection_calls':0,'target_runtime_probe_calls':0,
  'API':'ContextCollector(case,new_run_output,budgets,initial_state=context).query/export/close; source selection and LSP algorithms byte-identical to prior experiment.',
  'prior_writes':0,'old_context_backend_sha256':sha(INPUT_PREVIOUS/'context48.py'),'backend_algorithm_unchanged':sha(ROOT/'context48.py')==sha(INPUT_PREVIOUS/'context48.py')}
 put(ROOT/'audit/native_backend_ready.json',ready);print(json.dumps({'ready':True,'cases':48,'owner_refs':len(refs),'pinned_Go':len(pins),'queries':2,'model_calls':0}),flush=True)

if __name__=='__main__':main()
