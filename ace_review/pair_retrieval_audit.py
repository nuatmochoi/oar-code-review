"""Gold-aware, model-free retrieval audit for a validated Python pair."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .delta_scip import changed_ranges
from .minimal_subgraph import select_minimal_subgraph
from .paired_benchmark import load_pair, validate_pair
from .python_ast_graph import build_python_ast_graph
from .retrieval_gate import evaluate_retrieval, passes_retrieval_gate
from .semantic_delta import compute_semantic_delta


def audit_python_pair(
    pair_path: str | Path,
    output_path: str | Path,
    *,
    variant: str = "unsafe",
    budget: int = 1200,
    max_depth: int = 3,
    max_candidate_paths: int = 10_000,
) -> dict[str, Any]:
    path = Path(pair_path).resolve()
    pair = load_pair(path)
    static = validate_pair(pair, path.parent)
    if not static["eligible"]:
        raise ValueError("pair failed static eligibility: " + "; ".join(static["errors"]))
    if variant not in {"safe", "unsafe"}:
        raise ValueError("variant must be safe or unsafe")
    if pair["language"].lower() not in {"python", "py"}:
        raise ValueError("Python AST audit requires a Python pair")
    world = pair["worlds"][variant]
    changed_files = set(pair["target"]["changed_files"])
    diff = (path.parent / world["diff"]).read_text(encoding="utf-8")
    lines = changed_ranges(diff)
    base = build_python_ast_graph(path.parent / world["base_snapshot"], revision="base",
                                  changed_files=changed_files, changed_lines=lines["base"])
    head = build_python_ast_graph(path.parent / world["head_snapshot"], revision="head",
                                  changed_files=changed_files, changed_lines=lines["head"])
    seeds = set(head.metadata["seed_ids"])
    delta = compute_semantic_delta(base, head, seed_ids=seeds)
    selected = select_minimal_subgraph(
        head, seeds, budget=budget, max_depth=max_depth,
        max_candidate_paths=max_candidate_paths,
    )
    metrics = evaluate_retrieval(selected.graph, head, pair["gold"])
    gate = passes_retrieval_gate(metrics)
    if selected.audit["candidate_paths_truncated"]:
        gate["passed"] = False
        gate["failed_metrics"].append("candidate_paths_truncated")
    report = {
        "scope": "gold-aware retrieval-only diagnostic; no reviewer calls and no accuracy claim",
        "family_id": pair["family_id"],
        "variant": variant,
        "backend": head.metadata["provider"],
        "static_eligible": True,
        "pair_record_sha256": static["audit"]["pair_record_sha256"],
        "budget": budget,
        "max_depth": max_depth,
        "max_candidate_paths": max_candidate_paths,
        "reviewer_calls": 0,
        "base_graph": {"nodes": len(base.nodes), "edges": len(base.edges)},
        "head_graph": {"nodes": len(head.nodes), "edges": len(head.edges)},
        "semantic_delta": {
            "added_nodes": len(delta.added_nodes), "removed_nodes": len(delta.removed_nodes),
            "modified_nodes": len(delta.modified_nodes), "added_edges": len(delta.added_edges),
            "removed_edges": len(delta.removed_edges),
        },
        "selection": {
            "nodes": len(selected.graph.nodes), "edges": len(selected.graph.edges),
            "token_cost": selected.token_cost,
            "complete_paths": selected.complete_path_count,
            "audit": selected.audit,
        },
        "retrieval": metrics,
        "gate": gate,
        "backend_limits": head.metadata["limits"],
    }
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a model-free Python pair retrieval audit")
    parser.add_argument("pair")
    parser.add_argument("output")
    parser.add_argument("--variant", choices=("safe", "unsafe"), default="unsafe")
    parser.add_argument("--budget", type=int, default=1200)
    parser.add_argument("--max-depth", type=int, default=3)
    parser.add_argument("--max-candidate-paths", type=int, default=10_000)
    args = parser.parse_args()
    report = audit_python_pair(args.pair, args.output, variant=args.variant,
                               budget=args.budget, max_depth=args.max_depth,
                               max_candidate_paths=args.max_candidate_paths)
    print(json.dumps({
        "family_id": report["family_id"],
        "oracle_node_recall": report["retrieval"]["oracle_node_recall"],
        "oracle_edge_recall": report["retrieval"]["oracle_edge_recall"],
        "complete_path_recall": report["retrieval"]["complete_path_recall"],
        "gate_passed": report["gate"]["passed"],
    }, indent=2))


if __name__ == "__main__":
    main()
