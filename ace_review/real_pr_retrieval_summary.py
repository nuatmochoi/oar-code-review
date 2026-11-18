"""Summarize frozen retrieval packets without interpreting overlap as recall."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import mean

from .real_pr_retrieval import numbered_code, visible_head_lines


def source_lines(selection, chunks):
    return {(chunks[key]["file"], line)
            for key in selection["ids"]
            for line, _ in numbered_code(chunks[key]["text"])}


def summarize(audit, tasks):
    rows = []
    for record in audit["records"]:
        if record["status"] != "ok":
            continue
        visible = visible_head_lines(tasks[record["task_id"]]["diff"])
        diff_lines = {(file, line) for file, lines in visible.items() for line in lines}
        for selection in record["selections"]:
            left = source_lines(selection["bm25"], record["chunks"])
            right = source_lines(selection["semantic"], record["chunks"])
            rows.append({
                "task_id": record["task_id"], "budget": selection["budget"],
                "structurally_eligible": record["structurally_eligible"],
                "chunk_jaccard": selection["jaccard"],
                "line_jaccard": len(left & right) / len(left | right) if left | right else None,
                **{arm: {"unique_lines": len(lines),
                         "outside_diff_lines": len(lines - diff_lines),
                         "diff_visible_fraction": len(lines & diff_lines) / len(lines) if lines else None,
                         "source_tokens": selection[arm]["source_tokens"]}
                   for arm, lines in (("bm25", left), ("semantic", right))},
            })
    aggregates = []
    for budget in (512, 1024, 2048, 4096):
        subset = [r for r in rows if r["structurally_eligible"] and r["budget"] == budget]
        aggregates.append({"budget": budget, "n": len(subset),
                           **{metric: mean(r[metric] for r in subset if r[metric] is not None)
                              for metric in ("chunk_jaccard", "line_jaccard")},
                           **{arm: {metric: mean(r[arm][metric] for r in subset
                                               if r[arm][metric] is not None)
                                    for metric in ("source_tokens", "diff_visible_fraction", "outside_diff_lines")}
                              for arm in ("bm25", "semantic")}})
    return {"interpretation": "Descriptive overlap, not evidence relevance or recall; development cohort only",
            "rows": rows, "eligible_aggregates": aggregates}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audit")
    parser.add_argument("manifest")
    parser.add_argument("output")
    args = parser.parse_args()
    tasks = {task["id"]: task for task in
             (json.loads(line) for line in Path(args.manifest).read_text().splitlines())}
    result = summarize(json.loads(Path(args.audit).read_text()), tasks)
    with Path(args.output).open("x") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
