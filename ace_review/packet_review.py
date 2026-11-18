"""Run the frozen packet pilot, retaining raw traces and explicit failures."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import random
import subprocess
import tempfile
import time

import jsonschema

from .reviewer import DISABLED_AGENT_FEATURES, _trace_audit


SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["verdict", "explanation", "file", "line"],
    "properties": {
        "verdict": {"type": "string", "enum": [
            "introduced_defect", "no_supported_defect", "insufficient_evidence"]},
        "explanation": {"type": "string"},
        "file": {"type": "string"},
        "line": {"type": "integer"},
    },
}


def write_new(path: Path, value) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


MULTI_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["issues", "limitations"],
    "properties": {
        "issues": {"type": "array", "maxItems": 5, "items": {
            "type": "object", "additionalProperties": False,
            "required": ["explanation", "file", "line"],
            "properties": {key: SCHEMA["properties"][key] for key in ("explanation", "file", "line")}}},
        "limitations": {"type": "string"},
    },
}


def call_packet(row: dict, root: Path, model: str, schema=None) -> dict:
    schema = SCHEMA if schema is None else schema
    key = row["run_id"]
    started = time.monotonic()
    response = None
    error = None
    stdout = stderr = ""
    returncode = None
    with tempfile.TemporaryDirectory(prefix="ace-packet-model-") as temporary:
        temp = Path(temporary)
        write_new(temp / "schema.json", schema)
        command = ["codex", "exec", "--skip-git-repo-check", "--ephemeral",
                   "--ignore-user-config", "--ignore-rules", "--strict-config",
                   "-s", "read-only", "--json", "--model", model,
                   "-c", 'model_reasoning_effort="low"',
                   "-c", 'web_search="disabled"',
                   "--output-schema", str(temp / "schema.json"),
                   "--output-last-message", str(temp / "response.json"), "-C", temporary]
        for feature in DISABLED_AGENT_FEATURES:
            command.extend(["--disable", feature])
        command.append("-")
        try:
            result = subprocess.run(command, input=row["prompt"], capture_output=True,
                                    text=True, timeout=240, check=False)
            stdout, stderr, returncode = result.stdout, result.stderr, result.returncode
            if (temp / "response.json").exists():
                response = json.loads((temp / "response.json").read_text())
                jsonschema.validate(response, schema)
        except (OSError, ValueError, jsonschema.ValidationError, subprocess.TimeoutExpired) as exc:
            error = str(exc)
            if isinstance(exc, subprocess.TimeoutExpired):
                stdout = exc.stdout or ""
                stderr = exc.stderr or ""
                if isinstance(stdout, bytes):
                    stdout = stdout.decode(errors="replace")
                if isinstance(stderr, bytes):
                    stderr = stderr.decode(errors="replace")
    audit = _trace_audit(stdout)
    record = {k: v for k, v in row.items() if k != "prompt"}
    record.update(response=response, model=model, returncode=returncode,
                  status="ok" if not error and returncode == 0 and response is not None
                  and audit["tool_call_count"] == 0 else "error",
                  tool_audit=audit, error=error, duration_seconds=time.monotonic() - started)
    write_new(root / f"{key}.trace.json", {"stdout": stdout, "stderr": stderr})
    write_new(root / f"{key}.json", record)
    print(json.dumps({"run_id": key, "status": record["status"]}), flush=True)
    return record


def run(packet_path: str, output: str, arms=None, expected_calls=24, multi_issue=False) -> dict:
    schema = MULTI_SCHEMA if multi_issue else SCHEMA
    source = Path(packet_path).read_bytes()
    packet_data = json.loads(source)
    packets = packet_data["records"]
    included = set(arms or ("diff_only", "flow_source", "flow_relations", "shuffled_relations"))
    rows = []
    for repeat in (1, 2, 3):
        for packet in packets:
            if packet["arm"] not in included:
                continue
            if hashlib.sha256(packet["prompt"].encode()).hexdigest() != packet["prompt_sha256"]:
                raise ValueError("packet prompt hash mismatch")
            rows.append(packet | {"repeat": repeat, "run_id": f"review_{len(rows):02}"})
    if len(rows) != expected_calls:
        raise ValueError(f"expected {expected_calls} frozen pilot generations")
    random.Random(20260908).shuffle(rows)
    root = Path(output)
    root.mkdir(exist_ok=False)
    model = "gpt-5.6-sol"
    write_new(root / "manifest.json", {
        "model": model, "reasoning_effort": "low", "calls_cap": expected_calls,
        "web_search": "disabled",
        "packet_sha256": hashlib.sha256(source).hexdigest(),
        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "scope": packet_data["scope"],
        "scoring": "author mechanism adjudication; location separate; no independent samples claim",
        "rows": rows, "schema": schema,
    })
    first = call_packet(rows[0], root, model, schema)
    results = [first]
    if first["status"] == "ok":
        with ThreadPoolExecutor(max_workers=2) as pool:
            results.extend(pool.map(lambda row: call_packet(row, root, model, schema), rows[1:]))
    completion = {"attempted": len(results), "successful": sum(r["status"] == "ok" for r in results),
                  "planned": expected_calls, "complete": len(results) == expected_calls and all(r["status"] == "ok" for r in results)}
    write_new(root / "completion.json", completion)
    return completion


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("packets")
    parser.add_argument("output")
    parser.add_argument("--arms", nargs="+")
    parser.add_argument("--expected-calls", type=int, default=24)
    parser.add_argument("--multi-issue", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(args.packets, args.output, args.arms, args.expected_calls, args.multi_issue)))
