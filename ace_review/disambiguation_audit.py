"""Model-free ablation of call context versus typed value-flow evidence."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .delta_scip import changed_ranges
from .evidence_graph import EvidenceGraph
from .minimal_subgraph import SubgraphSelection, select_minimal_subgraph
from .paired_benchmark import load_pair, validate_pair
from .python_ast_graph import build_python_ast_graph
from .retrieval_gate import evaluate_retrieval, oracle_subgraph, passes_retrieval_gate


def _relation_subgraph(graph: EvidenceGraph, family: str) -> EvidenceGraph:
    edges = tuple(
        edge for edge in graph.edges
        if edge.attributes.get("relation_family") == family
    )
    node_ids = {endpoint for edge in edges for endpoint in (edge.source, edge.target)}
    node_ids.update(graph.metadata.get("seed_ids", []))
    return graph.subgraph(node_ids, (edge.key for edge in edges))


def _arm(
    graph: EvidenceGraph,
    candidate: EvidenceGraph,
    gold: dict[str, Any],
    selection: SubgraphSelection | None = None,
) -> dict[str, Any]:
    metrics = evaluate_retrieval(graph, candidate, gold)
    gate = passes_retrieval_gate(metrics)
    if selection is not None and selection.audit["candidate_paths_truncated"]:
        gate["passed"] = False
        gate["failed_metrics"].append("candidate_paths_truncated")
    return {
        "source_visibility": _source_visibility(graph, candidate, gold),
        "selection": {
            "nodes": len(graph.nodes),
            "edges": len(graph.edges),
            "token_cost": sum(node.token_cost for node in graph.nodes),
            "complete_paths": selection.complete_path_count if selection else None,
            "audit": selection.audit if selection else None,
        },
        "retrieval": metrics,
        "gate": gate,
    }


def _source_visibility(graph: EvidenceGraph, candidate: EvidenceGraph,
                       gold: dict[str, Any]) -> dict[str, Any]:
    """Check literal source containment independently of graph node identity.

    This is a diagnostic, not semantic sufficiency: names can coincide, and
    snippets may lack caller signatures or surrounding control flow.
    """
    matches = evaluate_retrieval(candidate, candidate, gold)["matched_backend_nodes"]
    visible = {}
    for logical_id, backend_ids in matches.items():
        visible[logical_id] = any(
            target.content.strip()
            and target.file == selected.file
            and target.revision == selected.revision
            and target.content.strip() in selected.content
            for backend_id in backend_ids
            for target in [candidate.by_id[backend_id]]
            for selected in graph.nodes
        )
    return {
        "oracle_snippet_containment_recall": (
            sum(visible.values()) / len(visible) if visible else None
        ),
        "visible": visible,
        "interpretation": "literal same-file snippet containment; not review correctness",
    }


def _validated_candidate(
    path: Path, variant: str,
) -> tuple[dict[str, Any], dict[str, Any], EvidenceGraph]:
    pair = load_pair(path)
    static = validate_pair(pair, path.parent)
    if not static["eligible"]:
        raise ValueError("pair failed static eligibility: " + "; ".join(static["errors"]))
    if variant not in {"safe", "unsafe"}:
        raise ValueError("variant must be safe or unsafe")
    if pair["language"].lower() not in {"python", "py"}:
        raise ValueError("Python AST audit requires a Python pair")
    world = pair["worlds"][variant]
    diff = (path.parent / world["diff"]).read_text(encoding="utf-8")
    lines = changed_ranges(diff)
    candidate = build_python_ast_graph(
        path.parent / world["head_snapshot"], revision="head",
        changed_files=set(pair["target"]["changed_files"]), changed_lines=lines["head"],
    )
    return pair, static, candidate


def _select_arms(
    candidate: EvidenceGraph,
    gold: dict[str, Any],
    *,
    budget: int,
    max_depth: int,
    max_candidate_paths: int,
) -> dict[str, dict[str, Any]]:
    function_seeds = set(candidate.metadata["seed_ids"])
    flow_seeds = set(candidate.metadata["flow_seed_ids"])
    if not function_seeds or not flow_seeds:
        raise ValueError("disambiguation audit requires function and changed value-flow seeds")
    # The local flow seed is the graph projection of the actual changed line;
    # the function node also contains unchanged surrounding lines.
    diff_only = candidate.subgraph(flow_seeds, ())
    call_selection = select_minimal_subgraph(
        _relation_subgraph(candidate, "call"), function_seeds,
        budget=budget, max_depth=max_depth, max_candidate_paths=max_candidate_paths,
    )
    flow_selection = select_minimal_subgraph(
        candidate, flow_seeds, budget=budget, max_depth=max_depth,
        max_candidate_paths=max_candidate_paths,
    )
    arms = {
        "diff_only": _arm(diff_only, candidate, gold),
        "call_only": _arm(call_selection.graph, candidate, gold, call_selection),
        "untyped_flow": _arm(flow_selection.graph.without_edge_labels(), candidate, gold),
        "typed_flow": _arm(flow_selection.graph, candidate, gold, flow_selection),
        "oracle": _arm(oracle_subgraph(candidate, gold), candidate, gold),
    }
    # Preserve isolated nodes so call-edge removal does not also remove the
    # value seeds: the crossed conditions vary only seeds and relation set.
    call_edges = candidate.subgraph(candidate.node_ids, (
        edge.key for edge in candidate.edges
        if edge.attributes.get("relation_family") == "call"
    ))
    for name, graph, seeds in (
        ("function_seed_full_graph", candidate, function_seeds),
        ("value_seed_call_edges", call_edges, flow_seeds),
    ):
        selected = select_minimal_subgraph(
            graph, seeds, budget=budget, max_depth=max_depth,
            max_candidate_paths=max_candidate_paths,
        )
        arms[name] = _arm(selected.graph, candidate, gold, selected)
    return arms


def _incremental(arms: dict[str, dict[str, Any]]) -> dict[str, float]:
    def metric(arm: str, name: str) -> float:
        return arms[arm]["retrieval"][name]

    return {
        "typed_minus_call_oracle_node_recall": (
            metric("typed_flow", "oracle_node_recall")
            - metric("call_only", "oracle_node_recall")
        ),
        "typed_minus_call_complete_path_recall": (
            metric("typed_flow", "complete_path_recall")
            - metric("call_only", "complete_path_recall")
        ),
        "typed_minus_untyped_complete_path_recall": (
            metric("typed_flow", "complete_path_recall")
            - metric("untyped_flow", "complete_path_recall")
        ),
    }


def audit_disambiguation_pair(
    pair_path: str | Path,
    output_path: str | Path,
    *,
    variant: str = "unsafe",
    budget: int = 1200,
    max_depth: int = 6,
    max_candidate_paths: int = 10_000,
) -> dict[str, Any]:
    """Compare evidence arms while holding the pair, backend, and gold fixed."""
    path = Path(pair_path).resolve()
    pair, static, candidate = _validated_candidate(path, variant)
    arms = _select_arms(
        candidate, pair["gold"], budget=budget, max_depth=max_depth,
        max_candidate_paths=max_candidate_paths,
    )
    report = {
        "scope": "synthetic retrieval-only disambiguation control; no reviewer accuracy claim",
        "family_id": pair["family_id"],
        "variant": variant,
        "backend": candidate.metadata["provider"],
        "static_eligible": True,
        "pair_record_sha256": static["audit"]["pair_record_sha256"],
        "reviewer_calls": 0,
        "budget": budget,
        "max_depth": max_depth,
        "max_candidate_paths": max_candidate_paths,
        "candidate_graph": {"nodes": len(candidate.nodes), "edges": len(candidate.edges)},
        "arms": arms,
        "incremental": _incremental(arms),
        "backend_limits": candidate.metadata["limits"],
    }
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Ablate call-only and typed-flow retrieval")
    parser.add_argument("pair")
    parser.add_argument("output")
    parser.add_argument("--variant", choices=("safe", "unsafe"), default="unsafe")
    parser.add_argument("--budget", type=int, default=1200)
    parser.add_argument("--max-depth", type=int, default=6)
    parser.add_argument("--max-candidate-paths", type=int, default=10_000)
    args = parser.parse_args()
    report = audit_disambiguation_pair(
        args.pair, args.output, variant=args.variant, budget=args.budget,
        max_depth=args.max_depth, max_candidate_paths=args.max_candidate_paths,
    )
    print(json.dumps({
        "family_id": report["family_id"],
        "incremental": report["incremental"],
        "typed_gate_passed": report["arms"]["typed_flow"]["gate"]["passed"],
    }, indent=2))


if __name__ == "__main__":
    main()
