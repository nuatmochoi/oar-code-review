"""Source-only fallback and discoverability within predeclared dependencies.

No new external source authorities or host interpreter/module assumptions.
Unsupported languages yield exact text, not invented AST/LSP/dataflow proof.
"""
import copy,os,time
from pathlib import Path,PurePosixPath
from step_search import ContextCollector as Previous,EXTENSIONS
from context48 import digest,Limit,source_tokens

SOURCE_INSTRUCTION='''A language without AST/LSP support can still return hash-verified exact source text. Such text may answer a semantic question when the shown code is sufficient, but complete_file is not complete_function and a literal match is not a producer/consumer binding. Dependency discovery is restricted to previously declared, versioned analysis-environment authorities. A source in an allowed package remains a static candidate; actual build inclusion/dynamic dispatch may be unresolved. Read capabilities and limitations rather than equating a missing native operation with an absent implementation.'''
EXTRA_EXTENSIONS={'.svelte','.vue','.ts','.tsx','.js','.jsx','.rb','.sql','.sh','.ini','.cfg'}

class ContextCollector(Previous):
 def __init__(self,*args,initial_state=None,**kwargs):
  super().__init__(*args,initial_state=initial_state,**kwargs)
  self.dependency_catalog=copy.deepcopy((initial_state or{}).get('dependency_catalog',{}))
  for name in ['source_catalog_entries','source_catalog_seconds','fallback_source_reads']:
   self.stats[name]=(initial_state or{}).get('cost',{}).get(name,0)
 def export(self):return super().export()|dict(dependency_catalog=copy.deepcopy(self.dependency_catalog))
 def search_extensions(self):return EXTENSIONS|EXTRA_EXTENSIONS
 def authorities(self,snapshot):
  spec=self.snapshots[snapshot];values=list(spec.get('allowed_external_roots',[]))
  for profile in spec.get('native_profiles',{}).values():values.extend(profile.get('allowed_external_roots',[]))
  return list({digest(a):a for a in values}.values())
 def build_catalog(self,snapshot):
  if snapshot in self.dependency_catalog:return
  started=time.monotonic();catalog={}
  try:
   for authority in self.authorities(snapshot):
    self._check();root=Path(authority.get('root',authority.get('path','')))
    if not root.is_absolute()or not root.exists():continue
    owned=authority.get('relative_files');excluded=set(authority.get('excluded_subdirectories',[]))|{'__pycache__','.git'}
    if 'stdlib'in authority.get('kind',authority.get('origin','')):excluded|={'site-packages','dist-packages'}
    if authority.get('source_kind')=='file':paths=[root]
    elif owned is not None:paths=[root/rel for rel in owned]
    else:
     paths=[]
     for directory,dirs,names in os.walk(root):
      self._check();dirs[:]=sorted(d for d in dirs if d not in excluded)
      paths.extend(Path(directory)/n for n in sorted(names)if PurePosixPath(n).suffix.lower()in self.search_extensions())
    for p in paths:
     self._check();relative=p.relative_to(root)
     if any(part in excluded for part in relative.parts)or p.suffix.lower()not in self.search_extensions():continue
     suffix=p.name if authority.get('source_kind')=='file'or p==root else str(relative)
     alias='__external__/'+digest(authority)[:12]+'/'+suffix
     catalog[alias]=dict(absolute_source=str(p),authority_sha256=digest(authority),origin=authority.get('origin'),version=authority.get('version'),content_hash_not_yet_read=True)
     self.stats['source_catalog_entries']+=1
   self.dependency_catalog[snapshot]=catalog
  finally:self.stats['source_catalog_seconds']+=time.monotonic()-started
 def search_files(self,snapshot):
  self.build_catalog(snapshot)
  return sorted(set(super().search_files(snapshot))|set(self.dependency_catalog.get(snapshot,{})))
 def _target(self,snapshot,path):
  entry=self.dependency_catalog.get(snapshot,{}).get(path)if hasattr(self,'dependency_catalog')else None
  if entry and path not in self.external_bindings.get(snapshot,{}):
   actual=self._native_path(snapshot,dict(path=entry['absolute_source'],absolute_path=entry['absolute_source']))
   if actual!=path:raise ValueError('dependency_catalog_authority_alias_mismatch')
   self.external_bindings[snapshot][path]['assumptions']=list(self.external_bindings[snapshot][path].get('assumptions',[]))+['Discovered from declared package source inventory; this file\'s selected-build inclusion and runtime use are not established by discovery.']
  return super()._target(snapshot,path)
 def read_fallback(self,q,operation):
  data=self._read(q['snapshot'],q['file']);self.stats['fallback_source_reads']+=1
  rows=data['rows'];a,b=1,len(rows)
  if not rows:return[]
  def block(a,b,whole):
   text='\n'.join(rows[a-1:b]);e=dict(snapshot=q['snapshot'],path=q['file'],start_line=a,end_line=b,content=text,kind='source_lines',physical_rows=[dict(line=n,text=t)for n,t in enumerate(rows[a-1:b],a)],mechanical_checks=dict(complete_unit=False,complete_file=whole,source_hash_verified=True,authoritative_file_sha256=data['sha256'],range_exact=True,semantic_truth_guaranteed=False),binding={**data['binding'],'native_language_supported':False,'requested_symbol_binding':'unresolved'},provenance=dict(provider='unsupported_language_exact_source_read',file_sha256=data['sha256'],requested_operation=operation));e['id']='E-'+digest(e)[:20];return e
  whole=block(a,b,True);e=None
  if source_tokens(self.evidence+[whole])<=self.active_source_cap:e=self._admit(whole,'complete_file_text_not_complete_function_or_dataflow_proof')
  else:
   symbol=q.get('symbol','').rsplit('.',1)[-1];hits=[n for n,t in enumerate(rows,1)if symbol and symbol in t]
   line=q.get('line',1);center=min(hits,key=lambda n:abs(n-line))if hits else max(1,min(len(rows),line))
   a,b=max(1,center-35),min(len(rows),center+35);e=self._admit(block(a,b,False),'bounded_exact_text_not_complete_implementation')
   self.unresolved.append(dict(reason='unsupported_language_file_partially_delivered',file=q['file'],range=[a,b],complete_file=False))
  self._binding=dict(status='exact_source_text_without_native_symbol_binding',target_body_ids=[],runtime_dispatch_confirmed=False,semantic_truth_guaranteed=False)
  self.unresolved.append(dict(reason='native_language_capability_unavailable_source_read_fallback',file=q['file'],operation=operation,source_read_supported=True,AST=False,LSP=False,dataflow=False))
  self.events.append(dict(event='language_capability_source_fallback',file=q['file'],operation=operation,delivered_evidence_ids=[e['id']]if e else[],complete_file=bool(e and e['mechanical_checks']['complete_file']),call_or_dataflow_proven=False))
  return[e]if e else[]
 def _query_body(self,q):
  return self.read_fallback(q,'body')if self.language_for(q['file'])is None else super()._query_body(q)
 def _lookup(self,q,operation):
  return self.read_fallback(q,operation)if self.language_for(q['file'])is None else super()._lookup(q,operation)
 def _flow(self,q):
  return self.read_fallback(q,'flow')if self.language_for(q['file'])is None else super()._flow(q)
