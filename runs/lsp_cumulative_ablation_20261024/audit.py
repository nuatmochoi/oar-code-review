"""Audit cumulative ablation inputs without changing the frozen causal rubric.

DIFF has always been an allowed citation. The arm-neutral file gate below
therefore accepts files visibly present in the patch even when B0 has no source
packet. Location mismatches retain the predecessor's warning-only semantics.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import re
import sys
from pathlib import Path

R = Path(__file__).resolve().parent
ROOT = R.parents[1]
REFERENCE = ROOT / "runs/lsp_agentic_preserved_recovery_20261023"
sys.path.insert(0, str(ROOT))
from ace_review.context_adaptive import digest, token_count
from ace_review.protocol_hypothesis import mask_source

HEADER = re.compile(r"^\[(E-(head|base)-[a-f0-9]+)\] (head|base) ([^;]+);")
LINE = re.compile(r"^(\d+): (.*)$")


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def diff_locations(common):
    """Text-only unified-diff coordinates; no AST, repository or native reads."""
    text = common.partition("[DIFF]\n")[2]
    files, lines = set(), {"head": collections.defaultdict(set), "base": collections.defaultdict(set)}
    old_file = new_file = None
    old_line = new_line = None
    for line in text.splitlines():
        if line.startswith("--- ") or line.startswith("+++ "):
            path = line[4:].split("\t", 1)[0]
            path = None if path == "/dev/null" else re.sub(r"^[ab]/", "", path)
            if line.startswith("--- "):
                old_file = path
            else:
                new_file = path
            if path:
                files.add(path)
            old_line = new_line = None
            continue
        match = re.match(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
        if match:
            old_line, new_line = map(int, match.groups())
            continue
        if old_line is None or not line or line.startswith("\\"):
            continue
        if line[0] in " -":
            if old_file:
                lines["base"][old_file].add(old_line)
            old_line += 1
        if line[0] in " +":
            if new_file:
                lines["head"][new_file].add(new_line)
            new_line += 1
    return files, lines


def evidence_view(packet):
    ids = set(re.findall(r"\[(E-[^\]]+)\]", packet["source"] + "\n" + packet["plain"])) | {"DIFF"}
    supplied = collections.defaultdict(dict)
    conflicts, current = [], None
    for line in packet["source"].splitlines():
        match = HEADER.match(line)
        if match:
            current = (match[2], match[4])
            continue
        if line.startswith("[E-"):
            current = None
            continue
        match = LINE.match(line)
        if current and match:
            number = int(match[1])
            if number in supplied[current] and supplied[current][number] != match[2]:
                conflicts.append([current, number])
            supplied[current][number] = match[2]
    lineids = {f"E-{version}-{digest(file)[:10]}": set(lines) for (version, file), lines in supplied.items()}
    return {"ids": ids, "lineids": lineids, "supplied": supplied, "conflicts": conflicts}


def valid_id(value, view):
    if value in view["ids"]:
        return True
    match = re.fullmatch(r"(E-(?:head|base)-[a-f0-9]+):(\d+)(?:-(\d+))?", value)
    return bool(match and match[1] in view["lineids"] and int(match[2]) <= int(match[3] or match[2])
                and set(range(int(match[2]), int(match[3] or match[2]) + 1)) <= view["lineids"][match[1]])


def response_checks(response, packet, common, *, allow_diff_files=True):
    """Same predecessor gates except accepting already-authorized DIFF files."""
    errors, warnings = [], []
    view = evidence_view(packet)
    audit = packet["audit"]
    diff_files, diff_lines = diff_locations(common)
    review = response["review"]
    if (review["status"] == "reported_issues") != bool(review["issues"]):
        errors.append("status_issue")
    source_files = set(audit.get("source_lines", {})) | set(audit.get("base_lines", {}))
    for section, items in [("issue", review["issues"]), ("check", response["checks"])]:
        for index, item in enumerate(items):
            if any(not valid_id(value, view) for value in item["evidence_ids"]):
                errors.append(f"{section}_evidence_{index}")
            if section == "issue":
                file = item["file"]
                if file not in source_files and not (allow_diff_files and file in diff_files):
                    errors.append(f"issue_file_{index}")
                visible = set(audit.get("source_lines", {}).get(file, [])) | set(audit.get("base_lines", {}).get(file, []))
                # Preserve the historical line warning even when the line is in
                # the diff. Text hunk membership is additionally recorded below.
                if item["line"] not in visible:
                    warnings.append(f"issue_line_diff_check_{index}")
            elif any(j < 0 or j >= len(review["issues"]) for j in item["issue_indices"]):
                errors.append(f"issue_index_{index}")
    assessment = response["context_assessment"]
    used = {x["evidence_id"] for x in assessment["used_evidence"]}
    dismissed = {x["evidence_id"] for x in assessment["dismissed_evidence"]}
    missing = {x["evidence_id"] for x in assessment["missing_connections"]}
    normalize = lambda values: {re.sub(r":\d+(?:-\d+)?$", "", value) for value in values}
    sourceids = set(re.findall(r"\[(E-[^\]]+)\]", packet["source"]))
    coverage = {
        "available_source_ids": sorted(sourceids), "used_ids": sorted(used), "dismissed_ids": sorted(dismissed),
        "missing_anchor_ids": sorted(missing), "invalid_assessment_ids": sorted(x for x in used | dismissed | missing if not valid_id(x, view)),
        "used_dismissed_overlap": sorted(normalize(used) & normalize(dismissed)),
        "unaccounted_source_ids": sorted(sourceids - normalize(used) - normalize(dismissed)),
        "cited_ids": sorted({x for item in review["issues"] + response["checks"] for x in item["evidence_ids"]}),
        "issue_diff_locations": [{"issue_index": i, "file_in_diff": x["file"] in diff_files,
                                  "head_hunk_line": x["line"] in diff_lines["head"].get(x["file"], set()),
                                  "base_hunk_line": x["line"] in diff_lines["base"].get(x["file"], set())}
                                 for i, x in enumerate(review["issues"])],
        "note": "Self-reported use and file-ID citation do not prove source understanding; location-only mismatches remain warnings."
    }
    return errors, warnings, coverage


def legacy_regression():
    """Read-only check that the DIFF compatibility fix changes no F/G validity."""
    old = read(REFERENCE / "audit_all.json")
    annotations = read(REFERENCE / "author_annotations.json")
    changed, count = [], 0
    before = collections.defaultdict(lambda: collections.Counter(TP=0, FP=0, FN=0))
    after = collections.defaultdict(lambda: collections.Counter(TP=0, FP=0, FN=0))
    for row in old["rows"]:
        rid = row["run_id"]
        common = read(REFERENCE / "cases" / f"{row['case_id']}_{row['world']}.json")["common"]
        ret = read(REFERENCE / "retrieval" / f"{rid}.json")
        packet = {"source": ret["source"], "plain": ret["observations"], "audit": ret["audit"]}
        response = read(REFERENCE / "raw" / f"{rid}_s2.json")["response"]
        prior_errors, _, _ = response_checks(response, packet, common, allow_diff_files=False)
        new_errors, _, _ = response_checks(response, packet, common, allow_diff_files=True)
        if prior_errors != new_errors:
            changed.append({"run_id": rid, "before": prior_errors, "after": new_errors})
        valid = row["status"] == "ok" and not row["errors"]
        valid_new = row["status"] == "ok" and not (set(row["errors"]) - set(prior_errors) | set(new_errors))
        n = len(response["review"]["issues"])
        unsafe = row["label"] == "unsafe"
        for target, okay in [(before, valid), (after, valid_new)]:
            for metric, field in [("strict", "supported_issue_indices"), ("local", "local_supported_issue_indices")]:
                tp = int(okay and unsafe and bool(annotations[rid][field]))
                target[(row["arm"], metric)].update(TP=tp, FP=n - tp, FN=int(unsafe) - tp)
        count += 1
    result = {"status": "passed" if not changed and before == after and count == 144 else "failed",
              "reference_rows": count, "changed_response_gates": changed,
              "before": {"/".join(k): dict(v) for k, v in before.items()},
              "after": {"/".join(k): dict(v) for k, v in after.items()},
              "reference_audit_sha256": sha(REFERENCE / "audit_all.json"),
              "reference_annotations_sha256": sha(REFERENCE / "author_annotations.json"),
              "scope": "Only DIFF-file compatibility gate is changed; causal annotations and line-warning policy unchanged."}
    save(R / "legacy_gate_regression.json", result)
    assert result["status"] == "passed", result
    return result


def audit_cohort(preflight=False):
    import experiment as e
    from jsonschema import Draft202012Validator

    frozen = read(R / "freeze.json")
    algorithm = read(R / "algorithm_seal.json")
    manifest = read(R / "stage_reuse_manifest.json")
    labels = read(R / "labels.json")
    checks = {"algorithm_hashes": all(sha(ROOT / p) == h for p, h in algorithm["files"].items()),
              "freeze_hashes": all(sha(ROOT / p) == h for p, h in frozen["files"].items())}
    workspace_errors, packet_errors, reuse_errors, common_errors = [], [], [], []
    for path, expected in read(R / "reference_seals.json").items():
        if sha(path) != expected:
            reuse_errors.append([path, "reference_seal_hash"])
    manifests = {p.stem: read(p) for p in (R / "workspace_manifests").glob("*.json")}
    masked = {}
    for stem, item in manifests.items():
        root = Path(item["root"])
        if read(root / "pyrightconfig.json") != item["pyrightconfig"]:
            workspace_errors.append([stem, "pyrightconfig"])
        for file, expected in item["sources"].items():
            if digest((root / file).read_text()) != expected:
                workspace_errors.append([stem, file])
    checks["workspace_hashes"] = not workspace_errors

    packets, treated_worlds = {}, []
    for stem in sorted(manifests):
        case_id, world = stem.rsplit("_", 1)
        meta = read(R / "cases" / f"{stem}.json")
        if meta["common"] != read(REFERENCE / "cases" / f"{stem}.json")["common"]:
            common_errors.append([stem, "common_changed"])
        changed_files = set(meta["changed_files"])
        old_seed = read(REFERENCE / "packets/seed" / f"{stem}_initial.json")
        old_fixed = read(REFERENCE / "packets/F" / f"{stem}_fixed.json")
        history = read(R / "candidate_history" / f"{stem}.json")
        if history["fixed_native_queries"] != old_fixed["audit"]["additional_queries"] or history["fresh_native_queries"] != 0:
            packet_errors.append([stem, "fixed_history_changed"])
        if sha(ROOT / history["source"]) != history["source_sha256"]:
            packet_errors.append([stem, "history_source_hash"])
        trace = read(REFERENCE / "lsp_fixed" / f"{stem}.json")["actual_trace"]
        native = [x for x in trace if x.get("method") in {"textDocument/definition", "textDocument/references"}]
        if len(native) != len(history["fixed_native_queries"]):
            packet_errors.append([stem, "historical_trace_count"])
        for actual, query in zip(native, history["fixed_native_queries"]):
            expected_position = {"line": query["line"] - 1, "character": query["character_utf16"]}
            if (actual["method"] != "textDocument/" + query["operation"]
                    or actual["params"]["position"] != expected_position
                    or actual["response"].get("result") != query["raw"]):
                packet_errors.append([stem, "historical_native_binding"])
        for arm in e.ARMS:
            job = {"case_id": case_id, "world": world, "arm": arm}
            for stage in (1, 2):
                packet = read(e.packet_path(job, stage))
                packets[arm, stem, stage] = packet
                a, view = packet["audit"], evidence_view(packet)
                key = f"{stem}/{arm}/s{stage}"
                if view["conflicts"]:
                    packet_errors.append([key, "conflicting_numbered_source", view["conflicts"]])
                if token_count(packet["source"]) > 5200 or token_count(packet["plain"]) > 800:
                    packet_errors.append([key, "budget"])
                queries = a.get("queries", [])
                if len(queries) > 18 or any(q.get("depth", 0) > 3 or q.get("error") for q in queries):
                    packet_errors.append([key, "query_budget_or_error"])
                if arm in {"B0", "B1"} and (queries or a.get("fresh_native_queries") != 0 or a.get("cached_native_queries") != 0):
                    packet_errors.append([key, "native_leakage"])
                if arm == "B0":
                    if packet["source"] or packet["plain"] or a.get("source_lines") or a.get("base_lines") or a.get("ast_parsed_files") or a.get("source_selection_inputs"):
                        packet_errors.append([key, "diff_only_leakage"])
                elif not a.get("root_coverage") or not a.get("has_roots"):
                    packet_errors.append([key, "root_coverage"])
                if arm == "B1":
                    if not set(a.get("ast_parsed_files", [])) <= changed_files:
                        packet_errors.append([key, "B1_nonchanged_parse"])
                    if any(p.get("origin") not in {"changed_root", "deleted_base"} for p in a.get("admitted", [])):
                        packet_errors.append([key, "B1_nonroot_selection"])
                for version, field in [("head", "source_lines"), ("base", "base_lines")]:
                    worldstem = stem if version == "head" else f"{case_id}_{'b' if world == 'a' else 'a'}"
                    source_root = Path(manifests[worldstem]["root"])
                    for file, numbers in a.get(field, {}).items():
                        if file not in manifests[worldstem]["sources"]:
                            packet_errors.append([key, "unsealed_source", version, file])
                            continue
                        if arm == "B1" and file not in changed_files:
                            packet_errors.append([key, "B1_crossfile_source", file])
                        cache_key = (worldstem, file)
                        if cache_key not in masked:
                            masked[cache_key] = mask_source((source_root / file).read_text()).splitlines()
                        actual_lines = masked[cache_key]
                        rendered = view["supplied"][version, file]
                        if set(rendered) != set(numbers):
                            packet_errors.append([key, "rendered_audit_mismatch", version, file])
                        if any(not 1 <= n <= len(actual_lines) or rendered.get(n) != actual_lines[n - 1] for n in numbers):
                            packet_errors.append([key, "source_content", version, file])
                for version, file in view["supplied"]:
                    if file not in a.get("source_lines" if version == "head" else "base_lines", {}):
                        packet_errors.append([key, "untracked_rendered_file", version, file])
                for inv in a.get("class_inventories", []):
                    source = (Path(manifests[stem]["root"]) / inv["file"]).read_text()
                    excerpt = "\n".join(source.splitlines()[inv["line"] - 1:inv["end_line"]])
                    if digest(source) != inv["full_file_source_sha256"] or digest(excerpt) != inv["full_class_source_sha256"] or inv["evidence_id"] not in view["ids"]:
                        packet_errors.append([key, "inventory_binding", inv["file"]])
            initial, final = packets[arm, stem, 1], packets[arm, stem, 2]
            if arm in {"B0", "B1"} and initial != final:
                packet_errors.append([stem, arm, "stage_packet_changed"])
            if arm == "B2":
                if initial != old_seed or final["audit"]["queries"] != old_fixed["audit"]["queries"]:
                    packet_errors.append([stem, "B2_seed_or_history_changed"])
                same = final["source"] == old_fixed["source"] and final["plain"] == old_fixed["plain"]
                if same != history["packing"]["same_f_source_plain"]:
                    packet_errors.append([stem, "B2_treatment_flag"])
                if not same:
                    treated_worlds.append(stem)
                    for package in old_seed["audit"]["admitted"]:
                        if package["origin"] in {"changed_root", "deleted_base"}:
                            version, file, _ = package["key"]
                            field = "source_lines" if version == "head" else "base_lines"
                            if not set(package["lines"]) <= set(final["audit"][field].get(file, [])):
                                packet_errors.append([stem, "mandatory_root_lost", package["key"]])

    settings = {s: e.settings(R / ("schema_first.json" if s == 1 else "schema_final.json")) for s in (1, 2)}
    validators = {s: Draft202012Validator(read(R / ("schema_first.json" if s == 1 else "schema_final.json"))) for s in (1, 2)}
    for s in (1, 2):
        name = "schema_first.json" if s == 1 else "schema_final.json"
        if sha(R / name) != sha(REFERENCE / name):
            reuse_errors.append([name, "schema_changed"])
    rows, coverage = [], []
    prompt_groups = collections.defaultdict(set)
    expected_stages = {e.stage_key(job, s) for job in frozen["jobs"] for s in (1, 2)}
    if set(manifest["entries"]) != expected_stages:
        reuse_errors.append(["manifest_stage_inventory"])
    if dict(collections.Counter(x["mode"] for x in manifest["entries"].values())) != manifest["stage_counts"]:
        reuse_errors.append(["manifest_stage_counts"])
    reference_seal = read(REFERENCE / "result_seal.json")["files"]
    fresh_record_keys, reused_keys = set(), set()
    for job in frozen["jobs"]:
        rid, stem = job["run_id"], f"{job['case_id']}_{job['world']}"
        meta = read(R / "cases" / f"{stem}.json")
        path = R / "trajectories" / f"{rid}.json"
        trajectory = read(path) if path.exists() else {"status": "missing"}
        errors, warnings, records, stage_reuse = [], [], {}, {}
        fresh_seconds = reused_seconds = 0.0
        usage, logical_usage, prompt_tokens = [], [], []
        fresh_calls = 0
        for stage in (1, 2):
            resolved = e.resolve_stage(job, stage)
            entry, key = resolved["entry"], e.stage_key(job, stage)
            stage_reuse[stage] = resolved["reused"]
            packet = packets[job["arm"], stem, stage]
            if (entry["job"] != job or entry["stage"] != stage or entry["logical_packet_file_sha256"] != sha(e.packet_path(job, stage))
                    or entry["logical_packet_sha256"] != e.packet_digest(packet) or entry["settings"] != settings[stage]):
                reuse_errors.append([key, "logical_binding_or_settings"])
            if resolved["reused"]:
                reused_keys.add(key)
                if job["arm"] != "B2" or not entry["prior_comparison"]["prompt_identical"]:
                    reuse_errors.append([key, "reuse_eligibility"])
                if sha(ROOT / entry["source_result_seal_path"]) != entry["source_result_seal_sha256"]:
                    reuse_errors.append([key, "reference_seal_changed"])
                for name, expected in entry["original_file_sha256"].items():
                    origin = resolved[name]
                    sealed = reference_seal.get(str(origin.relative_to(REFERENCE)), {})
                    sealed = sealed.get("sha256") if isinstance(sealed, dict) else sealed
                    if sha(origin) != expected or expected != sealed:
                        reuse_errors.append([key, "reused_original_hash", name])
                if (R / "raw" / f"{key}.json").exists() or (R / "inputs" / f"{key}.json").exists():
                    reuse_errors.append([key, "counterfeit_reused_output"])
            else:
                fresh_calls += (R / "actual_cli_starts" / f"{key}.json").exists()
            if not resolved["raw_path"].exists():
                if not preflight:
                    errors.append(f"missing_stage_{stage}")
                continue
            record = read(resolved["raw_path"])
            records[stage] = record
            if not resolved["reused"]:
                fresh_record_keys.add(key)
            inp, trace = read(resolved["input_path"]), read(resolved["trace_path"])
            if record["run_id"] != resolved["original_stage_id"] or inp["run_id"] != resolved["original_stage_id"]:
                errors.append(f"stage_identity_{stage}")
            if record["model"] != frozen["model"] or e.normalized_command(trace["command"]) != settings[stage]["command"]:
                errors.append(f"model_or_command_{stage}")
            if record["status"] != "ok":
                errors.append(f"transport_or_schema_{stage}")
            if record.get("tool_audit", {}).get("tool_call_count") != 0:
                errors.append(f"model_tools_{stage}")
            if digest(inp["prompt"]) != inp["prompt_sha256"] or inp["prompt_sha256"] != record["prompt_sha256"]:
                errors.append(f"prompt_hash_{stage}")
            if inp["packet_sha256"] != record["packet_sha256"]:
                errors.append(f"record_packet_binding_{stage}")
            if resolved["reused"]:
                original_packet = read(REFERENCE / ("packets/seed" if stage == 1 else "packets/F") / f"{stem}_{'initial' if stage == 1 else 'fixed'}.json")
                if e.packet_digest(original_packet) != record["packet_sha256"]:
                    reuse_errors.append([key, "original_packet_binding"])
            elif e.packet_digest(packet) != record["packet_sha256"]:
                errors.append(f"current_packet_binding_{stage}")
            prior = {k: v for k, v in records[1]["response"].items() if k != "context_requests"} if stage == 2 and records.get(1, {}).get("response") else None
            expected_prompt = e.build_prompt(meta, packet, stage == 1, prior)
            if inp["prompt"] != expected_prompt:
                errors.append(f"prompt_or_prior_reconstruction_{stage}")
                if resolved["reused"]:
                    reuse_errors.append([key, "reused_prompt_or_prior_mismatch"])
            if entry.get("expected_prompt_sha256") is not None and digest(expected_prompt) != entry["expected_prompt_sha256"]:
                reuse_errors.append([key, "manifest_prompt_binding"])
            if stage == 1:
                prompt_groups[job["arm"], stem].add(record["prompt_sha256"])
            prompt_tokens.append(token_count(inp["prompt"]))
            duration = record.get("duration_seconds", 0)
            logical_usage.extend(record.get("usage", []))
            if resolved["reused"]:
                reused_seconds += duration
            else:
                fresh_seconds += duration
                usage.extend(record.get("usage", []))
                if not (R / "actual_cli_starts" / f"{key}.json").exists():
                    errors.append(f"fresh_start_missing_{stage}")
            response = record.get("response")
            if not response:
                if resolved["reused"]:
                    reuse_errors.append([key, "reused_response_absent"])
                continue
            if list(validators[stage].iter_errors(response)):
                errors.append(f"response_schema_{stage}")
                if resolved["reused"]:
                    reuse_errors.append([key, "reused_response_schema"])
                continue
            local_errors, local_warnings, accounting = response_checks(response, packet, meta["common"])
            if stage == 2:
                errors.extend(local_errors)
            else:
                warnings.extend("first_stage:" + x for x in local_errors)
            warnings.extend(f"s{stage}:" + x for x in local_warnings)
            coverage.append({"run_id": rid, "arm": job["arm"], "stage": stage, "reused": resolved["reused"], **accounting})
        final_packet = packets[job["arm"], stem, 2]
        retrieval_path = R / "retrieval" / f"{rid}.json"
        if retrieval_path.exists():
            ret = read(retrieval_path)
            actual_packet = {"source": ret["source"], "plain": ret["observations"], "audit": ret["audit"]}
            if actual_packet != final_packet or ret["packet_sha256"] != e.packet_digest(final_packet) or ret["actual_additional_native_trace"] or ret.get("fresh_native_queries") != 0:
                packet_errors.append([rid, "retrieval_packet_or_native"])
            if job["arm"] == "B2" and sha(ROOT / ret["historical_native_trace_reference"]) != ret["historical_native_trace_sha256"]:
                packet_errors.append([rid, "retrieval_historical_trace_hash"])
        elif trajectory["status"] == "ok":
            packet_errors.append([rid, "missing_successful_retrieval"])
        if path.exists() and (trajectory["physical_calls"] != fresh_calls or trajectory.get("logical_stage_slots") != 2):
            errors.append("trajectory_call_accounting")
        rows.append({**job, "label": labels[job["case_id"]]["worlds"][job["world"]],
                     "status": trajectory["status"], "errors": errors, "warnings": warnings,
                     "first": records.get(1, {}).get("response"), "final": records.get(2, {}).get("response"),
                     "physical_calls": fresh_calls, "fresh_physical_calls": fresh_calls, "logical_stages": 2,
                     "reused_stages": sum(stage_reuse.values()), "stage_modes": {str(k): "reused" if v else "fresh" for k, v in stage_reuse.items()},
                     "source_tokens": token_count(final_packet["source"]), "observation_tokens": token_count(final_packet["plain"]),
                     "seconds": fresh_seconds, "reused_model_seconds": reused_seconds, "usage": usage,
                     "fresh_usage": usage, "logical_usage": logical_usage, "total_prompt_tokens": sum(prompt_tokens)})
    actual_new_raw = {p.stem for p in (R / "raw").glob("*_s[12].json")}
    actual_starts = {p.stem for p in (R / "actual_cli_starts").glob("*.json")}
    allowed_fresh = {key for key, item in manifest["entries"].items() if item["mode"] == "fresh"}
    if not actual_starts <= allowed_fresh or actual_starts & reused_keys:
        reuse_errors.append(["CLI_start_inventory", sorted(actual_starts - allowed_fresh)])
    if len(actual_starts) != sum(x["physical_calls"] for x in rows):
        reuse_errors.append(["CLI_start_count_not_attributed"])
    if actual_new_raw != fresh_record_keys:
        reuse_errors.append(["fresh_record_inventory", sorted(actual_new_raw - fresh_record_keys)])
    regression = legacy_regression()
    checks.update(packet_and_native_integrity=not packet_errors,
                  shared_common_prompts=not common_errors and all(len(values) == 1 for values in prompt_groups.values()),
                  complete_trajectories=len(rows) == 216 and all(x["status"] != "missing" for x in rows),
                  reuse_integrity=not reuse_errors, legacy_gate_regression=regression["status"] == "passed",
                  all_operational_valid=all(x["status"] == "ok" and not x["errors"] for x in rows),
                  exact_model_record_inventory=actual_new_raw == fresh_record_keys)
    out = {"checks": checks, "preflight": preflight, "workspace_errors": workspace_errors,
           "packet_errors": packet_errors, "reuse_errors": reuse_errors, "common_errors": common_errors,
           "logical_trajectories": len(rows), "logical_stages": len(rows) * 2,
           "fresh_physical_calls": sum(x["physical_calls"] for x in rows), "reused_stage_slots": len(reused_keys),
           "actual_new_native_requests": 0, "treated_B2_worlds": treated_worlds,
           "treated_unsafe_worlds": [s for s in treated_worlds if labels[s.rsplit('_', 1)[0]]["worlds"][s[-1]] == "unsafe"],
           "rows": rows}
    save(R / ("audit_preflight.json" if preflight else "audit_all.json"), out)
    if not preflight:
        save(R / "context_consumption_audit.json", {"rows": coverage, "assessment_errors_are_auxiliary": True})
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--legacy-regression", action="store_true")
    parser.add_argument("--preflight", action="store_true")
    args = parser.parse_args()
    if args.legacy_regression:
        print(json.dumps(legacy_regression(), ensure_ascii=False, indent=2))
    else:
        result = audit_cohort(args.preflight)
        print(json.dumps({k: v for k, v in result.items() if k != "rows"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
