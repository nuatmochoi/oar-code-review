"""Pre-registered rule policy for static/runtime/unresolved evidence."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from .evidence_graph import EvidenceGraph


STATIC_OPERATIONS = {
    "literal", "constant", "assignment", "field_assignment", "lower", "upper",
    "casefold", "prefix", "suffix", "path_normalize",
}
DYNAMIC_EDGE_TYPES = {"dynamic_dispatch", "reflection", "environment_provider", "opaque_external", "race_order"}


@dataclass(frozen=True)
class DecidabilityDecision:
    status: str
    reasons: tuple[str, ...]
    requested_probe_kinds: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "reasons": list(self.reasons),
                "requested_probe_kinds": list(self.requested_probe_kinds)}


def classify_decidability(
    graph: EvidenceGraph, *, available_probe_kinds: Iterable[str] = (),
) -> DecidabilityDecision:
    available = set(available_probe_kinds)
    uncertainties: list[str] = []
    requested: set[str] = set()
    for node in graph.nodes:
        if node.attributes.get("dynamic"):
            uncertainties.append(f"dynamic node: {node.id}")
            if node.attributes.get("probe_kind"):
                requested.add(str(node.attributes["probe_kind"]))
    for edge in graph.edges:
        operation = edge.attributes.get("operation")
        uncertain = not edge.resolved or edge.type in DYNAMIC_EDGE_TYPES
        if operation is not None and operation not in STATIC_OPERATIONS:
            uncertain = True
        if uncertain:
            uncertainties.append(f"uncertain edge: {edge.source}->{edge.target}:{edge.type}")
            if edge.attributes.get("probe_kind"):
                requested.add(str(edge.attributes["probe_kind"]))
    if not uncertainties:
        return DecidabilityDecision("static_decidable", ("all selected contract edges resolved by static policy",))
    if requested and requested <= available:
        return DecidabilityDecision(
            "runtime_required", tuple(uncertainties), tuple(sorted(requested)),
        )
    missing = sorted(requested - available)
    reasons = uncertainties + ([f"no registered probe for: {', '.join(missing)}"] if missing else ["no fixed probe requested"])
    return DecidabilityDecision("unresolved", tuple(reasons))
