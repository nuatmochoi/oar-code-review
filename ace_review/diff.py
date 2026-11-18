from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

from .schema import GoldIssue, ReviewTask

IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
STOP = {
    "and", "break", "case", "class", "const", "continue", "default", "def",
    "else", "false", "for", "from", "func", "function", "if", "import",
    "interface", "let", "new", "nil", "none", "null", "package", "private",
    "public", "return", "static", "struct", "true", "type", "var", "void",
    "while",
}


def changed_lines(diff: str) -> list[str]:
    return [
        line[1:]
        for line in diff.splitlines()
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
    ]


def identifiers(diff: str) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for token in IDENTIFIER.findall("\n".join(changed_lines(diff))):
        lowered = token.lower()
        if lowered in STOP or token in seen:
            continue
        seen.add(token)
        result.append(token)
    return result


def change_features(diff: str) -> dict[str, float]:
    lines = changed_lines(diff)
    text = "\n".join(lines)
    lower = text.lower()
    return {
        "changed_loc": float(len(lines)),
        "identifier_count": float(len(identifiers(diff))),
        "has_signature_change": float(bool(re.search(r"\b(func|def|function|class|interface)\b", text))),
        "has_type_change": float(bool(re.search(r"\b(type|struct|class|interface|enum)\b", text))),
        "has_control_change": float(bool(re.search(r"\b(if|else|switch|case|for|while)\b", text))),
        "has_error_change": float(any(word in lower for word in ("error", "exception", "panic", "catch", "nil", "null"))),
        "has_test_change": float("test" in lower or "assert" in lower),
        "has_build_change": float(any(word in lower for word in ("docker", "gradle", "maven", "package.json", "go.mod"))),
        "has_io_change": float(any(word in lower for word in ("request", "response", "query", "file", "socket", "http"))),
    }


def split_unified_diff(diff: str) -> list[tuple[str, str]]:
    """Return deterministic per-hunk units without consulting gold locations."""
    units: list[tuple[str, str]] = []
    file_header: list[str] = []
    hunk: list[str] = []
    current_file = ""
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            if hunk:
                units.append((current_file, "\n".join((*file_header, *hunk)) + "\n"))
            parts = line.split()
            current_file = parts[3][2:] if len(parts) >= 4 and parts[3].startswith("b/") else ""
            file_header = [line]
            hunk = []
        elif line.startswith("@@"):
            if hunk:
                units.append((current_file, "\n".join((*file_header, *hunk)) + "\n"))
            hunk = [line]
        elif hunk:
            hunk.append(line)
        else:
            file_header.append(line)
    if hunk:
        units.append((current_file, "\n".join((*file_header, *hunk)) + "\n"))
    return units


def split_task_hunks(task: ReviewTask) -> list[ReviewTask]:
    units = split_unified_diff(task.diff)
    if not units:
        return [task]
    result = []
    for index, (file, hunk) in enumerate(units, 1):
        result.append(replace(
            task, id=f"{task.id}#h{index:03d}", diff=hunk,
            changed_files=(file,) if file else task.changed_files,
            gold_issues=tuple(issue for issue in task.gold_issues if _issue_in_hunk(issue, file, hunk)),
            metadata=task.metadata | {
                "parent_task_id": task.id, "hunk_index": index, "inference_unit": "hunk",
                "parent_diff": task.diff, "parent_changed_files": list(task.changed_files),
            },
        ))
    return result


def _issue_in_hunk(issue: GoldIssue, file: str, hunk: str) -> bool:
    """Assign evaluation labels to hunks by new-file location, never by claim text."""
    if issue.file and file and issue.file.lstrip("./") != file.lstrip("./"):
        return False
    lines = new_side_line_numbers(hunk).get(file, set())
    if issue.start_line is None and issue.end_line is None:
        return bool(not issue.file or issue.file.lstrip("./") == file.lstrip("./"))
    start = issue.start_line if issue.start_line is not None else issue.end_line
    end = issue.end_line if issue.end_line is not None else issue.start_line
    assert start is not None and end is not None
    low, high = sorted((start, end))
    return any(low <= line <= high for line in lines)


def new_side_line_numbers(diff: str) -> dict[str, set[int]]:
    """Return non-deleted line numbers represented by each unified-diff hunk."""
    result: dict[str, set[int]] = {}
    current_file = ""
    new_line = 0
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            current_file = line[6:]
            result.setdefault(current_file, set())
        elif line.startswith("@@"):
            match = re.search(r"\+(\d+)", line)
            if match:
                new_line = int(match.group(1))
        elif line.startswith("-") and not line.startswith("---"):
            continue
        elif current_file and new_line and not line.startswith("\\ No newline"):
            result[current_file].add(new_line)
            new_line += 1
    return result


def changed_identifier_positions(diff: str, limit: int = 12) -> list[tuple[str, int, int, str]]:
    current_file = ""
    new_line = 0
    positions: list[tuple[str, int, int, str]] = []
    seen: set[str] = set()
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            current_file = line[6:]
        elif line.startswith("@@"):
            match = re.search(r"\+(\d+)", line)
            if match:
                new_line = int(match.group(1))
        elif line.startswith("-") and not line.startswith("---"):
            continue
        elif line.startswith("+") and not line.startswith("+++"):
            code = line[1:]
            for match in IDENTIFIER.finditer(code):
                symbol = match.group(0)
                if symbol.lower() in STOP or symbol in seen:
                    continue
                seen.add(symbol)
                positions.append((current_file, new_line, match.start() + 1, symbol))
                if len(positions) >= limit:
                    return positions
            new_line += 1
        elif current_file and new_line:
            new_line += 1
    return positions


def added_line_numbers(diff: str) -> dict[str, set[int]]:
    result: dict[str, set[int]] = {}
    current_file = ""
    new_line = 0
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            current_file = line[6:]
            result.setdefault(current_file, set())
        elif line.startswith("@@"):
            match = re.search(r"\+(\d+)", line)
            if match:
                new_line = int(match.group(1))
        elif line.startswith("-") and not line.startswith("---"):
            continue
        elif line.startswith("+") and not line.startswith("+++"):
            if current_file and line[1:].strip():
                result[current_file].add(new_line)
            new_line += 1
        elif current_file and new_line:
            new_line += 1
    return result


def ranked_added_line_numbers(diff: str, limit: int = 64) -> tuple[dict[str, set[int]], int]:
    """Gold-blind risk ranking that bounds expensive whole-PR flow sources."""
    candidates: list[tuple[float, int, str, int]] = []
    current_file = ""
    new_line = 0
    order = 0
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            current_file = line[6:]
        elif line.startswith("@@"):
            match = re.search(r"\+(\d+)", line)
            if match:
                new_line = int(match.group(1))
        elif line.startswith("-") and not line.startswith("---"):
            continue
        elif line.startswith("+") and not line.startswith("+++"):
            code = line[1:].strip()
            if current_file and code and not code.startswith(("//", "/*", "*")):
                score = 0.0
                lowered_file = current_file.lower()
                if "test" not in Path(current_file).name.lower():
                    score += 4.0
                if not code.startswith(("package ", "import ")):
                    score += 2.0
                if re.search(r"\b(if|switch|select|return|go|defer|panic)\b", code):
                    score += 3.0
                if re.search(r"\b(?:u?int(?:8|16|32|64)?|float(?:32|64)?|string|byte|rune)\s*\(", code):
                    score += 3.0
                if re.search(r"(?::=|[^=!<>]=[^=]|\w+\s*\()", code):
                    score += 2.0
                if lowered_file.endswith((".go", ".c", ".cc", ".cpp", ".h", ".hpp")):
                    score += 1.0
                candidates.append((-score, order, current_file, new_line))
                order += 1
            new_line += 1
        elif current_file and new_line:
            new_line += 1
    selected: dict[str, set[int]] = {}
    for _score, _order, file, line in sorted(candidates)[:limit]:
        selected.setdefault(file, set()).add(line)
    return selected, len(candidates)
