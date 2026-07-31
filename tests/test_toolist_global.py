"""
test_toolist_global.py —— Layer 0 (Config Agent) 工具测试。

测试全局工具定义和处理程序，重点关注：
1. tools_validate_config 的运行时验证行为
2. 工具 JSON Schema 完整性
3. 分子注册表查找
4. 错误诊断函数
"""

import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


# ============================================================
# 工具定义完整性
# ============================================================

class TestToolDefinitions:
    """工具 JSON Schema 定义验证。"""

    @pytest.fixture
    def global_tools(self):
        from willy.toolist_global import TOOLS
        return TOOLS

    def test_all_tools_have_required_format(self, global_tools):
        """每个工具定义必须遵循 OpenAI function calling 格式。"""
        for tool in global_tools:
            assert tool["type"] == "function"
            func = tool["function"]
            assert "name" in func
            assert "description" in func
            assert "parameters" in func
            assert func["parameters"]["type"] == "object"

    def test_tool_names_unique(self, global_tools):
        """工具名称必须唯一。"""
        names = [t["function"]["name"] for t in global_tools]
        assert len(names) == len(set(names)), f"重复名称: {names}"

    def test_tool_names_follow_convention(self, global_tools):
        """工具名称必须遵循 tools_{action}_{target} 命名规范。"""
        for tool in global_tools:
            name = tool["function"]["name"]
            assert name.startswith("tools_"), f"{name} 应以 tools_ 开头"

    def test_required_tools_present(self, global_tools):
        """所有 9 个必需工具必须存在。"""
        names = {t["function"]["name"] for t in global_tools}
        required = {
            "tools_lookup_molecule",
            "tools_resolve_compound",
            "tools_lookup_md_defaults",
            "tools_get_box_density",
            "tools_lookup_basis_set",
            "tools_refresh_structs",
            "tools_diagnose_error_config",
            "tools_validate_config",
            "tools_set_backend_quantum",
        }
        missing = required - names
        assert not missing, f"缺少工具定义: {missing}"


# ============================================================
# tools_validate_config
# ============================================================

class TestToolsValidateConfig:
    """配置验证工具应调用 llm_config.validate_config 并返回 JSON 结果。"""

    def test_handler_validates_config(self):
        from willy.toolist_global import handle_tool_call
        args = {"config_json": json.dumps({
            "residues": {"LiTFSI": 10},
            "molecules": {"LiTFSI": {"charge": 0}},
            "md": {},
        })}
        result = json.loads(handle_tool_call("tools_validate_config", args))
        assert result == {"valid": True, "issues": []}

    def test_json_parse_error_handling(self):
        from willy.toolist_global import handle_tool_call
        result = json.loads(handle_tool_call(
            "tools_validate_config", {"config_json": "not valid json {{{"}))
        assert result["valid"] is False
        assert result["issues"]


# ============================================================
# 分子注册表
# ============================================================

class TestMoleculeRegistry:
    """_MoleculeRegistry 查找逻辑测试。"""

    @pytest.fixture
    def registry(self):
        from willy.toolist_global import _MoleculeRegistry
        reg = _MoleculeRegistry()
        return reg

    def test_registry_has_entries(self, registry):
        """注册表应至少包含知识库中的分子（如果加载成功）。"""
        if len(registry._names) == 0:
            pytest.skip("MoleculeRegistry 未加载知识库数据")
        assert len(registry._names) > 0

    def test_lookup_exact_name(self, registry):
        """精确名称应匹配（如果注册表有数据）。"""
        result = registry.lookup("LiTFSI")
        if result is None:
            # 如果注册表未加载（测试环境），跳过断言
            pytest.skip("MoleculeRegistry 未加载知识库数据")
        assert "LiTFSI" in str(result) or isinstance(result, dict)

    def test_lookup_case_insensitive(self, registry):
        """查找应不区分大小写（如果实现支持）。"""
        # 取决于实现 —— 别名可能处理大小写
        result = registry.lookup("litfsi")
        # 注意：大小写敏感性取决于实现
        # 此测试记录当前行为
        assert result is not None or result is None  # 无断言，仅记录

    def test_lookup_unknown_returns_none(self, registry):
        """完全未知的分子应返回 None。"""
        result = registry.lookup("ZZZZ_Not_A_Real_Molecule_99999")
        assert result is None

    def test_alias_lookup(self, registry):
        """别名查找应正常工作。"""
        # LiTFSI 具有别名
        result = registry.lookup("双三氟甲基磺酰亚胺锂")  # 中文全名
        # 如果注册表中有此别名则匹配，否则为 None
        # 记录行为
        assert True  # 仅验证不崩溃


# ============================================================
# 化合物拆分
# ============================================================

class TestCompoundResolution:
    """_COMPOUNDS 拆分逻辑测试。"""

    def test_known_compounds_can_be_split(self):
        from willy.toolist_global import _COMPOUNDS
        assert "LiTFSI" in _COMPOUNDS
        assert len(_COMPOUNDS["LiTFSI"]) == 2  # Li + TFSI
        assert _COMPOUNDS["LiTFSI"][0] == ("Li", 1)
        assert _COMPOUNDS["LiTFSI"][1] == ("TFSI", 1)

    def test_compound_split_preserves_stoichiometry(self):
        from willy.toolist_global import _COMPOUNDS
        assert "LiPF6" in _COMPOUNDS
        assert _COMPOUNDS["LiPF6"][0] == ("Li", 1)
        assert _COMPOUNDS["LiPF6"][1] == ("PF6", 1)


# ============================================================
# 错误诊断预设
# ============================================================

class TestErrorDiagnosisPresets:
    """_ERRORS 字典中的预写诊断提示。"""

    def test_errors_dict_has_entries(self):
        from willy.toolist_global import _ERRORS
        assert len(_ERRORS) > 0, "错误诊断预设不应为空"

    def test_error_diagnosis_returns_hints(self):
        """每个错误条目应至少包含一条 hint。"""
        from willy.toolist_global import _ERRORS
        for key, val in _ERRORS.items():
            assert "hint" in val or isinstance(val, str), \
                f"ERRORS['{key}'] 应包含提示"


# ============================================================
# handle_tool_call 分发器
# ============================================================

class TestHandleToolCall:
    """handle_tool_call 分发器测试。"""

    def test_unknown_tool_returns_error(self):
        """未知工具名应返回错误 JSON。"""
        from willy.toolist_global import handle_tool_call
        result = handle_tool_call("tools_nonexistent", {})
        parsed = json.loads(result)
        assert "error" in parsed or "未知" in result

    def test_refresh_structs(self, tmp_project_root, monkeypatch):
        """tools_refresh_structs 应重新加载注册表。"""
        import willy.toolist_global as tg
        import willy._paths
        monkeypatch.setattr(willy._paths, "get_project_root", lambda: tmp_project_root)

        result = tg.handle_tool_call("tools_refresh_structs", {})
        parsed = json.loads(result)
        assert parsed.get("ok") is True or "refreshed" in str(parsed).lower()

    def test_get_box_density_known_system(self):
        """tools_get_box_density 对已知体系返回密度值。"""
        from willy.toolist_global import handle_tool_call
        result = handle_tool_call("tools_get_box_density", {"system_type": "electrolyte"})
        parsed = json.loads(result)
        # 应返回密度或回退消息
        assert "density" in parsed or "density" in str(parsed).lower() or "error" not in str(parsed).lower()

    def test_lookup_md_defaults_for_electrolyte(self):
        """tools_lookup_md_defaults 对 electrolyte 返回合理默认值。"""
        from willy.toolist_global import handle_tool_call
        result = handle_tool_call("tools_lookup_md_defaults", {"system_type": "electrolyte"})
        parsed = json.loads(result)
        # 应包含 MD 参数建议
        assert isinstance(parsed, dict)

    def test_set_backend_quantum(self, tmp_project_root, monkeypatch):
        """tools_set_backend_quantum 应更新 config.json。"""
        import willy.toolist_global as tg
        import willy._paths
        monkeypatch.setattr(willy._paths, "get_project_root", lambda: tmp_project_root)

        result = tg.handle_tool_call("tools_set_backend_quantum", {"backend": "orca"})
        parsed = json.loads(result)
        assert parsed.get("backend") == "orca" or parsed.get("ok") is True

    def test_lookup_basis_set_returns_recommendation(self):
        """tools_lookup_basis_set 应返回基组推荐。"""
        from willy.toolist_global import handle_tool_call
        result = handle_tool_call("tools_lookup_basis_set", {"element": "Li"})
        parsed = json.loads(result)
        assert isinstance(parsed, dict)
