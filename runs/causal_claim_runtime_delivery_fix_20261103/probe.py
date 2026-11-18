"""One small AST-restricted child probe; not a general project execution backend.

The child is isolated (-I -S), has only whitelisted stdlib imports/builtins,
no environment/config access, and wall/CPU/output limits. This is an explicit
restricted development fixture, not an adversarial-code security sandbox.
"""
import ast,hashlib,json,os,subprocess,time
from pathlib import Path
from common import ROOT,PINNED_PYTHON,PROBE_BUDGETS,load,write_new,sha,digest

SAFE_MODULES={'functools','decimal','math','fractions','itertools','collections'}
SAFE_DUNDERS={'__name__','__qualname__','__module__','__dict__','__wrapped__','__doc__','__annotations__','__get__','__slots__','__call__','__init__','__new__'}
FORBIDDEN_NAMES={'open','eval','exec','compile','globals','locals','vars','input','breakpoint','help','dir','exit','quit','getattr','setattr','delattr','__builtins__','__import__'}

def validate_probe(plan,questions,public_imports):
    assert plan['scope']=='analysis_profile'and plan['fixture_origin']=='analysis_probe_fixture'
    assert 0<len(plan['code'].encode())<=PROBE_BUDGETS['max_code_bytes']
    allowed_q={q['id']for q in questions};assert plan['question_ids']and set(plan['question_ids'])<=allowed_q
    allowed_claim={q['claim_id']for q in questions};assert set(plan['claim_ids'])<=allowed_claim
    assert 0<len(plan['bindings'])<=PROBE_BUDGETS['max_bindings']
    imports={(v['module'],v['symbol'])for v in public_imports}
    bindings={(v['module'],v['symbol'])for v in plan['bindings']}
    assert bindings<=imports and all(m in SAFE_MODULES for m,s in bindings)
    allowed_modules={m for m,s in bindings};tree=ast.parse(plan['code']);aliases={}
    parents={child:node for node in ast.walk(tree)for child in ast.iter_child_nodes(node)}
    # A literal-created local record is allowed to collect observations. All
    # assignments to that name must remain literal dict creation, not an alias
    # of a module dictionary/imported object. This does not permit attr writes.
    assigned={}
    for node in ast.walk(tree):
        if isinstance(node,ast.Assign):
            for t in node.targets:
                if isinstance(t,ast.Name):assigned.setdefault(t.id,[]).append(node.value)
    local_records={name for name,values in assigned.items()if len(values)==1 and isinstance(values[0],ast.Dict)}
    for node in ast.walk(tree):
        if isinstance(node,ast.arg):local_records.discard(node.arg)
    for n in ast.walk(tree):
        assert not isinstance(n,(ast.While,ast.With,ast.AsyncWith,ast.AsyncFor,ast.Await,ast.Yield,ast.YieldFrom,ast.Global,ast.Nonlocal,ast.Delete)),type(n).__name__
        if isinstance(n,ast.Name):
            if n.id=='getattr':
                call=parents.get(n)
                assert isinstance(call,ast.Call)and call.func is n and not call.keywords and len(call.args)in(2,3),'only direct literal name getter'
                assert isinstance(call.args[1],ast.Constant)and call.args[1].value in SAFE_DUNDERS,'only literal readable metadata getter'
                if len(call.args)==3:assert isinstance(call.args[2],ast.Constant)and type(call.args[2].value)in(type(None),bool,int,float,str)
            else:assert n.id not in FORBIDDEN_NAMES,n.id
        if isinstance(n,ast.Attribute)and n.attr.startswith('__'):assert n.attr in SAFE_DUNDERS,n.attr
        if isinstance(n,ast.Import):
            for a in n.names:assert a.name in allowed_modules;aliases[a.asname or a.name]=a.name
        if isinstance(n,ast.ImportFrom):
            assert n.level==0 and n.module in allowed_modules
            for a in n.names:assert(n.module,a.name)in bindings
        if isinstance(n,ast.Attribute)and isinstance(n.value,ast.Name)and n.value.id in aliases:
            assert(aliases[n.value.id],n.attr)in bindings
        if isinstance(n,ast.Assign):
            def safe_target(t):
                if isinstance(t,ast.Name):return True
                if isinstance(t,(ast.Tuple,ast.List)):return all(safe_target(v)for v in t.elts)
                return isinstance(t,ast.Subscript)and isinstance(t.value,ast.Name)and t.value.id in local_records and isinstance(t.slice,ast.Constant)and isinstance(t.slice.value,str)and not t.slice.value.startswith('__')
            assert all(safe_target(t)for t in n.targets),'only literal local-record keys; no attribute/module-export mutation'
        if isinstance(n,(ast.AnnAssign,ast.AugAssign,ast.NamedExpr)):
            assert isinstance(n.target,ast.Name),'no alternate attribute/module-export mutation'
    assert any(isinstance(n,ast.Name)and isinstance(n.ctx,ast.Store)and n.id=='RESULT'for n in ast.walk(tree)),'probe must set RESULT'
    return tree

CHILD=r'''
import sys,json,hashlib,importlib,os,resource
spec=json.loads(sys.stdin.read())
resource.setrlimit(resource.RLIMIT_CPU,(spec['limits']['max_cpu_seconds'],spec['limits']['max_cpu_seconds']))
resource.setrlimit(resource.RLIMIT_NOFILE,(32,32))
memory_limit={'requested_bytes':spec['limits']['max_memory_bytes'],'enforced':False}
try:
    resource.setrlimit(resource.RLIMIT_AS,(spec['limits']['max_memory_bytes'],spec['limits']['max_memory_bytes']))
    memory_limit['enforced']=True
except (ValueError,OSError)as exc:memory_limit['unsupported_reason']=type(exc).__name__+': '+str(exc)
allowed={x['module']for x in spec['probe']['bindings']}
def module_record(module,symbol):
    m=importlib.import_module(module);o=m.__dict__[symbol];origin=m.__spec__.origin
    h=hashlib.sha256(open(origin,'rb').read()).hexdigest()if origin and os.path.isfile(origin)else None
    aliases=[]
    for name,other in list(sys.modules.items()):
        if getattr(getattr(other,'__spec__',None),'origin',None)!='built-in':continue
        for k,v in list(other.__dict__.items()):
            if not k.startswith('__')and v is o:aliases.append({'module':name,'symbol':k,'origin':'built-in'})
    return {'module':module,'symbol':symbol,'export_module':getattr(o,'__module__',None),'export_qualname':getattr(o,'__qualname__',None),'type_module':type(o).__module__,'type_qualname':type(o).__qualname__,'module_origin':origin,'module_source_sha256':h,'same_object_builtin_exports':aliases,'object_id':id(o)}
before=[module_record(x['module'],x['symbol'])for x in spec['probe']['bindings']]
def safe_import(name,globals=None,locals=None,fromlist=(),level=0):
    if level or name not in allowed:raise ImportError('probe import not allowed')
    return importlib.import_module(name)
names=['Exception','AttributeError','TypeError','ValueError','RuntimeError','ZeroDivisionError','object','type','str','int','float','bool','dict','list','tuple','set','len','hasattr','isinstance','issubclass','callable','repr','range','enumerate','zip','min','max','sum','abs','round','sorted','property','staticmethod','classmethod']
builtins=__import__('builtins');safe={n:getattr(builtins,n)for n in names};safe['__import__']=safe_import;safe['__build_class__']=builtins.__build_class__
def literal_metadata_getter(obj,name,*default):
    if name not in spec['literal_readable_attrs']or isinstance(obj,type(sys))or len(default)>1:raise ValueError('only literal non-module readable metadata access')
    if isinstance(obj,type)and name=='__dict__':raise ValueError('type namespace metadata is unavailable')
    return getattr(obj,name,*default)
safe['getattr']=literal_metadata_getter
scope={'__builtins__':safe,'__name__':'analysis_probe_fixture'}
error=None;exception_events=[]
def fixture_trace(frame,event,arg):
    if event=='exception'and frame.f_code.co_filename=='<analysis_probe_fixture>'and len(exception_events)<32:
        typ,val,_=arg
        exception_events.append({'function':frame.f_code.co_name,'line':frame.f_lineno,'exception_type':typ.__name__,'message':str(val),'origin':'analysis_probe_fixture'})
    return fixture_trace
try:
    sys.settrace(fixture_trace)
    exec(compile(spec['probe']['code'],'<analysis_probe_fixture>','exec'),scope,scope)
    value=scope.get('RESULT');json.dumps(value,allow_nan=False)
except BaseException as exc:error={'type':type(exc).__name__,'message':str(exc)};value=None
finally:sys.settrace(None)
after=[module_record(x['module'],x['symbol'])for x in spec['probe']['bindings']]
changed=any(a['object_id']!=b['object_id']for a,b in zip(before,after))
out={'result':value,'exception':error,'fixture_exception_events':exception_events,'trace_scope':{'filename':'<analysis_probe_fixture>','events':'exception_only','max_events':32,'locals_collected':False,'external_file_events_collected':False},'bindings_before':before,'bindings_after':after,'public_exports_rebound':changed,'environment':{'python_version':sys.version,'executable':sys.executable,'implementation':sys.implementation.name,'flags':{'isolated':sys.flags.isolated,'no_site':sys.flags.no_site},'deployed_runtime_confirmed':False},'limit_enforcement':{'cpu_seconds':spec['limits']['max_cpu_seconds'],'wall_seconds_parent':spec['limits']['max_wall_seconds'],'open_files':32,'memory':memory_limit},'fixture_origin':'analysis_probe_fixture'}
print(json.dumps(out,ensure_ascii=False,allow_nan=False))
'''

def execute(probe,questions,public_imports,out_dir,remaining_wall=None):
    validate_probe(probe,questions,public_imports);out_dir=Path(out_dir);out_dir.mkdir(parents=True,exist_ok=True)
    write_new(out_dir/'plan.json',probe);write_new(out_dir/'probe.py',probe['code'])
    command=[PINNED_PYTHON,'-I','-S','-c',CHILD];start=time.monotonic()
    effective_wall=min(PROBE_BUDGETS['max_wall_seconds'],remaining_wall)if remaining_wall is not None else PROBE_BUDGETS['max_wall_seconds']
    assert effective_wall>0,'active wall budget exhausted before probe'
    process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env={'PATH':'/usr/bin:/bin','PYTHONDONTWRITEBYTECODE':'1'},cwd=str(out_dir))
    timeout=False
    try:stdout,stderr=process.communicate(json.dumps(dict(probe=probe,limits={**PROBE_BUDGETS,'max_wall_seconds':effective_wall},literal_readable_attrs=sorted(SAFE_DUNDERS))).encode(),timeout=effective_wall)
    except subprocess.TimeoutExpired:timeout=True;process.kill();stdout,stderr=process.communicate()
    (out_dir/'stdout.bin').write_bytes(stdout);(out_dir/'stderr.bin').write_bytes(stderr)
    valid=not timeout and process.returncode==0 and len(stdout)<=PROBE_BUDGETS['max_output_bytes']and len(stderr)<=PROBE_BUDGETS['max_output_bytes'];parsed=None
    if valid:
        try:parsed=json.loads(stdout);valid=not parsed['exception']and not parsed['public_exports_rebound']
        except (ValueError,KeyError,TypeError):valid=False
    receipt=dict(status='observed'if valid else'failed',valid=valid,plan_sha256=digest(probe),code_sha256=sha(out_dir/'probe.py'),trusted_bootstrap_sha256=digest(CHILD),python_binary_sha256=sha(PINNED_PYTHON),command=[PINNED_PYTHON,'-I','-S','-c','<trusted bootstrap; source SHA recorded>'],timeout=timeout,returncode=process.returncode,duration_seconds=time.monotonic()-start,stdout_sha256=sha(out_dir/'stdout.bin'),stderr_sha256=sha(out_dir/'stderr.bin'),stdout_bytes=len(stdout),stderr_bytes=len(stderr),observation=parsed,scope='analysis_profile',deployed_runtime_confirmed=False,analysis_probe_origin=dict(kind='generated_analysis_probe_fixture',code_path=str((out_dir/'probe.py').resolve()),code_sha256=sha(out_dir/'probe.py'),start_line=1,end_line=len(probe['code'].splitlines()),repository_snapshot=None,repository_path=None),limits=PROBE_BUDGETS,model_calls=0)
    receipt['environment_sha256']=digest(parsed['environment'])if parsed else None
    receipt['effective_wall_seconds']=effective_wall
    receipt['runtime_bindings_sha256']=digest([parsed['bindings_before'],parsed['bindings_after']])if parsed else None
    receipt['question_sha256']=digest(questions)
    receipt['public_imports_sha256']=digest(public_imports)
    write_new(out_dir/'receipt.json',receipt);return receipt

def evidence(probe,receipt,receipt_path):
    assert receipt['valid']and receipt['status']=='observed'
    text=json.dumps(dict(observation=receipt['observation'],scope='analysis_profile',deployed_runtime_confirmed=False,plan_sha256=receipt['plan_sha256'],code_sha256=receipt['code_sha256'],receipt_reference=dict(path=str(Path(receipt_path).resolve()),sha256=sha(receipt_path))),ensure_ascii=False)
    return dict(id='R-'+digest(text)[:16],snapshot='HEAD',path='_analysis_probe/'+probe['id'],start_line=1,end_line=1,content=text,kind='runtime_observation',provenance='runtime_probe',mechanical_checks=dict(successful_scoped_execution=True,semantic_truth_guaranteed=False,source_location=False),binding=dict(scope='analysis_profile',deployed_runtime_confirmed=False,fixture_origin='analysis_probe_fixture',plan_sha256=receipt['plan_sha256'],receipt_sha256=sha(receipt_path)))
