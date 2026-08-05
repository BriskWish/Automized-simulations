"""
test_env_checker.py —— 环境依赖检查器测试。

测试 check_all、check_module、ensure、EnvReport、DepResult。
"""

import pytest
from unittest.mock import patch, MagicMock

from willy.env_checker import (
    DepResult, EnvReport, _DEPENDENCIES,
    check_all, check_module, ensure,
)
from willy.step_registry import EXECUTION_MODULE_REGISTRY


# ============================================================
# DepResult
# ============================================================

class TestDepResult:
    """DepResult 数据类测试。"""

    def test_construction(self):
        dr = DepResult(
            name="g16", kind="binary", path="/usr/local/g16/g16",
            status="ok", needed_by=["struct_maker"],
            hint="source /opt/g16/bsd/g16.profile",
        )
        assert dr.name == "g16"
        assert dr.kind == "binary"
        assert dr.status == "ok"
        assert "struct_maker" in dr.needed_by

    def test_needed_by_is_list(self):
        """needed_by 应为字符串列表。"""
        dr = DepResult(name="g16", kind="binary", path="g16",
                       needed_by=["struct_maker", "chg_maker"])
        assert isinstance(dr.needed_by, list)
        assert len(dr.needed_by) == 2

    def test_default_status_is_ok(self):
        dr = DepResult(name="test", kind="binary", path="/tmp/x",
                       needed_by=["test"])
        assert dr.status == "ok"

    def test_status_values(self):
        for status in ["ok", "missing", "no_exec"]:
            dr = DepResult(name="test", kind="binary", path="/tmp/x",
                           status=status, needed_by=["test"])
            assert dr.status == status


# ============================================================
# EnvReport
# ============================================================

class TestEnvReport:
    """EnvReport 数据类测试。"""

    def test_empty_report(self):
        report = EnvReport(results=[])
        assert report.results == []
        assert report.failed() == []
        assert report.failed_strs() == []

    def test_all_ok(self):
        report = EnvReport(results=[
            DepResult(name="a", kind="binary", path="/usr/bin/a",
                      status="ok", needed_by=["test"]),
            DepResult(name="b", kind="binary", path="/usr/bin/b",
                      status="ok", needed_by=["test"]),
        ])
        assert report.failed() == []
        assert report.is_ok("test")

    def test_some_missing(self):
        report = EnvReport(results=[
            DepResult(name="a", kind="binary", path="", status="missing",
                      needed_by=["test"], hint="安装 a"),
            DepResult(name="b", kind="binary", path="/usr/bin/b",
                      status="ok", needed_by=["test"]),
        ])
        failed = report.failed()
        assert len(failed) == 1
        assert failed[0].name == "a"

    def test_failed_strs_format(self):
        """failed_strs 应为人类可读的字符串。"""
        report = EnvReport(results=[
            DepResult(name="g16", kind="binary", path="g16",
                      status="missing", needed_by=["struct_maker"],
                      hint="安装 Gaussian16"),
        ])
        strs = report.failed_strs()
        assert len(strs) >= 1
        assert "g16" in strs[0]

    def test_is_ok_with_module_filter(self):
        """is_ok 应按模块过滤。"""
        report = EnvReport(results=[
            DepResult(name="a", kind="binary", path="/bin/a",
                      status="ok", needed_by=["struct_maker"]),
            DepResult(name="b", kind="binary", path="",
                      status="missing", needed_by=["topo_gaff"],
                      hint="安装 b"),
        ])
        assert report.is_ok("struct_maker") is True
        assert report.is_ok("topo_gaff") is False

    def test_format_method(self):
        """format() 应返回人类可读的表格。"""
        report = EnvReport(results=[
            DepResult(name="test", kind="binary", path="/bin/test",
                      status="ok", needed_by=["test"]),
        ])
        formatted = report.format()
        assert "test" in formatted

    def test_failed_strs_handles_no_exec(self):
        """failed_strs 应处理 no_exec 状态。"""
        report = EnvReport(results=[
            DepResult(name="sobtop", kind="file_exec",
                      path="/tmp/sobtop", status="no_exec",
                      needed_by=["topo_gaff"],
                      hint="chmod +x sobtop"),
        ])
        strs = report.failed_strs()
        assert len(strs) >= 1
        assert "执行权限" in strs[0] or "chmod" in strs[0].lower() or "no_exec" in str(strs)


# ============================================================
# 依赖项定义
# ============================================================

class TestDependenciesDefinition:
    """全局依赖项定义测试。"""

    def test_dependencies_defined(self):
        assert len(_DEPENDENCIES) >= 17, f"期望 >=17 个依赖, 实际 {len(_DEPENDENCIES)}"

    def test_dependency_names_unique(self):
        names = [d.name for d in _DEPENDENCIES]
        duplicates = [n for n in names if names.count(n) > 1]
        assert not duplicates, f"重复的依赖名称: {set(duplicates)}"

    def test_dependencies_are_depresults(self):
        """_DEPENDENCIES 中的每个条目都应是 DepResult 实例。"""
        for dep in _DEPENDENCIES:
            assert isinstance(dep, DepResult), \
                f"{dep} 应为 DepResult 实例"

    def test_dependencies_have_kinds(self):
        for dep in _DEPENDENCIES:
            assert dep.kind in ("binary", "file", "file_exec", "envvar"), \
                f"{dep.name}: 无效的 kind '{dep.kind}'"

    def test_dependencies_grouped_by_module(self):
        """依赖项应按实际执行步骤的规范名称分组。"""
        modules = set()
        for d in _DEPENDENCIES:
            for m in d.needed_by:
                modules.add(m)
        assert {
            "struct_g16", "sp_g16", "struct_orca", "sp_orca", "chg_resp",
            "topo_gaff", "topo_opls", "box",
        } <= modules

    def test_dependency_ownership_is_derived_from_execution_registry(self):
        by_name = {dependency.name: dependency for dependency in _DEPENDENCIES}

        assert by_name["gmx"].needed_by == list(
            EXECUTION_MODULE_REGISTRY.modules_for_tool("gmx")
        )
        assert by_name["packmol"].needed_by == list(
            EXECUTION_MODULE_REGISTRY.modules_for_bundled_dependency("packmol")
        )
        assert all(
            EXECUTION_MODULE_REGISTRY.has_module(module_id)
            for dependency in _DEPENDENCIES
            for module_id in dependency.needed_by
        )

    def test_envvar_dependencies_exist(self):
        """BOSSdir 作为过渡期兼容变量保留在依赖报告中。"""
        envvar_deps = [d for d in _DEPENDENCIES if d.kind == "envvar"]
        assert [d.name for d in envvar_deps] == ["BOSSdir"]


# ============================================================
# check_all
# ============================================================

class TestCheckAll:
    """check_all 测试（依赖可能存在或不存在，主要测试不崩溃）。"""

    def test_check_all_returns_env_report(self):
        """check_all 应返回 EnvReport —— 不会崩溃。"""
        report = check_all()
        assert isinstance(report, EnvReport)
        assert hasattr(report, 'results')

    def test_check_all_has_results(self):
        report = check_all()
        assert len(report.results) >= 17  # 所有依赖项

    def test_check_all_result_statuses_valid(self):
        """每个结果的状态应为有效值。"""
        report = check_all()
        valid_statuses = {"ok", "missing", "no_exec"}
        for r in report.results:
            assert r.status in valid_statuses, \
                f"{r.name}: 无效状态 '{r.status}'"


# ============================================================
# check_module
# ============================================================

class TestCheckModule:
    """check_module 测试。"""

    def test_check_known_module(self):
        """已知模块应返回 EnvReport。"""
        report = check_module("struct_g16")
        assert isinstance(report, EnvReport)
        assert report.results

    def test_check_unknown_module(self):
        """未知模块应返回空报告。"""
        report = check_module("nonexistent_module_xyz")
        assert isinstance(report, EnvReport)

    def test_all_modules_work(self):
        """所有 referenced 模块应可检查且不崩溃。"""
        valid_modules = {
            "struct_g16", "sp_g16", "struct_orca", "sp_orca", "chg_resp",
            "topo_gaff", "topo_opls", "box",
        }
        for mod in valid_modules:
            report = check_module(mod)
            assert isinstance(report, EnvReport), f"{mod} 检查失败"

    def test_boss_default_does_not_mutate_process_environment(self, tmp_path, monkeypatch):
        import willy.env_registry as env_registry

        default = tmp_path / "boss"
        default.mkdir()
        executable = default / "BOSS"
        executable.write_text("#!/bin/sh\n")
        executable.chmod(0o755)
        monkeypatch.setattr(env_registry, "DEFAULT_BOSS_HOME", default)
        monkeypatch.delenv("BOSSdir", raising=False)
        monkeypatch.delenv("WILLY_BOSS_HOME", raising=False)

        resolved = env_registry.resolve_tool("boss")

        assert resolved.available
        assert resolved.home == default.resolve()
        assert "BOSSdir" not in env_registry.os.environ

    @patch("willy.env_checker.check_module")
    def test_gaussian_helper_uses_registered_step_name(self, mock_check):
        """兼容 helper 也必须查询 env_checker 中真实注册的步骤名。"""
        mock_check.return_value.failed_strs.return_value = []
        from willy.quantum.struct_g16 import check_env_ready

        assert check_env_ready() == []
        mock_check.assert_called_once_with("struct_g16")


# ============================================================
# ensure
# ============================================================

class TestEnsure:
    """ensure 运行时检查测试。"""

    @patch('willy.env_checker.check_module')
    def test_ensure_passes_when_all_ok(self, mock_check):
        """当所有依赖就绪时，ensure 不应抛出异常。"""
        mock_report = MagicMock()
        mock_report.failed.return_value = []
        mock_report.failed_strs.return_value = []
        mock_check.return_value = mock_report

        try:
            ensure("struct_g16")
        except RuntimeError:
            pytest.fail("ensure 在依赖就绪时不应抛出异常")

    @patch('willy.env_checker.check_module')
    def test_ensure_raises_runtime_error_when_missing(self, mock_check):
        """当依赖缺失时，ensure 应抛出 RuntimeError。"""
        mock_report = MagicMock()
        mock_report.is_ok.return_value = False  # <-- 关键：is_ok 返回 False
        mock_report.failed.return_value = [
            DepResult(name="g16", kind="binary", path="g16",
                      status="missing", needed_by=["struct_g16"],
                      hint="安装 g16"),
        ]
        mock_report.failed_strs.return_value = ["g16: missing (required by struct_g16)"]
        mock_check.return_value = mock_report

        with pytest.raises(RuntimeError) as exc_info:
            ensure("struct_g16")
        assert "struct_g16" in str(exc_info.value)


# ============================================================
# 边界情况
# ============================================================

class TestEnvCheckerEdgeCases:
    """边界情况。"""

    def test_envvar_dependency_handling(self):
        """旧 BOSSdir 显示名仍用于兼容既有预检界面。"""
        envvar_deps = [d for d in _DEPENDENCIES if d.kind == "envvar"]
        assert [d.name for d in envvar_deps] == ["BOSSdir"]

    def test_file_exec_dependency_handling(self):
        """file_exec 类型的依赖应存在。"""
        file_exec_deps = [d for d in _DEPENDENCIES if d.kind == "file_exec"]
        assert len(file_exec_deps) > 0

    def test_vendor_paths_exist_in_dependencies(self):
        """vendor/ 路径应存在于某些依赖中。"""
        vendor_deps = [d for d in _DEPENDENCIES
                       if "vendor" in str(d.path)]
        assert len(vendor_deps) > 0

    def test_check_all_does_not_crash(self):
        """check_all 即使依赖不可用也不应引发异常。"""
        try:
            report = check_all()
            assert isinstance(report, EnvReport)
        except Exception as e:
            pytest.fail(f"check_all 引发了异常: {e}")
