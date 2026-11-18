"""Source-verified common-pool BM25/SCIP audit on all frozen development PRs."""
from __future__ import annotations

import argparse
from dataclasses import replace
import gzip
import hashlib
import json
from pathlib import Path
import re
import subprocess

from .bm25 import BM25Index, Document
from .diff import identifiers
from .evidence_graph import EvidenceGraph
from .minimal_subgraph import _candidate_paths
from .obligation import token_count
from .scip_adapter import adapt_scip_bundles


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def visible_head_lines(diff):
    visible, file, line, in_hunk = {}, "", 0, False
    for text in diff.splitlines():
        if text.startswith("diff --git "):
            in_hunk = False
        elif text.startswith("+++ b/"):
            file = text[6:]
            visible.setdefault(file, set())
            in_hunk = False
        elif text.startswith("@@"):
            match = re.search(r"\+(\d+)", text)
            if match:
                line, in_hunk = int(match[1]), True
        elif in_hunk and text.startswith((" ", "+")):
            visible[file].add(line)
            line += 1
    return visible


def numbered_code(content):
    result = []
    for line in content.splitlines():
        match = re.match(r"^(\d+): (.*)$", line)
        if match:
            result.append((int(match[1]), match[2]))
    return result


def verify_lines(numbered, actual):
    return bool(numbered) and all(1 <= n <= len(actual) and actual[n-1] == text
                                 for n, text in numbered)


def normalize(graph, task, repos):
    visible = visible_head_lines(task["diff"])
    cache, chunks, mapping, audit, nodes = {}, {}, {}, [], []
    repo = repos / task["repo"].replace("/", "__")
    for node in graph.nodes:
        if not node.content:
            nodes.append(node)
            continue
        numbered = numbered_code(node.content)
        if node.file not in cache:
            result = subprocess.run(
                ["git", "show", f'{task["head_commit"]}:{node.file}'], cwd=repo,
                text=True, capture_output=True, timeout=30, check=False)
            cache[node.file] = result.stdout.splitlines() if result.returncode == 0 else None
        actual = cache[node.file]
        valid = actual is not None and verify_lines(numbered, actual)
        covered = valid and all(n in visible.get(node.file, set()) for n, _ in numbered)
        audit.append({"node_id": node.id, "file": node.file, "verified": valid,
                      "fully_diff_visible": covered})
        if valid and not covered:
            text = f"HEAD {node.file}\n" + "\n".join(f"{n}: {code}" for n, code in numbered)
            key = sha(text.encode())[:24]
            chunks[key] = {"id": key, "file": node.file, "start_line": numbered[0][0],
                           "end_line": numbered[-1][0], "text": text,
                           "code": "\n".join(code for _, code in numbered)}
            mapping[node.id] = key
            nodes.append(replace(node, content=text, token_cost=token_count(text)))
        else:
            nodes.append(replace(node, content="", token_cost=0))
    return EvidenceGraph(nodes, graph.edges, graph.metadata), chunks, mapping, audit


def pack(ids, chunks):
    return "\n\n".join(chunks[i]["text"] for i in sorted(ids))


def select(bundles, chunks, budget):
    selected = set()
    for bundle in bundles:
        proposed = selected | set(bundle)
        if token_count(pack(proposed, chunks)) <= budget:
            selected = proposed
    text = pack(selected, chunks)
    return {"ids": sorted(selected), "source_tokens": token_count(text),
            "source_sha256": sha(text.encode())}


def audit_task(task, root, repos):
    task_id = task["id"]
    bundle_file = root / "bundles" / f"{task_id}.json.gz"
    status = json.loads((root / "tasks" / f"{task_id}.json").read_text())
    if not bundle_file.exists():
        return {"task_id": task_id, "status": "unavailable", "reason": "no preserved bundle"}
    candidate_file = root / "candidates" / bundle_file.name
    with gzip.open(bundle_file, "rt") as stream:
        bundles = json.load(stream)
    with gzip.open(candidate_file, "rt") as stream:
        candidates = json.load(stream)
    graph = adapt_scip_bundles(bundles, candidates["bundle_sources"], revision="head")
    graph, chunks, mapping, verification = normalize(graph, task, repos)
    paths, truncated = _candidate_paths(graph, set(graph.metadata["seed_ids"]), 3, 10000)
    ranked = []
    for ids, edges in paths:
        keys = tuple(sorted({mapping[i] for i in ids if i in mapping}))
        if keys:
            ranked.append((len(edges), token_count(pack(keys, chunks)), keys))
    graph_bundles = [keys for _, _, keys in sorted(set(ranked))]
    docs = [Document(key, 1, 1, chunks[key]["code"]) for key in sorted(chunks)]
    query = " ".join(identifiers(task["diff"])) + " " + task["title"] + " " + task["description"]
    bm_bundles = [(d.file,) for _, d in BM25Index(docs).search(query, top_k=len(docs))]
    rows = []
    for budget in (512, 1024, 2048, 4096):
        bm = select(bm_bundles, chunks, budget)
        semantic = select(graph_bundles, chunks, budget)
        left, right = set(bm["ids"]), set(semantic["ids"])
        rows.append({"budget": budget, "bm25": bm, "semantic": semantic,
                     "jaccard": len(left & right) / len(left | right) if left | right else None,
                     "bm25_only": sorted(left-right), "semantic_only": sorted(right-left)})
    invalid = sum(not r["verified"] for r in verification)
    return {"task_id": task_id, "repo": task["repo"], "status": "ok",
            "head_commit": task["head_commit"], "base_commit": task["base_commit"],
            "scope_complete": status["scope_complete"], "path_truncated": truncated,
            "source_invalid_count": invalid, "source_verified_count": len(verification)-invalid,
            "fully_diff_visible_count": sum(r["fully_diff_visible"] for r in verification),
            "review_ready": False, "review_blocker": "no independently verified required-context annotations",
            "structurally_eligible": status["scope_complete"] and not truncated and invalid == 0,
            "bundle_sha256": sha(bundle_file.read_bytes()),
            "candidates_sha256": sha(candidate_file.read_bytes()),
            "diff_sha256": sha(task["diff"].encode()),
            "query": query, "verification": verification,
            "chunks": chunks, "selections": rows}


def run(manifest, root, repos, output):
    data = Path(manifest).read_bytes()
    tasks = sorted((json.loads(line) for line in data.splitlines()), key=lambda t: t["id"])
    records = []
    for task in tasks:
        try:
            # Deliberate allowlist: selectors do not receive benchmark gold.
            safe = {key: task[key] for key in (
                "id", "repo", "head_commit", "base_commit", "diff", "title", "description")}
            record = audit_task(safe, Path(root), Path(repos))
        except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as error:
            record = {"task_id": task["id"], "status": "error", "reason": str(error)}
        records.append(record)
        print(json.dumps({"task_id": task["id"], "status": record["status"]}), flush=True)
    report = {"scope": "previously observed 17-PR development cohort; common semantic source pool",
              "manifest_sha256": sha(data), "reviewer_calls": 0,
              "tasks": len(records), "records": records}
    with Path(output).open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest")
    parser.add_argument("index_root")
    parser.add_argument("repo_root")
    parser.add_argument("output")
    args = parser.parse_args()
    run(args.manifest, args.index_root, args.repo_root, args.output)
