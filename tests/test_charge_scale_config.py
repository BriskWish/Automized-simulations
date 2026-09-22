"""Deterministic charge-scale configuration and proposal display contracts."""

from copy import deepcopy
from decimal import Decimal, localcontext
import json
import math
from types import SimpleNamespace

import pytest

import willy.agent_config as agent_config
import willy.workflow_config as workflow_config
from willy.charge_scaling import DEFAULT_ION_CHARGE_SCALE, validate_ion_charge_scale
from willy.config_schema import validate_config_schema
from willy.quantum.input_audit import audit_quantum_inputs
from willy.simulation.mdp import MdpConfig, _build_eq
from willy.simulation.protocol import EQ_SEGMENT_NAMES, default_md_config, require_valid_md_config


pytestmark = pytest.mark.contract


def _config():
    return {
        "backend": "g16",
        "residues": {"Charged": 1, "Counter": 2, "Neutral": 3},
        "molecules": {
            "Charged": {"charge": 2, "spin": 1},
            "Counter": {"charge": -1, "spin": 2},
            "Neutral": {"charge": 0, "spin": 1},
        },
        "md": default_md_config(),
        "topology": {"backend": "sobtop", "force_field": "gaff_uff"},
    }


@pytest.mark.parametrize("value", [hundredths / 100 for hundredths in range(60, 101)] + [1, 0.800])
def test_helper_accepts_every_hundredth_without_changing_the_value(value):
    result = validate_ion_charge_scale(value)
    assert type(result) is float
    assert result == value


def test_helper_default_and_trailing_zero_equivalence():
    assert DEFAULT_ION_CHARGE_SCALE == validate_ion_charge_scale() == 1.0
    assert validate_ion_charge_scale(0.8) == validate_ion_charge_scale(0.80) == validate_ion_charge_scale(0.800)


def test_validation_is_independent_of_the_callers_decimal_precision():
    with localcontext() as decimal_context:
        decimal_context.prec = 1
        assert validate_ion_charge_scale(0.81) == 0.81
        assert agent_config._requested_ion_charge_scale("电荷缩放81%") == 0.81
        with pytest.raises(ValueError, match="精度"):
            validate_ion_charge_scale(0.801)
        with pytest.raises(ValueError, match="精度"):
            agent_config._requested_ion_charge_scale("电荷缩放80.1%")


@pytest.mark.parametrize("value", [
    True, False, None, "0.80", "nan", [], {}, (), complex(0.8), Decimal("0.8"),
    float("nan"), float("inf"), -float("inf"), 0, -1, 0.59, 1.01, 10 ** 1000,
    math.nextafter(0.6, 0), math.nextafter(1.0, 2), 0.801, 0.605, 0.999,
    math.nextafter(0.8, 1), math.nextafter(0.8, 0),
])
def test_invalid_scale_is_rejected_by_helper_schema_and_defaults(value):
    config = _config()
    config["ion_charge_scale"] = value
    with pytest.raises(ValueError, match="ion_charge_scale"):
        validate_ion_charge_scale(value)
    assert not validate_config_schema(config).valid
    assert any("ion_charge_scale" in issue for issue in workflow_config.validate_config(config))
    with pytest.raises(ValueError, match="ion_charge_scale"):
        workflow_config._apply_defaults(config)


def test_scale_is_known_and_defaulting_does_not_change_quantum_properties():
    config = _config()
    original = deepcopy(config)
    completed = workflow_config._apply_defaults(config)
    assert completed["ion_charge_scale"] == 1.0
    completed["ion_charge_scale"] = 0.8
    completed = workflow_config._apply_defaults(completed)
    assert completed["ion_charge_scale"] == 0.8
    assert validate_config_schema(completed).unknown_top_level_fields == ()
    for name, molecule in config["molecules"].items():
        assert completed["molecules"][name]["charge"] == molecule["charge"]
        assert completed["molecules"][name]["spin"] == molecule["spin"]
    assert config == original


def test_scale_does_not_change_integer_charge_balance_rules():
    config = _config()
    config["ion_charge_scale"] = 0.6
    config["residues"]["Counter"] = 1
    assert any("总电荷为 +1" in issue for issue in workflow_config.validate_config(config))
    config["non_neutral_confirmed"] = True
    assert workflow_config.validate_config(config) == []


@pytest.mark.parametrize("topology", [
    {"backend": "oplsaa", "force_field": "oplsaa"},
    {"backend": "opls", "force_field": "opls"},
    {"backend": "ligpargen", "force_field": "oplsaa"},
])
def test_non_sobtop_rejects_scaling_even_for_neutral_only_systems(topology):
    config = _config()
    config["residues"] = {"Neutral": 1}
    config["topology"] = topology
    assert workflow_config.validate_config(config) == []
    config["ion_charge_scale"] = 1.0
    assert workflow_config.validate_config(config) == []
    config["ion_charge_scale"] = 0.8
    issues = workflow_config.validate_config(config)
    assert any("非 sobtop" in issue and ".chg" in issue for issue in issues)


def test_documented_legacy_sobtop_alias_is_not_mistaken_for_opls():
    config = _config()
    config["ion_charge_scale"] = 0.8
    config["topology"] = {"backend": "ligpargen", "force_field": "gaff"}
    assert workflow_config.validate_config(config) == []


def test_explicit_apply_writes_default_and_rejects_invalid_without_writing(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    monkeypatch.setattr(workflow_config, "CONFIG_PATH", path)
    config = _config()
    workflow_config.apply_config(config, backup=False)
    assert json.loads(path.read_text())["ion_charge_scale"] == 1.0
    original_bytes = path.read_bytes()
    for value in (0.59, 1.01, 0.801, True, "0.8"):
        config["ion_charge_scale"] = value
        with pytest.raises(ValueError, match="ion_charge_scale"):
            workflow_config.apply_config(config)
        assert path.read_bytes() == original_bytes
    config["ion_charge_scale"] = 0.8
    config["topology"] = {"backend": "oplsaa", "force_field": "oplsaa"}
    with pytest.raises(ValueError, match="非 sobtop"):
        workflow_config.apply_config(config)
    assert path.read_bytes() == original_bytes
    assert not path.with_suffix(".json.bak").exists()


def test_read_only_validation_and_summary_leave_legacy_snapshot_unchanged(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(_config(), indent=2))
    original_bytes = path.read_bytes()
    config = json.loads(original_bytes)
    original = deepcopy(config)
    assert validate_config_schema(config).valid
    assert workflow_config.validate_config(config) == []
    summary = agent_config.summarize(config)
    plan = agent_config.create_pending_launch_plan(config, summary)
    history = [{"role": "assistant", "content": summary}]
    assert agent_config._pending_launch_config(plan, history) == original
    marker = next(line for line in summary.splitlines() if line.startswith("[[WILLY_PLAN_DATA:"))
    payload = json.loads(marker.removeprefix("[[WILLY_PLAN_DATA:").removesuffix("]]"))
    assert payload["environment"]["charge_scale"] == "1.00"
    assert config == original
    assert "ion_charge_scale" not in config
    assert path.read_bytes() == original_bytes


@pytest.mark.parametrize("message, expected", [
    ("A 1，电荷缩放0.80", 0.8),
    ("所有带电组分统一缩放到0.600", 0.6),
    ("离子电荷缩放因子设为1.00", 1.0),
    ("config.ion_charge_scale=0.800", 0.8),
    ('{"ion_charge_scale": 0.80}', 0.8),
    ("电荷缩放系数设置成8e-1", 0.8),
    ("charge scaling factor = 0.8", 0.8),
    ("电荷乘以0.8", 0.8),
    ("采用0.8倍部分电荷", 0.8),
    ("电荷缩放至80%", 0.8),
    ("取消电荷缩放", 1.0),
    ("电荷不缩放", 1.0),
    ("把温度改为350K", None),
])
def test_original_request_parsing(message, expected):
    assert agent_config._requested_ion_charge_scale(message) == expected


@pytest.mark.parametrize("message", [
    "电荷缩放设为0.59", "电荷缩放1.01", "电荷缩放0.801", "缩放到NaN",
    "ion_charge_scale=true", "ion_charge_scale=False", "ion_charge_scale=null",
    'ion_charge_scale="0.8"', "ion_charge_scale=[]", "ion_charge_scale={}",
    "电荷缩放为Infinity", "电荷缩放系数设置成0.1", "电荷缩放为abc",
    "电荷缩放0.8e", "电荷缩放0.8abc", "电荷缩放0.8/2",
    "电荷缩放0.8，另一个缩放0.9", "电荷缩放80.1%",
    "电荷缩放0.800000000000000000000000000001",
    "电荷缩放1.000000000000000000000000000001",
    "电荷缩放1e999999999%", "电荷缩放1e-999999999%",
])
def test_invalid_original_requests_stop_before_any_model_can_sanitize(monkeypatch, message):
    def forbidden(*_args, **_kwargs):
        pytest.fail("invalid scale request must stop before consulting a model")

    monkeypatch.setattr(agent_config, "_classify_framework_design_intent", forbidden)
    monkeypatch.setattr(agent_config, "_DS", SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=forbidden))))
    with pytest.raises(ValueError, match="ion_charge_scale"):
        agent_config._requested_ion_charge_scale(message)
    updates = list(agent_config.chat(message, []))
    assert updates[-1][3] is None
    assert "缩放请求无效" in updates[-1][1][-1]["content"]


def _scripted_model(monkeypatch, tmp_path, strict_config, semantic_scale=None):
    struct_dir = tmp_path / "struct"
    struct_dir.mkdir(exist_ok=True)
    for name, molecule in _config()["molecules"].items():
        (struct_dir / f"{name}.gjf").write_text(
            f"#p b3lyp/6-31g\n\n{name}\n\n{molecule['charge']} {molecule['spin']}\nH 0 0 0\n\n"
        )
    semantic = {"backend": "g16", "residues": _config()["residues"]}
    if semantic_scale is not None:
        semantic["ion_charge_scale"] = semantic_scale
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            message = SimpleNamespace(tool_calls=None, content=json.dumps(semantic))
        elif len(calls) == 2:
            components = [{"name": name, "count": count} for name, count in sorted(semantic["residues"].items())]
            tool_call = SimpleNamespace(id="audit", function=SimpleNamespace(
                name="tools_inspect_quantum_inputs",
                arguments=json.dumps({"backend": "g16", "components": components}),
            ))
            message = SimpleNamespace(tool_calls=[tool_call], content="")
        elif len(calls) == 3:
            message = SimpleNamespace(tool_calls=None, content=json.dumps(strict_config))
        else:
            pytest.fail("deterministic rejection must not retry or repair the configuration")
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    monkeypatch.setattr(agent_config, "ROOT", tmp_path)
    monkeypatch.setattr(agent_config, "_available_residues", lambda: _config()["molecules"])
    monkeypatch.setattr(agent_config, "_DS", SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    monkeypatch.setattr(agent_config, "handle_tool_call", lambda _name, args: json.dumps(
        audit_quantum_inputs(
            args["backend"],
            {item["name"]: item["count"] for item in args["components"]},
            struct_dir=struct_dir,
        )
    ))
    return calls


@pytest.mark.parametrize("message, expected", [("Charged 1 Counter 2 Neutral 3", 1.0), ("Charged 1 Counter 2 Neutral 3，电荷缩放0.800", 0.8)])
def test_generated_plan_defaults_or_binds_scale_without_changing_quantum_inputs(monkeypatch, tmp_path, message, expected):
    config = _config()
    calls = _scripted_model(monkeypatch, tmp_path, config)
    updates = list(agent_config.chat(message, []))
    plan = updates[-1][3]
    assert len(calls) == 3
    assert plan["config"]["ion_charge_scale"] == expected
    assert plan["config"]["molecules"] == config["molecules"]
    marker = next(line for line in updates[-1][1][-1]["content"].splitlines() if line.startswith("[[WILLY_PLAN_DATA:"))
    payload = json.loads(marker.removeprefix("[[WILLY_PLAN_DATA:").removesuffix("]]"))
    assert payload["environment"]["charge_scale"] == f"{expected:.2f}"
    assert agent_config._pending_launch_config(plan, updates[-1][1]) == plan["config"]
    assert not (tmp_path / "config.json").exists()


@pytest.mark.parametrize("value", [0.59, 1.01, 0.801, True, "0.8", float("nan"), 0.9])
@pytest.mark.parametrize("stage", ["semantic", "strict"])
def test_model_cannot_replace_requested_scale_or_hide_an_invalid_draft(monkeypatch, tmp_path, value, stage):
    config = _config()
    config["ion_charge_scale"] = value if stage == "strict" else 0.8
    calls = _scripted_model(monkeypatch, tmp_path, config, value if stage == "semantic" else None)
    updates = list(agent_config.chat("Charged 1 Counter 2 Neutral 3，电荷缩放0.8", []))
    assert len(calls) == (1 if stage == "semantic" else 3)
    assert updates[-1][3] is None
    assert "缩放参数校验" in updates[-1][1][-1]["content"]


@pytest.mark.parametrize("message, expected", [("把 EQ 高温改为600K", 0.8), ("电荷缩放0.70", 0.7), ("取消电荷缩放", 1.0)])
def test_revisions_preserve_or_explicitly_change_scale_and_replace_summary(monkeypatch, tmp_path, message, expected):
    previous = _config()
    previous["ion_charge_scale"] = 0.8
    old_summary = agent_config.summarize(previous)
    history = [{"role": "assistant", "content": old_summary}]
    old_plan = agent_config.create_pending_launch_plan(previous, old_summary)
    candidate = _config()
    candidate["md"]["eq"]["high_temperature"] = 600
    calls = _scripted_model(monkeypatch, tmp_path, candidate)
    updates = list(agent_config.chat(message, history, old_plan))
    new_plan = updates[-1][3]
    assert len(calls) == 3
    assert '"ion_charge_scale":0.8' in calls[0]["messages"][1]["content"]
    assert new_plan["config"]["ion_charge_scale"] == expected
    assert old_plan["config"] == previous
    assert agent_config._pending_launch_config(old_plan, updates[-1][1]) is None
    assert agent_config._pending_launch_config(new_plan, updates[-1][1]) == new_plan["config"]
    started = []
    monkeypatch.setattr(agent_config, "start_pipeline", lambda config: started.append(config) or SimpleNamespace(message="started", state="started"))
    confirmed = list(agent_config.chat("确认运行", updates[-1][1], new_plan))
    assert started == [new_plan["config"]]
    assert confirmed[-1][3] is None


def test_generation_rejects_non_sobtop_instead_of_ignoring_scale(monkeypatch, tmp_path):
    config = _config()
    config["topology"] = {"backend": "oplsaa", "force_field": "oplsaa"}
    _scripted_model(monkeypatch, tmp_path, config)
    updates = list(agent_config.chat("Charged 1 Counter 2 Neutral 3，OPLS-AA，电荷缩放0.8", []))
    assert updates[-1][3] is None
    assert "非 sobtop" in updates[-1][1][-1]["content"]


@pytest.mark.parametrize("value, backend", [(0.59, "sobtop"), (0.801, "sobtop"), (True, "sobtop"), (0.8, "oplsaa")])
def test_confirmation_revalidates_scale_before_reserving_or_writing(monkeypatch, value, backend):
    config = _config()
    config["ion_charge_scale"] = value
    config["topology"] = {"backend": backend, "force_field": "gaff_uff" if backend == "sobtop" else "oplsaa"}
    monkeypatch.setattr(agent_config, "write_startup_audit", lambda *_args: None)
    monkeypatch.setattr(agent_config, "reserve_pipeline_launch", lambda *_args: pytest.fail("must not reserve a run"))
    monkeypatch.setattr(agent_config, "apply_config", lambda *_args: pytest.fail("must not write config"))
    summary = "**模拟方案确认**\n旧方案"
    plan = agent_config.create_pending_launch_plan(config, summary)
    updates = list(agent_config.chat("确认运行", [{"role": "assistant", "content": summary}], plan))
    assert "ion_charge_scale" in updates[-1][1][-1]["content"]
    assert updates[-1][3] == plan


@pytest.mark.parametrize("partial", [False, True])
def test_summary_matches_mdp_six_segments_and_normalized_defaults(partial):
    config = _config()
    config["ion_charge_scale"] = 0.8
    config["md"]["eq"]["high_temperature"] = 600
    config["md"]["eq"]["segments_ns"]["heat"] = 2.0000004
    if partial:
        config["md"] = {
            "schema_version": 2,
            "eq": {"high_temperature": 600, "segments_ns": {"heat": 2.0000004}},
        }
    original = deepcopy(config)
    normalized, warnings = require_valid_md_config(config["md"])
    _, metadata = _build_eq(MdpConfig(normalized, 1, warnings))
    summary = agent_config.summarize(config)
    marker = next(line for line in summary.splitlines() if line.startswith("[[WILLY_PLAN_DATA:"))
    payload = json.loads(marker.removeprefix("[[WILLY_PLAN_DATA:").removesuffix("]]"))
    rows = payload["md_steps"][1:7]
    assert len(rows) == 6
    for index, name in enumerate(EQ_SEGMENT_NAMES):
        assert name in rows[index]["label"]
        assert f"{metadata['segments'][name]['actual_ns']:.9g} ns" in rows[index]["meta"]
        temperatures = metadata["annealing_temperature_k"]
        assert f"{temperatures[index]:.9g}K→{temperatures[index + 1]:.9g}K" in rows[index]["meta"]
    assert "600K→600K" in rows[1]["meta"]
    assert "400K→400K" in rows[3]["meta"]
    assert "298K→298K" in rows[5]["meta"]
    assert "按时间步对齐" in rows[0]["meta"]
    assert payload["environment"]["charge_scale"] == "0.80"
    assert payload["structure"][0]["optimization_level"] == "未指定"
    assert payload["md_steps"][0] == {"label": "EM", "meta": "能量最小化"}
    assert payload["md_steps"][-1]["label"] == "PROD"
    assert "下一步将开始结构优化。" in summary
    assert "确认无误后回复“运行”即可开始。" in summary
    assert config == original


def test_scale_change_invalidates_config_fingerprint_and_default_summary_has_plateau():
    config = _config()
    config["ion_charge_scale"] = 0.8
    summary = agent_config.summarize(config)
    assert "500K→500K" in summary
    history = [{"role": "assistant", "content": summary}]
    plan = agent_config.create_pending_launch_plan(config, summary)
    plan["config"]["ion_charge_scale"] = 0.7
    assert agent_config._pending_launch_config(plan, history) is None


def test_prompt_and_revision_context_include_the_scaling_contract(monkeypatch):
    monkeypatch.setattr(agent_config, "_available_residues", lambda: {})
    prompt = agent_config.get_system_prompt()
    assert "ion_charge_scale" in prompt
    assert "0.60..1.00" in prompt
    assert "非 sobtop" in prompt
    config = _config()
    config["ion_charge_scale"] = 0.8
    assert agent_config._config_outline(config)["ion_charge_scale"] == 0.8
