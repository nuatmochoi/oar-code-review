from __future__ import annotations

import json
import re
from pathlib import Path

from .diff import change_features, changed_lines
from .io import load_tasks, read_jsonl
from .selector import UtilitySelector, utility


CAST = re.compile(r"\b(?:u?int(?:8|16|32|64)?|float(?:32|64)?|string|byte|rune)\s*\(")


def static_policy(task) -> tuple[str, str]:
    """Gold-blind pilot policy; the learned selector replaces it at benchmark scale."""
    files = [path.lower() for path in task.changed_files]
    text = "\n".join(changed_lines(task.diff))
    features = change_features(task.diff)
    if any("test" in Path(path).name or Path(path).name in {"go.mod", "go.sum", "package.json"} for path in files):
        return "lsp_buildtest", "test_or_build_surface"
    if CAST.search(text):
        return "lsp_df", "explicit_value_conversion"
    if features["has_control_change"] or features["has_error_change"] or features["has_io_change"]:
        return "lsp_df", "behavior_or_flow_sensitive_change"
    if features["identifier_count"] >= 6:
        return "lsp", "symbol_dense_change"
    if features["changed_loc"] >= 12:
        return "bm25", "large_lexical_change"
    return "diff", "small_local_change"


def compose_adaptive_labels(manifest_path: str | Path, labels_path: str | Path, output_path: str | Path) -> dict:
    tasks = load_tasks(manifest_path)
    labels = list(read_jsonl(labels_path))
    by_task = {}
    for row in labels:
        key = (row["task_id"], row.get("repeat", 0))
        by_task.setdefault(key, {})[row["condition"]] = row
    selected = []
    decisions = []
    fallback = ("lsp_df", "lsp_buildtest", "lsp", "bm25", "diff")
    for task in tasks:
        preferred, reason = static_policy(task)
        repeats = sorted(repeat for task_id, repeat in by_task if task_id == task.id)
        for repeat in repeats:
            available = by_task.get((task.id, repeat), {})
            source = preferred if preferred in available else next((name for name in fallback if name in available), None)
            if source is None:
                continue
            selected.append(dict(available[source]) | {"condition": "adaptive", "source_condition": source})
            decisions.append({"task_id": task.id, "repeat": repeat, "preferred": preferred, "selected": source, "reason": reason})
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        for row in (*labels, *selected):
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    decisions_path = str(target) + ".decisions.json"
    Path(decisions_path).write_text(json.dumps(decisions, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"fixed_rows": len(labels), "adaptive_rows": len(selected), "output": str(target), "decisions": decisions_path}


def nested_repository_adaptive(
    manifest_path: str | Path,
    outcomes_path: str | Path,
    labels_path: str | Path,
    output_path: str | Path,
    neighbor_candidates: tuple[int, ...] = (1, 3, 5, 7),
) -> dict:
    """Nested leave-one-repository-out selection without target-repository labels."""
    tasks = load_tasks(manifest_path)
    outcomes = list(read_jsonl(outcomes_path))
    labels = list(read_jsonl(labels_path))
    repositories = sorted({task.repo for task in tasks})
    realized = {(row["task_id"], row["condition"]): utility(row) for row in outcomes}
    decisions = []
    for outer_repo in repositories:
        train = [task for task in tasks if task.repo != outer_repo]
        held_out = [task for task in tasks if task.repo == outer_repo]
        inner_repositories = sorted({task.repo for task in train})
        scores = {}
        for neighbors in neighbor_candidates:
            fold_scores = []
            for inner_repo in inner_repositories:
                inner_train = [task for task in train if task.repo != inner_repo]
                inner_valid = [task for task in train if task.repo == inner_repo]
                if not inner_train or not inner_valid:
                    continue
                selector = UtilitySelector.fit(inner_train, outcomes, neighbors=neighbors)
                for task in inner_valid:
                    selected = selector.predict(task)["condition"]
                    if (task.id, selected) in realized:
                        fold_scores.append(realized[(task.id, selected)])
            scores[neighbors] = sum(fold_scores) / len(fold_scores) if fold_scores else float("-inf")
        chosen_neighbors = max(neighbor_candidates, key=lambda value: (scores[value], -value))
        selector = UtilitySelector.fit(train, outcomes, neighbors=chosen_neighbors)
        for task in held_out:
            prediction = selector.predict(task)
            decisions.append({
                "task_id": task.id, "outer_repository": outer_repo,
                "condition": prediction["condition"], "chosen_neighbors": chosen_neighbors,
                "inner_scores": {str(key): value for key, value in scores.items()},
                "predicted_utilities": prediction["predicted_utilities"],
                "neighbors": prediction["neighbors"],
            })

    selected_by_task = {row["task_id"]: row for row in decisions}
    adaptive_rows = []
    for row in labels:
        decision = selected_by_task.get(row["task_id"])
        if decision and row["condition"] == decision["condition"]:
            adaptive_rows.append(dict(row) | {
                "condition": "adaptive_cv", "source_condition": row["condition"],
                "outer_repository": decision["outer_repository"],
            })
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        for row in (*labels, *adaptive_rows):
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    decisions_path = str(target) + ".decisions.json"
    Path(decisions_path).write_text(json.dumps(decisions, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "repositories": len(repositories), "tasks": len(tasks), "fixed_rows": len(labels),
        "adaptive_rows": len(adaptive_rows), "output": str(target), "decisions": decisions_path,
    }
