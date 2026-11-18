"""Bind model-selected short handles to immutable actual source/HEAD locations.

This module has no access to labels, oracle lists or model reasoning beyond the
returned structured selections. It never filters, merges, repairs or certifies
the meaning of findings. All raw findings retain a corresponding canonical row.
"""
from __future__ import annotations
import copy
import json
import re
from common import SYSTEM,normalized_blocks,digest,source_lines
from location_visibility import eligible_head_rows,relative_path
from diff_coordinates import render_numbered_diff,parse_diff
from location_instructions import ANCHOR_REASONING

VERSION='short-handles-numbered-diff-visible-head-lines-v2'

def diff_hunks(text):
    return [dict(path=relative_path(h['new_path']),start_line=max(1,h['new_start']),
        end_line=max(1,h['new_start']+h['new_count']-1),head_rows={r['new_line']:r['code'].removesuffix('\n').removesuffix('\r')
            for r in h['rows'] if r['new_line'] is not None}) for h in parse_diff(text)]

def meaningful_line(text):
    t=text.strip()
    return bool(t and not t.startswith(('#','//','/*')) and t not in ('*','*/','{','}',')',']',';',',','""',"''",'"""',"'''"))

def make_registry(case,arm):
    blocks=normalized_blocks(case['arms'][arm]);evidence={};locs={};visible={}
    changed=sorted(case['changed_files']);codes={p:f'F{i}' for i,p in enumerate(changed)}
    for i,b in enumerate(blocks,1):
        handle=f'E{i:03d}'
        evidence[handle]=dict(kind='source',canonical_id=b['id'],path=b['path'],snapshot=b['snapshot'],
                              start_line=b['start_line'],end_line=b['end_line'],content_sha256=digest(b['content']))
    # Location vocabulary is stable across retrieval treatments: only protected
    # changed-file AST and actual diff HEAD rows, never arbitrary fetched bodies.
    visible=eligible_head_rows(case,arm)
    hunks=diff_hunks(case['diff'])
    for i,h in enumerate(hunks,1):
        if h['path'] is None:continue
        evidence[f'D{i:03d}']=dict(kind='diff',canonical_id='DIFF',path=h['path'],snapshot='HEAD',
                                  start_line=h['start_line'],end_line=h['end_line'],content_sha256=digest(h))
    for (path,n),text in sorted(visible.items()):
        if path in codes and meaningful_line(text):
            locs[f'{codes[path]}:{n}']=dict(path=path,start_line=n,end_line=n,visible_code_sha256=digest(text))
    if not locs:raise ValueError('no actual visible nonblank HEAD location')
    return dict(version=VERSION,source_policy=arm.rsplit('_',1)[0],
                file_codes={v:k for k,v in codes.items()},locations=locs,evidence=evidence,
                source_blocks_sha256=digest(blocks),diff_sha256=digest(case['diff']))

def controlled_schema(case,arm):
    reg=make_registry(case,arm)
    finding={'type':'object','additionalProperties':False,'properties':{
        'location_ref':{'type':'string','enum':list(reg['locations'])},
        'title':{'type':'string'},'explanation':{'type':'string'},
        'evidence_refs':{'type':'array','items':{'type':'string','enum':list(reg['evidence'])}}},
        'required':['location_ref','title','explanation','evidence_refs']}
    return {'type':'object','additionalProperties':False,'properties':{
        'assessment':{'type':'string','enum':['issue','no_issue','uncertain']},
        'findings':{'type':'array','items':finding},'reasoning':{'type':'string'},
        'missing_context':{'type':'array','items':{'type':'string'}}},
        'required':['assessment','findings','reasoning','missing_context']}

def controlled_prompt(case,arm):
    reg=make_registry(case,arm);parts=[]
    for i,b in enumerate(normalized_blocks(case['arms'][arm]),1):
        code='\n'.join(f'{n}: {t}' for n,t in enumerate(source_lines(b['content']),b['start_line']))
        parts.append(f'<source id="E{i:03d}" snapshot="{b["snapshot"]}" path="{b["path"]}" start="{b["start_line"]}" end="{b["end_line"]}">\n{code}\n</source>')
    diffs=[dict(handle=h,**{k:v[k] for k in ('path','start_line','end_line')}) for h,v in reg['evidence'].items() if v['kind']=='diff']
    instructions=(ANCHOR_REASONING+'For each finding choose location_ref as file-code:actual-HEAD-line, using a relevant operation visible in supplied HEAD code or diff. '
                  'File codes are '+json.dumps(reg['file_codes'],ensure_ascii=False)+'. '
                  'Choose evidence_refs from the displayed E source handles or D diff-hunk handles. '
                  'The controller emits canonical evidence IDs and exact supplied unit ranges; do not invent IDs or line ranges. '
                  'A known handle proves only that source exists, not that your claim is true or that the source supports it. '
                  'Return only the specified JSON; no finding is required when no introduced defect is supported.')
    system=SYSTEM.replace('Cite a changed HEAD location. Evidence can cite DIFF or the provided source IDs and real lines. ',
                          'Select the relevant changed HEAD operation and its actual supporting supplied sources using the controller handles. ')
    prompt=(f'Language: {case["language"]}\nPublic review contract:\n{case["public_contract"]}\n\n'
            f'<diff id="DIFF">\n{render_numbered_diff(case["diff"])}\n</diff>\n\n'
            '<diff_handles>\n'+json.dumps(diffs,ensure_ascii=False)+'\n</diff_handles>\n\n'
            'Supplied repository code:\n'+'\n\n'.join(parts)+'\n\n'+instructions)
    return dict(system=system,shared='',prompt=prompt,suffix='')

def bind_payload(raw_payload,registry):
    """One-to-one canonicalization. Invalid selections stay failures, never repair."""
    payload=copy.deepcopy(raw_payload) if isinstance(raw_payload,dict) else {}
    findings=payload.get('findings',[])
    if not isinstance(findings,list):findings=[]
    canonical=[];errors=[];bindings=[]
    for index,f in enumerate(findings):
        f=f if isinstance(f,dict) else {}
        key=f.get('location_ref')
        loc=registry['locations'].get(key) if isinstance(key,str) else None;refs=f.get('evidence_refs',[])
        if not isinstance(refs,list):refs=[];errors.append(dict(finding=index,error='evidence_refs_not_array'))
        if loc is None:errors.append(dict(finding=index,error='unknown_location',selected=f.get('location_ref')))
        cites=[]
        for ref in refs:
            e=registry['evidence'].get(ref) if isinstance(ref,str) else None
            if e is None:
                errors.append(dict(finding=index,error='unknown_evidence',selected=ref))
                cites.append(dict(id='INVALID:'+str(ref),start_line=0,end_line=0))
            else:cites.append(dict(id=e['canonical_id'],start_line=e['start_line'],end_line=e['end_line']))
        canonical.append(dict(file=loc['path'] if loc else '',start_line=loc['start_line'] if loc else 0,
                              end_line=loc['end_line'] if loc else 0,title=f.get('title',''),explanation=f.get('explanation',''),evidence=cites))
        bindings.append(dict(finding=index,location_ref=f.get('location_ref'),evidence_refs=refs,
                             canonical_sha256=digest(canonical[-1])))
    out={k:copy.deepcopy(payload.get(k,[] if k=='missing_context' else '')) for k in ('assessment','reasoning','missing_context')}
    out['findings']=canonical
    return out,dict(version=VERSION,registry_sha256=digest(registry),raw_findings=len(findings),canonical_findings=len(canonical),
                    binding_errors=errors,bindings=bindings,semantic_assessment_performed=False,
                    filtered_findings=0,repaired_selections=0)
