from __future__ import annotations

import random
from collections import defaultdict

from .schema import ReviewTask
from .statistics import _metrics, _percentile


FACTORIAL_ARMS = {
    "diff": (0, 0, 0),
    "lsp": (1, 0, 0),
    "dataflow": (0, 1, 0),
    "buildtest": (0, 0, 1),
    "lsp_df": (1, 1, 0),
    "lsp_buildtest": (1, 0, 1),
    "dataflow_buildtest": (0, 1, 1),
    "structured_all": (1, 1, 1),
}

CONTRASTS = {
    "lsp_main_at_baseline": {"lsp": 1, "diff": -1},
    "dataflow_main_at_baseline": {"dataflow": 1, "diff": -1},
    "buildtest_main_at_baseline": {"buildtest": 1, "diff": -1},
    "dataflow_given_lsp": {"lsp_df": 1, "lsp": -1},
    "buildtest_given_lsp": {"lsp_buildtest": 1, "lsp": -1},
    "buildtest_given_dataflow": {"dataflow_buildtest": 1, "dataflow": -1},
    "lsp_x_dataflow": {"lsp_df": 1, "lsp": -1, "dataflow": -1, "diff": 1},
    "lsp_x_buildtest": {"lsp_buildtest": 1, "lsp": -1, "buildtest": -1, "diff": 1},
    "dataflow_x_buildtest": {"dataflow_buildtest": 1, "dataflow": -1, "buildtest": -1, "diff": 1},
    "lsp_x_dataflow_x_buildtest": {
        "structured_all": 1, "lsp_df": -1, "lsp_buildtest": -1,
        "dataflow_buildtest": -1, "lsp": 1, "dataflow": 1,
        "buildtest": 1, "diff": -1,
    },
}


def _contrast(metrics: dict[str, dict], coefficients: dict[str, int], metric: str) -> float:
    return sum(coefficient * metrics[condition][metric] for condition, coefficient in coefficients.items())


def _two_sided_zero_p(values: list[float]) -> float:
    if not values:
        return 1.0
    below = sum(value <= 0 for value in values)
    above = sum(value >= 0 for value in values)
    return min(1.0, 2 * min(below + 1, above + 1) / (len(values) + 1))


def _holm_adjust(p_values: dict[str, float]) -> dict[str, float]:
    ordered = sorted(p_values, key=p_values.get)
    adjusted: dict[str, float] = {}
    running = 0.0
    total = len(ordered)
    for index, name in enumerate(ordered):
        running = max(running, min(1.0, (total - index) * p_values[name]))
        adjusted[name] = running
    return adjusted


def factorial_ablation(
    tasks: list[ReviewTask], labels: list[dict], requests: list[dict] | None = None,
    samples: int = 5000, seed: int = 20260902, match_mode: str = "strict",
    resample_unit: str = "task",
) -> dict:
    task_map = {task.id: task for task in tasks}
    rows_by_arm_task: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for row in labels:
        if row.get("condition") in FACTORIAL_ARMS and row.get("task_id") in task_map:
            rows_by_arm_task[row["condition"]][row["task_id"]].append(row)
    common = set(task_map)
    for arm in FACTORIAL_ARMS:
        common &= set(rows_by_arm_task[arm])

    complete = set(common)
    if requests is not None:
        complete = {
            row["task_id"] for row in requests
            if row.get("condition") == "structured_all"
            and not row.get("context_status", {}).get("unavailable")
        } & common

    match_key, fp_key = (
        ("semantic_anywhere_gold_ids", "semantic_anywhere_fp_count")
        if match_mode == "semantic_anywhere" else ("matched_gold_ids", "fp_count")
    )
    rng = random.Random(seed)
    analyses = {}
    for mode, eligible_set in (("intention_to_treat", common), ("complete_case", complete)):
        eligible = sorted(eligible_set)

        def arm_metrics(selected: list[str]) -> dict[str, dict]:
            return {
                arm: _metrics(
                    [row for task_id in selected for row in rows_by_arm_task[arm][task_id]],
                    task_map, match_key, fp_key,
                )
                for arm in FACTORIAL_ARMS
            }

        point_metrics = arm_metrics(eligible)
        point_contrasts = {
            name: {metric: _contrast(point_metrics, coefficients, metric) for metric in ("precision", "recall", "f1")}
            for name, coefficients in CONTRASTS.items()
        }
        distributions = {
            name: {metric: [] for metric in ("precision", "recall", "f1")}
            for name in CONTRASTS
        }
        if eligible:
            if resample_unit == "repository":
                grouped: dict[str, list[str]] = defaultdict(list)
                for task_id in eligible:
                    grouped[task_map[task_id].repo].append(task_id)
                units = list(grouped.values())
            else:
                units = [[task_id] for task_id in eligible]
            for _ in range(samples):
                selected = [task_id for _unit in units for task_id in rng.choice(units)]
                sampled = arm_metrics(selected)
                for name, coefficients in CONTRASTS.items():
                    for metric in distributions[name]:
                        distributions[name][metric].append(_contrast(sampled, coefficients, metric))
        p_values = {
            metric: {name: _two_sided_zero_p(distributions[name][metric]) for name in CONTRASTS}
            for metric in ("precision", "recall", "f1")
        }
        adjusted = {metric: _holm_adjust(values) for metric, values in p_values.items()}
        analyses[mode] = {
            "eligible_tasks": len(eligible),
            "arm_metrics": point_metrics,
            "contrasts": {
                name: {
                    "coefficients": CONTRASTS[name], "estimate": point_contrasts[name],
                    "ci95": {
                        metric: [_percentile(values, 0.025), _percentile(values, 0.975)]
                        for metric, values in distributions[name].items()
                    },
                    "p_value": {metric: p_values[metric][name] for metric in p_values},
                    "p_holm": {metric: adjusted[metric][name] for metric in adjusted},
                }
                for name in CONTRASTS
            },
        }
    return {
        "factors": ["lsp", "dataflow", "buildtest"], "arms": FACTORIAL_ARMS,
        "match_mode": match_mode, "resample_unit": resample_unit,
        "bootstrap_samples": samples, "seed": seed, "analyses": analyses,
    }
