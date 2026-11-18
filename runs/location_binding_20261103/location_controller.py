"""Map row/AST-operation handles to one actual HEAD anchor without repair."""
import json
import line_controller
from common import SYSTEM,normalized_blocks,digest,source_lines
from diff_coordinates import render_numbered_diff
from location_instructions import ANCHOR_REASONING

VERSION='visible-ast-operation-single-head-anchor-v1'

def make_registry(case,arm):
    source,form=arm.rsplit('_',1)
    assert form in ('line','operation')
    reg=line_controller.make_registry(case,arm)
    if form=='line':return reg
    catalog=case['operation_catalogs'][source]
    locations={}
    for op in catalog['operations']:
        key=op['handle'];anchor=op['anchor_line']
        locations[key]=dict(path=op['path'],start_line=anchor,end_line=anchor,
            visible_code_sha256=digest(op['code']),operation_kind=op['kind'],operation_code=op['code'],
            ast_start_line=op['start_line'],ast_end_line=op['end_line'],ast_start_column=op['start_column'],
            ast_end_column=op['end_column'],file_sha256=op['file_sha256'],backend=op['backend'])
    if not locations:raise ValueError('no fully supplied AST operation available for anchor')
    reg.update(version=VERSION,locations=locations,operation_catalog_sha256=digest(catalog),
               canonical_location_policy='single actual HEAD start token line; AST span is not the report range')
    return reg

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
    if arm.endswith('_line'):return line_controller.controlled_prompt(case,arm)
    reg=make_registry(case,arm);parts=[]
    for i,b in enumerate(normalized_blocks(case['arms'][arm]),1):
        code='\n'.join(f'{n}: {t}' for n,t in enumerate(source_lines(b['content']),b['start_line']))
        parts.append(f'<source id="E{i:03d}" snapshot="{b["snapshot"]}" path="{b["path"]}" start="{b["start_line"]}" end="{b["end_line"]}">\n{code}\n</source>')
    diffs=[dict(handle=h,**{k:v[k] for k in ('path','start_line','end_line')}) for h,v in reg['evidence'].items() if v['kind']=='diff']
    operations=[]
    for handle,loc in reg['locations'].items():
        code=reg['file_codes'];filecode=next(k for k,v in code.items() if v==loc['path'])
        operations.append(f'<operation id="{handle}" file="{filecode}" anchor_HEAD="{loc["start_line"]}" kind="{loc["operation_kind"]}" '
            f'AST_start="{loc["ast_start_line"]}:{loc["ast_start_column"]}" AST_end_exclusive="{loc["ast_end_line"]}:{loc["ast_end_column"]}">\n{loc["operation_code"]}\n</operation>')
    instructions=(ANCHOR_REASONING+'Choose location_ref from the displayed AST operation handles. '
        'The controller reports only that operation\'s single actual HEAD anchor line, not its entire AST span. '
        'An operation handle is not evidence of semantic correctness. Choose evidence_refs from E source or D diff-hunk handles; '
        'the controller supplies canonical source IDs and ranges and does not repair or filter findings. '
        'File codes are '+json.dumps(reg['file_codes'],ensure_ascii=False)+'. Return only the specified JSON.')
    system=SYSTEM.replace('Cite a changed HEAD location. Evidence can cite DIFF or the provided source IDs and real lines. ',
        'Select the relevant supplied AST operation for the changed HEAD report anchor and its actual supporting sources using controller handles. ')
    prompt=(f'Language: {case["language"]}\nPublic review contract:\n{case["public_contract"]}\n\n'
        f'<diff id="DIFF">\n{render_numbered_diff(case["diff"])}\n</diff>\n\n'
        '<diff_handles>\n'+json.dumps(diffs,ensure_ascii=False)+'\n</diff_handles>\n\n'
        'Supplied repository code:\n'+'\n\n'.join(parts)+'\n\n'
        '<operation_handles>\n'+'\n'.join(operations)+'\n</operation_handles>\n\n'+instructions)
    return dict(system=system,shared='',prompt=prompt,suffix='')

def bind_payload(raw_payload,registry):
    # Same literal one-to-one binder for both conditions; only frozen map differs.
    return line_controller.bind_payload(raw_payload,registry)
