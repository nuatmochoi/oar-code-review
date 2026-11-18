from __future__ import annotations

import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path

from .schema import ReviewTask


def run(command: list[str], cwd: str | Path | None = None, timeout: int = 600) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, text=True, capture_output=True, timeout=timeout, check=False)


class RepositoryCache:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, repo: str) -> Path:
        return self.root / repo.replace("/", "__")

    def ensure(self, task: ReviewTask) -> tuple[Path | None, dict]:
        path = self.path_for(task.repo)
        if not path.exists():
            result = run(["git", "clone", "--filter=blob:none", f"https://github.com/{task.repo}.git", str(path)], timeout=1800)
            if result.returncode:
                return None, {"available": False, "stage": "clone", "stderr": result.stderr[-2000:]}
        for commit in (task.base_commit, task.head_commit):
            if not commit:
                continue
            exists = run(["git", "cat-file", "-e", f"{commit}^{{commit}}"], cwd=path)
            if exists.returncode:
                fetched = run(["git", "fetch", "origin", commit, "--depth", "1"], cwd=path, timeout=1200)
                if fetched.returncode:
                    return None, {"available": False, "stage": "fetch", "commit": commit, "stderr": fetched.stderr[-2000:]}
        return path, {"available": True, "path": str(path)}


def hydrate_diff(task: ReviewTask, repo_path: str | Path) -> ReviewTask:
    if task.diff:
        return task
    merge_base = run(["git", "merge-base", task.base_commit, task.head_commit], cwd=repo_path, timeout=120)
    if merge_base.returncode:
        raise RuntimeError(merge_base.stderr.strip())
    effective_base = merge_base.stdout.strip()
    review_range = f"{task.base_commit}...{task.head_commit}"
    result = run(["git", "diff", "--no-ext-diff", "--unified=3", review_range], cwd=repo_path, timeout=300)
    if result.returncode:
        raise RuntimeError(result.stderr.strip())
    data = task.to_dict()
    data["diff"] = result.stdout
    files = run(["git", "diff", "--name-only", review_range], cwd=repo_path)
    data["changed_files"] = [line for line in files.stdout.splitlines() if line]
    data["metadata"] = data.get("metadata", {}) | {
        "hydrated": True, "merge_base_commit": effective_base, "review_range": review_range,
    }
    return ReviewTask.from_dict(data)


@contextmanager
def revision_worktree(repo_path: str | Path, commit: str):
    """Materialize an isolated immutable snapshot and clean it up reliably."""
    root = Path(repo_path)
    with tempfile.TemporaryDirectory(prefix="ace-review-snapshot-") as temporary:
        # Git derives .git/worktrees/<name> from the basename. A constant
        # "repo" races when several PR snapshots from one repository are added
        # and removed concurrently, despite distinct parent directories.
        worktree = Path(temporary) / Path(temporary).name
        added = run(["git", "worktree", "add", "--detach", str(worktree), commit], cwd=root, timeout=300)
        if added.returncode:
            raise RuntimeError(added.stderr.strip())
        try:
            yield worktree
        finally:
            run(["git", "worktree", "remove", "--force", str(worktree)], cwd=root, timeout=300)
