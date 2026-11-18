from __future__ import annotations

import random
from collections import defaultdict

from .schema import ReviewTask


def _metrics(
    rows: list[dict], task_map: dict[str, ReviewTask],
    match_key: str = "matched_gold_ids", fp_key: str = "fp_count",
) -> dict[str, float]:
    tp = sum(len(set(row.get(match_key, []))) for row in rows)
    fp = sum(int(row.get(fp_key, 0)) for row in rows)
    gold = sum(len(task_map[row["task_id"]].gold_issues) for row in rows)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / gold if gold else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"tp": tp, "fp": fp, "gold": gold, "precision": precision, "recall": recall, "f1": f1}


def _percentile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    position = (len(ordered) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def paired_bootstrap(
    tasks: list[ReviewTask],
    labels: list[dict],
    baseline: str = "diff",
    requests: list[dict] | None = None,
    samples: int = 2000,
    seed: int = 20260902,
    match_mode: str = "strict",
    resample_unit: str = "task",
) -> dict:
    task_map = {task.id: task for task in tasks}
    conditions = sorted({row["condition"] for row in labels})
    by_condition_task: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for row in labels:
        if row["task_id"] in task_map:
            by_condition_task[row["condition"]][row["task_id"]].append(row)
    complete: dict[str, set[str]] = defaultdict(set)
    if requests is not None:
        for row in requests:
            if not row.get("context_status", {}).get("unavailable"):
                complete[row["condition"]].add(row["task_id"])
        for row in labels:
            source = row.get("source_condition")
            if source and row["task_id"] in complete[source]:
                complete[row["condition"]].add(row["task_id"])

    rng = random.Random(seed)
    match_key, fp_key = (
        ("semantic_anywhere_gold_ids", "semantic_anywhere_fp_count")
        if match_mode == "semantic_anywhere" else ("matched_gold_ids", "fp_count")
    )
    analyses = {}
    for mode in ("intention_to_treat", "availability_complete"):
        mode_results = {}
        for condition in conditions:
            if condition == baseline:
                continue
            eligible = sorted(set(by_condition_task[baseline]) & set(by_condition_task[condition]))
            if mode == "availability_complete" and requests is not None:
                eligible = [task_id for task_id in eligible if task_id in complete[condition]]
            baseline_rows = [row for task_id in eligible for row in by_condition_task[baseline][task_id]]
            condition_rows = [row for task_id in eligible for row in by_condition_task[condition][task_id]]
            base_point = _metrics(baseline_rows, task_map, match_key, fp_key)
            condition_point = _metrics(condition_rows, task_map, match_key, fp_key)
            deltas = {metric: condition_point[metric] - base_point[metric] for metric in ("precision", "recall", "f1")}
            distributions = {metric: [] for metric in deltas}
            if eligible:
                if resample_unit == "repository":
                    grouped_units: dict[str, list[str]] = defaultdict(list)
                    for task_id in eligible:
                        grouped_units[task_map[task_id].repo].append(task_id)
                    units = list(grouped_units.values())
                else:
                    units = [[task_id] for task_id in eligible]
                for _ in range(samples):
                    selected = [task_id for _ in units for task_id in rng.choice(units)]
                    sampled_base = [row for task_id in selected for row in by_condition_task[baseline][task_id]]
                    sampled_condition = [row for task_id in selected for row in by_condition_task[condition][task_id]]
                    base_metrics = _metrics(sampled_base, task_map, match_key, fp_key)
                    condition_metrics = _metrics(sampled_condition, task_map, match_key, fp_key)
                    for metric in distributions:
                        distributions[metric].append(condition_metrics[metric] - base_metrics[metric])
            mode_results[condition] = {
                "eligible_tasks": len(eligible), "baseline": base_point, "condition": condition_point,
                "delta": deltas,
                "delta_ci95": {metric: [_percentile(values, 0.025), _percentile(values, 0.975)] for metric, values in distributions.items()},
            }
        analyses[mode] = mode_results

    per_repeat = {}
    for repeat in sorted({int(row.get("repeat", 0)) for row in labels}):
        repeat_rows = [row for row in labels if int(row.get("repeat", 0)) == repeat]
        per_repeat[str(repeat)] = {
            condition: _metrics([row for row in repeat_rows if row["condition"] == condition], task_map, match_key, fp_key)
            for condition in conditions
        }
    return {
        "baseline": baseline, "bootstrap_samples": samples, "seed": seed,
        "match_mode": match_mode, "resample_unit": resample_unit,
        "tasks": len(tasks), "conditions": conditions, "analyses": analyses,
        "per_repeat": per_repeat,
    }
