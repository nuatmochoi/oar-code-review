"""Parse ordinary unified diffs and display unambiguous source coordinates.

This module uses only its input string.  It reads no files and resolves no
symbols.  Paths retain their header spelling (including a/b prefixes or git
quotes); a tab-separated timestamp is excluded from the path.

``parse_diff`` returns dictionaries with old_path/new_path, old_start/count,
new_start/count, the original hunk header, its 1-based physical ``diff_line``,
and ``rows``.  Each row has kind, old_line, new_line, code, original and its
1-based physical diff_line.  ``original`` includes the original line ending.
For source rows ``code`` removes only the first diff sigil, keeping that line
ending.  A no-newline marker has no source coordinates and code="".

``render_numbered_diff`` prefixes EVERY physical input line exactly once with
``[BASE=<number or -> HEAD=<number or ->] ``.  Non-source metadata and no-newline
markers have '-' on both sides.  The rest of each line is untouched, so removing
one leading prefix from every rendered line reconstructs the exact input.
Deleted source rows never receive a HEAD coordinate.
"""

from __future__ import annotations

import re
from typing import Any


class DiffParseError(ValueError):
    """An invalid or unsupported ordinary unified diff."""


_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")
_NO_NEWLINE = "\\ No newline at end of file"


def _physical_lines(text: str) -> list[str]:
    # str.splitlines() also splits U+2028, form feed and other characters which
    # can occur INSIDE source code.  Only LF terminates a unified-diff record;
    # a preceding CR remains part of the exact CRLF spelling.
    return re.findall(r"[^\n]*\n|[^\n]+$", text)


def _body(original: str) -> str:
    if original.endswith("\r\n"):
        return original[:-2]
    if original.endswith("\n"):
        return original[:-1]
    return original


def _path_from_header(original: str) -> str:
    path = _body(original)[4:].partition("\t")[0]
    if not path:
        raise DiffParseError("A file header has an empty path")
    return path


def _complete(hunk: dict[str, Any], old_used: int, new_used: int) -> bool:
    return old_used == hunk["old_count"] and new_used == hunk["new_count"]


def _count_error(hunk: dict[str, Any], old_used: int, new_used: int) -> DiffParseError:
    return DiffParseError(
        f"Hunk at diff line {hunk['diff_line']} declares "
        f"BASE={hunk['old_count']}, HEAD={hunk['new_count']} rows, "
        f"but encountered BASE={old_used}, HEAD={new_used}"
    )


def parse_diff(text: str) -> list[dict[str, Any]]:
    """Return ordinary unified-diff hunks, checking both side counts strictly.

    Empty input, metadata-only diffs and binary-file notices yield no hunks.
    Hunk rows require file headers.  Combined (@@@) diffs are rejected rather
    than being assigned fabricated two-way coordinates.  Counts omitted from
    a hunk header mean one; zero counts consume no source rows on that side.
    """
    if not isinstance(text, str):
        raise TypeError("Unified diff input must be a string")
    lines = _physical_lines(text)
    hunks: list[dict[str, Any]] = []
    old_path: str | None = None
    new_path: str | None = None
    active: dict[str, Any] | None = None
    old_used = new_used = 0
    last_was_code = False
    i = 0

    while i < len(lines):
        original = lines[i]
        body = _body(original)

        if body == _NO_NEWLINE:
            if active is None or not last_was_code:
                raise DiffParseError(f"Orphan no-newline marker at diff line {i + 1}")
            active["rows"].append(
                {"kind": "no_newline", "old_line": None, "new_line": None,
                 "code": "", "original": original, "diff_line": i + 1}
            )
            last_was_code = False
            i += 1
            continue

        if active is not None and not _complete(active, old_used, new_used):
            if not body or body[0] not in " +-":
                raise _count_error(active, old_used, new_used)
            sigil = body[0]
            takes_old = sigil != "+"
            takes_new = sigil != "-"
            next_old = old_used + int(takes_old)
            next_new = new_used + int(takes_new)
            if next_old > active["old_count"] or next_new > active["new_count"]:
                raise _count_error(active, next_old, next_new)
            active["rows"].append(
                {"kind": {" ": "context", "+": "add", "-": "delete"}[sigil],
                 "old_line": active["old_start"] + old_used if takes_old else None,
                 "new_line": active["new_start"] + new_used if takes_new else None,
                 "code": original[1:], "original": original, "diff_line": i + 1}
            )
            old_used, new_used = next_old, next_new
            last_was_code = True
            i += 1
            continue

        # A paired file header is metadata only between (completed) hunks.
        # While inside an incomplete hunk, ---/+++ can instead be real deleted
        # or added code and are correctly consumed by the source-row branch.
        if body.startswith("--- "):
            if i + 1 >= len(lines) or not _body(lines[i + 1]).startswith("+++ "):
                raise DiffParseError(f"Missing +++ file header after diff line {i + 1}")
            old_path = _path_from_header(original)
            new_path = _path_from_header(lines[i + 1])
            active = None
            last_was_code = False
            i += 2
            continue

        if body.startswith("@@"):
            match = _HUNK.fullmatch(body)
            if match is None:
                raise DiffParseError(f"Invalid two-way hunk header at diff line {i + 1}")
            if old_path is None or new_path is None:
                raise DiffParseError(f"Hunk at diff line {i + 1} has no file headers")
            old_start = int(match[1])
            old_count = int(match[2]) if match[2] is not None else 1
            new_start = int(match[3])
            new_count = int(match[4]) if match[4] is not None else 1
            if (old_count and old_start == 0) or (new_count and new_start == 0):
                raise DiffParseError(f"Nonempty range starts at zero at diff line {i + 1}")
            if old_path == "/dev/null" and old_count:
                raise DiffParseError(f"Added file consumes BASE rows at diff line {i + 1}")
            if new_path == "/dev/null" and new_count:
                raise DiffParseError(f"Deleted file consumes HEAD rows at diff line {i + 1}")
            active = {
                "old_path": old_path, "new_path": new_path,
                "old_start": old_start, "old_count": old_count,
                "new_start": new_start, "new_count": new_count,
                "header": original, "diff_line": i + 1, "rows": [],
            }
            hunks.append(active)
            old_used = new_used = 0
            last_was_code = False
            i += 1
            continue

        if body.startswith("diff --git ") or body.startswith("Index: "):
            # A new file must supply its own ---/+++ pair before a text hunk.
            old_path = new_path = None
            active = None
        elif body.startswith("+++ "):
            raise DiffParseError(f"Orphan +++ file header at diff line {i + 1}")
        elif active is not None and body and body[0] in " +-":
            # Counts were already fulfilled.  Do not silently treat an extra
            # source row as ignorable trailing metadata.
            raise DiffParseError(f"Extra source row after hunk at diff line {i + 1}")
        elif body.startswith("\\ "):
            raise DiffParseError(f"Unknown hunk marker at diff line {i + 1}")
        last_was_code = False
        i += 1

    if active is not None and not _complete(active, old_used, new_used):
        raise _count_error(active, old_used, new_used)
    return hunks


def render_numbered_diff(text: str) -> str:
    """Prefix exact input lines with explicit BASE/HEAD coordinates.

    Parse errors propagate.  Headers, metadata and no-newline markers receive
    '-' for both coordinates.  A deletion always has HEAD=-, an addition
    always has BASE=-; neither is assigned the neighboring source coordinate.
    """
    hunks = parse_diff(text)
    coordinates = {
        row["diff_line"]: (row["old_line"], row["new_line"])
        for hunk in hunks for row in hunk["rows"]
    }
    output = []
    for index, original in enumerate(_physical_lines(text), 1):
        old, new = coordinates.get(index, (None, None))
        old_label = "-" if old is None else str(old)
        new_label = "-" if new is None else str(new)
        output.append(f"[BASE={old_label} HEAD={new_label}] {original}")
    return "".join(output)
