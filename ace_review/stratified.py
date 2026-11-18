from __future__ import annotations

from collections import Counter, defaultdict

from .schema import ReviewTask


def stratified_metrics(tasks: list[ReviewTask], labels: list[dict], requests: list[dict] | None = None) -> dict:
    task_map = {task.id: task for task in tasks}
    gold_map = {task.id: {gold.id: gold for gold in task.gold_issues} for task in tasks}
    conditions = sorted({row["condition"] for row in labels})

    def breakdown(attribute: str, match_key: str) -> dict:
        result = {}
        for condition in conditions:
            tp = Counter(); denominator = Counter()
            for row in labels:
                if row["condition"] != condition or row["task_id"] not in task_map:
                    continue
                for gold in task_map[row["task_id"]].gold_issues:
                    denominator[getattr(gold, attribute)] += 1
                for gold_id in row.get(match_key, []):
                    gold = gold_map[row["task_id"]].get(gold_id)
                    if gold:
                        tp[getattr(gold, attribute)] += 1
            result[condition] = {
                value: {"tp": tp[value], "gold": count, "recall": tp[value] / count if count else 0.0}
                for value, count in sorted(denominator.items())
            }
        return result

    repository = {}
    for repo in sorted({task.repo for task in tasks}):
        repository[repo] = {}
        ids = {task.id for task in tasks if task.repo == repo}
        for condition in conditions:
            rows = [row for row in labels if row["task_id"] in ids and row["condition"] == condition]
            tp = sum(len(set(row.get("matched_gold_ids", []))) for row in rows)
            fp = sum(int(row.get("fp_count", 0)) for row in rows)
            gold = sum(len(task_map[row["task_id"]].gold_issues) for row in rows)
            precision = tp / (tp + fp) if tp + fp else 0.0
            recall = tp / gold if gold else 0.0
            f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
            repository[repo][condition] = {"tp": tp, "fp": fp, "gold": gold, "precision": precision, "recall": recall, "f1": f1}

    stability = {}
    for condition in conditions:
        grouped = defaultdict(list)
        for row in labels:
            if row["condition"] == condition:
                grouped[row["task_id"]].append(frozenset(row.get("matched_gold_ids", [])))
        stability[condition] = {
            "stable_tasks": sum(len(set(values)) == 1 for values in grouped.values()),
            "tasks": len(grouped),
        }

    availability = {}
    if requests is not None:
        for condition in sorted({row["condition"] for row in requests}):
            rows = [row for row in requests if row["condition"] == condition]
            availability[condition] = {
                "complete": sum(not row.get("context_status", {}).get("unavailable") for row in rows),
                "tasks": len(rows),
                "unavailable_patterns": {
                    ",".join(key) or "none": count
                    for key, count in Counter(tuple(row.get("context_status", {}).get("unavailable", [])) for row in rows).items()
                },
            }
    return {
        "strict_by_context_level": breakdown("context_level", "matched_gold_ids"),
        "semantic_anywhere_by_context_level": breakdown("context_level", "semantic_anywhere_gold_ids"),
        "strict_by_category": breakdown("category", "matched_gold_ids"),
        "repository": repository, "repeat_stability": stability,
        "evidence_availability": availability,
    }
