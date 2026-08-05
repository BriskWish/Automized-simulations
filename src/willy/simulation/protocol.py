"""Versioned MD protocol configuration and explicit legacy migration.

The simulation layer deliberately refuses to infer the old ``eq_ns`` and
``prod_ns`` fields at execution time.  A caller must first use the explicit
migration API, which writes both the v2 configuration and a durable mapping
record.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable
import json

from willy.config_store import write_json


MD_SCHEMA_VERSION = 2
LEGACY_MD_FIELDS = {"eq_ns", "prod_ns"}
EQ_SEGMENT_NAMES = (
    "heat",
    "hold_high",
    "cool_transition",
    "hold_transition",
    "cool_target",
    "hold_target",
)
DEFAULT_EQ_SEGMENTS_NS = {
    "heat": 2.0,
    "hold_high": 1.0,
    "cool_transition": 2.0,
    "hold_transition": 1.0,
    "cool_target": 2.0,
    "hold_target": 2.0,
}
DEFAULT_EQ_ACCEPTANCE = {
    "window_ns": 1.0,
    "temperature_abs_tolerance_k": 5.0,
    "max_relative_drift": 0.10,
    "max_trend_zscore": 2.0,
}

class MDConfigError(ValueError):
    """Raised when an MD protocol violates the v2 configuration contract."""


@dataclass(frozen=True)
class ProtocolValidation:
    """Validation result shared by configuration, MDP, and stage execution."""

    issues: tuple[str, ...]
    warnings: tuple[str, ...]

    @property
    def valid(self) -> bool:
        return not self.issues


def default_md_config() -> dict[str, Any]:
    """Return a complete v2 MD protocol without a run-specific seed."""
    return {
        "schema_version": MD_SCHEMA_VERSION,
        "dt": 0.001,
        "ref_p": 1.01325,
        "tcoupl": "V-rescale",
        "tau_t": 0.5,
        "pcoupl": "C-rescale",
        "pcoupltype": "isotropic",
        "compressibility": "8.5e-5",
        "constraints": "hbonds",
        "rcoulomb": 1.0,
        "rvdw": 1.0,
        "coulombtype": "PME",
        "vdwtype": "Cut-off",
        "dispersion_correction": "EnerPres",
        "nsteps": 10000,
        "emtol": 100.0,
        "emstep": 0.01,
        "lincs_iter": 1,
        "lincs_order": 4,
        "max_box_rollbacks": 1,
        "outputs": {"trr": False},
        "system_type": "uniform_liquid",
        "eq": {
            "high_temperature": 500.0,
            "transition_temperature": 400.0,
            "target_temperature": 298.0,
            "segments_ns": deepcopy(DEFAULT_EQ_SEGMENTS_NS),
            "tau_p": 1.0,
            "acceptance": deepcopy(DEFAULT_EQ_ACCEPTANCE),
        },
        "prod": {
            "duration_ns": 10.0,
            "temperature": 298.0,
            "tau_p": 2.0,
        },
    }


def contains_legacy_md_fields(md: object) -> bool:
    return isinstance(md, dict) and bool(LEGACY_MD_FIELDS.intersection(md))


def merge_v2_defaults(md: object) -> dict[str, Any]:
    """Merge a partial v2 MD section without reinterpreting legacy fields."""
    if md is None:
        md = {}
    if not isinstance(md, dict):
        raise MDConfigError("md 必须是对象")
    if contains_legacy_md_fields(md):
        raise MDConfigError("检测到旧字段 eq_ns/prod_ns；请先执行显式配置迁移")
    version = md.get("schema_version")
    if version not in (None, MD_SCHEMA_VERSION):
        raise MDConfigError(
            f"不支持 md.schema_version={version!r}；请使用显式迁移工具"
        )

    base = default_md_config()
    for key, value in md.items():
        if key in {"eq", "prod", "outputs"}:
            continue
        base[key] = deepcopy(value)
    for section in ("eq", "prod", "outputs"):
        supplied = md.get(section, {})
        if supplied is None:
            supplied = {}
        if not isinstance(supplied, dict):
            raise MDConfigError(f"md.{section} 必须是对象")
        base[section].update(deepcopy(supplied))
    base["eq"].setdefault("segments_ns", deepcopy(DEFAULT_EQ_SEGMENTS_NS))
    if not isinstance(base["eq"]["segments_ns"], dict):
        raise MDConfigError("md.eq.segments_ns 必须是对象")
    complete_segments = deepcopy(DEFAULT_EQ_SEGMENTS_NS)
    complete_segments.update(base["eq"]["segments_ns"])
    base["eq"]["segments_ns"] = complete_segments
    acceptance = base["eq"].get("acceptance", {})
    if not isinstance(acceptance, dict):
        raise MDConfigError("md.eq.acceptance 必须是对象")
    complete_acceptance = deepcopy(DEFAULT_EQ_ACCEPTANCE)
    complete_acceptance.update(acceptance)
    base["eq"]["acceptance"] = complete_acceptance
    base["schema_version"] = MD_SCHEMA_VERSION
    return base


def validate_md_config(md: object) -> ProtocolValidation:
    """Validate the v2 scientific protocol and return errors plus warnings."""
    issues: list[str] = []
    warnings: list[str] = []
    if not isinstance(md, dict):
        return ProtocolValidation(("md 必须是对象",), ())
    if contains_legacy_md_fields(md):
        return ProtocolValidation(
            ("旧字段 eq_ns/prod_ns 不可直接执行；请先使用显式迁移",), ()
        )
    if md.get("schema_version") != MD_SCHEMA_VERSION:
        return ProtocolValidation(
            (f"md.schema_version 必须为 {MD_SCHEMA_VERSION}",), ()
        )
    try:
        complete = merge_v2_defaults(md)
    except MDConfigError as exc:
        return ProtocolValidation((str(exc),), ())

    _in_range(issues, "md.dt", complete["dt"], 0.0001, 0.005, positive=True)
    _in_range(issues, "md.ref_p", complete["ref_p"], 0.000001, 10000, positive=True)
    _in_range(issues, "md.tau_t", complete["tau_t"], 0.0001, 20, positive=True)
    _in_range(issues, "md.rcoulomb", complete["rcoulomb"], 0.1, 3.0, positive=True)
    _in_range(issues, "md.rvdw", complete["rvdw"], 0.1, 3.0, positive=True)
    _in_range(issues, "md.nsteps", complete["nsteps"], 1, 100000000, integer=True, positive=True)
    _in_range(issues, "md.emtol", complete["emtol"], 0.000001, 1000000, positive=True)
    _in_range(issues, "md.emstep", complete["emstep"], 0.000001, 0.1, positive=True)
    _in_range(issues, "md.lincs_iter", complete["lincs_iter"], 1, 10, integer=True, positive=True)
    _in_range(issues, "md.lincs_order", complete["lincs_order"], 1, 12, integer=True, positive=True)

    if complete["tcoupl"] not in {"V-rescale", "Nose-Hoover", "Berendsen", "Andersen"}:
        issues.append(f"md.tcoupl={complete['tcoupl']!r} 不是支持的热浴算法")
    if complete["pcoupl"] not in {"C-rescale", "Berendsen", "Parrinello-Rahman"}:
        issues.append(f"md.pcoupl={complete['pcoupl']!r} 不是支持的压浴算法")
    pcoupltype = complete["pcoupltype"]
    if pcoupltype not in {"isotropic", "semiisotropic", "anisotropic", "surface-tension"}:
        issues.append(f"md.pcoupltype={pcoupltype!r} 无效")
    if complete.get("system_type") in {"interface", "crystal", "uniaxial"} and pcoupltype == "isotropic":
        issues.append(
            f"{complete['system_type']} 体系不能使用各向同性压力耦合；请选择相应 pcoupltype"
        )

    eq = complete["eq"]
    prod = complete["prod"]
    for key in ("high_temperature", "transition_temperature", "target_temperature"):
        _in_range(issues, f"md.eq.{key}", eq[key], 1, 2000, positive=True)
    try:
        high = float(eq["high_temperature"])
        transition = float(eq["transition_temperature"])
        target = float(eq["target_temperature"])
        if not high > transition > target:
            issues.append(
                "三点退火温度必须满足 high_temperature > transition_temperature > target_temperature"
            )
    except (TypeError, ValueError, KeyError):
        # Individual numeric validation above provides the more specific error.
        pass
    _in_range(issues, "md.eq.tau_p", eq["tau_p"], 0.0001, 20, positive=True)
    segments = eq["segments_ns"]
    if set(segments) != set(EQ_SEGMENT_NAMES):
        issues.append("md.eq.segments_ns 必须且只能包含六段具名时长")
        total_eq_ns = 0.0
    else:
        total_eq_ns = 0.0
        for name in EQ_SEGMENT_NAMES:
            value = segments[name]
            _in_range(issues, f"md.eq.segments_ns.{name}", value, 0.000001, 100, positive=True)
            try:
                total_eq_ns += float(value)
            except (TypeError, ValueError):
                pass
        if total_eq_ns < 7 - 1e-9 or total_eq_ns > 100 + 1e-9:
            issues.append(f"EQ 六段总时长 {total_eq_ns:g} ns 必须在 7-100 ns")
    acceptance = eq["acceptance"]
    _in_range(issues, "md.eq.acceptance.window_ns", acceptance["window_ns"], 0.000001, 100, positive=True)
    _in_range(
        issues,
        "md.eq.acceptance.temperature_abs_tolerance_k",
        acceptance["temperature_abs_tolerance_k"], 0.001, 100, positive=True,
    )
    _in_range(issues, "md.eq.acceptance.max_relative_drift", acceptance["max_relative_drift"], 0.000001, 1, positive=True)
    _in_range(issues, "md.eq.acceptance.max_trend_zscore", acceptance["max_trend_zscore"], 0.001, 10, positive=True)
    try:
        if float(segments.get("hold_target", 0)) < float(acceptance["window_ns"]):
            warnings.append(
                "最终 298 K 保温段短于 EQ 验收窗口；将禁止自动判定为已平衡"
            )
    except (TypeError, ValueError):
        pass

    _in_range(issues, "md.prod.duration_ns", prod.get("duration_ns"), 2, 200, positive=True)
    _in_range(issues, "md.prod.temperature", prod.get("temperature"), 1, 2000, positive=True)
    _in_range(issues, "md.prod.tau_p", prod.get("tau_p"), 0.0001, 20, positive=True)
    try:
        if abs(float(prod["temperature"]) - float(eq["target_temperature"])) > 1e-6:
            issues.append("PROD 温度必须与 EQ 目标温度完全一致")
    except (TypeError, ValueError, KeyError):
        pass
    return ProtocolValidation(tuple(issues), tuple(warnings))


def require_valid_md_config(md: object) -> tuple[dict[str, Any], tuple[str, ...]]:
    """Return normalized v2 config or raise a human-readable contract error."""
    validation = validate_md_config(md)
    if validation.issues:
        raise MDConfigError("; ".join(validation.issues))
    return merge_v2_defaults(md), validation.warnings


def eq_total_ns(md: dict[str, Any]) -> float:
    return sum(float(md["eq"]["segments_ns"][name]) for name in EQ_SEGMENT_NAMES)


def ns_to_nsteps(duration_ns: float, dt_ps: float) -> tuple[int, float]:
    """Round ns->steps once and expose the exact duration represented by MDP."""
    steps = int(round(float(duration_ns) * 1000.0 / float(dt_ps)))
    if steps <= 0:
        raise MDConfigError("时长换算后的 nsteps 必须为正")
    actual_ns = steps * float(dt_ps) / 1000.0
    return steps, actual_ns


def eq_annealing_points(md: dict[str, Any]) -> tuple[list[float], list[float], dict[str, float]]:
    """Return cumulative GROMACS ps points, temperatures, and actual durations."""
    eq = md["eq"]
    dt = float(md["dt"])
    cumulative_ps = [0.0]
    actual_segments: dict[str, float] = {}
    for name in EQ_SEGMENT_NAMES:
        steps, actual_ns = ns_to_nsteps(float(eq["segments_ns"][name]), dt)
        actual_segments[name] = actual_ns
        cumulative_ps.append(cumulative_ps[-1] + steps * dt)
    temps = [
        float(eq["target_temperature"]),
        float(eq["high_temperature"]),
        float(eq["high_temperature"]),
        float(eq["transition_temperature"]),
        float(eq["transition_temperature"]),
        float(eq["target_temperature"]),
        float(eq["target_temperature"]),
    ]
    return cumulative_ps, temps, actual_segments


def canonical_json_fingerprint(payload: object) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode()).hexdigest()


def migrate_config_dict(config: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Convert one legacy config only when explicitly requested by a caller."""
    if not isinstance(config, dict):
        raise MDConfigError("config 必须是对象")
    source = deepcopy(config)
    source_md = source.get("md", {})
    if not isinstance(source_md, dict):
        raise MDConfigError("md 必须是对象")
    if source_md.get("schema_version") == MD_SCHEMA_VERSION and not contains_legacy_md_fields(source_md):
        return source, {"status": "already_v2", "mapping": {}}

    legacy_eq_ns = source_md.get("eq_ns", 10.0)
    legacy_prod_ns = source_md.get("prod_ns", 10.0)
    legacy_ref_t = source_md.get("ref_t", 298.0)
    try:
        scale = float(legacy_eq_ns) / sum(DEFAULT_EQ_SEGMENTS_NS.values())
    except (TypeError, ValueError):
        scale = 1.0
    segments = {
        name: round(value * scale, 9)
        for name, value in DEFAULT_EQ_SEGMENTS_NS.items()
    }
    migrated = deepcopy(source)
    common = {
        key: deepcopy(value)
        for key, value in source_md.items()
        if key not in LEGACY_MD_FIELDS | {"ref_t", "schema_version", "eq", "prod"}
    }
    md = default_md_config()
    md.update(common)
    md["eq"].update({
        "target_temperature": legacy_ref_t,
        "segments_ns": segments,
    })
    md["prod"].update({
        "duration_ns": legacy_prod_ns,
        "temperature": legacy_ref_t,
    })
    migrated["md"] = md

    box = migrated.setdefault("box", {})
    density_mapping = None
    if isinstance(box, dict) and "density" in box:
        density_mapping = box.pop("density")
        box["packing_number_density_nm3"] = density_mapping

    mapping = {
        "status": "migrated",
        "from_schema_version": source_md.get("schema_version", 1),
        "to_schema_version": MD_SCHEMA_VERSION,
        "mapping": {
            "md.eq_ns": {
                "value": legacy_eq_ns,
                "target": "md.eq.segments_ns",
                "strategy": "按 [2,1,2,1,2,2] 比例拆分",
                "result": segments,
            },
            "md.prod_ns": {
                "value": legacy_prod_ns,
                "target": "md.prod.duration_ns",
            },
            "md.ref_t": {
                "value": legacy_ref_t,
                "target": ["md.eq.target_temperature", "md.prod.temperature"],
            },
        },
        "validation": {
            "issues": list(validate_md_config(md).issues),
            "warnings": list(validate_md_config(md).warnings),
        },
    }
    if density_mapping is not None:
        mapping["mapping"]["box.density"] = {
            "value": density_mapping,
            "target": "box.packing_number_density_nm3",
        }
    return migrated, mapping


def migrate_config_file(
    source_path: str | Path,
    output_path: str | Path | None = None,
    mapping_path: str | Path | None = None,
) -> tuple[Path, Path, dict[str, Any]]:
    """Explicitly write a v2 config and immutable mapping sidecar file."""
    source = Path(source_path)
    data = json.loads(source.read_text())
    migrated, mapping = migrate_config_dict(data)
    target = Path(output_path) if output_path is not None else source.with_name(f"{source.stem}.v2.json")
    audit = Path(mapping_path) if mapping_path is not None else target.with_suffix(".migration.json")
    write_json(target, migrated)
    write_json(audit, mapping)
    return target, audit, mapping


def adopt_migrated_config_file(
    source_path: str | Path,
    *,
    backup_path: str | Path | None = None,
    mapping_path: str | Path | None = None,
    validate_config_fn: Callable[[dict[str, Any]], list[str]] | None = None,
) -> tuple[Path, Path, Path, dict[str, Any]]:
    """Migrate a legacy config and atomically make v2 the active config.

    The caller has already obtained explicit user confirmation.  A durable
    pre-v2 backup and an audit sidecar are written below the project-local
    ``.willy/config-migrations/`` archive before atomically replacing the
    active config, so the repository root stays a single-source configuration
    surface and the orchestrator never observes a partially-written
    configuration.  Legacy EQ durations outside the v2 7--100 ns contract are
    deliberately replaced with the documented 10 ns default; the mapping
    records that scientific-plan change instead of silently accepting 5 ns.
    """
    source = Path(source_path)
    if not source.is_file():
        raise MDConfigError(f"迁移源配置不存在: {source}")
    try:
        original = json.loads(source.read_text())
    except json.JSONDecodeError as exc:
        raise MDConfigError(f"迁移源配置不是有效 JSON: {exc}") from exc
    if not isinstance(original, dict):
        raise MDConfigError("迁移源 config 必须是对象")

    migrated, mapping = migrate_config_dict(original)
    source_md = original.get("md", {})
    already_v2 = (
        isinstance(source_md, dict)
        and source_md.get("schema_version") == MD_SCHEMA_VERSION
        and not contains_legacy_md_fields(source_md)
    )
    if already_v2:
        # Do not manufacture a backup that claims to be pre-v2 data.
        return source, source, source, mapping

    validation = validate_md_config(migrated.get("md"))
    normalized_eq = False
    if any("EQ 六段总时长" in issue for issue in validation.issues):
        migrated["md"]["eq"]["segments_ns"] = deepcopy(DEFAULT_EQ_SEGMENTS_NS)
        normalized_eq = True
        validation = validate_md_config(migrated["md"])
    if validation.issues:
        raise MDConfigError("迁移后 MD 配置仍无效: " + "; ".join(validation.issues))
    if validate_config_fn is not None:
        config_issues = validate_config_fn(migrated)
        if config_issues:
            raise MDConfigError("迁移后完整配置仍无效: " + "; ".join(config_issues))

    archive_dir = source.parent / _CONFIG_MIGRATION_DIRNAME
    backup = (
        Path(backup_path)
        if backup_path is not None
        else archive_dir / f"{source.stem}.pre-v2.json"
    )
    audit = (
        Path(mapping_path)
        if mapping_path is not None
        else archive_dir / f"{source.stem}.migration.json"
    )
    if backup.resolve() == source.resolve() or audit.resolve() == source.resolve():
        raise MDConfigError("备份和迁移审计文件不能覆盖当前 config.json")
    if backup.exists() and backup.read_bytes() != source.read_bytes():
        raise MDConfigError(f"已有不同的迁移前备份，拒绝覆盖: {backup}")

    mapping = deepcopy(mapping)
    mapping["adoption"] = {
        "mode": "confirmed_in_place",
        "active_config": str(source),
        "backup": str(backup),
        "eq_duration_normalized_to_default": normalized_eq,
        "effective_eq_segments_ns": migrated["md"]["eq"]["segments_ns"],
        "effective_eq_total_ns": eq_total_ns(migrated["md"]),
        "validation": {
            "issues": list(validation.issues),
            "warnings": list(validation.warnings),
        },
    }

    # The active file is replaced last.  If writing the backup or audit fails,
    # the old config remains the active one and no ambiguous run can start.
    if not backup.exists():
        write_json(backup, original)
    write_json(audit, mapping)
    write_json(source, migrated)
    return source, backup, audit, mapping


def _in_range(
    issues: list[str],
    name: str,
    value: object,
    minimum: float,
    maximum: float,
    *,
    positive: bool = False,
    integer: bool = False,
) -> None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        issues.append(f"{name} 必须是数值")
        return
    if integer and not numeric.is_integer():
        issues.append(f"{name} 必须是整数")
    if positive and numeric <= 0:
        issues.append(f"{name} 必须为正")
    if numeric < minimum or numeric > maximum:
        issues.append(f"{name}={numeric:g} 超出允许范围 [{minimum:g}, {maximum:g}]")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="显式迁移 Willy MD 配置到 schema v2")
    parser.add_argument("source", help="旧 config.json 路径")
    parser.add_argument("--output", help="v2 配置输出路径（默认 <source>.v2.json）")
    parser.add_argument("--adopt", action="store_true", help="确认后原子替换当前配置为 v2")
    options = parser.parse_args()
    if options.adopt:
        target, backup, audit, report = adopt_migrated_config_file(options.source)
        print(f"已采用 v2 配置: {target}")
        print(f"迁移前备份: {backup}")
        print(f"字段映射: {audit}")
    else:
        target, audit, report = migrate_config_file(options.source, options.output)
        print(f"迁移配置: {target}")
        print(f"字段映射: {audit}")
    validation = report.get("validation", {})
    if validation.get("issues"):
        print("迁移后仍需调整:", "; ".join(validation["issues"]))
_CONFIG_MIGRATION_DIRNAME = ".willy/config-migrations"
