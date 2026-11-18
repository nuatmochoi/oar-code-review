"""Frozen pilot-v1 source reused for a multi-issue six-arm ablation."""
import argparse
import json
from pathlib import Path

from .cors_review_packets import fit_lines, sha
from .obligation import token_count

SEPARATOR = "\n\nADDITIONAL SOURCE\n"


def build(source, output):
    original = Path(source).read_text()
    data = json.loads(original)
    rows = {r["arm"]: r for r in data["records"]}
    for row in rows.values():
        if sha(row["prompt"]) != row["prompt_sha256"]:
            raise ValueError("Frozen source hash mismatch")
    common = rows["diff_only"]["prompt"].split(SEPARATOR)[0]
    old = ("Review this full pull-request diff for an introduced functional defect. "
           "Report the single most important supported defect, or no_supported_defect or "
           "insufficient_evidence as appropriate.")
    if old not in common:
        raise ValueError("Unexpected old prompt")
    common = common.replace(old,
        "Review this full pull-request diff for introduced functional defects. "
        "Report up to five distinct supported defects in issues, ordered by importance. "
        "Do not stop after the first defect. Use an empty issues array if none is supported. "
        "Explain uncertainty or missing evidence in limitations.", 1)
    contexts = {arm: row["prompt"].split(SEPARATOR)[1] for arm,row in rows.items()}
    cors = contexts["cors_source"]
    combined = contexts["cors_route_source"]
    prefix = cors + "\n\n"
    if not combined.startswith(prefix):
        raise ValueError("Unexpected source composition")
    contexts["route_source"] = combined[len(prefix):]
    placebo = fit_lines(contexts["placebo_source"], token_count(combined)-token_count(prefix))
    while token_count(prefix + placebo) > token_count(combined):
        placebo = "\n".join(placebo.splitlines()[:-1])
    contexts["cors_placebo_source"] = prefix + placebo
    records = []
    for arm,context in contexts.items():
        prompt = common + SEPARATOR + context
        records.append({"task_id":rows["diff_only"]["task_id"],"arm":arm,
                        "prompt":prompt,"prompt_sha256":sha(prompt),
                        "diff_sha256":rows["diff_only"]["diff_sha256"],"source_tokens":token_count(context)})
    result = {"scope":"One previously observed PR, author-selected source; multi-issue up to five; 18 calls",
              "parent_packet_sha256":sha(original),"records":records,
              "source_provenance":data["provenance"],
              "cors_placebo_gap":token_count(combined)-token_count(contexts["cors_placebo_source"])}
    with Path(output).open("x") as stream:
        json.dump(result,stream,ensure_ascii=False,indent=2)
        stream.write("\n")
    print(json.dumps({"tokens":{r['arm']:r['source_tokens'] for r in records},"gap":result['cors_placebo_gap']}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source")
    parser.add_argument("output")
    args = parser.parse_args()
    build(args.source,args.output)
