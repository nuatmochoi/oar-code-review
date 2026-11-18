"""Human-guided output-route development on seen cases; native admission, matched fresh B/O."""
import ast,concurrent.futures,difflib,hashlib,importlib.util,json,random,re,sys
from functools import lru_cache
from pathlib import Path
R=Path(__file__).resolve().parent;ROOT=R.parents[1];PRIOR=ROOT/'runs/lsp_state_holdout_20261002';sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'runs/lsp_ablation_fresh_20261001'))
import policy
sp=importlib.util.spec_from_file_location('output_shared_ex',ROOT/'runs/lsp_ablation_fresh_20261001/experiment.py');ex=importlib.util.module_from_spec(sp);sp.loader.exec_module(ex)
from ace_review.context_adaptive import Corpus,EvidenceState,digest,token_count
from ace_review.isolated_review import call_packet
read=ex.read;save=ex.save;prompt=ex.prompt
# Gap routes explicitly configured from inspected prior responses; not automatic or unseen validation.
ROUTES={
 'J01':[('_pytest/_code/code.py','ExceptionChainRepr',['__init__','toterminal']),('_pytest/_code/code.py','ExceptionRepr',['__init__','addsection']),('_pytest/_code/code.py','FormattedExcinfo.repr_excinfo',[])],
 'J02':[('_pytest/python.py','Package',['collect']),('_pytest/python.py','Module',['_getobj','_importtestmodule']),('_pytest/main.py','Session._collect',[])],
 'J03':[('_pytest/python.py','PyobjMixin.reportinfo',[]),('_pytest/reports.py','TestReport.from_item_and_call',[]),('_pytest/terminal.py','TerminalReporter.short_test_summary',[])],
 'J04':[('_pytest/python.py','_showfixtures_main',[]),('_pytest/fixtures.py','FixtureManager.parsefactories',[]),('_pytest/unittest.py','UnitTestCase._inject_setup_teardown_fixtures',[])]}

def prepare_one(pp):
 p=read(pp);cid=p['case_id'];w=p['world'];row=next(x for x in read(PRIOR/'selected.json') if x['instance_id']==p['instance_id']);d=read(PRIOR/'labels.json')[cid]['directions'][w];base,head=('original','fixed') if d=='forward' else ('fixed','original');src={v:ex.old.full_sources(PRIOR/'checkouts'/row['instance_id']/v,row['repo']) for v in ['original','fixed']};selected=set(p['source_sha256']['head'])|set(p['source_sha256']['base'])
 # Same full-workspace pool but output routes may lie outside the previous two-hop corpus.
 requested={f for f,n,ch in ROUTES[cid]};selected|=requested;vv={v:{f:src[rev][f] for f in sorted(selected) if f in src[rev]} for v,rev in [('base',base),('head',head)]};changed=p['source_selection']['changed_files'];diff=''.join(''.join(difflib.unified_diff(vv['base'][f].splitlines(True),vv['head'][f].splitlines(True),fromfile='a/'+f,tofile='b/'+f)) for f in changed);c=Corpus(vv,{v:{'nodes':{},'edges':[]} for v in vv},diff);resolver=policy.ref.old.Bindings(c,vv['head']);initial=policy.restored(c,p);state=EvidenceState(c);state.lines={k:set(v) for k,v in initial.lines.items()};state.paths=dict(initial.paths);root=ROOT/p['workspace_reference'];cl=policy.Client(root);native=policy.ref.LSPBackend(resolver,cl);extras=[];candidates=[];blocked=[]
 def add(file,name,reason):
  for k in resolver.unit(file,name):candidates.append((k,reason))
 try:
  for file,name,children in ROUTES[cid]:
   n=resolver.defs.get((file,name))
   if n is None:blocked.append({'file':file,'symbol':name,'reason':'Not in AST declaration index; native query not issued'});continue
   rowtext=vv['head'][file].splitlines()[n.lineno-1];m=re.search(r'\b(?:def|class)\s+('+re.escape(n.name)+r')\b',rowtext);line=n.lineno-1;char=len(rowtext[:m.start(1)].encode('utf-16-le'))//2;result=cl.definition(file,line,char);targets,excluded=native.map_result(result);units=[k for f,s in targets for k in resolver.unit(f,s)];extras.append({'method':'definition','file':file,'line':line+1,'utf16_character':char,'symbol':name,'units':units,'targets':targets,'raw_result':result,'excluded':excluded,'error':None})
   for f,s in targets:
    add(f,s,'Native definition at human-selected output-route declaration')
    for child in children:
     if (f,s+'.'+child) in resolver.defs:add(f,s+'.'+child,'AST child of native class; human-selected constructor/output method')
 finally:cl.close()
 save(R/'lsp_traces'/f'{pp.stem}_O.json',{'trace':cl.trace,'initialization':cl.initialization,'stderr':cl.errors})
 # Preserve existing scoped changed head roots; unrelated changed methods can be replaced.
 protected=set()
 for k in initial.lines:
  u=c.units[k]
  relevant=cid!='J04' or u.file=='_pytest/unittest.py'
  if relevant and u.version=='head' and k in c.seeds:protected.add(k)
 scores=c.scores(read(PRIOR/'scopes.json')[p['instance_id']]['scope']);removable=sorted(set(initial.lines)-protected,key=lambda k:(c.units[k].version!='base',scores.get(k,0),k));added=[];evicted=[]
 # Whole route units where possible; otherwise explicit first/last executable statement anchors and control conditions.
 for k,reason in list(dict.fromkeys(candidates)):
  if k in protected:continue
  u=c.units[k];lines=set(range(u.start,u.end+1));before={x:set(y) for x,y in state.lines.items()};removed=[]
  if token_count(state.render({k:lines},{}))>500:
   tree=ast.parse(vv[u.version][u.file]);fn=next((n for n in ast.walk(tree) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.end_lineno==u.end and n.lineno>=u.start),None);anchors=set(range(u.start,u.header_end+1))
   if fn:
    for n in ast.walk(fn):
     if isinstance(n,(ast.If,ast.For,ast.ExceptHandler)) and n.body:anchors.update(range(n.lineno,n.body[0].lineno))
     if isinstance(n,(ast.Return,ast.Assign,ast.AnnAssign)):
      text=ast.get_source_segment(vv[u.version][u.file],n) or ''
      if any(s in text for s in ['chain','longrepr','verbose','argname','fixture','_getobj','Package','reportinfo','getfslineno','ExceptionChainRepr','repr_excinfo','outcome','lineno']):anchors.update(range(n.lineno,min(n.end_lineno,n.lineno+3)+1))
    # Include output/skip/continue statements as well as predicates.
    for n in ast.walk(fn):
     if isinstance(n,(ast.Continue,ast.Break)):anchors.add(n.lineno)
     if isinstance(n,ast.Expr) and isinstance(n.value,ast.Call) and any(s in ast.unparse(n.value.func) for s in ['write','line','addsection']):anchors.update(range(n.lineno,n.end_lineno+1))
   ordered=sorted(anchors&lines);lines=set()
   for l in ordered:
    if token_count(state.render({k:lines|{l}},{}))<=500:lines.add(l)
  state.lines.setdefault(k,set()).update(lines)
  while state.tokens>1600:
   choices=[x for x in removable if x in state.lines and x not in protected and x!=k]
   if not choices:state.lines=before;blocked.append({'unit_id':k,'reason':'source cap with preserved head roots'});break
   victim=choices[0];state.lines.pop(victim);removed.append(victim)
  else:
   if state.lines!=before:added.append({'unit_id':k,'reason':reason,'selected_lines':sorted(state.lines[k]),'partial':state.lines[k]!=set(range(u.start,u.end+1)),'evicted':removed});evicted+=removed
   protected.add(k)
 assert state.tokens<=1600;assert all(state.lines[k]==initial.lines[k] for k in protected&set(initial.lines))
 p.pop('S');p.pop('S_audit');p.pop('S_observations');p.update(group='seen_output_development',O=state.render(),O_observations=policy.observation(p['B_audit']['jumps'],extras),O_audit={'state':state.audit(),'jumps':p['B_audit']['jumps'],'extra_navigation':extras,'protected_ids':sorted(protected&set(initial.lines)),'evicted_ids':evicted,'additions':added,'blocked':blocked,'policy':'Human-selected output route; native declaration definition confirms source target; AST child/slicing. No runtime binding proof or automatic gap selection claim.'},output_corpus_sha256=c.source_hashes);save(R/'packets'/pp.name,p);print('Prepared '+pp.stem+' additions='+str(len(added))+' blocked='+str(len(blocked)),flush=True)

def prepare():
 for d in ['packets','lsp_traces','inputs','raw']:(R/d).mkdir(exist_ok=False)
 ast._splitlines_no_ff=lru_cache(maxsize=128)(ast._splitlines_no_ff)
 with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(prepare_one,sorted((PRIOR/'packets').glob('*.json'))))
 save(R/'labels.json',read(PRIOR/'labels.json'));save(R/'schema.json',read(PRIOR/'schema.json'));jobs=[];shared={}
 for pp in sorted((R/'packets').glob('*.json')):
  p=read(pp)
  for rep in [1,2,3]:
   seen={}
   for arm in ['B','O']:
    rid=f'{pp.stem}_{arm}_r{rep}';text=prompt(p,arm);h=digest(text);inp={'run_id':rid,'case_id':p['case_id'],'world':p['world'],'group':p['group'],'arm':arm,'repeat':rep,'prompt':text,'prompt_sha256':h,'prompt_reference_tokens':token_count(text)};save(R/'inputs'/f'{rid}.json',inp)
    if h in seen:shared[rid]=seen[h]
    else:seen[h]=rid;jobs.append({k:inp[k] for k in ['run_id','case_id','world','group','arm','repeat']})
 files=[p for p in R.glob('*') if p.is_file()]+[p for d in ['packets','inputs','lsp_traces'] for p in (R/d).glob('*.json')];deps=set(read(PRIOR/'freeze.json')['files'])|{str(p.relative_to(ROOT)) for p in PRIOR.glob('*.py')}|{str((PRIOR/'freeze.json').relative_to(ROOT))};files += [ROOT/p for p in sorted(deps)]+[ROOT/'runs/lsp_output_paths_20261002/preparation_seal.json']
 save(R/'freeze.json',{'model':'gpt-5.6-sol','effort':'low','logical_reviews':48,'physical_calls':len(jobs),'jobs':jobs,'shared_identical_inputs':shared,'repeats':3,'source_cap':1600,'observation_cap':400,'query_cap':15,'workers':4,'retry':False,'provider_seed_control':False,'model_calls_before_freeze':0,'primary_contrast':'O minus fresh matched B: human-guided output-route source admission, four already-seen PRs. Not automatic/fresh/generalization or pure LSP effect.','files':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}})
 print('Frozen 48 logical / '+str(len(jobs))+' physical',flush=True)
def run():
 f=read(R/'freeze.json');assert all(hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==h for p,h in f['files'].items())
 for m in (PRIOR/'workspace_manifests').glob('*.json'):assert all(digest((PRIOR/'workspaces'/m.stem/p).read_text())==h for p,h in read(m)['source_sha256'].items())
 jobs=list(f['jobs']);random.Random(20261023).shuffle(jobs)
 def one(j):
  rid=j['run_id'];assert not (R/'raw'/f'{rid}.json').exists();call_packet(read(R/'inputs'/f'{rid}.json'),R/'raw',f['model'],read(R/'schema.json'))
 with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(one,jobs))
if __name__=='__main__':prepare() if sys.argv[1]=='prepare' else run()
