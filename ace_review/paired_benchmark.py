"""Schema and static eligibility gate for context-necessity pair families.

The validator intentionally reads the four snapshots.  A record is eligible
only when the review target is identical between safe and unsafe worlds, the
declared companion is stable within each world, and the target diff is the
only base-to-head source change.  Executable witness results are handled by
``ace_review.witness_runner`` so static and dynamic failures stay separable.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any


REQUIRED_TOP_LEVEL = (
    "schema_version",
    "family_id",
    "repository",
    "language",
    "origin",
    "split",
    "stratum",
    "target",
    "worlds",
    "companion_paths",
    "gold",
    "witness",
)
WORLD_NAMES = ("safe", "unsafe")
REVISIONS = ("base", "head")
ORIGINS = {"natural", "derived"}
SPLITS = {"development", "validation", "test"}
STRATA = {"static_decidable", "runtime_required", "unresolved"}


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def load_pair(path: str | Path) -> dict[str, Any]:
    """Load a pair JSON record and reject malformed top-level structures."""
    source = Path(path)
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"{source}: invalid JSON: {error}") from error
    if not isinstance(data, dict):
        raise ValueError("pair record must be a JSON object")
    missing = [field for field in REQUIRED_TOP_LEVEL if field not in data]
    if missing:
        raise ValueError("missing required field(s): " + ", ".join(missing))
    return data


def _resolve_inside(root: Path, value: object, label: str, errors: list[str]) -> Path | None:
    if not isinstance(value, str) or not value:
        errors.append(f"{label} must be a non-empty relative path")
        return None
    candidate = Path(value)
    if candidate.is_absolute():
        errors.append(f"{label} must be relative to pair root")
        return None
    resolved = (root / candidate).resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        errors.append(f"{label} escapes pair root: {value}")
        return None
    return resolved


def _tree_files(root: Path) -> dict[str, str]:
    if not root.is_dir():
        return {}
    return {
        path.relative_to(root).as_posix(): sha256_file(path)
        for path in sorted(root.rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts
    }


def _changed_paths(base: dict[str, str], head: dict[str, str]) -> set[str]:
    return {path for path in base.keys() | head.keys() if base.get(path) != head.get(path)}


def _diff_paths(text: str) -> set[str]:
    result: set[str] = set()
    for line in text.splitlines():
        if not line.startswith("+++ "):
            continue
        value = line[4:].split("\t", 1)[0]
        if value == "/dev/null":
            continue
        result.add(value[2:] if value.startswith("b/") else value)
    return result


HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def _apply_file_diff(base: str, diff: str, target_path: str) -> str:
    """Apply one ordinary unified-diff file section, validating every old line."""
    patch_lines = diff.splitlines(keepends=True)
    section: list[str] | None = None
    index = 0
    while index < len(patch_lines):
        if patch_lines[index].startswith("--- ") and index + 1 < len(patch_lines):
            new_header = patch_lines[index + 1]
            if not new_header.startswith("+++ "):
                raise ValueError("malformed unified diff header")
            new_path = new_header[4:].split("\t", 1)[0].rstrip("\r\n")
            new_path = new_path[2:] if new_path.startswith("b/") else new_path
            index += 2
            current: list[str] = []
            while index < len(patch_lines) and not patch_lines[index].startswith("--- "):
                current.append(patch_lines[index])
                index += 1
            if new_path == target_path:
                section = current
                break
            continue
        index += 1
    if section is None:
        raise ValueError(f"diff has no section for {target_path}")

    base_lines = base.splitlines(keepends=True)
    output: list[str] = []
    cursor = 0
    index = 0
    while index < len(section):
        header = section[index]
        match = HUNK.match(header)
        if not match:
            raise ValueError(f"expected hunk header for {target_path}")
        old_start = int(match.group(1))
        old_count = int(match.group(2) or 1)
        new_count = int(match.group(4) or 1)
        hunk_start = old_start - 1
        if hunk_start < cursor or hunk_start > len(base_lines):
            raise ValueError(f"invalid hunk offset for {target_path}")
        output.extend(base_lines[cursor:hunk_start])
        cursor = hunk_start
        old_seen = new_seen = 0
        index += 1
        while index < len(section) and not section[index].startswith("@@ "):
            line = section[index]
            if line.startswith("\\ No newline at end of file"):
                raise ValueError("no-newline unified diff markers are not supported")
            if not line or line[0] not in " +-":
                raise ValueError(f"invalid hunk line for {target_path}")
            operation, content = line[0], line[1:]
            if operation in " -":
                if cursor >= len(base_lines) or base_lines[cursor] != content:
                    raise ValueError(f"hunk does not match base snapshot for {target_path}")
                cursor += 1
                old_seen += 1
            if operation in " +":
                output.append(content)
                new_seen += 1
            index += 1
        if old_seen != old_count or new_seen != new_count:
            raise ValueError(f"hunk line count mismatch for {target_path}")
    output.extend(base_lines[cursor:])
    return "".join(output)


def _valid_span(span: object, changed_files: set[str]) -> bool:
    if not isinstance(span, dict) or span.get("file") not in changed_files:
        return False
    start, end = span.get("start_line"), span.get("end_line")
    return isinstance(start, int) and isinstance(end, int) and 1 <= start <= end


def validate_pair(pair: dict[str, Any], pair_root: str | Path) -> dict[str, Any]:
    """Return a deterministic static eligibility report without running code."""
    root = Path(pair_root).resolve()
    errors: list[str] = []
    checks: dict[str, bool] = {}

    checks["schema_version_supported"] = pair.get("schema_version") == 1
    checks["controlled_vocabulary"] = (
        pair.get("origin") in ORIGINS
        and pair.get("split") in SPLITS
        and pair.get("stratum") in STRATA
    )
    family_id = pair.get("family_id")
    checks["family_id_nonempty"] = isinstance(family_id, str) and bool(family_id.strip())

    target = pair.get("target")
    target = target if isinstance(target, dict) else {}
    changed_list = target.get("changed_files", [])
    changed_files = set(changed_list) if isinstance(changed_list, list) else set()
    checks["declared_changed_files_valid"] = (
        bool(changed_files)
        and len(changed_files) == len(changed_list)
        and all(isinstance(path, str) and path and not Path(path).is_absolute() and ".." not in Path(path).parts
                for path in changed_files)
    )
    checks["actionable_span_valid"] = _valid_span(target.get("actionable_span"), changed_files)

    companions = pair.get("companion_paths")
    companions = companions if isinstance(companions, list) else []
    checks["companion_paths_valid"] = (
        bool(companions)
        and len(set(companions)) == len(companions)
        and all(isinstance(path, str) and path and path not in changed_files
                and not Path(path).is_absolute() and ".." not in Path(path).parts for path in companions)
    )

    worlds = pair.get("worlds")
    worlds = worlds if isinstance(worlds, dict) else {}
    snapshot_paths: dict[tuple[str, str], Path] = {}
    tree_hashes: dict[tuple[str, str], dict[str, str]] = {}
    diff_text: dict[str, str] = {}
    diff_hashes: dict[str, str] = {}
    for variant in WORLD_NAMES:
        world = worlds.get(variant)
        if not isinstance(world, dict):
            errors.append(f"worlds.{variant} must be an object")
            continue
        expected = world.get("expected")
        if not isinstance(expected, dict) or set(expected) != set(REVISIONS):
            errors.append(f"worlds.{variant}.expected must contain base and head")
        for revision in REVISIONS:
            path = _resolve_inside(root, world.get(f"{revision}_snapshot"),
                                   f"worlds.{variant}.{revision}_snapshot", errors)
            if path is not None:
                if not path.is_dir():
                    errors.append(f"snapshot directory does not exist: {path}")
                else:
                    snapshot_paths[(variant, revision)] = path
                    tree_hashes[(variant, revision)] = _tree_files(path)
        path = _resolve_inside(root, world.get("diff"), f"worlds.{variant}.diff", errors)
        if path is not None:
            if not path.is_file():
                errors.append(f"diff file does not exist: {path}")
            else:
                diff_text[variant] = path.read_text(encoding="utf-8")
                diff_hashes[variant] = sha256_file(path)

    checks["all_artifacts_exist"] = len(snapshot_paths) == 4 and len(diff_text) == 2
    checks["same_nonempty_diff"] = (
        len(diff_text) == 2 and bool(diff_text.get("safe"))
        and diff_text.get("safe") == diff_text.get("unsafe")
    )
    checks["diff_paths_match_declared"] = (
        checks["same_nonempty_diff"] and _diff_paths(diff_text["safe"]) == changed_files
    )

    all_trees = len(tree_hashes) == 4
    checks["only_declared_target_changes_base_to_head"] = all_trees and all(
        _changed_paths(tree_hashes[(variant, "base")], tree_hashes[(variant, "head")]) == changed_files
        for variant in WORLD_NAMES
    )
    checks["target_snapshots_identical_across_worlds"] = all_trees and all(
        tree_hashes[("safe", revision)].get(path) == tree_hashes[("unsafe", revision)].get(path)
        and tree_hashes[("safe", revision)].get(path) is not None
        for revision in REVISIONS for path in changed_files
    )
    reconstructs = all_trees and checks["same_nonempty_diff"] and checks["diff_paths_match_declared"]
    if reconstructs:
        try:
            for variant in WORLD_NAMES:
                for path in changed_files:
                    base_path = snapshot_paths[(variant, "base")] / path
                    head_path = snapshot_paths[(variant, "head")] / path
                    if not base_path.is_file() or not head_path.is_file():
                        raise ValueError(f"target path missing from snapshot: {path}")
                    reconstructed = _apply_file_diff(
                        base_path.read_text(encoding="utf-8"), diff_text[variant], path,
                    )
                    if reconstructed != head_path.read_text(encoding="utf-8"):
                        raise ValueError(f"diff does not reconstruct head snapshot for {path}")
        except (OSError, UnicodeError, ValueError) as error:
            reconstructs = False
            errors.append(str(error))
    checks["diff_reconstructs_target_snapshots"] = reconstructs
    checks["companions_unchanged_within_world"] = all_trees and all(
        tree_hashes[(variant, "base")].get(path) == tree_hashes[(variant, "head")].get(path)
        and tree_hashes[(variant, "base")].get(path) is not None
        for variant in WORLD_NAMES for path in companions
    )
    checks["companion_differs_across_worlds"] = all_trees and any(
        tree_hashes[("safe", "head")].get(path) != tree_hashes[("unsafe", "head")].get(path)
        and tree_hashes[("safe", "head")].get(path) is not None
        and tree_hashes[("unsafe", "head")].get(path) is not None
        for path in companions
    )

    gold = pair.get("gold")
    gold = gold if isinstance(gold, dict) else {}
    nodes = gold.get("oracle_nodes")
    edges = gold.get("oracle_edges")
    nodes = nodes if isinstance(nodes, list) else []
    edges = edges if isinstance(edges, list) else []
    node_ids = [node.get("id") for node in nodes if isinstance(node, dict)]
    checks["oracle_nodes_unique"] = bool(node_ids) and len(node_ids) == len(nodes) == len(set(node_ids))
    checks["oracle_node_spans_valid"] = bool(nodes) and all(
        isinstance(node, dict)
        and isinstance(node.get("file"), str) and bool(node["file"])
        and not Path(node["file"]).is_absolute() and ".." not in Path(node["file"]).parts
        and isinstance(node.get("start_line"), int) and isinstance(node.get("end_line"), int)
        and 1 <= node["start_line"] <= node["end_line"]
        and ("symbols" not in node or isinstance(node["symbols"], list)
             and all(isinstance(symbol, str) and symbol for symbol in node["symbols"]))
        for node in nodes
    )
    bad_edges = [edge for edge in edges if not isinstance(edge, dict)
                 or edge.get("source") not in node_ids or edge.get("target") not in node_ids]
    checks["oracle_edges_resolve"] = bool(edges) and not bad_edges
    if bad_edges:
        errors.append("oracle edge references an unknown node")
    edge_keys = {
        (edge["source"], edge["target"], edge["type"])
        for edge in edges if isinstance(edge, dict)
        and all(key in edge for key in ("source", "target", "type"))
    }
    paths = gold.get("oracle_paths")
    paths = paths if isinstance(paths, list) else []
    bad_paths = []
    for path in paths:
        if not isinstance(path, dict):
            bad_paths.append(path)
            continue
        path_nodes, edge_types = path.get("nodes", []), path.get("edge_types", [])
        if (not isinstance(path_nodes, list) or not isinstance(edge_types, list)
                or len(path_nodes) < 2 or len(edge_types) != len(path_nodes) - 1
                or not set(path_nodes) <= set(node_ids)):
            bad_paths.append(path)
            continue
        path_edges = {
            (path_nodes[index], path_nodes[index + 1], edge_types[index])
            for index in range(len(edge_types))
        }
        if not path_edges <= edge_keys:
            bad_paths.append(path)
    checks["oracle_paths_resolve"] = bool(paths) and not bad_paths
    if bad_paths:
        errors.append("oracle path is not a typed path in the oracle graph")
    required = gold.get("required_evidence_nodes")
    checks["required_evidence_resolves"] = (
        isinstance(required, list) and bool(required) and set(required) <= set(node_ids)
    )
    checks["mechanism_nonempty"] = isinstance(gold.get("mechanism"), str) and bool(gold["mechanism"].strip())

    witness = pair.get("witness")
    witness = witness if isinstance(witness, dict) else {}
    argv = witness.get("argv")
    exit_codes = witness.get("exit_codes")
    timeout = witness.get("timeout_seconds")
    checks["witness_spec_valid"] = (
        isinstance(argv, list) and bool(argv) and all(isinstance(arg, str) and arg for arg in argv)
        and "{snapshot}" in argv
        and isinstance(timeout, (int, float)) and 0 < timeout <= 300
        and isinstance(exit_codes, dict) and exit_codes.get("pass") == 0
        and isinstance(exit_codes.get("target_failure"), int)
        and exit_codes.get("target_failure") != 0
    )
    expected_statuses = {
        worlds.get(variant, {}).get("expected", {}).get(revision)
        for variant in WORLD_NAMES for revision in REVISIONS
        if isinstance(worlds.get(variant), dict)
    }
    checks["expected_statuses_declared"] = (
        isinstance(exit_codes, dict) and None not in expected_statuses
        and bool(expected_statuses) and expected_statuses <= set(exit_codes)
    )

    for name, passed in checks.items():
        if not passed and name not in {"oracle_edges_resolve"}:
            errors.append(f"check failed: {name}")
    return {
        "family_id": family_id,
        "eligible": not errors and all(checks.values()),
        "checks": checks,
        "errors": errors,
        "audit": {
            "pair_record_sha256": sha256_bytes(json.dumps(
                pair, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            ).encode("utf-8")),
            "diff_sha256": diff_hashes,
            "snapshot_file_sha256": {
                f"{variant}_{revision}": tree_hashes.get((variant, revision), {})
                for variant in WORLD_NAMES for revision in REVISIONS
            },
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate one context-necessity pair")
    parser.add_argument("pair")
    parser.add_argument("--output")
    args = parser.parse_args()
    path = Path(args.pair).resolve()
    report = validate_pair(load_pair(path), path.parent)
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("x", encoding="utf-8") as stream:
            stream.write(rendered)
    else:
        print(rendered, end="")
    raise SystemExit(0 if report["eligible"] else 1)


if __name__ == "__main__":
    main()
