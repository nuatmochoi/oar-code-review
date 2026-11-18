"""Bounded literal search inside the existing frozen source pool, no new roots."""
from pathlib import PurePosixPath
from context48 import ContextCollector as BaseCollector, Limit

class ContextCollector(BaseCollector):
    def __init__(self,case,*args,**kwargs):
        import copy
        super().__init__(copy.deepcopy(case),*args,**kwargs)
        from context48 import ROOT,PROJECT
        reference=ROOT/'audit/cache_clone.json'
        if reference.exists():
            for spec in self.snapshots.values():
                spec.setdefault('source_environment_aliases',[]).append(dict(actual_root=str(ROOT/'cache/go-mod'),canonical_root=str(PROJECT/'runs/reviewbench48_ablation_20261103/native_validation/cache/go-mod'),identity_reference={'path':str(reference),'sha256':__import__('context48').sha(reference)}))
    def _search(self,q):
        needle=q.get('symbol','')
        if not isinstance(needle,str) or not 2<=len(needle)<=120 or '\n' in needle:
            raise ValueError('search_requires_bounded_literal_symbol')
        snap=q['snapshot']; hint=q['file']; self._target(snap,hint)
        files=self.snapshots[snap].get('files',{})
        extensions={'.py','.pyi','.go','.md','.rst','.txt','.yaml','.yml','.json','.toml','.ini','.cfg','.sql','.sh','.ts','.tsx','.js','.jsx'}
        candidates=[p for p in files if PurePosixPath(p).suffix.lower() in extensions]
        parent=str(PurePosixPath(hint).parent); changed=set(self.changed.get(snap,{}))
        candidates.sort(key=lambda p:(p!=hint,str(PurePosixPath(p).parent)!=parent,p not in changed,p))
        scanned=0; matches=0; seen=set()
        for path in candidates:
            if scanned>=32 or matches>=12: break
            self._check(); data=self._read(snap,path); scanned+=1
            hits=[n for n,t in enumerate(data['rows'],1) if needle in t]
            if not hits: continue
            parsed=None
            if self.language_for(path):
                try: parsed=self._parse(snap,path)
                except (ValueError,SyntaxError,RuntimeError,Limit): pass
            for line in hits:
                if matches>=12: break
                unit=self._unit_at(parsed,line) if parsed else None
                key=(path,unit['start_line'],unit['end_line']) if unit else (path,max(1,line-5),min(len(data['rows']),line+5))
                if key in seen: continue
                seen.add(key); matches+=1
                self._metadata(snap,path,line,'reference_edge',{'query':'literal_search','literal':needle,'match_line':line,'static_candidate_only':True,'call_or_dataflow_proven':False},'bounded_repository_literal_search')
                if unit: self._emit_unit(snap,path,unit,'literal_search_candidate','literal_match_not_execution_edge')
                else:
                    rows=data['rows'][key[1]-1:key[2]]
                    e=dict(snapshot=snap,path=path,start_line=key[1],end_line=key[2],content='\n'.join(rows),kind='source_lines',physical_rows=[{'line':n,'text':t}for n,t in enumerate(rows,key[1])],mechanical_checks={'complete_unit':False,'source_hash_verified':True,'semantic_truth_guaranteed':False},binding=data['binding'],provenance={'provider':'bounded_literal_search','file_sha256':data['sha256']})
                    from context48 import digest
                    e['id']='E-'+digest(e)[:20]
                    self._admit(e,'literal_match_lines_not_complete_implementation')
        self.events.append({'event':'bounded_search_completed','literal':needle,'files_scanned':scanned,'candidates_total':len(candidates),'match_units':matches,'scan_complete':scanned==len(candidates),'semantic_truth_guaranteed':False})
        if not matches:self.unresolved.append({'reason':'literal_search_no_match_in_scanned_subset','files_scanned':scanned,'candidate_files':len(candidates)})

