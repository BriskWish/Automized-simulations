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

from willy.charge_scaling import validate_ion_charge_scale


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
        "ion_compensation", "non_neutral_confirmed", "execution", "error", "warnings",
        "ion_charge_scale",
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
        if "ion_charge_scale" in payload:
            try:
                validate_ion_charge_scale(payload["ion_charge_scale"])
            except ValueError as exc:
                issues.append(str(exc))
        for field in self.mapping_fields:
            if field in payload and not isinstance(payload[field], Mapping):
                issues.append(f"{field} 必须是对象")
        for field in self.nullable_mapping_fields:
            if field in payload and payload[field] is not None and not isinstance(payload[field], Mapping):
                issues.append(f"{field} 必须是对象")

        backend = payload.get("backend")
        if backend is not None and (not isinstance(backend, str) or not backend.strip()):
            issues.append("backend 必须是非空字符串")
        elif isinstance(backend, str) and backend.strip().lower() not in {"g16", "g09", "orca"}:
            issues.append("backend 必须为 g16、g09 或 orca")
        confirmed = payload.get("non_neutral_confirmed")
        if confirmed is not None and not isinstance(confirmed, bool):
            issues.append("non_neutral_confirmed 必须是布尔值")
        issues.extend(_execution_shape_issues(payload.get("execution")))

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


def _execution_shape_issues(payload: object) -> list[str]:
    """Reject private connection details from the frozen public workflow config."""
    if payload is None:
        return []
    if not isinstance(payload, Mapping):
        return ["execution 必须是对象"]
    issues: list[str] = []
    unexpected_execution = sorted(set(payload) - {"md", "stop_after_stage"})
    if unexpected_execution:
        issues.append("execution 仅允许 md、stop_after_stage 字段")
    stop_after_stage = payload.get("stop_after_stage")
    if stop_after_stage is not None and stop_after_stage != "eq":
        issues.append("execution.stop_after_stage 仅允许 eq 或 null")
    if "md" not in payload:
        return issues
    md = payload["md"]
    if not isinstance(md, Mapping):
        return [*issues, "execution.md 必须是对象"]
    unexpected_md = sorted(set(md) - {"backend", "profile", "retain_remote_run"})
    if unexpected_md:
        issues.append("execution.md 包含不允许的字段")
    backend = md.get("backend")
    if backend is not None and not isinstance(backend, str):
        issues.append("execution.md.backend 必须是字符串")
    profile = md.get("profile")
    if profile is not None and (not isinstance(profile, str) or not profile.strip()):
        issues.append("execution.md.profile 必须是非空字符串或 null")
    retain = md.get("retain_remote_run")
    if retain is not None and not isinstance(retain, bool):
        issues.append("execution.md.retain_remote_run 必须是布尔值")
    return issues
