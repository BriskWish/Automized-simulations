"""Configuration validation and explicit schema-v2 migration tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from willy.workflow_config import (
    _apply_defaults,
    apply_config,
    migrate_and_adopt_md_config,
    migrate_md_config,
    validate_config,
)
from willy.config_schema import WORKFLOW_CONFIG_SCHEMA_VERSION, validate_config_schema
from willy.simulation.protocol import default_md_config


@pytest.fixture
def tmp_project_root(tmp_project_root):
    from willy.quantum import smd_solvents

    published_root = Path(smd_solvents.__file__).resolve().parents[3]
    catalog = tmp_project_root / "struct" / "smd_solvents"
    catalog.mkdir()
    builtin = published_root / "struct" / "smd_solvents" / "gaussian_builtin.json"
    (catalog / "gaussian_builtin.json").write_bytes(builtin.read_bytes())
    (catalog / "gaussian_manual.json").write_text('{"solvents": {}}', encoding="utf-8")
    return tmp_project_root


def _config(md=None, *, charge=0, count=1):
    return {
        "residues": {"A": count},
        "molecules": {"A": {"charge": charge, "spin": 1}},
        "md": default_md_config() if md is None else md,
        "box": {"packing_number_density_nm3": 6.0, "box_size": None, "tolerance": 2.0},
    }


def test_valid_v2_config_passes():
    assert validate_config(_config()) == []


@pytest.mark.parametrize("value", (0, -1, 1.5, "8", True, 4097))
def test_nproc_must_be_a_bounded_positive_integer(value):
    config = _config()
    config["defaults"] = {"mem": "5GB", "nproc": value}

    assert any("defaults.nproc" in issue for issue in validate_config(config))


def test_molecule_nproc_can_override_global_default():
    config = _config()
    config["defaults"] = {"mem": "5GB", "nproc": 8}
    config["molecules"]["A"]["nproc"] = 2

    assert validate_config(config) == []


def test_default_nproc_is_bounded_by_detected_cpu_count(monkeypatch):
    import willy.execution_resources as resources

    monkeypatch.setattr(resources, "local_cpu_count", lambda: 4)
    result = _apply_defaults({"residues": {"A": 1}, "molecules": {"A": {}}})

    assert result["defaults"]["nproc"] == 4


def test_local_cpu_count_prefers_process_affinity(monkeypatch):
    import willy.execution_resources as resources

    monkeypatch.setattr(resources.os, "sched_getaffinity", lambda _pid: {1, 3, 5})
    monkeypatch.setattr(resources.os, "cpu_count", lambda: 32)

    assert resources.local_cpu_count() == 3


def test_explicit_nproc_is_clamped_with_a_nonblocking_warning(monkeypatch):
    from willy.execution_resources import normalize_config_nproc

    result = normalize_config_nproc({
        "defaults": {"nproc": 8},
        "molecules": {"A": {"nproc": 12}},
    }, cpu_count=4)

    assert result.config["defaults"]["nproc"] == 4
    assert result.config["molecules"]["A"]["nproc"] == 4
    assert len(result.warnings) == 2
    assert all("当前系统检测到 4 核" in warning for warning in result.warnings)


def test_explicit_nproc_above_eight_is_allowed_when_the_host_supports_it():
    from willy.execution_resources import normalize_config_nproc

    result = normalize_config_nproc({"defaults": {"nproc": 12}}, cpu_count=16)

    assert result.config["defaults"]["nproc"] == 12
    assert result.warnings == ()


def test_runtime_nproc_resolution_also_bounds_legacy_snapshots(monkeypatch):
    import willy.execution_resources as resources

    monkeypatch.setattr(resources, "local_cpu_count", lambda: 3)

    assert resources.resolve_nproc(8) == 3
    assert resources.resolve_nproc(None) == 3


def test_execution_defaults_to_explicit_local_profileless_mode():
    result = _apply_defaults({"residues": {"A": 1}, "molecules": {"A": {"charge": 0, "spin": 1}}})

    assert result["execution"] == {
        "md": {
            "backend": "local",
            "profile": None,
            "retain_remote_run": True,
        }
    }


def test_outer_schema_keeps_unknown_extensions_but_reports_them_structurally():
    config = _config()
    config["future_extension"] = {"keep": True}

    validation = validate_config_schema(config)

    assert validation.valid
    assert validation.schema_version == WORKFLOW_CONFIG_SCHEMA_VERSION
    assert validation.unknown_top_level_fields == ("future_extension",)
    assert validate_config(config) == []


def test_outer_schema_rejects_malformed_nested_sections_before_defaults():
    config = _config()
    config["defaults"] = []
    config["molecules"]["A"] = "not-an-object"

    validation = validate_config_schema(config)
    issues = validate_config(config)

    assert "defaults 必须是对象" in validation.issues
    assert "molecules.A 必须是对象" in validation.issues
    assert "defaults 必须是对象" in issues
    assert "molecules.A 必须是对象" in issues


def test_execution_schema_rejects_private_connection_fields():
    config = _config()
    config["execution"] = {
        "md": {
            "backend": "ssh",
            "profile": "lab_gpu",
            "retain_remote_run": True,
            "ssh_host_alias": "must-not-be-here",
        }
    }

    issues = validate_config(config)

    assert "execution.md 包含不允许的字段" in issues


def test_execution_can_explicitly_stop_a_trial_after_eq():
    config = _config()
    config["execution"] = {
        "md": {"backend": "local", "profile": None, "retain_remote_run": True},
        "stop_after_stage": "eq",
    }

    assert validate_config(config) == []


@pytest.mark.parametrize("value", ("prod", "em", "", 9))
def test_execution_rejects_any_stop_scope_other_than_eq(value):
    config = _config()
    config["execution"] = {
        "md": {"backend": "local", "profile": None, "retain_remote_run": True},
        "stop_after_stage": value,
    }

    assert "execution.stop_after_stage 仅允许 eq 或 null" in validate_config(config)


def test_unknown_remote_profile_is_rejected_without_affecting_local_mode(tmp_path, monkeypatch):
    missing = tmp_path / "missing" / "remote_profiles.json"
    monkeypatch.setenv("WILLY_REMOTE_PROFILES_FILE", str(missing))
    remote = _config()
    remote["execution"] = {
        "md": {"backend": "ssh", "profile": "lab_gpu", "retain_remote_run": True}
    }

    assert any("profile" in issue for issue in validate_config(remote))
    assert validate_config(_config()) == []


def test_apply_config_rejects_invalid_outer_shape_without_writing(tmp_project_root, monkeypatch):
    import willy.workflow_config as workflow_config

    path = tmp_project_root / "config.json"
    original = path.read_text()
    monkeypatch.setattr(workflow_config, "CONFIG_PATH", path)
    invalid = _config()
    invalid["molecules"]["A"] = []

    with pytest.raises(ValueError, match="config.json 结构无效"):
        apply_config(invalid, backup=False)

    assert path.read_text() == original


def test_apply_config_preserves_forward_compatible_extension(tmp_project_root, monkeypatch):
    import willy.workflow_config as workflow_config

    path = tmp_project_root / "config.json"
    monkeypatch.setattr(workflow_config, "CONFIG_PATH", path)
    config = _config()
    config["future_extension"] = {"mode": "reserved"}

    apply_config(config, backup=False)

    assert json.loads(path.read_text())["future_extension"] == {"mode": "reserved"}


@pytest.mark.parametrize(
    ("config", "expected_issue"),
    [
        (None, "config 必须是对象"),
        ({"residues": ["A"], "molecules": {}}, "residues 必须是对象"),
        ({"residues": {"A": "many"}, "molecules": {"A": {}}}, "residues.A 必须是数值"),
        ({"residues": {"A": 1}, "molecules": [], "md": []}, "molecules 必须是对象"),
    ],
)
def test_malformed_config_returns_displayable_issues(config, expected_issue):
    issues = validate_config(config)

    assert expected_issue in issues


def test_legacy_durations_are_rejected_without_migration():
    issues = validate_config(_config({"eq_ns": 10, "prod_ns": 10}))
    assert any("旧字段" in issue for issue in issues)


def test_non_neutral_system_requires_confirmation():
    issues = validate_config(_config(charge=1))
    assert any("总电荷" in issue for issue in issues)
    config = _config(charge=1)
    config["non_neutral_confirmed"] = True
    assert validate_config(config) == []


def test_invalid_eq_and_prod_boundaries_are_reported():
    md = default_md_config()
    md["eq"]["segments_ns"]["hold_target"] = 0
    md["prod"]["duration_ns"] = 1
    issues = validate_config(_config(md))
    assert any("hold_target" in issue for issue in issues)
    assert any("prod.duration_ns" in issue for issue in issues)


def test_prod_temperature_must_match_eq_target():
    md = default_md_config()
    md["prod"]["temperature"] = 300
    issues = validate_config(_config(md))
    assert any("PROD 温度" in issue for issue in issues)


def test_special_system_rejects_isotropic_pressure_coupling():
    md = default_md_config()
    md["system_type"] = "interface"
    issues = validate_config(_config(md))
    assert any("各向同性" in issue for issue in issues)


def test_defaults_create_schema_v2_without_legacy_fields():
    result = _apply_defaults({"residues": {"A": 1}, "molecules": {"A": {}}})
    md = result["md"]
    assert md["schema_version"] == 2
    assert md["eq"]["segments_ns"]["hold_target"] == 2.0
    assert md["prod"]["duration_ns"] == 10.0
    assert "eq_ns" not in md and "prod_ns" not in md
    assert result["box"]["target_mass_density_g_cm3"] == 0.7
    assert "packing_number_density_nm3" not in result["box"]


def test_defaults_preserve_existing_number_density_snapshot():
    result = _apply_defaults({
        "residues": {"A": 1},
        "molecules": {"A": {}},
        "box": {"packing_number_density_nm3": 6.0},
    })

    assert result["box"]["packing_number_density_nm3"] == 6.0
    assert "target_mass_density_g_cm3" not in result["box"]


def test_defaults_preserve_an_explicit_trr_output_request():
    result = _apply_defaults({
        "residues": {"A": 1},
        "molecules": {"A": {}},
        "md": {"outputs": {"trr": True}},
    })

    assert result["md"]["outputs"]["trr"] is True


def test_defaults_preserve_an_explicit_oplsaa_force_field_request():
    result = _apply_defaults({
        "residues": {"A": 1},
        "molecules": {"A": {}},
        "topology": {"backend": "oplsaa", "force_field": "oplsaa"},
    })

    assert result["topology"]["backend"] == "oplsaa"
    assert result["topology"]["force_field"] == "oplsaa"


def test_config_prompt_describes_explicit_trr_and_oplsaa_requests():
    from willy.agent_config import get_system_prompt

    prompt = get_system_prompt()
    assert "md.outputs.trr=true" in prompt
    assert '"backend":"oplsaa"' in prompt


def test_defaults_preserve_legacy_fields_for_visible_rejection():
    result = _apply_defaults({
        "residues": {"A": 1},
        "molecules": {"A": {}},
        "md": {"eq_ns": 10, "prod_ns": 10},
    })
    assert result["md"]["eq_ns"] == 10
    assert any("旧字段" in issue for issue in validate_config(result))


def test_explicit_migration_writes_v2_and_mapping_without_overwriting_source(tmp_path):
    source = tmp_path / "config.json"
    legacy = _config({"ref_t": 310, "eq_ns": 10, "prod_ns": 12})
    legacy["box"] = {"density": 5.5, "box_size": None, "tolerance": 2.0}
    source.write_text(json.dumps(legacy))

    target, mapping, report = migrate_md_config(source)

    assert source.read_text() == json.dumps(legacy)
    migrated = json.loads(target.read_text())
    audit = json.loads(mapping.read_text())
    assert migrated["md"]["schema_version"] == 2
    assert migrated["md"]["prod"]["duration_ns"] == 12
    assert migrated["box"]["packing_number_density_nm3"] == 5.5
    assert audit["mapping"]["md.eq_ns"]["value"] == 10
    assert report["status"] == "migrated"


def test_confirmed_migration_adoption_replaces_active_config_with_valid_protocol(tmp_path):
    source = tmp_path / "config.json"
    legacy = _config({"ref_t": 298, "eq_ns": 5, "prod_ns": 10})
    legacy["box"] = {"density": 6.0, "box_size": None, "tolerance": 2.0}
    source.write_text(json.dumps(legacy))

    active, backup, audit, report = migrate_and_adopt_md_config(source)

    adopted = json.loads(active.read_text())
    assert active == source
    assert json.loads(backup.read_text()) == legacy
    assert adopted["md"]["schema_version"] == 2
    assert sum(adopted["md"]["eq"]["segments_ns"].values()) == 10.0
    assert "density" not in adopted["box"]
    assert validate_config(adopted) == []
    assert report["adoption"]["eq_duration_normalized_to_default"] is True
    assert json.loads(audit.read_text())["adoption"]["effective_eq_total_ns"] == 10.0


def test_annealing_temperatures_must_descend_strictly():
    md = default_md_config()
    md["eq"]["transition_temperature"] = md["eq"]["high_temperature"]
    issues = validate_config(_config(md))
    assert any("三点退火温度" in issue for issue in issues)


def test_apply_config_writes_v2_snapshot(tmp_project_root, monkeypatch):
    import willy.workflow_config as workflow_config

    path = tmp_project_root / "config.json"
    monkeypatch.setattr(workflow_config, "CONFIG_PATH", path)
    result = apply_config(_config(), backup=False)
    saved = json.loads(result.read_text())
    assert saved["md"]["schema_version"] == 2
    assert "eq_ns" not in saved["md"]
    assert saved["molecules"]["A"]["solvent"].casefold() == "acetone"
    assert saved["molecules"]["A"]["solvent_ref"]["source"] == "builtin"


def test_unknown_solvent_is_not_accepted_by_isolated_workflow_fixture(tmp_project_root, monkeypatch):
    import willy.workflow_config as workflow_config

    path = tmp_project_root / "config.json"
    monkeypatch.setattr(workflow_config, "CONFIG_PATH", path)
    original = path.read_bytes()
    config = _config()
    config["molecules"]["A"]["solvent"] = "UnregisteredSolvent"

    with pytest.raises(ValueError, match="未在 Gaussian 溶剂库中登记"):
        apply_config(config)

    assert path.read_bytes() == original
    assert not path.with_suffix(".json.bak").exists()


def test_apply_config_replaces_active_file_only_after_atomic_backup(tmp_project_root, monkeypatch):
    import willy.workflow_config as workflow_config

    path = tmp_project_root / "config.json"
    original = {"residues": {"OLD": 1}, "molecules": {"OLD": {"charge": 0}}}
    path.write_text(json.dumps(original))
    monkeypatch.setattr(workflow_config, "CONFIG_PATH", path)

    apply_config(_config())

    assert json.loads(path.with_suffix(".json.bak").read_text()) == original
    assert json.loads(path.read_text())["residues"] == {"A": 1}
