"""
llm_config.py
=============
config.json 的验证、写入、分子列表扫描。

用法:
  from willy.llm_config import validate_config, apply_config, _available_residues
"""

from __future__ import annotations
from pathlib import Path
from typing import Optional
import json
import copy

from willy._paths import get_project_root

ROOT = get_project_root()
CONFIG_PATH = ROOT / "config.json"


def _available_residues() -> dict[str, dict]:
    """扫描 struct/ 下所有 .gjf，从 config.json 补充已知电荷/自旋/基组。"""
    struct_dir = ROOT / "struct"
    residues = {}
    if struct_dir.exists():
        for gjf in sorted(struct_dir.glob("*.gjf")):
            name = gjf.stem
            if name.endswith("_run"):
                continue
            residues[name] = {"charge": None, "spin": None, "basis": None}
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            existing = json.load(f)
        mols = existing.get("molecules", {})
        for name in residues:
            if name in mols:
                residues[name]["charge"] = mols[name].get("charge")
                residues[name]["spin"] = mols[name].get("spin")
                residues[name]["basis"] = mols[name].get("basis", "b3lyp/6-311+g(d,p)")
    return residues


def validate_config(config_dict: dict) -> list[str]:
    """验证生成的 config.json 是否合法。返回问题列表，空列表 = 通过。"""
    issues = []
    residues = config_dict.get("residues", {})
    if not residues:
        issues.append("residues 不能为空")
    total = sum(residues.values())
    if total == 0:
        issues.append("总分子数为 0")
    if total > 10000:
        issues.append(f"总分子数 {total} 很大，可能计算时间极长")
    molecules = config_dict.get("molecules", {})
    for name in residues:
        if name not in molecules:
            issues.append(f"residues 中的 '{name}' 未在 molecules 中定义")
    md = config_dict.get("md", {})
    if md:
        ref_t = md.get("ref_t", 298)
        if ref_t < 0 or ref_t > 2000:
            issues.append(f"ref_t={ref_t} 超出合理范围")
        dt = md.get("dt", 0.001)
        if dt < 0.0001 or dt > 0.01:
            issues.append(f"dt={dt} 超出合理范围")
    return issues


def apply_config(config_dict: dict, backup: bool = True) -> Path:
    """将验证通过的配置写回 config.json。"""
    if backup and CONFIG_PATH.exists():
        backup_path = CONFIG_PATH.with_suffix(".json.bak")
        CONFIG_PATH.rename(backup_path)
        print(f"[llm_config] 已备份: {backup_path}")
    complete = _apply_defaults(config_dict)
    CONFIG_PATH.write_text(json.dumps(complete, indent=2, ensure_ascii=False) + "\n")
    print(f"[llm_config] config.json 已更新")
    return CONFIG_PATH


def _apply_defaults(config_dict: dict) -> dict:
    """将 LLM 输出的 config 与默认值合并，写入 config.json 前调用。"""
    out = copy.deepcopy(config_dict)
    # 剥离 LLM 协议字段，只保留运行时配置
    out.pop("error", None)
    out.pop("warnings", None)
    out.setdefault("backend", "g16")
    out.setdefault("defaults", {"mem": "5GB", "nproc": 8})
    out["defaults"].setdefault("mem", "5GB")
    out["defaults"].setdefault("nproc", 8)
    available = _available_residues()
    residues = out.get("residues", {})
    molecules = out.setdefault("molecules", {})
    for name in residues:
        if name not in molecules:
            info = available.get(name, {})
            molecules[name] = {
                "charge": info.get("charge", 0),
                "spin": info.get("spin", 1),
                "basis": info.get("basis", "b3lyp/6-311+g(d,p)"),
                "solvent": "acetone",
                "mem": "",
                "nproc": None,
            }
        molecules[name].setdefault("charge", 0)
        molecules[name].setdefault("spin", 1)
        molecules[name].setdefault("basis", "b3lyp/6-311+g(d,p)")
        molecules[name].setdefault("mem", "")
        molecules[name].setdefault("nproc", None)
        molecules[name].setdefault("solvent", "acetone")
    out.setdefault("md", {})
    md = out["md"]
    md.setdefault("dt", 0.001)
    md.setdefault("ref_t", 298.15)
    md.setdefault("ref_p", 1.01325)
    md.setdefault("eq_ns", 10)
    md.setdefault("prod_ns", 10)
    md.setdefault("tcoupl", "V-rescale")
    md.setdefault("tau_t", 0.5)
    md.setdefault("pcoupl", "C-rescale")
    md.setdefault("pcoupltype", "isotropic")
    md.setdefault("compressibility", "8.5e-5")
    md.setdefault("constraints", "hbonds")
    md.setdefault("rcoulomb", 1.0)
    md.setdefault("rvdw", 1.0)
    md.setdefault("coulombtype", "PME")
    md.setdefault("vdwtype", "Cut-off")
    out.setdefault("topology", {})
    topo = out["topology"]
    topo.setdefault("backend", "sobtop")
    topo.setdefault("force_field", "gaff")
    topo.setdefault("default_net_charge", 0)
    topo.setdefault("default_lbcc", False)
    topo.setdefault("default_opt_steps", 0)
    out.setdefault("box", {})
    out["box"].setdefault("density", 6.0)
    out["box"].setdefault("box_size", None)
    out["box"].setdefault("tolerance", 2.0)
    return out
