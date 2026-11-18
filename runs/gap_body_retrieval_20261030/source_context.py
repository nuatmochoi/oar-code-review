"""Reconstruct protected source from sealed ACTUAL input, never prior prose."""
from __future__ import annotations
import copy
import hashlib
import html
import json
from pathlib import Path

from bootstrap import ROOT, PARENT, BUDGET
import oar_core as oc
import delivery_rendering as dr
from ace_review.protocol_hypothesis import mask_source

CASES=('N03','N06','N07','H05','H10')
SOURCE_CAP=5200

def load(p):return json.loads(Path(p).read_text())
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def manifest_path(key):
    if key.startswith('H'):
        return PARENT/'holdout/public/workspace_manifests'/f'{key}.json'
    return ROOT.parent/'lsp_symbol_graph_20261025/workspace_manifests'/f'{key}.json'

def protected_sources(raw, audit):
    rows={r['evidence_id']:r for r in audit['evidence'] if r['content_kind']=='source_lines'}
    collected={}
    for text in raw.values():
        for b in dr._blocks(text):
            eid=b['attrs'].get('evidence_id' if b['tag']=='file' else 'id')
            if eid not in rows:continue
            row=rows[eid]
            assert dr._source_path_matches(b,row['file']),eid
            assert b['closed'],eid
            lines=collected.setdefault(eid,{})
            for physical,terminated in b['body']:
                m=dr._LINE.fullmatch(physical)
                if not m:continue
                n,t=int(m.group(1)),m.group(2)
                assert terminated,(eid,n)
                if n in lines:assert lines[n]==t,(eid,n)
                lines[n]=t
    out=[]
    for eid,row in rows.items():
        visible=collected.get(eid,{})
        assert set(visible)==set(row['visible_lines']),(eid,'visible line mismatch')
        if visible:out.append(dict(file=row['file'],snapshot=row['snapshot'],evidence_id=eid,lines=[[n,t] for n,t in sorted(visible.items())]))
    return out

def make_store(sources):
    store=oc.EvidenceStore()
    for s in sources:
        store.add(oc.Evidence(id=s['evidence_id'],file=s['file'],snapshot=s['snapshot'],
                             lines=tuple((int(n),t) for n,t in s['lines']),provenance='historical_seed'))
    return store

def load_context(group):
    prep=load(BUDGET/'prepared/inputs'/f'{group}_repaired_full.json')
    refs=prep['inherited_references'];original=load(Path(refs['review_result']['path']))
    sources=protected_sources(prep['raw'],original['delivery_audit']['stages'][-1])
    store=make_store(sources)
    assert store.union_tokens(oc.count_reference_tokens)==original['source_exposure']['union_reference_tokens'],group
    assert store.union_hash()==original['source_exposure']['union_sha256'],(group,'source union hash')
    key='_'.join(group.split('_')[:2]);mp=manifest_path(key);manifest=load(mp)
    assert Path(manifest['root'])==Path(original['workspace']['root'])
    for s in sources:
        if s['snapshot']=='head':
            file=Path(manifest['root'])/s['file'];assert sha(file)==manifest['sources'][s['file']]
            actual=mask_source(file.read_text(encoding="utf-8",errors="replace")).splitlines()
            assert all(actual[n-1]==t for n,t in s['lines']),s['evidence_id']
    return dict(group_id=group,key=key,prepared=prep,raw=copy.deepcopy(prep['raw']),
                sources=sources,store=store,manifest=manifest,manifest_path=str(mp),
                critic=load(Path(refs['update_1_result']['path'])),
                frontier=copy.deepcopy(original['body_frontier']['final']),
                inherited_queries=original['queries'],source_exposure=original['source_exposure'],
                prefix_references=refs,protected_source_hash=oc._sha(sources))

class SourceAccount:
    def __init__(self,sources):
        self.sources=copy.deepcopy(sources);self.store=make_store(sources)
        self.initial_tokens=self.store.union_tokens(oc.count_reference_tokens)
        self.added=[];self.log=[]

    @property
    def tokens(self):return self.store.union_tokens(oc.count_reference_tokens)

    def covered(self,path,start,end):return bool(self.store.covers('head',path,start,end))

    def admit(self,path,lines,request,provenance):
        if not lines:return None
        lines=tuple((int(n),str(t)) for n,t in lines)
        eid='lookup:'+oc._sha([path,lines])[:24]+'@head'
        ev=oc.Evidence(id=eid,file=path,snapshot='head',lines=lines,provenance='read_lines',backend='native-location+python-ast-bounds')
        trial=oc.EvidenceStore();trial.by_id=dict(self.store.by_id);trial._lines={k:dict(v) for k,v in self.store._lines.items()}
        trial.add(ev);after=trial.union_tokens(oc.count_reference_tokens)
        row=dict(evidence_id=eid,path=path,start=lines[0][0],end=lines[-1][0],before=self.tokens,after=after,
                 marginal=after-self.tokens,admitted=after<=SOURCE_CAP,request=copy.deepcopy(request),provenance=provenance)
        self.log.append(row)
        if after>SOURCE_CAP:return None
        source=dict(file=path,snapshot='head',evidence_id=eid,lines=[list(x) for x in lines])
        self.store.add(ev)
        if not any(s['evidence_id']==eid for s in self.added):self.added.append(source)
        return source

    def rendered(self):
        blocks=[]
        for s in self.added:
            eid=s['evidence_id'];path=html.escape(s['file'],quote=True)
            blocks.append(f'<evidence id="{eid}" operation="read_lines" source="{path}:{s["lines"][0][0]}-{s["lines"][-1][0]}" path="{path}" snapshot="head">\n'+
                          '\n'.join(f'{n}: {t}' for n,t in s['lines'])+'\n</evidence>')
        return '\n'.join(blocks)+'\n' if blocks else ''

    def verify_delivery(self,raw,allowed_prior_ids=()):
        observed=dr.observe_delivery(raw,self.store)
        errors=[]
        for r in observed['evidence']:
            if r.get('mismatched_lines') or r.get('unexpected_lines') or r.get('path_or_snapshot_mismatches'):
                errors.append(r)
            if r['content_kind']=='source_lines' and set(r['visible_lines'])!=set(n for n,_ in self.store.get(r['evidence_id']).lines):errors.append(r)
        assert not errors,errors
        assert set(observed['unknown_evidence_ids'])<=set(allowed_prior_ids),observed['unknown_evidence_ids']
        return observed
