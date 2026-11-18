"""Development control with an enforced serialized source budget."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .bm25 import BM25Index, Document
from .disambiguation_audit import _relation_subgraph, _validated_candidate
from .evidence_packets import digest, render, source_closure
from .minimal_subgraph import _candidate_paths
from .obligation import token_count


def select_sources(candidate, snapshot, changed_files, arm, budget, query):
    available = sorted({n.file for n in candidate.nodes} - changed_files)

    def content(files):
        ids = [n.id for n in candidate.nodes if n.file in files]
        return source_closure(candidate.subgraph(ids, ()), snapshot)

    if arm == "bm25":
        documents = [Document(f, 1, len((snapshot / f).read_text().splitlines()),
                              (snapshot / f).read_text()) for f in available]
        bundles = [(doc.file,) for _, doc in BM25Index(documents).search(query, top_k=len(documents))]
    elif arm in {"call_source", "flow_source"}:
        graph = _relation_subgraph(candidate, "call") if arm == "call_source" else candidate
        seeds = candidate.metadata["seed_ids" if arm == "call_source" else "flow_seed_ids"]
        paths, truncated = _candidate_paths(graph, set(seeds), 6, 10000)
        if truncated:
            raise ValueError("truncated path enumeration")
        ranked = []
        for ids, edges in paths:
            files = tuple(sorted({candidate.by_id[i].file for i in ids} - changed_files))
            if files:
                ranked.append((len(edges), token_count(content(files)), files))
        bundles = [files for _, _, files in sorted(set(ranked))]
    elif arm == "diff_only":
        bundles = []
    else:
        raise ValueError("unknown arm")
    selected = set()
    decisions = []
    for bundle in bundles:
        proposed = selected | set(bundle)
        tokens = token_count(content(proposed))
        accepted = tokens <= budget
        decisions.append({"files": bundle, "resulting_source_tokens": tokens, "accepted": accepted})
        if accepted:
            selected = proposed
    return content(selected), {"selected_files": sorted(selected), "decisions": decisions}


def prepare(pair_path, contract_path, output, budget=128):
    if budget < 0:
        raise ValueError("negative budget")
    path = Path(pair_path).resolve()
    contract = Path(contract_path).read_text()
    rows = []
    for variant in ("safe", "unsafe"):
        pair, static, candidate = _validated_candidate(path, variant)
        world = pair["worlds"][variant]
        diff = (path.parent / world["diff"]).read_text()
        for arm in ("diff_only", "call_source", "flow_source", "bm25"):
            source, audit = select_sources(candidate, path.parent / world["head_snapshot"],
                                           set(pair["target"]["changed_files"]), arm, budget,
                                           diff + "\n" + contract)
            prompt = render(diff, contract, source, [])
            rows.append({"variant": variant, "arm": arm, "prompt": prompt,
                         "source_sha256": digest(source), "diff_sha256": digest(diff),
                         "source_tokens": token_count(source), "selection": audit,
                         "unmatched_prompt_tokens": token_count(prompt),
                         "pair_sha256": static["audit"]["pair_record_sha256"]})
    target = max(row["unmatched_prompt_tokens"] for row in rows) + 16
    for row in rows:
        for count in range(target + 1):
            padded = row["prompt"] + " ." * count + "\nEND LENGTH CONTROL.\n"
            if token_count(padded) == target:
                row.update(prompt=padded, prompt_sha256=digest(padded), matched_prompt_tokens=target)
                break
        else:
            raise ValueError("token matching failed")
    report = {"scope": "new synthetic fanout development control; author constructed",
              "source_budget": budget, "tokenizer": "o200k_base reference",
              "ranking": "graph: path distance, serialized bundle cost, filenames; BM25: diff plus contract",
              "limits": "seed and graph jointly changed; no paired-index treatment; lexical tie sensitivity",
              "contract_sha256": digest(contract), "reviewer_calls": 0, "records": rows}
    with Path(output).open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pair")
    parser.add_argument("contract")
    parser.add_argument("output")
    parser.add_argument("--budget", type=int, default=128)
    args = parser.parse_args()
    result = prepare(args.pair, args.contract, args.output, args.budget)
    print(json.dumps([{k: r[k] for k in ("variant", "arm", "source_tokens", "selection")}
                      for r in result["records"]], indent=2))
