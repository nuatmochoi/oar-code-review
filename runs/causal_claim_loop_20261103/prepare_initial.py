"""Annotate completeness of already supplied initial source, without gold.

This opens no new model context. A header alone must not inherit the full-body
carrier property; exact snapshot AST spans mark only actually supplied bodies.
"""
import ast,time
from pathlib import Path
from common import ROOT,load,sha,digest,write_new

def block_key(b):return digest({k:b[k] for k in ('snapshot','path','start_line','end_line','content')})
def annotate(cases,receipt_name='initial_body_annotations.json',annotation_dir='initial_body_annotations_final'):
    cache={};began=time.monotonic();reads=parses=0;results=[]
    for c in cases:
        annotations={};errors=[]
        for b in c['initial_blocks']:
            spec=c['snapshots'][b['snapshot']];rel=b['path'];meta=spec.get('external_files',{}).get(rel)
            if meta:p=Path(meta['absolute_source']);wanted=meta['sha256']
            elif rel in spec['files']:p=Path(spec['root'])/rel;wanted=spec['files'][rel]
            else:
                annotations[block_key(b)]=dict(complete_unit=False,reason='source_not_in_snapshot_manifest');continue
            try:
                key=(c['language'],str(p),wanted)
                if key not in cache:
                    assert sha(p)==wanted;reads+=1;parses+=1
                    if c['language']=='Python':
                        tree=ast.parse(p.read_bytes().decode('utf-8'))
                        units=[dict(name=n.name,start_line=n.lineno,end_line=n.end_lineno,kind=type(n).__name__) for n in ast.walk(tree) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef))]
                    else:
                        from go_tools import parse_file
                        units=parse_file(p,wanted)['units']
                    cache[key]=units
                contained=[{k:u[k] for k in ('name','start_line','end_line','kind')} for u in cache[key] if b['start_line']<=u['start_line'] and u['end_line']<=b['end_line']]
                annotations[block_key(b)]=dict(complete_unit=bool(contained),complete_units=contained,
                    authoritative_file_sha256=wanted,range_containment_checked=True,semantic_callee_binding_proved=False,
                    note='Full AST body inside actual supplied range; not runtime/causal truth or uniqueness of the called implementation.')
            except Exception as exc:
                errors.append(dict(path=rel,snapshot=b['snapshot'],error=f'{type(exc).__name__}: {exc}'))
                annotations[block_key(b)]=dict(complete_unit=False,reason='AST_completeness_unresolved')
        dest=ROOT/'prepared'/annotation_dir/f'{c["id"]}.json';write_new(dest,annotations)
        results.append(dict(case_id=c['id'],blocks=len(annotations),complete_blocks=sum(x['complete_unit'] for x in annotations.values()),errors=errors))
    receipt=dict(cases=len(cases),results=results,cost=dict(file_reads=reads,ast_parses=parses,seconds=time.monotonic()-began),model_calls=0,added_model_source_lines=0)
    write_new(ROOT/'prepared'/receipt_name,receipt);return receipt
if __name__=='__main__':print(annotate(load(ROOT/'prepared/cases.json')))
