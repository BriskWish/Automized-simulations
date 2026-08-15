"""
workflow_config.py
==================
config.json 的验证、写入、分子列表扫描。

用法:
  from willy.workflow_config import validate_config, apply_config, _available_residues
"""

from __future__ import annotations
from pathlib import Path
from typing import Any, Mapping, Optional
import json
import copy
import math

from willy._paths import get_project_root
from willy.config_schema import validate_config_schema
from willy.config_store import replace_json_with_backup
from willy.remote_registry import merge_execution_md_defaults, validate_execution_md
from willy.simulation.protocol import (
    MDConfigError,
    adopt_migrated_config_file,
    merge_v2_defaults,
    migrate_config_file,
    validate_md_config,
)

ROOT = get_project_root()
CONFIG_PATH = ROOT / "config.json"


def _available_residues() -> dict[str, dict]:
    """扫描 struct/ 下可选后端原始输入，不把历史配置当作电荷来源。"""
    struct_dir = ROOT / "struct"
    residues = {}
    if struct_dir.exists():
        for input_path in sorted([*struct_dir.glob("*.gjf"), *struct_dir.glob("*.inp")]):
            name = input_path.stem
            if name.endswith("_run"):
                continue
            residues.setdefault(name, {"charge": None, "spin": None, "basis": None, "input_suffixes": []})
            residues[name]["input_suffixes"].append(input_path.suffix)
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            existing = json.load(f)
        mols = existing.get("molecules", {})
        for name in residues:
            if isinstance(mols, Mapping) and isinstance(mols.get(name), Mapping):
                # Basis is a user preference; charge/spin must be re-read from
                # the selected backend's raw source before every proposal.
                residues[name]["basis"] = mols[name].get("basis", "b3lyp/6-311+g(d,p)")
    return residues


def validate_config(config_dict: object) -> list[str]:
    """验证生成的 config.json 是否合法。返回问题列表，空列表 = 通过。"""
    schema = validate_config_schema(config_dict)
    if not isinstance(config_dict, Mapping):
        return list(schema.issues)

    issues: list[str] = list(schema.issues)
    residues = config_dict.get("residues", {})
    if not isinstance(residues, Mapping):
        residues = {}
    if not residues:
        issues.append("residues 不能为空")
    total = 0.0
    for name, value in residues.items():
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            issues.append(f"residues.{name} 必须是数值")
            continue
        if not math.isfinite(numeric):
            issues.append(f"residues.{name} 必须是有限数值")
            continue
        total += numeric
    if total == 0:
        issues.append("总分子数为 0")
    if total > 10000:
        issues.append(f"总分子数 {total} 很大，可能计算时间极长")
    molecules = config_dict.get("molecules", {})
    if not isinstance(molecules, Mapping):
        molecules = {}
    defaults = config_dict.get("defaults", {})
    if isinstance(defaults, Mapping) and "nproc" in defaults:
        _validate_nproc(issues, "defaults.nproc", defaults.get("nproc"), nullable=False)
    for name, molecule in molecules.items():
        if isinstance(molecule, Mapping) and "nproc" in molecule:
            _validate_nproc(issues, f"molecules.{name}.nproc", molecule.get("nproc"), nullable=True)
    for name in residues:
        if name not in molecules:
            issues.append(f"residues 中的 '{name}' 未在 molecules 中定义")
            continue
        molecule = molecules.get(name)
        if not isinstance(molecule, Mapping):
            continue
        charge = molecule.get("charge")
        spin = molecule.get("spin")
        if isinstance(charge, bool) or not isinstance(charge, int):
            issues.append(f"molecules.{name}.charge 必须是由原始输入审计得到的整数")
        if isinstance(spin, bool) or not isinstance(spin, int) or spin < 1:
            issues.append(f"molecules.{name}.spin 必须是由原始输入审计得到的正整数")
    md = config_dict.get("md", {})
    if isinstance(md, Mapping) and md:
        md_validation = validate_md_config(md)
        issues.extend(md_validation.issues)
    box = config_dict.get("box", {})
    if box is not None and not isinstance(box, dict):
        pass
    elif isinstance(box, dict):
        if "density" in box:
            issues.append("旧字段 box.density 不可直接执行；请迁移为 box.packing_number_density_nm3")
        mass_density = box.get("target_mass_density_g_cm3")
        if mass_density is not None:
            try:
                if not 0 < float(mass_density) <= 25:
                    issues.append("box.target_mass_density_g_cm3 必须在 (0, 25]")
            except (TypeError, ValueError):
                issues.append("box.target_mass_density_g_cm3 必须是数值")
        density = box.get("packing_number_density_nm3")
        if density is not None:
            try:
                if not 0 < float(density) <= 100:
                    issues.append("box.packing_number_density_nm3 必须在 (0, 100]")
            except (TypeError, ValueError):
                issues.append("box.packing_number_density_nm3 必须是数值")
        for key in ("box_size", "tolerance"):
            value = box.get(key)
            if value is None and key == "box_size":
                continue
            try:
                if value is not None and float(value) <= 0:
                    issues.append(f"box.{key} 必须为正")
            except (TypeError, ValueError):
                issues.append(f"box.{key} 必须是数值")
    net_charge = _net_charge(config_dict)
    if net_charge and not _has_charge_confirmation(config_dict):
        issues.append(
            f"体系总电荷为 {net_charge:+g}；请设置 ion_compensation 或明确 non_neutral_confirmed"
        )
    topology = config_dict.get("topology")
    if isinstance(topology, Mapping):
        from willy.topology.backends import normalize_topology_config
        _, topology_issues, _ = normalize_topology_config(topology)
        issues.extend(topology_issues)
    execution = config_dict.get("execution")
    if execution is None:
        issues.extend(validate_execution_md(None))
    elif isinstance(execution, Mapping):
        issues.extend(validate_execution_md(execution.get("md")))
    return issues


def _validate_nproc(issues: list[str], field: str, value: object, *, nullable: bool) -> None:
    if value is None and nullable:
        return
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 4096:
        suffix = "正整数（1-4096）或 null" if nullable else "正整数（1-4096）"
        issues.append(f"{field} 必须是{suffix}")


def apply_config(config_dict: dict, backup: bool = True) -> Path:
    """将验证通过的配置写回 config.json。"""
    complete = _apply_defaults(config_dict)
    issues = validate_config(complete)
    if issues:
        raise ValueError("config.json 无效: " + "; ".join(issues))
    backup_path = CONFIG_PATH.with_suffix(".json.bak") if backup else None
    replace_json_with_backup(CONFIG_PATH, complete, backup_path=backup_path)
    if backup_path is not None and backup_path.exists():
        print(f"[workflow_config] 已备份: {backup_path}")
    print(f"[workflow_config] config.json 已更新")
    return CONFIG_PATH


def _apply_defaults(config_dict: dict) -> dict:
    """将 LLM 输出的 config 与默认值合并，写入 config.json 前调用。"""
    schema = validate_config_schema(config_dict)
    if not schema.valid:
        raise ValueError("config.json 结构无效: " + "; ".join(schema.issues))
    out = copy.deepcopy(config_dict)
    # 剥离 LLM 协议字段，只保留运行时配置
    out.pop("error", None)
    out.pop("warnings", None)
    out.setdefault("backend", "g16")
    out.setdefault("defaults", {"mem": "5GB", "nproc": 8})
    out["defaults"].setdefault("mem", "5GB")
    out["defaults"].setdefault("nproc", 8)
    residues = out.get("residues", {})
    molecules = out.setdefault("molecules", {})
    for name in residues:
        if name not in molecules:
            molecules[name] = {
                "charge": None,
                "spin": None,
                "basis": "b3lyp/6-311+g(d,p)",
                "solvent": "acetone",
                "mem": "",
                "nproc": None,
            }
        if isinstance(molecules[name], Mapping):
            molecules[name].setdefault("basis", "b3lyp/6-311+g(d,p)")
            molecules[name].setdefault("mem", "")
            molecules[name].setdefault("nproc", None)
            molecules[name].setdefault("solvent", "acetone")
    try:
        out["md"] = merge_v2_defaults(out.get("md", {}))
    except MDConfigError:
        # Keep legacy fields intact so validate_config can make the migration
        # requirement visible instead of silently changing the scientific plan.
        out.setdefault("md", {})
    out.setdefault("topology", {})
    topo = out["topology"]
    topo.setdefault("backend", "sobtop")
    topo.setdefault("force_field", "gaff_uff")
    from willy.topology.backends import normalize_topology_config
    normalized_topology, _, _ = normalize_topology_config(topo)
    out["topology"] = normalized_topology
    topo = out["topology"]
    topo.setdefault("default_lbcc", False)
    topo.setdefault("default_opt_steps", 0)
    out.setdefault("box", {})
    box = out["box"]
    box.setdefault("box_size", None)
    box.setdefault("tolerance", 2.0)
    # Preserve an existing number-density snapshot for reproducibility. New
    # configurations receive the mass-density default used by Packmol.
    if "target_mass_density_g_cm3" not in box and "packing_number_density_nm3" not in box:
        box["target_mass_density_g_cm3"] = 0.7
    out.setdefault("execution", {})
    execution = out["execution"]
    execution["md"] = merge_execution_md_defaults(execution.get("md"))
    # All local engines consume this shared run-local configuration.  Bound
    # defaults and explicit molecule overrides once here before writing it.
    from willy.execution_resources import normalize_config_nproc
    return normalize_config_nproc(out).config


def migrate_md_config(
    source_path: str | Path = CONFIG_PATH,
    output_path: str | Path | None = None,
    mapping_path: str | Path | None = None,
) -> tuple[Path, Path, dict]:
    """Explicitly migrate a legacy configuration; never mutates source by default."""
    return migrate_config_file(source_path, output_path, mapping_path)


def migrate_and_adopt_md_config(
    source_path: str | Path = CONFIG_PATH,
    *,
    backup_path: str | Path | None = None,
    mapping_path: str | Path | None = None,
) -> tuple[Path, Path, Path, dict]:
    """Explicitly adopt a validated v2 migration as the active config.

    This is intentionally separate from :func:`migrate_md_config`: callers
    must choose the mutating adoption path after the user confirms it.
    """
    return adopt_migrated_config_file(
        source_path,
        backup_path=backup_path,
        mapping_path=mapping_path,
        validate_config_fn=validate_config,
    )


def _net_charge(config_dict: dict) -> float:
    residues = config_dict.get("residues", {})
    molecules = config_dict.get("molecules", {})
    try:
        return sum(float(count) * float(molecules.get(name, {}).get("charge", 0))
                   for name, count in residues.items())
    except (AttributeError, TypeError, ValueError):
        return 0.0


def _has_charge_confirmation(config_dict: dict) -> bool:
    if config_dict.get("non_neutral_confirmed") is True:
        return True
    compensation = config_dict.get("ion_compensation", {})
    return isinstance(compensation, dict) and bool(
        compensation.get("confirmed") or compensation.get("strategy")
    )
