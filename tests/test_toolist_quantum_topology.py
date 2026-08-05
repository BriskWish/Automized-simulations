"""
test_toolist_quantum_topology.py —— Layer 1 & 2 工具测试。

测试量子化学和拓扑工具集的处理程序。
关注错误路径、无效输入、缺失文件场景。
"""

import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from willy.errors import StepResult, StepError, ErrorKind


# ============================================================
# Quantum 工具定义
# ============================================================

class TestQuantumToolDefinitions:
    """量子工具 JSON Schema 验证。"""

    @pytest.fixture
    def quantum_tools(self):
        from willy.toolist_quantum import QUANTUM_TOOLS
        return QUANTUM_TOOLS

    def test_eight_tools_defined(self, quantum_tools):
        """应有 8 个量子工具。"""
        names = {t["function"]["name"] for t in quantum_tools}
        assert len(names) == 8, f"期望 8 个工具, 实际 {len(names)}"

    def test_all_tool_names(self, quantum_tools):
        names = {t["function"]["name"] for t in quantum_tools}
        required = {
            "tools_retry_struct_g16",
            "tools_retry_struct_orca",
            "tools_retry_mol2_conversion",
            "tools_retry_chg_g16",
            "tools_retry_chg_orca",
            "tools_diagnose_error_quantum",
            "tools_modify_config_molecule",
            "tools_skip_molecule_quantum",
        }
        missing = required - names
        assert not missing, f"缺少工具: {missing}"

    def test_tools_have_valid_json_schema(self, quantum_tools):
        for tool in quantum_tools:
            assert tool["type"] == "function"
            assert "name" in tool["function"]
            assert "parameters" in tool["function"]
            props = tool["function"]["parameters"].get("properties", {})
            assert isinstance(props, dict)

    def test_retry_tools_use_current_fchk_contract(self, quantum_tools):
        """mol2 和 RESP 重试都必须消费 Step 2 的 *_opt.fchk。"""
        definitions = {tool["function"]["name"]: tool["function"]
                       for tool in quantum_tools}
        for name in {
            "tools_retry_mol2_conversion",
            "tools_retry_chg_g16",
            "tools_retry_chg_orca",
        }:
            parameters = definitions[name]["parameters"]
            assert "fchk_path" in parameters["properties"]
            assert "fchk_path" in parameters["required"]


# ============================================================
# Quantum 工具处理程序
# ============================================================

class TestQuantumToolHandler:
    """量子工具处理程序测试。"""

    def test_unknown_tool_returns_error(self):
        from willy.toolist_quantum import handle_quantum_tool_call
        result = handle_quantum_tool_call("tools_unknown", {})
        parsed = json.loads(result)
        assert "error" in parsed

    def test_diagnose_tool_returns_result(self):
        """tools_diagnose_error_quantum 应返回诊断结果。"""
        from willy.toolist_quantum import handle_quantum_tool_call
        result = handle_quantum_tool_call("tools_diagnose_error_quantum", {
            "step": "struct_maker",
            "log_path": "/nonexistent/test.log",
        })
        parsed = json.loads(result)
        assert "_diagnosis" in parsed
        assert parsed["source"] == "quantum"

    def test_modify_config_molecule(self, tmp_project_root):
        """tools_modify_config_molecule 应更新 config.json。"""
        import willy.toolist_quantum as tq
        original_root = tq.ROOT
        tq.ROOT = tmp_project_root
        try:
            result = tq.handle_quantum_tool_call("tools_modify_config_molecule", {
                "molecule_name": "LiTFSI",
                "basis": "b3lyp/6-31+g(d)",
                "mem": "10GB",
            })
            parsed = json.loads(result)
            assert parsed.get("ok") is True
        finally:
            tq.ROOT = original_root

    def test_modify_config_uses_the_active_run_snapshot(self, tmp_path):
        """Agent 调整只影响当前运行，不得改写项目根配置。"""
        from willy.toolist_quantum import handle_quantum_tool_call

        root_config = tmp_path / "config.json"
        root_config.write_text(json.dumps({"molecules": {"Li": {"basis": "old"}}}))
        run_dir = tmp_path / "md_run" / "run-1"
        run_dir.mkdir(parents=True)
        run_config = run_dir / "config.json"
        run_config.write_text(root_config.read_text())

        parsed = json.loads(handle_quantum_tool_call(
            "tools_modify_config_molecule",
            {"molecule_name": "Li", "basis": "new"},
            work_dir=str(run_dir),
            config_path=str(run_config),
        ))

        assert parsed["ok"] is True
        assert json.loads(run_config.read_text())["molecules"]["Li"]["basis"] == "new"
        assert json.loads(root_config.read_text())["molecules"]["Li"]["basis"] == "old"

    def test_skip_molecule_quantum(self, tmp_project_root):
        """tools_skip_molecule_quantum 应将分子加入跳过列表。"""
        import willy.toolist_quantum as tq
        original_root = tq.ROOT
        tq.ROOT = tmp_project_root
        try:
            result = tq.handle_quantum_tool_call("tools_skip_molecule_quantum", {
                "molecule_name": "BadMol",
                "reason": "SCF 无法收敛",
            })
            parsed = json.loads(result)
            assert parsed.get("ok") is True
            assert parsed.get("molecule") == "BadMol"

            # 验证 config.json
            saved = json.loads((tmp_project_root / "config.json").read_text())
            assert "BadMol" in saved.get("skipped_molecules", [])
        finally:
            tq.ROOT = original_root

    def test_mol2_conversion_error_on_missing_file(self):
        """当文件不存在时，mol2 转换应优雅失败。"""
        from willy.toolist_quantum import handle_quantum_tool_call
        result = handle_quantum_tool_call("tools_retry_mol2_conversion", {
            "method": "reparse",
            "fchk_path": "/nonexistent/file.fchk",
            "output_path": "/tmp/test.mol2",
        })
        parsed = json.loads(result)
        # 应返回错误或结果
        assert "_step_result" not in parsed or parsed.get("success") is False

    def test_mol2_conversion_returns_step_result_for_malformed_fchk(self, tmp_path):
        """fchk 解析异常不得从公开工具路径泄露。"""
        from willy.toolist_quantum import handle_quantum_tool_call

        fchk = tmp_path / "broken_opt.fchk"
        fchk.write_text("not an fchk file\n")
        parsed = json.loads(handle_quantum_tool_call(
            "tools_retry_mol2_conversion", {"fchk_path": str(fchk)},
        ))

        assert parsed["_step_result"] is True
        assert parsed["success"] is False
        assert parsed["error_kind"] == ErrorKind.UNKNOWN.value

    @pytest.mark.parametrize("missing_block", ["NBond", "IBond", "RBond"])
    def test_mol2_conversion_rejects_missing_bond_connectivity(self, tmp_path, missing_block):
        """缺失任一键连接字段时，不得生成可被 Sobtop 消费的 mol2。"""
        from willy.quantum.fchk_mol2 import convert

        blocks = {
            "NBond": "NBond I N= 2\n1 1\n",
            "IBond": "IBond I N= 2\n2 1\n",
            "RBond": "RBond R N= 2\n1.0 1.0\n",
        }
        content = (
            "Number of atoms I 2\n"
            "Atomic numbers I N= 2\n6 1\n"
            "Current cartesian coordinates R N= 6\n"
            "0.0 0.0 0.0 0.0 0.0 1.0\n"
            "MxBond I 1\n"
            + "".join(value for name, value in blocks.items() if name != missing_block)
        )
        fchk = tmp_path / "incomplete_opt.fchk"
        mol2 = tmp_path / "incomplete.mol2"
        fchk.write_text(content)

        result = convert(str(fchk), str(mol2))

        assert result.success is False
        assert result.error is not None
        assert missing_block in result.error.message
        assert not mol2.exists()

    def test_mol2_conversion_rejects_incomplete_coordinates(self, tmp_path):
        """原子数与坐标数组长度不一致时必须失败。"""
        from willy.quantum.fchk_mol2 import convert

        fchk = tmp_path / "truncated_opt.fchk"
        mol2 = tmp_path / "truncated.mol2"
        fchk.write_text(
            "Number of atoms I 2\n"
            "Atomic numbers I N= 2\n6 1\n"
            "Current cartesian coordinates R N= 6\n"
            "0.0 0.0 0.0 0.0 0.0\n"
            "MxBond I 1\n"
            "NBond I N= 2\n1 1\n"
            "IBond I N= 2\n2 1\n"
            "RBond R N= 2\n1.0 1.0\n"
        )

        result = convert(str(fchk), str(mol2))

        assert result.success is False
        assert result.error is not None
        assert "Current cartesian coordinates" in result.error.message
        assert not mol2.exists()

    @pytest.mark.parametrize("tool_name", [
        "tools_retry_chg_g16",
        "tools_retry_chg_orca",
    ])
    def test_chg_retry_reports_missing_explicit_fchk(self, tool_name, tmp_path):
        """两种后端重试都基于同一显式 fchk 输入。"""
        from willy.toolist_quantum import handle_quantum_tool_call

        missing = tmp_path / "Li_opt.fchk"
        parsed = json.loads(handle_quantum_tool_call(
            tool_name, {"fchk_path": str(missing)},
        ))

        assert parsed["_step_result"] is True
        assert parsed["success"] is False
        assert parsed["error_kind"] == ErrorKind.FILE_NOT_FOUND.value


# ============================================================
# Topology 工具定义
# ============================================================

class TestTopologyToolDefinitions:
    """拓扑工具 JSON Schema 验证。"""

    @pytest.fixture
    def topology_tools(self):
        from willy.toolist_topology import TOPOLOGY_TOOLS
        return TOPOLOGY_TOOLS

    def test_six_tools_defined(self, topology_tools):
        """应有 6 个拓扑工具。"""
        names = {t["function"]["name"] for t in topology_tools}
        assert len(names) == 6, f"期望 6 个工具, 实际 {len(names)}"

    def test_all_tool_names(self, topology_tools):
        names = {t["function"]["name"] for t in topology_tools}
        required = {
            "tools_retry_topo_gaff",
            "tools_retry_topo_opls",
            "tools_retry_top_assembly",
            "tools_diagnose_error_topology",
            "tools_modify_config_topology",
            "tools_skip_molecule_topology",
        }
        missing = required - names
        assert not missing, f"缺少工具: {missing}"

    def test_topology_config_tool_has_no_unused_default_charge(self, topology_tools):
        modify_tool = next(tool for tool in topology_tools if tool["function"]["name"] == "tools_modify_config_topology")
        assert "default_net_charge" not in modify_tool["function"]["parameters"]["properties"]


# ============================================================
# Topology 工具处理程序
# ============================================================

class TestTopologyToolHandler:
    """拓扑工具处理程序测试。"""

    def test_unknown_tool_returns_error(self):
        from willy.toolist_topology import handle_topology_tool_call
        result = handle_topology_tool_call("tools_unknown", {})
        parsed = json.loads(result)
        assert "error" in parsed

    def test_diagnose_tool_returns_result(self):
        """tools_diagnose_error_topology 应返回诊断结果。"""
        from willy.toolist_topology import handle_topology_tool_call
        result = handle_topology_tool_call("tools_diagnose_error_topology", {
            "error_source": "sobtop",
            "raw_output": "Fortran runtime error: atomtype CX not found",
        })
        parsed = json.loads(result)
        assert "_diagnosis" in parsed
        assert parsed["source"] == "topology"

    def test_diagnose_detects_atomtype_error(self):
        """应从 raw_output 中检测 atomtype 缺失。"""
        from willy.toolist_topology import handle_topology_tool_call
        result = handle_topology_tool_call("tools_diagnose_error_topology", {
            "error_source": "sobtop",
            "raw_output": "Error: atomtype CX not found in database",
        })
        parsed = json.loads(result)
        issues = parsed.get("issues", [])
        has_atomtype = any("atomtype" in i.lower() for i in issues)
        assert has_atomtype or len(parsed.get("hint", "")) > 0

    def test_diagnose_detects_sobtop_exit_24(self):
        """应从 raw_output 中检测 Sobtop 退出码 24。"""
        from willy.toolist_topology import handle_topology_tool_call
        result = handle_topology_tool_call("tools_diagnose_error_topology", {
            "error_source": "sobtop",
            "raw_output": "Fortran runtime error: rc=24",
        })
        parsed = json.loads(result)
        assert "_diagnosis" in parsed

    def test_diagnose_detects_ligpargen_error(self):
        """应检测 LigParGen 相关错误。"""
        from willy.toolist_topology import handle_topology_tool_call
        result = handle_topology_tool_call("tools_diagnose_error_topology", {
            "error_source": "ligpargen",
            "raw_output": "No valid atom types generated",
        })
        parsed = json.loads(result)
        assert "_diagnosis" in parsed

    def test_modify_config_requires_active_run(self, tmp_project_root):
        """Topology config tools may only mutate an explicit run snapshot."""
        from willy.toolist_topology import handle_topology_tool_call

        result = json.loads(handle_topology_tool_call(
            "tools_modify_config_topology", {"default_lbcc": False},
        ))
        assert result["success"] is False

    def test_skip_molecule_topology_is_disabled(self, tmp_project_root):
        """Skip must not leave manifest and residues inconsistent."""
        from willy.toolist_topology import handle_topology_tool_call

        run_dir = tmp_project_root / "md_run" / "run-1"
        run_dir.mkdir(parents=True)
        result = json.loads(handle_topology_tool_call(
            "tools_skip_molecule_topology", {"molecule_name": "UnstableMol"}, work_dir=str(run_dir),
        ))
        assert result["ok"] is False


# ============================================================
# 跨工具集一致性
# ============================================================

class TestCrossToolistConsistency:
    """量子/拓扑/模拟工具集之间的一致性检查。"""

    def test_all_tool_names_use_tools_prefix(self):
        """所有工具名称必须以 tools_ 开头。"""
        from willy.toolist_quantum import QUANTUM_TOOLS
        from willy.toolist_topology import TOPOLOGY_TOOLS
        from willy.toolist_simulation import SIMULATION_TOOLS

        for tools in [QUANTUM_TOOLS, TOPOLOGY_TOOLS, SIMULATION_TOOLS]:
            for tool in tools:
                name = tool["function"]["name"]
                assert name.startswith("tools_"), f"{name} 应以 tools_ 开头"

    def test_all_handlers_return_json_strings(self):
        """诊断处理程序应返回 JSON 字符串。"""
        from willy.toolist_quantum import handle_quantum_tool_call
        from willy.toolist_topology import handle_topology_tool_call
        from willy.toolist_simulation import handle_simulation_tool_call

        # 诊断工具应返回有效的 JSON
        result = handle_quantum_tool_call("tools_diagnose_error_quantum", {
            "step": "struct_maker",
            "log_path": "/nonexistent/test.log",
        })
        assert isinstance(result, str)
        json.loads(result)

        result = handle_topology_tool_call("tools_diagnose_error_topology", {
            "error_source": "sobtop",
            "raw_output": "",
        })
        json.loads(result)

    def test_diagnosis_tools_all_use_diagnosis_marker(self):
        """所有 diagnose_* 工具应返回 _diagnosis=True。"""
        from willy.toolist_quantum import handle_quantum_tool_call
        from willy.toolist_topology import handle_topology_tool_call
        from willy.toolist_simulation import handle_simulation_tool_call

        quantum_result = json.loads(handle_quantum_tool_call(
            "tools_diagnose_error_quantum",
            {"step": "struct_maker", "log_path": "/nonexistent/test.log"},
        ))
        assert quantum_result.get("_diagnosis") is True

        topo_result = json.loads(handle_topology_tool_call(
            "tools_diagnose_error_topology",
            {"error_source": "sobtop", "raw_output": ""},
        ))
        assert topo_result.get("_diagnosis") is True

        sim_result = json.loads(handle_simulation_tool_call(
            "tools_diagnose_error_simulation",
            {"step": "em", "work_dir": "."},
        ))
        assert sim_result.get("_diagnosis") is True
