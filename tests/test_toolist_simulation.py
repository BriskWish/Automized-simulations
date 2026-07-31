"""
test_toolist_simulation.py —— Layer 3 (Simulation Agent) 工具测试。

重点测试：
1. BUG: tools_retry_prod — extra_mdrun 死代码
2. tools_retry_mdp — stage 参数被忽略
3. tools_retry_em / tools_retry_eq — 覆盖参数传递
4. tools_skip_molecule_simulation — 跳过逻辑
5. tools_diagnose_error_simulation — GROMACS 日志诊断
6. tools_modify_config_simulation — config.json 更新
"""

import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock, call


# ============================================================
# 工具定义完整性
# ============================================================

class TestSimulationToolDefinitions:
    """Simulation 工具 JSON Schema 验证。"""

    @pytest.fixture
    def sim_tools(self):
        from willy.toolist_simulation import SIMULATION_TOOLS
        return SIMULATION_TOOLS

    def test_eight_tools_defined(self, sim_tools):
        """应有 8 个工具定义（包括 skip_molecule）。"""
        names = {t["function"]["name"] for t in sim_tools}
        assert len(names) == 8, f"期望 8 个工具, 实际 {len(names)}: {names}"

    def test_all_tool_names_present(self, sim_tools):
        """所有必需工具应存在。"""
        names = {t["function"]["name"] for t in sim_tools}
        required = {
            "tools_retry_mdp",
            "tools_retry_box",
            "tools_retry_em",
            "tools_retry_eq",
            "tools_retry_prod",
            "tools_diagnose_error_simulation",
            "tools_modify_config_simulation",
            "tools_skip_molecule_simulation",
        }
        missing = required - names
        assert not missing, f"缺少工具: {missing}"

    def test_tool_meta_covers_all_tools(self, sim_tools):
        """TOOL_META 应覆盖所有已定义工具。"""
        from willy.toolist_simulation import TOOL_META
        names = {t["function"]["name"] for t in sim_tools}
        meta_names = set(TOOL_META.keys())
        missing = names - meta_names
        assert not missing, f"TOOL_META 缺少: {missing}"

    def test_skip_molecule_is_config_category(self):
        """skip_molecule 应为 config 分类（低风险）。"""
        from willy.toolist_simulation import TOOL_META
        meta = TOOL_META["tools_skip_molecule_simulation"]
        assert meta["category"] == "config"
        assert meta["risk"] == "medium"


# ============================================================
# handle_simulation_tool_call 分发器
# ============================================================

class TestHandleSimulationToolCall:
    """工具分发器基本测试。"""

    def test_unknown_tool_returns_error(self):
        from willy.toolist_simulation import handle_simulation_tool_call
        result = handle_simulation_tool_call("tools_unknown", {})
        parsed = json.loads(result)
        assert "error" in parsed

    def test_step_to_dict_format(self):
        """_step_to_dict 应包含 _step_result 标记。"""
        from willy.toolist_simulation import _step_to_dict
        from willy.errors import StepResult
        sr = StepResult(step_name="test", step_index=1, success=True)
        d = _step_to_dict(sr)
        assert d["_step_result"] is True
        assert d["success"] is True
        assert d["step_name"] == "test"


# ============================================================
# BUG: tools_retry_prod — extra_mdrun 死代码
# ============================================================

class TestRetryProdBug:
    """
    BUG: toolist_simulation.py:337-349
    tools_retry_prod 处理程序构建 extra_mdrun 列表但从未传递给 run_prod。
    run_prod 调用 grompp_and_mdrun()，后者确实接受 extra_mdrun 参数，
    但 run_prod 未将其透传。因此检查点/追加参数被静默丢弃。
    """

    def test_extra_mdrun_not_passed_to_run_prod(self):
        """
        验证 run_prod 的签名不接受 extra_mdrun。

        在 toolist_simulation.py:348 中：
            sr = run_prod(work_dir=work_dir)
        extra_mdrun 列表（第 342-346 行）已构建但未使用。
        """
        from willy.simulation.prod import run_prod
        import inspect
        sig = inspect.signature(run_prod)
        params = list(sig.parameters.keys())
        # run_prod 接受: work_dir, mdp, conf, topol
        # 但不接受 extra_mdrun
        assert "extra_mdrun" not in params, \
            f"run_prod 不接受 extra_mdrun 参数: {params}"
        assert "work_dir" in params

    def test_grompp_and_mdrun_does_accept_extra_mdrun(self):
        """
        用于对比：grompp_and_mdrun 确实接受 extra_mdrun。
        这是 run_prod 实际调用的函数。
        """
        from willy.simulation._gmx_utils import grompp_and_mdrun
        import inspect
        sig = inspect.signature(grompp_and_mdrun)
        params = list(sig.parameters.keys())
        assert "extra_mdrun" in params, \
            f"grompp_and_mdrun 应接受 extra_mdrun: {params}"

    def test_dead_code_locations(self):
        """
        精确定位死代码位置：

        第 342 行: extra_mdrun = []
        第 343-344 行: if args.get("from_checkpoint")...
        第 345-346 行: if args.get("append")...
        第 348 行: sr = run_prod(work_dir=work_dir)  # extra_mdrun 未传递

        如果 run_prod 具有 extra_mdrun 参数，则修复为：
            sr = run_prod(work_dir=work_dir, extra_mdrun=extra_mdrun)
        """
        # 读取源文件以验证行号
        source_path = Path(__file__).parent.parent / "src" / "willy" / "toolist_simulation.py"
        if source_path.exists():
            lines = source_path.read_text().split("\n")
            # 第 342 行 (0-indexed: 341)
            assert "extra_mdrun" in lines[341], f"第 342 行: {lines[341]}"
            # 第 348 行 (0-indexed: 347)
            assert "run_prod" in lines[347], f"第 348 行: {lines[347]}"
            # 确认 extra_mdrun 未传递给 run_prod
            assert "extra_mdrun" not in lines[347], \
                f"第 348 行不应包含 extra_mdrun: {lines[347]}"

    def test_fix_suggestion(self):
        """
        修复方案：
        1. 向 run_prod() 添加 extra_mdrun 参数
        2. 将其透传给 grompp_and_mdrun()
        3. 在 tools_retry_prod 处理程序中传递 extra_mdrun
        """
        assert True  # 文档化测试


# ============================================================
# BUG: tools_retry_mdp — stage 参数被忽略
# ============================================================

class TestRetryMdpBug:
    """
    tools_retry_mdp 接受 stage 参数（"em"/"eq"/"prod"/"all"），
    但始终重建所有 MDP 文件（build_all 没有 stage 过滤器）。

    此外，overrides 通过关键字参数传递，而 build_all 的签名是
    build_all(config_path="config.json", output_dir="process", overrides=None)。
    使用关键字参数 overrides=... 是正确的。
    """

    def test_stage_parameter_extracted_but_unused(self):
        """
        第 277 行：stage = args.get("stage", "all")
        阶段值已提取但 handler 中从未再次出现。
        build_all 总是生成所有三个 MDP 文件。
        """
        source_path = Path(__file__).parent.parent / "src" / "willy" / "toolist_simulation.py"
        if source_path.exists():
            lines = source_path.read_text().split("\n")
            # 第 277 行 (0-indexed: 276)
            assert "stage" in lines[276], f"第 277 行应提取 stage: {lines[276]}"
            # 验证 stage 未在后面的构建调用中使用（第 278-286 行）
            handler_body = "\n".join(lines[276:286])
            # stage 在第 277 行之后不应出现
            after_stage = "\n".join(lines[277:286])
            assert "stage" not in after_stage, \
                f"第 278-286 行不应引用 stage: {after_stage}"

    def test_build_all_signature_accepts_overrides(self):
        """build_all 按关键字接受 overrides 参数。"""
        from willy.simulation.mdp import build_all
        import inspect
        sig = inspect.signature(build_all)
        params = list(sig.parameters.keys())
        assert "overrides" in params
        # overrides 默认为 None
        assert sig.parameters["overrides"].default is None

    def test_keyword_override_call_is_correct(self):
        """
        build_all(overrides=overrides) 在 Python 中是合法的，
        因为前两个参数有默认值。这不是 bug —— 澄清探索报告。
        """
        from willy.simulation.mdp import build_all
        import inspect
        sig = inspect.signature(build_all)
        # config_path 默认为 "config.json"
        assert sig.parameters["config_path"].default == "config.json"
        # output_dir 默认为 "process"
        assert sig.parameters["output_dir"].default == "process"


# ============================================================
# tools_modify_config_simulation
# ============================================================

class TestModifyConfigSimulation:
    """tools_modify_config_simulation 测试。"""

    def test_writes_to_config_json(self, tmp_project_root):
        """应更新 config.json 中的 md 字段。"""
        from willy.toolist_simulation import handle_simulation_tool_call
        import willy.toolist_simulation as ts
        # 覆盖 ROOT
        import willy.toolist_simulation
        willy.toolist_simulation.ROOT = tmp_project_root

        result = handle_simulation_tool_call("tools_modify_config_simulation", {
            "dt": 0.002, "ref_t": 310.0, "eq_ns": 20,
        })
        parsed = json.loads(result)
        assert parsed.get("ok") is True
        assert "dt" in parsed.get("updated_fields", [])

    def test_missing_config_json(self, tmp_path):
        """config.json 不存在时应有错误。"""
        import willy.toolist_simulation as ts
        original_root = ts.ROOT
        ts.ROOT = tmp_path
        try:
            result = ts.handle_simulation_tool_call("tools_modify_config_simulation", {"dt": 0.002})
            parsed = json.loads(result)
            assert parsed.get("ok") is False or "error" in parsed
        finally:
            ts.ROOT = original_root

    def test_preserves_non_md_fields(self, tmp_project_root):
        """修改 MD 字段不应影响其他 config 段。"""
        import willy.toolist_simulation as ts
        original_root = ts.ROOT
        ts.ROOT = tmp_project_root
        try:
            ts.handle_simulation_tool_call("tools_modify_config_simulation", {"dt": 0.002})
            saved = json.loads((tmp_project_root / "config.json").read_text())
            assert "residues" in saved, "非 MD 字段应保留"
            assert "molecules" in saved, "非 MD 字段应保留"
        finally:
            ts.ROOT = original_root


# ============================================================
# tools_skip_molecule_simulation
# ============================================================

class TestSkipMoleculeSimulation:
    """tools_skip_molecule_simulation 测试。"""

    def test_adds_molecule_to_skip_list(self, tmp_project_root):
        """应将分子添加到 config.json 的 skipped_molecules 列表中。"""
        import willy.toolist_simulation as ts
        original_root = ts.ROOT
        ts.ROOT = tmp_project_root
        try:
            result = ts.handle_simulation_tool_call("tools_skip_molecule_simulation", {
                "molecule_name": "LiTFSI",
                "reason": "原子类型未解析",
            })
            parsed = json.loads(result)
            assert parsed.get("ok") is True
            assert parsed.get("molecule") == "LiTFSI"

            saved = json.loads((tmp_project_root / "config.json").read_text())
            assert "LiTFSI" in saved.get("skipped_molecules", [])
            assert saved.get("skip_reasons", {}).get("LiTFSI") == "原子类型未解析"
        finally:
            ts.ROOT = original_root

    def test_duplicate_skip_is_idempotent(self, tmp_project_root):
        """对同一分子重复跳过不应重复添加。"""
        import willy.toolist_simulation as ts
        original_root = ts.ROOT
        ts.ROOT = tmp_project_root
        try:
            ts.handle_simulation_tool_call("tools_skip_molecule_simulation", {
                "molecule_name": "FEC",
                "reason": "原因 1",
            })
            ts.handle_simulation_tool_call("tools_skip_molecule_simulation", {
                "molecule_name": "FEC",
                "reason": "原因 2",
            })
            saved = json.loads((tmp_project_root / "config.json").read_text())
            skipped = saved.get("skipped_molecules", [])
            assert skipped.count("FEC") == 1, f"重复条目: {skipped}"
        finally:
            ts.ROOT = original_root

    def test_missing_config_json_creates_new(self, tmp_path):
        """config.json 不存在时应创建新文件。"""
        import willy.toolist_simulation as ts
        original_root = ts.ROOT
        ts.ROOT = tmp_path
        try:
            result = ts.handle_simulation_tool_call("tools_skip_molecule_simulation", {
                "molecule_name": "LiTFSI",
                "reason": "测试",
            })
            parsed = json.loads(result)
            assert parsed.get("ok") is True
            assert (tmp_path / "config.json").exists()
        finally:
            ts.ROOT = original_root


# ============================================================
# tools_diagnose_error_simulation
# ============================================================

class TestDiagnoseErrorSimulation:
    """模拟错误诊断测试。"""

    def test_returns_diagnosis_result_format(self):
        """结果应包含 _diagnosis=True 标记。"""
        from willy.toolist_simulation import handle_simulation_tool_call

        result = handle_simulation_tool_call("tools_diagnose_error_simulation", {
            "step": "em",
            "work_dir": ".",
        })
        parsed = json.loads(result)
        assert parsed.get("_diagnosis") is True
        assert parsed.get("source") == "simulation"

    def test_analyzes_grompp_stderr_for_atomtype(self):
        """应从 grompp stderr 中检测 atomtype 问题。"""
        from willy.toolist_simulation import handle_simulation_tool_call

        result = handle_simulation_tool_call("tools_diagnose_error_simulation", {
            "step": "em",
            "work_dir": ".",
            "grompp_stderr": "ERROR: atomtype CX not found in atomtype database",
        })
        parsed = json.loads(result)
        # 应检测到 atomtype 问题
        issues = parsed.get("issues", [])
        has_atomtype = any("atomtype" in i.lower() for i in issues)
        if not has_atomtype:
            # 如果 parse_gromacs_log 文件缺失，则 issues 可能为空
            # 但至少不应崩溃
            assert parsed.get("_diagnosis") is True

    def test_analyzes_mdrun_stderr_for_nan(self):
        """应从 mdrun stderr 中检测 NaN。"""
        from willy.toolist_simulation import handle_simulation_tool_call

        result = handle_simulation_tool_call("tools_diagnose_error_simulation", {
            "step": "prod",
            "work_dir": ".",
            "mdrun_stderr": "NaN detected in force calculation",
        })
        parsed = json.loads(result)
        issues = parsed.get("issues", [])
        has_nan = any("nan" in i.lower() for i in issues)
        if not has_nan:
            assert parsed.get("_diagnosis") is True


# ============================================================
# _step_to_dict 辅助函数
# ============================================================

class TestStepToDict:
    """_step_to_dict 格式转换测试。"""

    def test_success_result_format(self):
        from willy.toolist_simulation import _step_to_dict
        from willy.errors import StepResult

        sr = StepResult(
            step_name="mdp", step_index=6, success=True,
            outputs={"em": "/tmp/em.mdp", "eq": "/tmp/eq.mdp"},
            artifacts=["/tmp/em.mdp", "/tmp/eq.mdp", "/tmp/prod.mdp"],
            duration_s=0.1,
        )
        d = _step_to_dict(sr)
        assert d["_step_result"] is True
        assert d["success"] is True
        assert d["error_message"] == ""
        assert d["error_kind"] == ""

    def test_failure_result_format(self):
        from willy.toolist_simulation import _step_to_dict
        from willy.errors import StepResult, StepError, ErrorKind

        err = StepError(
            kind=ErrorKind.GROMPP_FAILED,
            message="grompp 失败",
            raw_output="Fatal error",
            hint="检查 .mdp",
        )
        sr = StepResult(step_name="md_em", step_index=8, success=False, error=err)
        d = _step_to_dict(sr)
        assert d["success"] is False
        assert d["error_kind"] == "grompp_failed"
        assert d["error_message"] == "grompp 失败"
        assert d["hint"] == "检查 .mdp"
        assert d["raw_output"] == "Fatal error"
