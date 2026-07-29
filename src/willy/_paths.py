"""
_paths.py
=========
统一路径解析 —— 所有模块通过此模块定位项目根目录。

优先级: WILLY_ROOT 环境变量 > MDAUTO_ROOT 环境变量 > 包自身位置推断 > 当前工作目录
"""

import os
from pathlib import Path


def get_project_root() -> Path:
    """返回项目根目录的绝对路径。"""
    # 环境变量优先
    for env_var in ("WILLY_ROOT", "MDAUTO_ROOT"):
        if root := os.environ.get(env_var):
            return Path(root).resolve()
    # 从包位置推断: _paths.py 在 src/willy/_paths.py → 项目根在 ../../ 即 3 级上
    pkg_dir = Path(__file__).resolve().parent     # src/willy/
    proj_dir = pkg_dir.parent.parent               # 项目根
    if (proj_dir / "struct").exists():
        return proj_dir
    return Path.cwd().resolve()
