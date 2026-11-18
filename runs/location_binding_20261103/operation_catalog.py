"""Source-only AST operation candidates; never consumes labels or responses."""
from __future__ import annotations
import ast
import hashlib
from pathlib import Path
import subprocess
import json
from common import ROOT,digest,sha
from location_visibility import eligible_head_rows,supplied_head_variants

VERSION='visible-head-ast-operations-single-anchor-v1'
MAX_SPAN_LINES=12
SIMPLE=(ast.Assign,ast.AugAssign,ast.AnnAssign,ast.Return,ast.Raise,ast.Delete,ast.Assert,
        ast.Call,ast.BinOp,ast.UnaryOp,ast.BoolOp,ast.Compare,ast.Subscript,ast.Await,ast.Yield,ast.YieldFrom)

def byte_lines(raw,*,python=False):
    starts=[0];lines=[];first=0;i=0
    while i<len(raw):
        if raw[i]==10 or (python and raw[i]==13):
            end=i
            if raw[i]==10 and end>first and raw[end-1]==13:end-=1
            lines.append(raw[first:end])
            i+=2 if python and raw[i]==13 and i+1<len(raw) and raw[i+1]==10 else 1
            first=i;starts.append(first)
        else:i+=1
    lines.append(raw[first:])
    return lines,starts

def python_file(path):
    path=Path(path);raw=path.read_bytes();text=raw.decode('utf-8');tree=ast.parse(text,filename=str(path))
    _,starts=byte_lines(raw,python=True)
    candidates=[]
    for node in ast.walk(tree):
        selected=[]
        if isinstance(node,SIMPLE):selected=[(type(node).__name__,node)]
        elif isinstance(node,(ast.If,ast.While,ast.IfExp)):selected=[(type(node).__name__+'.test',node.test)]
        elif isinstance(node,(ast.For,ast.AsyncFor)):selected=[(type(node).__name__+'.iter',node.iter)]
        elif isinstance(node,(ast.With,ast.AsyncWith)):selected=[(type(node).__name__+'.context',i.context_expr) for i in node.items]
        elif isinstance(node,ast.Expr) and isinstance(node.value,(ast.Call,ast.Await,ast.Yield,ast.YieldFrom)):
            selected=[('Expr',node)]
        for kind,part in selected:
            first,last=part.lineno,part.end_lineno
            if not (first and last and 1<=last-first+1<=MAX_SPAN_LINES):continue
            start=starts[first-1]+part.col_offset;end=starts[last-1]+part.end_col_offset
            code=raw[start:end].decode('utf-8')
            if not code.strip():continue
            candidates.append(dict(kind=kind,start_line=first,end_line=last,start_column=part.col_offset+1,
                end_column=part.end_col_offset+1,start_offset=start,end_offset=end,anchor_line=first,code=code))
    candidates.sort(key=lambda x:(x['start_offset'],x['end_offset'],x['kind']))
    return dict(path=str(path.resolve()),sha256=hashlib.sha256(raw).hexdigest(),operations=candidates,errors=[])

def go_files(paths):
    binary=ROOT/'go_operations/extractor'
    p=subprocess.run([str(binary)],input=json.dumps(dict(files=[str(Path(p).resolve()) for p in paths])),
        text=True,capture_output=True,timeout=30,check=True)
    value=json.loads(p.stdout)
    assert len(value['files'])==len(paths)
    return value['files']

def build_catalog(case,arm,parsed_files):
    visible=eligible_head_rows(case,arm);variants=supplied_head_variants(case,arm);ops=[];checks=[]
    for path in sorted(case['changed_files']):
        absolute=Path(case['workspace'])/path;record=parsed_files[str(absolute.resolve())]
        assert not record['errors'],record['errors']
        assert record['sha256']==sha(absolute)
        raw=absolute.read_bytes();actual,_=byte_lines(raw,python=case['language']=='Python')
        admitted=0;masked_rejections=0
        for op in record['operations']:
            start,end=op['start_line'],op['end_line']
            if not 1<=end-start+1<=MAX_SPAN_LINES:continue
            if not all((path,n) in visible for n in range(start,end+1)):continue
            assert raw[op['start_offset']:op['end_offset']].decode('utf-8')==op['code']
            # Only the exact token slice must be visible; masked text elsewhere
            # on a boundary line need not be exposed. Interior hidden comments
            # or strings make the whole parent node ineligible.
            token_visible=True
            for n in range(start,end+1):
                first=op['start_column']-1 if n==start else 0
                last=op['end_column']-1 if n==end else len(actual[n-1])
                expected=actual[n-1][first:last]
                if not any(len(t.encode('utf-8'))>=last and t.encode('utf-8')[first:last]==expected
                           for t in variants[path,n]):token_visible=False;break
            if not token_visible:masked_rejections+=1;continue
            anchor=op['anchor_line'];assert anchor==start and start<=anchor<=end
            # Real AST expressions/statements only. Comments, docstring constants,
            # function/class definitions and whole control-flow bodies are absent.
            if not visible[path,anchor].strip():continue
            item=dict(op,path=path,snapshot='HEAD',file_sha256=record['sha256'],backend='python_ast' if case['language']=='Python' else 'go_parser_ast')
            if not any((x['path'],x['start_offset'],x['end_offset'],x['kind'])==(path,item['start_offset'],item['end_offset'],item['kind']) for x in ops):
                ops.append(item);admitted+=1
        checks.append(dict(path=path,file_sha256=record['sha256'],parsed_operations=len(record['operations']),
                           admitted_operations=admitted,masked_token_span_rejections=masked_rejections))
    ops.sort(key=lambda x:(x['path'],x['start_offset'],x['end_offset'],x['kind']))
    for i,op in enumerate(ops,1):op['handle']=f'O{i:04d}'
    return dict(version=VERSION,operations=ops,checks=checks,eligible_HEAD_rows=len(visible),
                max_span_lines=MAX_SPAN_LINES,canonical_anchor_policy='start_line=end_line=actual AST start token line',
                labels_or_responses_consumed=False,content_identity_sha256=digest(ops))
