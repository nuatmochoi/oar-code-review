"""Evidence-anchored, advisory CPython semantic probes.

The model supplies a hypothesis and synthetic code, never an oracle. The parent
must pass *actually delivered* source lines, and charge every dispatched probe
as one logical query. No repository module is imported or executed. Unsupported
or inconclusive probes must not suppress findings.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import time

VERSION = 1
MAX_CODE_BYTES = 8000
MAX_AST_NODES = 800
MAX_OUTPUT_BYTES = 4096
MAX_SECONDS = 2.0
MAX_RSS_KIB = 256 * 1024
SAFE_METADATA = {'__name__', '__qualname__', '__module__', '__doc__'}
SAFE_DEFINITIONS = SAFE_METADATA | {'__init__', '__new__', '__call__', '__iter__',
    '__next__', '__len__', '__getitem__', '__bool__', '__str__', '__repr__', '__eq__'}
FORBIDDEN_NAMES = {'open', 'input', 'eval', 'exec', '__import__', 'globals', 'locals',
    'vars', 'dir', 'help', 'breakpoint', 'exit', 'quit', 'memoryview', 'super'}
MODULE_EXPORTS = {
    'functools': ['partial', 'partialmethod', 'wraps', 'update_wrapper', 'reduce',
                  'cmp_to_key', 'cache', 'lru_cache'],
    'operator': ['add', 'sub', 'mul', 'truediv', 'floordiv', 'mod', 'pow', 'eq', 'ne',
                 'lt', 'le', 'gt', 'ge', 'not_', 'truth', 'is_', 'is_not', 'index',
                 'getitem', 'contains', 'countOf', 'indexOf', 'concat'],
    'itertools': ['chain', 'islice', 'repeat', 'product', 'permutations', 'combinations',
                  'zip_longest', 'accumulate', 'takewhile', 'dropwhile', 'groupby'],
    'collections': ['Counter', 'defaultdict', 'deque', 'OrderedDict'],
    'math': ['sqrt', 'isfinite', 'isinf', 'isnan', 'floor', 'ceil', 'trunc', 'copysign',
             'factorial', 'gcd', 'lcm', 'fsum', 'prod', 'log', 'log10', 'exp', 'pi',
             'e', 'inf', 'nan', 'isclose'],
    'decimal': ['Decimal', 'Context', 'localcontext', 'getcontext', 'InvalidOperation',
                'DivisionByZero', 'ROUND_HALF_EVEN', 'ROUND_HALF_UP', 'ROUND_DOWN'],
    'fractions': ['Fraction'],
    'copy': ['copy', 'deepcopy'],
    'types': ['SimpleNamespace'],
    're': ['compile', 'match', 'fullmatch', 'search', 'findall', 'finditer', 'sub',
           'split', 'escape', 'IGNORECASE', 'MULTILINE', 'DOTALL'],
    # AST constructors are filled from the *executor's* installed stdlib.
    'ast': ['parse', 'dump', 'fix_missing_locations', 'copy_location', 'walk',
            'iter_fields', 'iter_child_nodes', 'increment_lineno', 'get_docstring'],
}
MODULE_EXPORTS['ast'] += sorted(n for n, v in vars(ast).items()
    if isinstance(v, type) and issubclass(v, ast.AST))
SAFE_BUILTINS = ['abs', 'all', 'any', 'bool', 'bytes', 'callable', 'chr', 'complex',
    'dict', 'divmod', 'enumerate', 'filter', 'float', 'format', 'frozenset', 'hash',
    'hex', 'id', 'int', 'isinstance', 'issubclass', 'iter', 'len', 'list', 'map',
    'max', 'min', 'next', 'object', 'ord', 'pow', 'print', 'range', 'repr', 'reversed',
    'round', 'set', 'slice', 'sorted', 'str', 'sum', 'tuple', 'type', 'zip',
    'Exception', 'AssertionError', 'AttributeError', 'TypeError', 'ValueError',
    'KeyError', 'IndexError', 'RuntimeError', 'StopIteration', 'ZeroDivisionError',
    'OverflowError', 'MemoryError', 'NotImplementedError', 'property', 'staticmethod',
    'classmethod']


def _sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


def probe_schema():
    """A proposal field schema; expected_stdout is the model's hypothesis only."""
    return {'type': 'object', 'additionalProperties': False,
        'required': ['id', 'claim_id', 'code', 'expected_stdout', 'required_python', 'source_refs'],
        'properties': {
            'id': {'type': 'string', 'maxLength': 80},
            'claim_id': {'type': 'string', 'maxLength': 80},
            'code': {'type': 'string', 'maxLength': MAX_CODE_BYTES},
            'expected_stdout': {'type': 'string', 'maxLength': MAX_OUTPUT_BYTES},
            'required_python': {'type': 'string', 'description': 'CPython major.minor or major.minor.patch; must match the authoritative declared runtime.'},
            'source_refs': {'type': 'array', 'minItems': 1, 'maxItems': 4, 'items': {
                'type': 'object', 'additionalProperties': False,
                'required': ['evidence_id', 'snapshot', 'start', 'end'],
                'properties': {'evidence_id': {'type': 'string'},
                    'snapshot': {'type': 'string', 'enum': ['head', 'base']},
                    'start': {'type': 'integer', 'minimum': 1},
                    'end': {'type': 'integer', 'minimum': 1}}}}}}


def _source_map(supplied_evidence):
    """Accept only explicit delivered-source records, not raw registry evidence.

    Each row: evidence_id, file/path, snapshot, lines=[[number, exact text]],
    delivered=True. Metadata and diff are not standalone source bodies.
    """
    out = {}
    values = supplied_evidence.values() if isinstance(supplied_evidence, dict) else supplied_evidence
    for e in values or []:
        if not isinstance(e, dict) or e.get('delivered') is not True:
            continue
        if e.get('content_kind', 'source_lines') != 'source_lines':
            continue
        eid = e.get('evidence_id', e.get('id'))
        if not eid or eid.startswith('diff:') or e.get('snapshot') not in ('head', 'base'):
            continue
        lines = e.get('lines', [])
        if not all(isinstance(x, (list, tuple)) and len(x) == 2 and type(x[0]) is int
                   and x[0] >= 1 and isinstance(x[1], str) and '\n' not in x[1] for x in lines):
            continue
        key = (eid, e['snapshot'])
        old = out.get(key)
        current = {'evidence_id': eid, 'file': e.get('file', e.get('path')),
                   'snapshot': e['snapshot'], 'lines': dict(lines)}
        if old and (old['file'] != current['file'] or
                    any(n in old['lines'] and old['lines'][n] != t for n, t in lines)):
            raise ValueError('conflicting delivered source identity')
        if old:
            old['lines'].update(current['lines'])
        else:
            out[key] = current
    return out


def extract_claims(payload, supplied_evidence):
    sources = _source_map(supplied_evidence)
    available = {eid for eid, _ in sources}
    rows = [('finding', x) for x in payload.get('findings', []) if isinstance(x, dict)]
    # Safe conclusions receive the same causal scrutiny as reported defects.
    rows += [('observation', x) for x in payload.get('review', {}).get('observations', [])
             if isinstance(x, dict)]
    claims, seen = [], set()
    for kind, row in rows:
        evidence_ids = sorted({x.get('evidence_id') for x in row.get('evidence', [])
                               if isinstance(x, dict) and x.get('evidence_id') in available})
        fields = {name: str(row.get(name, ''))[:2400] for name in ('title', 'trigger', 'mechanism', 'impact')}
        if kind == 'observation':
            fields['mechanism'] = str(row.get('body', ''))[:2400]
        signature = json.dumps(fields, sort_keys=True, ensure_ascii=False)
        if signature in seen:
            continue
        seen.add(signature)
        source_text = '\n'.join(t for (eid, _), e in sources.items() if eid in evidence_ids
                                for t in e['lines'].values())
        identifiers = set(re.findall(r'[A-Za-z_]\w*', signature)) & set(re.findall(r'[A-Za-z_]\w*', source_text))
        identifiers -= {'self', 'cls', 'return', 'if', 'else', 'True', 'False', 'None', 'def', 'class'}
        claims.append({'id': 'claim-' + _sha(signature)[:16], 'kind': kind, **fields,
            'evidence_ids': evidence_ids, 'anchored_identifiers': sorted(identifiers),
            'source_backed': bool(evidence_ids),
            'verification_questions': [
                'Which exact operation fails or establishes the asserted behavior?',
                'Does the supplied source connect the trigger, operation, and consumer?',
                'What concrete counterexample would falsify this causal explanation?']})
        if len(claims) >= 8:
            break
    return claims


def build_verification(payload, supplied_evidence):
    claims = extract_claims(payload, supplied_evidence)
    return {'claims': claims, 'prompt': (
        'Verify the causal explanation of findings and safe conclusions symmetrically. '
        'Separate a missing source connection from a false operation-level claim. '
        'Identify trigger, exact failure operation, consumer and a falsifying example. '
        'Request necessary actual source bodies within the existing query budget. '
        'A probe may test only a small stdlib/CPython semantic hypothesis anchored in '
        'actually delivered source. It is synthetic and does not execute the repository. '
        'Unsupported, missing, timed-out or inconclusive evidence is not a finding-removal gate. '
        'Do not invent source, bindings, runtime versions, probe output or execution success.'),
        'probe_schema': probe_schema(), 'source_output_gate': False}


def _validate_code(code):
    errors = []
    if not isinstance(code, str) or len(code.encode()) > MAX_CODE_BYTES:
        return ['code_size_or_type'], None
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError):
        return ['invalid_python_syntax'], None
    nodes = list(ast.walk(tree))
    if len(nodes) > MAX_AST_NODES:
        errors.append('ast_node_limit')
    for n in nodes:
        if isinstance(n, ast.Constant) and ((type(n.value) is int and abs(n.value)>1000000) or
                isinstance(n.value,(str,bytes)) and len(n.value)>2048):
            errors.append('literal_allocation_limit')
        if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Pow) and isinstance(n.right, ast.Constant) and (
                not isinstance(n.right.value,(int,float)) or abs(n.right.value)>128):
            errors.append('literal_exponent_limit')
        if isinstance(n, (ast.Global, ast.Nonlocal, ast.AsyncFunctionDef, ast.Await)):
            errors.append('unsupported_language_construct')
        if isinstance(n, ast.Name) and (n.id in FORBIDDEN_NAMES or n.id.startswith('_')):
            errors.append('forbidden_name:' + n.id)
        if isinstance(n, ast.Attribute) and n.attr.startswith('_') and n.attr not in SAFE_METADATA:
            errors.append('forbidden_attribute:' + n.attr)
        if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name.startswith('_') and n.name not in SAFE_DEFINITIONS:
            errors.append('forbidden_definition:' + n.name)
        if isinstance(n, ast.Import):
            for x in n.names:
                if x.name not in MODULE_EXPORTS or (x.asname or x.name).startswith('_'):
                    errors.append('unsupported_import:' + x.name)
        if isinstance(n, ast.ImportFrom):
            if n.level or n.module not in MODULE_EXPORTS:
                errors.append('unsupported_import:' + str(n.module))
            else:
                for x in n.names:
                    if x.name not in MODULE_EXPORTS[n.module] or (x.asname or x.name).startswith('_'):
                        errors.append('unsupported_import_member:' + x.name)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == 'compile':
            # Runtime wrapper additionally insists the input is an ast.AST.
            if len(n.args) != 3 or n.keywords:
                errors.append('compile_requires_three_positional_arguments')
    return sorted(set(errors)), tree


def _version(value):
    if not isinstance(value, str):
        return None
    m = re.fullmatch(r'(?:CPython\s+)?(\d+)\.(\d+)(?:\.(\d+))?', value.strip())
    return tuple(int(x) for x in m.groups() if x is not None) if m else None


def validate_probe(proposal, claims, supplied_evidence, declared_runtime=None):
    """Fail closed on absent authoritative runtime or unsupplied provenance."""
    errors, provenance = [], []
    if not isinstance(proposal, dict) or set(proposal) != set(probe_schema()['required']):
        return {'allowed': False, 'reasons': ['invalid_proposal_fields'], 'proposal': None,
                'source_provenance': [], 'source_output_gate': False}
    for field, limit in [('id', 80), ('claim_id', 80), ('expected_stdout', MAX_OUTPUT_BYTES)]:
        if not isinstance(proposal[field], str) or len(proposal[field].encode()) > limit:
            errors.append('invalid_' + field)
    code_errors, tree = _validate_code(proposal.get('code'))
    errors += code_errors
    claim = next((c for c in claims if c.get('id') == proposal['claim_id']), None)
    if not claim:
        errors.append('unknown_claim')
    declared, requested = _version(declared_runtime), _version(proposal.get('required_python'))
    if declared is None:
        errors.append('authoritative_runtime_unknown')
    if requested is None or declared is not None and requested[:len(declared)] != declared[:len(requested)]:
        errors.append('declared_runtime_mismatch')
    sources = _source_map(supplied_evidence)
    refs = proposal.get('source_refs')
    if not isinstance(refs, list) or not 1 <= len(refs) <= 4:
        errors.append('source_refs_required')
        refs = []
    for ref in refs:
        if not isinstance(ref, dict) or set(ref) != {'evidence_id', 'snapshot', 'start', 'end'}:
            errors.append('invalid_source_ref'); continue
        if not isinstance(ref['evidence_id'], str) or ref['snapshot'] not in ('head', 'base'):
            errors.append('invalid_source_ref'); continue
        e = sources.get((ref['evidence_id'], ref['snapshot']))
        if e is None or claim and ref['evidence_id'] not in claim['evidence_ids']:
            errors.append('source_not_delivered_or_not_claim_anchor'); continue
        start, end = ref['start'], ref['end']
        if type(start) is not int or type(end) is not int or not 1 <= start <= end or end - start >= 200:
            errors.append('invalid_source_range'); continue
        if not all(n in e['lines'] for n in range(start, end + 1)):
            errors.append('source_range_not_fully_delivered'); continue
        text = '\n'.join(f'{n}: {e["lines"][n]}' for n in range(start, end + 1))
        provenance.append({**ref, 'file': e['file'], 'source_sha256': _sha(text),
                           'numbered_source': text})
    if tree is not None and claim:
        tokens = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        tokens |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        tokens |= {a.name for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names}
        if not tokens & set(claim['anchored_identifiers']):
            errors.append('probe_has_no_source_anchored_operation')
    return {'allowed': not errors, 'reasons': sorted(set(errors)), 'proposal': proposal.copy(),
            'source_provenance': provenance, 'declared_runtime': declared_runtime,
            'code_sha256': _sha(proposal['code']) if isinstance(proposal.get('code'), str) else None,
            'scope': 'synthetic_cpython_stdlib_semantics_only', 'source_output_gate': False,
            'version': VERSION}


def sandbox_profile(python_executable):
    """macOS Seatbelt: no writes/network/fork; no user-volume source access.

    OS runtime bootstrap requires broad system read permission on this host.
    The language/module/audit restrictions additionally deny all probe file I/O.
    This is not a general-purpose arbitrary-code or repository execution API.
    """
    py = Path(python_executable).resolve()
    prefix = py.parent.parent
    return ('(version 1)(deny default)'
            '(allow process-exec (subpath ' + json.dumps(str(prefix)) + '))'
            '(allow file-read*)(deny file-read* (subpath "/Users") (subpath "/Volumes"))'
            '(allow sysctl-read)')


_HARNESS = r'''
import ast, builtins, contextlib, hashlib, importlib, io, json, resource, sys, types
payload = json.loads(sys.stdin.read())
modules = {}
module_hashes = {}
for name, exports in payload['exports'].items():
    module = importlib.import_module(name)
    modules[name] = types.SimpleNamespace(**{n:getattr(module,n) for n in exports if hasattr(module,n)})
    filename=getattr(module,'__file__',None)
    if filename:
        with open(filename,'rb') as stream: digest=hashlib.sha256(stream.read()).hexdigest()
        module_hashes[name]={'path':filename,'sha256':digest}
actual = {'implementation':sys.implementation.name,'version':list(sys.version_info[:3]),
          'executable':sys.executable,'isolated':sys.flags.isolated,'no_site':sys.flags.no_site,
          'stdlib_module_source_sha256':module_hashes}
if actual['implementation'] != 'cpython' or any(actual['version'][:len(v)] != v for v in payload['versions']):
    sys.stdout.write(json.dumps({'runtime':actual,'status':'runtime_mismatch','stdout':'','stderr':'',
        'exception_type':None,'exception_message':None,'probe_code_executed':False}))
    sys.exit(0)
resource.setrlimit(resource.RLIMIT_CPU,(1,1))
resource.setrlimit(resource.RLIMIT_FSIZE,(0,0))
resource.setrlimit(resource.RLIMIT_NOFILE,(32,32))
resource.setrlimit(resource.RLIMIT_NPROC,(0,0))
# macOS rejects lowering RLIMIT_DATA/AS/RSS in this environment. The parent
# samples RSS and kills on excess; this is not a strict instantaneous OS cap.
code = compile(payload['code'],'<semantic-probe>','exec')
class OutputLimit(Exception): pass
class Capture(io.StringIO):
    def write(self,s):
        if len((self.getvalue()+s).encode()) > payload['output_limit']: raise OutputLimit('output limit')
        return super().write(s)
def safe_import(name, globals=None, locals=None, fromlist=(), level=0):
    if level or name not in modules: raise ImportError('stdlib module not allowed')
    return modules[name]
def safe_getattr(value,name,*default):
    if not isinstance(name,str) or (name.startswith('_') and name not in payload['safe_metadata']):
        raise AttributeError('private/introspection attribute forbidden')
    return getattr(value,name,*default)
def safe_hasattr(value,name):
    try: safe_getattr(value,name); return True
    except AttributeError: return False
def safe_compile(tree,filename,mode):
    if not isinstance(tree,ast.AST) or not isinstance(filename,str) or mode not in ('exec','eval','single'):
        raise TypeError('compile accepts AST validation only')
    compile(tree,'<constructed-ast>',mode)
    # Never return executable bytecode to the untrusted namespace.
    return True
allowed = {n:getattr(builtins,n) for n in payload['builtins']}
allowed.update(__import__=safe_import,__build_class__=builtins.__build_class__,
               getattr=safe_getattr,hasattr=safe_hasattr,compile=safe_compile)
namespace = {'__builtins__':allowed,'__name__':'semantic_probe'}
execution_events = 0
def audit(event,args):
    global execution_events
    if event == 'exec':
        execution_events += 1
        if execution_events > 1: raise RuntimeError('dynamic bytecode execution forbidden')
    if event == 'open' or event.startswith(('os.','socket.','subprocess.','ctypes.')):
        raise RuntimeError('probe I/O/process operation forbidden')
sys.addaudithook(audit)
out,err=Capture(),Capture()
result={'runtime':actual,'status':'completed','exception_type':None,'exception_message':None,
        'probe_code_executed':True}
try:
    with contextlib.redirect_stdout(out),contextlib.redirect_stderr(err):
        exec(code,namespace,namespace)
except BaseException as exc:
    result['status']='output_limit' if isinstance(exc,OutputLimit) else 'exception'
    result['exception_type']=type(exc).__name__
    result['exception_message']=str(exc)[:500]
result['stdout'],result['stderr']=out.getvalue(),err.getvalue()
sys.__stdout__.write(json.dumps(result))
'''


def execute_probe(validated, *, python_executable, timeout=MAX_SECONDS, max_output_bytes=MAX_OUTPUT_BYTES):
    """Execute only a validated proposal; caller accounts one query per dispatch.

    The returned status describes a semantic experiment, never a finding verdict.
    """
    base = {'version': VERSION, 'scope': 'synthetic_cpython_stdlib_semantics_only',
            'source_output_gate': False, 'source_provenance': validated.get('source_provenance', []),
            'code_sha256': validated.get('code_sha256'),
            'cost': {'logical_queries': 0, 'native_rpc_queries': 0, 'source_tool_reads': 0,
                     'process_starts': 0}, 'stdout': '', 'stderr': '',
            'budget_note': 'Caller charges a proposed probe attempt once under the shared query policy; logical_queries here counts dispatched execution only.'}
    if not validated.get('allowed'):
        return {**base, 'status': 'unsupported', 'reasons': validated.get('reasons', [])}
    proposal = validated['proposal']
    if _sha(proposal['code']) != validated['code_sha256'] or _validate_code(proposal['code'])[0]:
        return {**base, 'status': 'unsupported', 'reasons': ['proposal_changed_after_validation']}
    if not 0 < timeout <= MAX_SECONDS or not 1 <= max_output_bytes <= MAX_OUTPUT_BYTES:
        raise ValueError('probe resource limit cannot exceed frozen maxima')
    py = Path(python_executable).resolve()
    sandbox = Path('/usr/bin/sandbox-exec')
    if not sandbox.is_file() or not py.is_file():
        return {**base, 'status': 'unsupported', 'reasons': ['sandbox_or_python_unavailable']}
    data = {'code': proposal['code'], 'exports': MODULE_EXPORTS, 'builtins': SAFE_BUILTINS,
            'safe_metadata': sorted(SAFE_METADATA), 'output_limit': max_output_bytes,
            'versions': [list(_version(validated['declared_runtime'])), list(_version(proposal['required_python']))]}
    profile=sandbox_profile(py)
    args = [str(sandbox), '-p', profile, str(py), '-I', '-S', '-B', '-u', '-c', _HARNESS]
    start = time.monotonic()
    try:
        p = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd='/', env={'PATH': '/usr/bin:/bin', 'PYTHONDONTWRITEBYTECODE': '1'}, start_new_session=True)
    except OSError as exc:
        return {**base,'status':'execution_unavailable','reasons':[type(exc).__name__]}
    base['cost'].update(logical_queries=1, process_starts=1)
    base.update(child_pid=p.pid,process_group=p.pid,sandbox_profile_sha256=_sha(profile))
    buffers = {'stdout': bytearray(), 'stderr': bytearray()}
    status, limit = None, max_output_bytes * 3 + 4096
    peak_rss, samples, last_sample = 0, 0, 0.0
    selector = selectors.DefaultSelector()
    try:
        p.stdin.write(json.dumps(data).encode()); p.stdin.close()
        for name, stream in [('stdout',p.stdout),('stderr',p.stderr)]:
            os.set_blocking(stream.fileno(),False); selector.register(stream,selectors.EVENT_READ,name)
        while selector.get_map():
            if time.monotonic()-start >= timeout:
                status='timeout'; break
            if time.monotonic()-last_sample >= .05 and p.poll() is None:
                last_sample=time.monotonic()
                try:
                    measurement=subprocess.run(['/bin/ps','-o','rss=','-p',str(p.pid)],
                        capture_output=True,text=True,timeout=.15,env={'PATH':'/usr/bin:/bin'})
                    rss=int(measurement.stdout.strip());peak_rss=max(peak_rss,rss);samples+=1
                    if rss>MAX_RSS_KIB:status='memory_limit';break
                except (ValueError,subprocess.TimeoutExpired):
                    pass
            for key,_ in selector.select(min(.05,max(0,timeout-(time.monotonic()-start)))):
                chunk=os.read(key.fileobj.fileno(),8192)
                if not chunk: selector.unregister(key.fileobj); continue
                buffers[key.data].extend(chunk)
                if sum(len(v) for v in buffers.values()) > limit:
                    status='output_limit'; break
            if status: break
    except (BrokenPipeError,OSError) as exc:
        status='execution_failure'
        buffers['stderr'].extend(type(exc).__name__.encode())
    finally:
        selector.close()
        if p.poll() is None or status:
            try: os.killpg(p.pid,signal.SIGKILL)
            except ProcessLookupError: pass
        p.wait(timeout=1)
        p.stdout.close(); p.stderr.close()
    base.update(duration_seconds=time.monotonic()-start, returncode=p.returncode,
                sandbox='macos_seatbelt', cleanup='process_group_killed_or_reaped',
                declared_runtime=validated['declared_runtime'],
                memory_control={'kind':'sampled_rss_kill','threshold_kib':MAX_RSS_KIB,
                    'sample_interval_seconds':.05,'observed_peak_kib':peak_rss,'samples':samples,
                    'strict_os_memory_cap':False,'note':'Transient between-sample overshoot is possible.'})
    if status:
        return {**base,'status':status,'stderr':buffers['stderr'][:max_output_bytes].decode(errors='replace')}
    try:
        result=json.loads(buffers['stdout'])
    except (ValueError,UnicodeError):
        return {**base,'status':'execution_failure','stderr':buffers['stderr'][:max_output_bytes].decode(errors='replace')}
    actual=tuple(result['runtime']['version'])
    declared=_version(validated['declared_runtime']); requested=_version(proposal['required_python'])
    if result['runtime']['implementation']!='cpython' or actual[:len(declared)]!=declared or actual[:len(requested)]!=requested:
        return {**base,'status':'runtime_mismatch','actual_python':result['runtime'],
                'reasons':['actual_executor_does_not_match_declared_runtime'],
                'probe_code_executed':False}
    return {**base,**result,'actual_python':result['runtime'],
            'model_hypothesis_expected_stdout':proposal['expected_stdout'],
            'model_hypothesis_output_matched':result['status']=='completed' and result['stdout']==proposal['expected_stdout'],
            'interpretation':'Output tests only the synthetic operation, not repository binding or defect validity.'}
