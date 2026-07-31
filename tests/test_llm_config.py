"""
test_llm_config.py —— validate_config / _apply_defaults / _available_residues 测试。

重点：边界值、无效输入、默认值合并。
"""

import json
import pytest
from pathlib import Path

from willy.llm_config import validate_config, _apply_defaults


# ============================================================
# validate_config
# ============================================================

class TestValidateConfig:
    """config.json 验证逻辑测试。"""

    # ---- 合法输入 ----

    def test_valid_minimal_config(self, sample_config_dict):
        """最小合法配置应无问题。"""
        issues = validate_config(sample_config_dict)
        assert issues == [], f"不期望有问题: {issues}"

    def test_valid_single_molecule(self):
        """单个分子的配置应通过。"""
        cfg = {
            "residues": {"H2O": 100},
            "molecules": {"H2O": {"charge": 0, "spin": 1}},
            "md": {},
        }
        assert validate_config(cfg) == []

    def test_valid_many_molecules(self):
        """多分子体系应通过。"""
        cfg = {
            "residues": {"A": 10, "B": 20, "C": 30},
            "molecules": {
                "A": {"charge": 0, "spin": 1},
                "B": {"charge": -1, "spin": 2},
                "C": {"charge": 1, "spin": 1},
            },
            "md": {},
        }
        assert validate_config(cfg) == []

    # ---- 无效输入 ----

    def test_empty_residues(self):
        """residues 为空必须报告。"""
        cfg = {"residues": {}, "molecules": {}, "md": {}}
        issues = validate_config(cfg)
        assert any("不能为空" in i for i in issues), f"issues: {issues}"

    def test_missing_residues_key(self):
        """缺少 residues 键应报告。"""
        cfg = {"molecules": {}, "md": {}}
        issues = validate_config(cfg)
        assert any("不能为空" in i for i in issues), f"issues: {issues}"

    def test_zero_total_molecules(self):
        """总分子数为零应报告。"""
        cfg = {
            "residues": {"A": 0},
            "molecules": {"A": {"charge": 0}},
            "md": {},
        }
        issues = validate_config(cfg)
        assert any("0" in i for i in issues), f"issues: {issues}"

    def test_very_large_total(self):
        """总分子数超量应警告。"""
        cfg = {
            "residues": {"A": 20000},
            "molecules": {"A": {"charge": 0}},
            "md": {},
        }
        issues = validate_config(cfg)
        assert any("很大" in i for i in issues), f"issues: {issues}"

    def test_residue_not_in_molecules(self):
        """residues 中引用了未定义的分子名。"""
        cfg = {
            "residues": {"UnknownMol": 10},
            "molecules": {"LiTFSI": {"charge": 0}},
            "md": {},
        }
        issues = validate_config(cfg)
        assert any("UnknownMol" in i for i in issues), f"issues: {issues}"

    # ---- MD 参数验证 ----

    def test_ref_t_out_of_range_low(self):
        """ref_t 低于 0 应报告。"""
        cfg = {
            "residues": {"A": 1},
            "molecules": {"A": {"charge": 0}},
            "md": {"ref_t": -10},
        }
        issues = validate_config(cfg)
        assert any("ref_t" in i for i in issues), f"issues: {issues}"

    def test_ref_t_out_of_range_high(self):
        """ref_t 超过 2000 应报告。"""
        cfg = {
            "residues": {"A": 1},
            "molecules": {"A": {"charge": 0}},
            "md": {"ref_t": 5000},
        }
        issues = validate_config(cfg)
        assert any("ref_t" in i for i in issues), f"issues: {issues}"

    def test_dt_too_small(self):
        """dt 过小应报告。"""
        cfg = {
            "residues": {"A": 1},
            "molecules": {"A": {"charge": 0}},
            "md": {"dt": 0.00001},
        }
        issues = validate_config(cfg)
        assert any("dt" in i for i in issues), f"issues: {issues}"

    def test_dt_too_large(self):
        """dt 过大应报告。"""
        cfg = {
            "residues": {"A": 1},
            "molecules": {"A": {"charge": 0}},
            "md": {"dt": 0.1},
        }
        issues = validate_config(cfg)
        assert any("dt" in i for i in issues), f"issues: {issues}"

    def test_ref_t_at_boundary(self):
        """边界值 ref_t=2000 应通过。"""
        cfg = {
            "residues": {"A": 1},
            "molecules": {"A": {"charge": 0}},
            "md": {"ref_t": 2000},
        }
        issues = validate_config(cfg)
        temp_issues = [i for i in issues if "ref_t" in i]
        assert len(temp_issues) == 0, f"边界值不应报告: {temp_issues}"

    def test_dt_at_boundary(self):
        """边界值 dt=0.01 应通过。"""
        cfg = {
            "residues": {"A": 1},
            "molecules": {"A": {"charge": 0}},
            "md": {"dt": 0.01},
        }
        issues = validate_config(cfg)
        dt_issues = [i for i in issues if "dt" in i]
        assert len(dt_issues) == 0, f"边界值不应报告: {dt_issues}"

    # ---- 缺少 MD 字段时使用默认值 ----

    def test_missing_md_section(self):
        """缺少 md 段应静默通过（使用默认值）。"""
        cfg = {
            "residues": {"A": 1},
            "molecules": {"A": {"charge": 0}},
        }
        issues = validate_config(cfg)
        assert issues == [], f"缺少 md 段应通过: {issues}"


# ============================================================
# _apply_defaults
# ============================================================

class TestApplyDefaults:
    """默认值合并逻辑测试。"""

    def test_applies_backend_default(self):
        """应设置默认 backend。"""
        cfg = {"residues": {"A": 1}, "molecules": {"A": {}}}
        result = _apply_defaults(cfg)
        assert result["backend"] == "g16"

    def test_applies_md_defaults(self):
        """应设置所有 MD 默认值。"""
        cfg = {"residues": {"A": 1}, "molecules": {"A": {}}}
        result = _apply_defaults(cfg)
        md = result["md"]
        assert md["dt"] == 0.001
        assert md["ref_t"] == 298.15
        assert md["ref_p"] == 1.01325
        assert md["eq_ns"] == 10
        assert md["prod_ns"] == 10
        assert md["tcoupl"] == "V-rescale"
        assert md["tau_t"] == 0.5
        assert md["pcoupl"] == "C-rescale"
        assert md["constraints"] == "hbonds"
        assert md["rcoulomb"] == 1.0
        assert md["rvdw"] == 1.0
        assert md["coulombtype"] == "PME"
        assert md["vdwtype"] == "Cut-off"

    def test_applies_topology_defaults(self):
        """应设置拓扑默认值。"""
        cfg = {"residues": {"A": 1}, "molecules": {"A": {}}}
        result = _apply_defaults(cfg)
        topo = result["topology"]
        assert topo["backend"] == "sobtop"
        assert topo["force_field"] == "gaff"
        assert topo["default_net_charge"] == 0
        assert topo["default_lbcc"] is False

    def test_user_values_override_defaults(self):
        """用户值应覆盖默认值。"""
        cfg = {
            "residues": {"A": 1},
            "molecules": {"A": {"charge": -1}},
            "md": {"dt": 0.002, "eq_ns": 50},
            "topology": {"force_field": "opls"},
        }
        result = _apply_defaults(cfg)
        assert result["md"]["dt"] == 0.002
        assert result["md"]["eq_ns"] == 50
        assert result["topology"]["force_field"] == "opls"
        # 未指定的字段应回退到默认值
        assert result["md"]["prod_ns"] == 10

    def test_molecule_defaults(self):
        """分子默认值应正确合并。"""
        cfg = {
            "residues": {"A": 1, "B": 2},
            "molecules": {
                "A": {"charge": 0},
                # B 缺失 —— 应从 available_residues 补全
            },
        }
        result = _apply_defaults(cfg)
        assert "B" in result["molecules"]
        assert "charge" in result["molecules"]["B"]

    def test_preserves_extra_keys(self):
        """不应删除未知键。"""
        cfg = {
            "residues": {"A": 1},
            "molecules": {"A": {}},
            "custom_field": "keep_me",
        }
        result = _apply_defaults(cfg)
        assert result["custom_field"] == "keep_me"


# ============================================================
# 配置验证导出
# ============================================================

class TestConfigValidationExport:
    """验证模块只导出领域函数，tool 名由 toolist 层承载。"""

    def test_validate_config_exists(self):
        """validate_config 在 llm_config 中确实存在。"""
        from willy.llm_config import validate_config
        assert callable(validate_config)

    def test_validate_config_accepts_valid_config(self):
        from willy.llm_config import validate_config
        issues = validate_config({
            "residues": {"A": 1},
            "molecules": {"A": {"charge": 0}},
            "md": {},
        })
        assert issues == []


# ============================================================
# config.json 文件写入 (apply_config)
# ============================================================

class TestApplyConfig:
    """apply_config 测试（需要 mock 以避免写入真实 config.json）。"""

    def test_apply_config_writes_file(self, tmp_project_root, monkeypatch):
        """apply_config 应将完整配置写入 config.json。"""
        # 覆盖 CONFIG_PATH 以使用临时路径
        import willy.llm_config as llm_cfg
        monkeypatch.setattr(llm_cfg, "CONFIG_PATH", tmp_project_root / "config.json")

        cfg = {
            "residues": {"EMC": 50},
            "molecules": {"EMC": {"charge": 0}},
        }
        result = llm_cfg.apply_config(cfg, backup=False)
        assert result.exists()
        saved = json.loads(result.read_text())
        assert saved["backend"] == "g16"  # 默认值
        assert "EMC" in saved["molecules"]

    def test_apply_config_creates_backup(self, tmp_project_root, monkeypatch):
        """apply_config 在 backup=True 时应创建 .bak 文件。"""
        import willy.llm_config as llm_cfg
        config_path = tmp_project_root / "config.json"
        # 确保原始文件存在
        config_path.write_text(json.dumps({"original": True}))
        monkeypatch.setattr(llm_cfg, "CONFIG_PATH", config_path)

        cfg = {"residues": {"A": 1}, "molecules": {"A": {}}}
        llm_cfg.apply_config(cfg, backup=True)

        backup_path = tmp_project_root / "config.json.bak"
        assert backup_path.exists(), "应创建备份文件"
        original = json.loads(backup_path.read_text())
        assert original.get("original") is True
