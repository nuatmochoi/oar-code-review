"""BASE/HEAD syntactic behavior changes without inferring execution semantics."""
import ast,collections,time
from pathlib import Path
from common import sha,digest,write_new

def python_operations(path):
    text=Path(path).read_text();tree=ast.parse(text)
    out=[]
    for node in ast.walk(tree):
        if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)):
            value=[node.name,ast.dump(node.args,include_attributes=False),[ast.dump(x,include_attributes=False) for x in node.decorator_list],ast.dump(node.returns,include_attributes=False) if node.returns else None]
            out.append(dict(kind='FunctionSignature',line=node.lineno,end_line=node.lineno,structural_sha256=digest(value)))
        elif isinstance(node,ast.ClassDef):
            value=[node.name,[ast.dump(x,include_attributes=False) for x in node.bases],[ast.dump(x,include_attributes=False) for x in node.keywords],[ast.dump(x,include_attributes=False) for x in node.decorator_list]]
            out.append(dict(kind='ClassMetadata',line=node.lineno,end_line=node.lineno,structural_sha256=digest(value)))
        part=None
        if isinstance(node,(ast.Call,ast.Assign,ast.AnnAssign,ast.AugAssign,ast.Return,ast.Raise)):part=node
        elif isinstance(node,(ast.If,ast.While,ast.IfExp)):part=node.test
        if part is not None:
            value=ast.dump(part,include_attributes=False)
            out.append(dict(kind=type(node).__name__,line=part.lineno,end_line=part.end_lineno,structural_sha256=digest(value)))
    return dict(operations=out,source_sha256=sha(path),backend='python_ast',errors=[],
        structural_signature=digest([(x['kind'],x['structural_sha256']) for x in out]))

def build_delta(case):
    began=time.monotonic();out=[];cost=dict(ast_invocations=0,file_reads=0);errors=[]
    for rel in case['changed_files']:
        sides={}
        for snap,config in case['snapshots'].items():
            if rel not in config['files']:
                sides[snap]=dict(operations=[],unresolved='file_absent_in_snapshot');continue
            p=Path(config['root'])/rel
            assert sha(p)==config['files'][rel]
            try:
                if case['language']=='Python':r=python_operations(p)
                else:
                    from go_tools import parse_file
                    value=parse_file(p,config['files'][rel])
                    # Keep native AST record, not inferred semantic equivalence.
                    r=value if isinstance(value,dict) else value[0]
                sides[snap]=r;cost['ast_invocations']+=1;cost['file_reads']+=1
            except Exception as exc:
                sides[snap]=dict(unresolved=f'{type(exc).__name__}: {exc}');errors.append(dict(snapshot=snap,path=rel,error=str(exc)))
        signatures={s:r.get('structural_signature') for s,r in sides.items()}
        out.append(dict(path=rel,snapshots=sides,structurally_equal=(signatures.get('BASE')==signatures.get('HEAD')) if all(signatures.get(s) for s in ('BASE','HEAD')) else None,
            unchanged_symbol_relation_does_not_imply_unchanged_behavior=True))
    cost['seconds']=time.monotonic()-began
    return dict(case_id=case['id'],files=out,errors=errors,cost=cost,
        semantics='AST syntax only, not executed call/dataflow; changes in arguments/assignments/guards remain visible even if symbols persist.')

def summary(value):
    # Never dump entire unmasked AST/source into model prompts.
    rows=[]
    for f in value['files']:
        v=dict(path=f['path'],structurally_equal=f['structurally_equal'],snapshots={})
        for s,r in f['snapshots'].items():
            ops=r.get('operations',[])
            units=r.get('units',[])
            v['snapshots'][s]=dict(operation_kinds=dict(collections.Counter(x['kind'] for x in ops)),
                unit_kinds=dict(collections.Counter(x['kind'] for x in units)),
                calls=sum(len(x.get('calls',[])) for x in units),conditions=sum(len(x.get('conditions',[])) for x in units),
                structural_digest=digest(r['structural_signature']) if isinstance(r.get('structural_signature'),list) else r.get('structural_signature'),unresolved=r.get('unresolved'),errors=r.get('errors',[]))
        rows.append(v)
    return dict(files=rows,semantics=value['semantics'])
