from __future__ import annotations

import json
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .io import read_jsonl
from .schema import ReviewTask


def _path(value: str) -> str:
    normalized = (value or "").replace("\\", "/").lstrip("./")
    return normalized[2:] if normalized.startswith(("a/", "b/")) else normalized


def _line_candidate(gold, issue: dict, tolerance: int) -> bool:
    if gold.file and issue.get("file") and _path(gold.file) != _path(str(issue["file"])):
        return False
    if gold.start_line is None and gold.end_line is None:
        return True
    line = issue.get("line")
    if not isinstance(line, int):
        return False
    start = gold.start_line if gold.start_line is not None else gold.end_line
    end = gold.end_line if gold.end_line is not None else gold.start_line
    assert start is not None and end is not None
    low, high = sorted((start, end))
    return low - tolerance <= line <= high + tolerance


def _same_path(gold, issue: dict) -> bool:
    return not (gold.file and issue.get("file") and _path(gold.file) != _path(str(issue["file"])))


def _semantic_match(reference: str, generated: str, model: str, schema: Path, timeout: int) -> dict:
    prompt = (
        "Decide whether two code-review comments express the same underlying concern or suggestion. "
        "Ignore wording, tone, formatting, proposed-fix differences, platform-specific examples, "
        "and extra downstream details. Match when the core intent and technical defect mechanism "
        "are equivalent; an added valid consequence does not make it a different defect. A comment "
        "that merely mentions the same line or API without the same concern is not enough.\n\n"
        f"REFERENCE:\n{reference.strip()}\n\nGENERATED:\n{generated.strip()}\n"
    )
    with tempfile.TemporaryDirectory(prefix="ace-review-judge-") as temporary:
        output = Path(temporary) / "match.json"
        command = [
            "codex", "exec", "--skip-git-repo-check", "--ephemeral", "--ignore-user-config",
            "--ignore-rules", "--strict-config", "-s", "read-only", "--model", model,
            "--output-schema", str(schema.resolve()), "--output-last-message", str(output),
            "-C", temporary, "-",
        ]
        for feature in ("shell_tool", "unified_exec", "browser_use", "browser_use_external", "computer_use", "apps", "in_app_browser"):
            command[command.index("-"):command.index("-")] = ["--disable", feature]
        started = time.monotonic()
        try:
            result = subprocess.run(command, input=prompt, text=True, capture_output=True, timeout=timeout, check=False)
            payload = json.loads(output.read_text(encoding="utf-8")) if output.exists() else None
            if result.returncode == 0 and isinstance(payload, dict):
                return payload | {"status": "ok", "duration_seconds": time.monotonic() - started}
            return {"match": False, "reason": result.stderr[-1000:], "status": "error", "duration_seconds": time.monotonic() - started}
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as error:
            return {"match": False, "reason": str(error), "status": "error", "duration_seconds": time.monotonic() - started}


def judge_predictions(
    manifest_path: str | Path,
    predictions_path: str | Path | list[str] | tuple[str, ...],
    output_path: str | Path,
    model: str = "gpt-5.6-sol",
    line_tolerance: int = 1,
    jobs: int = 4,
    timeout_seconds: int = 600,
    schema_path: str | Path = "ace_review/schemas/semantic_match.schema.json",
    semantic_anywhere: bool = False,
) -> dict:
    task_rows = [ReviewTask.from_dict(item) for item in read_jsonl(manifest_path)]
    tasks = {task.id: task for task in task_rows}
    prediction_paths = predictions_path if isinstance(predictions_path, (list, tuple)) else [predictions_path]
    predictions = [row for path in prediction_paths for row in read_jsonl(path)]
    candidates: list[tuple[int, int, str, str, bool]] = []
    issues_by_row: list[list[dict]] = []
    for row_index, row in enumerate(predictions):
        response = row.get("response") if row.get("status") == "ok" else None
        issues = response.get("issues", []) if isinstance(response, dict) else []
        issues_by_row.append(issues)
        task = tasks.get(row.get("task_id"))
        if not task:
            continue
        for issue_index, issue in enumerate(issues):
            generated = "\n".join(str(issue.get(key, "")) for key in ("claim", "failure_scenario", "suggestion"))
            for gold in task.gold_issues:
                line_hit = _line_candidate(gold, issue, line_tolerance)
                if line_hit or (semantic_anywhere and _same_path(gold, issue)):
                    candidates.append((row_index, issue_index, gold.id, generated, line_hit))

    schema = Path(schema_path)
    def run_candidate(candidate):
        row_index, issue_index, gold_id, generated, _line_hit = candidate
        task = tasks[predictions[row_index]["task_id"]]
        gold = next(item for item in task.gold_issues if item.id == gold_id)
        return candidate, _semantic_match(gold.claim, generated, model, schema, timeout_seconds)

    unique_candidates = {}
    for candidate in candidates:
        key = (candidate[2], candidate[3])
        unique_candidates.setdefault(key, candidate)
    unique_decisions = {}
    with ThreadPoolExecutor(max_workers=max(1, jobs)) as executor:
        for candidate, decision in executor.map(run_candidate, unique_candidates.values()):
            unique_decisions[(candidate[2], candidate[3])] = decision
    decisions = [(candidate, unique_decisions[(candidate[2], candidate[3])]) for candidate in candidates]

    by_row: dict[int, list[tuple[tuple, dict]]] = {}
    for candidate, decision in decisions:
        by_row.setdefault(candidate[0], []).append((candidate, decision))

    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    semantic_calls = len(unique_decisions)
    judge_errors = sum(decision.get("status") != "ok" for decision in unique_decisions.values())
    with target.open("w", encoding="utf-8") as handle:
        for row_index, row in enumerate(predictions):
            matched_gold: set[str] = set()
            matched_issues: set[int] = set()
            semantic_gold: set[str] = set()
            semantic_issues: set[int] = set()
            relocated_issues: set[int] = set()
            duplicate_issues: set[int] = set()
            details = []
            for candidate, decision in by_row.get(row_index, []):
                _, issue_index, gold_id, _, line_hit = candidate
                accepted = bool(decision.get("match")) and line_hit and gold_id not in matched_gold and issue_index not in matched_issues
                if accepted:
                    matched_gold.add(gold_id); matched_issues.add(issue_index)
                details.append({
                    "prediction_index": issue_index, "gold_id": gold_id, "accepted": accepted,
                    "line_candidate": line_hit,
                    "decision": decision,
                })
            for candidate, decision in sorted(by_row.get(row_index, []), key=lambda item: not item[0][4]):
                _, issue_index, gold_id, _, line_hit = candidate
                if not decision.get("match"):
                    continue
                if gold_id not in semantic_gold and issue_index not in semantic_issues:
                    semantic_gold.add(gold_id); semantic_issues.add(issue_index)
                    if not line_hit:
                        relocated_issues.add(issue_index)
                elif gold_id in semantic_gold and issue_index not in semantic_issues:
                    duplicate_issues.add(issue_index)
            issues = issues_by_row[row_index]
            label = {
                "task_id": row.get("task_id"), "condition": row.get("condition"),
                "repeat": row.get("repeat", 0),
                "status": row.get("status"), "matched_gold_ids": sorted(matched_gold),
                "fp_count": len(issues) - len(matched_issues), "generated_count": len(issues),
                "candidate_pair_count": len(by_row.get(row_index, [])),
                "line_candidate_count": sum(candidate[4] for candidate, _ in by_row.get(row_index, [])),
                "semantic_matches": details,
                "semantic_anywhere_gold_ids": sorted(semantic_gold),
                "semantic_anywhere_fp_count": len(issues) - len(semantic_issues),
                "relocated_match_count": len(relocated_issues),
                "duplicate_semantic_count": len(duplicate_issues),
                "hallucination_count": len(issues) - len(semantic_issues) - len(duplicate_issues),
                "tokens": row.get("input_tokens_estimate", 0),
                "context_tokens": row.get("context_tokens", 0),
                "latency_seconds": row.get("duration_seconds", 0),
            }
            handle.write(json.dumps(label, ensure_ascii=False) + "\n")
    return {
        "predictions": len(predictions), "semantic_pairs": len(candidates), "semantic_calls": semantic_calls,
        "judge_errors": judge_errors, "output": str(target), "judge_model": model,
        "line_tolerance": line_tolerance,
        "semantic_anywhere": semantic_anywhere,
    }
