from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path

from .schema import ContextBlock, ReviewTask


class ExternalContextAdapter:
    """JSON-over-stdin adapter for LSP/CodeQL/language-specific extractors."""

    def __init__(self, kind: str, command: str, timeout_seconds: int = 300):
        self.kind = kind
        self.command = command
        self.timeout_seconds = timeout_seconds

    def collect(self, task: ReviewTask, repo_path: str | Path) -> tuple[list[ContextBlock], dict]:
        payload = task.to_dict() | {"repo_path": str(Path(repo_path).resolve())}
        try:
            result = subprocess.run(
                shlex.split(self.command), input=json.dumps(payload), text=True,
                capture_output=True, timeout=self.timeout_seconds, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            return [], {"available": False, "kind": self.kind, "error": str(error)}
        if result.returncode:
            return [], {
                "available": False, "kind": self.kind, "returncode": result.returncode,
                "stderr": result.stderr[-4000:],
            }
        try:
            raw = json.loads(result.stdout)
            blocks = [ContextBlock.from_dict(item | {"kind": self.kind}) for item in raw]
        except (json.JSONDecodeError, TypeError, ValueError) as error:
            return [], {"available": False, "kind": self.kind, "error": f"invalid adapter output: {error}"}
        return blocks, {"available": True, "kind": self.kind, "count": len(blocks)}
