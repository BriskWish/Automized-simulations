"""
top_assembly.py
============
从零生成 GROMACS 主拓扑文件（.top）。

流程:
  1. 读取 config.json → 获取残基种类与个数
  2. 扫描 topo/ 下对应 .itp → 提取 [atomtypes] → 去重
  3. 生成 #include 列表、[system] 名称、[molecules] 列表
  4. 写出 .top 文件
  5. 直接调用 itp_revise 清理 itp 中的 [atomtypes] 段
"""

from __future__ import annotations
from pathlib import Path
from typing import Dict, OrderedDict
import json

from willy._paths import get_project_root
from willy.errors import StepResult, StepError, ErrorKind


# ── 路径 ──
ROOT = get_project_root()
CONFIG_PATH = ROOT / "config.json"
TOPO_DIR = ROOT / "topo"


# ============================================================
# 入参层
# ============================================================

class TopConfig:
    """
    主拓扑配置。

    从 config.json 读取，也可直接构建（方便 AI 填充）。
    """
    def __init__(self, residues: dict[str, int] = None):
        self.residues: dict[str, int] = residues or {}

    @classmethod
    def from_json(cls, path: str = "config.json") -> "TopConfig":
        with open(path) as f:
            data = json.load(f)
        return cls(residues=data.get("residues", {}))


# ============================================================
# 核心：atomtype 提取与去重
# ============================================================

def _extract_atomtypes(itp_path: Path) -> list[str]:
    """
    从单个 .itp 文件提取 [ atomtypes ] 段的数据行（不含注释和空行）。

    Returns:
        每行一个 atomtype 定义
    """
    content = itp_path.read_text()
    lines = content.split("\n")

    in_section = False
    entries = []

    for line in lines:
        stripped = line.strip()

        # 检测段头
        if stripped.startswith("[") and "atomtypes" in stripped.lower():
            in_section = True
            continue

        if not in_section:
            continue

        # 段尾：空行或下一个 [
        if stripped == "" or stripped.startswith("["):
            break

        # 跳过注释行
        if stripped.startswith(";"):
            continue

        entries.append(stripped)

    return entries


def _collect_dedup_atomtypes(residues: list[str], topo_dir: str = "topo") -> list[str]:
    """
    收集所有残基的 atomtype 定义，按首次出现顺序去重。

    Args:
        residues: 残基名列表（如 ["Li", "DME", "DMM", "FEC", "NO3"]）
        topo_dir: .itp 文件所在目录

    Returns:
        去重后的 atomtype 行列表
    """
    seen: set[str] = set()
    result: list[str] = []

    for name in residues:
        itp_path = Path(topo_dir) / f"{name}.itp"
        if not itp_path.exists():
            print(f"[top_assembly] ⚠  {itp_path} 不存在，跳过 atomtype 提取")
            continue

        for entry in _extract_atomtypes(itp_path):
            # atomtype 名 = 第一个空格分隔的字段
            at_name = entry.split()[0] if entry.split() else entry
            if at_name not in seen:
                seen.add(at_name)
                result.append(entry)
                print(f"[top_assembly]   + {at_name} (from {name})")
            else:
                print(f"[top_assembly]   - {at_name} (重复，跳过)")

    return result


# ============================================================
# 生成 .top 文件
# ============================================================

def generate_top(config: TopConfig = None,
                 config_path: str = None,
                 topo_dir: str = None,
                 output_path: str = None,
                 ) -> Path:
    """
    生成主拓扑文件。

    Args:
        config: TopConfig 对象；None 则从 config.json 读取
        config_path: 配置文件路径；None 则使用 ROOT/config.json
        topo_dir: .itp 文件所在目录；None 则使用 ROOT/topo
        output_path: 输出 .top 路径；None 则自动为 topo/topol.top

    Returns:
        生成的 .top 文件路径
    """
    if config_path is None:
        config_path = str(CONFIG_PATH)
    if topo_dir is None:
        topo_dir = str(TOPO_DIR)
    if config is None:
        config = TopConfig.from_json(config_path)

    residues = config.residues
    residue_names = list(residues.keys())

    if not residue_names:
        raise ValueError("config.json 中 residues 为空")

    # ── 构建各段 ──

    # [ atomtypes ] —— 从各 itp 提取并去重
    print("[top_assembly] 收集 atomtypes…")
    atomtype_lines = _collect_dedup_atomtypes(residue_names, topo_dir)

    # #include 行
    include_lines = [f'#include "{name}.itp"' for name in residue_names]

    # [ system ] 名称
    system_name = "-".join(residue_names)

    # [ molecules ] 行
    molecule_lines = [f"{name}   {count}" for name, count in residues.items()]

    # ── 组装 .top 内容 ──
    lines = []

    # 头
    lines.append("#define GAFF")
    lines.append("#define UFF")
    lines.append("")

    # [ defaults ]
    lines.append("[ defaults ]")
    lines.append("1 3 yes 0.5 0.5")
    lines.append("")

    # [ atomtypes ]
    lines.append("[ atomtypes ]")
    lines.append("; name   at.num      mass       charge   ptype     sigma (nm)    epsilon (kJ/mol)")
    for entry in atomtype_lines:
        lines.append(entry)
    lines.append("")

    # #include
    for inc in include_lines:
        lines.append(inc)
    lines.append("")

    # [ system ]
    lines.append("[ system ]")
    lines.append(system_name)
    lines.append("")

    # [ molecules ]
    lines.append("[ molecules ]")
    for mol in molecule_lines:
        lines.append(mol)
    lines.append("")

    # ── 写出 ──
    if output_path is None:
        output_path = str(Path(topo_dir) / "topol.top")

    out = Path(output_path)
    out.write_text("\n".join(lines))
    print(f"[top_assembly] ✅ 主拓扑已生成: {out}")
    print(f"[top_assembly]    包含 {len(atomtype_lines)} 个 atomtype, "
          f"{len(include_lines)} 个 itp, {len(molecule_lines)} 个分子行")

    return out


# ============================================================
# 主流程：生成 top → 结构检查 → 过滤 itp
# ============================================================

def build(config_path: str = None,
          topo_dir: str = None,
          output_path: str = None,
          ) -> StepResult:
    """
    完整流程:
      1. 生成 .top 文件
      2. 结构完整性检查
      3. 直接调用 itp_revise 清理 itp

    Returns:
        StepResult (success=True 时 outputs={"topol": path})
    """
    import time as _time
    _start = _time.time()

    if config_path is None:
        config_path = str(CONFIG_PATH)
    if topo_dir is None:
        topo_dir = str(TOPO_DIR)
    # ── Step 1: 生成 ──
    try:
        top_path = generate_top(
            config_path=config_path,
            topo_dir=topo_dir,
            output_path=output_path,
        )
    except ValueError as e:
        return StepResult(
            step_name="top_assembly", step_index=5, success=False,
            error=StepError(kind=ErrorKind.CONFIG_INVALID,
                            message=str(e),
                            hint="检查 config.json 中 residues 是否配置正确"),
            duration_s=_time.time() - _start,
        )

    # ── Step 2: 结构检查 ──
    content = top_path.read_text()
    checks = {
        "[ defaults ]":  "缺失 [ defaults ] 段",
        "[ atomtypes ]": "缺失 [ atomtypes ] 段",
        "[ system ]":    "缺失 [ system ] 段",
        "[ molecules ]": "缺失 [ molecules ] 段",
    }
    missing = []
    for tag, msg in checks.items():
        if tag not in content:
            missing.append(f"{msg}")

    mol_idx = content.find("[ molecules ]")
    after_mol = content[mol_idx:].strip().split("\n")
    if len(after_mol) < 2:
        missing.append("[ molecules ] 段无内容")

    if missing:
        print("[top_assembly] ❌ 结构检查失败:")
        for m in missing:
            print(f"  {m}")
        return StepResult(
            step_name="top_assembly", step_index=5, success=False,
            error=StepError(kind=ErrorKind.UNKNOWN,
                            message="topol.top 结构不完整",
                            raw_output="\n".join(missing),
                            hint="检查 itp 文件是否完整，重新运行 topo_gaff"),
            artifacts=[str(top_path)],
            duration_s=_time.time() - _start,
        )
    else:
        print("[top_assembly] ✅ 结构检查通过")

    # ── Step 3: 直接调用 itp_revise ──
    print("[top_assembly] 调用 itp_revise 修订 itp…")
    from willy.topology.itp_revise import revise_all
    count = revise_all(topo_dir)
    print(f"[top_assembly] ✅ itp_revise 完成: {count} 个文件修订")

    duration = _time.time() - _start
    return StepResult(
        step_name="top_assembly", step_index=5, success=True,
        outputs={"topol": str(top_path)},
        artifacts=[str(top_path)],
        duration_s=duration,
        extra={"itp_revised": count},
    )


# ============================================================
# 测试入口
# ============================================================

if __name__ == "__main__":
    build()
