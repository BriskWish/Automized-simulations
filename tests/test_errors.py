"""
test_errors.py —— ErrorKind / StepError / StepResult / DiagnosisResult / RetryContext 测试。

覆盖所有错误类型、属性、边界情况。
"""

import json
from pathlib import Path

import pytest
from willy.errors import (
    ErrorKind, StepError, StepResult, DiagnosisResult, RetryContext,
    _RETRYABLE, _ROLLBACK_REQUIRED,
)


# ============================================================
# ErrorKind 枚举
# ============================================================

class TestErrorKind:
    """ErrorKind 枚举的完整测试。"""

    def test_all_kinds_have_unique_values(self):
        """所有 ErrorKind 成员的值必须唯一。"""
        values = [e.value for e in ErrorKind]
        assert len(values) == len(set(values)), f"重复值: {values}"

    def test_retryable_property_exists_for_all(self):
        """每个 ErrorKind 都应有 retryable 属性。"""
        for kind in ErrorKind:
            assert isinstance(kind.retryable, bool), f"{kind} 缺少 retryable"

    def test_rollback_required_property_exists_for_all(self):
        """每个 ErrorKind 都应有 rollback_required 属性。"""
        for kind in ErrorKind:
            assert isinstance(kind.rollback_required, bool), f"{kind} 缺少 rollback_required"

    # ---- 可重试错误 ----

    @pytest.mark.parametrize("kind", [
        ErrorKind.SCF_NOT_CONVERGED,
        ErrorKind.GEOM_NOT_CONVERGED,
        ErrorKind.GAUSSIAN_CRASH,
        ErrorKind.ORCA_CRASH,
        ErrorKind.FORMCHK_FAILED,
        ErrorKind.GROMPP_FAILED,
        ErrorKind.MDRUN_FAILED,
        ErrorKind.PACKMOL_FAILED,
        ErrorKind.EM_NOT_CONVERGED,
        ErrorKind.EQ_NOT_CONVERGED,
        ErrorKind.TIMEOUT,
        ErrorKind.SOBTOP_FAILED,
        ErrorKind.LIGPARGEN_FAILED,
        ErrorKind.UNKNOWN,
    ])
    def test_retryable_errors(self, kind):
        """可重试的错误应返回 retryable=True。"""
        assert kind.retryable is True, f"{kind} 应为可重试"

    @pytest.mark.parametrize("kind", [
        ErrorKind.DEPENDENCY_MISSING,
        ErrorKind.DEPENDENCY_NO_EXEC,
        ErrorKind.RESP_FAILED,
        ErrorKind.FILE_NOT_FOUND,
        ErrorKind.CONFIG_INVALID,
        # 注意：SOBTOP_EXIT_24 和 LOCK_CONFLICT 未在 _RETRYABLE 中
    ])
    def test_non_retryable_errors(self, kind):
        """不可自动修复的错误应返回 retryable=False。"""
        assert kind.retryable is False, f"{kind} 不应为可重试"

    # ---- 需要回滚的错误 ----

    @pytest.mark.parametrize("kind", [
        ErrorKind.GROMPP_FAILED,
        ErrorKind.MDRUN_FAILED,
        ErrorKind.FORMCHK_FAILED,
        ErrorKind.SOBTOP_FAILED,
        ErrorKind.LIGPARGEN_FAILED,
        ErrorKind.ORCA_CRASH,
        ErrorKind.GAUSSIAN_CRASH,
    ])
    def test_rollback_required_errors(self, kind):
        """回滚类错误应返回 rollback_required=True。"""
        assert kind.rollback_required is True, f"{kind} 应需要回滚"

    @pytest.mark.parametrize("kind", [
        ErrorKind.SCF_NOT_CONVERGED,
        ErrorKind.GEOM_NOT_CONVERGED,
        ErrorKind.RESP_FAILED,
        ErrorKind.EM_NOT_CONVERGED,
        ErrorKind.EQ_NOT_CONVERGED,
        ErrorKind.TIMEOUT,
    ])
    def test_no_rollback_errors(self, kind):
        """非回滚类错误应返回 rollback_required=False。"""
        assert kind.rollback_required is False, f"{kind} 不应需要回滚"

    # ---- 枚举完备性检查 ----

    def test_retryable_set_matches_all_retryable(self):
        """_RETRYABLE 集合中不应有未知成员。"""
        for kind in _RETRYABLE:
            assert kind in ErrorKind, f"未知 ErrorKind 在 _RETRYABLE 中: {kind}"

    def test_rollback_set_matches_all_rollback(self):
        """_ROLLBACK_REQUIRED 集合中不应有未知成员。"""
        for kind in _ROLLBACK_REQUIRED:
            assert kind in ErrorKind, f"未知 ErrorKind 在 _ROLLBACK_REQUIRED 中: {kind}"

    def test_unused_error_kinds(self):
        """
        BUG: SOBTOP_EXIT_24 和 LOCK_CONFLICT 在 ErrorKind 中定义，
        但在整个代码库中从未引用或抛出。
        """
        unused = {ErrorKind.SOBTOP_EXIT_24, ErrorKind.LOCK_CONFLICT}
        # 检查它们确实存在于枚举中
        for kind in unused:
            assert kind in ErrorKind
        # 检查它们不在 _RETRYABLE 或 _ROLLBACK_REQUIRED 中
        for kind in unused:
            assert kind not in _RETRYABLE
            assert kind not in _ROLLBACK_REQUIRED
        # SOBTOP_EXIT_24 实际上可能是可重试的（已知的 Fortran 清理错误）
        assert not ErrorKind.SOBTOP_EXIT_24.retryable, \
            "SOBTOP_EXIT_24 可能应为 retryable=True"


# ============================================================
# StepError
# ============================================================

class TestStepError:
    """StepError 数据类测试。"""

    def test_minimal_construction(self):
        """仅 kind 必填。"""
        e = StepError(kind=ErrorKind.UNKNOWN)
        assert e.kind == ErrorKind.UNKNOWN
        assert e.message == ""
        assert e.raw_output == ""
        assert e.hint == ""

    def test_full_construction(self):
        """所有字段均应正确存储。"""
        e = StepError(
            kind=ErrorKind.SCF_NOT_CONVERGED,
            message="SCF 在 128 次迭代后未收敛",
            raw_output="Cycle 128: E= -1234.5678 deltaE= 0.0012",
            hint="尝试添加 scf=xqc 或增加基组",
        )
        assert e.kind == ErrorKind.SCF_NOT_CONVERGED
        assert "128 次迭代" in e.message
        assert "deltaE" in e.raw_output
        assert "scf=xqc" in e.hint

    def test_raw_output_truncation(self):
        """raw_output 可接受长字符串（截断由调用者负责）。"""
        long_output = "x" * 10000
        e = StepError(kind=ErrorKind.UNKNOWN, raw_output=long_output)
        assert len(e.raw_output) == 10000


# ============================================================
# StepResult
# ============================================================

class TestStepResult:
    """StepResult 数据类测试。"""

    def test_success_result(self):
        sr = StepResult(step_name="struct_maker", step_index=1, success=True,
                        outputs={"fchk": "/tmp/LiTFSI.fchk"},
                        artifacts=["/tmp/LiTFSI.fchk", "/tmp/LiTFSI.log"],
                        duration_s=120.5)
        assert sr.success is True
        assert sr.error is None
        assert sr.escalated is False
        assert sr.outputs["fchk"] == "/tmp/LiTFSI.fchk"
        assert len(sr.artifacts) == 2

    def test_failure_result(self):
        err = StepError(kind=ErrorKind.GAUSSIAN_CRASH, message="g16 崩溃")
        sr = StepResult(step_name="struct_maker", step_index=1, success=False, error=err)
        assert sr.success is False
        assert sr.error.kind == ErrorKind.GAUSSIAN_CRASH
        assert sr.escalated is False

    def test_escalated_result(self):
        """升级结果应同时设置 success=False 和 escalated=True。"""
        sr = StepResult(step_name="struct_maker", step_index=1, success=False,
                        escalated=True,
                        extra={"escalation": {"layer": "quantum"}})
        assert sr.success is False
        assert sr.escalated is True
        assert sr.extra["escalation"]["layer"] == "quantum"

    def test_defaults(self):
        """默认值应与文档一致。"""
        sr = StepResult(step_name="test", step_index=0, success=True)
        assert sr.error is None
        assert sr.outputs == {}
        assert sr.artifacts == []
        assert sr.duration_s == 0.0
        assert sr.extra == {}
        assert sr.escalated is False

    def test_to_dict_preserves_protocol_fields_and_is_json_safe(self):
        sr = StepResult(
            step_name="prod", step_index=10, success=False,
            error=StepError(kind=ErrorKind.MDRUN_FAILED, message="MD failed"),
            outputs={"trajectory": Path("/tmp/prod.xtc")},
            artifacts=[Path("/tmp/prod.log")],
            extra={"retry": ("append",), "work_dir": Path("/tmp/run")},
            escalated=True,
        )

        data = sr.to_dict()

        assert data["_step_result"] is True
        assert data["step_index"] == 10
        assert data["error_kind"] == "mdrun_failed"
        assert data["outputs"] == {"trajectory": "/tmp/prod.xtc"}
        assert data["artifacts"] == ["/tmp/prod.log"]
        assert data["extra"] == {"retry": ["append"], "work_dir": "/tmp/run"}
        assert data["escalated"] is True
        json.dumps(data)


# ============================================================
# DiagnosisResult
# ============================================================

class TestDiagnosisResult:
    """DiagnosisResult 数据类及 to_dict() 测试。"""

    def test_to_dict_marker(self):
        """to_dict() 必须包含 _diagnosis=True 标记。"""
        dr = DiagnosisResult(source="quantum", severity="error",
                             issues=["SCF 不收敛"], hint="添加 scf=xqc")
        d = dr.to_dict()
        assert d["_diagnosis"] is True
        assert d["source"] == "quantum"
        assert d["severity"] == "error"
        assert d["issues"] == ["SCF 不收敛"]
        assert d["evidence"] == []
        assert d["hint"] == "添加 scf=xqc"
        assert d["extra"] == {}

    def test_default_severity(self):
        """默认严重级别应为 'info'。"""
        dr = DiagnosisResult(source="simulation")
        assert dr.severity == "info"

    def test_all_severity_levels(self):
        """所有严重级别均应支持。"""
        for sev in ["info", "warning", "error", "fatal"]:
            dr = DiagnosisResult(source="quantum", severity=sev)
            assert dr.to_dict()["severity"] == sev

    def test_extra_fields_preserved(self):
        """extra 字段应在 to_dict() 中完整保留。"""
        dr = DiagnosisResult(source="topology", extra={
            "atomtype_conflicts": ["CX", "NX"],
            "itp_missing": ["EMC.itp"],
        })
        d = dr.to_dict()
        assert d["extra"]["atomtype_conflicts"] == ["CX", "NX"]
        assert d["extra"]["itp_missing"] == ["EMC.itp"]

    def test_empty_issues_and_evidence(self):
        """issues 和 evidence 默认为空列表。"""
        dr = DiagnosisResult(source="config")
        d = dr.to_dict()
        assert d["issues"] == []
        assert d["evidence"] == []


# ============================================================
# RetryContext
# ============================================================

class TestRetryContext:
    """RetryContext 数据类测试。"""

    def test_not_exhausted_initially(self):
        ctx = RetryContext(layer="quantum", step_name="struct_maker",
                           error_kind=ErrorKind.SCF_NOT_CONVERGED,
                           max_attempts=5)
        assert ctx.attempts == 0
        assert ctx.exhausted is False

    def test_exhausted_when_attempts_equal_max(self):
        ctx = RetryContext(layer="quantum", step_name="struct_maker",
                           error_kind=ErrorKind.SCF_NOT_CONVERGED,
                           attempts=3, max_attempts=3)
        assert ctx.exhausted is True

    def test_exhausted_when_attempts_exceed_max(self):
        """即使 attempts > max_attempts 也应返回 True。"""
        ctx = RetryContext(layer="quantum", step_name="struct_maker",
                           error_kind=ErrorKind.SCF_NOT_CONVERGED,
                           attempts=5, max_attempts=3)
        assert ctx.exhausted is True

    def test_actions_tried_accumulation(self):
        ctx = RetryContext(layer="topology", step_name="topo_gaff",
                           error_kind=ErrorKind.SOBTOP_FAILED)
        ctx.actions_tried.append("retry sobtop with gaff=2")
        ctx.actions_tried.append("fallback to ligpargen")
        assert len(ctx.actions_tried) == 2
        assert "ligpargen" in ctx.actions_tried[1]

    def test_max_attempts_default(self):
        """默认 max_attempts 应为 3。"""
        ctx = RetryContext(layer="simulation", step_name="mdp",
                           error_kind=ErrorKind.GROMPP_FAILED)
        assert ctx.max_attempts == 3

    def test_last_raw_output_default(self):
        ctx = RetryContext(layer="quantum", step_name="struct_maker",
                           error_kind=ErrorKind.UNKNOWN)
        assert ctx.last_raw_output == ""


# ============================================================
# 错误传播场景
# ============================================================

class TestErrorPropagation:
    """测试错误在流水线步骤之间的传播。"""

    def test_step_error_from_result_extraction(self, make_step_result):
        """从 StepResult 中提取 StepError 应保持完整性。"""
        sr = make_step_result(
            success=False, step_name="chg_maker",
            error_kind=ErrorKind.RESP_FAILED,
            error_message="Multiwfn RESP 计算失败",
            raw_output="Error in module ESP",
            hint="检查 .chg 文件格式",
        )
        assert sr.error is not None
        assert sr.error.kind == ErrorKind.RESP_FAILED
        assert sr.error.message == "Multiwfn RESP 计算失败"

    def test_chain_of_failures(self, make_step_result):
        """多个步骤的失败链应保留各个错误。"""
        results = [
            make_step_result(success=True, step_name="struct_maker", step_index=1),
            make_step_result(success=False, step_name="chg_maker", step_index=3,
                             error_kind=ErrorKind.RESP_FAILED,
                             error_message="电荷拟合失败"),
            make_step_result(success=False, step_name="topo_gaff", step_index=4,
                             error_kind=ErrorKind.SOBTOP_FAILED,
                             error_message="Sobtop 退出码 24"),
        ]
        failures = [r for r in results if not r.success]
        assert len(failures) == 2
        assert failures[0].error.kind == ErrorKind.RESP_FAILED
        assert failures[1].error.kind == ErrorKind.SOBTOP_FAILED

    def test_error_kind_determines_agent_behavior(self):
        """
        Agent 行为应基于 ErrorKind，不应解析裸字符串。

        此测试验证分区是正确的。
        """
        # 应触发重试的错误
        assert ErrorKind.SCF_NOT_CONVERGED.retryable
        # 不应触发重试的错误
        assert not ErrorKind.DEPENDENCY_MISSING.retryable
        # 需要回滚的错误
        assert ErrorKind.GROMPP_FAILED.rollback_required
        # 不需要回滚的错误
        assert not ErrorKind.EM_NOT_CONVERGED.rollback_required
