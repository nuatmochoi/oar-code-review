from __future__ import annotations

import hashlib

from .schema import estimate_tokens


def _truncate(text: str, tokens: int) -> str:
    if tokens <= 0:
        return ""
    return text.encode("utf-8")[: tokens * 4].decode("utf-8", errors="ignore")


def make_token_matched_placebos(
    request_rows: list[dict],
    source_conditions: tuple[str, ...] = ("testsource", "history"),
    seed: int = 20260902,
) -> list[dict]:
    """Replace evidence with equal-budget blocks drawn from other repositories."""
    by_condition = {
        condition: [row for row in request_rows if row.get("condition") == condition]
        for condition in source_conditions
    }
    output = []
    for condition, rows in by_condition.items():
        for row in rows:
            target_tokens = int(row.get("context_status", {}).get("context_tokens", 0))
            donors = [candidate for candidate in rows if candidate.get("repository") != row.get("repository")]
            donors.sort(key=lambda candidate: hashlib.sha256(
                f"{seed}:{row['task_id']}:{candidate['task_id']}".encode()
            ).hexdigest())
            pool = [
                (donor, block)
                for donor in donors
                for block in donor.get("contexts", [])
            ]
            selected = []
            used = 0
            for index, (donor, block) in enumerate(pool, 1):
                remaining = target_tokens - used
                if remaining <= 0:
                    break
                content = str(block.get("content", ""))
                cost = estimate_tokens(content)
                if cost > remaining:
                    content = _truncate(content, remaining)
                    cost = estimate_tokens(content)
                if not content:
                    continue
                cloned = dict(block)
                cloned.update({
                    "id": f"PLACEBO-{condition.upper()}-{index:02d}",
                    "content": content, "token_cost": cost,
                    "metadata": dict(block.get("metadata", {})) | {
                        "placebo": True, "donor_task_id": donor["task_id"],
                        "donor_repository": donor["repository"], "seed": seed,
                    },
                })
                selected.append(cloned)
                used += cost
            cloned_row = dict(row)
            cloned_row["condition"] = f"{condition}_placebo"
            cloned_row["contexts"] = selected
            cloned_row["context_status"] = {
                "requested": [condition], "present": [condition] if selected else [],
                "unavailable": [] if selected else [condition], "budget_omitted": [],
                "token_budget": target_tokens, "context_tokens": used,
                "placebo": True, "seed": seed, "donor_policy": "different_repository",
            }
            output.append(cloned_row)
    return output
