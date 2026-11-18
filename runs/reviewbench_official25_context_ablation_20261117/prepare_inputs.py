#!/usr/bin/env python3
"""Replay the frozen public AST/LSP collection as three nested input arms.

No API, Git, judge, gold, or model-output data are consulted.  Source artifacts
are read-only.  Existing output files must match exactly; divergent files are
never overwritten.  Collection cost and availability stay outside public/.
"""
from __future__ import annotations

import copy
import hashlib
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[1]
OLD = PROJECT / "runs/reviewbench_official25_lsp_20261105"
SAVED = PROJECT / "runs/reviewbench_changeunits_commit_query25_20261105/public"
MANIFEST = OLD / "upstream/corpus/test/test.json"
NATIVE_AUDIT = OLD / "audit/native-coverage.json"
SOURCE_CAP = 6000
BODY_KINDS = {"source_lines", "implementation_body", "exception_branch", "body"}
PR_KEYS = (
    "repo", "pr_number", "pr_url", "base", "head", "title", "body", "nwo",
    "language", "repo_size_kb", "lines_added", "lines_removed", "files_changed",
)
CASE_KEYS = ("id", "language", "title", "body", "diff", "changed_files")
UNIT_KEYS = (
    "id", "path", "base_start", "head_start", "header", "changed_head",
    "changed_base", "sample", "head_end", "base_end", "kind",
)
FORBIDDEN_KEYS = {
    "gold", "private_gold", "ground_truth", "gold_findings", "expected_findings",
    "judge", "judgment", "judgement", "prior_findings", "previous_findings",
    "raw_findings", "official_findings", "unmapped_findings", "findings",
    "commits", "history_errors", "coverage", "native_profiles", "native_calls",
    "native_seconds", "analysis_environment", "original_repo",
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def value_sha(value) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_once(path: Path, value) -> None:
    raw = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()
    if path.exists():
        assert path.read_bytes() == raw, f"Refusing to overwrite divergent artifact: {path}"
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(raw)


def selected(value: dict, keys, where: str, removed: list[str]) -> dict:
    removed.extend(f"{where}.{key}" for key in value if key not in keys)
    return {key: copy.deepcopy(value[key]) for key in keys if key in value}


def forbidden_paths(value, where="", skip_file_map=False) -> list[str]:
    """Check structured metadata, never code/public-text strings or source paths."""
    result = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in FORBIDDEN_KEYS or key.startswith("private_") or key.startswith("gold_"):
                result.append(f"{where}.{key}")
            if key == "files" and skip_file_map:
                continue  # snapshot file names are arbitrary public paths
            result.extend(forbidden_paths(item, f"{where}.{key}", skip_file_map))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            result.extend(forbidden_paths(item, f"{where}[{index}]", skip_file_map))
    return result


def physical_rows(evidence: list[dict]) -> dict:
    rows = {}
    for block in evidence:
        if block.get("kind", "source_lines") not in BODY_KINDS:
            continue
        lines = block["content"].split("\n") if block["content"] else []
        if lines and lines[-1] == "":
            lines.pop()
        for number, text in enumerate(lines, block["start_line"]):
            key = (block["snapshot"], block.get("path", block.get("file")), number)
            text = text.removesuffix("\r")
            assert key not in rows or rows[key] == text, ("Source conflict", key)
            rows[key] = text
    return rows


def numeric_difference(after: dict, before: dict) -> dict:
    return {key: after.get(key, 0) - before.get(key, 0) for key in after}


def sum_costs(rows: list[dict]) -> dict:
    keys = sorted({key for row in rows for key in row})
    return {key: sum(row.get(key, 0) for row in rows) for key in keys}


def main() -> None:
    manifest = load(MANIFEST)
    native_audit = load(NATIVE_AUDIT)
    assert isinstance(manifest, list) and len(manifest) == 25
    native_by_index = {row["index"]: row for row in native_audit["rows"]}
    assert sorted(native_by_index) == list(range(25))
    historical_hashes = {}

    def track(path: Path):
        historical_hashes[str(path)] = sha(path)
        return load(path)

    track(MANIFEST)
    track(NATIVE_AUDIT)
    public_manifest = []
    audits = []
    staged_packets = []
    for index, official_pr in enumerate(manifest):
        original_path = SAVED / f"{index:02d}.json"
        original = track(original_path)
        native = native_by_index[index]
        folder = Path(native["folder"])
        original_case = track(folder / "case.json")
        ast_saved = track(folder / "context/context_AST.json")
        lsp_saved = track(folder / "context/context_LSP.json")
        final_saved = track(folder / "lsp.context.json")
        prefix = original["prefix"]
        assert original["index"] == index
        assert original["pr"] == official_pr, (index, "Manifest changed")
        assert original["case"] == original_case
        assert original["key"] == native["key"]
        assert prefix["evidence"] == final_saved["evidence"] == lsp_saved["evidence"]
        assert prefix["source_tokens"] == final_saved["source_tokens"]
        stages = prefix["stages"]
        ast_ids = stages["AST"]["evidence_ids"]
        lsp_ids = stages["LSP"]["evidence_ids"]
        assert len(ast_ids) == len(set(ast_ids))
        assert len(lsp_ids) == len(set(lsp_ids))
        b2 = copy.deepcopy(prefix["evidence"])
        assert [block["id"] for block in b2] == lsp_ids
        b1 = [copy.deepcopy(block) for block in b2 if block["id"] in set(ast_ids)]
        assert [block["id"] for block in b1] == ast_ids
        assert b1 == ast_saved["evidence"], (index, "AST changed after LSP")
        b2_by_id = {block["id"]: block for block in b2}
        assert all(block == b2_by_id[block["id"]] for block in b1)
        assert not any(block.get("provenance", {}).get("provider") == "typed_static_metadata" for block in b1)
        ast_tokens = stages["AST"]["source_tokens"]
        lsp_tokens = stages["LSP"]["source_tokens"]
        assert ast_tokens == ast_saved["source_tokens"]
        assert lsp_tokens == lsp_saved["source_tokens"] == prefix["source_tokens"]
        assert 0 <= ast_tokens <= lsp_tokens <= SOURCE_CAP
        ast_rows, lsp_rows = physical_rows(b1), physical_rows(b2)
        assert all(lsp_rows[key] == value for key, value in ast_rows.items())
        assert len(ast_rows) == stages["AST"]["source_rows"]
        assert len(lsp_rows) == stages["LSP"]["source_rows"]
        added = [block for block in b2 if block["id"] not in set(ast_ids)]

        removed = []
        pr = selected(original["pr"], PR_KEYS, "pr", removed)
        case = selected(original["case"], CASE_KEYS + ("snapshots",), "case", removed)
        case["snapshots"] = {}
        for snapshot, commit_key in (("HEAD", "head"), ("BASE", "base")):
            original_snapshot = original["case"]["snapshots"][snapshot]
            assert original_snapshot["commit"] == pr[commit_key]
            case["snapshots"][snapshot] = selected(
                original_snapshot, ("commit", "files"), f"case.snapshots.{snapshot}", removed,
            )
        units = [selected(unit, UNIT_KEYS, f"units[{n}]", removed)
                 for n, unit in enumerate(original["units"])]
        removed.extend(key for key in original if key not in ("index", "key", "pr", "case", "units"))
        packet = {"index": index, "key": original["key"], "pr": pr, "case": case,
                  "units": units, "contexts": {"B0": {"evidence": []},
                  "B1": {"evidence": b1}, "B2": {"evidence": b2}}}
        assert not forbidden_paths(packet, skip_file_map=True), forbidden_paths(packet, skip_file_map=True)
        for key in ("title", "body", "diff"):
            assert packet["case"][key] == original_case[key]
        assert packet["units"] == original["units"]
        output = ROOT / "public" / f"{index:02d}.json"
        staged_packets.append((output, packet))
        public_manifest.append({"index": index, "key": packet["key"], "nwo": pr["nwo"],
                                "pr_number": pr["pr_number"], "base": pr["base"], "head": pr["head"],
                                "path": f"public/{index:02d}.json", "packet_value_sha256": value_sha(packet)})

        # Cost before export excludes LSP shutdown; the final context contains it.
        final_cost = prefix["cost"]
        ast_cost = ast_saved["cost"]
        lsp_stage_cost = stages["LSP"]["incremental_cost"]
        total_lsp_cost = numeric_difference(final_cost, ast_cost)
        residual_cost = numeric_difference(final_cost, lsp_saved["cost"])
        unsupported = [copy.deepcopy(item) for item in prefix["unresolved"]
                       if item.get("reason") == "unsupported_actual_changed_file_language"]
        audits.append({
            "index": index, "key": packet["key"], "nwo": pr["nwo"], "language": case["language"],
            "origin": {"public_packet": str(original_path), "native_folder": str(folder)},
            "common_public_hashes": {key: value_sha(case[key]) for key in ("title", "body", "diff", "snapshots")},
            "sanitization_removed_structural_paths": sorted(removed),
            "public_text_and_source_content_not_redacted": True,
            "gold_or_judge_structured_keys_in_retained_input": [],
            "arms": {
                "B0": {"evidence_records": 0, "source_tokens": 0, "physical_source_rows": 0,
                       "evidence_sha256": value_sha([]), "historical_collection_cost": {}},
                "B1": {"evidence_records": len(b1), "source_tokens": ast_tokens,
                       "physical_source_rows": len(ast_rows), "evidence_sha256": value_sha(b1),
                       "historical_collection_cost": ast_cost,
                       "historical_stage_seconds": stages["AST"]["seconds"]},
                "B2": {"evidence_records": len(b2), "source_tokens": lsp_tokens,
                       "physical_source_rows": len(lsp_rows), "evidence_sha256": value_sha(b2),
                       "historical_collection_cost": final_cost,
                       "historical_stage_seconds": stages["AST"]["seconds"] + stages["LSP"]["seconds"]},
            },
            "incremental_LSP": {
                "evidence_records": len(added), "body_records": sum(b.get("kind") in BODY_KINDS for b in added),
                "typed_metadata_records": sum(b.get("provenance", {}).get("provider") == "typed_static_metadata" for b in added),
                "physical_source_rows": len(set(lsp_rows) - set(ast_rows)),
                "source_tokens": lsp_tokens - ast_tokens,
                "stage_seconds": stages["LSP"]["seconds"],
                "stage_cost": lsp_stage_cost,
                "total_cost_including_post_export_lifecycle": total_lsp_cost,
                "post_export_lifecycle_residual_cost": residual_cost,
            },
            "availability": {
                "changed_extensions": native["changed_extensions"],
                "unsupported_file_observations": unsupported,
                "unsupported_is_case_failure": False,
                "AST_unresolved": ast_saved["unresolved"],
                "LSP_unresolved": prefix["unresolved"],
                "unresolved_reason_counts": dict(Counter(item.get("reason", "unspecified") for item in prefix["unresolved"])),
                "historical_RPC_queries": native["observed_RPC_queries"],
                "historical_successful_location_RPC": native["successful_location_RPC"],
                "historical_failed_RPC": native["failed_RPC"],
            },
            "new_native_collection_calls": 0,
            "SCIP_calls": 0,
            "runtime_probe_calls": 0,
            "B1_exact_subset_unchanged_in_B2": True,
            "original_stage_evidence_exactly_matched": True,
        })

    summary = {
        "PR_count": len(audits), "fixed_original_order": True,
        "same_common_full_diff_title_body_snapshots_in_all_arms": True,
        "source_replay_not_new_native_collection": True,
        "source_cap_reference_tokens": SOURCE_CAP,
        "source_token_definition": "Original saved o200k_base unique physical source-line counts; not billed model tokens. Exact stage evidence and row counts independently asserted.",
        "original_AST_collection_cap": 3000,
        "original_LSP_cumulative_collection_cap": 6000,
        "fairness_limit": "Matched 6000 source-token ceiling; realized source size differs. AST collection replay retains its original 3000-token stage cap; this is an input ablation, not a new equal-6000 AST retrieval-policy comparison.",
        "public_packet_usage": "Select one contexts[arm] only. Do not serialize the entire packet, other contexts, snapshots inventory, or audit into planner/reviewer prompts. Common title/body/full diff and units are public inputs.",
        "historical_cost_usage": "B1 historical cost is AST. B2 is cumulative AST+LSP including lifecycle; do not sum B1 with B2 again. New native collection cost is zero because artifacts are replayed. Native cost is not model API dollars.",
        "semantic_limit": "Static metadata is a candidate relation, not runtime binding or semantic truth. Unsupported languages remain included as availability limitations, not run failures.",
        "new_native_collection_calls": 0,
        "arm_totals": {arm: {
            "evidence_records": sum(row["arms"][arm]["evidence_records"] for row in audits),
            "source_tokens": sum(row["arms"][arm]["source_tokens"] for row in audits),
            "physical_source_rows": sum(row["arms"][arm]["physical_source_rows"] for row in audits),
            "historical_collection_cost": sum_costs([row["arms"][arm]["historical_collection_cost"] for row in audits]),
        } for arm in ("B0", "B1", "B2")},
        "LSP_addition_totals": {key: sum(row["incremental_LSP"][key] for row in audits) for key in (
            "evidence_records", "body_records", "typed_metadata_records", "physical_source_rows", "source_tokens", "stage_seconds",
        )},
        "PR_indices_with_new_LSP_physical_rows": [row["index"] for row in audits if row["incremental_LSP"]["physical_source_rows"]],
        "PR_indices_with_metadata_only_LSP_additions": [row["index"] for row in audits if row["incremental_LSP"]["evidence_records"] and not row["incremental_LSP"]["physical_source_rows"]],
        "PR_indices_with_identical_AST_LSP_evidence": [row["index"] for row in audits if not row["incremental_LSP"]["evidence_records"]],
    }
    # All source validation completes before publishing any packet.
    for output, packet in staged_packets:
        write_once(output, packet)
    for entry in public_manifest:
        entry["sha256"] = sha(ROOT / entry["path"])
    write_once(ROOT / "public/manifest.json", public_manifest)
    write_once(ROOT / "audit/source_provenance.json", {"summary": summary, "rows": audits})
    for path, expected in historical_hashes.items():
        assert sha(Path(path)) == expected, f"Historical source changed during preparation: {path}"
    write_once(ROOT / "audit/historical_source_hashes.json", {
        "algorithm": "SHA256", "historical_file_count": len(historical_hashes),
        "sources_unchanged_after_preparation": True, "files": historical_hashes,
    })
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
