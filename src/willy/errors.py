"""
errors.py
=========
流水线错误分类与统一结果类型。

Agent 根据 ErrorKind 做决策，而不是解析裸字符串。
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional


class ErrorKind(Enum):
    """错误分类 —— Agent 决策的依据。"""
    # 环境问题（通常无法自动修复）
    DEPENDENCY_MISSING = "dependency_missing"
    DEPENDENCY_NO_EXEC = "dependency_no_exec"

    # 量子化学
    SCF_NOT_CONVERGED = "scf_not_converged"
    GEOM_NOT_CONVERGED = "geom_not_converged"
    GAUSSIAN_CRASH = "gaussian_crash"
    ORCA_CRASH = "orca_crash"
    FORMCHK_FAILED = "formchk_failed"

    # RESP / Multiwfn
    RESP_FAILED = "resp_failed"

    # Sobtop
    SOBTOP_FAILED = "sobtop_failed"
    SOBTOP_EXIT_24 = "sobtop_exit_24"      # 已知 Fortran 清理错误

    # LigParGen
    LIGPARGEN_FAILED = "ligpargen_failed"

    # GROMACS
    GROMPP_FAILED = "grompp_failed"
    MDRUN_FAILED = "mdrun_failed"
    EM_NOT_CONVERGED = "em_not_converged"
    EQ_NOT_CONVERGED = "eq_not_converged"

    # 通用
    FILE_NOT_FOUND = "file_not_found"
    TIMEOUT = "timeout"
    CONFIG_INVALID = "config_invalid"
    LOCK_CONFLICT = "lock_conflict"
    UNKNOWN = "unknown"

    @property
    def retryable(self) -> bool:
        """该错误是否可通过修改配置重试。"""
        return self in _RETRYABLE

    @property
    def rollback_required(self) -> bool:
        """该错误是否需要回滚上一步产物后重试。"""
        return self in _ROLLBACK_REQUIRED


_RETRYABLE = {
    ErrorKind.SCF_NOT_CONVERGED,
    ErrorKind.GEOM_NOT_CONVERGED,
    ErrorKind.GAUSSIAN_CRASH,
    ErrorKind.ORCA_CRASH,
    ErrorKind.FORMCHK_FAILED,
    ErrorKind.GROMPP_FAILED,
    ErrorKind.MDRUN_FAILED,
    ErrorKind.EM_NOT_CONVERGED,
    ErrorKind.EQ_NOT_CONVERGED,
    ErrorKind.TIMEOUT,
    ErrorKind.SOBTOP_FAILED,
    ErrorKind.LIGPARGEN_FAILED,
    ErrorKind.UNKNOWN,
}

_ROLLBACK_REQUIRED = {
    ErrorKind.GROMPP_FAILED,
    ErrorKind.MDRUN_FAILED,
    ErrorKind.FORMCHK_FAILED,
    ErrorKind.SOBTOP_FAILED,
    ErrorKind.LIGPARGEN_FAILED,
    ErrorKind.ORCA_CRASH,
    ErrorKind.GAUSSIAN_CRASH,
}


# ============================================================
# 统一结果类型
# ============================================================

@dataclass
class StepError:
    """结构化的步骤错误。"""
    kind: ErrorKind
    message: str = ""
    raw_output: str = ""         # 外部程序的原始输出（最后500字符）
    hint: str = ""               # Agent 修复建议


@dataclass
class StepResult:
    """所有流水线步骤的统一返回类型。"""
    step_name: str                            # "struct_maker", "chg_maker", ...
    step_index: int                           # 1-11
    success: bool
    error: Optional[StepError] = None
    outputs: dict[str, str] = field(default_factory=dict)  # {"fchk": "/path/to/Li.fchk", ...}
    artifacts: list[str] = field(default_factory=list)      # 生成的文件路径列表
    duration_s: float = 0.0
    extra: dict = field(default_factory=dict)  # 步骤特定的额外信息
    escalated: bool = False                    # 是否已升级到用户（Agent 放弃自助修复）


@dataclass
class RetryContext:
    """Agent 重试上下文 —— 记录一层内对某个 Step 的修复尝试。"""
    layer: str                                # "quantum" | "topology" | "simulation"
    step_name: str
    error_kind: ErrorKind
    attempts: int = 0
    max_attempts: int = 3
    actions_tried: list[str] = field(default_factory=list)  # 已尝试的修复动作描述
    last_raw_output: str = ""                                # 最后一次失败的原始输出尾部

    @property
    def exhausted(self) -> bool:
        return self.attempts >= self.max_attempts
