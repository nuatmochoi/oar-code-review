"""Summarize a pilot using explicit author judgments, not lexical auto-grading."""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path

from .packet_review import write_new


def analyze(root: Path, judgments_path: Path) -> dict:
    judgments = json.loads(judgments_path.read_text())
    manifest = json.loads((root / "manifest.json").read_text())
    completion = json.loads((root / "completion.json").read_text())
    if not completion["complete"]:
        raise ValueError("incomplete pilot must not be scored as completed")
    expected = {row["run_id"] for row in manifest["rows"]}
    if set(judgments["judgments"]) != expected:
        raise ValueError("author judgments must cover exactly all planned responses")
    groups = defaultdict(list)
    records = []
    usage = []
    for key in sorted(expected):
        data = (root / f"{key}.json").read_bytes()
        row = json.loads(data)
        if row["status"] != "ok":
            raise ValueError("failed response cannot be scored")
        judgment = judgments["judgments"][key]
        if judgment not in {"grounded_target", "correct_negative", "unsupported_generic_alarm",
                            "insufficient_evidence"}:
            raise ValueError("unknown author judgment")
        alarm = row["response"]["verdict"] == "introduced_defect"
        if (judgment in {"correct_negative", "insufficient_evidence"}) == alarm:
            raise ValueError("author judgment conflicts with emitted verdict")
        record = {"run_id": key, "variant": row["variant"], "arm": row["arm"],
                  "repeat": row["repeat"], "verdict_alarm": alarm,
                  "grounded_target": judgment == "grounded_target", "judgment": judgment,
                  "actionable_location": row["response"]["file"] == "target.py"
                  and row["response"]["line"] == 4,
                  "response_record_sha256": hashlib.sha256(data).hexdigest()}
        records.append(record)
        groups[row["arm"]].append(record)
        trace = json.loads((root / f"{key}.trace.json").read_text())
        for line in trace["stdout"].splitlines():
            event = json.loads(line)
            if event.get("type") == "turn.completed":
                usage.append(event.get("usage", {}))
    arms = {}
    for arm, rows in groups.items():
        unsafe = [r for r in rows if r["variant"] == "unsafe"]
        safe = [r for r in rows if r["variant"] == "safe"]
        arms[arm] = {
            "unsafe_verdict_hits": sum(r["verdict_alarm"] for r in unsafe),
            "unsafe_grounded_mechanism_hits": sum(r["grounded_target"] for r in unsafe),
            "safe_alarms": sum(r["verdict_alarm"] for r in safe),
            "safe_correct_negatives": sum(r["judgment"] == "correct_negative" for r in safe),
            "safe_abstentions": sum(r["judgment"] == "insufficient_evidence" for r in safe),
            "unsafe_abstentions": sum(r["judgment"] == "insufficient_evidence" for r in unsafe),
            "repeats": 3,
            "paired_grounded_success": sum(
                u["grounded_target"] and not s["verdict_alarm"]
                for u in unsafe for s in safe if u["repeat"] == s["repeat"]),
        }
    report = {"scope": "one reused synthetic family; author adjudication, not blind evaluation",
              "arms": arms, "records": records,
              "usage_totals": {key: sum(u.get(key, 0) for u in usage) for key in (
                  "input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens")},
              "api_input_tokens_range": [min(u["input_tokens"] for u in usage),
                                         max(u["input_tokens"] for u in usage)]}
    write_new(root / "analysis.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("judgments", type=Path)
    args = parser.parse_args()
    print(json.dumps(analyze(args.root, args.judgments)["arms"], indent=2))
