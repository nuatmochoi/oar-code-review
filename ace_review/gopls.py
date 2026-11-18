from __future__ import annotations

import re
import subprocess
from pathlib import Path

from .diff import changed_identifier_positions
from .schema import ContextBlock, ReviewTask

LOCATION = re.compile(r"^(.+?):(\d+):(\d+)")


def _locations(output: str, root: Path) -> list[tuple[Path, int]]:
    result: list[tuple[Path, int]] = []
    for line in output.splitlines():
        match = LOCATION.match(line.strip())
        if not match:
            continue
        path = Path(match.group(1))
        if not path.is_absolute():
            path = root / path
        try:
            path.resolve().relative_to(root.resolve())
        except ValueError:
            continue
        result.append((path, int(match.group(2))))
    return result


def _snippet(path: Path, line: int, radius: int = 15) -> tuple[str, int, int]:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    start = max(1, line - radius)
    end = min(len(lines), line + radius)
    return "\n".join(lines[start - 1 : end]), start, end


def collect_gopls(
    task: ReviewTask,
    repo_path: str | Path,
    executable: str | Path,
    max_symbols: int = 8,
    max_blocks: int = 8,
    timeout_seconds: int = 90,
) -> tuple[list[ContextBlock], dict]:
    root = Path(repo_path).resolve()
    executable = str(Path(executable).resolve())
    blocks: list[ContextBlock] = []
    seen_locations: set[tuple[str, int, str]] = set()
    errors: list[str] = []
    positions = changed_identifier_positions(task.diff, limit=max_symbols)
    for file, line, column, symbol in positions:
        if not file or not (root / file).exists():
            continue
        target = f"{file}:{line}:{column}"
        for query_kind in ("definition", "references"):
            try:
                completed = subprocess.run(
                    [executable, query_kind, target], cwd=root, text=True,
                    capture_output=True, timeout=timeout_seconds, check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as error:
                errors.append(f"{query_kind}:{symbol}:{error}")
                continue
            if completed.returncode:
                errors.append(f"{query_kind}:{symbol}:{completed.stderr[-300:]}")
                continue
            query_blocks = 0
            query_limit = 1 if query_kind == "definition" else 3
            for path, target_line in _locations(completed.stdout, root):
                relative = str(path.resolve().relative_to(root))
                key = (relative, target_line, query_kind)
                if key in seen_locations:
                    continue
                seen_locations.add(key)
                content, start, end = _snippet(path, target_line)
                blocks.append(ContextBlock(
                    id=f"LSP-{len(blocks) + 1:02d}", kind="lsp", content=content,
                    file=relative, start_line=start, end_line=end, query=symbol,
                    relation=f"{query_kind}_of_changed_identifier", confidence=1.0,
                    metadata={"provider": "gopls", "position": target, "target_line": target_line},
                ))
                query_blocks += 1
                if len(blocks) >= max_blocks:
                    return blocks, {"available": True, "provider": "gopls", "symbols": len(positions), "blocks": len(blocks), "errors": errors}
                if query_blocks >= query_limit:
                    break
    return blocks, {"available": bool(blocks), "provider": "gopls", "symbols": len(positions), "blocks": len(blocks), "errors": errors}
