"""Convert preserved paired-SCIP bundles into the common evidence graph.

SCIP edges retain their original conservative meaning: a reference/use edge is
not relabeled as a proven call or data-flow edge.
"""
from __future__ import annotations

import hashlib
from pathlib import PurePosixPath
from typing import Any, Iterable

from .evidence_graph import EvidenceEdge, EvidenceGraph, EvidenceNode


RELATION_CONFIDENCE = {
    "uses_definition": 0.55,
    "referenced_by": 0.55,
    "isImplementation": 0.8,
    "inverse_isImplementation": 0.8,
    "isTypeDefinition": 0.8,
    "inverse_isTypeDefinition": 0.8,
}


def _node_id(symbol: str) -> str:
    return "SCIP-NODE-" + hashlib.sha256(symbol.encode("utf-8")).hexdigest()[:16]


def _safe_path(value: object) -> str:
    path = str(value or "")
    pure = PurePosixPath(path)
    if pure.is_absolute() or ".." in pure.parts:
        raise ValueError(f"unsafe SCIP source path: {path}")
    return path


def _source_for_revision(source: dict[str, Any], revision: str) -> bool:
    metadata = source.get("metadata", {})
    spans = metadata.get("source_spans", [])
    return metadata.get("version") == revision or any(span.get("version") == revision for span in spans)


def _role(symbol: str, anchors: set[str], incoming_relation: str = "") -> str:
    if symbol in anchors:
        return "diff_seed"
    if "Implementation" in incoming_relation:
        return "implementation"
    if incoming_relation == "referenced_by":
        return "caller"
    return "semantic_endpoint"


def adapt_scip_bundles(
    bundles: Iterable[dict[str, Any]],
    source_records: Iterable[dict[str, Any]],
    *,
    revision: str,
) -> EvidenceGraph:
    if revision not in {"base", "head"}:
        raise ValueError("SCIP revision must be base or head")
    bundles = list(bundles)
    sources = list(source_records)
    source_by_symbol: dict[str, list[dict[str, Any]]] = {}
    for source in sources:
        _safe_path(source.get("file"))
        owner = source.get("metadata", {}).get("owner_symbol")
        if owner and _source_for_revision(source, revision):
            source_by_symbol.setdefault(owner, []).append(source)

    anchors = {bundle.get("anchor") for bundle in bundles if bundle.get("anchor")}
    symbol_facts: dict[str, dict[str, Any]] = {
        anchor: {"role": "diff_seed", "file": "", "line": None, "relation": ""}
        for anchor in anchors
    }
    raw_edges: list[dict[str, Any]] = []
    for bundle in bundles:
        for edge in bundle.get("edges", []):
            if edge.get("version") != revision:
                continue
            source, target = edge.get("from"), edge.get("to")
            relation = edge.get("relation")
            if not source or not target or not relation:
                raise ValueError("SCIP edge needs from, to, relation, and version")
            file = _safe_path(edge.get("file"))
            symbol_facts.setdefault(source, {"role": _role(source, anchors), "file": file,
                                              "line": edge.get("line"), "relation": relation})
            symbol_facts.setdefault(target, {"role": _role(target, anchors, relation), "file": file,
                                              "line": edge.get("line"), "relation": relation})
            raw_edges.append(edge)

    ordered_symbols = sorted(symbol_facts, key=lambda symbol: (symbol not in anchors, symbol))
    nodes = []
    attached = 0
    for symbol in ordered_symbols:
        fact = symbol_facts[symbol]
        candidates = sorted(source_by_symbol.get(symbol, []), key=lambda item: (
            item.get("token_cost", 0), item.get("file", ""), item.get("start_line", 0),
        ))
        source = candidates[0] if candidates else None
        if source:
            attached += 1
        role = fact["role"] if source or symbol in anchors else "transform"
        nodes.append(EvidenceNode(
            id=_node_id(symbol), kind="symbol", role=role,
            file=_safe_path(source.get("file")) if source else fact["file"],
            start_line=source.get("start_line") if source else fact["line"],
            end_line=source.get("end_line") if source else fact["line"],
            revision=revision, symbol=symbol,
            content=source.get("content", "") if source else "",
            token_cost=int(source.get("token_cost", 0)) if source else 0,
            changed=symbol in anchors,
            confidence=1.0 if source else 0.55,
            attributes={
                "provider": "scip",
                "source_record_id": source.get("id") if source else None,
                "role_semantics": "heuristic_endpoint_not_proven_producer",
            },
        ))

    edges_by_key: dict[tuple[str, str, str], EvidenceEdge] = {}
    for edge in raw_edges:
        relation = edge["relation"]
        converted = EvidenceEdge(
            source=_node_id(edge["from"]), target=_node_id(edge["to"]),
            type="scip_" + relation, revision=revision,
            confidence=RELATION_CONFIDENCE.get(relation, 0.5), resolved=True,
            attributes={
                "provider": "scip",
                "semantics": "static_use_not_proven_call",
                "witness_file": _safe_path(edge.get("file")),
                "witness_line": edge.get("line"),
            },
        )
        edges_by_key[converted.key] = converted
    seed_ids = [_node_id(symbol) for symbol in sorted(anchors)]
    return EvidenceGraph(
        nodes=nodes,
        edges=tuple(edges_by_key[key] for key in sorted(edges_by_key)),
        metadata={
            "provider": "scip_bundle_adapter_v1",
            "revision": revision,
            "input_bundles": len(bundles),
            "input_source_records": len(sources),
            "filtered_edges": len(raw_edges),
            "unique_edges": len(edges_by_key),
            "nodes_with_source": attached,
            "seed_ids": seed_ids,
            "edge_semantics": "static_use_not_proven_call_or_data_flow",
        },
    )
