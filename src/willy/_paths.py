"""
_paths.py
=========
统一路径解析 —— 所有模块通过此模块定位项目根目录。

优先级: WILLY_ROOT 环境变量 > MDAUTO_ROOT 环境变量 > 包自身位置推断。
"""

import os
from pathlib import Path


class ProjectRootError(RuntimeError):
    """The installation root cannot be determined safely."""


def get_project_root() -> Path:
    """返回项目根目录的绝对路径。"""
    for env_var in ("WILLY_ROOT", "MDAUTO_ROOT"):
        raw_root = os.environ.get(env_var, "").strip()
        if not raw_root:
            continue
        candidate = Path(raw_root).expanduser().resolve()
        if not candidate.is_dir():
            raise ProjectRootError(f"{env_var} 不是可用目录: {candidate}")
        return candidate

    # _paths.py lives at <root>/src/willy/_paths.py in the source distribution.
    package_dir = Path(__file__).resolve().parent
    project_dir = package_dir.parent.parent
    if (project_dir / "src" / "willy" / "_paths.py").is_file():
        return project_dir
    raise ProjectRootError("无法确定项目根目录；请设置 WILLY_ROOT")
