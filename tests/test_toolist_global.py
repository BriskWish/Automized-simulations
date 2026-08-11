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
        """所有必需的 Config Agent 工具必须存在。"""
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
            "tools_inspect_quantum_inputs",
        }
        missing = required - names
        assert not missing, f"缺少工具定义: {missing}"


# ============================================================
# tools_validate_config
# ============================================================

class TestToolsValidateConfig:
    """配置验证工具应调用 workflow_config.validate_config 并返回 JSON 结果。"""

    def test_handler_validates_config(self):
        from willy.toolist_global import handle_tool_call
        args = {"config_json": json.dumps({
            "residues": {"LiTFSI": 10},
            "molecules": {"LiTFSI": {"charge": 0, "spin": 1}},
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
        """注册表必须提供知识库或内置兜底分子。"""
        assert len(registry._names) > 0

    def test_lookup_exact_registered_name(self, registry):
        """精确注册的离子名必须返回其结构化条目。"""
        result = registry.lookup("TFSI")
        assert result is not None
        assert result["name"] == "TFSI"
        assert result["charge"] == -1

    def test_lookup_case_insensitive(self, registry):
        """ASCII 分子名应不区分大小写。"""
        result = registry.lookup("tfsi")
        assert result is not None
        assert result["name"] == "TFSI"

    def test_lookup_unknown_returns_none(self, registry):
        """完全未知的分子应返回 None。"""
        result = registry.lookup("ZZZZ_Not_A_Real_Molecule_99999")
        assert result is None

    def test_alias_lookup(self, registry):
        """知识库中的中文别名应返回对应离子。"""
        result = registry.lookup("双三氟甲磺酰亚胺")
        assert result is not None
        assert result["name"] == "TFSI"

    @pytest.mark.parametrize(("alias", "name", "charge", "spin", "atom_count"), [
        ("钠离子", "Na", 1, 1, 1),
        ("六氟砷酸根", "AsF6", -1, 1, 7),
        ("B(CN)4-", "BCN4", -1, 1, 9),
        ("FTFSI-", "FTFSI", -1, 2, 13),
        ("四乙二醇二甲醚", "T4GM", 0, 1, 37),
    ])
    def test_imported_species_aliases_are_indexed(self, registry, alias, name, charge, spin, atom_count):
        """导入物种的中英文/带电别名必须进入表格与 TF-IDF 索引。"""
        result = registry.lookup(alias)

        assert result is not None
        assert (result["name"], result["charge"], result["spin"], result["atom_count"]) == (
            name, charge, spin, atom_count,
        )


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

    def test_refresh_structs_is_idempotent(self):
        """重复刷新不得复制表格条目或向量文档。"""
        import willy.toolist_global as tg

        first = json.loads(tg.handle_tool_call("tools_refresh_structs", {}))
        second = json.loads(tg.handle_tool_call("tools_refresh_structs", {}))

        assert second["count"] == first["count"]
        assert len(second["molecules"]) == len(set(second["molecules"]))

    def test_get_box_density_known_system(self):
        """tools_get_box_density returns the mass-density default contract."""
        from willy.toolist_global import handle_tool_call
        result = handle_tool_call("tools_get_box_density", {"system_type": "electrolyte"})
        parsed = json.loads(result)
        assert parsed["target_mass_density_g_cm3"] == 0.7
        assert parsed["unit"] == "g/cm3"
        assert "初始体积将由使用默认0.7g/cm3的密度猜测" == parsed["note"]

    def test_lookup_md_defaults_for_electrolyte(self):
        """tools_lookup_md_defaults 对 electrolyte 返回合理默认值。"""
        from willy.toolist_global import handle_tool_call
        result = handle_tool_call("tools_lookup_md_defaults", {"system_type": "electrolyte"})
        parsed = json.loads(result)
        # 应包含 MD 参数建议
        assert isinstance(parsed, dict)

    @pytest.mark.parametrize("system_type", ["ionic_liquid", "solvent_mix", "aqueous", "organic"])
    def test_all_md_default_presets_use_one_fs(self, system_type):
        """所有 Agent 体系预设统一使用 1 fs，避免新任务回退到 2 fs。"""
        from willy.toolist_global import handle_tool_call
        result = json.loads(handle_tool_call(
            "tools_lookup_md_defaults", {"system_type": system_type},
        ))
        assert result["dt"] == 0.001

    def test_set_backend_quantum(self, tmp_project_root, monkeypatch):
        """tools_set_backend_quantum 应更新 config.json。"""
        import willy.toolist_global as tg
        import willy._paths
        monkeypatch.setattr(willy._paths, "get_project_root", lambda: tmp_project_root)

        result = tg.handle_tool_call("tools_set_backend_quantum", {"backend": "orca"})
        parsed = json.loads(result)
        assert parsed.get("backend") == "orca" or parsed.get("ok") is True

    def test_set_g09_backend_quantum(self, tmp_project_root, monkeypatch):
        import willy.toolist_global as tg
        import willy._paths
        monkeypatch.setattr(willy._paths, "get_project_root", lambda: tmp_project_root)

        result = tg.handle_tool_call("tools_set_backend_quantum", {"backend": "g09"})
        parsed = json.loads(result)

        assert parsed["ok"] is True
        assert parsed["backend"] == "g09"

    def test_lookup_basis_set_returns_recommendation(self):
        """tools_lookup_basis_set 应返回基组推荐。"""
        from willy.toolist_global import handle_tool_call
        result = handle_tool_call("tools_lookup_basis_set", {"element": "Li"})
        parsed = json.loads(result)
        assert isinstance(parsed, dict)
