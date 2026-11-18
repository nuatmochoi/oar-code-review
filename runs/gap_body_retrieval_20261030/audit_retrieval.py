"""Read-only, pre-model audit of all prepared retrieval inputs and tool records.

No selector/executor/retrieval policy is imported or executed. Source-token
accounting is recomputed from exact snapshot/path/line unions. The inherited
line-framed renderer parser and masking convention are used as file formats.
"""
from __future__ import annotations

import argparse
from collections import Counter
import copy
from datetime import datetime, timezone
import hashlib
import html
import json
from pathlib import Path
import re
from urllib.parse import unquote, urlparse

from bootstrap import ROOT
import delivery_rendering as framing
from ace_review.protocol_hypothesis import mask_source
import tiktoken


ARMS = ("replay", "frontier", "gap")
METHODS = {
    "go_to_definition": {"textDocument/definition"},
    "find_references": {"textDocument/references"},
    "symbol_search": {"workspace/symbol"},
    "document_symbols": {"textDocument/documentSymbol"},
    "incoming_calls": {"textDocument/prepareCallHierarchy", "callHierarchy/incomingCalls"},
    "outgoing_calls": {"textDocument/prepareCallHierarchy", "callHierarchy/outgoingCalls"},
}
ENCODING = None


def sha_bytes(value):
    return hashlib.sha256(value).hexdigest()


def sha_file(path):
    return sha_bytes(Path(path).read_bytes())


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def load(path):
    return json.loads(Path(path).read_text())


def require(condition, message):
    if not condition:
        raise ValueError(message)


def union(sources):
    out, ids = {}, {}
    for source in sources:
        require(source["snapshot"] in ("head", "base"), "invalid source snapshot")
        eid = source["evidence_id"]
        require(eid not in ids or ids[eid] == source, "conflicting evidence ID")
        ids[eid] = source
        for n, text in source["lines"]:
            require(type(n) is int and n > 0 and isinstance(text, str), "invalid source line")
            key = source["snapshot"], source["file"], n
            require(key not in out or out[key] == text, "source union conflict")
            out[key] = text
    return out


def tokens(lines):
    global ENCODING
    if ENCODING is None:
        ENCODING = tiktoken.get_encoding("o200k_base")
    total, run, previous = 0, [], None
    for key, text in sorted(lines.items()):
        contiguous = previous and key[:2] == previous[:2] and key[2] == previous[2] + 1
        if run and not contiguous:
            total += len(ENCODING.encode("\n".join(run), disallowed_special=()))
            run = []
        run.append(text)
        previous = key
    if run:
        total += len(ENCODING.encode("\n".join(run), disallowed_special=()))
    return total


def render_extra(sources):
    blocks = []
    for s in sources:
        path = html.escape(s["file"], quote=True)
        blocks.append(f'<evidence id="{s["evidence_id"]}" operation="read_lines" source="{path}:{s["lines"][0][0]}-{s["lines"][-1][0]}" path="{path}" snapshot="head">\n' +
                      "\n".join(f"{n}: {text}" for n, text in s["lines"]) + "\n</evidence>")
    return "\n".join(blocks) + "\n" if blocks else ""


def visible_lines(parts, sources):
    expected = {s["evidence_id"]: s for s in sources}
    visible = {eid: {} for eid in expected}
    for text in parts.values():
        for block in framing._blocks(text):
            attrs = block["attrs"]
            eid = attrs.get("evidence_id" if block["tag"] == "file" else "id")
            if eid not in expected:
                continue  # historical diff/metadata are preserved byte-for-byte
            s = expected[eid]
            require(block["closed"], f"unterminated source wrapper: {eid}")
            require(framing._source_path_matches(block, s["file"]), f"source path mismatch: {eid}")
            require(not attrs.get("snapshot") or attrs["snapshot"] == s["snapshot"], f"snapshot mismatch: {eid}")
            declared = eid.rsplit("@", 1)[-1]
            require(declared not in ("head", "base") or declared == s["snapshot"], f"ID snapshot mismatch: {eid}")
            wanted = dict(s["lines"])
            for line, terminated in block["body"]:
                match = framing._LINE.fullmatch(line)
                if not match:
                    continue
                n, value = int(match.group(1)), match.group(2)
                require(terminated and n in wanted and wanted[n] == value, f"source text mismatch: {eid}:{n}")
                require(n not in visible[eid] or visible[eid][n] == value, f"repeated source conflict: {eid}:{n}")
                visible[eid][n] = value
    for eid, s in expected.items():
        require(visible[eid] == dict(s["lines"]), f"source omitted from prepared raw: {eid}")
    return union(sources)


class HeadReader:
    def __init__(self):
        self.cache = {}

    def read(self, manifest, path):
        require(path in manifest["sources"], f"outside HEAD manifest: {path}")
        root = Path(manifest["root"]).resolve()
        target = (root / path).resolve(strict=True)
        require(not Path(path).is_absolute() and target.is_relative_to(root), "source path escapes root")
        key = str(root), path, manifest["sources"][path]
        if key not in self.cache:
            raw = target.read_bytes()
            require(sha_bytes(raw) == manifest["sources"][path], f"HEAD SHA changed: {path}")
            text = raw.decode("utf-8", errors="replace")
            self.cache[key] = raw, text.splitlines(), mask_source(text).splitlines()
        return self.cache[key]


def native_positions(value, root):
    """Positions present in the saved server response, not inferred symbols."""
    positions = set()
    def visit(node):
        if isinstance(node, list):
            for item in node:
                visit(item)
        elif isinstance(node, dict):
            uri = node.get("uri") or node.get("targetUri")
            if isinstance(uri, str):
                try:
                    path = str(Path(unquote(urlparse(uri).path)).resolve().relative_to(Path(root).resolve()))
                    for name in ("range", "selectionRange", "targetSelectionRange"):
                        rng = node.get(name)
                        if isinstance(rng, dict) and isinstance(rng.get("start"), dict):
                            start, end = rng["start"], rng.get("end", rng["start"])
                            positions.add((path, start.get("line", 0) + 1, end.get("line", start.get("line", 0)) + 1,
                                           start.get("character", 0) + 1))
                except (ValueError, TypeError):
                    pass
            for child in node.values():
                if isinstance(child, (list, dict)):
                    visit(child)
    visit(value)
    return positions


def head_coverage(lines):
    return {(path, n) for (snapshot, path, n) in lines if snapshot == "head"}


def unit_summary(kind, units, initial, acquired, admitted, prepared):
    rows = []
    for u in units:
        expected = {(u["path"], n) for n in range(u["start"], u["end"] + 1)}
        require(bool(expected), "empty unit interval")
        before, after = expected <= initial, expected <= admitted
        if kind == "gap":
            require(u.get("model_delivered_complete") == before, "gap model_delivered_complete must describe initial delivery only")
            require(u.get("initial_delivered_complete") == before, "gap initial coverage flag mismatch")
            require(u.get("acquired_and_admitted_complete") == after, "gap acquisition coverage flag mismatch")
        elif "delivered_by_new_call" in u:
            require(u["delivered_by_new_call"] is False, "preparation claims a new model delivery")
        rows.append(dict(kind=kind, id=u["id"], path=u["path"], start=u["start"], end=u["end"],
                         initial_source_complete=before, physical_read_coverage_complete=expected <= acquired,
                         admitted_complete=after, prepared_source_complete=expected <= prepared,
                         newly_prepared_complete=not before and expected <= prepared,
                         actual_new_model_delivery_complete=None, semantic_grounding=None))
    return rows


def audit_review(root, group, arm, context, reader):
    rid = f"{group}_{arm}"
    path, tracepath = root / "prepared/inputs" / f"{rid}.json", root / "retrieval" / f"{rid}.json"
    p, trace = load(path), load(tracepath)
    require(p["review_id"] == trace["review_id"] == rid and p["group_id"] == group and p["arm"] == trace["arm"] == arm, "review dimensions differ")
    require(p["retrieval_reference"]["path"] == str(tracepath) and p["retrieval_reference"]["sha256"] == sha_file(tracepath), "trace reference differs")
    require(trace["policy_freeze_sha256"] == sha_file(root / "policy_freeze.json"), "policy freeze reference differs")
    require(trace["actual_model_calls"] == 0 and trace["source_output_gate"] is False, "unexpected model call/output gate")
    require(trace["visibility_stage"] == "prepared_composition_not_yet_a_model_call", "preparation mislabeled as delivery")
    initial, extra = p["protected_sources"], p["extra_sources"]
    require(initial == context["sources"], "protected source rows/order/IDs changed")
    before = union(initial)
    require(sha_bytes(canonical([[*key, value] for key, value in sorted(before.items())]).encode()) == context["source_exposure"]["union_sha256"], "initial union SHA mismatch")
    initial_tokens = tokens(before)
    require(initial_tokens == trace["initial_source_tokens"] == context["source_exposure"]["union_reference_tokens"], "initial token count mismatch")
    expected_raw = copy.deepcopy(context["raw"])
    rendered = render_extra(extra)
    if rendered:
        suffix = expected_raw["suffix"]
        pos = suffix.index("<causal_review_state>")
        expected_raw["suffix"] = suffix[:pos] + "<retrieval_evidence>\n" + rendered + "</retrieval_evidence>\n" + suffix[pos:]
    require(p["raw"] == expected_raw, "protected prefix/state or non-source parts changed")
    require(p["state_cap"] == 5120 and p["state_tokens"] == context["prepared"]["state_tokens"] <= 5120, "state cap/count changed")
    state = re.search(r"<causal_review_state>.*?</causal_review_state>", p["raw"]["suffix"], re.S).group()
    require(len(ENCODING.encode(state, disallowed_special=())) == p["state_tokens"], "actual state tokens differ")
    require(p["order"] == "system_shared_prompt_suffix", "unexpected composition order")
    composed = "\n\n".join(f"[{k.upper()}]\n{p['raw'][k]}" for k in ("system", "shared", "prompt", "suffix") if p["raw"][k])
    require(sha_bytes(composed.encode()) == p["composed_sha256"], "prepared composed SHA mismatch")
    require(p["provided_evidence_ids"] == list(dict.fromkeys(context["prepared"]["provided_evidence_ids"] + [s["evidence_id"] for s in extra])), "provided evidence IDs differ")
    prepared = visible_lines(p["raw"], initial + extra)
    final_tokens = tokens(prepared)
    require(final_tokens == trace["final_source_tokens"] <= 5200 and trace["source_cap"] == 5200, "source cap/count mismatch")
    events, native = trace["events"], trace["native_executor"]
    require(trace["new_query_cap"] == 6 and trace["new_logical_queries"] == len(events) <= 6, "logical query budget mismatch")
    require(trace["inherited_queries"] == context["inherited_queries"], "inherited query accounting changed")
    counts = Counter(logical_queries=0, native_invocations=0, rpc_writes=0, rpc_write_attempts=0,
                     body_queries=0, body_fs_read_attempts=0, executor_file_read_attempts=0,
                     server_open_read_attempts=0, frontier_index_read_attempts=0, gap_index_read_attempts=0)
    statuses = Counter()
    admitted, acquired, flattened = list(initial), list(initial), {k: [] for k in ("rpc_events", "rpc_responses", "file_reads", "server_open_reads")}
    source_ids, request_results = {}, []
    manifest = context["manifest"]
    for sequence, event in enumerate(events, 1):
        q, result = event["request"], event["result"]
        require(event["sequence"] == sequence and result["request"] == q, "event request/sequence differs")
        require(result["logical_queries"] == 1, "executed query is not charged exactly once")
        op = q["operation"]
        statuses[result["status"]] += 1
        require(op == "read_lines" or op in METHODS, "unknown query operation")
        counts["logical_queries"] += 1
        counts["native_invocations"] += result["native_invocations"]
        require(result["native_invocations"] in (0, 1) and (op != "read_lines" or result["native_invocations"] == 0), "native invocation count invalid")
        wires = result["rpc_events"]
        require(all(w["message"]["method"] in METHODS.get(op, set()) for w in wires), "wire method incompatible with logical query")
        require(result["rpc_delta"] == sum(w["write_succeeded"] for w in wires) and result["rpc_write_attempts"] == len(wires), "wire counter mismatch")
        counts["rpc_writes"] += result["rpc_delta"]
        counts["rpc_write_attempts"] += len(wires)
        require(result["file_read_attempts"] == len(result["file_reads"]), "physical file-read counter mismatch")
        body_reads = sum(x["purpose"] == "body_read" for x in result["file_reads"])
        require(result["body_read_attempts"] == body_reads, "body filesystem counter mismatch")
        counts["executor_file_read_attempts"] += len(result["file_reads"])
        counts["body_fs_read_attempts"] += body_reads
        counts["server_open_read_attempts"] += len(result["server_open_reads"])
        for key in flattened:
            flattened[key].extend(result[key])
        for read in result["file_reads"]:
            if read.get("read_succeeded"):
                raw, _, _ = reader.read(manifest, read["path"])
                require(read["expected_sha256"] == read["actual_sha256"] == sha_bytes(raw) and read["hash_matches"] is True, "successful executor read failed HEAD integrity")
        returned_positions = set()
        for response in result["rpc_responses"]:
            require(response["method"] in METHODS.get(op, set()), "RPC response method mismatch")
            if response["status"] == "completed":
                returned_positions |= native_positions(response["raw_result"], manifest["root"])
        for location in result["locations"]:
            raw, rawlines, _ = reader.read(manifest, location["path"])
            require(location["source_sha256"] == sha_bytes(raw) and location["source"].startswith("lsp:"), "location is not native hash-verified HEAD")
            original = location["native_range"]
            key = (location["path"], original["line_start"], original["line_end"], original["column_start"])
            require(key in returned_positions, "normalized native position missing from actual RPC response")
            require(1 <= location["start"] <= location["end"] <= len(rawlines), "native normalized range outside HEAD")
        if op == "read_lines":
            counts["body_queries"] += 1
            require(type(q["start"]) is int and type(q["end"]) is int and 1 <= q["start"] <= q["end"] and q["end"] - q["start"] < 200, "read range exceeds 200 lines")
            if result["lines"]:
                raw, _, masked = reader.read(manifest, q["path"])
                wanted = [[n, masked[n - 1]] for n in range(q["start"], min(q["end"], len(masked)) + 1)]
                require(result["lines"] == wanted and result["source_sha256"] == sha_bytes(raw), "returned body differs from actual masked HEAD")
                require(result["normalization"] == "mask_source" and body_reads == 1, "body lacks real read/masking provenance")
                acquired.append(dict(file=q["path"], snapshot="head", evidence_id=f"audit-read-{sequence}", lines=result["lines"]))
        else:
            require(not result["lines"] and not event["admitted_sources"], "locator treated as source body")
        for source in event["admitted_sources"]:
            require(op == "read_lines" and source["snapshot"] == "head" and source["file"] == q["path"] and source["lines"] == result["lines"], "admitted source is not exact body read result")
            source_ids[source["evidence_id"]] = source
            admitted.append(source)
        require(tokens(union(admitted)) == event["source_union_tokens"] <= 5200, "event cumulative source cap/count mismatch")
        request_results.append(result)
    require(list(source_ids.values()) == extra, "extra prepared sources differ from admitted results")
    require(union(admitted) == prepared, "admitted union differs from actual prepared lines")
    cleanup = native["cleanup"]
    if arm == "replay":
        require(not events and not extra and p["raw"] == context["raw"] and cleanup.get("not_started") is True, "replay performed retrieval")
    else:
        require(native["requests"] == request_results, "executor request ledger differs from query trace")
        for key, values in flattened.items():
            require(native[key] == values, f"executor aggregate {key} differs from query records")
        require(cleanup.get("passed") is True and not cleanup["errors"] and cleanup["stderr_drained"] is True, "native cleanup failed")
        require(all(p.get("reaped") is True and p.get("exit_code") is not None for p in cleanup["processes"]), "native process not reaped")
    frontier = trace["acquisition_frontier"]
    inherited_reads = context["frontier"]["source_reads"]
    require(frontier["source_reads"][:len(inherited_reads)] == inherited_reads, "historical index reads changed")
    new_frontier_reads = frontier["source_reads"][len(inherited_reads):]
    gap = trace.get("gap_selector")
    gap_reads = gap["source_reads"] if gap else []
    for read in new_frontier_reads + gap_reads:
        if read.get("read_succeeded"):
            raw, _, _ = reader.read(manifest, read["path"])
            require(read["source_sha256"] == sha_bytes(raw), "scoped index source hash mismatch")
    counts["frontier_index_read_attempts"] = len(new_frontier_reads)
    counts["gap_index_read_attempts"] = len(gap_reads)
    if gap:
        require(gap["counters"]["scoped_index_read_attempts"] == len(gap_reads), "gap index read count mismatch")
    oldcov, readcov, admittedcov, preparedcov = map(head_coverage, (before, union(acquired), union(admitted), prepared))
    units = unit_summary("frontier", frontier["units"], oldcov, readcov, admittedcov, preparedcov)
    if gap:
        units += unit_summary("gap", gap["units"], oldcov, readcov, admittedcov, preparedcov)
    return dict(review_id=rid, arm=arm, passed=True, prepared_sha256=sha_file(path), trace_sha256=sha_file(tracepath),
                counts=dict(counts), tool_statuses=dict(statuses), initial_source_tokens=initial_tokens, final_source_tokens=final_tokens,
                initial_source_lines=len(before), prepared_source_lines=len(prepared), added_evidence_ids=len(extra),
                protected_source_exact=True, state_unchanged=True, metadata_added=0, cleanup=cleanup,
                acquisition_units=units, actual_new_model_delivery_audited=False, semantic_grounding_assigned=False)


def run(root=ROOT, *, require_complete=True):
    root = Path(root).resolve()
    freeze = load(root / "policy_freeze.json")
    errors, rows = [], []
    for path, expected in freeze["files"].items():
        if sha_file(root / path) != expected:
            errors.append(dict(path=path, error="policy freeze file differs"))
    groups = freeze["groups"]
    expected = {f"{group}_{arm}" for group in groups for arm in ARMS}
    inputs = {p.stem for p in (root / "prepared/inputs").glob("*.json")}
    traces = {p.stem for p in (root / "retrieval").glob("*.json")}
    missing = sorted(expected - inputs | expected - traces)
    unexpected = sorted(inputs - expected | traces - expected)
    if len(groups) != 30 or len(expected) != 90 or unexpected or (require_complete and missing):
        errors.append(dict(error="incomplete or unexpected prepared coverage", missing=missing, unexpected=unexpected))
    reader = HeadReader()
    for group in groups:
        context = load(root / "prepared/contexts" / f"{group}.json")
        for arm in ARMS:
            rid = f"{group}_{arm}"
            if rid in missing:
                continue
            try:
                rows.append(audit_review(root, group, arm, context, reader))
            except Exception as exc:
                errors.append(dict(review_id=rid, error=f"{type(exc).__name__}: {exc}"))
    totals = {arm: dict(Counter({key: sum(r["counts"][key] for r in rows if r["arm"] == arm)
                                 for key in rows[0]["counts"]})) for arm in ARMS} if rows else {}
    statuses = {arm: dict(sum((Counter(r["tool_statuses"]) for r in rows if r["arm"] == arm), Counter())) for arm in ARMS}
    return dict(passed=not errors and (not require_complete or len(rows) == 90), complete=len(rows) == 90 and not missing,
                created_at_utc=datetime.now(timezone.utc).isoformat(), audit_source_sha256=sha_file(Path(__file__)),
                model_calls=0, lsp_calls=0, reviewed_inputs=len(rows), expected_inputs=90,
                by_arm_reviews=dict(Counter(r["arm"] for r in rows)), counts_by_arm=totals,
                tool_statuses_by_arm=statuses,
                missing=missing, errors=errors, rows=rows,
                scope="Independent saved-trace and prepared-input audit; no model calls or LSP calls. Acquired/admitted/prepared body coverage is not semantic grounding or proof of a new model delivery.",
                accounting="Logical invocations, native resolver invocations, successful wire writes, body FS reads, server didOpen reads and scoped index reads are separate. Planner observed counters include skipped requests and are not execution costs. Filtered native results are distinct from empty and failed outcomes.",
                unmetered_costs="File-read/hash/body/index counts cover controller-side filesystem access and the product client's didOpen reads. Pyright subprocess internal parsing, indexing, filesystem reads and CPU work are not metered; these are not total backend I/O costs.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run(args.root)
    if result["missing"]:
        print(json.dumps(dict(passed=False, missing=result["missing"], errors=result["errors"]), ensure_ascii=False))
        raise SystemExit(2)  # do not occupy the exclusive final output with a partial audit
    output = args.output or args.root / "verification/retrieval_audit.json"
    with output.open("x") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(json.dumps(dict(passed=result["passed"], reviewed_inputs=result["reviewed_inputs"], errors=result["errors"],
                          counts_by_arm=result["counts_by_arm"], output=str(output), sha256=sha_file(output)), ensure_ascii=False))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
