"""Typed, paged source discovery inside the unchanged authorized source pool.

The legacy planner wire field `file` is a scope/hint for SEARCH only. Positions
are never used for discovery. Every source read still checks its sealed hash.
Candidate discovery is not import/call/dataflow/runtime binding.
"""
import copy,re
from dataclasses import dataclass
from pathlib import PurePosixPath
from symbol_context import ContextCollector as Previous
from context48 import safe_rel,digest,Limit,SOURCE_KINDS,put

EXTENSIONS={'.py','.pyi','.go','.md','.rst','.txt','.yaml','.yml','.json','.toml'}
SEARCH_INSTRUCTION='''SEARCH operation: file is a repository/dependency search scope or path hint, not a mandatory known file. Use "." for workspace, a directory for scoped search, or a filename hint; line/column are ignored for search. Search returns static candidate bodies/occurrences, never proof they are the requested API. Multiple candidates remain open. Repeat the same scope+symbol search to resume a nonexhaustive page. For definition/references/implementation/body/flow, use a real source file and actual token/location instead.'''

@dataclass(frozen=True)
class SearchRequest:
    snapshot:str
    scope:str
    literal:str

def request(q):
    scope=q.get('file')or'.'
    scope=safe_rel(scope)
    literal=q.get('symbol','')
    if not isinstance(literal,str)or not 2<=len(literal)<=120 or '\n'in literal:
        raise ValueError('search_requires_bounded_literal_symbol')
    return SearchRequest(q.get('snapshot','HEAD'),scope,literal)

class ContextCollector(Previous):
    def __init__(self,*args,initial_state=None,**kwargs):
        super().__init__(*args,initial_state=initial_state,**kwargs)
        self.search_sessions=copy.deepcopy((initial_state or{}).get('search_sessions',{}))
    def export(self):
        return super().export()|{'search_sessions':copy.deepcopy(self.search_sessions)}
    def query(self,q,**kwargs):
        if q.get('query')!='search':return super().query(q,**kwargs)
        original=copy.deepcopy(q);actual={**q,'file':q.get('file')or'.','line':1,'column':0}
        answer=super().query(actual,**kwargs)
        answer['backend_receipt']=answer['query_receipt'];answer['question']=original
        answer['typed_search_request']=dict(snapshot=actual.get('snapshot','HEAD'),scope=actual['file'],literal=actual.get('symbol',''),coordinates_ignored=True)
        receipt=self.out/f'typed-search-query-{self.sequence:04d}.json'
        put(receipt,{k:v for k,v in answer.items()if k!='query_receipt'});answer['query_receipt']=str(receipt)
        return answer
    def search_files(self,snapshot):
        spec=self.snapshots[snapshot]
        return sorted(set(spec.get('files',{}))|set(spec.get('external_files',{}))|set(self.external_bindings.get(snapshot,{})))
    def search_extensions(self):return EXTENSIONS
    def _search(self,q):
        req=request(q)
        if req.snapshot not in self.snapshots:raise ValueError('snapshot_unavailable')
        key=digest(req.__dict__);state=self.search_sessions.get(key)
        simple=req.literal.rsplit('.',1)[-1]
        decl=re.fullmatch(r'(?:def|class|func)\s+([^\W\d]\w*)',req.literal)
        if decl:simple=decl.group(1)
        if state is None:
            allfiles=[p for p in self.search_files(req.snapshot)if PurePosixPath(p).suffix.lower()in self.search_extensions()]
            directory=req.scope.rstrip('/')+'/'
            is_dir=req.scope!='.'and any(p.startswith(directory)for p in allfiles)
            files=[p for p in allfiles if not is_dir or p.startswith(directory)]
            words=re.sub(r'([a-z0-9])([A-Z])',r'\1_\2',simple).lower().strip('_')
            files.sort(key=lambda p:(p!=req.scope,words not in p.lower(),str(PurePosixPath(p).parent)!=str(PurePosixPath(req.scope).parent),p not in self.changed.get(req.snapshot,{}),p))
            state=dict(request=req.__dict__,files=files,file_cursor=0,pending=[],candidates=[],delivered=[],scan_complete=False,scope_mode='directory'if is_dir else'workspace_with_file_hint',binding_proven=False)
            self.search_sessions[key]=state
        page=[];before={e['id']for e in self.evidence};seen=set(state['delivered']);blocked=[];current=None
        try:
            while len(page)<4:
                self._check()
                if not state['pending']:
                    if state['file_cursor']>=len(state['files']):state['scan_complete']=True;break
                    path=state['files'][state['file_cursor']]
                    # Advance only after a successful source read: budget-blocked
                    # files remain resumable, rather than silently disappearing.
                    data=self._read(req.snapshot,path);state['file_cursor']+=1
                    hits=[n for n,t in enumerate(data['rows'],1)if req.literal in t or re.search(r'(?<!\w)'+re.escape(simple)+r'(?!\w)',t)]
                    if not hits:continue
                    parsed=None
                    if self.language_for(path):
                        try:parsed=self._parse(req.snapshot,path)
                        except (ValueError,SyntaxError,RuntimeError,Limit)as exc:
                            self.unresolved.append(dict(reason='search_candidate_parse_unresolved',path=path,detail=str(exc)))
                    candidates={}
                    for line in hits:
                        unit=self._unit_at(parsed,line)if parsed else None
                        a,b=(unit['start_line'],unit['end_line'])if unit else(max(1,line-5),min(len(data['rows']),line+5))
                        cid=digest([req.snapshot,path,a,b])
                        candidates[cid]=dict(id=cid,path=path,line=line,start_line=a,end_line=b,unit_name=unit.get('name')if unit else None,complete_AST_unit=unit is not None,requested_target_identity_proven=False)
                    state['candidates'].extend(candidates.values());state['pending'].extend(candidates.values())
                item=state['pending'].pop(0)
                if item['id']in seen:continue
                current=item
                ids=self.read_search_candidate(req.snapshot,item)
                current=None
                if not ids:blocked.append(item);continue
                page.append(dict(**item,evidence_ids=ids));seen.add(item['id']);state['delivered'].append(item['id'])
        except (ValueError,OSError,RuntimeError,UnicodeError)as exc:
            if current is not None:blocked.append(current)
            self.unresolved.append(dict(reason='search_page_interrupted',detail=type(exc).__name__+': '+str(exc),resumable=True))
        state['pending']=blocked+state['pending']
        self._binding=dict(status='search_candidates_not_requested_API_binding',target_body_ids=[],search_candidate_only=True,search_exhaustive=state['scan_complete']and not state['pending'],candidate_count=len(state['candidates']),runtime_dispatch_confirmed=False,semantic_truth_guaranteed=False)
        self.events.append(dict(event='typed_search_page',request=req.__dict__,page=page,files_scanned=state['file_cursor'],total_files=len(state['files']),pending_candidates=len(state['pending']),scan_complete=state['scan_complete'],continuation_available=not state['scan_complete']or bool(state['pending']),not_a_call_or_dataflow_edge=True))
        if not page:self.unresolved.append(dict(reason='search_no_new_deliverable_page',scan_complete=state['scan_complete'],pending_candidates=len(state['pending'])))
        return[e for e in self.evidence if e['id']not in before]
    def read_search_candidate(self,snapshot,item):
        path=item['path']
        if item['complete_AST_unit']:
            data=self._parse(snapshot,path);unit=self._unit_at(data,item['line'])
            return[e['id']for e in self._emit_unit(snapshot,path,unit,'typed_search_static_candidate','literal_occurrence_not_requested_API_binding')]
        data=self._read(snapshot,path);a,b=item['start_line'],item['end_line'];rows=data['rows'][a-1:b]
        e=dict(snapshot=snapshot,path=path,start_line=a,end_line=b,content='\n'.join(rows),kind='source_lines',physical_rows=[dict(line=n,text=t)for n,t in enumerate(rows,a)],mechanical_checks=dict(complete_unit=False,source_hash_verified=True,authoritative_file_sha256=data['sha256'],range_exact=True,semantic_truth_guaranteed=False),binding={**data['binding'],'search_candidate_only':True},provenance=dict(provider='typed_search_exact_source_window',file_sha256=data['sha256']))
        e['id']='E-'+digest(e)[:20];value=self._admit(e,'literal_source_window_not_complete_body')
        return[value['id']]if value else[]
