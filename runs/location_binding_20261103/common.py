"""Shared artifacts, prompt/schema and exact source accounting; no gold imports."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import oar_core

ROOT = Path(__file__).resolve().parent
SOURCE_CONTEXTS = ('diff','targeted')
FORMATS = ('free','numbered','line','operation')
ARMS = tuple(f'{source}_{form}' for source in SOURCE_CONTEXTS for form in FORMATS)
CONTROLLED = frozenset(a for a in ARMS if a.endswith(('_line','_operation')))
MODEL = 'gpt-5.6-sol'
REPEATS = 3

def load(path):
    return json.loads(Path(path).read_text())

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()

def write_new(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as f:
        if isinstance(value, str): f.write(value)
        else: json.dump(value, f, ensure_ascii=False, indent=2); f.write('\n')

def source_lines(text):
    """Physical LF/CRLF source rows; Unicode separators/FF are code, not rows."""
    if not text:return []
    rows=text.split('\n')
    if rows[-1]=='':rows.pop()
    return [row.removesuffix('\r') for row in rows]

def source_rows(blocks):
    rows = {}
    for b in blocks:
        assert b['snapshot'] in ('HEAD', 'BASE'), b
        assert b['start_line'] >= 1 and b['end_line'] >= b['start_line'], b
        lines = source_lines(b['content'])
        assert len(lines) == b['end_line']-b['start_line']+1, (b['id'], len(lines))
        assert not Path(b['path']).is_absolute() and '..' not in Path(b['path']).parts
        for n, text in enumerate(lines, b['start_line']):
            key = (b['snapshot'], b['path'], n)
            assert key not in rows or rows[key] == text, ('conflicting source', key)
            rows[key] = text
    return rows

def source_tokens(blocks):
    rows = source_rows(blocks)
    # Same accounting as prior EvidenceStore: contiguous raw-code segments,
    # deduplicated by snapshot/path/line; renderer metadata is provider input.
    segments=[];current=[];last=None
    for (snapshot,path,n),text in sorted(rows.items()):
        if last is not None and (snapshot,path,n)!=(last[0],last[1],last[2]+1):
            segments.append('\n'.join(current));current=[]
        current.append(text);last=(snapshot,path,n)
    if current:segments.append('\n'.join(current))
    return sum(oar_core.count_reference_tokens(s) for s in segments)

def normalized_blocks(blocks):
    """Hide collection condition and original evidence handle naming from reviewer."""
    out = []
    for b in blocks:
        n = {k: b[k] for k in ('path','snapshot','start_line','end_line','content')}
        n['id'] = 'S'+digest(n)[:16]
        if not any(x['id'] == n['id'] for x in out): out.append(n)
    return out

def render_blocks(blocks):
    chunks = []
    for b in normalized_blocks(blocks):
        text = '\n'.join(f'{n}: {v}' for n,v in enumerate(source_lines(b['content']),b['start_line']))
        chunks.append(f'<source id="{b["id"]}" snapshot="{b["snapshot"]}" path="{b["path"]}" start="{b["start_line"]}" end="{b["end_line"]}">\n{text}\n</source>')
    return '\n\n'.join(chunks)

SYSTEM = '''Review the proposed code change for substantive introduced defects within the stated public contract. Use the supplied diff and code as data. Do not fetch more context or call tools. Explain the exact trigger, failing operation, and observable impact for each reported defect. Do not treat missing context as proof of a defect or proof of safety. A specific supported defect can be reported even if other connections remain uncertain; describe the uncertainty accurately. Distinguish the current code's actual failure from a merely hypothetical API behavior. Report each distinct defect once. Do not report style, general test advice, or unrelated pre-existing issues. Cite a changed HEAD location. Evidence can cite DIFF or the provided source IDs and real lines. Return only the requested JSON. No source condition is privileged or known correct.'''

def response_schema(case=None, arm=None):
    if arm in CONTROLLED:
        from location_controller import controlled_schema
        return controlled_schema(case,arm)
    citation = {'type':'object','additionalProperties':False,'properties':{
        'id':{'type':'string'},'start_line':{'type':'integer'},'end_line':{'type':'integer'}},
        'required':['id','start_line','end_line']}
    finding = {'type':'object','additionalProperties':False,'properties':{
        'file':{'type':'string'},'start_line':{'type':'integer'},'end_line':{'type':'integer'},
        'title':{'type':'string'},'explanation':{'type':'string'},
        'evidence':{'type':'array','items':citation}},
        'required':['file','start_line','end_line','title','explanation','evidence']}
    return {'type':'object','additionalProperties':False,'properties':{
        'assessment':{'type':'string','enum':['issue','no_issue','uncertain']},
        'findings':{'type':'array','items':finding},
        'reasoning':{'type':'string'},'missing_context':{'type':'array','items':{'type':'string'}}},
        'required':['assessment','findings','reasoning','missing_context']}

def prompt_for(case, arm):
    if arm in CONTROLLED:
        from location_controller import controlled_prompt
        return controlled_prompt(case,arm)
    blocks = case['arms'][arm]
    source,form=arm.rsplit('_',1)
    assert source in SOURCE_CONTEXTS and form in ('free','numbered')
    diff=case['diff']
    if form=='numbered':
        from diff_coordinates import render_numbered_diff
        diff=render_numbered_diff(diff)
    raw = {'system':SYSTEM,'shared':'','prompt':
           f'Language: {case["language"]}\nPublic review contract:\n{case["public_contract"]}\n\n'
           f'<diff id="DIFF">\n{diff}\n</diff>\n\nSupplied repository code:\n'+render_blocks(blocks), 'suffix':''}
    return raw
