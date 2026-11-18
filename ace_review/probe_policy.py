"""Registry-only probe selection; no model-generated command is accepted."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from .decidability import DecidabilityDecision


@dataclass(frozen=True)
class ProbeTemplate:
    kind: str
    template_id: str
    argv: tuple[str, ...]
    timeout_seconds: float = 30

    def __post_init__(self) -> None:
        if not self.kind or not self.template_id or not self.argv:
            raise ValueError("probe kind, template_id and argv are required")
        if not 0 < self.timeout_seconds <= 300:
            raise ValueError("probe timeout must be in (0, 300]")


@dataclass(frozen=True)
class ProbeRegistry:
    templates: tuple[ProbeTemplate, ...]

    def __post_init__(self) -> None:
        kinds = [template.kind for template in self.templates]
        if len(kinds) != len(set(kinds)):
            raise ValueError("only one fixed template per probe kind is allowed")

    @property
    def kinds(self) -> set[str]:
        return {template.kind for template in self.templates}

    @classmethod
    def from_records(cls, records: Iterable[dict[str, Any]]) -> "ProbeRegistry":
        return cls(tuple(ProbeTemplate(
            kind=record["kind"], template_id=record["template_id"],
            argv=tuple(record["argv"]), timeout_seconds=record.get("timeout_seconds", 30),
        ) for record in records))


def select_probes(
    decision: DecidabilityDecision, registry: ProbeRegistry, *, policy: str,
) -> tuple[ProbeTemplate, ...]:
    if policy not in {"never", "always", "adaptive"}:
        raise ValueError(f"unknown probe policy: {policy}")
    if policy == "never":
        return ()
    if policy == "always":
        return registry.templates
    if decision.status != "runtime_required":
        return ()
    requested = set(decision.requested_probe_kinds)
    return tuple(template for template in registry.templates if template.kind in requested)
