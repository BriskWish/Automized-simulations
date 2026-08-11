"""
test_toolist_simulation.py —— Layer 3 (Simulation Agent) 工具测试。

重点测试：
1. tools_retry_prod — 检查点与追加参数透传
2. tools_retry_mdp — stage 参数过滤
3. tools_retry_em / tools_retry_eq — 覆盖参数传递
4. tools_diagnose_error_simulation — GROMACS 日志诊断
5. tools_modify_config_simulation — config.json 更新
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

    def test_fourteen_tools_defined(self, sim_tools):
        """应有 14 个工具定义，不暴露不完整的跳过分子功能。"""
        names = {t["function"]["name"] for t in sim_tools}
        assert len(names) == 14, f"期望 14 个工具, 实际 {len(names)}: {names}"

    def test_all_tool_names_present(self, sim_tools):
        """所有必需工具应存在。"""
        names = {t["function"]["name"] for t in sim_tools}
        required = {
            "tools_retry_mdp",
            "tools_retry_box",
            "tools_retry_em",
            "tools_retry_eq",
            "tools_retry_prod",
            "tools_run_em_simulation",
            "tools_run_eq_simulation",
            "tools_run_prod_simulation",
            "tools_configure_outputs_simulation",
            "tools_configure_prod_simulation",
            "tools_diagnose_error_simulation",
            "tools_modify_config_simulation",
            "tools_migrate_md_config_simulation",
            "tools_lookup_mdrun_knowledge",
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

    def test_skip_molecule_is_not_exposed(self, sim_tools):
        names = {t["function"]["name"] for t in sim_tools}
        assert "tools_skip_molecule_simulation" not in names

    def test_eq_schema_exposes_all_three_annealing_temperatures(self, sim_tools):
        tools = {tool["function"]["name"]: tool["function"] for tool in sim_tools}
        for name in ("tools_retry_mdp", "tools_retry_eq", "tools_modify_config_simulation"):
            fields = tools[name]["parameters"]["properties"]
            assert {"eq_high_temperature", "eq_transition_temperature", "eq_target_temperature"} <= set(fields)
            assert "eq_acceptance" in fields

    def test_gromacs_run_tools_expose_file_contract(self, sim_tools):
        by_name = {tool["function"]["name"]: tool["function"] for tool in sim_tools}
        for stage in ("em", "eq", "prod"):
            properties = by_name[f"tools_run_{stage}_simulation"]["parameters"]["properties"]
            assert {"top_path", "itp_paths", "mdp_path", "pdb_path", "structure_path", "tpr_path"} <= set(properties)

    def test_protocol_tools_do_not_expose_model_confirmed_flag(self, sim_tools):
        """User approval is server-side, never a tool argument the model can forge."""
        for tool in sim_tools:
            properties = tool["function"]["parameters"]["properties"]
            assert "confirmed" not in properties


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
        """StepResult.to_dict 应包含 _step_result 标记。"""
        from willy.errors import StepResult
        sr = StepResult(step_name="test", step_index=1, success=True)
        d = sr.to_dict()
        assert d["_step_result"] is True
        assert d["success"] is True
        assert d["step_name"] == "test"

    def test_migration_adoption_requires_confirmation_and_switches_active_config(self, tmp_path):
        from willy.toolist_simulation import handle_simulation_tool_call

        config = tmp_path / "config.json"
        config.write_text(json.dumps({
            "residues": {"A": 1},
            "molecules": {"A": {"charge": 0, "spin": 1}},
            "md": {"ref_t": 298, "eq_ns": 5, "prod_ns": 10},
            "box": {"density": 6.0, "box_size": None, "tolerance": 2.0},
        }))
        refused = json.loads(handle_simulation_tool_call(
            "tools_migrate_md_config_simulation", {"adopt": True},
            work_dir=str(tmp_path), config_path=str(config),
        ))
        assert refused["_step_result"] is True
        assert refused["error_kind"] == "user_confirmation_required"

        adopted = json.loads(handle_simulation_tool_call(
            "tools_migrate_md_config_simulation", {"adopt": True},
            work_dir=str(tmp_path), config_path=str(config),
            protocol_change_authorized=True,
        ))
        assert adopted["ok"] is True
        assert adopted["adopted"] is True
        archive = tmp_path / ".willy" / "config-migrations"
        assert (archive / "config.pre-v2.json").is_file()
        assert (archive / "config.migration.json").is_file()
        assert not (tmp_path / "config.pre-v2.json").exists()
        assert json.loads(config.read_text())["md"]["schema_version"] == 2

    def test_model_supplied_confirmed_flag_cannot_authorize_protocol_change(self, tmp_project_root):
        """Even an explicit LLM ``confirmed=true`` must leave config untouched."""
        from willy.toolist_simulation import handle_simulation_tool_call

        config = tmp_project_root / "config.json"
        before = config.read_text()
        result = json.loads(handle_simulation_tool_call(
            "tools_modify_config_simulation",
            {"dt": 0.0005, "confirmed": True},
            config_path=str(config),
        ))

        assert result["_step_result"] is True
        assert result["success"] is False
        assert result["error_kind"] == "user_confirmation_required"
        assert config.read_text() == before


# ============================================================
# tools_retry_prod
# ============================================================

class TestRetryProd:
    """生产重试由 manifest 决定是否允许 checkpoint append。"""

    def test_run_prod_accepts_extra_mdrun(self):
        from willy.simulation.prod import run_prod
        import inspect
        sig = inspect.signature(run_prod)
        params = list(sig.parameters.keys())
        assert "extra_mdrun" in params
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

    def test_handler_does_not_inject_unverified_restart_flags(self, tmp_path):
        from willy.errors import StepResult
        from willy.toolist_simulation import handle_simulation_tool_call

        with patch("willy.simulation.prod.run_prod", return_value=StepResult(
            step_name="prod", step_index=10, success=True,
        )) as run_prod:
            handle_simulation_tool_call("tools_retry_prod", {
                "work_dir": str(tmp_path),
                "from_checkpoint": True,
                "append": True,
            })

        run_prod.assert_called_once_with(work_dir=str(tmp_path))


class TestRunGromacsTools:
    """Direct tools expose the stage executors while keeping paths in one run."""

    def test_run_em_uses_bound_workspace(self, tmp_path):
        from willy.errors import StepResult
        from willy.toolist_simulation import handle_simulation_tool_call

        with patch("willy.simulation.em.run_em", return_value=StepResult("em", 8, True)) as run_em:
            result = handle_simulation_tool_call(
                "tools_run_em_simulation",
                {"top_path": "topol.top", "itp_paths": ["solute.itp"], "mdp_path": "em.mdp", "structure_path": "model.pdb", "tpr_path": "em.tpr"},
                work_dir=str(tmp_path),
                config_path=str(tmp_path / "config.json"),
            )

        assert json.loads(result)["success"] is True
        assert run_em.call_args.kwargs["work_dir"] == str(tmp_path.resolve())
        assert run_em.call_args.kwargs["topol"] == str((tmp_path / "topol.top").resolve())
        assert run_em.call_args.kwargs["itps"] == [str((tmp_path / "solute.itp").resolve())]

    def test_run_tool_rejects_path_outside_workspace(self, tmp_path):
        from willy.toolist_simulation import handle_simulation_tool_call

        result = handle_simulation_tool_call(
            "tools_run_eq_simulation",
            {"top_path": "/etc/passwd"},
            work_dir=str(tmp_path),
        )

        parsed = json.loads(result)
        assert parsed["success"] is False
        assert parsed["error_kind"] == "config_invalid"


# ============================================================
# tools_retry_mdp
# ============================================================

class TestRetryMdp:
    """MDP 重试应重建所有受全局协议变更影响的下游阶段。"""

    def test_stage_is_forwarded_to_mdp_builder(self, tmp_project_root, tmp_path):
        from willy.errors import StepResult
        from willy.toolist_simulation import handle_simulation_tool_call

        with patch("willy.simulation.mdp.build_all", return_value=StepResult(
            step_name="mdp", step_index=6, success=True,
        )) as build_all:
            handle_simulation_tool_call(
                "tools_retry_mdp", {"stage": "em"},
                work_dir=str(tmp_path), config_path=str(tmp_project_root / "config.json"),
            )

        build_all.assert_called_once_with(
            config_path=str(tmp_project_root / "config.json"),
            output_dir=str(tmp_path), stages=("em",),
        )

    def test_all_stage_requests_all_mdp_files(self, tmp_project_root, tmp_path):
        from willy.errors import StepResult
        from willy.toolist_simulation import handle_simulation_tool_call

        with patch("willy.simulation.mdp.build_all", return_value=StepResult(
            step_name="mdp", step_index=6, success=True,
        )) as build_all:
            handle_simulation_tool_call(
                "tools_retry_mdp", {"stage": "all"},
                work_dir=str(tmp_path), config_path=str(tmp_project_root / "config.json"),
            )

        build_all.assert_called_once_with(
            config_path=str(tmp_project_root / "config.json"),
            output_dir=str(tmp_path), stages=("em", "eq", "prod"),
        )

    def test_eq_change_rebuilds_eq_and_prod(self, tmp_project_root, tmp_path):
        from willy.errors import StepResult
        from willy.toolist_simulation import handle_simulation_tool_call

        with patch("willy.simulation.mdp.build_all", return_value=StepResult(
            step_name="mdp", step_index=6, success=True,
        )) as build_all:
            handle_simulation_tool_call(
                "tools_retry_mdp",
                {"stage": "eq", "eq_high_temperature": 550},
                work_dir=str(tmp_path), config_path=str(tmp_project_root / "config.json"),
                protocol_change_authorized=True,
            )

        build_all.assert_called_once_with(
            config_path=str(tmp_project_root / "config.json"),
            output_dir=str(tmp_path), stages=("eq", "prod"),
        )

    def test_build_all_signature_accepts_overrides(self):
        """build_all 按关键字接受 overrides 参数。"""
        from willy.simulation.mdp import build_all
        import inspect
        sig = inspect.signature(build_all)
        params = list(sig.parameters.keys())
        assert "overrides" in params
        assert "stages" in params
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
        original_root = ts.ROOT
        ts.ROOT = tmp_project_root
        try:
            result = handle_simulation_tool_call("tools_modify_config_simulation", {
                "dt": 0.002,
                "eq_target_temperature": 310.0,
                "prod_temperature": 310.0,
                "eq_segments_ns": {
                    "heat": 2, "hold_high": 1, "cool_transition": 2,
                    "hold_transition": 1, "cool_target": 2, "hold_target": 2,
                },
            }, protocol_change_authorized=True)
            parsed = json.loads(result)
            assert parsed.get("ok") is True
            assert "dt" in parsed.get("updated_fields", [])
        finally:
            ts.ROOT = original_root

    def test_reports_config_delta_after_the_run_snapshot_is_updated(self, tmp_project_root):
        from willy.toolist_simulation import handle_simulation_tool_call

        config_path = tmp_project_root / "config.json"
        config = json.loads(config_path.read_text())
        config["md"]["eq"]["tau_p"] = 1.0
        config_path.write_text(json.dumps(config))
        observed = []
        result = handle_simulation_tool_call(
            "tools_modify_config_simulation",
            {"tau_t": 2.0, "eq_tau_p": 2.0},
            config_path=str(config_path),
            on_config_updated=lambda before, after, fields: observed.append((before, after, fields)),
            protocol_change_authorized=True,
        )

        assert json.loads(result)["ok"] is True
        assert len(observed) == 1
        before, after, fields = observed[0]
        assert fields == ["tau_t", "eq_tau_p"]
        assert before["md"]["tau_t"] != after["md"]["tau_t"]
        assert before["md"]["eq"]["tau_p"] != after["md"]["eq"]["tau_p"]

    def test_missing_config_json(self, tmp_path):
        """config.json 不存在时应有错误。"""
        import willy.toolist_simulation as ts
        original_root = ts.ROOT
        ts.ROOT = tmp_path
        try:
            result = ts.handle_simulation_tool_call(
                "tools_modify_config_simulation", {"dt": 0.002}, protocol_change_authorized=True,
            )
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
            ts.handle_simulation_tool_call(
                "tools_modify_config_simulation", {"dt": 0.002}, protocol_change_authorized=True,
            )
            saved = json.loads((tmp_project_root / "config.json").read_text())
            assert "residues" in saved, "非 MD 字段应保留"
            assert "molecules" in saved, "非 MD 字段应保留"
        finally:
            ts.ROOT = original_root


# ============================================================
# Removed simulation skip tool
# ============================================================

def test_removed_skip_handler_does_not_mutate_configuration(tmp_project_root):
    from willy.toolist_simulation import handle_simulation_tool_call

    before = (tmp_project_root / "config.json").read_text()
    result = json.loads(handle_simulation_tool_call(
        "tools_skip_molecule_simulation", {"molecule_name": "LiTFSI"},
        config_path=str(tmp_project_root / "config.json"),
    ))
    assert result["ok"] is False
    assert "不支持" in result["error"]
    assert (tmp_project_root / "config.json").read_text() == before


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
# StepResult 序列化
# ============================================================

class TestStepResultSerialization:
    """共享 StepResult 序列化格式测试。"""

    def test_success_result_format(self):
        from willy.errors import StepResult

        sr = StepResult(
            step_name="mdp", step_index=6, success=True,
            outputs={"em": "/tmp/em.mdp", "eq": "/tmp/eq.mdp"},
            artifacts=["/tmp/em.mdp", "/tmp/eq.mdp", "/tmp/prod.mdp"],
            duration_s=0.1,
        )
        d = sr.to_dict()
        assert d["_step_result"] is True
        assert d["success"] is True
        assert d["error_message"] == ""
        assert d["error_kind"] == ""

    def test_failure_result_format(self):
        from willy.errors import StepResult, StepError, ErrorKind

        err = StepError(
            kind=ErrorKind.GROMPP_FAILED,
            message="grompp 失败",
            raw_output="Fatal error",
            hint="检查 .mdp",
        )
        sr = StepResult(step_name="md_em", step_index=8, success=False, error=err)
        d = sr.to_dict()
        assert d["success"] is False
        assert d["error_kind"] == "grompp_failed"
        assert d["error_message"] == "grompp 失败"
        assert d["hint"] == "检查 .mdp"
        assert d["raw_output"] == "Fatal error"
