from __future__ import annotations

from pathlib import Path

from .bm25 import BM25Index, Document
from .diff import identifiers
from .schema import ContextBlock, ReviewTask


def iter_test_documents(repo_path: str | Path, max_lines: int = 100):
    """Yield bounded Go test-source chunks from the reviewed revision."""
    root = Path(repo_path)
    for path in sorted(root.rglob("*_test.go")):
        if not path.is_file() or any(part in {".git", "vendor"} for part in path.relative_to(root).parts):
            continue
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        for start in range(0, len(lines), max_lines):
            chunk = lines[start : start + max_lines]
            if chunk:
                yield Document(str(path.relative_to(root)), start + 1, start + len(chunk), "\n".join(chunk))


def retrieve_test_context(task: ReviewTask, repo_path: str | Path, top_k: int = 4) -> list[ContextBlock]:
    """Retrieve existing tests that encode contracts related to changed symbols.

    Changed test files are excluded because their contents are already in the PR diff.
    """
    index = BM25Index(iter_test_documents(repo_path))
    query = " ".join(identifiers(task.diff)) + " " + task.title + " " + task.description
    results = index.search(query, top_k=top_k, excluded_files=set(task.changed_files))
    return [
        ContextBlock(
            id=f"TEST-{position:02d}", kind="testsource", content=document.text,
            file=document.file, start_line=document.start_line, end_line=document.end_line,
            query=query, relation="tests_related_symbols", confidence=score,
            metadata={"rank": position, "bm25_score": score, "revision": task.head_commit},
        )
        for position, (score, document) in enumerate(results, 1)
    ]
