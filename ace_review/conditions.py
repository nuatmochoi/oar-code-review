from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .schema import ContextBlock, ReviewTask, estimate_tokens


@dataclass(frozen=True)
class Condition:
    name: str
    kinds: tuple[str, ...]


CONDITIONS = {
    condition.name: condition for condition in (
        Condition("diff", ()),
        Condition("random", ("random",)),
        Condition("bm25", ("bm25",)),
        Condition("neighbor", ("neighbor",)),
        Condition("lsp", ("lsp",)),
        Condition("dataflow", ("dataflow",)),
        Condition("buildtest", ("buildtest",)),
        Condition("testsource", ("testsource",)),
        Condition("history", ("history",)),
        Condition("cochange", ("cochange",)),
        Condition("targetedtest", ("targetedtest",)),
        Condition("test_history", ("testsource", "history")),
        Condition("lsp_df", ("lsp", "dataflow")),
        Condition("lsp_buildtest", ("lsp", "buildtest")),
        Condition("dataflow_buildtest", ("dataflow", "buildtest")),
        Condition("structured_all", ("lsp", "dataflow", "buildtest")),
        Condition("stuffed_all", ("bm25", "neighbor", "lsp", "dataflow", "buildtest")),
    )
}

SELECTION_PRIORITY = {"targetedtest": 0, "buildtest": 1, "testsource": 2, "dataflow": 3, "lsp": 4, "cochange": 5, "history": 6, "bm25": 7, "neighbor": 8, "similar": 9, "random": 10}
PRESENTATION_PRIORITY = {"lsp": 0, "dataflow": 1, "testsource": 2, "cochange": 3, "history": 4, "targetedtest": 5, "buildtest": 6, "bm25": 7, "neighbor": 8, "similar": 9, "random": 10}


def select_contexts(
    task: ReviewTask,
    condition_name: str,
    extra: Iterable[ContextBlock] = (),
    token_budget: int = 4096,
) -> list[ContextBlock]:
    condition = CONDITIONS[condition_name]
    candidates = [block for block in (*task.cached_contexts, *tuple(extra)) if block.kind in condition.kinds]
    candidates.sort(key=lambda block: (SELECTION_PRIORITY.get(block.kind, 99), -block.confidence, block.id))
    selected: list[ContextBlock] = []
    used = 0
    for block in candidates:
        cost = block.token_cost or estimate_tokens(block.content)
        if used + cost > token_budget:
            continue
        selected.append(block)
        used += cost
    selected.sort(key=lambda block: (PRESENTATION_PRIORITY.get(block.kind, 99), block.id))
    return selected


def make_request(task: ReviewTask, condition_name: str, contexts: list[ContextBlock]) -> dict:
    full_diff = task.metadata.get("parent_diff", task.diff)
    return {
        "task_id": task.id,
        "condition": condition_name,
        "repository": task.repo,
        "language": task.language,
        "pr": {"url": task.pr_url, "title": task.title, "description": task.description},
        "diff": full_diff,
        "focus_diff": task.diff if full_diff != task.diff else "",
        "contexts": [block.to_dict() for block in contexts],
        "output_schema": {
            "issues": [{
                "file": "string", "line": "integer", "category": "string",
                "claim": "string", "evidence_ids": ["string"],
                "failure_scenario": "string", "suggestion": "string",
                "confidence": "number",
            }]
        },
    }
