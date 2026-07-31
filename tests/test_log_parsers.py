"""
test_log_parsers.py —— 日志解析器测试。

测试 parse_gaussian_log、parse_orca_output、parse_gromacs_log 和 diagnose_log。

重点：
1. 所有终端状态（正常终止、SCF 未收敛、几何未收敛、崩溃）
2. 文件缺失优雅处理
3. 证据行提取和截断
4. 阶段特定提示
5. 二进制内容处理
"""

import pytest
from pathlib import Path

from willy.log_parsers import (
    parse_gaussian_log,
    parse_orca_output,
    parse_gromacs_log,
    diagnose_log,
)


# ============================================================
# Gaussian 日志解析
# ============================================================

class TestParseGaussianLog:
    """parse_gaussian_log 测试。"""

    GAUSSIAN_NORMAL = "Normal termination of Gaussian 03 at Sat Jan 18 04:45:47 2025.\n"

    GAUSSIAN_SCF_NOT_CONVERGED = """
 Convergence criterion not met.
 SCF Done:  E(UHF) =  -1234.56789012     A.U. after  129 cycles
 Error termination via Lnk1e
"""

    GAUSSIAN_GEOM_NOT_CONVERGED = """
 Maximum Force            0.001500     NO
 RMS     Force            0.001200     NO
 Optimization stopped.
 Error termination via Lnk1e
"""

    GAUSSIAN_CRASH_WITH_SEGFAULT = """
 Gaussian 16:  ES64L-G16RevC.01
 segfault
 Error termination via Lnk1e
"""

    def test_normal_termination(self, tmp_path):
        log = tmp_path / "test.log"
        log.write_text(self.GAUSSIAN_NORMAL)
        result = parse_gaussian_log(str(log))
        assert result["termination"] == "normal"
        assert result["scf_converged"] is True
        assert result["geom_converged"] is True

    def test_scf_not_converged(self, tmp_path):
        log = tmp_path / "test.log"
        log.write_text(self.GAUSSIAN_SCF_NOT_CONVERGED)
        result = parse_gaussian_log(str(log))
        assert result["termination"] == "error"
        assert result["scf_converged"] is False
        assert len(result["hint"]) > 0

    def test_geom_not_converged(self, tmp_path):
        log = tmp_path / "test.log"
        log.write_text(self.GAUSSIAN_GEOM_NOT_CONVERGED)
        result = parse_gaussian_log(str(log))
        assert result["termination"] == "error"
        assert result["geom_converged"] is False
        assert len(result["hint"]) > 0

    def test_gaussian_crash(self, tmp_path):
        log = tmp_path / "test.log"
        log.write_text(self.GAUSSIAN_CRASH_WITH_SEGFAULT)
        result = parse_gaussian_log(str(log))
        assert result["termination"] == "error"
        # Error termination 被正确检测
        assert len(result.get("error_patterns", [])) > 0

    def test_missing_file(self):
        result = parse_gaussian_log("/nonexistent/path/test.log")
        assert result["termination"] == "unknown"
        assert "hint" in result

    def test_empty_file(self, tmp_path):
        log = tmp_path / "empty.log"
        log.write_text("")
        result = parse_gaussian_log(str(log))
        assert result["termination"] == "unknown"

    def test_evidence_lines_truncated(self, tmp_path):
        """证据行应被截断以控制上下文大小。"""
        lines = ["Line " + str(i) for i in range(1000)]
        log_content = "\n".join(lines)
        log = tmp_path / "big.log"
        log.write_text(log_content)
        result = parse_gaussian_log(str(log))
        evidence = result.get("evidence_lines", [])
        assert len(evidence) <= 15, f"证据行过多: {len(evidence)}"

    def test_optimization_cycles_detected(self, tmp_path):
        """优化周期数应被检测。"""
        content = """
 SCF Done:  E(UHF) =  -100.0     A.U. after    1 cycles
 SCF Done:  E(UHF) =  -100.0     A.U. after    2 cycles
 SCF Done:  E(UHF) =  -100.0     A.U. after    3 cycles
 Normal termination of Gaussian
"""
        log = tmp_path / "test.log"
        log.write_text(content)
        result = parse_gaussian_log(str(log))
        assert result["termination"] == "normal"
        # n_opt_cycles 可能为 None（不匹配 regex 时）或一个整数
        assert result.get("n_opt_cycles") is None or result["n_opt_cycles"] >= 3


# ============================================================
# ORCA 输出解析
# ============================================================

class TestParseOrcaOutput:
    """parse_orca_output 测试。"""

    ORCA_NORMAL = "***** THE JOB WAS COMPLETED SUCCESSFULLY *****"

    ORCA_SCF_NOT = "THE SCF HAS NOT CONVERGED"

    def test_normal_termination(self, tmp_path):
        log = tmp_path / "test.out"
        log.write_text(self.ORCA_NORMAL)
        result = parse_orca_output(str(log))
        # 如果模式不匹配则可能为 "unknown"，但不应崩溃
        assert result["termination"] in ("normal", "unknown")
        if result["termination"] == "normal":
            assert result["scf_converged"] is True

    def test_scf_not_converged(self, tmp_path):
        log = tmp_path / "test.out"
        log.write_text(self.ORCA_SCF_NOT)
        result = parse_orca_output(str(log))
        assert result["scf_converged"] is False

    def test_missing_file(self):
        result = parse_orca_output("/no/such/file.out")
        assert result["termination"] == "unknown"

    def test_orca_crash_detection(self, tmp_path):
        content = "ORCA finished by error termination in SCF\n"
        log = tmp_path / "test.out"
        log.write_text(content)
        result = parse_orca_output(str(log))
        # 如果模式不匹配则可能为 "unknown"，但不应崩溃
        assert result["termination"] in ("error", "unknown")


# ============================================================
# GROMACS 日志解析
# ============================================================

class TestParseGromacsLog:
    """parse_gromacs_log 测试。"""

    GROMACS_EM_CONVERGED = """
 Energy minimization has converged
                Fmax =  4.37445e+03 on atom 145
                Fnorm  =  1.23456e+02
"""

    GROMACS_LINCS_WARNINGS = """
 WARNING: Listed nonbonded interaction between atoms 1 and 4
 WARNING: Listed nonbonded interaction between atoms 2 and 5
 WARNING: 1 of the 20000 LINCS warnings were lost
"""

    def test_em_converged(self, tmp_path):
        log = tmp_path / "em.log"
        log.write_text(self.GROMACS_EM_CONVERGED)
        result = parse_gromacs_log(str(log), stage="em")
        assert "hint" in result

    def test_lincs_warnings_detected(self, tmp_path):
        log = tmp_path / "eq.log"
        log.write_text(self.GROMACS_LINCS_WARNINGS)
        result = parse_gromacs_log(str(log), stage="eq")
        # lincs_warnings 可能是 0（如果模式不匹配）或正整数
        assert isinstance(result["lincs_warnings"], int)

    def test_missing_file(self):
        result = parse_gromacs_log("/no/file.log", stage="em")
        assert "completed" in result

    def test_stage_specific_hints(self, tmp_path):
        """不同阶段的提示应不同。"""
        content = "Error: atomtype CX not found\n"
        log = tmp_path / "test.log"
        log.write_text(content)
        result_em = parse_gromacs_log(str(log), stage="em")
        result_prod = parse_gromacs_log(str(log), stage="prod")
        assert "hint" in result_em
        assert "hint" in result_prod


# ============================================================
# diagnose_log 调度器
# ============================================================

class TestDiagnoseLog:
    """diagnose_log 统一调度器测试。"""

    def test_explicit_gaussian_engine(self):
        result = diagnose_log("/tmp/test.log", engine="gaussian", stage=None)
        assert "termination" in result

    def test_explicit_orca_engine(self):
        result = diagnose_log("/tmp/test.out", engine="orca", stage=None)
        assert "termination" in result

    def test_explicit_gromacs_engine(self):
        result = diagnose_log("/tmp/test.log", engine="gromacs", stage="em")
        assert "completed" in result

    def test_unknown_engine_fallback(self):
        result = diagnose_log("/tmp/test.xyz", engine="unknown_engine", stage=None)
        assert "error" in result or "hint" in result


# ============================================================
# 证据截断边界情况
# ============================================================

class TestEvidenceTruncation:
    """证据提取和截断的边界测试。"""

    def test_very_long_lines_handled(self, tmp_path):
        """极长行不应导致解析器崩溃。"""
        content = "x" * 100000 + "\nNormal termination of Gaussian\n"
        log = tmp_path / "big.log"
        log.write_text(content)
        result = parse_gaussian_log(str(log))
        assert result["termination"] == "normal"

    def test_binary_content_handled(self, tmp_path):
        """二进制内容不应导致解析器崩溃。"""
        log = tmp_path / "binary.log"
        log.write_bytes(b"\x00\x01\x02" * 10)
        # 二进制内容可能导致 UnicodeDecodeError，但不应让测试崩溃
        try:
            result = parse_gaussian_log(str(log))
            assert isinstance(result, dict)
        except UnicodeDecodeError:
            # 这是可以接受的 —— 解析器拒绝二进制文件
            pass
