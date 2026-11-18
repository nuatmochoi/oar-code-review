from __future__ import annotations

import json
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .io import read_jsonl
from .schema import estimate_tokens


DISABLED_AGENT_FEATURES = (
    "shell_tool", "unified_exec", "browser_use", "browser_use_external",
    "computer_use", "apps", "in_app_browser",
)


def _trace_audit(stdout: str) -> dict:
    events = []
    tool_events = []
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        events.append(event)
        item = event.get("item") if isinstance(event, dict) else None
        item_type = str(item.get("type", "")) if isinstance(item, dict) else ""
        event_type = str(event.get("type", "")) if isinstance(event, dict) else ""
        marker = f"{event_type}:{item_type}".lower()
        if any(word in marker for word in ("command_execution", "tool_call", "web_search", "file_search", "computer_call")):
            tool_events.append(marker)
    return {
        "event_types": [str(event.get("type", "")) for event in events],
        "tool_call_count": len(tool_events),
        "tool_events": tool_events,
    }


def render_prompt(request: dict, instruction_path: str | Path) -> str:
    instruction = Path(instruction_path).read_text(encoding="utf-8").strip()
    contexts = "\n\n".join(
        f"[{block['id']}] kind={block['kind']} file={block.get('file', '')} "
        f"lines={block.get('start_line')}-{block.get('end_line')}\n{block['content']}"
        for block in request.get("contexts", [])
    ) or "(no additional repository context)"
    focus = request.get("focus_diff", "")
    focus_section = (
        "\n\nREVIEW TARGET HUNK:\n"
        + focus
        + "\nOnly report defects whose primary actionable location is an added or modified line "
          "in this target hunk. Treat all other changed hunks as patch context."
        if focus else ""
    )
    return (
        f"{instruction}\n\n"
        f"Repository: {request['repository']}\n"
        f"Language: {request['language']}\n"
        f"PR title: {request['pr'].get('title', '')}\n"
        f"PR description: {request['pr'].get('description', '')}\n\n"
        f"FULL PR DIFF:\n{request['diff']}{focus_section}\n\n"
        f"CONTEXT EVIDENCE:\n{contexts}\n"
    )


def run_codex_requests(
    requests_path: str | Path,
    output_path: str | Path,
    instruction_path: str | Path,
    schema_path: str | Path,
    limit: int | None = None,
    condition: str | None = None,
    model: str | None = None,
    timeout_seconds: int = 600,
    jobs: int = 1,
    repeat_id: int = 0,
    task_ids: set[str] | None = None,
) -> dict:
    rows = list(read_jsonl(requests_path))
    if task_ids:
        rows = [row for row in rows if row["task_id"] in task_ids]
    if condition:
        rows = [row for row in rows if row["condition"] == condition]
    if limit is not None:
        rows = rows[:limit]
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    def review_one(request: dict) -> dict:
        prompt = render_prompt(request, instruction_path)
        with tempfile.TemporaryDirectory(prefix="ace-review-model-") as temporary:
            response_path = Path(temporary) / "response.json"
            command = [
                "codex", "exec", "--skip-git-repo-check", "--ephemeral",
                "--ignore-user-config", "--ignore-rules", "--strict-config",
                "-s", "read-only", "--json",
                "--output-schema", str(Path(schema_path).resolve()),
                "--output-last-message", str(response_path), "-C", temporary,
            ]
            for feature in DISABLED_AGENT_FEATURES:
                command.extend(["--disable", feature])
            if model:
                command.extend(["--model", model])
            command.append("-")
            started = time.monotonic()
            common = {
                "task_id": request["task_id"], "condition": request["condition"],
                "repeat": repeat_id,
                "model": model or "codex-default", "input_tokens_estimate": estimate_tokens(prompt),
                "context_tokens": request.get("context_status", {}).get("context_tokens", 0),
            }
            try:
                result = subprocess.run(command, input=prompt, text=True, capture_output=True, timeout=timeout_seconds, check=False)
                audit = _trace_audit(result.stdout)
                response = json.loads(response_path.read_text(encoding="utf-8")) if response_path.exists() else None
                status = "ok" if result.returncode == 0 and response is not None and audit["tool_call_count"] == 0 else "error"
                return common | {
                    "status": status, "duration_seconds": time.monotonic() - started,
                    "response": response, "returncode": result.returncode,
                    "tool_isolation": {
                        "hard_disabled_features": list(DISABLED_AGENT_FEATURES),
                        **audit,
                    },
                    "stderr_tail": result.stderr[-2000:] if status != "ok" else "",
                }
            except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as error:
                return common | {
                    "status": "error", "duration_seconds": time.monotonic() - started,
                    "response": None, "error": str(error),
                }

    completed = failed = 0
    with target.open("w", encoding="utf-8") as handle:
        with ThreadPoolExecutor(max_workers=max(1, jobs)) as executor:
            for record in executor.map(review_one, rows):
                completed += record["status"] == "ok"
                failed += record["status"] != "ok"
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                handle.flush()
    return {"requested": len(rows), "completed": completed, "failed": failed, "output": str(target)}
