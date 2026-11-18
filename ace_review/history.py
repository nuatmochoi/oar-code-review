from __future__ import annotations

import re
import subprocess
from pathlib import Path

from .diff import identifiers
from .schema import ContextBlock, ReviewTask


def _git(repo: Path, args: list[str], timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=repo, text=True, capture_output=True,
        timeout=timeout, check=False,
    )


def _bounded_patch(text: str, query_terms: set[str], max_lines: int = 100) -> str:
    """Keep commit metadata and the most query-relevant diff hunks."""
    lines = text.splitlines()
    prefix: list[str] = []
    hunks: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if line.startswith("@@"):
            if current:
                hunks.append(current)
            current = [line]
        elif current:
            current.append(line)
        elif len(prefix) < 12:
            prefix.append(line)
    if current:
        hunks.append(current)
    ranked = sorted(
        hunks,
        key=lambda h: -sum(sum(term in line.lower() for term in query_terms) for line in h),
    )
    output = prefix[:]
    for hunk in ranked:
        remaining = max_lines - len(output)
        if remaining <= 0:
            break
        output.extend(hunk[:remaining])
    return "\n".join(output)


def retrieve_history_context(
    task: ReviewTask,
    repo_path: str | Path,
    top_k: int = 4,
    max_changed_files: int = 12,
    max_identifiers: int = 10,
) -> list[ContextBlock]:
    """Retrieve pre-change commits touching changed files/symbols.

    All searches start at the merge base, so PR commits and later history cannot leak.
    """
    repo = Path(repo_path)
    base = str(task.metadata.get("merge_base_commit") or task.base_commit)
    files = list(task.changed_files)[:max_changed_files]
    symbols = identifiers(task.diff)[:max_identifiers]
    if not base or not files:
        return []

    candidates: dict[str, dict] = {}
    recent = _git(repo, ["log", "--format=%H%x09%ct%x09%s", "-n", str(max(12, top_k * 4)), base, "--", *files])
    if recent.returncode == 0:
        for rank, line in enumerate(recent.stdout.splitlines(), 1):
            parts = line.split("\t", 2)
            if len(parts) != 3:
                continue
            commit, timestamp, subject = parts
            subject_lower = subject.lower()
            subject_hits = {symbol for symbol in symbols if symbol.lower() in subject_lower}
            candidates.setdefault(commit, {
                "timestamp": int(timestamp), "subject": subject,
                "symbol_hits": subject_hits, "recency": rank,
            })

    ranked = sorted(
        candidates.items(),
        key=lambda item: (-len(item[1]["symbol_hits"]), item[1]["recency"], -item[1]["timestamp"], item[0]),
    )[:top_k]
    terms = {symbol.lower() for symbol in symbols}
    blocks: list[ContextBlock] = []
    for position, (commit, info) in enumerate(ranked, 1):
        shown = _git(
            repo,
            ["show", "--format=commit %H%nDate: %cI%nSubject: %s", "--no-ext-diff", "--unified=3", commit, "--", *files],
        )
        if shown.returncode:
            continue
        content = _bounded_patch(shown.stdout, terms)
        if not content.strip():
            continue
        confidence = float(10 * len(info["symbol_hits"]) + 1 / max(1, info["recency"]))
        touched = re.findall(r"^\+\+\+ b/(.+)$", content, flags=re.MULTILINE)
        blocks.append(ContextBlock(
            id=f"HIST-{position:02d}", kind="history", content=content,
            file=touched[0] if len(set(touched)) == 1 else "",
            query=" ".join(symbols), relation="pre_change_history",
            confidence=confidence,
            metadata={
                "rank": position, "commit": commit, "base_commit": base,
                "symbol_hits": sorted(info["symbol_hits"]), "subject": info["subject"],
                "leakage_guard": "git log rooted at merge base",
            },
        ))
    return blocks
