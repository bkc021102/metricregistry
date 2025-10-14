"""Executable metric definitions, and the monitor that keeps them honest.

Finance reports 14,200 enrolled students. The registrar reports 13,850.
Marketing reports 14,600. All three are correct - they use different census
dates and different treatments of deferrals - and the meeting spends forty
minutes arguing about the number instead of making the decision the number was
for.

A metrics dictionary written in English does not fix this. It drifts from the
SQL the day after it is written and has no mechanism to notice. So here a
definition IS its SQL: it executes, it produces a value, and a scheduled
monitor recomputes every definition and alerts when two metrics that claim to
measure the same thing disagree beyond tolerance.

The point is not to declare one of them correct. It is to make the
disagreement visible, attributable and dated.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field


@dataclass(frozen=True)
class MetricDefinition:
    name: str
    version: int
    #: What this metric is about. Two definitions with the same subject are
    #: expected to agree; that expectation is what the monitor checks.
    subject: str
    grain: str
    owner: str
    description: str
    sql: str
    #: How far two same-subject metrics may differ before it is an incident.
    tolerance: float = 0.005

    @property
    def key(self) -> str:
        return f"{self.name}@v{self.version}"


@dataclass
class Registry:
    definitions: dict[str, list[MetricDefinition]] = field(default_factory=dict)

    def register(self, definition: MetricDefinition) -> None:
        versions = self.definitions.setdefault(definition.name, [])
        if any(v.version == definition.version for v in versions):
            raise ValueError(
                f"{definition.name} v{definition.version} already registered; "
                "publish a new version rather than editing one in place")
        versions.append(definition)
        versions.sort(key=lambda d: d.version)

    def current(self, name: str) -> MetricDefinition:
        return self.definitions[name][-1]

    def at_version(self, name: str, version: int) -> MetricDefinition:
        for d in self.definitions[name]:
            if d.version == version:
                return d
        raise KeyError(f"{name} v{version}")

    def names(self) -> list[str]:
        return sorted(self.definitions)

    def by_subject(self, subject: str) -> list[MetricDefinition]:
        return sorted(
            (self.current(n) for n in self.names()
             if self.current(n).subject == subject),
            key=lambda d: d.name,
        )

    def subjects(self) -> list[str]:
        return sorted({self.current(n).subject for n in self.names()})


@dataclass(frozen=True)
class MetricValue:
    definition: MetricDefinition
    value: float

    @property
    def label(self) -> str:
        return f"{self.definition.name} (v{self.definition.version})"


def evaluate(conn: sqlite3.Connection, definition: MetricDefinition) -> MetricValue:
    """A definition that cannot execute is not a definition."""
    row = conn.execute(definition.sql).fetchone()
    if row is None or row[0] is None:
        return MetricValue(definition, 0.0)
    return MetricValue(definition, float(row[0]))


# ---------------------------------------------------------------------------
# The divergence monitor
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Divergence:
    subject: str
    a: MetricValue
    b: MetricValue

    @property
    def absolute(self) -> float:
        return abs(self.a.value - self.b.value)

    @property
    def relative(self) -> float:
        base = max(abs(self.a.value), abs(self.b.value), 1e-9)
        return self.absolute / base

    @property
    def tolerance(self) -> float:
        return min(self.a.definition.tolerance, self.b.definition.tolerance)

    @property
    def breached(self) -> bool:
        return self.relative > self.tolerance

    def describe(self) -> str:
        return (
            f"{self.subject}: {self.a.label} = {self.a.value:,.0f} vs "
            f"{self.b.label} = {self.b.value:,.0f} "
            f"({self.relative:.2%} apart, tolerance {self.tolerance:.2%})"
        )


def check_divergence(
    conn: sqlite3.Connection, registry: Registry,
) -> list[Divergence]:
    """Compare every pair of metrics that claim the same subject."""
    out: list[Divergence] = []
    for subject in registry.subjects():
        definitions = registry.by_subject(subject)
        values = [evaluate(conn, d) for d in definitions]
        for i in range(len(values)):
            for j in range(i + 1, len(values)):
                out.append(Divergence(subject, values[i], values[j]))
    return sorted(out, key=lambda d: -d.relative)


def breaches(conn: sqlite3.Connection, registry: Registry) -> list[Divergence]:
    return [d for d in check_divergence(conn, registry) if d.breached]


# ---------------------------------------------------------------------------
# Definition-change log
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DefinitionChange:
    name: str
    from_version: int
    to_version: int
    old_value: float
    new_value: float

    @property
    def moved_by(self) -> float:
        return self.new_value - self.old_value


def definition_changes(
    conn: sqlite3.Connection, registry: Registry,
) -> list[DefinitionChange]:
    """How much of a metric's movement came from the DEFINITION changing.

    Without this, a number that moved because somebody amended its SQL looks
    exactly like a number that moved because the world changed - and the first
    one is not a business event.
    """
    out: list[DefinitionChange] = []
    for name in registry.names():
        versions = registry.definitions[name]
        for old, new in zip(versions, versions[1:]):
            out.append(DefinitionChange(
                name, old.version, new.version,
                evaluate(conn, old).value, evaluate(conn, new).value,
            ))
    return out


def lineage_of(definition: MetricDefinition) -> set[str]:
    """Source columns a definition reads. Deliberately conservative."""
    import re
    columns: set[str] = set()
    for m in re.finditer(r"\b([a-z_]+)\.([a-z_]+)\b", definition.sql):
        columns.add(f"{m.group(1)}.{m.group(2)}")
    for m in re.finditer(r"\b(?:where|and|or)\s+([a-z_]+)\s*(?:=|>|<|is)",
                         definition.sql, re.I):
        columns.add(m.group(1))
    return columns
