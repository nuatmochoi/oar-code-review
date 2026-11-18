"""HEAD code actually supplied, intersected with the shared changed AST region."""
from pathlib import PurePosixPath
import json
from common import source_rows
from diff_coordinates import parse_diff

def relative_path(raw):
    if raw is None or raw=='/dev/null':return None
    if raw.startswith('"'):
        # Ordinary quoted UTF8 paths are supported. Unsupported C/octal escapes
        # fail explicitly instead of indexing a different, invented path.
        raw=json.loads(raw)
        if not isinstance(raw,str):raise ValueError('invalid quoted diff path')
        if raw=='/dev/null':return None
    path=raw[2:] if raw.startswith(('a/','b/')) else raw
    p=PurePosixPath(path)
    if p.is_absolute() or '..' in p.parts:raise ValueError('unsafe diff path')
    return str(p)

def diff_head_rows(diff):
    out={}
    for h in parse_diff(diff):
        path=relative_path(h['new_path'])
        if path is None:continue
        for row in h['rows']:
            n=row['new_line']
            if n is None:continue
            code=row['code'].removesuffix('\n').removesuffix('\r')
            key=path,n
            if key in out and out[key]!=code:raise ValueError('conflicting diff row')
            out[key]=code
    return out

def merge_rows(target,extra):
    for key,value in extra.items():
        if key in target and target[key]!=value:raise ValueError('conflicting actual HEAD source')
        target[key]=value

def supplied_head_variants(case,arm):
    # Masked source and unmasked DIFF are both literal supplied representations.
    # A blank placeholder is not evidence that the original HEAD line is blank.
    rows={}
    for (snap,path,n),text in source_rows(case['arms'][arm]).items():
        if snap=='HEAD':rows.setdefault((path,n),[]).append(text)
    for key,text in diff_head_rows(case['diff']).items():
        if text not in rows.setdefault(key,[]):rows[key].append(text)
    return rows

def supplied_head_rows(case,arm):
    return {key:max(texts,key=lambda t:(bool(t.strip()),len(t))) for key,texts in supplied_head_variants(case,arm).items()}

def eligible_head_rows(case,arm):
    protected={(path,n) for (snap,path,n) in source_rows(case['protected_ast'])
               if snap=='HEAD' and path in case['changed_files']}
    protected.update(diff_head_rows(case['diff']))
    supplied=supplied_head_rows(case,arm)
    return {key:text for key,text in supplied.items() if key in protected and key[0] in case['changed_files']}
