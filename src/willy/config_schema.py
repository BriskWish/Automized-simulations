"""Conservative structural contract for the workflow-level ``config.json``.

The MD protocol is versioned and scientifically validated in
``simulation.protocol``.  This module owns only the outer document shape, so
all configuration entry points can reject malformed nested sections before
defaults or execution code dereferences them.  Unknown fields remain allowed
for forward compatibility and legacy migration remains an explicit operation.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


WORKFLOW_CONFIG_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class ConfigSchemaValidation:
    """Structured outer-schema result without changing the legacy list API."""

    schema_version: int
    issues: tuple[str, ...]
    unknown_top_level_fields: tuple[str, ...]

    @property
    def valid(self) -> bool:
        return not self.issues


@dataclass(frozen=True)
class WorkflowConfigSchema:
    """Validate stable workflow sections while allowing future extensions."""

    schema_version: int = WORKFLOW_CONFIG_SCHEMA_VERSION
    known_top_level_fields: frozenset[str] = frozenset({
        "backend", "defaults", "molecules", "residues", "md", "topology", "box",
        "ion_compensation", "non_neutral_confirmed", "error", "warnings",
    })
    mapping_fields: frozenset[str] = frozenset({
        "defaults", "molecules", "residues", "md", "topology", "ion_compensation",
    })
    nullable_mapping_fields: frozenset[str] = frozenset({"box"})

    def validate(self, payload: object) -> ConfigSchemaValidation:
        if not isinstance(payload, Mapping):
            return ConfigSchemaValidation(
                self.schema_version,
                ("config 必须是对象",),
                (),
            )

        issues: list[str] = []
        for field in self.mapping_fields:
            if field in payload and not isinstance(payload[field], Mapping):
                issues.append(f"{field} 必须是对象")
        for field in self.nullable_mapping_fields:
            if field in payload and payload[field] is not None and not isinstance(payload[field], Mapping):
                issues.append(f"{field} 必须是对象")

        backend = payload.get("backend")
        if backend is not None and (not isinstance(backend, str) or not backend.strip()):
            issues.append("backend 必须是非空字符串")
        confirmed = payload.get("non_neutral_confirmed")
        if confirmed is not None and not isinstance(confirmed, bool):
            issues.append("non_neutral_confirmed 必须是布尔值")

        molecules = payload.get("molecules")
        if isinstance(molecules, Mapping):
            for name, molecule in molecules.items():
                if not isinstance(name, str) or not name:
                    issues.append("molecules 的名称必须是非空字符串")
                if not isinstance(molecule, Mapping):
                    issues.append(f"molecules.{name} 必须是对象")

        residues = payload.get("residues")
        if isinstance(residues, Mapping):
            for name in residues:
                if not isinstance(name, str) or not name:
                    issues.append("residues 的名称必须是非空字符串")

        return ConfigSchemaValidation(
            self.schema_version,
            tuple(issues),
            tuple(sorted(str(field) for field in set(payload) - self.known_top_level_fields)),
        )


WORKFLOW_CONFIG_SCHEMA = WorkflowConfigSchema()


def validate_config_schema(payload: object) -> ConfigSchemaValidation:
    """Validate only the workflow document shape, never mutate or migrate it."""
    return WORKFLOW_CONFIG_SCHEMA.validate(payload)
