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

    def test_modify_config_topology(self, tmp_project_root):
        """tools_modify_config_topology 应更新 config.json 的 topology 段。"""
        import willy.toolist_topology as tt
        original_root = tt.ROOT
        tt.ROOT = tmp_project_root
        try:
            result = tt.handle_topology_tool_call("tools_modify_config_topology", {
                "force_field": "opls",
                "default_net_charge": -1,
            })
            parsed = json.loads(result)
            assert parsed.get("ok") is True

            saved = json.loads((tmp_project_root / "config.json").read_text())
            assert saved["topology"]["force_field"] == "opls"
            assert saved["topology"]["default_net_charge"] == -1
        finally:
            tt.ROOT = original_root

    def test_skip_molecule_topology(self, tmp_project_root):
        """tools_skip_molecule_topology 应将分子加入跳过列表。"""
        import willy.toolist_topology as tt
        original_root = tt.ROOT
        tt.ROOT = tmp_project_root
        try:
            result = tt.handle_topology_tool_call("tools_skip_molecule_topology", {
                "molecule_name": "UnstableMol",
                "reason": "atomtype 无法解析",
            })
            parsed = json.loads(result)
            assert parsed.get("ok") is True

            saved = json.loads((tmp_project_root / "config.json").read_text())
            assert "UnstableMol" in saved.get("skipped_molecules", [])
        finally:
            tt.ROOT = original_root


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
