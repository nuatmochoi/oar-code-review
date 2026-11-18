"""Gold-only retrieval diagnostics run before any reviewer-model calls."""
from __future__ import annotations

from typing import Any

from .evidence_graph import EvidenceGraph


def _recall(found: int, total: int) -> float | None:
    return found / total if total else None


def _matches_oracle_node(oracle: dict[str, Any], node: Any) -> bool:
    if oracle.get("id") == node.id:
        return True
    if not oracle.get("file") or oracle.get("file") != node.file:
        return False
    start, end = oracle.get("start_line"), oracle.get("end_line")
    if not isinstance(start, int) or not isinstance(end, int):
        return False
    if node.start_line is None or node.end_line is None:
        return False
    if end < node.start_line or node.end_line < start:
        return False
    symbols = oracle.get("symbols", [])
    return not symbols or node.symbol in symbols


def oracle_subgraph(candidate: EvidenceGraph, gold: dict[str, Any]) -> EvidenceGraph:
    """Project a candidate graph onto the backend nodes/edges named by gold."""
    oracle_records = gold.get("oracle_nodes", [])
    matches = {
        oracle["id"]: {
            node.id for node in candidate.nodes if _matches_oracle_node(oracle, node)
        }
        for oracle in oracle_records
    }
    node_ids = set().union(*matches.values()) if matches else set()
    candidate_edge_keys = {edge.key for edge in candidate.edges}
    edge_keys = {
        (source_id, target_id, edge["type"])
        for edge in gold.get("oracle_edges", [])
        for source_id in matches.get(edge["source"], set())
        for target_id in matches.get(edge["target"], set())
        if (source_id, target_id, edge["type"]) in candidate_edge_keys
    }
    return candidate.subgraph(node_ids, edge_keys)


def evaluate_retrieval(
    selected: EvidenceGraph,
    candidate: EvidenceGraph,
    gold: dict[str, Any],
) -> dict[str, Any]:
    """Measure evidence recovery, typed-path completeness, noise, and compression."""
    oracle_node_records = gold.get("oracle_nodes", [])
    oracle_nodes = {node["id"] for node in oracle_node_records}
    required_nodes = set(gold.get("required_evidence_nodes", []))
    oracle_edges = {
        (edge["source"], edge["target"], edge["type"])
        for edge in gold.get("oracle_edges", [])
    }
    selected_edges = {edge.key for edge in selected.edges}
    matched = {
        oracle["id"]: {node.id for node in selected.nodes if _matches_oracle_node(oracle, node)}
        for oracle in oracle_node_records
    }
    found_oracle_nodes = {oracle_id for oracle_id, backend_ids in matched.items() if backend_ids}

    def edge_found(source: str, target: str, edge_type: str) -> bool:
        return any(
            (backend_source, backend_target, edge_type) in selected_edges
            for backend_source in matched.get(source, set())
            for backend_target in matched.get(target, set())
        )

    found_oracle_edges = {
        edge for edge in oracle_edges if edge_found(edge[0], edge[1], edge[2])
    }
    paths = gold.get("oracle_paths", [])
    complete_paths = 0
    for path in paths:
        nodes = path.get("nodes", [])
        types = path.get("edge_types", [])
        if len(nodes) < 2 or len(types) != len(nodes) - 1:
            raise ValueError("each oracle path needs N nodes and N-1 edge types")
        node_matches = all(matched.get(node_id) for node_id in nodes)
        edges_match = all(edge_found(nodes[index], nodes[index + 1], types[index])
                          for index in range(len(types)))
        if node_matches and edges_match:
            complete_paths += 1

    selected_tokens = sum(node.token_cost for node in selected.nodes)
    candidate_tokens = sum(node.token_cost for node in candidate.nodes)
    relevant_backend_nodes = set().union(*matched.values()) if matched else set()
    irrelevant_tokens = sum(node.token_cost for node in selected.nodes if node.id not in relevant_backend_nodes)
    return {
        "oracle_nodes_found": len(found_oracle_nodes),
        "oracle_nodes_total": len(oracle_nodes),
        "oracle_node_recall": _recall(len(found_oracle_nodes), len(oracle_nodes)),
        "required_nodes_found": len(required_nodes & found_oracle_nodes),
        "required_nodes_total": len(required_nodes),
        "required_node_recall": _recall(len(required_nodes & found_oracle_nodes), len(required_nodes)),
        "oracle_edges_found": len(found_oracle_edges),
        "oracle_edges_total": len(oracle_edges),
        "oracle_edge_recall": _recall(len(found_oracle_edges), len(oracle_edges)),
        "complete_paths_found": complete_paths,
        "complete_paths_total": len(paths),
        "complete_path_recall": _recall(complete_paths, len(paths)),
        "selected_tokens": selected_tokens,
        "candidate_tokens": candidate_tokens,
        "irrelevant_tokens": irrelevant_tokens,
        "irrelevant_token_ratio": irrelevant_tokens / selected_tokens if selected_tokens else 0.0,
        "selected_token_fraction": selected_tokens / candidate_tokens if candidate_tokens else None,
        "matched_backend_nodes": {
            oracle_id: sorted(backend_ids) for oracle_id, backend_ids in matched.items()
        },
    }


def passes_retrieval_gate(
    metrics: dict[str, Any],
    *,
    min_complete_path_recall: float = 0.8,
    max_irrelevant_token_ratio: float = 0.25,
) -> dict[str, Any]:
    if not 0 <= min_complete_path_recall <= 1 or not 0 <= max_irrelevant_token_ratio <= 1:
        raise ValueError("retrieval thresholds must be between zero and one")
    required = {
        "complete_path_recall": min_complete_path_recall,
        "irrelevant_token_ratio": max_irrelevant_token_ratio,
    }
    if any(metrics.get(name) is None for name in required):
        raise ValueError("retrieval gate metrics have an empty denominator")
    failed = []
    if metrics["complete_path_recall"] < min_complete_path_recall:
        failed.append("complete_path_recall")
    if metrics["irrelevant_token_ratio"] > max_irrelevant_token_ratio:
        failed.append("irrelevant_token_ratio")
    return {
        "passed": not failed,
        "failed_metrics": failed,
        "thresholds": {
            "min_complete_path_recall": min_complete_path_recall,
            "max_irrelevant_token_ratio": max_irrelevant_token_ratio,
        },
    }
