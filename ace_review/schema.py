from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class ContextBlock:
    id: str
    kind: str
    content: str
    file: str = ""
    start_line: int | None = None
    end_line: int | None = None
    query: str = ""
    relation: str = ""
    confidence: float = 1.0
    token_cost: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if not data["token_cost"]:
            data["token_cost"] = estimate_tokens(self.content)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ContextBlock":
        return cls(**data)


@dataclass(frozen=True)
class GoldIssue:
    id: str
    claim: str
    file: str = ""
    start_line: int | None = None
    end_line: int | None = None
    category: str = "unknown"
    context_level: str = "unknown"
    is_ai_comment: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GoldIssue":
        return cls(**data)


@dataclass(frozen=True)
class ReviewTask:
    id: str
    repo: str
    language: str
    base_commit: str
    head_commit: str
    diff: str
    pr_url: str = ""
    title: str = ""
    description: str = ""
    changed_files: tuple[str, ...] = ()
    gold_issues: tuple[GoldIssue, ...] = ()
    cached_contexts: tuple[ContextBlock, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["changed_files"] = list(self.changed_files)
        data["gold_issues"] = [issue.to_dict() for issue in self.gold_issues]
        data["cached_contexts"] = [block.to_dict() for block in self.cached_contexts]
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ReviewTask":
        payload = dict(data)
        payload["changed_files"] = tuple(payload.get("changed_files", ()))
        payload["gold_issues"] = tuple(
            GoldIssue.from_dict(item) for item in payload.get("gold_issues", ())
        )
        payload["cached_contexts"] = tuple(
            ContextBlock.from_dict(item) for item in payload.get("cached_contexts", ())
        )
        return cls(**payload)


def estimate_tokens(text: str) -> int:
    """Stable model-independent approximation used only for budget accounting."""
    return max(1, (len(text.encode("utf-8")) + 3) // 4) if text else 0
