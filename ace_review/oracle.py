from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from .io import read_jsonl
from .schema import ContextBlock, ReviewTask


def build_oracle_candidates(
    tasks: list[ReviewTask],
    request_paths: list[str | Path],
    context_level: str | None = None,
) -> list[dict]:
    """Create a human-selection sheet; this function never selects using gold text."""
    evidence: dict[str, dict[tuple[str, str], ContextBlock]] = defaultdict(dict)
    for path in request_paths:
        for row in read_jsonl(path):
            for item in row.get("contexts", []):
                block = ContextBlock.from_dict(item)
                evidence[row["task_id"]][(block.kind, block.id)] = block

    rows = []
    for task in tasks:
        candidates = sorted(
            evidence.get(task.id, {}).values(), key=lambda block: (block.kind, block.id),
        )
        for gold in task.gold_issues:
            if context_level and gold.context_level != context_level:
                continue
            rows.append({
                "task_id": task.id,
                "gold_id": gold.id,
                "context_level": gold.context_level,
                "gold_location": {
                    "file": gold.file, "start_line": gold.start_line, "end_line": gold.end_line,
                },
                "gold_claim_for_annotator_only": gold.claim,
                "candidate_evidence": [block.to_dict() for block in candidates],
                "annotation": {
                    "selected_evidence_ids": [],
                    "sufficient_to_verify_defect": None,
                    "missing_evidence_description": "",
                    "minimality_checked": False,
                    "annotator_notes": "",
                },
            })
    return rows
