from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .diff import identifiers
from .schema import ContextBlock, ReviewTask

TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]+|[가-힣]{2,}")
SOURCE_EXTENSIONS = {
    ".c", ".cc", ".cpp", ".cs", ".go", ".h", ".hpp", ".java", ".js",
    ".jsx", ".php", ".py", ".rb", ".rs", ".swift", ".ts", ".tsx",
}
SKIP_PARTS = {".git", "node_modules", "vendor", "dist", "build", "target"}


def tokenize(text: str) -> list[str]:
    return [token.lower() for token in TOKEN.findall(text)]


@dataclass(frozen=True)
class Document:
    file: str
    start_line: int
    end_line: int
    text: str


def iter_documents(repo_path: str | Path, max_lines: int = 120) -> Iterable[Document]:
    root = Path(repo_path)
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in SOURCE_EXTENSIONS:
            continue
        if any(part in SKIP_PARTS for part in path.relative_to(root).parts):
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for start in range(0, len(lines), max_lines):
            chunk = lines[start : start + max_lines]
            if chunk:
                yield Document(str(path.relative_to(root)), start + 1, start + len(chunk), "\n".join(chunk))


class BM25Index:
    def __init__(self, documents: Iterable[Document], k1: float = 1.5, b: float = 0.75):
        self.documents = list(documents)
        self.k1 = k1
        self.b = b
        self.terms = [Counter(tokenize(document.text)) for document in self.documents]
        self.lengths = [sum(counter.values()) for counter in self.terms]
        self.avg_length = sum(self.lengths) / max(1, len(self.lengths))
        self.df: Counter[str] = Counter()
        for counter in self.terms:
            self.df.update(counter.keys())

    def search(self, query: str, top_k: int = 5, excluded_files: set[str] | None = None) -> list[tuple[float, Document]]:
        excluded_files = excluded_files or set()
        query_terms = tokenize(query)
        total = len(self.documents)
        scored: list[tuple[float, Document]] = []
        for document, frequencies, length in zip(self.documents, self.terms, self.lengths):
            if document.file in excluded_files:
                continue
            score = 0.0
            for term in query_terms:
                frequency = frequencies.get(term, 0)
                if not frequency:
                    continue
                idf = math.log(1 + (total - self.df[term] + 0.5) / (self.df[term] + 0.5))
                denominator = frequency + self.k1 * (1 - self.b + self.b * length / max(1, self.avg_length))
                score += idf * frequency * (self.k1 + 1) / denominator
            if score:
                scored.append((score, document))
        return sorted(scored, key=lambda item: (-item[0], item[1].file, item[1].start_line))[:top_k]


def retrieve_bm25(task: ReviewTask, repo_path: str | Path, top_k: int = 5) -> list[ContextBlock]:
    index = BM25Index(iter_documents(repo_path))
    query = " ".join(identifiers(task.diff)) + " " + task.title + " " + task.description
    results = index.search(query, top_k=top_k, excluded_files=set(task.changed_files))
    return [
        ContextBlock(
            id=f"BM25-{position:02d}", kind="bm25", content=document.text,
            file=document.file, start_line=document.start_line, end_line=document.end_line,
            query=query, relation="lexically_similar", confidence=score,
            metadata={"rank": position, "bm25_score": score},
        )
        for position, (score, document) in enumerate(results, 1)
    ]
