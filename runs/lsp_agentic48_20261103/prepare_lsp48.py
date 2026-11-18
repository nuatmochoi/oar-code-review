"""Reuse exact frozen B2 contexts/public cases; project only writable environments.

No LSP preparation, source collection, SCIP read, private gold read, or model
call occurs here. All initial source/evidence IDs and bytes remain unchanged.
"""
from pathlib import Path
import copy,json,os,shutil,time
from cache_isolation import ROOT,PREVIOUS,CACHE,prepare,isolation_environment,sha,digest,put

def load(p):return json.loads(Path(p).read_text())
def remove_scip(d):
 for key in list(d):
  if key.startswith('scip_'):del d[key]
 d['scip_exports']={};d['scip_usage_allowed']=False

def pinned_toolchains(spec,profile):
 result={}
 for authority in profile.get('allowed_external_roots',[]):
  if authority.get('origin')!='go_stdlib':continue
  for ref in authority.get('binding_evidence',[]):
   p=Path(ref['path'])
   if p.name!='go-authority-env.stdout':continue
   assert sha(p)==ref['sha256'];v=load(p)
   root=Path(v['GOROOT']);go=root/'bin/go';assert go.is_file()
   gomod=Path(v['GOMOD'])
   if not gomod.is_absolute()or not gomod.parent.is_relative_to(Path(spec['root'])):continue
   module=str(gomod.parent.relative_to(Path(spec['root'])))
   pin={'GOROOT':str(root),'GOVERSION':v['GOVERSION'],'go_binary':str(go),
    'go_binary_sha256':sha(go),'recorded_environment_reference':ref,
    'GOOS':v['GOOS'],'GOARCH':v['GOARCH'],'CGO_ENABLED':v['CGO_ENABLED']}
   if module in result:assert result[module]==pin
   result[module]=pin
 return result

def main():
 started=time.monotonic();clone=prepare();assert clone['complete']
 env=isolation_environment();old_cases=load(PREVIOUS/'prepared/cases.json');assert len(old_cases)==48
 new_cases=[];projections=[];context_rows=[]
 clone_ref={'path':str(ROOT/'audit/cache_clone.json'),'sha256':sha(ROOT/'audit/cache_clone.json')}
 old_context_summary=load(PREVIOUS/'contexts/collection_summary.json');assert old_context_summary['completed_cases']==48
 for old in old_cases:
  c=copy.deepcopy(old);c['scip_usage_allowed']=False;c['paired_index_seconds']=0
  original_pool=c['source_pool_hash']
  for snapshot,spec in c['snapshots'].items():
   original_spec=old['snapshots'][snapshot];remove_scip(spec)
   # Native results in a writable cache are canonicalized back to these sealed
   # exact source roots, after read-time byte equality checks by the backend.
   spec['source_environment_aliases']=[{'actual_root':str(CACHE/'go-mod'),
    'canonical_root':str(PREVIOUS/'native_validation/cache/go-mod'),
    'identity_reference':clone_ref,'scope':'Only declared old authorities can be admitted after canonicalization; this alias grants no new source authority.'}]
   for language,profile in spec.get('native_profiles',{}).items():
    original_profile=original_spec['native_profiles'][language];remove_scip(profile)
    profile['native_env']={**original_profile.get('native_env',{}),**env}
    if language=='Go':
     profile['pinned_toolchains']=pinned_toolchains(spec,original_profile)
     profile['native_env']['PATH']=os.environ.get('PATH','')
     profile['native_env'].pop('GOROOT',None)
    profile['environment_isolation_reference']=clone_ref
    profile['source_environment_origin']={'analysis_env_reference':original_profile.get('analysis_env_reference'),
     'native_env_sha256':digest(original_profile.get('native_env',{})),
     'authority_table_sha256':digest(original_profile.get('allowed_external_roots',[])),
     'read_only_sources_preserved':True}
    assert profile.get('allowed_external_roots')==original_profile.get('allowed_external_roots')
    assert profile.get('python_executable')==original_profile.get('python_executable')
   primary=spec['native_profiles'][c['language']]
   spec['native_env']=copy.deepcopy(primary['native_env'])
   spec['source_environment_origin']={'previous_snapshot_sha256':digest(original_spec),'cache_clone_reference':clone_ref}
   assert spec['root']==original_spec['root']and spec['files']==original_spec['files']
  c['source_pool_hash']=hashlib_common(c['snapshots'])
  c['original_source_pool_hash']=original_pool
  c['source_content_hash']=digest({s:{k:v for k,v in p.items()if k in('root','files','commit')}for s,p in c['snapshots'].items()})
  for key in ('diff','diff_reference','public_contract','title','body','base','head','changed_files'):
   assert c[key]==old[key]
  oldp=PREVIOUS/'contexts'/c['id']/'context_LSP.json';newp=ROOT/'contexts'/c['id']/'context_LSP.json'
  assert not newp.exists();newp.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(oldp,newp)
  assert sha(oldp)==sha(newp);context=load(newp)
  assert context['cost'].get('scip_index_reads',0)==0 and context['cost'].get('scip_index_bytes',0)==0
  assert set(context['stages'])<=set(('AST','LSP'))
  cr={'case_id':c['id'],'previous_reference':{'path':str(oldp),'sha256':sha(oldp)},
   'new_reference':{'path':str(newp),'sha256':sha(newp)},'evidence_sha256':context['delivery_sha256'],
   'original_source_pool_sha256':context['source_pool_sha256'],'source_tokens':context['source_tokens'],
   'initial_cost_unchanged':True,'new_preparation_native_calls':0,'new_preparation_model_calls':0}
  context_rows.append(cr);new_cases.append(c)
  projections.append({'case_id':c['id'],'old_snapshot_pool_sha256':original_pool,'new_snapshot_pool_sha256':c['source_pool_hash'],
   'read_only_source_roots_and_files_unchanged':True,'public_input_and_diff_unchanged':True,
   'old_LSP_context_bytes_and_evidence_IDs_unchanged':True,'SCIP_disabled':True,
   'only_analysis_cache_environment_and_explicit_SClP_prohibition_projected':True})
 put(ROOT/'prepared/cases.json',new_cases)
 put(ROOT/'contexts/collection_summary.json',{'complete':True,'completed_cases':48,'method':'Byte-identical frozen previous B2 context projection; no new collection','cases':context_rows,'previous_summary_reference':{'path':str(PREVIOUS/'contexts/collection_summary.json'),'sha256':sha(PREVIOUS/'contexts/collection_summary.json')},'new_native_calls':0,'model_calls':0})
 put(ROOT/'prepared/manifest.json',{'complete':True,'cases':48,'cases_sha256':sha(ROOT/'prepared/cases.json'),
  'previous_cases_reference':{'path':str(PREVIOUS/'prepared/cases.json'),'sha256':sha(PREVIOUS/'prepared/cases.json')},
  'cache_clone_reference':clone_ref,'contexts_reference':{'path':str(ROOT/'contexts/collection_summary.json'),'sha256':sha(ROOT/'contexts/collection_summary.json')},
  'source_environment_projection':projections,'SCIP_disabled':True,'private_gold_reads':0,'new_native_collection_calls':0,'model_calls':0,'seconds':time.monotonic()-started})
 print(json.dumps({'complete':True,'cases':48,'contexts':48,'SCIP_disabled':True,'model_calls':0}),flush=True)

def hashlib_common(v):
 import hashlib
 return hashlib.sha256(json.dumps(v,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

if __name__=='__main__':main()
