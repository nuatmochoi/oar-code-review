"""Compute an auditable delta between normalized base/head semantic graphs."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from .evidence_graph import EvidenceEdge, EvidenceGraph, EvidenceNode


@dataclass(frozen=True)
class SemanticDelta:
    seed_ids: tuple[str, ...]
    added_nodes: tuple[EvidenceNode, ...]
    removed_nodes: tuple[EvidenceNode, ...]
    modified_nodes: tuple[str, ...]
    added_edges: tuple[EvidenceEdge, ...]
    removed_edges: tuple[EvidenceEdge, ...]
    head_graph: EvidenceGraph

    def to_dict(self) -> dict[str, Any]:
        return {
            "seed_ids": list(self.seed_ids),
            "added_nodes": [node.to_dict() for node in self.added_nodes],
            "removed_nodes": [node.to_dict() for node in self.removed_nodes],
            "modified_nodes": list(self.modified_nodes),
            "added_edges": [edge.to_dict() for edge in self.added_edges],
            "removed_edges": [edge.to_dict() for edge in self.removed_edges],
            "head_graph": self.head_graph.to_dict(),
        }


def _node_signature(node: EvidenceNode) -> tuple[Any, ...]:
    value = node.to_dict()
    value.pop("revision", None)
    return tuple(sorted((key, repr(item)) for key, item in value.items()))


def _edge_signature(edge: EvidenceEdge) -> tuple[Any, ...]:
    value = edge.to_dict()
    value.pop("revision", None)
    return tuple(sorted((key, repr(item)) for key, item in value.items()))


def compute_semantic_delta(
    base: EvidenceGraph, head: EvidenceGraph, *, seed_ids: Iterable[str] | None = None,
) -> SemanticDelta:
    base_nodes, head_nodes = base.by_id, head.by_id
    shared = base_nodes.keys() & head_nodes.keys()
    modified = tuple(sorted(node_id for node_id in shared
                            if _node_signature(base_nodes[node_id]) != _node_signature(head_nodes[node_id])))
    base_edge_signatures = {_edge_signature(edge): edge for edge in base.edges}
    head_edge_signatures = {_edge_signature(edge): edge for edge in head.edges}
    inferred_seeds = {node.id for node in head.nodes if node.changed}
    seeds = set(seed_ids) if seed_ids is not None else inferred_seeds
    unknown = seeds - set(head.node_ids)
    if unknown:
        raise ValueError(f"unknown head seed ids: {sorted(unknown)}")
    return SemanticDelta(
        seed_ids=tuple(sorted(seeds)),
        added_nodes=tuple(head_nodes[node_id] for node_id in sorted(head_nodes.keys() - base_nodes.keys())),
        removed_nodes=tuple(base_nodes[node_id] for node_id in sorted(base_nodes.keys() - head_nodes.keys())),
        modified_nodes=modified,
        added_edges=tuple(head_edge_signatures[key] for key in sorted(head_edge_signatures.keys() - base_edge_signatures.keys())),
        removed_edges=tuple(base_edge_signatures[key] for key in sorted(base_edge_signatures.keys() - head_edge_signatures.keys())),
        head_graph=head,
    )
