from __future__ import annotations

from collections import Counter
from pathlib import Path

from .bm25 import BM25Index, Document, SOURCE_EXTENSIONS
from .diff import identifiers
from .history import _git
from .schema import ContextBlock, ReviewTask


def _historical_cochanges(task: ReviewTask, repo_path: Path, max_commits: int) -> Counter[str]:
    base = str(task.metadata.get("merge_base_commit") or task.base_commit)
    changed = set(task.changed_files)
    if not base or not changed:
        return Counter()
    result = _git(repo_path, ["log", f"-n{max_commits}", "--format=%H", base, "--", *sorted(changed)])
    if result.returncode:
        return Counter()
    counts: Counter[str] = Counter()
    for commit in result.stdout.splitlines():
        files = _git(repo_path, ["diff-tree", "--root", "--no-commit-id", "--name-only", "-r", commit])
        if files.returncode == 0:
            counts.update(set(files.stdout.splitlines()) - changed)
    return counts


def retrieve_cochange_context(
    task: ReviewTask,
    repo_path: str | Path,
    snapshot_path: str | Path,
    top_k: int = 4,
    max_commits: int = 30,
    max_candidate_files: int = 24,
    max_lines: int = 100,
) -> list[ContextBlock]:
    """Retrieve current code from files historically co-changed before the PR."""
    snapshot = Path(snapshot_path)
    frequencies = _historical_cochanges(task, Path(repo_path), max_commits)
    candidate_files = [
        file for file, _count in frequencies.most_common()
        if Path(file).suffix.lower() in SOURCE_EXTENSIONS and (snapshot / file).is_file()
    ][:max_candidate_files]
    documents: list[Document] = []
    for file in candidate_files:
        lines = (snapshot / file).read_text(encoding="utf-8", errors="replace").splitlines()
        for start in range(0, len(lines), max_lines):
            chunk = lines[start : start + max_lines]
            if chunk:
                documents.append(Document(file, start + 1, start + len(chunk), "\n".join(chunk)))
    query = " ".join(identifiers(task.diff)) + " " + task.title + " " + task.description
    results = BM25Index(documents).search(query, top_k=top_k)
    return [
        ContextBlock(
            id=f"COCHANGE-{position:02d}", kind="cochange", content=document.text,
            file=document.file, start_line=document.start_line, end_line=document.end_line,
            query=query, relation="historically_cochanged_current_code",
            confidence=score * (1 + frequencies[document.file]),
            metadata={
                "rank": position, "bm25_score": score,
                "cochange_frequency": frequencies[document.file],
                "history_root": str(task.metadata.get("merge_base_commit") or task.base_commit),
                "revision": task.head_commit,
            },
        )
        for position, (score, document) in enumerate(results, 1)
    ]
