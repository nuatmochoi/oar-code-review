"""Gold-blind, bounded evidence retrieval over paired SCIP snapshots.

SCIP reference edges are *uses*, not a sound call/data-flow graph. Selection is
a deterministic coverage heuristic, not a proof of globally minimal evidence.
"""
from __future__ import annotations

import hashlib
import heapq
import re
from collections import defaultdict, deque
from dataclasses import replace
from pathlib import PurePosixPath

from .schema import ContextBlock, estimate_tokens


def canonical_symbol(symbol: str, module: str, path: str, version: str) -> str:
    if symbol.startswith("local "):
        return f"{version}:{path}:{symbol}"
    # SCIP escapes spaces in package fields by doubling them.
    parts = symbol.replace("  ", "\0").split(" ", 4)
    if len(parts) == 5 and parts[2].replace("\0", " ") == module:
        parts[3] = "<project>"
    return " ".join(parts).replace("\0", "  ")


def _diff_maps(diff: str) -> tuple[dict, dict]:
    changes = {"base": defaultdict(set), "head": defaultdict(set)}
    visible = {"base": defaultdict(set), "head": defaultdict(set)}
    old = new = ""
    a = b = 0
    in_hunk = False
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            in_hunk = False
        elif not in_hunk and line.startswith("--- "):
            old = line[4:].removeprefix("a/").split("\t")[0]
        elif not in_hunk and line.startswith("+++ "):
            new = line[4:].removeprefix("b/").split("\t")[0]
        elif line.startswith("@@"):
            match = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
            if match:
                a, b = map(int, match.groups())
                in_hunk = True
        elif in_hunk and line.startswith("-"):
            if old != "/dev/null":
                changes["base"][old].add(a); visible["base"][old].add(a)
            a += 1
        elif in_hunk and line.startswith("+"):
            if new != "/dev/null":
                changes["head"][new].add(b); visible["head"][new].add(b)
            b += 1
        elif in_hunk and line.startswith(" "):
            visible["base"][old].add(a); visible["head"][new].add(b)
            a += 1; b += 1
    return ({v: dict(x) for v, x in changes.items()},
            {v: dict(x) for v, x in visible.items()})


def changed_ranges(diff: str) -> dict:
    return _diff_maps(diff)[0]


def _span(item: dict, enclosing: bool = False) -> tuple[int, int] | None:
    suffix = "EnclosingRange" if enclosing else "Range"
    single = item.get("singleLine" + suffix)
    multi = item.get("multiLine" + suffix)
    if single is not None:
        return single.get("line", 0) + 1, single.get("line", 0) + 1
    if multi is not None:
        start = multi.get("startLine", 0) + 1
        end = multi.get("endLine", 0) + int(multi.get("endCharacter", 0) > 0)
        return start, max(start, end)
    values = item.get("enclosingRange" if enclosing else "range", [])
    if len(values) == 3:
        return values[0] + 1, values[0] + 1
    if len(values) == 4:
        return values[0] + 1, max(values[0] + 1, values[2] + int(values[3] > 0))
    return None


class SemanticIndex:
    def __init__(self, raw: dict, module: str, version: str):
        self.version, self.module = version, module
        self.texts: dict[str, list[str]] = {}
        self.by_file = defaultdict(list)
        self.defs = defaultdict(list)
        self.refs = defaultdict(list)
        self.outgoing = defaultdict(list)
        self.info: dict[str, dict] = {}
        self.relations = defaultdict(list)
        self.roles = defaultdict(int)
        seen = set()
        for document in raw.get("documents", []):
            path = document["relativePath"]
            document_module=document.get("indexModule",module)
            if PurePosixPath(path).is_absolute() or ".." in PurePosixPath(path).parts:
                raise ValueError(f"Unsafe SCIP path: {path}")
            self.texts[path] = document.get("text", "").splitlines()
            for info in document.get("symbols", []):
                key = canonical_symbol(info["symbol"], document_module, path, version)
                self.info[key] = info
                for relation in info.get("relationships", []):
                    other = canonical_symbol(relation["symbol"], document_module, path, version)
                    for name in ("isImplementation", "isTypeDefinition", "isDefinition", "isReference"):
                        if relation.get(name):
                            self.relations[key].append((name, other))
                            self.relations[other].append(("inverse_" + name, key))
            for occurrence in document.get("occurrences", []):
                symbol = occurrence.get("symbol", "")
                span = _span(occurrence)
                if not symbol or span is None:
                    continue
                role = occurrence.get("symbolRoles", 0)
                key = canonical_symbol(symbol, document_module, path, version)
                identity = (path, span, key, role)
                if identity in seen:
                    continue
                seen.add(identity)
                node = {"file": path, "start_line": span[0], "end_line": span[1],
                        "enclosing": _span(occurrence, True), "symbol": key,
                        "raw_symbol": symbol, "role": role, "owner": ""}
                self.by_file[path].append(node)
                (self.defs if role & 1 else self.refs)[key].append(node)
                for mask, name in ((1,"definition"),(2,"import"),(4,"write"),(8,"read"),(16,"generated"),(32,"test")):
                    self.roles[name] += int(bool(role & mask))
        for path, occurrences in self.by_file.items():
            # Sweep intervals instead of scanning all definitions for every
            # occurrence (large generated/checker files have >100K uses).
            definitions=sorted((o["enclosing"][0],i,o) for i,o in enumerate(occurrences)
                               if o["role"] & 1 and o["enclosing"])
            active=[];position=0
            for occurrence in sorted(occurrences,key=lambda n:n["start_line"]):
                line=occurrence["start_line"]
                while position < len(definitions) and definitions[position][0] <= line:
                    lo,order,definition=definitions[position];hi=definition["enclosing"][1]
                    heapq.heappush(active,(hi-lo,order,hi,definition["symbol"]))
                    position+=1
                while active and active[0][2] < line:
                    heapq.heappop(active)
                if active:
                    occurrence["owner"]=active[0][3]
            for occurrence in occurrences:
                if not occurrence["role"] & 1 and occurrence["owner"]:
                    self.outgoing[occurrence["owner"]].append(occurrence)

    def anchors(self, changed: dict[str, set[int]]) -> tuple[set[str], set[tuple[str, int]]]:
        keys, covered = set(), set()
        for path, lines in changed.items():
            for node in self.by_file.get(path, []):
                lo, hi = node["start_line"], node["end_line"]
                direct = lines.intersection(range(lo, hi + 1))
                enclosed = set()
                if node["role"] & 1 and node["enclosing"]:
                    lo, hi = node["enclosing"]
                    enclosed = lines.intersection(range(lo, hi + 1))
                if direct or enclosed:
                    # Local identifiers cannot be aligned between snapshots. Anchor
                    # their enclosing project definition instead. Package imports
                    # and ubiquitous external APIs are not impact roots.
                    eligible = {key for key in (node["symbol"], node["owner"])
                                if key in self.defs and " <project> " in key
                                and not key.endswith("/")}
                    if eligible:
                        keys.update(eligible)
                        covered.update((path, line) for line in direct | enclosed)
        return keys, covered

    def body(self, key: str) -> str:
        return "\n".join("\n".join(self.texts[n["file"]][lo - 1:hi])
                         for n in self.defs.get(key, [])
                         for lo, hi in [n["enclosing"] or (n["start_line"], n["end_line"])])


def _delta(base: SemanticIndex, head: SemanticIndex, symbol: str) -> list[str]:
    b, h = base.defs.get(symbol), head.defs.get(symbol)
    changes = []
    if b and not h:
        changes.append("removed_definition")
    if h and not b:
        changes.append("added_definition")
    if b and h:
        if base.body(symbol) != head.body(symbol):
            changes.append("body_changed")
        if {n["file"] for n in b} != {n["file"] for n in h}:
            changes.append("definition_moved")
        for field, label in (("signatureDocumentation","signature_changed"),("documentation","documentation_changed")):
            if base.info.get(symbol, {}).get(field) != head.info.get(symbol, {}).get(field):
                changes.append(label)
    # Counts are facts, not a cross-version occurrence alignment claim.
    if len(base.refs.get(symbol, [])) != len(head.refs.get(symbol, [])):
        changes.append("reference_count_changed")
    def dependencies(index):
        return {n["symbol"] for n in index.outgoing.get(symbol, [])
                if not n["raw_symbol"].startswith("local ") and not n["symbol"].endswith("/")}
    if dependencies(base) != dependencies(head):
        changes.append("dependency_set_changed")
    if {n["owner"] for n in base.refs.get(symbol, []) if n["owner"]} != {
            n["owner"] for n in head.refs.get(symbol, []) if n["owner"]}:
        changes.append("reference_owner_set_changed")
    if set(base.relations.get(symbol, [])) != set(head.relations.get(symbol, [])):
        changes.append("typed_relationship_changed")
    return changes


def _slice(index: SemanticIndex, node: dict, max_lines: int = 60) -> tuple[str, int, int]:
    lines = index.texts.get(node["file"], [])
    lo, hi = node["enclosing"] or (max(1,node["start_line"]-5),node["end_line"]+8)
    if not node["enclosing"] and node["owner"]:
        for definition in index.defs.get(node["owner"], []):
            if definition["file"] == node["file"] and definition["enclosing"]:
                lo, hi = definition["enclosing"]
                break
    if hi - lo + 1 > max_lines:
        lo = max(lo, node["start_line"] - max_lines // 3)
        hi = lo + max_lines - 1
    hi = min(hi, len(lines))
    return "\n".join(f"{i}: {lines[i-1]}" for i in range(lo,hi+1)), lo, hi


def _walk_endpoints(index: SemanticIndex, seed: str, max_hops: int):
    """One lazy BFS stream; each seed/revision gets a turn before it expands."""
    queue = deque([(seed, [], [])]); visited = set(); emitted = set()
    while queue:
        symbol, path, witness = queue.popleft()
        if symbol in visited:
            continue
        visited.add(symbol)
        endpoints = [(n, path or ["definition"],witness) for n in index.defs.get(symbol, [])]
        if len(path) < max_hops:
            for n in index.refs.get(symbol, []):
                p = path + ["referenced_by"]
                edge={'from':symbol,'to':n['owner'] or n['symbol'],'relation':'referenced_by',
                      'version':index.version,'file':n['file'],'line':n['start_line'],'owner':n['owner']}
                extended=witness+[edge]
                endpoints.append((n,p,extended))
                if n["owner"] and n["owner"] != symbol:
                    queue.append((n["owner"],p,extended))
            for n in index.outgoing.get(symbol, []):
                p = path + ["uses_definition"]
                edge={'from':symbol,'to':n['symbol'],'relation':'uses_definition',
                      'version':index.version,'file':n['file'],'line':n['start_line'],'owner':n['owner']}
                endpoints.extend((d,p,witness+[edge]) for d in index.defs.get(n["symbol"], [])
                                 if not d["raw_symbol"].startswith("local ")
                                 and not d["symbol"].endswith("/"))
            for relation, target in sorted(index.relations.get(symbol, [])):
                p = path + [relation]
                edge={'from':symbol,'to':target,'relation':relation,'version':index.version}
                endpoints.extend((d,p,witness+[edge]) for d in index.defs.get(target, []))
        for node, evidence_path, edge_witness in endpoints:
            identity=(node["file"],node["start_line"],node["end_line"],tuple(evidence_path))
            if identity not in emitted:
                emitted.add(identity)
                yield node,evidence_path,edge_witness


def build_candidates(base: SemanticIndex, head: SemanticIndex, diff: str,
                     use_delta: bool = True, max_hops: int = 2,
                     max_candidates: int = 4000) -> tuple[list[ContextBlock], dict]:
    changes, visible = _diff_maps(diff)
    b_seeds, b_covered = base.anchors(changes["base"])
    h_seeds, h_covered = head.anchors(changes["head"])
    seeds = sorted(b_seeds | h_seeds if use_delta else h_seeds)
    indices = (head,base) if use_delta else (head,)
    if max_candidates < 1 or max_hops < 0:
        raise ValueError("Positive candidate cap and nonnegative hop limit required")
    deltas={s:_delta(base,head,s) if use_delta else [] for s in seeds}
    streams=deque((seed,index,_walk_endpoints(index,seed,max_hops))
                  for seed in seeds for index in indices)
    unique={};visited_streams=set();attempts=0;attempt_cap=max_candidates*50
    while streams and len(unique) < max_candidates and attempts < attempt_cap:
        seed,index,stream=streams.popleft()
        visited_streams.add((seed,index.version))
        try:
            node,evidence_path,edge_witness=next(stream)
        except StopIteration:
            continue
        streams.append((seed,index,stream));attempts+=1
        content,lo,hi=_slice(index,node)
        if not content:
            continue
        identity=(index.version,node["file"],lo,hi)
        delta=deltas[seed]
        outside=set(range(lo,hi+1))-visible[index.version].get(node["file"],set())
        test=node["file"].endswith("_test.go")
        cross=node["file"] not in changes[index.version]
        score=1+2*bool(delta)+2*cross+int(test)+bool(outside)-.5*len(evidence_path)
        prior=unique.get(identity)
        anchors=sorted(set(prior.metadata["anchors"] if prior else []) | {seed})
        if prior and prior.metadata["score"] >= score:
            unique[identity]=replace(prior,metadata=prior.metadata | {"anchors":anchors})
            continue
        packet=(f"Version: {index.version}\nAnchor symbol: {seed}\n"
                f"Index delta: {', '.join(delta) if use_delta else 'not supplied (head-only control)'}\n"
                f"Evidence path: {' -> '.join(evidence_path)}\n"
                f"Endpoint: {node['symbol']} at {node['file']}:{node['start_line']}\n"
                f"Selection facts: outside-diff lines={len(outside)}; test-file={test}\n"
                "These are structural facts, not proof of a defect or a feasible data-flow path.\n"
                f"Source ({index.version}):\n{content}")
        short=hashlib.sha256(repr(identity).encode()).hexdigest()[:12]
        unique[identity]=ContextBlock(
            id=f"SCIP-{short}",kind="scip_delta" if use_delta else "scip_head",
            content=packet,file=node["file"],start_line=lo,end_line=hi,
            query=seed,relation=" -> ".join(evidence_path),confidence=1.0,token_cost=estimate_tokens(packet),
            metadata={"version":index.version,"anchor":seed,"anchors":anchors,"path":evidence_path,
                      "edge_witness":edge_witness,"endpoint_symbol":node['symbol'],
                      "owner_symbol":node['owner'] or node['symbol'],
                      "target_line":node["start_line"],"delta":delta,"score":score,
                      "outside_diff_lines":len(outside),"is_test_file":test,
                      "provider":"scip-go","edge_semantics":"static_use_not_proven_call"})
    blocks=list(unique.values())
    total = sum(len(lines) for paths in changes.values() for f,lines in paths.items() if f.endswith(".go"))
    mapped = sum(f.endswith(".go") for f,_ in b_covered) + sum(f.endswith(".go") for f,_ in h_covered)
    return blocks, {"base_anchors":len(b_seeds),"head_anchors":len(h_seeds),"seeds":len(seeds),
                    "changed_go_lines":total,"mapped_changed_go_lines":mapped,
                    "unindexed_changed_go_files":{
                        index.version:sorted(f for f in changes[index.version]
                                             if f.endswith(".go") and f not in index.texts)
                        for index in (base,head)},
                    "anchor_line_coverage":mapped/total if total else None,
                    "candidate_count":len(blocks),"candidate_cap_reached":bool(streams),
                    "endpoint_attempts":attempts,"endpoint_attempt_cap":attempt_cap,
                    "seed_revision_streams_visited":len(visited_streams),
                    "seed_revision_streams_total":len(seeds)*len(indices),
                    "max_hops":max_hops,"base_roles":dict(base.roles),"head_roles":dict(head.roles),
                    "delta_anchor_count":sum(bool(v) for v in deltas.values()) if use_delta else None}


def select_evidence(blocks: list[ContextBlock], budget: int, ranked: bool = True) -> list[ContextBlock]:
    if budget < 0:
        raise ValueError("Negative budget")
    pool = sorted(blocks,key=lambda b:(b.file,b.start_line or 0,b.id))
    selected, used, anchors, relations, spans = [], 0, set(), set(), set()
    while pool:
        feasible = [b for b in pool if used+b.token_cost <= budget]
        if not feasible: break
        def utility(b):
            m=b.metadata
            lines={(m["version"],b.file,i) for i in range(b.start_line or 1,(b.end_line or 0)+1)}
            novelty=len(lines-spans)/max(1,len(lines))
            diversity=2*bool(set(m.get("anchors",[m["anchor"]]))-anchors)+int(b.relation not in relations)
            return ((m["score"]*novelty+diversity)/(max(1,b.token_cost)**.5),b.id)
        best=max(feasible,key=utility) if ranked else feasible[0]
        pool.remove(best)
        m=best.metadata
        lines={(m["version"],best.file,i) for i in range(best.start_line or 1,(best.end_line or 0)+1)}
        if lines and lines <= spans:
            continue
        selected.append(best); used+=best.token_cost
        anchors.update(m.get("anchors",[m["anchor"]]));relations.add(best.relation);spans.update(lines)
    return selected


def endpoint_metrics(blocks: list[ContextBlock], evidence: list[dict]) -> dict:
    hits=[]
    for item in evidence:
        lo=item["start_line"];hi=item.get("end_line",lo)
        covered={line for b in blocks if b.file == item["file"] and b.metadata.get("version") == item["version"]
                 for line in range(max(lo,b.start_line or 0),min(hi,b.end_line or 0)+1)}
        hits.append(set(range(lo,hi+1)) <= covered)
    return {"endpoints":len(hits),"covered_endpoints":sum(hits),
            "endpoint_recall":sum(hits)/len(hits) if hits else None,
            "all_endpoints_covered":all(hits) if hits else None}
