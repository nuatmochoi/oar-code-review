"""Index-only, semantics-preserving memoization and membership validation."""
import ast
from functools import lru_cache
from ace_review.evidence_graph import EvidenceGraph

def validate_graph_with_set(self):
    object.__setattr__(self,'nodes',tuple(self.nodes))
    object.__setattr__(self,'edges',tuple(self.edges))
    ids=[node.id for node in self.nodes]
    known=set(ids)
    if len(ids)!=len(known):
        raise ValueError('duplicate evidence node id')
    unknown=[edge for edge in self.edges if edge.source not in known or edge.target not in known]
    if unknown:
        raise ValueError('evidence edge references an unknown node')
    keys=[edge.key for edge in self.edges]
    if len(keys)!=len(set(keys)):
        raise ValueError('duplicate evidence edge')

def install():
    ast._splitlines_no_ff=lru_cache(maxsize=128)(ast._splitlines_no_ff)
    EvidenceGraph.__post_init__=validate_graph_with_set
