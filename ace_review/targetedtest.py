from __future__ import annotations

import re
import subprocess
import tempfile
import time
from collections import Counter
from pathlib import Path

from .repository import run
from .schema import ContextBlock, ReviewTask


def _rank_changed_packages(diff: str, changed_files: tuple[str, ...], limit: int = 3) -> list[str]:
    counts: Counter[str] = Counter()
    current = ""
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            current = line[6:]
        elif current and line.startswith("+") and not line.startswith("+++"):
            counts[str(Path(current).parent)] += 1
    candidates = {
        str(Path(file).parent) for file in changed_files
        if file.endswith(".go") and not file.startswith("vendor/")
    }
    ranked = sorted(candidates, key=lambda directory: (-counts[directory], directory))[:limit]
    return ["." if directory == "." else f"./{Path(directory).as_posix()}" for directory in ranked]


def _changed_test_pattern(diff: str) -> str | None:
    names = sorted(set(re.findall(r"^\+func\s+(Test[A-Za-z0-9_]+)\s*\(", diff, flags=re.MULTILINE)))
    return "^(" + "|".join(map(re.escape, names)) + ")$" if names else None


def _execute(command: list[str], cwd: Path, timeout: int) -> dict:
    started = time.monotonic()
    try:
        result = subprocess.run(command, cwd=cwd, text=True, capture_output=True, timeout=timeout, check=False)
        return {
            "status": "pass" if result.returncode == 0 else "fail",
            "returncode": result.returncode, "duration_seconds": time.monotonic() - started,
            "command": command, "output": (result.stdout + "\n" + result.stderr)[-5000:],
        }
    except subprocess.TimeoutExpired as error:
        output = (error.stdout or "") + "\n" + (error.stderr or "")
        return {"status": "timeout", "duration_seconds": time.monotonic() - started,
                "command": command, "output": output[-5000:]}


def _run_revision(repo: Path, commit: str, packages: list[str], pattern: str | None, timeout: int) -> dict:
    with tempfile.TemporaryDirectory(prefix="ace-targeted-test-") as temporary:
        worktree = Path(temporary) / "repo"
        added = run(["git", "worktree", "add", "--detach", str(worktree), commit], cwd=repo, timeout=300)
        if added.returncode:
            return {"status": "infra_error", "error": added.stderr[-2000:]}
        try:
            if not (worktree / "go.mod").exists():
                return {"status": "unsupported"}
            selector = ["-run", pattern] if pattern else []
            normal = _execute(["go", "test", "-count=1", *selector, *packages], worktree, timeout)
            race = _execute(["go", "test", "-race", "-count=1", *selector, *packages], worktree, timeout)
            return {"status": "available", "normal": normal, "race": race}
        finally:
            run(["git", "worktree", "remove", "--force", str(worktree)], cwd=repo, timeout=300)


def collect_targeted_tests(
    task: ReviewTask,
    repo_path: str | Path,
    timeout: int = 180,
    max_packages: int = 3,
) -> tuple[list[ContextBlock], dict]:
    """Run bounded package tests and the race detector on base and patch revisions."""
    packages = _rank_changed_packages(task.diff, task.changed_files, max_packages)
    if task.language.lower() != "go" or not packages:
        return [], {"available": False, "reason": "unsupported_or_no_go_package"}
    pattern = _changed_test_pattern(task.diff)
    repo = Path(repo_path)
    base_commit = str(task.metadata.get("merge_base_commit") or task.base_commit)
    base = _run_revision(repo, base_commit, packages, pattern, timeout)
    head = _run_revision(repo, task.head_commit, packages, pattern, timeout)
    available = base.get("status") == "available" and head.get("status") == "available"
    if not available:
        return [], {"available": False, "base": base.get("status"), "patch": head.get("status")}

    def delta(mode: str) -> str:
        states = (base[mode]["status"], head[mode]["status"])
        return {("pass", "fail"): "regression", ("pass", "pass"): "no_observed_failure",
                ("fail", "fail"): "shared_failure", ("fail", "pass"): "improvement"}.get(states, "inconclusive")

    normal_delta, race_delta = delta("normal"), delta("race")
    outputs = []
    for mode, classification in (("normal", normal_delta), ("race", race_delta)):
        if classification in {"regression", "inconclusive"}:
            outputs.append(f"\n{mode.upper()} patch output (tail):\n{head[mode].get('output', '')}")
    summary = (
        f"Packages: {' '.join(packages)}\n"
        f"Changed-test selector: {pattern or '(package tests)'}\n"
        f"Normal test: base={base['normal']['status']}, patch={head['normal']['status']}, delta={normal_delta}\n"
        f"Race test: base={base['race']['status']}, patch={head['race']['status']}, delta={race_delta}\n"
        + "".join(outputs)
    )
    block = ContextBlock(
        id="TARGETEDTEST-01", kind="targetedtest", content=summary,
        relation="base_patch_targeted_test_and_race_delta", confidence=1.0,
        metadata={"packages": packages, "test_pattern": pattern, "base": base, "patch": head,
                  "normal_classification": normal_delta, "race_classification": race_delta},
    )
    return [block], {"available": True, "normal": normal_delta, "race": race_delta,
                     "packages": packages, "test_pattern": pattern}
