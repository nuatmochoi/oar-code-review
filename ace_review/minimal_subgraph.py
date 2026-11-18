"""Budgeted selection of complete provenance paths, never isolated chunks."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any, Iterable

from .evidence_graph import EvidenceEdge, EvidenceGraph


ENDPOINT_ROLES = {
    "producer", "resource_writer", "config_source", "caller", "sink",
    "downstream_consumer", "test", "build_target", "override", "implementation",
    "semantic_endpoint",
}


@dataclass(frozen=True)
class SubgraphSelection:
    graph: EvidenceGraph
    complete_path_count: int
    token_cost: int
    audit: dict[str, Any]


def _candidate_paths(
    graph: EvidenceGraph, seeds: set[str], max_depth: int, max_paths: int,
) -> tuple[list[tuple[tuple[str, ...], tuple[EvidenceEdge, ...]]], bool]:
    by_id = graph.by_id
    adjacency: dict[str, list[tuple[str, EvidenceEdge]]] = {node_id: [] for node_id in by_id}
    for edge in graph.edges:
        if not edge.resolved or edge.confidence <= 0:
            continue
        adjacency[edge.source].append((edge.target, edge))
        adjacency[edge.target].append((edge.source, edge))
    found: dict[tuple[str, ...], tuple[EvidenceEdge, ...]] = {}
    for seed in sorted(seeds):
        queue = deque([(seed, (seed,), ())])
        while queue:
            current, path, edges = queue.popleft()
            if current != seed and by_id[current].role in ENDPOINT_ROLES:
                canonical = path if path[0] < path[-1] else tuple(reversed(path))
                found.setdefault(canonical, edges)
                if len(found) > max_paths:
                    retained = sorted(found)[:max_paths]
                    return [(nodes, found[nodes]) for nodes in retained], True
                continue
            # A changed function is a valid starting seed for call-context
            # selection, but it is not a data-flow junction.  Letting a local
            # flow seed traverse through its containing function mixes every
            # parameter of that function into the same provenance path.
            if current != seed and by_id[current].role == "diff_seed":
                continue
            if len(edges) >= max_depth:
                continue
            for neighbor, edge in sorted(adjacency[current], key=lambda item: (item[0], item[1].type)):
                if neighbor not in path:
                    queue.append((neighbor, path + (neighbor,), edges + (edge,)))
    return [(nodes, found[nodes]) for nodes in sorted(found)], False


def select_minimal_subgraph(
    graph: EvidenceGraph,
    seed_ids: Iterable[str],
    *,
    budget: int = 1200,
    max_depth: int = 3,
    max_candidate_paths: int = 10_000,
) -> SubgraphSelection:
    if budget < 0:
        raise ValueError("budget cannot be negative")
    if max_depth < 1:
        raise ValueError("max_depth must be positive")
    if max_candidate_paths < 1:
        raise ValueError("max_candidate_paths must be positive")
    seeds = set(seed_ids)
    if not seeds <= set(graph.node_ids):
        raise ValueError("all seeds must exist in the graph")
    paths, paths_truncated = _candidate_paths(graph, seeds, max_depth, max_candidate_paths)
    by_id = graph.by_id

    def rank(item: tuple[tuple[str, ...], tuple[EvidenceEdge, ...]]) -> tuple[float, int, tuple[str, ...]]:
        node_ids, edges = item
        cost = sum(by_id[node_id].token_cost for node_id in set(node_ids))
        roles = len({by_id[node_id].role for node_id in node_ids
                     if node_id in seeds or by_id[node_id].role in ENDPOINT_ROLES})
        confidence = sum(edge.confidence for edge in edges) / max(1, len(edges))
        delta = len(seeds & set(node_ids))
        utility = roles + confidence + delta
        return (-utility / max(1, cost), cost, node_ids)

    selected_nodes: set[str] = set()
    selected_edges: set[tuple[str, str, str]] = set()
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    covered_obligations: set[tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]] = set()
    for node_ids, edges in sorted(paths, key=rank):
        relation_families = tuple(sorted({
            str(edge.attributes["relation_family"])
            for edge in edges if edge.attributes.get("relation_family")
        }))
        obligation = (
            tuple(sorted(seeds & set(node_ids))),
            tuple(sorted(node_id for node_id in node_ids
                         if node_id not in seeds and by_id[node_id].role in ENDPOINT_ROLES)),
            relation_families,
        )
        additions = set(node_ids) - selected_nodes
        marginal_cost = sum(by_id[node_id].token_cost for node_id in additions)
        current_cost = sum(by_id[node_id].token_cost for node_id in selected_nodes)
        record = {"nodes": list(node_ids), "edge_types": [edge.type for edge in edges],
                  "relation_families": list(relation_families),
                  "marginal_cost": marginal_cost}
        if obligation in covered_obligations:
            rejected.append(record | {"reason": "redundant_obligation"})
            continue
        if current_cost + marginal_cost > budget:
            rejected.append(record | {"reason": "budget"})
            continue
        selected_nodes.update(node_ids)
        selected_edges.update(edge.key for edge in edges)
        covered_obligations.add(obligation)
        accepted.append(record)
    selected = graph.subgraph(selected_nodes, selected_edges)
    cost = sum(node.token_cost for node in selected.nodes)
    return SubgraphSelection(
        graph=selected,
        complete_path_count=len(accepted),
        token_cost=cost,
        audit={
            "budget": budget,
            "max_depth": max_depth,
            "max_candidate_paths": max_candidate_paths,
            "candidate_paths_truncated": paths_truncated,
            "diff_seed_transit_policy": "start_only",
            "excluded_unresolved_edges": sum(
                not edge.resolved or edge.confidence <= 0 for edge in graph.edges
            ),
            "candidate_paths": len(paths),
            "accepted_paths": accepted,
            "rejected_paths": rejected,
        },
    )
