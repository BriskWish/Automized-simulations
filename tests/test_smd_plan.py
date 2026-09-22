"""Pure configuration tests for controlled SMD proposal operations."""

from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from willy import agent_config, workflow_config
from willy.quantum import smd_solvents
from willy.quantum.input_audit import audit_quantum_inputs
from willy.simulation.protocol import default_md_config


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    directory = tmp_path / "struct" / "smd_solvents"
    directory.mkdir(parents=True)
    (directory / "gaussian_builtin.json").write_text(json.dumps({"solvents": {
        "Acetone": {"epsilon": "20.493"}, "Water": {"epsilon": "78.3553"},
    }}))
    (directory / "gaussian_manual.json").write_text('{"solvents": {}}')
    monkeypatch.setattr(smd_solvents, "get_project_root", lambda: tmp_path)
    monkeypatch.setattr(agent_config, "ROOT", tmp_path)
    monkeypatch.setattr(workflow_config, "ROOT", tmp_path)
    monkeypatch.setattr(workflow_config, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(agent_config, "_DS", None)
    for name in ("A", "B"):
        (tmp_path / "struct" / f"{name}.gjf").write_text(
            f"#p HF/STO-3G\n\n{name}\n\n0 1\nHe 0 0 0\n\n"
        )
    monkeypatch.setattr(agent_config, "_available_residues", lambda: {"A": {}, "B": {}})
    return directory


def _config(**extra):
    return {
        "backend": "g16", "residues": {"A": 1, "B": 2},
        "molecules": {name: {"charge": 0, "spin": 1} for name in ("A", "B")},
        "md": default_md_config(), **extra,
    }


def _pending(config=None):
    summary = "**模拟方案确认**\n\n原方案"
    history = [{"role": "assistant", "content": summary}]
    return history, agent_config.create_pending_launch_plan(config or _config(), summary)


def _reply(message, config=None):
    history, plan = _pending(config)
    before = deepcopy(plan)
    updates = list(agent_config.chat(message, history, plan))
    assert updates[-1][3] is plan
    assert plan == before
    return updates[-1][1][-1]["content"]


@pytest.mark.parametrize("message", [
    "epsilon=20 epsinf=1.8", "EPSILON:20，EPSINF:1.8", "eps=20; epsinf=1.8",
    "登记溶剂 epsilon=20 epsinf=1.8", "请注册人工溶剂 epsilon=20 epsinf=1.8",
    "介电常数20，极限介电常数1.8", "静态介电常数为20；光学介电常数为1.8",
    "介电常数：20，高频介电常数：1.8", "ε=20 εinf=1.8", "ε 20，ε∞ 1.8",
    "ε=20，ε_inf=1.8", "Ε=20，ΕINF=1.8", "epsilon=20，极限介电常数1.8",
])
def test_explicit_values_register_default_without_model_or_plan_mutation(catalog, message):
    smd_solvents.register_manual_solvent("DEFAULT_1", 10, 1)
    reply = _reply(message)
    record = smd_solvents.resolve_exact("default_2")
    assert record.epsilon == "20" and record.epsinf == "1.8"
    assert "default_2" in reply and "不是完整 SMD 参数化" in reply
    assert "登记未修改或确认模拟方案" in reply


@pytest.mark.parametrize("message", [
    "登记溶剂 MyMix epsilon=20 epsinf=1.8",
    "注册溶剂 name=MyMix epsilon=20 epsinf=1.8",
    "epsilon=20 epsinf=1.8 名称=MyMix",
    "新增手工溶剂 命名为MyMix，epsilon=20，epsinf=1.8",
    "登记溶剂 MyMix，介电常数20，极限介电常数1.8",
    "请注册人工溶剂 MyMix ε=20 εinf=1.8",
    "介电常数20，极限介电常数1.8，名称=MyMix",
])
def test_named_registration_and_case_insensitive_duplicates(catalog, message):
    assert "MyMix" in _reply(message)
    before = (catalog / "gaussian_manual.json").read_bytes()
    reply = _reply("登记溶剂 mYmIx epsilon=21 epsinf=2")
    assert "被拒绝" in reply and "大小写" in reply
    assert (catalog / "gaussian_manual.json").read_bytes() == before


@pytest.mark.parametrize("message", [
    "登记溶剂 WATER epsilon=20 epsinf=1.8",
    "epsilon=1 epsinf=2", "epsilon=nan epsinf=1", "epsilon=true epsinf=1",
    "epsilon=20", "epsilon=20 epsilon=21 epsinf=1.8", "epsinf=0.9 epsilon=20",
    "介电常数1，极限介电常数2", "介电常数20", "极限介电常数1.8",
    "ε=20，介电常数21，εinf=1.8", "介电常数20，epsinf=1.8，极限介电常数2",
    "介电常数nan，极限介电常数1.8",
])
def test_bad_registration_never_changes_catalog(catalog, message):
    before = (catalog / "gaussian_manual.json").read_bytes()
    assert "被拒绝" in _reply(message)
    assert (catalog / "gaussian_manual.json").read_bytes() == before


@pytest.mark.parametrize("message", [
    "不要登记溶剂 MyMix epsilon=20 epsinf=1.8", "例如 epsilon=20 epsinf=1.8",
    "epsilon=20 epsinf=1.8 可以吗？", "解释 epsilon=20 epsinf=1.8 的含义",
    "如果登记溶剂 MyMix epsilon=20 epsinf=1.8，会怎样？",
    "例如介电常数20，极限介电常数1.8", "不要登记溶剂 MyMix，介电常数20，极限介电常数1.8",
    "登记溶剂 示例，介电常数20，极限介电常数1.8",
    "登记溶剂 先别，ε=20，εinf=1.8", "介电常数20，极限介电常数1.8，可以吗？",
    "登记溶剂 ε=20 εinf=1.8 取消", "解释 ε=20 εinf=1.8 的含义",
    "假设登记溶剂 MyMix，ε=20，ε∞=1.8", "请勿登记介电常数20，极限介电常数1.8",
    "“介电常数20，极限介电常数1.8”", "例如 ε=20，εinf=1.8",
    "介电常数NaN，極限=1.8",
])
def test_questions_examples_and_negations_are_not_write_authority(catalog, message):
    before = (catalog / "gaussian_manual.json").read_bytes()
    assert agent_config._solvent_catalog_reply(message) is None
    assert (catalog / "gaussian_manual.json").read_bytes() == before


@pytest.mark.parametrize("message", [
    "查询溶剂 water", "溶剂库中有 WATER 吗？", "查看溶剂库", "有哪些溶剂？",
    "当前有哪些溶剂", "当前有哪些溶剂？", "现在溶剂有哪些", "查询solvents",
    "查询 solvents", "查询Solvent", "查询solvents water",
])
def test_solvent_queries_do_not_need_model_or_invalidate_plan(catalog, message):
    before = (catalog / "gaussian_manual.json").read_bytes()
    assert "Water" in _reply(message)
    assert (catalog / "gaussian_manual.json").read_bytes() == before


def test_fuzzy_query_never_silently_selects_candidate(catalog):
    reply = _reply("查询溶剂 acet")
    assert "Acetone" in reply and "候选不会自动采用" in reply


def test_semantic_tools_are_read_only(catalog):
    tools = {tool["function"]["name"] for tool in agent_config.SEMANTIC_TOOLS}
    assert "tools_lookup_solvent" in tools
    assert "tools_register_solvent" not in tools
    assert "tools_register_solvent" not in agent_config._PLAN_READ_ONLY_TOOL_NAMES
    prompt = agent_config.get_system_prompt()
    assert "solvent_selection" in prompt and "不是完整 SMD 参数化" in prompt


def test_default_selection_applies_to_all_and_per_molecule_override_wins(catalog):
    config = _config(solvent_selection={"default": "water", "molecules": {"B": "gas"}})
    before = deepcopy(config)
    bound = agent_config._prepare_plan_solvents(config)
    assert "solvent_selection" not in bound
    assert bound["molecules"]["A"]["solvent"] == "Water"
    assert bound["molecules"]["B"]["solvent"] == "gas"
    assert bound["molecules"]["A"]["solvent_ref"] == smd_solvents.resolve_exact("water").as_dict()
    assert config == before
    assert not workflow_config.validate_config(bound)


@pytest.mark.parametrize("message", ["溶剂使用 Water", "A 1 B 2，所有分子的溶剂设为 Water", "solvent=Water"])
def test_explicit_global_assignment_fills_an_incomplete_semantic_draft(catalog, message):
    name = agent_config._requested_global_solvent(message)
    assert name == "Water"
    bound = agent_config._prepare_plan_solvents(_config(), requested_default=name)
    assert all(molecule["solvent"] == "Water" for molecule in bound["molecules"].values())
    with pytest.raises(ValueError, match="用户明确请求"):
        agent_config._prepare_plan_solvents(_config(solvent_selection={"default": "gas"}), requested_default=name)


@pytest.mark.parametrize("message", ["仅 A 的溶剂使用 Water", "溶剂使用 Water 可以吗？", "为什么溶剂使用 Water", "不要溶剂使用 Water"])
def test_per_molecule_or_question_text_is_not_a_global_assignment(catalog, message):
    assert agent_config._requested_global_solvent(message) is None


@pytest.mark.parametrize("backend,solvent", [("g16", "Acetone"), ("g09", "Acetone"), ("orca", "gas")])
def test_config_defaults_bind_shared_optimization_sp_snapshot(catalog, backend, solvent):
    config = _config(backend=backend)
    completed = workflow_config._apply_defaults(config)
    for molecule in completed["molecules"].values():
        assert molecule["solvent"] == solvent
        assert molecule["solvent_ref"]["name"] == solvent
        assert "solvent" not in config["molecules"]["A"]


@pytest.mark.parametrize("selection", [
    {"default": "unknown"}, {"default": None}, {"default": {"name": "Water"}},
    {"molecules": {"C": "Water"}}, {"molecules": []},
    {"register": {"name": "Invented", "epsilon": 20, "epsinf": 2}},
])
def test_invalid_semantic_selection_cannot_write_catalog(catalog, selection):
    before = (catalog / "gaussian_manual.json").read_bytes()
    with pytest.raises(ValueError):
        agent_config._prepare_plan_solvents(_config(solvent_selection=selection))
    assert (catalog / "gaussian_manual.json").read_bytes() == before


def test_model_cannot_create_manual_snapshot_without_registration(catalog):
    config = _config()
    config["molecules"]["A"].update(solvent="Invented", solvent_ref={
        "name": "Invented", "source": "manual", "manual": True, "epsilon": "20", "epsinf": "2",
    })
    with pytest.raises(ValueError, match="未登记"):
        agent_config._prepare_plan_solvents(config)
    assert smd_solvents.resolve_exact("Invented") is None


def test_existing_manual_snapshot_survives_catalog_changes_and_revision(catalog):
    record = smd_solvents.register_manual_solvent("MyMix", 20, 1.8)
    config = agent_config._prepare_plan_solvents(_config(solvent_selection={"default": record.name}))
    (catalog / "gaussian_manual.json").write_text('{"solvents": {"MyMix": {"epsilon": "30", "epsinf": "2"}}}')
    completed = workflow_config._apply_defaults(config)
    assert completed["molecules"]["A"]["solvent_ref"] == record.as_dict()
    draft = {"backend": "g16", "residues": {"A": 2, "B": 2}, "solvent_selection": {"molecules": {"B": "gas"}}}
    revised = agent_config._prepare_plan_solvents(draft, config)
    assert revised["molecules"]["A"]["solvent_ref"] == record.as_dict()
    assert revised["molecules"]["B"]["solvent"] == "gas"
    assert config["molecules"]["B"]["solvent"] == "MyMix"


@pytest.mark.parametrize("solvent,snapshot", [
    ("Water", {"name": "Acetone", "source": "builtin", "manual": False, "epsilon": "20.493", "epsinf": None}),
    ("gas", {"name": "gas", "source": "manual", "manual": True, "epsilon": "20", "epsinf": "2"}),
    ("Water", {"name": "Water", "source": "none", "manual": False}),
    ("Water", {"name": "Water", "source": "builtin", "manual": False, "epsilon": "99", "epsinf": None}),
    ("Water", {"name": "Water", "source": "manual", "manual": True, "epsilon": "20", "epsinf": "2"}),
    ("Manual", {"name": "Manual", "source": "manual", "manual": True, "epsilon": "nan", "epsinf": "2"}),
    ("Manual", {"name": "Manual", "source": "manual", "manual": True, "epsilon": "1", "epsinf": "2"}),
    ("Manual", {"name": "Manual", "source": "unknown"}),
    ("Water", []), ("Water", {}),
])
def test_config_rejects_mismatched_or_invalid_snapshots_before_overwriting(catalog, solvent, snapshot):
    config = _config()
    config["molecules"]["A"].update(solvent=solvent, solvent_ref=snapshot)
    before = deepcopy(config)
    assert any("solvent" in issue for issue in workflow_config.validate_config(config))
    with pytest.raises(ValueError):
        workflow_config._apply_defaults(config)
    assert config == before


@pytest.mark.parametrize("backend,override", [("orca", None), (" ORCA ", "g16"), ("g16", "orca")])
def test_orca_cannot_bypass_smd_rejection_with_molecule_backend(catalog, backend, override):
    config = _config(backend=backend)
    config["molecules"]["A"].update(solvent="Water", _backend=override)
    assert any("ORCA" in issue for issue in workflow_config.validate_config(config))
    with pytest.raises(ValueError, match="ORCA"):
        workflow_config._apply_defaults(config)


@pytest.mark.parametrize("solvent", ["Water", "gas"])
def test_summary_reports_multiline_scrf_override_or_removal(catalog, solvent):
    path = catalog.parent / "A.gjf"
    path.write_text("#p HF/STO-3G\n SCRF=(\n PCM,\n Solvent=Acetone)\n\nA\n\n0 1\nHe 0 0 0\n\n")
    config = agent_config._prepare_plan_solvents(_config(solvent_selection={"default": solvent}))
    before = path.read_bytes()
    summary = agent_config.summarize(config)
    expected = "移除原始 SCRF（含多行设置）" if solvent == "gas" else "覆盖原始 SCRF（含多行设置）"
    assert expected in summary
    assert "优化/单点共享此名称与 solvent_ref" in summary
    assert path.read_bytes() == before


def test_manual_summary_never_claims_complete_smd_parameters(catalog):
    smd_solvents.register_manual_solvent("MyMix", 20, 1.8)
    config = agent_config._prepare_plan_solvents(_config(solvent_selection={"default": "MyMix"}))
    summary = agent_config.summarize(config)
    assert "Eps=20，EpsInf=1.8" in summary
    assert "介电近似" in summary
    assert "不是完整 SMD 参数化" in summary
    payload = json.loads(summary.splitlines()[0].removeprefix("[[WILLY_PLAN_DATA:").removesuffix("]]"))
    assert all("Eps=20，EpsInf=1.8" in item["solvent_note"] for item in payload["structure"])
    assert all("不是完整 SMD 参数化" in item["solvent_note"] for item in payload["structure"])


@pytest.mark.parametrize("backend,solvent,source", [
    ("g16", "Acetone", "builtin"), ("g09", "Acetone", "builtin"), ("orca", "gas", "none"),
])
@pytest.mark.parametrize("explicit_gas", [False, True])
def test_summary_resolves_shared_workflow_defaults_without_mutating_config(catalog, backend, solvent, source, explicit_gas):
    config = _config(backend=backend)
    if explicit_gas:
        for molecule in config["molecules"].values():
            molecule["solvent"] = "gas"
        solvent, source = "gas", "none"
    before = deepcopy(config)
    effective = workflow_config._apply_defaults(config)
    summary = agent_config.summarize(config)
    marker = summary.splitlines()[0]
    data = json.loads(marker.removeprefix("[[WILLY_PLAN_DATA:").removesuffix("]]"))
    for item in data["structure"]:
        molecule = effective["molecules"][item["name"]]
        assert item["solvent"] == molecule["solvent"] == solvent
        assert item["solvent_source"] == molecule["solvent_ref"]["source"] == source
        assert f"{item['name']}：{solvent}（{source}）" in summary
    assert config == before


@pytest.mark.parametrize("snapshot", [{}, {"name": "Acetone", "source": "none", "manual": False}])
def test_summary_does_not_hide_invalid_solvent_snapshots(catalog, snapshot):
    config = _config()
    config["molecules"]["A"].update(solvent="Water", solvent_ref=snapshot)
    with pytest.raises(ValueError, match="solvent_ref"):
        agent_config.summarize(config)


def _scripted_chat(monkeypatch, catalog, semantic, strict, *, audit_tool=True, prefix=None):
    calls = []
    messages = list(prefix or [])
    messages.append(SimpleNamespace(tool_calls=None, content=json.dumps(semantic)))
    components = [{"name": name, "count": count} for name, count in sorted(semantic["residues"].items())]
    audit_call = SimpleNamespace(id="audit", function=SimpleNamespace(
        name="tools_inspect_quantum_inputs",
        arguments=json.dumps({"backend": semantic.get("backend", "g16"), "components": components}),
    ))
    messages.append(SimpleNamespace(tool_calls=[audit_call] if audit_tool else None, content="审计完成"))
    messages.append(SimpleNamespace(tool_calls=None, content=json.dumps(strict)))

    def create(**kwargs):
        calls.append(kwargs)
        assert messages, "configuration failures must not cause model retries"
        return SimpleNamespace(choices=[SimpleNamespace(message=messages.pop(0))])

    def handle(name, arguments):
        assert name == "tools_inspect_quantum_inputs"
        return json.dumps(audit_quantum_inputs(
            arguments["backend"], {item["name"]: item["count"] for item in arguments["components"]},
            struct_dir=catalog.parent,
        ))

    monkeypatch.setattr(agent_config, "_DS", SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    monkeypatch.setattr(agent_config, "handle_tool_call", handle)
    monkeypatch.setattr(agent_config, "apply_config", lambda *_args, **_kwargs: pytest.fail("proposal must not write config"))
    monkeypatch.setattr(agent_config, "start_pipeline", lambda *_args, **_kwargs: pytest.fail("proposal must not launch"))
    return calls


@pytest.mark.parametrize("backend,solvent,source", [
    ("g16", "Acetone", "builtin"), ("g09", "Acetone", "builtin"), ("orca", "gas", "none"),
])
def test_chat_strict_omission_still_displays_actual_opt_sp_solvent(catalog, monkeypatch, backend, solvent, source):
    if backend == "orca":
        for name in ("A", "B"):
            (catalog.parent / f"{name}.inp").write_text("! HF STO-3G Opt\n* xyz 0 1\nHe 0 0 0\n*\n")
    semantic = {"backend": backend, "residues": {"A": 1, "B": 2}}
    calls = _scripted_chat(monkeypatch, catalog, semantic, _config(backend=backend))
    updates = list(agent_config.chat(f"A 1 B 2，使用 {backend}", []))
    plan = updates[-1][3]
    assert plan is not None, updates[-1][1][-1]["content"]
    assert len(calls) == 3
    summary = updates[-1][1][-1]["content"]
    data = json.loads(summary.splitlines()[0].removeprefix("[[WILLY_PLAN_DATA:").removesuffix("]]"))
    effective = workflow_config._apply_defaults(plan["config"])
    for item in data["structure"]:
        assert item["solvent"] == effective["molecules"][item["name"]]["solvent"] == solvent
        assert item["solvent_source"] == source
    assert agent_config._pending_launch_config(plan, updates[-1][1]) == plan["config"]


def test_chat_binds_all_solvents_after_required_audit_and_waits_for_confirmation(catalog, monkeypatch):
    semantic = {"backend": "g16", "residues": {"A": 1, "B": 2}, "solvent_selection": {"default": "Water"}}
    calls = _scripted_chat(monkeypatch, catalog, semantic, _config())
    original = {path.name: path.read_bytes() for path in catalog.parent.glob("*.gjf")}
    updates = list(agent_config.chat("A 1 B 2，溶剂使用 Water", []))
    plan = updates[-1][3]
    assert plan is not None, updates[-1][1][-1]["content"]
    assert len(calls) == 3
    assert calls[1]["tools"] == agent_config.QUANTUM_AUDIT_TOOLS
    assert "solvent_ref" in calls[2]["messages"][1]["content"]
    for molecule in plan["config"]["molecules"].values():
        assert molecule["solvent"] == "Water"
        assert molecule["solvent_ref"] == smd_solvents.resolve_exact("Water").as_dict()
        assert molecule["charge"] == 0 and molecule["spin"] == 1
    assert agent_config._pending_launch_config(plan, updates[-1][1]) == plan["config"]
    assert original == {path.name: path.read_bytes() for path in catalog.parent.glob("*.gjf")}


def test_solvent_revision_keeps_previous_per_molecule_choice(catalog, monkeypatch):
    previous = agent_config._prepare_plan_solvents(_config(solvent_selection={"default": "gas"}))
    semantic = {"backend": "g16", "residues": {"A": 1, "B": 2}, "solvent_selection": {"molecules": {"A": "Water"}}}
    calls = _scripted_chat(monkeypatch, catalog, semantic, _config())
    history, plan = _pending(previous)
    updates = list(agent_config.chat("仅 A 的溶剂使用 Water", history, plan))
    revised = updates[-1][3]
    assert len(calls) == 3 and revised is not plan
    assert revised["config"]["molecules"]["A"]["solvent"] == "Water"
    assert revised["config"]["molecules"]["B"]["solvent"] == "gas"
    assert plan["config"] == previous


@pytest.mark.parametrize("solvent", ["Water", "gas", "MyMix"])
def test_chat_global_revision_replaces_all_inherited_choices_and_old_snapshots(catalog, monkeypatch, solvent):
    smd_solvents.register_manual_solvent("MyMix", 20, 1.8)
    previous = agent_config._prepare_plan_solvents(_config(solvent_selection={"default": "Acetone", "molecules": {"B": "gas"}}))
    semantic = deepcopy(previous)
    calls = _scripted_chat(monkeypatch, catalog, semantic, _config())
    history, plan = _pending(previous)
    updates = list(agent_config.chat(f"溶剂使用 {solvent}", history, plan))
    revised = updates[-1][3]
    assert revised is not plan, updates[-1][1][-1]["content"]
    assert len(calls) == 3
    for molecule in revised["config"]["molecules"].values():
        assert molecule["solvent"] == solvent
        assert molecule["solvent_ref"]["name"] == solvent
    assert plan["config"] == previous
    assert agent_config._pending_launch_config(revised, updates[-1][1]) == revised["config"]


def test_chat_non_solvent_edit_keeps_distinct_manual_and_gas_snapshots(catalog, monkeypatch):
    smd_solvents.register_manual_solvent("MyMix", 20, 1.8)
    previous = agent_config._prepare_plan_solvents(_config(solvent_selection={"default": "MyMix", "molecules": {"B": "gas"}}))
    semantic = {"backend": "g16", "residues": {"A": 3, "B": 2}}
    strict = _config(residues=semantic["residues"])
    calls = _scripted_chat(monkeypatch, catalog, semantic, strict)
    history, plan = _pending(previous)
    updates = list(agent_config.chat("A 改为3个", history, plan))
    revised = updates[-1][3]
    assert revised is not plan and len(calls) == 3
    for name in ("A", "B"):
        assert revised["config"]["molecules"][name]["solvent_ref"] == previous["molecules"][name]["solvent_ref"]
    assert revised["config"]["residues"]["A"] == 3


@pytest.mark.parametrize("mutation", ["name", "snapshot", "charge", "backend", "residues"])
def test_strict_stage_cannot_change_solvent_or_quantum_audit_scope(catalog, monkeypatch, mutation):
    semantic = _config(solvent_selection={"default": "Water"})
    strict = agent_config._prepare_plan_solvents(semantic)
    if mutation == "name":
        strict["molecules"]["A"]["solvent"] = "gas"
    elif mutation == "snapshot":
        strict["molecules"]["A"]["solvent_ref"]["epsilon"] = "999"
    elif mutation == "charge":
        strict["molecules"]["A"]["charge"] = 10
    elif mutation == "backend":
        strict["backend"] = "orca"
    else:
        strict["residues"]["A"] = 20
    calls = _scripted_chat(monkeypatch, catalog, semantic, strict)
    updates = list(agent_config.chat("A 1 B 2，溶剂使用 Water", []))
    if mutation == "charge":
        assert updates[-1][3]["config"]["molecules"]["A"]["charge"] == 0
    else:
        assert updates[-1][3] is None
        assert "未通过" in updates[-1][1][-1]["content"]
    assert len(calls) == 3


def test_solvent_selection_does_not_bypass_mandatory_quantum_tool(catalog, monkeypatch):
    semantic = _config(solvent_selection={"default": "Water"})
    calls = _scripted_chat(monkeypatch, catalog, semantic, _config(), audit_tool=False)
    updates = list(agent_config.chat("A 1 B 2，溶剂使用 Water", []))
    assert len(calls) == 2
    assert updates[-1][3] is None
    assert "审计" in updates[-1][1][-1]["content"]


def test_model_register_tool_call_is_rejected_without_library_write(catalog, monkeypatch):
    tool_call = SimpleNamespace(id="write", function=SimpleNamespace(
        name="tools_register_solvent", arguments=json.dumps({"name": "Injected", "epsilon": 20, "epsinf": 2}),
    ))
    semantic = _config(solvent_selection={"default": "Water"})
    prefix = [SimpleNamespace(tool_calls=[tool_call], content="")]
    calls = _scripted_chat(monkeypatch, catalog, semantic, _config(), prefix=prefix)
    before = (catalog / "gaussian_manual.json").read_bytes()
    updates = list(agent_config.chat("A 1 B 2，溶剂使用 Water", []))
    assert updates[-1][3] is not None
    assert "语义阶段不允许该工具" in calls[1]["messages"][-1]["content"]
    assert (catalog / "gaussian_manual.json").read_bytes() == before
