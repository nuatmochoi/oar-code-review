"""Prepare selector-free claim annotation inputs; never auto-promote them to oracle."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def packet(task):
    # No cached_contexts, selector rankings, model outputs, or previous judgments.
    source = {key: task[key] for key in
              ("id", "repo", "base_commit", "head_commit", "diff", "title", "description")}
    claims = []
    for issue in task["gold_issues"]:
        claims.append({"gold_id": issue["id"], "claim": issue["claim"],
                       "file": issue["file"], "start_line": issue["start_line"],
                       "end_line": issue["end_line"], "is_ai_comment": issue.get("is_ai_comment"),
                       "status": "pending", "defect_verdict": "unresolved",
                       "diff_sufficiency": "unresolved", "rationale": "",
                       "required_evidence_alternatives": [],
                       "independent_reviews": [], "adjudication": None})
    return {"schema_version": 1, "purpose": "annotation_only_not_reviewer_input",
            "task": source, "diff_sha256": digest(source["diff"]), "claims": claims}


def blockers(claim):
    reasons = []
    if claim["status"] != "adjudicated":
        reasons.append("not_adjudicated")
    if claim["defect_verdict"] != "confirmed":
        reasons.append("defect_not_confirmed")
    if claim["diff_sufficiency"] != "insufficient":
        reasons.append("context_necessity_not_confirmed")
    reviews = claim["independent_reviews"]
    ids = {r.get("reviewer_id") for r in reviews if r.get("reviewer_id")}
    if len(ids) < 2 or any(not r.get("selector_blind") or not r.get("record_sha256") for r in reviews):
        reasons.append("independent_review_receipts_missing")
    if not claim["adjudication"] or not claim["rationale"]:
        reasons.append("rationale_or_adjudication_missing")
    alternatives = claim["required_evidence_alternatives"]
    if not alternatives:
        reasons.append("required_evidence_missing")
    # Receipt fields are declarations, not proof of honest independent annotation.
    for alternative in alternatives:
        if not alternative.get("minimality_rationale") or not alternative.get("spans"):
            reasons.append("evidence_minimality_missing")
        for span in alternative.get("spans", []):
            if (span.get("revision") not in ("base", "head")
                    or not span.get("file") or not span.get("content_sha256")
                    or not span.get("causal_role")
                    or not isinstance(span.get("start_line"), int)
                    or not isinstance(span.get("end_line"), int)
                    or span["start_line"] < 1 or span["end_line"] < span["start_line"]):
                reasons.append("invalid_evidence_span")
    return sorted(set(reasons))


def prepare(manifest, output):
    source = Path(manifest).read_text()
    tasks = sorted((json.loads(line) for line in source.splitlines()), key=lambda t: t["id"])
    if len({t['id'] for t in tasks}) != len(tasks):
        raise ValueError("Duplicate task IDs")
    payloads = [packet(task) for task in tasks]
    root = Path(output)
    root.mkdir(parents=True, exist_ok=False)
    records = []
    for index, value in enumerate(payloads):
        filename = f"task_{index:03}.json"
        text = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
        with (root / filename).open("x") as stream:
            stream.write(text)
        records.append({"task_id": value["task"]["id"], "file": filename,
                        "sha256": digest(text), "claims": len(value["claims"])})
    counts = Counter(reason for p in payloads for c in p["claims"] for reason in blockers(c))
    result = {"manifest_sha256": digest(source), "tasks": len(records),
              "claims": sum(r["claims"] for r in records), "records": records,
              "readiness_blocker_counts": dict(counts), "oracle_metrics": None,
              "warning": "Unannotated templates. No independent review or source-span validation yet."}
    with (root / "manifest.json").open("x") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest")
    parser.add_argument("output")
    args = parser.parse_args()
    result = prepare(args.manifest, args.output)
    print(json.dumps({key: result[key] for key in
                      ("tasks", "claims", "readiness_blocker_counts", "oracle_metrics")}))
