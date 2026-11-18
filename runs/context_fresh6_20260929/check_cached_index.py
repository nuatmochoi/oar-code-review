import ast,json,sys
from functools import lru_cache
from pathlib import Path
R=Path(__file__).resolve().parent;sys.path.insert(0,str(R.parents[1]))
from ace_review.context_adaptive import digest
from ace_review.python_ast_graph import build_python_ast_graph
from ace_review.delta_scip import changed_ranges
case=json.loads((R/'index_preflight_before_overlap_exclusion/cases/F01.json').read_text())
reference=case['worlds']['a'];source=reference['versions']['base']
choices=[R/'pairs/F01/snapshots'/f'{w}_base' for w in ('safe','unsafe')]
snapshot=next(p for p in choices if all((p/f).read_text()==s for f,s in source.items()))
ranges=changed_ranges(case['diff'])['base']
from index_acceleration import install
install()
g=build_python_ast_graph(snapshot,revision='base',changed_files=ranges,changed_lines=ranges)
spec={'nodes':{n.id:{'file':n.file,'start':n.start_line,'end':n.end_line} for n in g.nodes if n.start_line is not None and n.end_line is not None},'edges':[{'source':e.source,'target':e.target,'kind':e.type} for e in g.edges if e.resolved and e.confidence>0],'audit':dict(g.metadata,source_package_prefix='')}
assert spec==reference['graphs']['base']
r={'valid':True,'cached_graph_sha256':digest(spec),'uncached_graph_sha256':digest(reference['graphs']['base']),'source_files':len(source),'cache':str(ast._splitlines_no_ff.cache_info()),'memoized_function':'CPython ast._splitlines_no_ff; graph validation uses set membership','python':sys.version}
(R/'index_cache_equivalence.json').write_text(json.dumps(r,indent=2));print(json.dumps(r))
