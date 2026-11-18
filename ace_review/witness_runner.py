"""Run a fixed four-cell pair witness on disposable snapshot copies."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from .paired_benchmark import REVISIONS, WORLD_NAMES, _tree_files, load_pair, validate_pair


def _resolve_inside(root: Path, value: str, label: str) -> Path:
    root = root.resolve()
    path = (root / value).resolve()
    try:
        path.relative_to(root)
    except ValueError as error:
        raise ValueError(f"{label} escapes pair root") from error
    return path


def _expand_argv(
    argv: list[str], *, snapshot: Path, pair_root: Path, python_executable: str,
    variant: str, revision: str,
) -> list[str]:
    replacements = {
        "{snapshot}": str(snapshot),
        "{pair_root}": str(pair_root),
        "{python}": python_executable,
        "{variant}": variant,
        "{revision}": revision,
    }
    expanded = []
    for argument in argv:
        value = argument
        for marker, replacement in replacements.items():
            value = value.replace(marker, replacement)
        if "{" in value or "}" in value:
            raise ValueError(f"unsupported witness placeholder in argument: {argument}")
        expanded.append(value)
    return expanded


def _parse_observation(stdout: str, returncode: int, exit_codes: dict[str, int]) -> tuple[str, dict[str, Any] | None]:
    try:
        record = json.loads(stdout)
    except json.JSONDecodeError:
        return "environment_error", None
    if not isinstance(record, dict) or not isinstance(record.get("status"), str):
        return "environment_error", record if isinstance(record, dict) else None
    status = record["status"]
    if status not in exit_codes or returncode != exit_codes[status]:
        return "environment_error", record
    return status, record


def run_pair_witness(
    pair: dict[str, Any],
    pair_root: str | Path,
    *,
    python_executable: str | None = None,
) -> dict[str, Any]:
    """Execute all four cells and compare observed with predeclared outcomes.

    Snapshot trees are copied before execution.  Any mutation in the disposable
    copy invalidates that cell and is recorded; the frozen source stays intact.
    This runner does not claim network or child-process isolation.
    """
    root = Path(pair_root).resolve()
    static_report = validate_pair(pair, root)
    if not static_report["eligible"]:
        return {
            "family_id": pair.get("family_id"),
            "eligible": False,
            "static_eligible": False,
            "cells": {},
            "errors": ["static eligibility gate failed", *static_report["errors"]],
        }

    spec = pair["witness"]
    executable = str(Path(python_executable or sys.executable).resolve())
    cells: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    with tempfile.TemporaryDirectory(prefix="ace-pair-witness-") as temporary:
        stage_root = Path(temporary)
        for variant in WORLD_NAMES:
            world = pair["worlds"][variant]
            for revision in REVISIONS:
                name = f"{variant}_{revision}"
                source = _resolve_inside(root, world[f"{revision}_snapshot"], f"{name} snapshot")
                staged = stage_root / name
                shutil.copytree(source, staged, symlinks=False)
                before = _tree_files(staged)
                command = _expand_argv(
                    spec["argv"], snapshot=staged, pair_root=root,
                    python_executable=executable, variant=variant, revision=revision,
                )
                input_file_sha256 = {}
                for argument in command[1:]:
                    argument_path = Path(argument)
                    if not argument_path.is_file():
                        continue
                    try:
                        relative = argument_path.resolve().relative_to(root).as_posix()
                    except ValueError:
                        continue
                    input_file_sha256[relative] = hashlib.sha256(argument_path.read_bytes()).hexdigest()
                cwd_value = spec.get("cwd", ".")
                cwd = _resolve_inside(staged, cwd_value, f"{name} cwd")
                started = time.monotonic()
                timed_out = False
                try:
                    completed = subprocess.run(
                        command,
                        cwd=cwd,
                        capture_output=True,
                        text=True,
                        timeout=float(spec["timeout_seconds"]),
                        env={
                            "PATH": os.environ.get("PATH", ""),
                            "PYTHONNOUSERSITE": "1",
                            "PYTHONDONTWRITEBYTECODE": "1",
                            "LC_ALL": "C.UTF-8",
                        },
                    )
                    returncode = completed.returncode
                    stdout, stderr = completed.stdout, completed.stderr
                    observed, observation = _parse_observation(stdout, returncode, spec["exit_codes"])
                except subprocess.TimeoutExpired as error:
                    timed_out = True
                    returncode = None
                    stdout = error.stdout.decode() if isinstance(error.stdout, bytes) else (error.stdout or "")
                    stderr = error.stderr.decode() if isinstance(error.stderr, bytes) else (error.stderr or "")
                    observed, observation = "timeout", None
                duration = time.monotonic() - started
                after = _tree_files(staged)
                unchanged = before == after
                expected = world["expected"][revision]
                matched = observed == expected and unchanged and not timed_out
                if not matched:
                    errors.append(f"{name}: expected {expected}, observed {observed}, unchanged={unchanged}")
                cells[name] = {
                    "expected_status": expected,
                    "observed_status": observed,
                    "matched": matched,
                    "returncode": returncode,
                    "timed_out": timed_out,
                    "duration_seconds": round(duration, 6),
                    "stdout": stdout,
                    "stderr": stderr,
                    "observation": observation,
                    "source_tree_unchanged": unchanged,
                    "source_sha256_before": before,
                    "source_sha256_after": after,
                    "command_argv": command,
                    "input_file_sha256": input_file_sha256,
                }
    return {
        "family_id": pair["family_id"],
        "eligible": not errors and all(cell["matched"] for cell in cells.values()),
        "static_eligible": True,
        "cells": cells,
        "errors": errors,
        "runtime": {
            "python": executable,
            "source_execution": "disposable_copy",
            "shell": False,
            "network_isolation": False,
            "child_process_isolation": False,
            "pair_record_sha256": static_report["audit"]["pair_record_sha256"],
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the four-cell witness for one pair")
    parser.add_argument("pair")
    parser.add_argument("--python", dest="python_executable")
    parser.add_argument("--output")
    args = parser.parse_args()
    path = Path(args.pair).resolve()
    report = run_pair_witness(load_pair(path), path.parent, python_executable=args.python_executable)
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("x", encoding="utf-8") as stream:
            stream.write(rendered)
    else:
        print(rendered, end="")
    raise SystemExit(0 if report["eligible"] else 1)


if __name__ == "__main__":
    main()
