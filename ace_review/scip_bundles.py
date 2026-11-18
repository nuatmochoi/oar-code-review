"""Gold-blind revision collapse and atomic static-use witness bundle selection."""
from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from dataclasses import replace

from .delta_scip import SemanticIndex, _diff_maps, _slice
from .schema import ContextBlock, estimate_tokens


def _spans(block):
    return block.metadata.get('source_spans') or [
        {'version':block.metadata['version'],'file':block.file,
         'start_line':block.start_line,'end_line':block.end_line}]


def _source(block):
    marker=f"Source ({block.metadata['version']}):\n"
    if marker not in block.content:
        return None
    return re.sub(r'(?m)^\d+: ','',block.content.split(marker,1)[1])


def collapse_revisions(blocks: list[ContextBlock]) -> list[ContextBlock]:
    groups=defaultdict(list)
    for i,b in enumerate(blocks):
        text=_source(b)
        owner=b.metadata.get('owner_symbol')
        key=(b.file,owner,text) if text is not None and owner else ('unmergeable',i)
        groups[key].append(b)
    output=[]
    for members in groups.values():
        versions=[b.metadata['version'] for b in members]
        if len(members)==2 and set(versions)=={'base','head'}:
            head=next(b for b in members if b.metadata['version']=='head')
            base=next(b for b in members if b.metadata['version']=='base')
            note=f"Same source text verified at base {base.file}:{base.start_line}-{base.end_line}; shown with head line numbers.\n"
            content=note+head.content
            metadata=head.metadata|{'source_spans':_spans(base)+_spans(head),
                'original_ids':[base.id,head.id],'identical_revisions_collapsed':True,
                'anchors':sorted({a for b in members for a in b.metadata.get('anchors',[b.metadata['anchor']])})}
            output.append(replace(head,content=content,token_cost=estimate_tokens(content),metadata=metadata))
        else:
            output.extend(replace(b,metadata=b.metadata|{'source_spans':_spans(b),'original_ids':[b.id]}) for b in members)
    return sorted(output,key=lambda b:b.id)


def _fragment(index, node, anchor, score, delta, max_lines=32):
    text,lo,hi=_slice(index,node,max_lines)
    if not text:
        return None
    owner=node.get('owner') or node.get('symbol') or ''
    identity=(index.version,node['file'],lo,hi,owner)
    content=f"Static source; owner={owner}\nSource ({index.version}):\n{text}"
    return ContextBlock('SCIP-SRC-'+hashlib.sha256(repr(identity).encode()).hexdigest()[:16],
        'scip_bundle_source',content,node['file'],lo,hi,query=anchor,relation='witness_source',
        token_cost=estimate_tokens(content),metadata={
            'version':index.version,'owner_symbol':owner,'anchor':anchor,'anchors':[anchor],
            'score':score,'delta':delta,'source_spans':[{'version':index.version,'file':node['file'],
                                                       'start_line':lo,'end_line':hi}]})


def _entries(index, changed):
    entries=defaultdict(list)
    for path,lines in changed.items():
        for node in index.by_file.get(path,[]):
            direct=lines.intersection(range(node['start_line'],node['end_line']+1))
            enclosed=lines.intersection(range(node['enclosing'][0],node['enclosing'][1]+1)) if node['enclosing'] and node['role']&1 else set()
            for symbol in {node['symbol'],node['owner']}:
                if symbol in index.defs and ' <project> ' in symbol and not symbol.endswith('/') and (direct or enclosed):
                    entries[symbol].append({'version':index.version,'file':path,'line':min(direct|enclosed),
                                            'mapping':'occurrence' if direct else 'enclosing_definition'})
    return entries


def build_bundles(base: SemanticIndex, head: SemanticIndex, diff: str,
                  candidates: list[ContextBlock]) -> list[dict]:
    changes,visible=_diff_maps(diff)
    entries=defaultdict(list)
    for index in (base,head):
        for symbol,sites in _entries(index,changes[index.version]).items():
            entries[symbol]+=sites
    # One deterministic representative per static semantic path and revision;
    # never align occurrence offsets across revisions by assuming equal lines.
    representatives={}
    for b in sorted(candidates,key=lambda b:(b.metadata.get('target_line',0),b.start_line or 0,b.id)):
        m=b.metadata
        if not entries[m['anchor']]:
            continue
        key=(m['anchor'],b.file,m.get('owner_symbol'),m.get('endpoint_symbol'),tuple(m['path']))
        representatives.setdefault((key,m['version']),b)
    grouped=defaultdict(list)
    for (key,version),b in representatives.items():grouped[key].append(b)
    bundles=[]
    for key,blocks in sorted(grouped.items(),key=lambda item:repr(item[0])):
        anchor=key[0];sources={};edges={};deltas=set()
        for b in blocks:
            m=b.metadata;index=head if m['version']=='head' else base
            deltas.update(m['delta'])
            endpoint={'file':b.file,'start_line':b.start_line,'end_line':b.end_line,
                      'enclosing':(b.start_line,b.end_line),'owner':m.get('owner_symbol','')}
            nodes=[(endpoint,60)]
            for n in index.defs.get(anchor,[])[:1]:nodes.append((n,24))
            for edge in m.get('edge_witness',[]):
                edges[json.dumps(edge,sort_keys=True)]=edge
                if 'file' in edge:
                    nodes.append(({'file':edge['file'],'start_line':edge['line'],'end_line':edge['line'],
                                   'enclosing':None,'owner':edge.get('owner','')},32))
                else:
                    # Typed index relation: include both declarations; do not
                    # invent a source-level call site for a non-occurrence edge.
                    for symbol in (edge['from'],edge['to']):
                        nodes.extend((n,24) for n in index.defs.get(symbol,[])[:1])
            for node,limit in nodes:
                fragment=_fragment(index,node,anchor,m['score'],m['delta'],limit)
                if fragment and not set(range(fragment.start_line,fragment.end_line+1)) <= visible[index.version].get(fragment.file,set()):
                    sources[fragment.id]=fragment
        if not sources:continue
        sites=sorted({json.dumps(e,sort_keys=True) for e in entries[anchor]})
        bounded_sites=[json.loads(s) for version in ('base','head')
                       for s in [s for s in sites if json.loads(s)['version']==version][:2]]
        bundles.append({'id':'BUNDLE-'+hashlib.sha256(repr(key).encode()).hexdigest()[:14],
            'anchor':anchor,'entry_sites':bounded_sites,'score':max(b.metadata['score'] for b in blocks),
            'edges':list(edges.values()),'path':list(key[-1]),'delta':sorted(deltas),
            'sources':list(sources.values())})
    # Collapse globally, so a shared source is paid for once across bundles.
    pool={s.id:s for b in bundles for s in b['sources']}
    merged=collapse_revisions(list(pool.values()))
    by_old={old:s for s in merged for old in s.metadata['original_ids']}
    for b in bundles:
        b['sources']=list({by_old[s.id].id:by_old[s.id] for s in b['sources']}.values())
    return bundles


def bundle_sources(bundles: list[dict]) -> list[ContextBlock]:
    pool={}
    for bundle in bundles:
        for source in bundle['sources']:
            prior=pool.get(source.id)
            anchors=set(prior.metadata['anchors'] if prior else [])|{bundle['anchor']}
            pool[source.id]=replace(source,metadata=source.metadata|{'anchors':sorted(anchors)})
    return sorted(pool.values(),key=lambda b:b.id)


def _render_selection(selected):
    sources=bundle_sources(selected)
    if not sources:return []
    lines=['Static-use evidence bundles. Entry sites refer to the supplied PR diff.',
           'Edges are compiler-resolved uses/typed relations, NOT feasible data-flow or proof of a defect.']
    for bundle in selected:
        sites='; '.join(f"{e['version']} {e['file']}:{e['line']} ({e['mapping']})" for e in bundle['entry_sites'])
        lines.extend([f"{bundle['id']}: anchor={bundle['anchor']}",f"Diff entry: {sites}",
                      f"Delta: {', '.join(bundle['delta'])}",
                      'Required sources: '+', '.join(s.id for s in bundle['sources'])])
        for edge in bundle['edges']:
            location=f" at {edge['file']}:{edge['line']}" if 'file' in edge else ' (index relationship; no call-site claim)'
            lines.append(f"{edge['version']}: {edge['from']} --{edge['relation']}--> {edge['to']}{location}")
    content='\n'.join(lines)
    manifest=ContextBlock('SCIP-BUNDLE-MANIFEST','scip_bundle_manifest',content,
                          token_cost=estimate_tokens(content),metadata={'source_spans':[],
                          'selected_bundle_ids':[b['id'] for b in selected]})
    return [manifest]+sources


def select_bundles(bundles: list[dict], budget: int, policy: str = 'diversity') -> tuple[list[ContextBlock], dict]:
    if budget < 0:raise ValueError('Negative budget')
    if policy not in {'diversity','density'}:raise ValueError('Unknown bundle selection policy')
    selected=[];anchors=set();relations=set();pool=list(bundles)
    single_cost={b['id']:sum(s.token_cost for s in _render_selection([b])) for b in bundles} if policy=='density' else {}
    while pool:
        used={s.id for s in bundle_sources(selected)}
        def priority(b):
            if policy=='density':
                novelty=sum(s.id not in used for s in b['sources'])/max(1,len(b['sources']))
                utility=(b.get('score',1)*novelty+2*(b['anchor'] not in anchors)+int(tuple(b['path']) not in relations))
                return (utility/max(1,single_cost[b['id']])**.5,b['id'])
            structural=bool(set(b['delta']) & {'body_changed','signature_changed','added_definition','removed_definition'})
            return (b['anchor'] not in anchors,tuple(b['path']) not in relations,
                    structural,-len(b['path']),b['id'])
        # Ranking depends only on accepted bundles. Rejected candidates do not
        # change priorities, so sort once per acceptance, not once per rejection.
        ordered=sorted(pool,key=priority,reverse=True)
        for position,candidate in enumerate(ordered):
            rendered=_render_selection(selected+[candidate])
            if rendered and sum(s.token_cost for s in rendered)<=budget:
                selected.append(candidate);anchors.add(candidate['anchor']);relations.add(tuple(candidate['path']))
                pool=ordered[position+1:]
                break
        else:
            break
    output=_render_selection(selected)
    return output,{'selected_bundle_ids':[b['id'] for b in selected],
        'selected_bundles':[{'id':b['id'],'anchor':b['anchor'],'source_ids':[s.id for s in b['sources']]} for b in selected],
        'selected_source_count':max(0,len(output)-1),'context_tokens':sum(s.token_cost for s in output),
        'policy':policy}
