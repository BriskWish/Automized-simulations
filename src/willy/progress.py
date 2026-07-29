"""
progress.py
===========
流水线进度报告器 —— 扫描文件系统，显示各步骤完成状态。

用法:
  CLI:  python3 -m willy.progress
  API:  from willy.progress import get_status, format_status, is_running
"""

from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
import json
import os
import signal
from datetime import datetime, timezone

from willy._paths import get_project_root

ROOT = get_project_root()
LOCK_FILE = ROOT / ".pipeline.lock"


# ============================================================
# 数据模型
# ============================================================

@dataclass
class StepStatus:
    index: int
    label: str
    done: bool
    detail: str = ""          # e.g. "3/3 .fchk" or "topo/topol.top"


@dataclass
class PipelineStatus:
    steps: list[StepStatus]
    running: bool
    lock_info: str = ""       # PID + start time if running

    @property
    def done_count(self) -> int:
        return sum(1 for s in self.steps if s.done)

    @property
    def total(self) -> int:
        return len(self.steps)


# ============================================================
# 锁文件
# ============================================================

def _pid_alive(pid: int) -> bool:
    """检查 PID 是否还在运行。"""
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def check_lock() -> tuple[bool, str]:
    """
    检查锁文件状态。

    Returns:
        (running, info): running=True 表示有活着的实例正在运行
    """
    if not LOCK_FILE.exists():
        return False, ""

    try:
        content = LOCK_FILE.read_text().strip().split("\n")
        pid = int(content[0])
        start_time = content[1] if len(content) > 1 else "unknown"
        if _pid_alive(pid):
            return True, f"PID {pid}, started {start_time}"
        else:
            return False, f"stale lock (PID {pid} dead, started {start_time})"
    except (ValueError, IndexError):
        return False, "stale lock (unreadable)"


# ============================================================
# 进度扫描
# ============================================================

def _load_residues() -> dict[str, int]:
    """从 config.json 读取期望的残基列表。"""
    config_path = ROOT / "config.json"
    if not config_path.exists():
        return {}
    with open(config_path) as f:
        data = json.load(f)
    return data.get("residues", {})


def _count_matching(dir_path: Path, pattern: str, names: list[str]) -> tuple[int, int]:
    """统计 names 中有多少个在 dir_path 下有匹配 pattern 的文件。"""
    if not dir_path.exists():
        return 0, len(names)
    found = 0
    for name in names:
        if list(dir_path.glob(f"{name}{pattern}")):
            found += 1
    return found, len(names)


def get_status() -> PipelineStatus:
    """扫描文件系统，返回当前流水线状态。"""
    residues = _load_residues()
    names = list(residues.keys()) if residues else []
    total = len(names)

    # Step 1: .fchk (g16) 或 .molden (ORCA)
    fchk_ok, _ = _count_matching(ROOT / "struct", ".fchk", names)
    molden_ok, _ = _count_matching(ROOT / "struct", ".molden", names)
    s1_done = total > 0 and (fchk_ok == total or molden_ok == total)

    # Step 2: .mol2 (from fchk or molden)
    mol2_ok, _ = _count_matching(ROOT / "struct", ".mol2", names)
    molden_ok2, _ = _count_matching(ROOT / "struct", ".molden", names)
    s2_done = total > 0 and (mol2_ok == total or molden_ok2 == total)

    # Step 3: .chg
    chg_ok, _ = _count_matching(ROOT / "struct", ".chg", names)
    s3_done = total > 0 and chg_ok == total

    s4_done = s5_done = s6_done = s7_done = s8_done = False
    s4_detail = s8_detail = ""
    mdp_count = 0

    latest = None
    md_dir = ROOT / "md_run"
    if md_dir.exists():
        dirs = sorted(md_dir.glob("md_*"), reverse=True)
        if dirs:
            latest = dirs[0]
            itp_ok, _ = _count_matching(latest, ".itp", names)
            gro_ok, _ = _count_matching(latest, ".gro", names)
            s4_done = total > 0 and itp_ok == total and gro_ok == total
            s4_detail = f"{itp_ok}/{total} .itp, {gro_ok}/{total} .gro" if total > 0 else ""
            s5_done = (latest / "topol.top").exists()
            mdp_files = list(latest.glob("*.mdp"))
            mdp_count = len(mdp_files)
            s6_done = mdp_count >= 3
            s7_done = (latest / "model.pdb").exists()
            s8_done = True
            s8_detail = str(latest.name)

    steps = [
        StepStatus(1, "结构优化 + formchk", s1_done,
                   f"{fchk_ok}/{total} .fchk" if total > 0 else "no residues"),
        StepStatus(2, "fchk → mol2", s2_done,
                   f"{mol2_ok}/{total} .mol2" if total > 0 else ""),
        StepStatus(3, "RESP 电荷", s3_done,
                   f"{chg_ok}/{total} .chg" if total > 0 else ""),
        StepStatus(4, "mol2+chg → itp+gro", s4_done, s4_detail),
        StepStatus(5, "生成主拓扑 + 修订 itp", s5_done,
                   f"{latest.name}/topol.top" if latest and s5_done else ""),
        StepStatus(6, "生成 MDP", s6_done, f"{mdp_count}/3 .mdp"),
        StepStatus(7, "Packmol 盒子", s7_done,
                   f"{latest.name}/model.pdb" if latest and s7_done else ""),
        StepStatus(8, "MD 运行就绪", s8_done, s8_detail),
    ]

    running, lock_info = check_lock()

    return PipelineStatus(steps=steps, running=running, lock_info=lock_info)


def format_status(status: PipelineStatus = None) -> str:
    """格式化为终端友好的进度表。"""
    if status is None:
        status = get_status()

    lines = []
    lines.append(" Pipeline Progress")
    lines.append(" ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    bar_width = 20
    done = status.done_count
    total = status.total
    filled = int(bar_width * done / total) if total > 0 else 0
    bar = "█" * filled + "░" * (bar_width - filled)
    lines.append(f" [{bar}] {done}/{total}")

    for s in status.steps:
        if s.done:
            icon = "✅"
            state = "done"
        else:
            icon = "⬚ "
            state = "pending"
        detail = f"  → {s.detail}" if s.detail else ""
        lines.append(f"  {icon} [{s.index}/{total}] {s.label:<24s} {state:<8s}{detail}")

    lines.append(" ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    if status.running:
        lines.append(f" 🔒 Running: {status.lock_info}")
    elif status.lock_info:
        lines.append(f" ⚠  {status.lock_info}")
    else:
        lines.append(" ⬚  Idle (no running instance)")

    if not status.running and done == 0:
        lines.append("")
        lines.append(" 💡 Run: python3 run_pipeline.py")

    lines.append(" ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    return "\n".join(lines)


# ============================================================
# CLI
# ============================================================

if __name__ == "__main__":
    print(format_status())
