from __future__ import annotations

import json
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .io import load_tasks, read_jsonl
from .judge import _line_candidate, _same_path


def _judge_row(row: dict, task, model: str, schema: Path, timeout: int) -> dict:
    issues = (row.get("response") or {}).get("issues", []) if row.get("status") == "ok" else []
    references = [
        {
            "gold_id": gold.id, "file": gold.file, "start_line": gold.start_line,
            "end_line": gold.end_line, "comment": gold.claim,
        }
        for gold in task.gold_issues
    ]
    generated = [
        {
            "prediction_index": index, "file": issue.get("file"), "line": issue.get("line"),
            "claim": issue.get("claim"), "failure_scenario": issue.get("failure_scenario"),
            "suggestion": issue.get("suggestion"),
        }
        for index, issue in enumerate(issues)
    ]
    if not generated or not references:
        return {"matches": [], "duplicates": [], "status": "skipped", "duration_seconds": 0.0}
    prompt = (
        "You are the semantic matching judge for a code-review benchmark. Match generated comments "
        "to references when they express the same underlying concrete defect or suggestion. Ignore "
        "wording, tone, formatting, proposed-fix differences, platform-specific examples, line-location "
        "differences within the same file, and valid extra downstream details. Require the same core "
        "technical mechanism; mentioning the same API is insufficient.\n\n"
        "Return a one-to-one maximum set in `matches`: each prediction_index and gold_id at most once. "
        "If an additional generated comment repeats a defect already represented in `matches`, put it "
        "in `duplicates` with that gold_id. Never match across different files. Use only supplied IDs.\n\n"
        f"REFERENCES:\n{json.dumps(references, ensure_ascii=False, indent=2)}\n\n"
        f"GENERATED:\n{json.dumps(generated, ensure_ascii=False, indent=2)}\n"
    )
    with tempfile.TemporaryDirectory(prefix="ace-review-batch-judge-") as temporary:
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
            return {"matches": [], "duplicates": [], "status": "error", "error": result.stderr[-1000:], "duration_seconds": time.monotonic() - started}
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as error:
            return {"matches": [], "duplicates": [], "status": "error", "error": str(error), "duration_seconds": time.monotonic() - started}


def judge_predictions_batch(
    manifest_path: str | Path,
    prediction_paths: list[str] | tuple[str, ...],
    output_path: str | Path,
    model: str = "gpt-5.4",
    line_tolerance: int = 1,
    jobs: int = 8,
    timeout_seconds: int = 600,
    schema_path: str | Path = "ace_review/schemas/batch_semantic_match.schema.json",
) -> dict:
    tasks = {task.id: task for task in load_tasks(manifest_path)}
    predictions = [row for path in prediction_paths for row in read_jsonl(path)]
    schema = Path(schema_path)

    def run(row):
        task = tasks[row["task_id"]]
        return _judge_row(row, task, model, schema, timeout_seconds)

    with ThreadPoolExecutor(max_workers=max(1, jobs)) as executor:
        decisions = list(executor.map(run, predictions))

    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    calls = errors = 0
    with target.open("w", encoding="utf-8") as handle:
        for row, decision in zip(predictions, decisions):
            task = tasks[row["task_id"]]
            issues = (row.get("response") or {}).get("issues", []) if row.get("status") == "ok" else []
            gold = {item.id: item for item in task.gold_issues}
            if decision.get("status") != "skipped":
                calls += 1
            errors += decision.get("status") == "error"
            matched_gold = set()
            matched_predictions = set()
            semantic_gold = set()
            semantic_predictions = set()
            relocated = set()
            valid_matches = []
            for match in decision.get("matches", []):
                index = match.get("prediction_index")
                gold_id = match.get("gold_id")
                if not isinstance(index, int) or not (0 <= index < len(issues)) or gold_id not in gold:
                    continue
                if index in semantic_predictions or gold_id in semantic_gold or not _same_path(gold[gold_id], issues[index]):
                    continue
                semantic_predictions.add(index); semantic_gold.add(gold_id)
                line_hit = _line_candidate(gold[gold_id], issues[index], line_tolerance)
                if line_hit:
                    matched_predictions.add(index); matched_gold.add(gold_id)
                else:
                    relocated.add(index)
                valid_matches.append(dict(match) | {"line_candidate": line_hit})
            duplicate_predictions = set()
            valid_duplicates = []
            for duplicate in decision.get("duplicates", []):
                index = duplicate.get("prediction_index")
                gold_id = duplicate.get("gold_id")
                if not isinstance(index, int) or not (0 <= index < len(issues)) or gold_id not in semantic_gold:
                    continue
                if index in semantic_predictions or index in duplicate_predictions or not _same_path(gold[gold_id], issues[index]):
                    continue
                duplicate_predictions.add(index)
                valid_duplicates.append(duplicate)
            line_pairs = sum(_line_candidate(item, issue, line_tolerance) for issue in issues for item in task.gold_issues)
            label = {
                "task_id": row["task_id"], "condition": row["condition"], "repeat": row.get("repeat", 0),
                "status": row.get("status"), "judge_status": decision.get("status"),
                "matched_gold_ids": sorted(matched_gold), "fp_count": len(issues) - len(matched_predictions),
                "generated_count": len(issues), "line_candidate_count": line_pairs,
                "semantic_anywhere_gold_ids": sorted(semantic_gold),
                "semantic_anywhere_fp_count": len(issues) - len(semantic_predictions),
                "relocated_match_count": len(relocated),
                "duplicate_semantic_count": len(duplicate_predictions),
                "hallucination_count": len(issues) - len(semantic_predictions) - len(duplicate_predictions),
                "semantic_matches": valid_matches, "semantic_duplicates": valid_duplicates,
                "tokens": row.get("input_tokens_estimate", 0), "context_tokens": row.get("context_tokens", 0),
                "latency_seconds": row.get("duration_seconds", 0), "judge_duration_seconds": decision.get("duration_seconds", 0),
            }
            handle.write(json.dumps(label, ensure_ascii=False) + "\n")
    return {"predictions": len(predictions), "judge_calls": calls, "judge_errors": errors, "output": str(target), "judge_model": model}
