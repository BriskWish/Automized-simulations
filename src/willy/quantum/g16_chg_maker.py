"""
chg_maker.py
============
自动调用 RESP_noopt.sh 生成 .chg 电荷文件。

入参单独封装，方便后续接入 AI 模块。
"""

from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, List
import subprocess
import re

from willy._paths import get_project_root
from willy.errors import StepResult, StepError, ErrorKind

# ── 外部依赖路径 ──
ROOT = get_project_root()
RESP_SCRIPT = ROOT / "RESP_noopt.sh"


# ============================================================
# 环境预检
# ============================================================

def check_env_ready() -> list[str]:
    """
    预检外部依赖是否就绪。委托到 env_checker。

    Returns:
        问题列表，空列表 = 一切正常。
    """
    from willy.env_checker import check_module
    issues = check_module("chg_maker").failed_strs()
    if not issues:
        print("[chg_maker] ✅ 环境预检通过")
    return issues


# ============================================================
# .gjf 解析
# ============================================================

def parse_gjf(gjf_path: str) -> dict:
    """
    从 .gjf 文件提取分子名、电荷、自旋多重度、溶剂。

    .gjf 格式示例:
        %chk=XXX.chk
        # opt freq b3lyp/6-311+g(d,p) scrf(solvent=water)

        MoleculeName

        -1 1
         N  0.0  0.0  0.0
         ...

    Returns:
        {"name": str, "charge": int, "spin": int, "solvent": str|None}
    """
    with open(gjf_path) as f:
        lines = f.readlines()

    result = {
        "name": Path(gjf_path).stem,
        "charge": 0,
        "spin": 1,
        "solvent": None,
    }

    # 分子名 —— 第 4 行（1-indexed）
    if len(lines) >= 4:
        result["name"] = lines[3].strip()

    # 溶剂 —— 从 route card 中提取 scrf(solvent=XXX)
    if len(lines) >= 2:
        m = re.search(r'scrf\s*\(\s*solvent\s*=\s*(\w+)', lines[1], re.IGNORECASE)
        if m:
            result["solvent"] = m.group(1)

    # 电荷 & 自旋 —— 标题和坐标之间的 "N N" 行
    for i, line in enumerate(lines):
        parts = line.strip().split()
        if len(parts) == 2:
            try:
                chg = int(parts[0])
                spin = int(parts[1])
                # 确认下一行是坐标行（4 列以上）
                if i + 1 < len(lines) and len(lines[i + 1].strip().split()) >= 4:
                    result["charge"] = chg
                    result["spin"] = spin
                    break
            except ValueError:
                continue

    return result


# ============================================================
# 入参层
# ============================================================

@dataclass
class ChgConfig:
    """
    chg 生成的入参配置。

    溶剂优先级: solvent 参数 > .gjf route card > 默认 acetone
    """
    struct_dir: str = "struct"           # 结构文件所在目录
    solvent: Optional[str] = None        # 统一溶剂名；None 则每个分子分别从 .gjf 取
    default_solvent: str = "acetone"     # 当 .gjf 中也没有溶剂时的兜底


# ============================================================
# 核心逻辑
# ============================================================

def make_chg_one(gjf_path: str,
                 solvent: Optional[str] = None,
                 default_solvent: str = "acetone",
                 ) -> StepResult:
    """
    对单个 .gjf 文件调用 RESP_noopt.sh 生成 .chg。

    Args:
        gjf_path: .gjf 文件路径
        solvent: 显式溶剂；None 则从 .gjf 解析 → default_solvent
        default_solvent: 兜底溶剂

    Returns:
        StepResult (success=True 时 outputs["chg"] = 路径)
    """
    import time as _time
    _start = _time.time()
    info = parse_gjf(gjf_path)
    struct_dir = Path(gjf_path).parent
    name = info["name"]

    # 溶剂优先级
    final_solvent = solvent or info["solvent"] or default_solvent

    print(f"[chg_maker] {name}: charge={info['charge']}  "
          f"spin={info['spin']}  solvent={final_solvent}")

    # 调用 RESP_noopt.sh <结构文件> <电荷> <自旋> <溶剂>
    cmd = [
        "bash", str(RESP_SCRIPT),
        str(Path(gjf_path).resolve()),
        str(info["charge"]),
        str(info["spin"]),
        final_solvent,
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(struct_dir),
            timeout=600,
        )
    except subprocess.TimeoutExpired:
        return StepResult(
            step_name="chg_maker", step_index=3, success=False,
            error=StepError(kind=ErrorKind.TIMEOUT,
                            message=f"{name}: RESP 计算超时 (600s)",
                            hint="减少基组大小或检查 Multiwfn/Gaussian 是否正常"),
            duration_s=_time.time() - _start,
        )

    chg_path = struct_dir / f"{name}.chg"

    if chg_path.exists():
        duration = _time.time() - _start
        print(f"[chg_maker] ✅ {name}: {chg_path.name}  ({duration:.1f}s)")
        return StepResult(
            step_name="chg_maker", step_index=3, success=True,
            outputs={"chg": str(chg_path)},
            artifacts=[str(chg_path)],
            duration_s=duration,
        )

    # 失败诊断
    print(f"[chg_maker] ❌ {name} 失败 (rc={result.returncode})")
    stdout_tail = result.stdout.strip().split("\n")[-10:]
    stderr_tail = result.stderr.strip().split("\n")[-10:]
    raw_output = "\n".join(stdout_tail + stderr_tail)
    for line in stdout_tail:
        print(f"  [stdout] {line}")
    for line in stderr_tail:
        print(f"  [stderr] {line}")

    return StepResult(
        step_name="chg_maker", step_index=3, success=False,
        error=StepError(kind=ErrorKind.RESP_FAILED,
                        message=f"{name}: RESP 电荷计算失败",
                        raw_output=raw_output[-500:],
                        hint=f"检查 Gaussian/Multiwfn 输出，尝试更换溶剂（当前={final_solvent}）"),
        duration_s=_time.time() - _start,
    )


def batch_make_chg(struct_dir: str = "struct",
                   solvent: Optional[str] = None,
                   default_solvent: str = "acetone",
                   ) -> List[StepResult]:
    """
    批量处理 struct_dir 下所有 .gjf 文件。

    Args:
        struct_dir: 结构文件目录
        solvent: 统一溶剂；None 则每个分子从 .gjf 取 → default_solvent
        default_solvent: 兜底溶剂

    Returns:
        List[StepResult] —— 每个分子一个结果（含成功和失败）
    """
    issues = check_env_ready()
    if issues:
        return [StepResult(
            step_name="chg_maker", step_index=3, success=False,
            error=StepError(kind=ErrorKind.DEPENDENCY_MISSING,
                            message="环境未就绪",
                            raw_output="\n".join(issues),
                            hint="安装缺失依赖后重试"),
        )]

    gjf_files = sorted(Path(struct_dir).glob("*.gjf"))
    if not gjf_files:
        print(f"[chg_maker] ⚠ {struct_dir}/ 下没有 .gjf 文件")
        return []

    results: List[StepResult] = []
    for gjf in gjf_files:
        sr = make_chg_one(str(gjf), solvent=solvent,
                          default_solvent=default_solvent)
        results.append(sr)

    ok = sum(1 for r in results if r.success)
    print(f"[chg_maker] 完成: {ok}/{len(gjf_files)} 个分子")
    return results


# ============================================================
# 快捷函数
# ============================================================

def make_chg_all(solvent: str = None) -> List[StepResult]:
    """快捷：处理 struct/ 下所有 .gjf"""
    return batch_make_chg("struct", solvent=solvent)


# ============================================================
# 测试入口 —— 先跑一个分子验证
# ============================================================

if __name__ == "__main__":
    import sys

    # 环境预检
    issues = check_env_ready()
    if issues:
        print("\n".join(issues))
        sys.exit(1)

    # 测试单个：取 struct/ 下第一个 .gjf
    test_file = sorted(Path("struct").glob("*.gjf"))[0]
    print(f"===== 测试: {test_file} =====")
    sr = make_chg_one(str(test_file), default_solvent="acetone")
    if sr.success:
        print(f"成功: {sr.outputs}")
    else:
        print(f"失败: {sr.error.kind.value} - {sr.error.message}")
