"""Seen-case development: hand-specified gap routes, native LSP admission, matched fresh B/T."""
import ast,concurrent.futures,hashlib,importlib.util,json,random,re,sys
from functools import lru_cache
from pathlib import Path
R=Path(__file__).resolve().parent;ROOT=R.parents[1];PRIOR=ROOT/'runs/lsp_ablation_fresh_20261001';sys.path.insert(0,str(PRIOR));sys.path.insert(0,str(ROOT))
import policy
sp=importlib.util.spec_from_file_location('target_shared',PRIOR/'experiment.py');ex=importlib.util.module_from_spec(sp);sp.loader.exec_module(ex)
from ace_review.context_adaptive import Corpus,EvidenceState,digest,token_count
read=ex.read
save=ex.save
prompt=ex.prompt
from ace_review.isolated_review import call_packet

def at(src,text,symbol):
 rows=src.splitlines();i=next(i for i,s in enumerate(rows) if text in s);m=re.search(r'\b'+re.escape(symbol)+r'\b',rows[i]);assert m;return i,len(rows[i][:m.start()].encode('utf-16-le'))//2

def prepare_one(pp):
 p=read(pp);cid=p['case_id'];w=p['world'];row=next(x for x in read(PRIOR/'selected.json') if x['instance_id']==p['instance_id']);d=read(PRIOR/'labels.json')[cid]['directions'][w];base,head=('original','fixed') if d=='forward' else ('fixed','original')
 src={v:ex.old.full_sources(PRIOR/'checkouts'/row['instance_id']/v,row['repo']) for v in ['original','fixed']};selected=set(p['source_sha256']['head'])|set(p['source_sha256']['base']);vv={v:{f:src[rev][f] for f in sorted(selected) if f in src[rev]} for v,rev in [('base',base),('head',head)]}
  # Rebuild exactly the direction diff used by previous common input.
 import difflib
 changed=p['source_selection']['changed_files'];diff=''.join(''.join(difflib.unified_diff(vv['base'][f].splitlines(True),vv['head'][f].splitlines(True),fromfile='a/'+f,tofile='b/'+f)) for f in changed)
 c=Corpus(vv,{v:{'nodes':{},'edges':[]} for v in vv},diff);assert c.source_hashes==p['source_sha256'];resolver=policy.ref.old.Bindings(c,vv['head']);state=policy.restored(c,p);initial={k:set(v) for k,v in state.lines.items()};extras=[];admitted=[];evicted=[];protected=set();candidates=[]
 def units(file,symbol):return [k for k,u in c.units.items() if u.version=='head' and u.file==file and u.symbol==symbol]
 def add(file,symbol,reason,markers=None):
  ids=units(file,symbol);assert ids,(file,symbol)
  for k in ids:
   u=c.units[k];lines=set(range(u.start,u.end+1))
   if markers:
    tree=ast.parse(vv['head'][file]);fn=next(n for n in ast.walk(tree) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.end_lineno==u.end and n.lineno>=u.start);lines=set(range(u.start,u.header_end+1));hot=[]
    for n in ast.walk(fn):
     if isinstance(n,ast.stmt) and not isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)):
      rendered=ast.get_source_segment(vv['head'][file],n) or ''
      # Match the statement's own first line, avoiding selection of the entire parent body.
      own=rendered.splitlines()[0]
      if any(m in own for m in markers):hot.append(n)
    for n in hot:
     if hasattr(n,'body') and isinstance(n.body,list) and n.body:lines.update(range(n.lineno,n.body[0].lineno))
     else:lines.update(range(n.lineno,n.end_lineno+1))
     for owner in ast.walk(fn):
      if isinstance(owner,(ast.If,ast.Try,ast.For,ast.With,ast.ExceptHandler)) and owner.lineno<=n.lineno<=owner.end_lineno and owner.body:
       lines.update(range(owner.lineno,owner.body[0].lineno))
    lines&=set(range(u.start,u.end+1));assert len(lines)>1
   candidates.append((k,lines,reason))
 def preserve(symbols):
  protected.update(k for k in initial if c.units[k].version=='head' and c.units[k].symbol in symbols)
 if cid in ['N03','N04','N05']:
  root=ROOT/p['workspace_reference'];cl=policy.Client(root);backend=policy.ref.LSPBackend(resolver,cl)
  def query(method,file,text,symbol):
   line,char=at(vv['head'][file],text,symbol);result=getattr(cl,method)(file,line,char)
   if method=='definition':
    targets,excluded=backend.map_result(result);ids=[k for f,n in targets for k in resolver.unit(f,n)]
   else:
    hits,excluded=policy.locations(c,cl,result);ids=list(dict.fromkeys(k for k,l in hits));targets=[(c.units[k].file,c.units[k].symbol) for k in ids]
   extras.append({'method':method,'file':file,'line':line+1,'utf16_character':char,'symbol':symbol,'units':ids,'targets':targets,'excluded':excluded,'raw_result':result,'error':None});return set(targets)
  try:
   if cid=='N03':
    f='_pytest/skipping.py';targets=query('references',f,'xfailed_key =','xfailed_key');preserve(['pytest_runtest_call','pytest_runtest_setup','evaluate_xfail_marks','xfailed_key'])
    if (f,'pytest_runtest_makereport') in targets:add(f,'pytest_runtest_makereport','LSP state-key reference consumer')
   elif cid=='N04':
    preserve(['Function._eval_evalf','implemented_function']);targets=query('definition','sympy/core/function.py','return Float(self._imp_','Float')
    if ('sympy/core/numbers.py','Float') in targets:
     add('sympy/core/numbers.py','Float.__new__','AST constructor child of native Float class', ['isinstance(num, (SYMPY_INTS, Integer))','num = str(num)','isinstance(num, Float)','_mpf_ = num._mpf_','_mpf_ = mlib.from_float','_mpf_ = mlib.from_str','return num','dps = prec','dps_to_prec','isinstance(num, Rational)','from_rational','mpmath.mpf(num)','obj = Expr.__new__','obj._mpf_','obj._prec','return obj'])
    targets=query('definition','sympy/core/expr.py','class Expr(','EvalfMixin')
    if ('sympy/core/evalf.py','EvalfMixin') in targets:
     add('sympy/core/evalf.py','EvalfMixin.evalf','AST evalf child of native mixin class',['if not evalf_table','_create_evalf_table','prec = dps_to_prec','options =','result = evalf(self','except NotImplementedError','v = self._eval_evalf','if v is None','return self','result = evalf(v','return v'])
    targets=query('definition','sympy/core/evalf.py','result = evalf(self,','evalf')
    if ('sympy/core/evalf.py','evalf') in targets:
     add('sympy/core/evalf.py','evalf','Native evalf dispatcher',['rf = evalf_table','r = rf(','except KeyError','xe = x._eval_evalf','re, im = xe.as_real_imag','except AttributeError','raise NotImplementedError'])
   else:
    f='sympy/printing/str.py';preserve(['Printer._print','StrPrinter._print_Limit','StrPrinter._print_Relational']);targets=query('definition',f,'p = StrPrinter(settings)','StrPrinter')
    if (f,'StrPrinter') in targets:
     add(f,'StrPrinter._print_Limit','Contract-specific child of native StrPrinter class');add(f,'sstr','Public caller of native printer class');add(f,'StrPrinter._print_Rational','Setting-use child of native StrPrinter class');add(f,'StrPrinter._print_Relational','Contract-specific child of native StrPrinter class')
     # Class non-function statements with default settings and printmethod.
     for k,u in c.units.items():
      if u.version=='head' and u.file==f and u.start>=18 and u.end<=30 and u.kind!='function':candidates.append((k,set(range(u.start,u.end+1)),'Native class configuration'))
    targets=query('definition',f,'class StrPrinter(Printer)','Printer')
    if ('sympy/printing/printer.py','Printer') in targets:
     add('sympy/printing/printer.py','Printer._print','Dispatch child of native base class');add('sympy/printing/printer.py','Printer.__init__','Settings constructor child of native base class')
    targets=query('definition',f,'s = p.doprint(expr)','doprint')
    if ('sympy/printing/printer.py','Printer.doprint') in targets:add('sympy/printing/printer.py','Printer.doprint','Native public print entry')
  finally:cl.close()
  save(R/'lsp_traces'/f'{pp.stem}_T.json',{'trace':cl.trace,'initialization':cl.initialization,'stderr':cl.errors})
  # Admit targeted source transactionally. Preserve selected existing contract roots exactly.
  scores=c.scores(read(PRIOR/'scopes.json')[p['instance_id']]['scope']);removable=sorted(set(initial)-protected,key=lambda k:(c.units[k].version!='base',scores.get(k,0),k))
  for k,lines,reason in candidates:
   before={x:set(y) for x,y in state.lines.items()};removed=[];state.lines.setdefault(k,set()).update(lines)
   while state.tokens>1600:
    choices=[x for x in removable if x in state.lines and x not in protected and x!=k]
    if not choices:state.lines=before;break
    victim=choices[0];state.lines.pop(victim);removed.append(victim)
   else:
    if state.lines!=before:admitted.append({'unit_id':k,'reason':reason,'selected_lines':sorted(state.lines[k]),'partial':state.lines[k]!=set(range(c.units[k].start,c.units[k].end+1)),'evicted':removed});evicted+=removed
    protected.add(k)
  assert all(state.lines[k]==initial[k] for k in protected&set(initial))
 assert state.tokens<=1600
 p['group']='seen_development';p.update(T=state.render(),T_observations=policy.observation(p['B_audit']['jumps'],extras) if extras else p['B_observations'],T_audit={'state':state.audit(),'jumps':p['B_audit']['jumps'],'extra_navigation':extras,'protected_ids':sorted(protected&set(initial)),'evicted_ids':evicted,'additions':admitted,'policy':'Human-configured gap targets on previously seen cases. Native target admission; class-child/source slicing by AST. Unrelated B units may be evicted. No runtime execution claim.'})
 for key in list(p):
  if key=='P' or key=='R' or key.startswith('P_') or key.startswith('R_'):p.pop(key)
 save(R/'packets'/pp.name,p);print(pp.stem,state.tokens,len(extras),len(admitted),flush=True)

def prepare():
 for d in ['packets','lsp_traces','inputs','raw']:(R/d).mkdir(exist_ok=False)
 ast._splitlines_no_ff=lru_cache(maxsize=128)(ast._splitlines_no_ff)
 with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(prepare_one,sorted((PRIOR/'packets').glob('*.json'))))
 save(R/'labels.json',read(PRIOR/'labels.json'));save(R/'schema.json',read(PRIOR/'schema.json'));jobs=[];shared={}
 for pp in sorted((R/'packets').glob('*.json')):
  p=read(pp)
  for rep in [1,2,3]:
   seen={}
   for arm in ['B','T']:
    rid=f'{pp.stem}_{arm}_r{rep}';text=prompt(p,arm);h=digest(text);row={'run_id':rid,'case_id':p['case_id'],'world':p['world'],'group':p['group'],'arm':arm,'repeat':rep,'prompt':text,'prompt_sha256':h,'prompt_reference_tokens':token_count(text)};save(R/'inputs'/f'{rid}.json',row)
    if h in seen:shared[rid]=seen[h]
    else:seen[h]=rid;jobs.append({k:row[k] for k in ['run_id','case_id','world','group','arm','repeat']})
 files=[p for p in R.glob('*') if p.is_file()]+[p for d in ['packets','inputs','lsp_traces'] for p in (R/d).glob('*.json')];deps=set(read(PRIOR/'freeze.json')['files'])|{str(p.relative_to(ROOT)) for p in PRIOR.glob('*.py')}|{str((PRIOR/'freeze.json').relative_to(ROOT))};files += [ROOT/p for p in sorted(deps)]+[ROOT/'runs/lsp_targeted_paths_20261001/preparation_seal.json']
 save(R/'freeze.json',{'model':'gpt-5.6-sol','effort':'low','logical_reviews':72,'physical_calls':len(jobs),'jobs':jobs,'shared_identical_inputs':shared,'repeats':3,'source_cap':1600,'observation_cap':400,'query_cap':15,'common_definition_cap':12,'extra_query_cap':3,'workers':4,'retry':False,'provider_seed_control':False,'model_calls_before_freeze':0,'primary_contrast':'T minus fresh matched B on six already-seen development PRs; targeted gap routes tuned on prior failures, no unseen/generalization claim','files':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}})
 print('Frozen 72 logical / '+str(len(jobs))+' physical',flush=True)
def run():
 f=read(R/'freeze.json');assert all(hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==h for p,h in f['files'].items())
 for m in (PRIOR/'workspace_manifests').glob('*.json'):assert all(digest((PRIOR/'workspaces'/m.stem/p).read_text())==h for p,h in read(m)['source_sha256'].items())
 jobs=list(f['jobs']);random.Random(20261013).shuffle(jobs)
 def one(j):
  rid=j['run_id'];assert not (R/'raw'/f'{rid}.json').exists();call_packet(read(R/'inputs'/f'{rid}.json'),R/'raw',f['model'],read(R/'schema.json'));print('Completed '+rid,flush=True)
 with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(one,jobs))
if __name__=='__main__':prepare() if sys.argv[1]=='prepare' else run()
