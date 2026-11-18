"""Official ReviewBench contract -> L6 multilingual -> lossless findings adapter."""
import hashlib,io,json,os,subprocess,sys,tarfile,time,traceback,urllib.request
from pathlib import Path
ROOT=Path(os.environ.get('REVIEW_RUN_ROOT','/work/out/agent')).resolve();ROOT.mkdir(parents=True,exist_ok=True)
os.environ['REVIEW_RUN_ROOT']=str(ROOT)
from common import write_new,sha,source_tokens,BUDGETS,render_evidence
from multilingual import ContextCollector,EXT,COMMANDS
from review_prompt import reviewer_input,OFFLINE_INSTRUCTION
from location_ids import decode
import jsonschema

def command(args,**kw):return subprocess.check_output(args,**kw)
def snapshot(repo,commit,dest=None):
 if dest:
  dest.mkdir(parents=True,exist_ok=True)
  raw=command(['git','-C',str(repo),'archive','--format=tar',commit]);tarfile.open(fileobj=io.BytesIO(raw)).extractall(dest,filter='data');root=dest
 else:root=repo
 tracked=command(['git','-C',str(repo),'ls-tree','-rz','--name-only',commit]).decode().split('\0');files={};unavailable=[]
 for rel in tracked:
  if not rel:continue
  p=root/rel
  if p.is_symlink()or not p.is_file():unavailable.append(dict(path=rel,reason='symlink_submodule_or_unavailable'));continue
  files[rel]=sha(p)
 profiles={}
 languages=sorted({ContextCollector.language_for(p)for p in files}-{None})
 env=dict(analysis_environment='Docker image toolchain; historical PR environment equivalence not proven',runtime_binding_proven=False)
 for lang in languages:profiles[lang]={'native_env':{},'environment_assumptions':[env],'allowed_external_roots':[]}
 if 'Python'in profiles:
  profiles['Python']['python_executable']=sys.executable
  # Explicit assumption, never the historical PR's proven stdlib implementation.
  stdlib=Path(sys.base_prefix)/'lib'/('python'+str(sys.version_info.major)+'.'+str(sys.version_info.minor))
  profiles['Python']['allowed_external_roots']=[dict(root=str(stdlib),origin='analysis_python_stdlib',version=sys.version,binding_status='environment_assumption',excluded_subdirectories=['site-packages','dist-packages'],assumptions=['Image interpreter selected for static analysis; PR historical interpreter/dependency binding unresolved.'])]
 if 'Go'in profiles:
  go=json.loads(command(['go','env','-json','GOROOT','GOVERSION','GOOS','GOARCH','CGO_ENABLED']).decode());pins={}
  for p in files:
   if p=='go.mod'or p.endswith('/go.mod'):pins[str(Path(p).parent)]={**go,'go_binary':str(Path(go['GOROOT'])/'bin/go')}
  profiles['Go']['pinned_toolchains']=pins
  profiles['Go']['allowed_external_roots']=[dict(root=str(Path(go['GOROOT'])/'src'),origin='go_stdlib',version=go['GOVERSION'],binding_status='environment_assumption',assumptions=['Explicit analysis toolchain, PR toolchain compatibility recorded but not runtime-equivalent.'])]
 return dict(root=str(root),commit=commit,files=files,native_profiles=profiles,analysis_environment=env,unavailable=unavailable)

def main():
 start=time.monotonic();repo=Path(os.environ['RB_REPO']).resolve();pr=json.loads(Path(os.environ['RB_PR_JSON']).read_text());diff=Path(os.environ['RB_DIFF']).read_text()
 actual_base=command(['git','-C',str(repo),'merge-base',pr['base'],pr['head']]).decode().strip()
 case=dict(id=os.environ['RB_NWO'].replace('/','_')+'_'+str(pr['pr_number']),language='Mixed',title=pr.get('title')or'',body=pr.get('body')or'',diff=diff,changed_files=command(['git','-C',str(repo),'diff','--name-only',actual_base,pr['head']]).decode().splitlines(),snapshots={})
 for name,commit in [('HEAD',pr['head']),('BASE',actual_base)]:case['snapshots'][name]=snapshot(repo,commit,None if name=='HEAD'else ROOT/'base')
 write_new(ROOT/'case.json',case);write_new(ROOT/'input_identity.json',dict(declared_base=pr['base'],analysis_base=actual_base,head=pr['head'],diff_sha256=sha(Path(os.environ['RB_DIFF'])),base_basis='Official runner three-dot diff merge base',image_build_environment_assumption=True))
 tool=ContextCollector(case,ROOT/'context',{**BUDGETS,'ast_source_tokens':3000,'lsp_source_tokens':6000,'max_native_calls':32,'max_file_reads':128})
 try:ast_context=tool.collect('AST');context=tool.collect('LSP')
 finally:tool.close()
 context['cost']=tool.export()['cost'];write_new(ROOT/'ast.context.json',ast_context);write_new(ROOT/'lsp.context.json',context)
 raw,schema,evidence,catalog=reviewer_input(case,context)
 write_new(ROOT/'delivered_evidence.json',evidence);write_new(ROOT/'locations.json',catalog);write_new(ROOT/'review.schema.json',schema)
 text='[SYSTEM]\n'+raw['system']+'\n\n[PROMPT]\n'+raw['prompt']
 body=dict(model='gpt-5.6-sol',input=[{'role':'developer','content':OFFLINE_INSTRUCTION},{'role':'user','content':text}],reasoning={'effort':'low'},text={'format':{'type':'json_schema','name':'research_output','strict':True,'schema':schema}},max_output_tokens=16384,tools=[],store=False,service_tier='default')
 write_new(ROOT/'review.request.json',body)
 if os.environ.get('REVIEW_PREPARE_ONLY')=='1':
  write_new(ROOT/'prepare.complete.json',dict(complete=True,source_tokens=source_tokens(evidence),native_calls=context['cost']['native_calls']));return
 req=urllib.request.Request(os.environ['REVIEW_BASE_URL']+'/responses',data=json.dumps(body).encode(),headers={'Authorization':'Bearer '+os.environ['REVIEW_BROKER_TOKEN'],'Content-Type':'application/json'},method='POST')
 with urllib.request.urlopen(req,timeout=1000)as r:response=json.load(r)
 write_new(ROOT/'review.response.json',response)
 if response.get('status')!='completed':raise RuntimeError('Model did not complete; response retained')
 output=''.join(c.get('text','')for item in response.get('output',[])if item.get('type')=='message'for c in item.get('content',[])if c.get('type')=='output_text')
 payload=json.loads(output);jsonschema.validate(payload,schema);canonical=decode(payload,catalog)
 write_new(ROOT/'review.wire.json',payload);write_new(ROOT/'review.decoded.json',canonical)
 findings=[];unmapped=[]
 for i,f in enumerate(canonical['findings']):
  loc=f['regression_anchor']
  if loc.get('status')!='bound'or loc.get('snapshot')!='HEAD':
   unmapped.append(dict(finding_index=i,reason='regression_anchor_unresolved',finding=f));continue
  p=repo/loc['path'];actual=p.resolve()
  if not actual.is_relative_to(repo)or not p.is_file():raise ValueError('Controller-bound HEAD path no longer valid')
  n=len(p.read_text().splitlines())
  if not 1<=loc['start_line']<=loc['end_line']<=n:raise ValueError('Controller-bound HEAD lines no longer valid')
  message=f['title']+'\n\n'+f['explanation']+'\n\nTrigger: '+f['trigger']+'\nImpact: '+f['impact']+'\nSuggested fix target: '+f['suggested_fix_target']
  if f['uncertainties']:message+='\nUncertainties: '+'; '.join(f['uncertainties'])
  findings.append(dict(file=loc['path'],start_line=loc['start_line'],end_line=loc['end_line'],message=message,producer=os.environ.get('RB_AGENT','try-agent')))
 write_new(ROOT/'conversion_audit.json',dict(unmapped=unmapped,lossless_wording=True,semantic_repair=False,location_policy='HEAD regression anchor only; unresolved findings retained here but cannot become valid official comments.'))
 usage=response.get('usage',{});i=usage.get('input_tokens',0);o=usage.get('output_tokens',0);cache=(usage.get('input_tokens_details')or{}).get('cached_tokens',0);long=i>272000
 cost=((i-cache)*(8 if long else 4)+cache*(.8 if long else .4)+o*(30 if long else 20))/1e6
 result=dict(pr={k:pr[k]for k in('repo','pr_number','base','head')},agent=os.environ.get('RB_AGENT','try-agent'),findings=findings,usage=dict(prompt_tokens=i,completion_tokens=o,total_tokens=i+o,cached_tokens=cache,time_in_ms=round((time.monotonic()-start)*1000),cost_usd=cost))
 write_new(Path(os.environ['RB_OUT']),result);write_new(ROOT/'complete.json',dict(complete=True,findings=len(findings),raw_findings=len(canonical['findings']),unmapped=len(unmapped),seconds=time.monotonic()-start,native=context['cost']))
 print(json.dumps(dict(pr=case['id'],findings=len(findings),native_calls=context['cost']['native_calls'],source_tokens=context['source_tokens'],usage=usage)),flush=True)
if __name__=='__main__':
 try:main()
 except Exception:
  write_new(ROOT/'failure.json',dict(traceback=traceback.format_exc(),no_model_retry=True));traceback.print_exc();sys.exit(1)
