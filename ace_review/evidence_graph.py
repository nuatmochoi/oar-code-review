"""Backend-neutral typed evidence graph used by provenance experiments."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Iterable


@dataclass(frozen=True)
class EvidenceNode:
    id: str
    kind: str
    role: str
    file: str
    start_line: int | None = None
    end_line: int | None = None
    revision: str = "head"
    symbol: str = ""
    content: str = ""
    token_cost: int = 0
    changed: bool = False
    confidence: float = 1.0
    attributes: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("node id must be non-empty")
        if self.token_cost < 0:
            raise ValueError("node token_cost cannot be negative")
        if not 0 <= self.confidence <= 1:
            raise ValueError("node confidence must be between zero and one")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "EvidenceNode":
        return cls(**value)


@dataclass(frozen=True)
class EvidenceEdge:
    source: str
    target: str
    type: str
    revision: str = "head"
    confidence: float = 1.0
    resolved: bool = True
    attributes: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.source or not self.target or not self.type:
            raise ValueError("edge source, target and type must be non-empty")
        if not 0 <= self.confidence <= 1:
            raise ValueError("edge confidence must be between zero and one")

    @property
    def key(self) -> tuple[str, str, str]:
        return self.source, self.target, self.type

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "EvidenceEdge":
        return cls(**value)


@dataclass(frozen=True)
class EvidenceGraph:
    nodes: tuple[EvidenceNode, ...] | list[EvidenceNode] = ()
    edges: tuple[EvidenceEdge, ...] | list[EvidenceEdge] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "nodes", tuple(self.nodes))
        object.__setattr__(self, "edges", tuple(self.edges))
        ids = [node.id for node in self.nodes]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate evidence node id")
        unknown = [edge for edge in self.edges if edge.source not in ids or edge.target not in ids]
        if unknown:
            raise ValueError("evidence edge references an unknown node")
        keys = [edge.key for edge in self.edges]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate evidence edge")

    @property
    def node_ids(self) -> tuple[str, ...]:
        return tuple(node.id for node in self.nodes)

    @property
    def by_id(self) -> dict[str, EvidenceNode]:
        return {node.id: node for node in self.nodes}

    def subgraph(self, node_ids: Iterable[str], edge_keys: Iterable[tuple[str, str, str]] | None = None) -> "EvidenceGraph":
        selected = set(node_ids)
        allowed_edges = set(edge_keys) if edge_keys is not None else None
        return EvidenceGraph(
            nodes=tuple(node for node in self.nodes if node.id in selected),
            edges=tuple(
                edge for edge in self.edges
                if edge.source in selected and edge.target in selected
                and (allowed_edges is None or edge.key in allowed_edges)
            ),
            metadata=dict(self.metadata),
        )

    def without_edge_labels(self) -> "EvidenceGraph":
        grouped: dict[tuple[str, str], list[EvidenceEdge]] = {}
        for edge in self.edges:
            grouped.setdefault((edge.source, edge.target), []).append(edge)
        edges = tuple(
            EvidenceEdge(
                source=source, target=target, type="untyped",
                revision=relations[0].revision,
                confidence=max(edge.confidence for edge in relations),
                resolved=all(edge.resolved for edge in relations),
            )
            for (source, target), relations in sorted(grouped.items())
        )
        return EvidenceGraph(
            nodes=self.nodes,
            edges=edges,
            metadata=dict(self.metadata) | {
                "ablation": "edge_labels_removed",
                "coalesced_parallel_edges": len(self.edges) - len(edges),
            },
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": [node.to_dict() for node in self.nodes],
            "edges": [edge.to_dict() for edge in self.edges],
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "EvidenceGraph":
        return cls(
            nodes=tuple(EvidenceNode.from_dict(node) for node in value.get("nodes", [])),
            edges=tuple(EvidenceEdge.from_dict(edge) for edge in value.get("edges", [])),
            metadata=dict(value.get("metadata", {})),
        )
