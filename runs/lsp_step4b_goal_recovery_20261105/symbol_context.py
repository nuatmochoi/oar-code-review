"""Typed AST identifiers/declarations and native definition binding.

Lexical occurrences and reference locations remain candidates. No name match
or declaration alone proves execution, causal truth or a complete method set.
"""
import ast,copy,re,json,os,hashlib
from pathlib import Path,PurePosixPath
import context48 as core
from bound_context import ContextCollector as BoundCollector

core.GO_PARSER=Path(__file__).resolve().parent/'tools/frontier-go'
DECLARATIONS={'type_declaration','constant_declaration','variable_declaration'}
class AmbiguousSymbol(ValueError):pass

class ContextCollector(BoundCollector):
    def __init__(self,*args,initial_state=None,**kwargs):
        super().__init__(*args,initial_state=initial_state,**kwargs)
        self.controller_frontier_state=copy.deepcopy((initial_state or{}).get('controller_frontier_state',{}))
        self._verified_parse_stats={}
        self._authority_recovery_attempted=set()
        self.native_environment_recoveries=copy.deepcopy((initial_state or{}).get('native_environment_recoveries',[]))
        self.stats['ast_memory_cache_hits']=(initial_state or{}).get('cost',{}).get('ast_memory_cache_hits',0)

    def export(self):
        return super().export()|{'controller_frontier_state':copy.deepcopy(self.controller_frontier_state),'native_environment_recoveries':copy.deepcopy(self.native_environment_recoveries)}
    def _environment(self,snapshot,language,module=None):
        if language=='Go' and module is not None:
            profile=self._profile(snapshot,language);root=Path(self.snapshots[snapshot]['root']).resolve();relative=str(Path(module).resolve().relative_to(root))
            recovery_key=(snapshot,relative)
            if relative not in profile.get('pinned_toolchains',{}) and recovery_key not in self._authority_recovery_attempted:
                self._authority_recovery_attempted.add(recovery_key)
                allowed_stdlibs={(str(Path(s['root']).resolve()),s.get('version'))for s in profile.get('allowed_external_roots',[])if s.get('origin')=='go_stdlib'}
                seen=set()
                # Only declared, hashed package-loader authority receipts may
                # recover a missing pin. Never use the host default toolchain.
                for spec in profile.get('allowed_external_roots',[]):
                    for ref in spec.get('binding_evidence',[]):
                        path=Path(ref['path'])
                        if path.name!='go-authority-env.stdout' or str(path)in seen:continue
                        seen.add(str(path))
                        self._check()
                        if self.stats['file_reads']>=self.budgets['max_file_reads']:raise core.Limit('file_read_budget')
                        raw=path.read_bytes();self.stats['file_reads']+=1;self.stats['source_bytes_read']+=len(raw)
                        if hashlib.sha256(raw).hexdigest()!=ref['sha256']:raise ValueError('Go_authority_receipt_hash_mismatch')
                        env=json.loads(raw)
                        if Path(env.get('GOMOD','')).resolve()!=Path(module).resolve()/'go.mod':continue
                        goroot=Path(env['GOROOT']).resolve();binary=goroot/'bin/go'
                        if (str(goroot/'src'),env['GOVERSION'])not in allowed_stdlibs or not binary.is_file():continue
                        pinned=dict(GOROOT=str(goroot),GOVERSION=env['GOVERSION'],go_binary=str(binary),go_binary_sha256=core.sha(binary),recorded_environment_reference=ref,**{k:env[k]for k in ('GOOS','GOARCH','CGO_ENABLED')})
                        profile.setdefault('pinned_toolchains',{})[relative]=pinned
                        record=dict(snapshot=snapshot,module=relative,pin=pinned,basis='hash_verified_predeclared_package_loader_environment',runtime_or_error_free_package_load_proven=False)
                        self.native_environment_recoveries.append(record);self.events.append(dict(event='missing_toolchain_pin_recovered_from_authority',**record));break
                    if relative in profile.get('pinned_toolchains',{}):break
        return super()._environment(snapshot,language,module)

    def _parse(self,snapshot,path):
        self._check();actual,expected,_=self._target(snapshot,path);stat=actual.stat();stamp=(stat.st_ino,stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns)
        key=(snapshot,path,expected)
        if self._verified_parse_stats.get(key)==stamp and key in self.cache:
            self.stats['ast_memory_cache_hits']=self.stats.get('ast_memory_cache_hits',0)+1
            return self.cache[key]
        data=super()._parse(snapshot,path);self._verified_parse_stats[key]=stamp
        if '_identifiers'in data:return data
        identifiers=[];declarations=[]
        if path.endswith('.go'):
            for item in data['tree'].get('identifiers',[]):
                identifiers.append(dict(name=item['code'],line=item['start_line'],column=item['start_column'],role='AST_identifier'))
            for item in data['tree'].get('declarations',[]):
                ns=item.get('name_span')or{};declarations.append({**item,'name_line':ns['start_line'],'name_column':ns['start_column']})
        else:
            tree=data['tree'];parents={ch:n for n in ast.walk(tree)for ch in ast.iter_child_nodes(n)}
            for node in ast.walk(tree):
                if isinstance(node,ast.Name):identifiers.append(dict(name=node.id,line=node.lineno,column=node.col_offset+1,role='write'if isinstance(node.ctx,ast.Store)else'identifier_use',node=node))
                elif isinstance(node,ast.Attribute):identifiers.append(dict(name=node.attr,line=node.end_lineno,column=node.end_col_offset-len(node.attr.encode())+1,role='attribute_access',node=node))
                if isinstance(node,(ast.Assign,ast.AnnAssign)):
                    targets=node.targets if isinstance(node,ast.Assign)else[node.target]
                    for target in targets:
                        for name in ast.walk(target):
                            if isinstance(name,ast.Name):declarations.append(dict(name=name.id,name_line=name.lineno,name_column=name.col_offset+1,start_line=node.lineno,end_line=node.end_lineno,kind='variable_declaration',node=node))
            for unit in data['units']:
                if unit.get('name_line'):identifiers.append(dict(name=unit['name'],line=unit['name_line'],column=unit['name_column'],role='declaration'))
        for unit in declarations:
            identifiers.append(dict(name=unit['name'],line=unit['name_line'],column=unit['name_column'],role=unit['kind']))
        unique={(x['name'],x['line'],x['column']):x for x in identifiers}
        data['_identifiers']=list(unique.values());data['_declarations']=declarations
        return data

    def _unit_at(self,data,line,*,definition=False,name=None):
        unit=super()._unit_at(data,line,definition=definition,name=name)
        if unit is not None or not definition:return unit
        candidates=[u for u in data.get('_declarations',[])if u['name_line']==line and(name is None or u['name']==name)]
        return min(candidates,key=lambda u:u['end_line']-u['start_line'])if candidates else None

    def _symbol_anchor(self,q):
        name=q.get('symbol','').rsplit('.',1)[-1]
        if not self.language_for(q['file'])or not re.fullmatch(r'[^\W\d]\w*',name):return q,[]
        data=self._parse(q['snapshot'],q['file']);positions=[x for x in data['_identifiers']if x['name']==name]
        exact=[x for x in positions if x['line']==q['line']and(not q.get('column')or x['column']==q['column'])]
        if exact:choices=exact
        else:
            scope=lambda line:(lambda u:u if u and u['kind']in('function','class','closure')else None)(self._unit_at(data,line))
            owner=scope(q['line']);near=[x for x in positions if owner and abs(x['line']-q['line'])<=3 and scope(x['line'])==owner]
            declarations=[dict(name=u['name'],line=u['name_line'],column=u['name_column'],role=u['kind'])for u in data['_declarations']if u['name']==name and(scope(u['name_line'])is None or scope(u['name_line'])==owner)]
            scoped=[x for x in positions if owner and owner['start_line']<=x['line']<=owner['end_line']]
            choices=near if len(near)==1 else declarations if len(declarations)==1 else scoped if len(scoped)==1 else positions
        choices=list({(x['line'],x['column']):x for x in choices}.values())
        catalog=[{k:x[k]for k in('name','line','column','role')}for x in choices]
        if len(choices)>1:
            self.unresolved.append(dict(reason='ambiguous_AST_symbol_positions',symbol=name,candidates=catalog))
            raise AmbiguousSymbol('ambiguous_AST_symbol_positions')
        if not choices:return q,catalog
        item=choices[0];actual={**q,'line':item['line'],'column':item['column'],'symbol':name};self._anchor(actual,data)
        self.events.append(dict(event='AST_symbol_anchor',requested={k:q.get(k)for k in('snapshot','file','line','column','symbol')},actual={k:actual[k]for k in('snapshot','file','line','column','symbol')},role=item['role']))
        return actual,catalog

    def _lookup(self,q,operation):
        try:actual,candidates=self._symbol_anchor(q)
        except AmbiguousSymbol:
            self._binding=dict(status='ambiguous_symbol_positions',target_body_ids=[],semantic_truth_guaranteed=False);return []
        effective='definition'if operation=='implementation'and q.get('kind')in('implementation_body','exception_branch','value_path')else operation
        result=super()._lookup(actual,effective)
        if self._binding:
            self._binding['original_anchor']={k:q.get(k)for k in('snapshot','file','line','column')}
            self._binding['requested_operation']=operation;self._binding['AST_identifier_candidates']=candidates
            typed=[e['id']for e in result if e.get('mechanical_checks',{}).get('unit_kind')in DECLARATIONS]
            if typed:self._binding.update(typed_declaration_ids=typed,status='LSP_definition_to_complete_declaration')
        return result

    def _search(self,q):
        needle=q.get('symbol','');simple=needle.rsplit('.',1)[-1]
        declaration_hint=re.fullmatch(r'(?:def|class|func)\s+([^\W\d]\w*)',needle)
        if declaration_hint:simple=declaration_hint.group(1)
        if not isinstance(needle,str)or not 2<=len(needle)<=120 or '\n'in needle:raise ValueError('search_requires_bounded_literal_symbol')
        if self.language_for(q['file']):
            try:
                actual,positions=self._symbol_anchor(q)
                if positions:return self._lookup(actual,'definition')
            except AmbiguousSymbol:pass
        files=self.snapshots[q['snapshot']].get('files',{});hint=q['file'];self._target(q['snapshot'],hint)
        words=re.sub(r'([a-z0-9])([A-Z])',r'\1_\2',simple).lower().strip('_')
        candidates=[p for p in files if PurePosixPath(p).suffix.lower()in{'.py','.pyi','.go','.md','.rst','.txt','.yaml','.yml','.json','.toml'}]
        candidates.sort(key=lambda p:(p!=hint,words not in str(PurePosixPath(p).with_suffix('')).lower(),not self.language_for(p),str(PurePosixPath(p).parent)!=str(PurePosixPath(hint).parent),p not in self.changed.get(q['snapshot'],{}),p))
        scanned=0;oldids={e['id']for e in self.evidence}
        for path in candidates:
            self._check();data=self._read(q['snapshot'],path);scanned+=1
            hits=[n for n,row in enumerate(data['rows'],1)if needle in row or simple in row]
            if not hits:continue
            parsed=self._parse(q['snapshot'],path)if self.language_for(path)else None
            if parsed:
                declared=[u for u in parsed['units']+parsed['_declarations']if u['name']==simple and u.get('name_line')]
                calls=[c for c in parsed['calls']if c['name']==simple]
                choices=[dict(line=u['name_line'],column=u['name_column'])for u in declared]+[dict(line=c['line'],column=c.get('column',0))for c in calls]
                if choices:
                    # A concrete candidate position is bound by LSP; its link
                    # to the original missing semantic premise stays open.
                    for pos in choices:
                        self._check();answer=self._lookup({**q,'file':path,'symbol':simple,**pos},'definition')
                        if answer:
                            self._binding=(self._binding or{})|dict(search_candidate_only=True,search_exhaustive=False,requested_hint=hint,search_source=path)
                            self.events.append(dict(event='search_to_AST_to_LSP',symbol=simple,path=path,position=pos,files_scanned=scanned,exhaustive=False));return answer
                for line in hits:
                    unit=self._unit_at(parsed,line)
                    parts=self._emit_unit(q['snapshot'],path,unit,'symbol_search_source_context','identifier_occurrence_context_not_callee_binding')if unit else[]
                    if parts:
                        self._binding=dict(status='literal_source_context_only',target_body_ids=[],semantic_truth_guaranteed=False);return []
        self.unresolved.append(dict(reason='literal_search_no_bound_target',files_scanned=scanned,exhaustive=scanned==len(candidates)))
