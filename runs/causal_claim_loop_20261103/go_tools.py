"""Question-directed, hash-bound Go source/LSP/lexical-flow evidence.

No labels, prior model answers or privileged path lists are read. Static source
identity, lexical relations and runtime/environment binding stay distinct.
"""
from pathlib import Path, PurePosixPath
import hashlib,json,os,re,subprocess,time,urllib.parse
from go_flow.native_rpc import NativeRpc

ROOT=Path(__file__).resolve().parent
FLOW=ROOT/'go_flow/go-flow'
GOPLS=ROOT.parents[1]/'tools/bin/gopls'
DEFAULTS=dict(max_native_calls=48,max_native_seconds=180,max_file_reads=256,max_query_seconds=20,max_wall_seconds=900,max_body_bytes=200000,max_file_bytes=16000000)
def digest(v):return hashlib.sha256(json.dumps(v,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def physical(text):
    rows=text.split('\n') if text else []
    if rows and rows[-1]=='':rows.pop()
    return [r.removesuffix('\r') for r in rows]
def parse_file(path,expected_sha=None,timeout=20):
    """Public preparation API. Returned _cost includes the physical parser run."""
    path=Path(path).resolve();start=time.monotonic()
    p=subprocess.run([str(FLOW)],input=json.dumps({'path':str(path)}),text=True,capture_output=True,timeout=timeout,check=True)
    value=json.loads(p.stdout)
    if expected_sha and value['sha256']!=expected_sha:raise ValueError('parser_source_hash_mismatch')
    value['_cost']={'native_calls':1,'ast_invocations':1,'file_reads':1,'native_seconds':time.monotonic()-start}
    return value
def _relative(value):
    p=PurePosixPath(str(value));
    if p.is_absolute() or '..' in p.parts:raise ValueError('non_relative_source_path')
    return str(p)
def _delta_lines(text):
    out={'BASE':{},'HEAD':{}};oldpath=newpath=None;a=b=0
    for row in physical(text):
        if row.startswith('--- '):
            oldpath=row[4:].removeprefix('a/').split('\t')[0]
            if oldpath=='/dev/null':oldpath=None
        elif row.startswith('+++ '):
            newpath=row[4:].removeprefix('b/').split('\t')[0]
            if newpath=='/dev/null':newpath=None
        elif row.startswith('@@'):
            m=re.match(r'@@ -(\d+)(?:,\d+)? \+(\d+)',row)
            if m:a,b=map(int,m.groups())
        elif row.startswith('-') and not row.startswith('---'):
            if oldpath:out['BASE'].setdefault(oldpath,set()).add(a)
            if newpath:out['HEAD'].setdefault(newpath,set()).add(max(1,b))
            a+=1
        elif row.startswith('+') and not row.startswith('+++'):
            if newpath:out['HEAD'].setdefault(newpath,set()).add(b)
            if oldpath:out['BASE'].setdefault(oldpath,set()).add(max(1,a))
            b+=1
        elif row.startswith(' '):a+=1;b+=1
    return out

class GoTools:
    def __init__(self,case,out_dir,budgets):
        self.case=case;self.out=Path(out_dir).resolve();self.out.mkdir(parents=True,exist_ok=True)
        self.budgets={**DEFAULTS,**(budgets or {})};self.started=time.monotonic();self.seq=0
        self.totals=dict(native_calls=0,native_rpc_requests=0,ast_invocations=0,environment_calls=0,native_seconds=0.0,file_reads=0,body_reads=0,source_bytes=0,query_seconds=0.0)
        self.delivered={k:0 for k in self.totals};self.sessions={};self.accounted={};self.cache={};self.texts={};self.raw=[];self.reported_raw=0;self.seed_unresolved=[]
    def _guard(self,native=0):
        if time.monotonic()-self.started>self.budgets['max_wall_seconds']:raise RuntimeError('wall_budget_exhausted')
        if self.totals['native_calls']+native+len(self.sessions)>self.budgets['max_native_calls']:raise RuntimeError('native_call_budget_exhausted_including_reserved_shutdown')
        if self.totals['native_seconds']>=self.budgets['max_native_seconds']:raise RuntimeError('native_time_budget_exhausted')
    def _timeout(self):
        return max(.001,min(self.budgets['max_query_seconds'],self.budgets['max_native_seconds']-self.totals['native_seconds'],self.budgets['max_wall_seconds']-(time.monotonic()-self.started)))
    def _authority(self,snapshot,rel):
        rel=_relative(rel);s=self.case['snapshots'][snapshot]
        if rel in s['files']:
            p=(Path(s['root'])/rel).resolve()
            if not p.is_relative_to(Path(s['root']).resolve()):raise ValueError('source_path_escape')
            return p,s['files'][rel],{'status':'snapshot_source_identity_only','runtime_dispatch':'unresolved','environment':self.case.get('environment',{})}
        e=s.get('external_files',{}).get(rel)
        if e:
            return Path(e['absolute_source']).resolve(),e['sha256'],{'status':e.get('binding_status','assumption'),'runtime_dispatch':'unresolved','origin':e.get('origin'),'version':e.get('version'),'binding_evidence':e.get('binding_evidence',[]),'assumptions':e.get('assumptions',[])}
        raise ValueError('source_outside_allowed_pool')
    def _load(self,snapshot,rel):
        key=snapshot,rel
        self._guard()
        if self.totals['file_reads']>=self.budgets['max_file_reads']:raise RuntimeError('file_read_budget_exhausted')
        if key not in self.texts:
            p,h,b=self._authority(snapshot,rel);raw=p.read_bytes();self.totals['file_reads']+=1
            if len(raw)>self.budgets['max_file_bytes']:raise RuntimeError('file_byte_limit')
            if hashlib.sha256(raw).hexdigest()!=h:raise ValueError('source_hash_mismatch')
            self.texts[key]=(raw,p,h,b)
        else:
            raw,p,h,b=self.texts[key]
            current=p.read_bytes();self.totals['file_reads']+=1
            if hashlib.sha256(current).hexdigest()!=h:raise ValueError('source_changed_after_first_read')
        return self.texts[key]
    def _parse(self,snapshot,rel):
        key=snapshot,rel
        if key not in self.cache:
            raw,p,h,b=self._load(snapshot,rel);self._guard(1);start=time.monotonic()
            if self.totals['file_reads']>=self.budgets['max_file_reads']:raise RuntimeError('file_read_budget_exhausted')
            self.totals['native_calls']+=1;self.totals['ast_invocations']+=1;self.totals['file_reads']+=1
            try:
                self.cache[key]=parse_file(p,h,self._timeout())
                artifact=self.out/f'ast-{snapshot}-{digest(rel)[:12]}.json';artifact.write_text(json.dumps(self.cache[key],ensure_ascii=False,indent=2)+'\n');self.raw.append(str(artifact))
            finally:self.totals['native_seconds']+=time.monotonic()-start
        value=self.cache[key]
        if value.get('errors'):raise RuntimeError('go_parse_error:'+str(value['errors']))
        return value
    def parse_file(self,snapshot,file):return self._parse(snapshot,file)
    def _source(self,snapshot,rel,start,end,kind='implementation_body',binding=None):
        parsed=self._parse(snapshot,rel);raw,p,h,basebinding=self._load(snapshot,rel)
        for a,b in reversed(parsed['comments']):raw=raw[:a]+bytes(10 if c==10 else 32 for c in raw[a:b])+raw[b:]
        lines=physical(raw.decode())
        if not 1<=start<=end<=len(lines):raise ValueError('source_range_outside_file')
        content='\n'.join(lines[start-1:end])
        if len(content.encode())>self.budgets['max_body_bytes']:raise RuntimeError('whole_body_exceeds_explicit_byte_limit_no_truncation')
        self.totals['body_reads']+=1;self.totals['source_bytes']+=len(content.encode())
        e=dict(snapshot=snapshot,path=rel,start_line=start,end_line=end,content=content,kind=kind,
            provenance={'provider':'GoTools','file_sha256':h,'absolute_source':str(p),'source_representation':'legacy-compatible-comment-byte-mask-physical-lines-v1'},
            mechanical_checks={'source_hash_verified':True,'range_exact':True,'truncated':False,'complete_unit':kind=='implementation_body' and any(u['start_line']==start and u['end_line']==end for u in parsed['units']),'mask_policy':'legacy-compatible-comment-byte-mask-physical-lines-v1','semantic_truth_guaranteed':False},binding={**basebinding,**(binding or {})})
        e['id']='E-'+digest(e)[:16];return e
    def _unit(self,parsed,line):
        units=[u for u in parsed['units'] if u['start_line']<=line<=u['end_line']]
        return min(units,key=lambda u:u['end_offset']-u['start_offset']) if units else None
    def _native_path(self,snapshot,absolute):
        absolute=Path(absolute).resolve();s=self.case['snapshots'][snapshot];root=Path(s['root']).resolve()
        if absolute.is_relative_to(root):
            rel=str(absolute.relative_to(root))
            if rel in s['files']:return rel
        for rel,e in s.get('external_files',{}).items():
            origins=[e['absolute_source']]
            if isinstance(e.get('origin'),str):origins.append(e['origin'])
            if str(absolute) in {str(Path(p).resolve()) for p in origins}:return rel
        return None
    def _module(self,snapshot,rel):
        s=self.case['snapshots'][snapshot];p=PurePosixPath(rel).parent
        while True:
            candidate=str(p/'go.mod')
            if candidate in s['files']:return Path(s['root'])/str(p)
            if str(p)=='.':break
            p=p.parent
        raise RuntimeError('no_bound_go_module_for_file')
    def _account(self,key):
        rpc=self.sessions[key];old=self.accounted.get(key,0);new=rpc.requests[old:]
        self.totals['native_calls']+=len(new);self.totals['native_rpc_requests']+=len(new)
        self.totals['native_seconds']+=sum(r['duration_seconds'] for r in new);self.accounted[key]=len(rpc.requests)
    def _rpc(self,snapshot,rel,kind,line,column):
        module=self._module(snapshot,rel).resolve();key=(snapshot,str(module))
        if key not in self.sessions:
            self._guard(2)
            self.sessions[key]=NativeRpc(GOPLS,module,timeout=self._timeout());self._account(key)
        rpc=self.sessions[key]
        if rpc.initialize_error:return {'error':{'message':'gopls_initialize_failed','detail':rpc.initialize_error},'result':None,'capabilities':rpc.capabilities}
        cap={'definition':'definitionProvider','references':'referencesProvider','implementation':'implementationProvider'}[kind]
        if not rpc.capabilities.get(cap):return {'error':{'message':'unsupported_capability','capability':cap},'result':None,'capabilities':rpc.capabilities}
        self._guard(1);p=self._load(snapshot,rel)[1]
        if self.totals['file_reads']>=self.budgets['max_file_reads']:raise RuntimeError('file_read_budget_exhausted')
        self.totals['file_reads']+=1
        rpc.timeout=self._timeout()
        try:result=rpc.query(kind,p,line,column)
        finally:self._account(key)
        return {**result,'capabilities':rpc.capabilities,'binding':'static_LSP_location_not_runtime_dispatch'}
    def _locations(self,snapshot,result):
        found=[];unresolved=[]
        if result.get('error'):return [],[{'kind':'native_error','error':result['error']}]
        rows=result.get('result') or [];rows=rows if isinstance(rows,list) else [rows]
        for row in rows:
            row=row.get('location',row);uri=row.get('targetUri',row.get('uri'));span=row.get('targetSelectionRange',row.get('targetRange',row.get('range')))
            if not uri or not span:continue
            path=urllib.parse.unquote(urllib.parse.urlparse(uri).path);rel=self._native_path(snapshot,path)
            if rel is None:unresolved.append({'kind':'native_target_outside_allowed_pool','native_path':path});continue
            found.append((rel,span['start']['line']+1,span['start']['character']))
        if len(found)>1:unresolved.append({'kind':'multiple_static_candidates','count':len(found),'runtime_dispatch':'unresolved'})
        return sorted(set(found)),unresolved
    def _position(self,snapshot,rel,q):
        raw,p,h,b=self._load(snapshot,rel);lines=physical(raw.decode());line=int(q.get('line') or 0)
        if not 1<=line<=len(lines):raise ValueError('question_needs_valid_actual_line')
        name=str(q.get('symbol','')).rsplit('.',1)[-1].removesuffix('()')
        identifiers=[n for n in self._parse(snapshot,rel)['identifiers'] if n['start_line']==line]
        if q.get('column'):
            column=int(q['column']);matches=[n for n in identifiers if n['start_column']==column and (not name or n['code']==name)]
            if len(matches)!=1:raise ValueError('explicit_position_is_not_matching_AST_identifier_token')
            return line,column
        if name:
            matches=[n for n in identifiers if n['code']==name]
            if len(matches)==1:return line,matches[0]['start_column']
            if len(matches)>1:raise ValueError('ambiguous_symbol_occurrences_need_column')
        calls=[c for u in self._parse(snapshot,rel)['units'] for c in u['calls'] if c['line']==line]
        if len(calls)==1:return line,calls[0]['column']
        raise ValueError('question_position_unresolved_no_arbitrary_token_choice')
    def _body(self,q):
        s=q.get('snapshot','HEAD');rel=q['file'];p=self._parse(s,rel);u=self._unit(p,int(q.get('line') or 1))
        if not u:raise ValueError('no_enclosing_function_body')
        name=str(q.get('symbol','')).rsplit('.',1)[-1].removesuffix('()')
        mismatch=bool(name and name!=u['name'])
        return [self._source(s,rel,u['start_line'],u['end_line'],'source_lines' if mismatch else 'implementation_body',binding={'relation':'explicit_enclosing_body_read','requested_symbol_matches_unit_name':not mismatch,'target_definition_resolved':bool(name and not mismatch)})],[{'kind':'explicit_enclosing_body_not_requested_symbol_definition','requested_symbol':name,'actual_unit':u['name']}] if mismatch else []
    def _function_at_native_name(self,snapshot,path,parsed,line,character):
        # LSP character is UTF-16; AST columns are UTF-8 bytes. Only a native
        # selection at a real declared function-name token resolves a body.
        raw=self._load(snapshot,path)[0];rows=physical(raw.decode())
        if not 1<=line<=len(rows):return None
        for unit in parsed['units']:
            span=unit.get('name_span')
            if not span or span['start_line']!=line:continue
            prefix=rows[line-1].encode()[:span['start_column']-1].decode()
            start=len(prefix.encode('utf-16-le'))//2
            width=len(unit['name'].encode('utf-16-le'))//2
            if start<=character<start+width:return unit
        return None
    def _lookup(self,q):
        s=q.get('snapshot','HEAD');rel=q['file'];kind=q['query'];line,column=self._position(s,rel,q)
        result=self._rpc(s,rel,kind,line,column);self.last_native.append(result)
        candidates,unresolved=self._locations(s,result);evidence=[]
        limit=int(self.budgets.get('max_native_locations',8))
        if len(candidates)>limit:unresolved.append({'kind':'candidate_fanout_limit','returned':len(candidates),'admitted':limit})
        for path,n,character in candidates[:limit]:
            parsed=self._parse(s,path);u=self._function_at_native_name(s,path,parsed,n,character)
            wants_body=q.get('kind')!='definition_location' and kind!='references'
            start=end=n;source_kind='source_lines'
            if wants_body and u:start,end=u['start_line'],u['end_line'];source_kind='implementation_body'
            elif wants_body:unresolved.append({'kind':'native_definition_not_function_name_token','path':path,'line':n,'body_resolution':'unresolved'})
            target_row=physical(self._load(s,path)[0].decode())[n-1]
            target_column=len(target_row.encode('utf-16-le')[:character*2].decode('utf-16-le').encode())+1
            binding={'relation':'static_reference_candidate' if kind=='references' else 'static_'+kind+'_candidate','runtime_dispatch':'unresolved','native_query':{'file':rel,'line':line,'column':column},'native_target':{'line':n,'utf8_column':target_column,'utf16_character':character},'multiple_candidates':len(candidates)>1,'definition_function_name_verified':bool(u)}
            evidence.append(self._source(s,path,start,end,source_kind,binding))
            metadata=dict(snapshot=s,path=path,start_line=n,end_line=n,kind='reference_edge' if kind=='references' else 'definition_location',content=json.dumps({'native_query':binding['native_query'],'target':{'path':path,'line':n,'utf16_character':character},'relation':binding['relation']},ensure_ascii=False),provenance={'provider':'gopls'},mechanical_checks={'native_location_bound_to_allowed_source':True,'semantic_truth_guaranteed':False},binding=binding)
            metadata['id']='E-'+digest(metadata)[:16];evidence.append(metadata)
        if not candidates:unresolved.append({'kind':'no_bound_native_locations','query':kind})
        return evidence,unresolved
    def _flow(self,q):
        s=q.get('snapshot','HEAD');rel=q['file'];parsed=self._parse(s,rel);u=self._unit(parsed,int(q.get('line') or 1))
        if not u:raise ValueError('no_enclosing_flow_unit')
        name=str(q.get('symbol','')).rsplit('.',1)[-1];seed=set();selected=[]
        for fact in u['facts']:
            if fact['start_line']<=int(q.get('line') or 1)<=fact['end_line'] or any(b['name']==name for b in fact['from']+fact['to']):seed.update(b['id'] for b in fact['from']+fact['to'])
        for _ in range(3):
            for fact in u['facts']:
                if seed.intersection(b['id'] for b in fact['from']+fact['to']) or fact['start_line']<=int(q.get('line') or 1)<=fact['end_line']:
                    if fact not in selected:selected.append(fact)
                    seed.update(b['id'] for b in fact['from']+fact['to'])
        fact_count=len(selected);selected=selected[:32];evidence=[];unresolved=[{'kind':'flow_scope','detail':x} for x in parsed['limitations']];graph=[]
        if fact_count>32:unresolved.append({'kind':'flow_fact_limit','returned':fact_count,'admitted':32})
        def source_span(path,start,end,relation):
            e=self._source(s,path,start,end,'source_lines',{'relation':relation,'runtime_dispatch':'unresolved'})
            if not any(a['id']==e['id'] for a in evidence):evidence.append(e)
        for fact in selected:
            source_span(rel,fact['start_line'],fact['end_line'],fact['relation']);graph.append({k:v for k,v in fact.items() if k!='code'})
        calls=[c for c in u['calls'] if c['start_line']<=int(q.get('line') or 1)<=c['end_line'] or seed.intersection(b['id'] for bs in c['arg_bindings'] for b in bs) or any(b['id'] in seed for b in c['assigned'])]
        for call in calls[:4]:
            source_span(rel,call['start_line'],call['end_line'],'call_site_syntax');targets=[]
            if call['local_target']:
                target=next((x for x in parsed['units'] if x['id']==call['local_target']),None)
                if target:targets=[(rel,target)]
            else:
                result=self._rpc(s,rel,'definition',call['line'],call['column']);self.last_native.append(result)
                locs,errors=self._locations(s,result);unresolved.extend(errors)
                for path,line,character in locs[:4]:
                    unit=self._function_at_native_name(s,path,self._parse(s,path),line,character)
                    if unit:targets.append((path,unit))
                    else:unresolved.append({'kind':'callee_definition_not_a_proven_function_target','path':path,'line':line,'syntax_resolution':call['resolution']})
            if not targets:unresolved.append({'kind':'unresolved_callee','name':call['name'],'line':call['line'],'reason':call['resolution']})
            for path,target in targets:
                evidence.append(self._source(s,path,target['start_line'],target['end_line'],'implementation_body',{'relation':'static_callee_candidate','resolution':call['resolution'],'runtime_dispatch':'unresolved'}))
                if not target['variadic'] and len(call['args'])==len(target['params']):
                    graph.extend({'relation':'argument_to_parameter','caller':{'path':rel,'line':call['line']},'callee':{'path':path,'line':target['start_line']},'slot':i,'from':call['arg_bindings'][i],'to':[p],'binding':'static_positional_candidate','runtime_dispatch':'unresolved'} for i,p in enumerate(target['params']))
                else:unresolved.append({'kind':'arity_variadic_or_method_expression_unresolved','line':call['line']})
                returns=[f for f in target['facts'] if f['relation']=='return_value']
                for ret in returns:
                    if ret['slot']<len(call['assigned']):graph.append({'relation':'return_to_assignment','from_path':path,'from_line':ret['start_line'],'from':ret['from'],'to_path':rel,'to':call['assigned'][ret['slot']],'slot':ret['slot'],'path_feasibility':'unresolved','runtime_dispatch':'unresolved'})
        if len(calls)>4:unresolved.append({'kind':'flow_call_fanout_limit','returned':len(calls),'admitted':4})
        metadata=dict(snapshot=s,path=rel,start_line=u['start_line'],end_line=u['end_line'],kind='value_path',content=json.dumps({'facts':graph,'analysis':'lexical/static candidates, not runtime/path/heap proof'},ensure_ascii=False),provenance={'provider':'go_parser_ast','file_sha256':parsed['sha256']},mechanical_checks={'source_binding_checked':True,'semantic_truth_guaranteed':False},binding={'runtime_dispatch':'unresolved'})
        metadata['id']='E-'+digest(metadata)[:16];evidence.append(metadata)
        return evidence,unresolved
    def _environment(self,q):
        ref=self.case.get('environment_reference');summary=self.case.get('environment',{})
        if ref:
            if self.totals['file_reads']>=self.budgets['max_file_reads']:raise RuntimeError('file_read_budget_exhausted')
            raw=Path(ref['path']).read_bytes();self.totals['file_reads']+=1
            if hashlib.sha256(raw).hexdigest()!=ref['sha256']:raise ValueError('environment_reference_hash_mismatch')
            summary=json.loads(raw)
        e=dict(snapshot=q.get('snapshot','HEAD'),path='__environment__/record.json',start_line=1,end_line=1,kind='dependency_version',content=json.dumps(summary,ensure_ascii=False),provenance={'provider':'frozen_environment_record','reference':ref},mechanical_checks={'reference_hash_verified':bool(ref),'environment_reference_sha256':ref['sha256'] if ref else None,'semantic_truth_guaranteed':False},binding={'status':'recorded_environment_not_runtime_observation'})
        e['id']='E-'+digest(e)[:16]
        return [e],[{'kind':'runtime_observation_not_performed','note':'A source/version record is not deployment or runtime dispatch proof'}]
    def _cost(self):
        delta={k:self.totals[k]-self.delivered[k] for k in self.totals};self.delivered=dict(self.totals)
        delta['scope']='incremental; native_calls=LSP RPC lifecycle plus AST/explicit environment CLI; file_reads=counted source validation/parser reads, native package-internal I/O unknown; startup not double-counted'
        return delta
    def seed_questions(self,case=None):
        changes=_delta_lines(self.case.get('diff',''));questions=[]
        for snapshot,path,lines in [(s,p,n) for s in ('HEAD','BASE') for p,n in sorted(changes[s].items())]:
            if not path.endswith('.go') or path not in self.case['snapshots'][snapshot]['files']:continue
            try:parsed=self._parse(snapshot,path)
            except Exception as error:self.seed_unresolved.append({'kind':'seed_parse_failure','snapshot':snapshot,'path':path,'error':str(error)});continue
            for u in parsed['units']:
                for call in u['calls']:
                    if any(call['start_line']<=n<=call['end_line'] for n in lines):questions.append((0,call['start_line'],dict(kind='implementation_body',query='definition',snapshot=snapshot,file=path,line=call['line'],column=call['column'],symbol=call['name'],reason='Inspect actual behavior of a changed call/use or opposite-snapshot edit boundary; no defect is presumed.')))
                for fact in u['facts']:
                    if any(fact['start_line']<=n<=fact['end_line'] for n in lines):questions.append((1 if fact['relation']=='call_result_assignment' else 2,fact['start_line'],dict(kind='value_path',query='flow',snapshot=snapshot,file=path,line=fact['start_line'],symbol=(fact['to'] or fact['from'] or [{'name':''}])[0]['name'],reason='Inspect the changed assignment/return value path or opposite-snapshot boundary without assuming a defect.')))
                for condition in u['conditions']:
                    if any(condition['start_line']<=n<=condition['end_line'] for n in lines):questions.append((3,condition['start_line'],dict(kind='exception_branch',query='body',snapshot=snapshot,file=path,line=condition['start_line'],symbol=u['name'],reason='Inspect the changed condition or opposite-snapshot boundary and enclosing branches; no failure is presumed.')))
        result=[];seen=set()
        # Round-robin question categories before filling leftover slots. This
        # avoids allowing four nested call slots to hide every changed value or
        # guard question; it is source-only and independent of case/defect IDs.
        categories={p:[] for p in sorted({p for p,_,_ in questions})}
        for p,n,q in sorted(questions,key=lambda x:(x[0],x[2]['snapshot']!='HEAD',x[2]['file'],x[1],x[2].get('column',0),x[2]['query'])):categories[p].append(q)
        ordered=[]
        while any(categories.values()):
            for values in categories.values():
                if values:ordered.append(values.pop(0))
        for q in ordered:
            key=q['snapshot'],q['file'],q['line'],q['query'],q['symbol']
            if key in seen:continue
            seen.add(key);q.update(id='Q-seed-'+digest(q)[:12],claim_id='exploration');result.append(q)
            if len(result)==4:break
        return result
    def query(self,q):
        self.seq+=1;start=time.monotonic();self.last_native=[];evidence=[];unresolved=[]
        try:
            self._guard();kind=q['query']
            if kind in ('definition','references','implementation'):evidence,unresolved=self._lookup(q)
            elif kind=='body':evidence,unresolved=self._body(q)
            elif kind=='flow':evidence,unresolved=self._flow(q)
            elif kind=='environment':evidence,unresolved=self._environment(q)
            else:unresolved=[{'kind':'unsupported_query','query':kind}]
        except Exception as error:unresolved.append({'kind':'query_error','error':f'{type(error).__name__}: {error}'})
        self.totals['query_seconds']+=time.monotonic()-start
        p=self.out/f'query-{self.seq:04d}.json'
        output=dict(evidence=evidence,unresolved=unresolved,raw_paths=self.raw[self.reported_raw:]+[str(p)],cost=self._cost())
        p.write_text(json.dumps({'question':q,'native':self.last_native,'result':output},ensure_ascii=False,indent=2)+'\n');self.raw.append(str(p));self.reported_raw=len(self.raw)
        return output
    def close(self):
        paths=[]
        for i,(key,rpc) in enumerate(self.sessions.items()):
            closed=rpc.close();self._account(key);p=self.out/f'native-session-{i:02d}.json';p.write_text(json.dumps({'snapshot':key[0],'module':key[1],'capabilities':rpc.capabilities,**closed},ensure_ascii=False,indent=2)+'\n');paths.append(str(p))
        self.sessions={};pending=self.raw[self.reported_raw:]+paths;self.reported_raw=len(self.raw)
        return {'cost':self._cost(),'raw_paths':pending,'unresolved':self.seed_unresolved}
