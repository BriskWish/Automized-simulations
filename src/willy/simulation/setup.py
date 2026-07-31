"""
setup.py
===========
MD 运行准备 —— 将 GROMACS 所需的所有输入文件收集到运行目录。

GROMACS 只能读取当前工作目录下的文件，因此需要把
topol.top、.itp、.mdp、model.pdb 集中到一个目录。

用法:
  CLI:  python3 -m willy.simulation.setup [target_dir]
  API:  from willy.simulation.setup import collect_files
"""

from __future__ import annotations
from pathlib import Path
import shutil
import re
import json
from datetime import datetime

from willy._paths import get_project_root

ROOT = get_project_root()
_COUNTER_FILE = ROOT / ".md_counter"


def _next_run_dir(topol_path: Path = None, base_dir: Path = None) -> Path:
    """
    生成下一个 MD 运行目录名: md_Li100FEC200NO3100_202607260001

    命名规则:
      - 残基名排序后拼接: Li100FEC200NO3100
      - 日期: YYYYMMDD
      - 序号: 0001 起, 每日 00:00 重置
    """
    if base_dir is None:
        base_dir = ROOT / "md_run"

    # 解析残基信息
    if topol_path is None:
        topol_path = ROOT / "topo" / "topol.top"
    topol = Path(topol_path)

    residue_str = ""
    if topol.exists():
        in_molecules = False
        residues = {}
        for line in topol.read_text().split("\n"):
            s = line.strip()
            if s.startswith("[") and "molecules" in s.lower():
                in_molecules = True
                continue
            if in_molecules and s.startswith("["):
                break
            if in_molecules and s:
                parts = s.split()
                if len(parts) >= 2:
                    try:
                        residues[parts[0]] = int(parts[1])
                    except ValueError:
                        pass
        # 按名称排序拼接
        residue_str = "".join(f"{k}{v}" for k, v in sorted(residues.items())) if residues else "unknown"

    # 日期 + 序号
    today = datetime.now().strftime("%Y%m%d")

    # 读取计数器
    counter = 1
    if _COUNTER_FILE.exists():
        try:
            data = json.loads(_COUNTER_FILE.read_text())
            if data.get("date") == today:
                counter = data.get("count", 0) + 1
        except (json.JSONDecodeError, KeyError):
            pass
    _COUNTER_FILE.write_text(json.dumps({"date": today, "count": counter}))

    dirname = f"md_{residue_str}_{today}{counter:04d}"
    return base_dir / dirname


def _reset_counter():
    """手动重置计数器（每日 00:00 由 cron 调用或首次运行时自动）"""
    today = datetime.now().strftime("%Y%m%d")
    _COUNTER_FILE.write_text(json.dumps({"date": today, "count": 0}))


def _parse_includes(topol_path: Path) -> list[str]:
    """从 topol.top 中提取 #include 引用的文件名。"""
    if not topol_path.exists():
        return []
    includes = []
    for line in topol_path.read_text().split("\n"):
        m = re.match(r'^#include\s+"(.+)"', line.strip())
        if m:
            includes.append(m.group(1))
    return includes


def _resolve_itp_files(residues: dict[str, int]) -> list[Path]:
    """根据 residues 推断需要的 .itp 文件列表。"""
    topo_dir = ROOT / "topo"
    result = []
    for name in residues:
        itp = topo_dir / f"{name}.itp"
        if itp.exists():
            result.append(itp)
    return result


def collect_files(target_dir: str = None,
                  topol_path: str = None,
                  mdp_dir: str = None,
                  structure: str = None,
                  ) -> Path:
    """
    收集 GROMACS 所需的全部输入文件到自动命名的运行目录。

    Args:
        target_dir: 目标目录 (None 则自动生成 md_Li100FEC100_202607260001)
        topol_path: topol.top 路径
        mdp_dir: .mdp 文件目录
        structure: 起始结构文件

    Returns:
        目标目录的 Path
    """
    if target_dir is None:
        out = _next_run_dir(topol_path)
    else:
        out = Path(target_dir)
        if not out.is_absolute():
            out = ROOT / target_dir
    out.mkdir(parents=True, exist_ok=True)

    # ── 1. 主拓扑 topol.top ──
    if topol_path is None:
        topol_path = str(ROOT / "topo" / "topol.top")
    topol = Path(topol_path)

    collected = []

    if topol.exists():
        shutil.copy2(topol, out / "topol.top")
        collected.append("topol.top")

        # ── 2. 从 topol.top 解析 #include 行，复制 .itp ──
        for inc_name in _parse_includes(topol):
            src = topol.parent / inc_name
            if src.exists():
                shutil.copy2(src, out / inc_name)
                collected.append(inc_name)
            else:
                print(f"[setup] ⚠  {inc_name}: 文件不存在，跳过")
    else:
        # topol.top 不存在，尝试从 config.json 推断
        print(f"[setup] ⚠  {topol} 不存在，从 config.json 推断所需 .itp")
        config_path = ROOT / "config.json"
        if config_path.exists():
            with open(config_path) as f:
                residues = json.load(f).get("residues", {})
            if residues:
                print(f"[setup]   请先运行 top_assembly 生成 topol.top")
                print(f"[setup]   需要的 .itp: {', '.join(residues.keys())}")
        return out

    # ── 3. .mdp 文件 ──
    if mdp_dir is None:
        mdp_dir = str(ROOT / "process")
    mdp_dir = Path(mdp_dir)
    for mdp in sorted(mdp_dir.glob("*.mdp")):
        shutil.copy2(mdp, out / mdp.name)
        collected.append(mdp.name)

    # ── 4. 起始结构 ──
    if structure is None:
        structure = str(ROOT / "model.pdb")
    struct = Path(structure)
    if struct.exists():
        shutil.copy2(struct, out / struct.name)
        collected.append(struct.name)
    else:
        print(f"[setup] ⚠  起始结构 {struct} 不存在")

    print(f"[setup] ✅ 已收集 {len(collected)} 个文件到 {out}/")
    for f in collected:
        print(f"[setup]     {f}")

    return out


# ============================================================
# CLI
# ============================================================

if __name__ == "__main__":
    import sys
    target = sys.argv[1] if len(sys.argv) > 1 else "md_run"
    collect_files(target)
