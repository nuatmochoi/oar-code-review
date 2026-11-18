from __future__ import annotations

from collections import defaultdict

from .schema import ReviewTask


def aggregate_labels(tasks: list[ReviewTask], labels: list[dict], baseline: str = "diff") -> dict:
    task_map = {task.id: task for task in tasks}
    by_condition: dict[str, list[dict]] = defaultdict(list)
    by_task: dict[tuple[str, int], dict[str, dict]] = defaultdict(dict)
    for row in labels:
        if row["task_id"] not in task_map:
            continue
        normalized = dict(row)
        normalized["matched_gold_ids"] = set(row.get("matched_gold_ids", []))
        by_condition[row["condition"]].append(normalized)
        by_task[(row["task_id"], int(row.get("repeat", 0)))][row["condition"]] = normalized

    condition_metrics = {}
    semantic_condition_metrics = {}
    selector_outcomes = []
    for condition, rows in sorted(by_condition.items()):
        tp = sum(len(row["matched_gold_ids"]) for row in rows)
        fp = sum(int(row.get("fp_count", 0)) for row in rows)
        gold = sum(len(task_map[row["task_id"]].gold_issues) for row in rows)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / gold if gold else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        clean_rows = [row for row in rows if not task_map[row["task_id"]].gold_issues]
        far = sum(bool(row.get("fp_count", 0)) for row in clean_rows) / len(clean_rows) if clean_rows else None
        condition_metrics[condition] = {
            "tasks": len(rows), "tp": tp, "fp": fp, "gold": gold,
            "precision": precision, "recall": recall, "f1": f1,
            "false_alarm_rate": far,
            "mean_tokens": sum(float(row.get("tokens", 0)) for row in rows) / max(1, len(rows)),
            "mean_latency_seconds": sum(float(row.get("latency_seconds", 0)) for row in rows) / max(1, len(rows)),
        }
        semantic_tp = sum(len(set(row.get("semantic_anywhere_gold_ids", []))) for row in rows)
        semantic_fp = sum(int(row.get("semantic_anywhere_fp_count", row.get("fp_count", 0))) for row in rows)
        semantic_precision = semantic_tp / (semantic_tp + semantic_fp) if semantic_tp + semantic_fp else 0.0
        semantic_recall = semantic_tp / gold if gold else 0.0
        semantic_f1 = 2 * semantic_precision * semantic_recall / (semantic_precision + semantic_recall) if semantic_precision + semantic_recall else 0.0
        semantic_condition_metrics[condition] = {
            "tasks": len(rows), "tp": semantic_tp, "fp": semantic_fp, "gold": gold,
            "precision": semantic_precision, "recall": semantic_recall, "f1": semantic_f1,
            "relocated_matches": sum(int(row.get("relocated_match_count", 0)) for row in rows),
            "duplicate_comments": sum(int(row.get("duplicate_semantic_count", 0)) for row in rows),
            "hallucinations": sum(int(row.get("hallucination_count", row.get("fp_count", 0))) for row in rows),
        }

    transitions = {}
    for condition in sorted(by_condition):
        if condition == baseline:
            continue
        gain = suppression = induced_fp = paired = 0
        for (task_id, _repeat), conditions in by_task.items():
            if baseline not in conditions or condition not in conditions:
                continue
            paired += 1
            base = conditions[baseline]
            current = conditions[condition]
            gain += len(current["matched_gold_ids"] - base["matched_gold_ids"])
            suppression += len(base["matched_gold_ids"] - current["matched_gold_ids"])
            induced_fp += max(0, int(current.get("fp_count", 0)) - int(base.get("fp_count", 0)))
        transitions[condition] = {
            "paired_tasks": paired, "context_gain": gain,
            "context_suppression": suppression, "context_induced_fp": induced_fp,
        }
    raw_outcomes = []
    for (task_id, _repeat), conditions in by_task.items():
        base = conditions.get(baseline)
        if base is None:
            continue
        for condition, current in sorted(conditions.items()):
            raw_outcomes.append({
                "task_id": task_id, "condition": condition,
                "tp": len(current["matched_gold_ids"]), "fp": int(current.get("fp_count", 0)),
                "suppression": len(base["matched_gold_ids"] - current["matched_gold_ids"]),
                "tokens": current.get("tokens", 0), "latency_seconds": current.get("latency_seconds", 0),
            })
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in raw_outcomes:
        grouped[(row["task_id"], row["condition"])].append(row)
    for (task_id, condition), rows in sorted(grouped.items()):
        selector_outcomes.append({
            "task_id": task_id, "condition": condition, "repeats": len(rows),
            **{key: sum(float(row[key]) for row in rows) / len(rows)
               for key in ("tp", "fp", "suppression", "tokens", "latency_seconds")},
        })
    return {
        "baseline": baseline, "conditions": condition_metrics,
        "semantic_anywhere_conditions": semantic_condition_metrics,
        "transitions": transitions, "selector_outcomes": selector_outcomes,
    }
