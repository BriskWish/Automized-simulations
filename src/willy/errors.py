"""
errors.py
=========
流水线错误分类与统一结果类型。

Agent 根据 ErrorKind 做决策，而不是解析裸字符串。
"""

from __future__ import annotations
from dataclasses import asdict, dataclass, field, is_dataclass
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

    # Topology assembly
    ATOMTYPE_CONFLICT = "atomtype_conflict"

    # GROMACS
    INPUT_CONTRACT = "input_contract"
    PACKMOL_FAILED = "packmol_failed"
    ENGINE_FAILURE = "engine_failure"
    NUMERICAL_INSTABILITY = "numerical_instability"
    EQUILIBRATION_FAILED = "equilibration_failed"
    RECOVERY_CONFLICT = "recovery_conflict"
    GROMPP_FAILED = "grompp_failed"
    MDRUN_FAILED = "mdrun_failed"
    EM_NOT_CONVERGED = "em_not_converged"
    EQ_NOT_CONVERGED = "eq_not_converged"
    POSTPROCESS_FAILED = "postprocess_failed"

    # 通用
    FILE_NOT_FOUND = "file_not_found"
    TIMEOUT = "timeout"
    RETRY_LIMIT_EXCEEDED = "retry_limit_exceeded"
    CONFIG_INVALID = "config_invalid"
    USER_CONFIRMATION_REQUIRED = "user_confirmation_required"
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


# Messages written to the public pipeline state must be stable, concise, and
# independent of external-program output.  The detailed ``StepError`` remains
# available to the internal LayerAgent only.
_PUBLIC_ERROR_REASONS = {
    ErrorKind.DEPENDENCY_MISSING: "运行依赖不可用",
    ErrorKind.DEPENDENCY_NO_EXEC: "运行依赖不可用",
    ErrorKind.SCF_NOT_CONVERGED: "SCF 未收敛",
    ErrorKind.GEOM_NOT_CONVERGED: "结构优化未收敛",
    ErrorKind.GAUSSIAN_CRASH: "G16 计算失败",
    ErrorKind.ORCA_CRASH: "ORCA 计算失败",
    ErrorKind.FORMCHK_FAILED: "结果格式转换失败",
    ErrorKind.RESP_FAILED: "RESP 电荷计算失败",
    ErrorKind.SOBTOP_FAILED: "Sobtop 拓扑参数化失败",
    ErrorKind.SOBTOP_EXIT_24: "Sobtop 拓扑参数化失败",
    ErrorKind.LIGPARGEN_FAILED: "LigParGen 拓扑参数化失败",
    ErrorKind.ATOMTYPE_CONFLICT: "拓扑参数冲突",
    ErrorKind.INPUT_CONTRACT: "模拟输入契约不满足",
    ErrorKind.PACKMOL_FAILED: "Packmol 建盒执行失败",
    ErrorKind.ENGINE_FAILURE: "GROMACS 引擎执行失败",
    ErrorKind.NUMERICAL_INSTABILITY: "模拟数值不稳定",
    ErrorKind.EQUILIBRATION_FAILED: "平衡验收未通过",
    ErrorKind.RECOVERY_CONFLICT: "恢复条件与原运行不一致",
    ErrorKind.GROMPP_FAILED: "GROMACS 输入预处理失败",
    ErrorKind.MDRUN_FAILED: "GROMACS 模拟运行失败",
    ErrorKind.EM_NOT_CONVERGED: "能量最小化未收敛",
    ErrorKind.EQ_NOT_CONVERGED: "NPT 平衡未达标",
    ErrorKind.POSTPROCESS_FAILED: "MD 后处理失败",
    ErrorKind.FILE_NOT_FOUND: "输入文件缺失",
    ErrorKind.TIMEOUT: "计算超时",
    ErrorKind.RETRY_LIMIT_EXCEEDED: "重试次数已用尽",
    ErrorKind.CONFIG_INVALID: "配置无效",
    ErrorKind.USER_CONFIRMATION_REQUIRED: "模拟协议变更等待用户确认",
    ErrorKind.LOCK_CONFLICT: "运行资源冲突",
    ErrorKind.UNKNOWN: "执行失败",
}


def public_error_reason(error: "StepError | ErrorKind | None") -> str:
    """Return a user-facing reason without exposing raw engine output."""
    if isinstance(error, StepError):
        kind = error.kind
    elif isinstance(error, ErrorKind):
        kind = error
    else:
        kind = ErrorKind.UNKNOWN
    return _PUBLIC_ERROR_REASONS.get(kind, "执行失败")


def public_error_summary(
    tool: str,
    operation: str,
    target: str = "当前体系",
    error: "StepError | ErrorKind | None" = None,
) -> str:
    """Build the only error shape that may be shown by the frontend."""
    safe_tool = str(tool or "流水线")
    safe_operation = str(operation or "执行")
    safe_target = str(target or "当前体系")
    if "/" in safe_target or "\\" in safe_target or ".." in safe_target:
        safe_target = "当前对象"
    return f"{safe_tool} {safe_operation}失败：{safe_target}\n原因：{public_error_reason(error)}"


_RETRYABLE = {
    ErrorKind.SCF_NOT_CONVERGED,
    ErrorKind.GEOM_NOT_CONVERGED,
    ErrorKind.GAUSSIAN_CRASH,
    ErrorKind.ORCA_CRASH,
    ErrorKind.FORMCHK_FAILED,
    ErrorKind.GROMPP_FAILED,
    ErrorKind.MDRUN_FAILED,
    ErrorKind.PACKMOL_FAILED,
    ErrorKind.ENGINE_FAILURE,
    ErrorKind.NUMERICAL_INSTABILITY,
    ErrorKind.EQUILIBRATION_FAILED,
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


def _json_safe(value: object) -> object:
    """将执行结果中的常见 Python 值转换为 JSON 兼容形式。"""
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return _json_safe(asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return value


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
    target_type: str = ""                       # 公开对象类型，如 molecule/stage/system
    target: str = ""                            # 公开对象标识，不含绝对路径

    def to_dict(self) -> dict:
        """转换为供 tool handler 和 Agent 消费的 JSON 兼容结果。"""
        error = self.error
        return {
            "_step_result": True,
            "success": self.success,
            "step_name": self.step_name,
            "step_index": self.step_index,
            "outputs": _json_safe(self.outputs),
            "artifacts": _json_safe(self.artifacts),
            "duration_s": self.duration_s,
            "error_message": error.message if error else "",
            "error_kind": error.kind.value if error else "",
            "hint": error.hint if error else "",
            "raw_output": error.raw_output if error else "",
            "extra": _json_safe(self.extra),
            "escalated": self.escalated,
            "target_type": self.target_type,
            "target": self.target,
        }


@dataclass
class DiagnosisResult:
    """统一的诊断结果 —— 所有 diagnose_* 工具的标准返回格式。

    遵循 _diagnosis 约定（镜像 _step_result 约定）。
    """
    source: str                              # "quantum"|"topology"|"simulation"|"config"
    severity: str = "info"                   # "info"|"warning"|"error"|"fatal"
    issues: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    hint: str = ""
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "_diagnosis": True,
            "source": self.source,
            "severity": self.severity,
            "issues": self.issues,
            "evidence": self.evidence,
            "hint": self.hint,
            "extra": self.extra,
        }


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
