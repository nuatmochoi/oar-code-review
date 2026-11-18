import json,sys
from pathlib import Path
R=Path(__file__).resolve().parent;sys.path.insert(0,str(R.parents[1]))
from ace_review.evidence_graph import EvidenceGraph,EvidenceNode,EvidenceEdge
from index_acceleration import validate_graph_with_set
old=EvidenceGraph.__post_init__
a=EvidenceNode('a','function','consumer','x.py');b=EvidenceNode('b','function','callee','y.py')
e=EvidenceEdge('a','b','calls');missing=EvidenceEdge('a','c','calls')
cases=[('valid',[a,b],[e]),('duplicate_node',[a,a],[]),('unknown_endpoint',[a,b],[missing]),('duplicate_edge',[a,b],[e,e]),('empty',[],[])]
records=[]
for name,nodes,edges in cases:
 outcomes=[]
 for impl in [old,validate_graph_with_set]:
  EvidenceGraph.__post_init__=impl
  try:outcomes.append({'graph':EvidenceGraph(nodes,edges).to_dict()})
  except ValueError as exc:outcomes.append({'error':str(exc)})
 assert outcomes[0]==outcomes[1],name
 records.append({'case':name,'equivalent':True,'outcome':outcomes[0]})
EvidenceGraph.__post_init__=old
(R/'index_validation_equivalence.json').write_text(json.dumps({'valid':True,'checks':records},indent=2));print('5 graph validation equivalence checks passed')
