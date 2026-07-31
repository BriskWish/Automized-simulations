"""
prod.py
==========
生产 MD 执行器 —— grompp + mdrun，无后检查。

用法:
  CLI:  python3 -m willy.simulation.prod [work_dir]
  API:  from willy.simulation.prod import run_prod, ProdResult
"""

from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path

from willy._paths import get_project_root
from willy.simulation._gmx_utils import grompp_and_mdrun
from willy.errors import StepResult, StepError, ErrorKind

ROOT = get_project_root()
DEFAULT_WORK_DIR = ROOT / "md_run"


@dataclass
class ProdResult:
    started: bool
    tpr: str = ""


def run_prod(work_dir: str = None,
             mdp: str = None,
             conf: str = None,
             topol: str = "topol.top",
             extra_mdrun: list[str] | None = None,
             ) -> StepResult:
    """执行生产 MD。返回 StepResult 包装 ProdResult。"""
    cwd = Path(work_dir) if work_dir else DEFAULT_WORK_DIR
    if not cwd.is_absolute():
        cwd = ROOT / work_dir
    if not cwd.exists():
        return StepResult(
            step_name="prod", step_index=10, success=False,
            error=StepError(kind=ErrorKind.FILE_NOT_FOUND,
                            message=f"工作目录不存在: {cwd}"),
        )

    gmx_result = grompp_and_mdrun(
        stage="prod", cwd=cwd,
        mdp=mdp, conf=conf, topol=topol, extra_mdrun=extra_mdrun,
    )
    if not gmx_result.success:
        gmx_result.step_name = "prod"
        gmx_result.step_index = 10
        return gmx_result

    return StepResult(
        step_name="prod", step_index=10, success=True,
        outputs=gmx_result.outputs,
        artifacts=gmx_result.artifacts,
        duration_s=gmx_result.duration_s,
        extra={"prod_result": ProdResult(started=True, tpr=gmx_result.outputs.get("tpr", ""))},
    )


if __name__ == "__main__":
    import sys
    wd = sys.argv[1] if len(sys.argv) > 1 else str(DEFAULT_WORK_DIR)
    sr = run_prod(work_dir=wd)
    if sr.success:
        print(f"✅ PROD 已启动  tpr: {sr.outputs.get('tpr', '')}")
    else:
        print(f"❌ PROD 失败: {sr.error.message if sr.error else 'unknown'}")
        exit(1)
