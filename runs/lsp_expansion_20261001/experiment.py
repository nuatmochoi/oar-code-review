"""New repositories, real fix PRs and declared reverse controls; frozen backend comparison."""
from __future__ import annotations
import ast,concurrent.futures,difflib,hashlib,importlib.util,json,os,random,re,sys,threading,time
from functools import lru_cache
from pathlib import Path
R=Path(__file__).resolve().parent;ROOT=R.parents[1];PREV=ROOT/'runs/lsp_backend_20261001'
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(PREV))
from backend import old,ASTBackend,LSPBackend,packet
from lsp_client import Client,SERVER
from ace_review.context_adaptive import Corpus,digest,token_count,FINAL_PROMPT
from ace_review.context_adaptive_run import common
from ace_review.isolated_review import call_packet
spec=importlib.util.spec_from_file_location('ledger',ROOT/'runs/grounded_scope_20261001/experiment.py');ledger=importlib.util.module_from_spec(spec);spec.loader.exec_module(ledger)
read=lambda p:json.loads(p.read_text())
def save(p,obj):
 p.parent.mkdir(parents=True,exist_ok=True)
 with p.open('x') as f:json.dump(obj,f,ensure_ascii=False,indent=2)
LOCK=threading.Lock()
def full_sources(root,repo):
 source=root/'src' if repo=='pytest-dev/pytest' else root;prefixes=('_pytest','pytest.py') if repo=='pytest-dev/pytest' else ('requests',) if repo=='psf/requests' else ('sympy',)
 out={}
 for p in sorted(source.rglob('*.py')):
  rel=p.relative_to(source)
  if rel.parts[0] not in prefixes or any(x in {'tests','testing','benchmarks','examples','__pycache__'} for x in rel.parts) or p.name.startswith('test_') or p.name=='conftest.py':continue
  text=p.read_text(encoding='utf-8');out[rel.as_posix()]=text
 return out

def closure(sources,changed):
 modules={p.removesuffix('.py').replace('/','.').removesuffix('.__init__'):p for p in sources};selected=set(changed);front=set(changed)
 for depth in range(2):
  nxt=set()
  for file in sorted(front):
   if file not in sources:continue
   mod=file.removesuffix('.py').replace('/','.');pkg=mod.split('.')[:-1] if not file.endswith('__init__.py') else mod.split('.')[:-1]
   for n in ast.walk(ast.parse(sources[file])):
    names=[]
    if isinstance(n,ast.Import):names=[x.name for x in n.names]
    elif isinstance(n,ast.ImportFrom):
     base='.'.join(pkg[:len(pkg)-n.level+1]+([n.module] if n.module else [])) if n.level else n.module or ''
     names=[base]+[base+'.'+a.name for a in n.names]
    for name in names:
     if name in modules:nxt.add(modules[name])
   for i in range(1,len(Path(file).parts)):
    init='/'.join(Path(file).parts[:i])+'/__init__.py'
    if init in sources:nxt.add(init)
  front=nxt-selected;selected|=nxt
 return selected

def stage(sources,key):
 root=R/'workspaces'/key;root.mkdir(parents=True,exist_ok=False);pool=R/'source_pool';pool.mkdir(exist_ok=True);hashes={}
 for file,src in sources.items():
  h=digest(src);hashes[file]=h;p=pool/(h+'.py')
  with LOCK:
   if not p.exists():p.write_text(src)
  dest=root/file;dest.parent.mkdir(parents=True,exist_ok=True);os.link(p,dest)
 config={'include':['**/*.py'],'pythonVersion':'3.11','typeCheckingMode':'off','autoSearchPaths':False,'useLibraryCodeForTypes':False,'extraPaths':['.']};(root/'pyrightconfig.json').write_text(json.dumps(config))
 return root,{'source_sha256':hashes,'pyrightconfig':config}

def observations(jumps):
 lines=[]
 for i,j in enumerate(jumps,1):
  targets='; '.join(f+'::'+n for f,n in j['targets']) or 'unresolved in this analysis'
  line=f'[E-NAV-{i:02d}] head {j["source_file"]}:{j["line"]}, UTF16 column {j["utf16_character"]}, symbol {j["called"]}: static definition candidates -> {targets}.'
  if token_count('\n'.join(lines+[line]))>400:break
  lines.append(line)
 return '\n'.join(lines)

def prep_one(job):
 row,world,direction,cid=job;start=time.monotonic();iid=row['instance_id'];checkout=R/'checkouts'/iid
 allsrc={rev:full_sources(checkout/rev,row['repo']) for rev in ['original','fixed']}
 changed=[x for x in allsrc['original'] if allsrc['original'][x]!=allsrc['fixed'].get(x)]
 assert changed
 selected=closure(allsrc['original'],changed)|closure(allsrc['fixed'],changed)
 sources={rev:{p:allsrc[rev][p] for p in sorted(selected) if p in allsrc[rev]} for rev in ['original','fixed']}
 base,head=('original','fixed') if direction=='forward' else ('fixed','original')
 versions={'base':sources[base],'head':sources[head]}
 diff=''.join(''.join(difflib.unified_diff(sources[base][p].splitlines(True),sources[head][p].splitlines(True),fromfile='a/'+p,tofile='b/'+p)) for p in changed)
 scope='Review only newly introduced violations of the following public API contract, including the public issue reproduction inputs. A bug already present in base is not an introduced regression. A fix is not an introduced regression merely because it changes behavior. Do not assume unstated runtime bindings or values.\n'+row['problem_statement'][:5000]
 case={'scope':scope,'diff':diff};text=common({'repository':row['repo'],'scope':scope,'diff':diff})
 corpus=Corpus(versions,{v:{'nodes':{},'edges':[]} for v in versions},diff);resolver=old.Bindings(corpus,versions['head']);key=cid+'_'+world
 root,manifest=stage(allsrc[head],key);save(R/'workspace_manifests'/f'{key}.json',manifest)
 aa,audit_a=packet(corpus,case,resolver,ASTBackend(resolver));client=Client(root)
 try:ll,audit_l=packet(corpus,case,resolver,LSPBackend(resolver,client))
 finally:client.close()
 save(R/'lsp_traces'/f'{key}.json',{'initialization':client.initialization,'startup_seconds':client.startup_seconds,'trace':client.trace,'stderr':client.errors})
 result={'case_id':cid,'world':world,'stage':'external_repository_pr_derived','repository':row['repo'],'instance_id':iid,'common':text,'AST':aa.render(),'LSP':ll.render(),'AST_observations':observations(audit_a['jumps']),'LSP_observations':observations(audit_l['jumps']),'AST_audit':audit_a,'LSP_audit':audit_l,'source_sha256':corpus.source_hashes,'source_selection':{'rule':'Changed production files + static import two-hop union across both revisions + ancestor package initializers','corpus_files':len(selected),'full_workspace_files':len(allsrc[head]),'changed_files':changed},'prepare_seconds':time.monotonic()-start}
 save(R/'packets'/f'{key}.json',result)
 print(json.dumps({'prepared':key,'instance':iid,'different':result['AST']!=result['LSP'] or result['AST_observations']!=result['LSP_observations'],'corpus_files':len(selected),'full_files':len(allsrc[head]),'seconds':round(time.monotonic()-start,2)}),flush=True)

def prepare():
 assert not (R/'freeze.json').exists()
 for d in ['packets','lsp_traces','workspace_manifests','inputs','raw']:(R/d).mkdir(exist_ok=False)
 ast._splitlines_no_ff=lru_cache(maxsize=128)(ast._splitlines_no_ff)
 rows=read(R/'selected.json');labels={};jobs=[];rng=random.Random(20261002)
 for idx,row in enumerate(rows,1):
  cid=f'E{idx:02d}';dirs=['forward','reverse'];rng.shuffle(dirs);labels[cid]={'instance_id':row['instance_id'],'repository':row['repo'],'directions':dict(zip(['a','b'],dirs)),'worlds':{w:'safe' if d=='forward' else 'unsafe' for w,d in zip(['a','b'],dirs)}}
  jobs.extend((row,w,d,cid) for w,d in zip(['a','b'],dirs))
 save(R/'labels.json',labels);save(R/'schema.json',ledger.LEDGER_SCHEMA)
 with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(prep_one,jobs))
 packets=[read(p) for p in (R/'packets').glob('*.json')];shared={p['case_id']+'_'+p['world']+'_lsp':p['case_id']+'_'+p['world']+'_ast' for p in packets if p['AST']==p['LSP'] and p['AST_observations']==p['LSP_observations']}
 files=[R/x for x in ['experiment.py','PROTOCOL_KO.md','selected.json','provenance.json','labels.json','schema.json','witness_one.py','witnesses.py','witnesses.json']]+[p for d in ['packets','lsp_traces','workspace_manifests'] for p in (R/d).glob('*.json')]+[PREV/'backend.py',PREV/'lsp_client.py',ROOT/'runs/binding_context_20261001/binding.py',ROOT/'runs/icml_priority_20261001/policy.py',ROOT/'runs/grounded_scope_20261001/experiment.py',ROOT/'ace_review/context_adaptive.py',ROOT/'ace_review/isolated_review.py',ROOT/'ace_review/reviewer.py',ROOT/'ace_review/packet_review.py',ROOT/'tools/pyright-lsp-20261001/package-lock.json',SERVER]
 save(R/'freeze.json',{'model':'gpt-5.6-sol','effort':'low','source_cap':1600,'observation_cap':400,'logical_reviews':48,'planned_physical_calls':48-len(shared),'shared_identical_inputs':shared,'pyright_version':'1.1.414','files':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files},'retry':False,'workers':4,'RL':False,'public_bugfix_and_reverse_controls':True})
 print('Frozen',48-len(shared),'physical model calls.',flush=True)

def run():
 f=read(R/'freeze.json')
 assert all(hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==h for p,h in f['files'].items())
 for p in (R/'workspace_manifests').glob('*.json'):
  m=read(p);assert all(digest((R/'workspaces'/p.stem/file).read_text())==h for file,h in m['source_sha256'].items())
 jobs=[(read(p),arm) for p in sorted((R/'packets').glob('*.json')) for arm in ['ast','lsp'] if p.stem+'_'+arm not in f['shared_identical_inputs']];random.Random(20261003).shuffle(jobs)
 def worker(job):
  p,arm=job;rid=p['case_id']+'_'+p['world']+'_'+arm
  if (R/'raw'/f'{rid}.json').exists():return
  prompt=ledger.LEDGER_PROMPT+'\n'+FINAL_PROMPT+p['common']+'\n[EVIDENCE]\n'+p[arm.upper()]+'\n[STATIC DEFINITION OBSERVATIONS — NOT RUNTIME PROOF]\n'+p[arm.upper()+'_observations']
  row={'run_id':rid,'case_id':p['case_id'],'world':p['world'],'stage':p['stage'],'arm':arm,'prompt':prompt,'prompt_sha256':digest(prompt),'prompt_reference_tokens':token_count(prompt)}
  save(R/'inputs'/f'{rid}.json',row);call_packet(row,R/'raw','gpt-5.6-sol',read(R/'schema.json'))
 with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(worker,jobs))
 print('All physical review calls completed.',flush=True)
if __name__=='__main__':prepare() if sys.argv[1]=='prepare' else run()
