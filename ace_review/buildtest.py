from __future__ import annotations

import subprocess
import tempfile
import time
from pathlib import Path

from .repository import run
from .schema import ContextBlock, ReviewTask


_RUN_CACHE: dict[tuple[str, str, str, tuple[str, ...], int], dict] = {}


def detect_test_command(root: Path, language: str, changed_files: tuple[str, ...] = ()) -> list[str] | None:
    lowered = language.lower()
    if lowered == "go" and (root / "go.mod").exists():
        packages = sorted({
            "." if str(Path(path).parent) == "." else f"./{Path(path).parent.as_posix()}"
            for path in changed_files if path.endswith(".go") and "vendor/" not in path
        })
        return ["go", "test", *(packages or ["./..."])]
    if lowered in {"python", "py"} and any((root / name).exists() for name in ("pyproject.toml", "setup.py", "pytest.ini")):
        return ["python3", "-m", "pytest", "-q"]
    if lowered in {"javascript", "typescript", "js", "ts"} and (root / "package.json").exists():
        return ["npm", "test", "--", "--runInBand"]
    if lowered == "java" and (root / "mvnw").exists():
        return ["./mvnw", "test", "-q"]
    if lowered == "java" and (root / "gradlew").exists():
        return ["./gradlew", "test", "--console=plain"]
    return None


def _run_revision(repo_path: Path, commit: str, language: str, changed_files: tuple[str, ...], timeout: int) -> dict:
    package_scope = tuple(sorted({str(Path(path).parent) for path in changed_files}))
    cache_key = (str(repo_path.resolve()), commit, language.lower(), package_scope, timeout)
    if cache_key in _RUN_CACHE:
        return dict(_RUN_CACHE[cache_key]) | {"cached": True}
    with tempfile.TemporaryDirectory(prefix="ace-review-") as temporary:
        worktree = Path(temporary) / "repo"
        added = run(["git", "worktree", "add", "--detach", str(worktree), commit], cwd=repo_path, timeout=300)
        if added.returncode:
            return {"status": "infra_error", "stderr": added.stderr[-3000:]}
        started = time.monotonic()
        try:
            command = detect_test_command(worktree, language, changed_files)
            if not command:
                return {"status": "unsupported", "duration_seconds": 0.0}
            try:
                result = subprocess.run(command, cwd=worktree, text=True, capture_output=True, timeout=timeout, check=False)
            except subprocess.TimeoutExpired as error:
                return {"status": "timeout", "duration_seconds": time.monotonic() - started, "output": (error.stdout or "")[-3000:]}
            outcome = {
                "status": "pass" if result.returncode == 0 else "fail",
                "returncode": result.returncode,
                "duration_seconds": time.monotonic() - started,
                "output": (result.stdout + "\n" + result.stderr)[-5000:],
                "command": command,
            }
            _RUN_CACHE[cache_key] = outcome
            return dict(outcome)
        finally:
            run(["git", "worktree", "remove", "--force", str(worktree)], cwd=repo_path, timeout=300)


def collect_buildtest(task: ReviewTask, repo_path: str | Path, timeout: int = 600) -> tuple[list[ContextBlock], dict]:
    root = Path(repo_path)
    effective_base = task.metadata.get("merge_base_commit", task.base_commit)
    base = _run_revision(root, effective_base, task.language, task.changed_files, timeout)
    head = _run_revision(root, task.head_commit, task.language, task.changed_files, timeout)
    available = base["status"] not in {"infra_error", "unsupported"} and head["status"] not in {"infra_error", "unsupported"}
    states = (base["status"], head["status"])
    classification = {
        ("pass", "fail"): "regression",
        ("pass", "pass"): "no_observed_failure",
        ("fail", "fail"): "shared_failure",
        ("fail", "pass"): "improvement",
    }.get(states, "inconclusive")
    include_patch_output = classification in {"regression", "inconclusive"}
    summary = (
        f"Base revision: {base['status'].upper()} ({base.get('duration_seconds', 0):.2f}s)\n"
        f"Patch revision: {head['status'].upper()} ({head.get('duration_seconds', 0):.2f}s)\n\n"
        f"Classification: {classification}\n"
        + (f"\nPatch output (tail):\n{head.get('output', '')}" if include_patch_output else "")
    )
    blocks = [ContextBlock(
        id="BUILDTEST-01", kind="buildtest", content=summary,
        relation="base_patch_execution_delta", confidence=1.0 if available else 0.0,
        metadata={"base": base, "patch": head, "classification": classification},
    )] if available else []
    return blocks, {"available": available, "base": base["status"], "patch": head["status"], "classification": classification}
