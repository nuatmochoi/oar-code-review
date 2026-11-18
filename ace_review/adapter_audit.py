"""Structural audit for preserved paired-SCIP runs; never a gold evaluation."""
from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path
from typing import Any

from .minimal_subgraph import select_minimal_subgraph
from .scip_adapter import adapt_scip_bundles
from .semantic_delta import compute_semantic_delta


def _read_gzip_json(path: Path) -> Any:
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return json.load(stream)


def audit_scip_run(
    input_root: str | Path,
    output_path: str | Path,
    *,
    budget: int = 1200,
    max_depth: int = 3,
) -> dict[str, Any]:
    root = Path(input_root).resolve()
    output = Path(output_path)
    records = []
    bundle_ids = {path.name.removesuffix(".json.gz") for path in (root / "bundles").glob("*.json.gz")}
    manifest_ids = {path.stem for path in (root / "tasks").glob("*.json")}
    task_ids = sorted(manifest_ids | bundle_ids)
    for task_id in task_ids:
        bundle_path = root / "bundles" / f"{task_id}.json.gz"
        if not bundle_path.is_file():
            records.append({"task_id": task_id, "status": "unavailable",
                            "reason": "no preserved bundle file"})
            continue
        candidate_path = root / "candidates" / bundle_path.name
        if not candidate_path.is_file():
            records.append({"task_id": task_id, "status": "error",
                            "error": f"missing candidate file: {candidate_path}"})
            continue
        try:
            bundles = _read_gzip_json(bundle_path)
            candidates = _read_gzip_json(candidate_path)
            if not isinstance(bundles, list) or not isinstance(candidates, dict):
                raise ValueError("preserved bundle/candidate JSON has the wrong top-level type")
            sources = candidates.get("bundle_sources")
            if not isinstance(sources, list):
                raise ValueError("candidate file has no bundle_sources list")
            base = adapt_scip_bundles(bundles, sources, revision="base")
            head = adapt_scip_bundles(bundles, sources, revision="head")
            seeds = set(head.metadata["seed_ids"])
            delta = compute_semantic_delta(base, head, seed_ids=seeds)
            selected = select_minimal_subgraph(head, seeds, budget=budget, max_depth=max_depth)
            records.append({
                "task_id": task_id,
                "status": "ok",
                "base": {
                    "nodes": len(base.nodes), "edges": len(base.edges),
                    "nodes_with_source": base.metadata["nodes_with_source"],
                },
                "head": {
                    "nodes": len(head.nodes), "edges": len(head.edges),
                    "nodes_with_source": head.metadata["nodes_with_source"],
                },
                "aligned_node_ids": len(set(base.node_ids) & set(head.node_ids)),
                "delta": {
                    "added_nodes": len(delta.added_nodes), "removed_nodes": len(delta.removed_nodes),
                    "modified_nodes": len(delta.modified_nodes), "added_edges": len(delta.added_edges),
                    "removed_edges": len(delta.removed_edges),
                },
                "selected": {
                    "nodes": len(selected.graph.nodes), "edges": len(selected.graph.edges),
                    "nodes_with_source": sum(bool(node.content) for node in selected.graph.nodes),
                    "token_cost": selected.token_cost,
                    "complete_paths": selected.complete_path_count,
                    "candidate_paths": selected.audit["candidate_paths"],
                    "candidate_paths_truncated": selected.audit["candidate_paths_truncated"],
                },
            })
        except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            records.append({"task_id": task_id, "status": "error",
                            "error": f"{type(error).__name__}: {error}"})
    succeeded = sum(record["status"] == "ok" for record in records)
    unavailable = sum(record["status"] == "unavailable" for record in records)
    failed = sum(record["status"] == "error" for record in records)
    report = {
        "scope": "structural adapter audit only; no oracle or review-accuracy claim",
        "input_root": str(root),
        "budget": budget,
        "max_depth": max_depth,
        "tasks": len(records),
        "succeeded": succeeded,
        "unavailable": unavailable,
        "failed": failed,
        "nonempty_complete_path_selections": sum(
            record.get("selected", {}).get("complete_paths", 0) > 0 for record in records
        ),
        "candidate_path_truncation_tasks": sum(
            record.get("selected", {}).get("candidate_paths_truncated", False)
            for record in records
        ),
        "records": records,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit preserved paired-SCIP bundles")
    parser.add_argument("input_root")
    parser.add_argument("output")
    parser.add_argument("--budget", type=int, default=1200)
    parser.add_argument("--max-depth", type=int, default=3)
    args = parser.parse_args()
    report = audit_scip_run(args.input_root, args.output, budget=args.budget, max_depth=args.max_depth)
    print(json.dumps({key: report[key] for key in (
        "tasks", "succeeded", "unavailable", "failed", "nonempty_complete_path_selections",
    )}, indent=2))
    raise SystemExit(0 if report["failed"] == 0 else 1)


if __name__ == "__main__":
    main()
