from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path

from .diff import ranked_added_line_numbers
from .schema import ContextBlock, ReviewTask


def _safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", value)


def _run(command: list[str], cwd: Path | None = None, timeout: int = 1800) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, text=True, capture_output=True, timeout=timeout, check=False)


def _source_predicate(task: ReviewTask, max_source_lines: int) -> tuple[str, dict]:
    clauses = []
    selected, total = ranked_added_line_numbers(task.diff, limit=max_source_lines)
    for file, lines in sorted(selected.items()):
        if not lines:
            continue
        line_clause = " or ".join(f"source.getLocation().getStartLine() = {line}" for line in sorted(lines))
        clauses.append(
            "(source.getLocation().getFile().getRelativePath() = "
            + json.dumps(file)
            + f" and ({line_clause}))"
        )
    return " or\n    ".join(clauses) or "none()", {
        "candidate_source_lines": total,
        "selected_source_lines": sum(len(lines) for lines in selected.values()),
        "max_source_lines": max_source_lines,
    }


def _query_text(task: ReviewTask, max_source_lines: int) -> tuple[str, dict]:
    predicate, source_stats = _source_predicate(task, max_source_lines)
    return f'''/**
 * @name Changed-line evidence flow
 * @description Tracks values originating on changed lines to review-relevant sinks.
 * @kind path-problem
 * @problem.severity recommendation
 * @id ace-review/changed-line-flow
 * @precision medium
 */

import go
import semmle.go.dataflow.DataFlow

module Config implements DataFlow::ConfigSig {{
  predicate isSource(DataFlow::Node source) {{
    {predicate}
  }}

  predicate isSink(DataFlow::Node sink) {{
    sink instanceof DataFlow::TypeCastNode
    or exists(DataFlow::CallNode call | sink = call.getAnArgument())
    or sink = DataFlow::exprNode(any(ReturnStmt ret).getAnExpr())
  }}
}}

module Flow = DataFlow::Global<Config>;
import Flow::PathGraph

from Flow::PathNode source, Flow::PathNode sink
where Flow::flowPath(source, sink) and source != sink
select sink, source, sink,
  "A value from changed code flows from $@ to the review-relevant sink $@.", source,
  source.getNode().toString(), sink, sink.getNode().toString()
''', source_stats


def _compact_path(locations: list[dict], max_nodes: int = 8) -> list[dict]:
    nodes = []
    seen = set()
    for item in locations:
        location = item.get("location", {})
        physical = location.get("physicalLocation", {})
        artifact = physical.get("artifactLocation", {})
        region = physical.get("region", {})
        node = {
            "file": artifact.get("uri", ""),
            "line": region.get("startLine"),
            "message": location.get("message", {}).get("text", "value"),
        }
        key = (node["file"], node["line"], node["message"])
        if key not in seen:
            nodes.append(node); seen.add(key)
    if len(nodes) > max_nodes:
        nodes = nodes[:4] + nodes[-4:]
    return nodes


def _parse_sarif(path: Path, max_paths: int = 5) -> list[list[dict]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    paths: list[list[dict]] = []
    for run in data.get("runs", []):
        for result in run.get("results", []):
            for code_flow in result.get("codeFlows", []):
                for thread_flow in code_flow.get("threadFlows", []):
                    compact = _compact_path(thread_flow.get("locations", []))
                    if compact and compact not in paths:
                        paths.append(compact)
                    if len(paths) >= max_paths:
                        return paths
    return paths


def collect_codeql(
    task: ReviewTask,
    repo_path: str | Path,
    executable: str | Path,
    search_path: str | Path,
    cache_root: str | Path,
    timeout_seconds: int = 1800,
    max_source_lines: int = 64,
) -> tuple[list[ContextBlock], dict]:
    started = time.monotonic()
    root = Path(repo_path).resolve()
    executable = str(Path(executable).resolve())
    search_path = str(Path(search_path).resolve())
    cache = Path(cache_root).resolve()
    database = cache / "databases" / f"{_safe(task.repo)}-{task.head_commit[:12]}"
    query_dir = cache / "queries" / _safe(task.id)
    query_dir.mkdir(parents=True, exist_ok=True)
    (query_dir / "qlpack.yml").write_text(
        "name: ace-review/generated-go-evidence\nversion: 0.0.1\n"
        "libraryPathDependencies:\n  - codeql/go-all\n", encoding="utf-8",
    )
    query = query_dir / "ChangedLineFlow.ql"
    query_text, source_stats = _query_text(task, max_source_lines)
    query.write_text(query_text, encoding="utf-8")
    sarif = query_dir / "result.sarif"

    if not (database / "codeql-database.yml").exists():
        database.parent.mkdir(parents=True, exist_ok=True)
        created = _run([
            executable, "database", "create", str(database), "--language=go",
            f"--source-root={root}", "--build-mode=autobuild",
        ], timeout=timeout_seconds)
        if created.returncode:
            return [], {
                "available": False, "provider": "codeql", "stage": "database_create",
                "stderr": created.stderr[-4000:], "duration_seconds": time.monotonic() - started,
                **source_stats,
            }

    analyzed = _run([
        executable, "database", "analyze", str(database), str(query),
        f"--search-path={search_path}", "--format=sarif-latest", f"--output={sarif}", "--rerun",
    ], timeout=timeout_seconds)
    if analyzed.returncode:
        return [], {
            "available": False, "provider": "codeql", "stage": "query",
            "stderr": analyzed.stderr[-4000:], "duration_seconds": time.monotonic() - started,
            **source_stats,
        }
    paths = _parse_sarif(sarif)
    if not paths:
        block = ContextBlock(
            id="DF-EMPTY", kind="dataflow",
            content="CodeQL found no path from the added lines to a selected call, return, or type-conversion sink.",
            relation="no_selected_path", confidence=1.0,
            metadata={"provider": "codeql", "path_sensitive": True, "query": "changed-line-flow"},
        )
        return [block], {"available": True, "provider": "codeql", "paths": 0, "duration_seconds": time.monotonic() - started, **source_stats}

    blocks = []
    for index, nodes in enumerate(paths, 1):
        content = "CodeQL path:\n" + "\n".join(
            f"{position}. {node['file']}:{node['line']} — {node['message']}"
            for position, node in enumerate(nodes, 1)
        )
        blocks.append(ContextBlock(
            id=f"DF-{index:02d}", kind="dataflow", content=content,
            file=nodes[0]["file"], start_line=nodes[0]["line"],
            end_line=nodes[-1]["line"], relation="codeql_global_flow_path", confidence=1.0,
            metadata={"provider": "codeql", "path_sensitive": True, "nodes": nodes},
        ))
    return blocks, {"available": True, "provider": "codeql", "paths": len(blocks), "duration_seconds": time.monotonic() - started, **source_stats}
