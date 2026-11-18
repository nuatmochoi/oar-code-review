from __future__ import annotations

import json
import math
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from .diff import change_features
from .schema import ReviewTask

FEATURE_NAMES = (
    "changed_loc", "identifier_count", "has_signature_change", "has_type_change",
    "has_control_change", "has_error_change", "has_test_change", "has_build_change",
    "has_io_change", "gold_issue_count",
)


def task_features(task: ReviewTask) -> dict[str, float]:
    result = change_features(task.diff)
    result["gold_issue_count"] = 0.0  # Never expose gold at train or inference time.
    return result


def utility(row: dict, fp_weight: float = 2.0, suppression_weight: float = 0.1, token_weight: float = 0.00001, latency_weight: float = 0.001) -> float:
    return (
        float(row.get("tp", 0))
        - fp_weight * float(row.get("fp", 0))
        - suppression_weight * float(row.get("suppression", 0))
        - token_weight * float(row.get("tokens", 0))
        - latency_weight * float(row.get("latency_seconds", 0))
    )


@dataclass
class UtilitySelector:
    conditions: tuple[str, ...]
    means: dict[str, float]
    scales: dict[str, float]
    training: list[dict]
    neighbors: int = 15

    @classmethod
    def fit(cls, tasks: list[ReviewTask], outcome_rows: list[dict], neighbors: int = 15) -> "UtilitySelector":
        outcomes: dict[str, dict[str, float]] = defaultdict(dict)
        for row in outcome_rows:
            outcomes[row["task_id"]][row["condition"]] = utility(row)
        conditions = tuple(sorted({row["condition"] for row in outcome_rows}))
        feature_rows = [task_features(task) for task in tasks if task.id in outcomes]
        means = {name: sum(row[name] for row in feature_rows) / max(1, len(feature_rows)) for name in FEATURE_NAMES}
        scales = {}
        for name in FEATURE_NAMES:
            variance = sum((row[name] - means[name]) ** 2 for row in feature_rows) / max(1, len(feature_rows))
            scales[name] = max(math.sqrt(variance), 1.0)
        training = []
        for task in tasks:
            if task.id not in outcomes:
                continue
            training.append({"task_id": task.id, "repo": task.repo, "features": task_features(task), "utilities": outcomes[task.id]})
        return cls(conditions, means, scales, training, neighbors)

    def predict(self, task: ReviewTask) -> dict:
        target = task_features(task)
        ranked: list[tuple[float, dict]] = []
        for row in self.training:
            distance = math.sqrt(sum(
                ((target[name] - row["features"][name]) / self.scales[name]) ** 2
                for name in FEATURE_NAMES
            ))
            ranked.append((distance, row))
        nearest = sorted(ranked, key=lambda item: item[0])[: self.neighbors]
        predicted: dict[str, float] = {}
        for condition in self.conditions:
            weighted = [(1 / (distance + 1e-6), row["utilities"][condition]) for distance, row in nearest if condition in row["utilities"]]
            predicted[condition] = sum(weight * value for weight, value in weighted) / sum(weight for weight, _ in weighted) if weighted else float("-inf")
        selected = max(predicted, key=predicted.get) if predicted else "diff"
        return {"condition": selected, "predicted_utilities": predicted, "neighbors": [row["task_id"] for _, row in nearest]}

    def to_dict(self) -> dict:
        return {
            "conditions": list(self.conditions), "means": self.means, "scales": self.scales,
            "training": self.training, "neighbors": self.neighbors,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "UtilitySelector":
        return cls(tuple(data["conditions"]), data["means"], data["scales"], data["training"], data["neighbors"])

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "UtilitySelector":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
