from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

from .io import write_tasks
from .schema import ContextBlock, GoldIssue, ReviewTask

csv.field_size_limit(min(sys.maxsize, 2**31 - 1))


def _read_context_csv(path: Path, kind: str, column: str) -> dict[str, ContextBlock]:
    result: dict[str, ContextBlock] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            content = row.get(column, "").strip()
            key = row.get("comment_url", "")
            if content and key:
                result[key] = ContextBlock(
                    id=f"{kind.upper()}-01", kind=kind, content=content,
                    relation={
                        "lsp": "symbol_dependency",
                        "neighbor": "enclosing_symbol",
                        "similar": "similar_cochange",
                        "random": "random_control",
                    }.get(kind, kind),
                    metadata={"source_file": path.name, "cached": True},
                )
    return result


def convert_go_artifact(source: str | Path, output: str | Path) -> int:
    source = Path(source)
    base_path = source / "_extended_data" / "1438_go_from_ref-test.jsonl"
    context_dir = source / "code_context_mangers" / "_context"
    context_specs = [
        ("neighbor", "neighborhood_context", "1438_go_from_ref-test_with_neighborhood(26A-neighbor).csv"),
        ("lsp", "semantic_context", "1438_go_from_ref-test_with_semantic(26A-treesit-both).csv"),
        ("similar", "similar_context", "1438_go_from_ref-test_with_similar(26A-similar-medium-0.001).csv"),
        ("random", "random_context", "1438_go_from_ref-test_with_random(26A-random-8x20).csv"),
    ]
    cached: dict[str, dict[str, ContextBlock]] = {}
    for kind, column, filename in context_specs:
        cached[kind] = _read_context_csv(context_dir / filename, kind, column)

    tasks: list[ReviewTask] = []
    with base_path.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            key = row["comment_url"]
            try:
                files = json.loads(row.get("files", "[]"))
            except json.JSONDecodeError:
                files = []
            changed_files = tuple(item["filename"] for item in files if item.get("filename"))
            contexts = tuple(values[key] for values in cached.values() if key in values)
            tasks.append(
                ReviewTask(
                    id=f"go-{row['id']}", repo=row["repo"], language="go",
                    base_commit=row.get("base_sha", ""),
                    head_commit=row.get("original_commit_id", ""),
                    diff=row.get("hunk_change") or row.get("diff_hunk", ""),
                    pr_url=row.get("pr_url", ""), changed_files=changed_files,
                    gold_issues=(GoldIssue(
                        id=f"go-{row['id']}-human", claim=row.get("msg", ""),
                        file=row.get("path", ""), category="human_review",
                    ),),
                    cached_contexts=contexts,
                    metadata={"comment_url": key, "ghid": row.get("ghid"), "source": "go_lsp_artifact"},
                )
            )
    return write_tasks(output, tasks)


def convert_aacr(source: str | Path, output: str | Path, defects_only: bool = False) -> int:
    records = json.loads(Path(source).read_text(encoding="utf-8"))
    tasks: list[ReviewTask] = []
    for index, row in enumerate(records):
        match = re.search(r"github\.com/([^/]+/[^/]+)/pull/(\d+)", row.get("githubPrUrl", ""))
        if not match:
            continue
        repo, pr_number = match.groups()
        issues: list[GoldIssue] = []
        for issue_index, comment in enumerate(row.get("comments", [])):
            category = comment.get("category", "unknown")
            if defects_only and category not in {"Code Defect", "Security", "Performance"}:
                continue
            issues.append(GoldIssue(
                id=f"aacr-{index}-{issue_index}", claim=comment.get("note", ""),
                file=comment.get("path", ""), start_line=comment.get("from_line"),
                end_line=comment.get("to_line"), category=category,
                context_level=comment.get("context", "unknown"),
                is_ai_comment=comment.get("is_ai_comment"),
            ))
        if defects_only and not issues:
            continue
        tasks.append(ReviewTask(
            id=f"aacr-{repo.replace('/', '__')}-{pr_number}", repo=repo,
            language=row.get("project_main_language", "unknown"),
            base_commit=row.get("source_commit", ""), head_commit=row.get("target_commit", ""),
            diff="", pr_url=row.get("githubPrUrl", ""), gold_issues=tuple(issues),
            metadata={
                "change_line_count": row.get("change_line_count"),
                "source": "aacr_bench", "diff_requires_checkout": True,
            },
        ))
    return write_tasks(output, tasks)
