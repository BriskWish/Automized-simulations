"""
test_pipeline_orchestrator.py —— PipelineOrchestrator 错误处理测试。

重点测试：
1. 依赖缺失处理
2. 批量结果跳过逻辑
3. Agent 调用决策
4. StepRegistry 映射正确性
5. LLM 初始化优雅降级
6. 跳过分子过滤
"""

import json
from hashlib import sha256
import pytest
from pathlib import Path
from unittest.mock import ANY, MagicMock, patch, PropertyMock

from willy.errors import StepResult, StepError, ErrorKind
from willy.pipeline_orchestrator import PipelineOrchestrator
from willy.run_metadata import load_run_manifest
from willy.step_registry import STEP_REGISTRY


def test_cli_accepts_g09_backend():
    from run_pipeline import _parse_args

    no_llm, backend, run_dir, lock_fd, launch_token, pending_action_id, resume_from_step = _parse_args(["g09"])

    assert (no_llm, backend, run_dir, lock_fd, launch_token, pending_action_id, resume_from_step) == (
        False, "g09", None, None, None, None, None,
    )


# ============================================================
# StepRegistry 映射
# ============================================================

class TestStepRegistryLayerMapping:
    """步骤→层映射测试。"""

    def test_step_layer_mapping_is_complete(self):
        """步骤 1-10 应全部映射。"""
        for i in range(1, 11):
            assert STEP_REGISTRY.step(i).layer, f"步骤 {i} 缺少层映射"

    def test_step_layers_correct(self):
        """各步骤的层映射应正确。"""
        # 量子层：步骤 1-3（结构优化、mol2 转换、RESP 电荷）
        for i in [1, 2, 3]:
            assert STEP_REGISTRY.layer_for(i) == "quantum"
        # 拓扑层：步骤 4-5（拓扑生成、主拓扑）
        for i in [4, 5]:
            assert STEP_REGISTRY.layer_for(i) == "topology"
        # 模拟层：步骤 6-10（MDP、Packmol、EM、EQ、PROD）
        for i in [6, 7, 8, 9, 10]:
            assert STEP_REGISTRY.layer_for(i) == "simulation"

    def test_no_steps_beyond_10(self):
        """步骤 11+ 尚未定义。"""
        with pytest.raises(ValueError):
            STEP_REGISTRY.step(11)


# ============================================================
# PipelineOrchestrator 构造
# ============================================================

class TestPipelineOrchestratorInit:
    """初始化测试。"""

    @patch.dict('os.environ', {}, clear=True)
    def test_no_api_key_disables_llm(self, tmp_project_root, monkeypatch):
        """没有任何 LLM API Key 时，应禁用 LLM。"""
        import willy.pipeline_orchestrator as po
        monkeypatch.setattr(po, "ROOT", tmp_project_root)
        orch = PipelineOrchestrator(backend="g16", use_llm=True)
        assert orch.use_llm is False, \
            "如果没有 API 密钥，则应禁用 LLM"

    def test_use_llm_false_skips_init(self):
        """use_llm=False 时不应初始化 LLM 客户端。"""
        orch = PipelineOrchestrator(backend="g16", use_llm=False)
        assert orch.llm_client is None
        assert orch._agents[1] is None
        assert orch._agents[2] is None
        assert orch._agents[3] is None

    def test_backend_stored(self):
        orch = PipelineOrchestrator(backend="orca", use_llm=False)
        assert orch.backend == "orca"

    def test_g09_backend_is_a_supported_distinct_backend(self):
        orch = PipelineOrchestrator(backend="g09", use_llm=False)
        assert orch.backend == "g09"

    def test_unknown_quantum_backend_is_rejected(self):
        with pytest.raises(ValueError, match="g16、g09 或 orca"):
            PipelineOrchestrator(backend="gaussian", use_llm=False)

    def test_configured_model_is_injected_into_all_repair_agents(self, monkeypatch):
        import willy.pipeline_orchestrator as po
        from willy.llm_config import LLMSettings

        client = MagicMock()
        settings = LLMSettings(
            api_key="test-secret",
            base_url="https://example.test/v1",
            model="compatible-repair-model",
            source="environment",
        )
        monkeypatch.setattr(po, "configured_llm_client", lambda root: (client, settings))

        orch = PipelineOrchestrator(backend="g16", use_llm=True)

        assert orch.llm_model == "compatible-repair-model"
        assert {agent.model for agent in orch._agents.values() if agent} == {"compatible-repair-model"}

    def test_new_run_never_inherits_previous_done_steps(self, tmp_path, monkeypatch):
        """A fresh launch must not infer a resume from the global status file."""
        import willy.pipeline_state as pstate

        monkeypatch.setattr(pstate, "get_project_root", lambda: tmp_path)
        (tmp_path / "status.json").write_text(json.dumps({
            "state": "escalated", "done_steps": [1],
            "extra": {"run_id": "md_previous"},
        }))

        orch = PipelineOrchestrator(backend="g16", use_llm=False)

        assert orch._resume_from == 0
        assert orch._resume_run_dir is None
        assert orch._sm._status.done_steps == []

    def test_explicit_resume_requires_the_original_run_directory(self, tmp_path, monkeypatch):
        import willy.pipeline_state as pstate

        monkeypatch.setattr(pstate, "get_project_root", lambda: tmp_path)
        with pytest.raises(ValueError, match="必须提供原运行目录"):
            PipelineOrchestrator(backend="g16", use_llm=False, resume_from=1)

        orch = PipelineOrchestrator(
            backend="g16", use_llm=False,
            resume_from=1, resume_run_dir="/tmp/md_previous",
        )
        assert orch._resume_from == 1
        assert orch._resume_run_dir == Path("/tmp/md_previous")



# ============================================================
# Public repair updates
# ============================================================

class TestPublicRepairUpdates:
    """Applied Simulation Agent changes must reach the state-machine snapshot."""

    def test_simulation_config_delta_is_labeled_and_unitized(self):
        orch = PipelineOrchestrator(backend="g16", use_llm=False)
        orch._sm.start_retry("simulation", 2, 3)
        before = {"md": {"tau_t": 0.5, "eq": {"tau_p": 1.0}}}
        after = {"md": {"tau_t": 2.0, "eq": {"tau_p": 2.0}}}

        orch._record_simulation_config_update(before, after, ["tau_t", "eq_tau_p"])

        assert orch._sm._status.adjustments == [
            {"name": "恒温耦合时间", "before": "0.5 ps", "after": "2 ps"},
            {"name": "EQ 压强耦合时间", "before": "1 ps", "after": "2 ps"},
        ]


# ============================================================
# _build_steps
# ============================================================

class TestBuildSteps:
    """步骤构建测试。"""

    def test_g16_backend_builds_10_steps(self):
        orch = PipelineOrchestrator(backend="g16", use_llm=False)
        steps = orch._build_steps(Path("/tmp/run"))
        assert len(steps) == 10

    def test_orca_backend_builds_10_steps(self):
        orch = PipelineOrchestrator(backend="orca", use_llm=False)
        steps = orch._build_steps(Path("/tmp/run"))
        assert len(steps) == 10

    def test_g09_backend_builds_its_own_quantum_modules(self):
        orch = PipelineOrchestrator(backend="g09", use_llm=False)
        steps = orch._build_steps(Path("/tmp/run"))

        assert len(steps) == 10
        assert [step[2] for step in steps[:2]] == ["struct_g09", "sp_g09"]
        assert steps[0][0] == "g09 优化 + formchk"

    def test_final_three_steps_are_gromacs_execution(self):
        orch = PipelineOrchestrator(backend="g16", use_llm=False)
        steps = orch._build_steps(Path("/tmp/run"))
        assert [step[0] for step in steps[-3:]] == [
            "GROMACS 能量最小化",
            "GROMACS 三点式退火平衡",
            "GROMACS 生产模拟",
        ]
        assert [step[2] for step in steps[-3:]] == [
            "gromacs_em", "gromacs_eq", "gromacs_prod",
        ]

    def test_step_structure(self):
        """每个步骤应为 5 元组: (label, func, dep_module, is_batch, layer_index)。"""
        orch = PipelineOrchestrator(backend="g16", use_llm=False)
        steps = orch._build_steps(Path("/tmp/run"))
        for s in steps:
            assert len(s) == 5, f"步骤应为 5 元组: {s}"
            label, func, dep_module, is_batch, layer_index = s
            assert isinstance(label, str)
            assert callable(func)
            assert dep_module is None or isinstance(dep_module, str)
            assert isinstance(is_batch, bool)
            assert isinstance(layer_index, int)
            assert 1 <= layer_index <= 3

    def test_step_1_is_batch(self):
        """第一步（结构优化）应是批量的。"""
        orch = PipelineOrchestrator(backend="g16", use_llm=False)
        steps = orch._build_steps(Path("/tmp/run"))
        assert steps[0][3] is True  # is_batch

    def test_step_5_is_not_batch(self):
        """第五步（主拓扑）不应是批量的。"""
        orch = PipelineOrchestrator(backend="g16", use_llm=False)
        steps = orch._build_steps(Path("/tmp/run"))
        assert steps[4][3] is False  # is_batch

    def test_all_pipeline_steps_receive_the_same_run_directory(self, tmp_path, monkeypatch):
        """正常流水线不得回退到项目根目录的共享输入或拓扑目录。"""
        import willy.pipeline_orchestrator as po
        from willy.quantum import chg_resp, struct_g16
        from willy.topology import backends, top_assembly
        from willy.simulation import mdp

        run_dir = tmp_path / "md_run" / "run-1"
        run_dir.mkdir(parents=True)
        config_path = run_dir / "config.json"
        config_path.write_text("{}")
        orch = PipelineOrchestrator(backend="g16", use_llm=False)
        orch._run_config_path = config_path

        struct_run = MagicMock(return_value=[])
        sp_run = MagicMock(return_value=[])
        chg_run = MagicMock(return_value=[])
        topo_run = MagicMock(return_value=[])
        top_run = MagicMock(return_value=StepResult("top", 5, True))
        mdp_run = MagicMock(return_value=StepResult("mdp", 6, True))
        monkeypatch.setattr(struct_g16, "run_all", struct_run)
        monkeypatch.setattr(po, "_g16_sp_and_mol2", sp_run)
        monkeypatch.setattr(chg_resp, "batch_make_chg", chg_run)
        monkeypatch.setattr(backends, "dispatch_topology", topo_run)
        monkeypatch.setattr(top_assembly, "build", top_run)
        monkeypatch.setattr(mdp, "build_all", mdp_run)

        steps = orch._build_steps(run_dir)
        for index in range(6):
            steps[index][1]()

        expected_config = str(config_path)
        expected_run = str(run_dir)
        struct_run.assert_called_once_with(
            config_path=expected_config, struct_dir=expected_run, on_progress=ANY,
        )
        sp_run.assert_called_once_with(expected_config, ANY, work_dir=run_dir)
        chg_run.assert_called_once_with(
            struct_dir=expected_run, config_path=expected_config, on_progress=ANY,
        )
        topo_run.assert_called_once_with(
            config_path=expected_config, workspace=run_dir, on_progress=ANY,
        )
        top_run.assert_called_once_with(config_path=expected_config, topo_dir=expected_run)
        mdp_run.assert_called_once_with(
            config_path=expected_config, output_dir=expected_run, on_progress=ANY,
        )


class TestPublicQuantumProgress:
    def test_g09_progress_callbacks_are_labeled_g09(self, tmp_path, monkeypatch):
        import willy.pipeline_orchestrator as po
        from willy.quantum import struct_g09

        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps({"molecules": {"Li": {}}, "defaults": {}}))
        (tmp_path / "Li.gjf").write_text("geometry")
        monkeypatch.setattr(struct_g09, "check_env_ready", lambda: [])
        monkeypatch.setattr(
            struct_g09, "run_one", lambda name, *_args: StepResult("struct_g09", 1, True),
        )

        activities = []
        results = struct_g09.run_all(str(config_path), str(tmp_path), activities.append)

        assert [(item["tool"], item["target"], item["current"], item["total"]) for item in activities] == [
            ("G09", "Li", 1, 1),
        ]
        assert [result.step_name for result in results] == ["struct_g09"]

        sp_activities = []
        po._g09_sp_and_mol2(str(config_path), sp_activities.append, work_dir=tmp_path)
        assert [(item["tool"], item["operation"], item["target"]) for item in sp_activities] == [
            ("G09", "单点计算与 mol2 转换", "Li"),
        ]

    def test_g16_orca_resp_and_step2_callbacks_are_structured(self, tmp_path, monkeypatch):
        import willy.pipeline_orchestrator as po
        from willy.quantum import chg_resp, struct_g16, struct_orca

        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps({
            "molecules": {"Li": {}, "NO3": {}}, "defaults": {},
        }))
        for name in ("Li", "NO3"):
            (tmp_path / f"{name}.gjf").write_text("geometry")
            (tmp_path / f"{name}.inp").write_text("! B3LYP def2-SVP Opt\n* xyz 0 1\nH 0 0 0\n*\n")

        monkeypatch.setattr(struct_g16, "check_env_ready", lambda: [])
        monkeypatch.setattr(struct_g16, "run_one", lambda name, *_: StepResult("struct_g16", 1, True))
        monkeypatch.setattr(struct_orca, "run_one", lambda name, *_: StepResult("struct_orca", 1, True))

        g16_activities, orca_activities = [], []
        g16_results = struct_g16.run_all(str(config_path), str(tmp_path), g16_activities.append)
        orca_results = struct_orca.run_all(str(config_path), str(tmp_path), orca_activities.append)

        assert [(item["tool"], item["target"], item["current"], item["total"]) for item in g16_activities] == [
            ("G16", "Li", 1, 2), ("G16", "NO3", 2, 2),
        ]
        assert [(item["tool"], item["target"], item["current"], item["total"]) for item in orca_activities] == [
            ("ORCA", "Li", 1, 2), ("ORCA", "NO3", 2, 2),
        ]
        assert [result.target for result in g16_results] == ["Li", "NO3"]
        assert [result.target for result in orca_results] == ["Li", "NO3"]

        for name in ("Li", "NO3"):
            (tmp_path / f"{name}_opt.fchk").write_text("fchk")
        monkeypatch.setattr(chg_resp, "make_chg", lambda *_args, **_kwargs: StepResult("chg_resp", 3, True))
        resp_activities = []
        resp_results = chg_resp.batch_make_chg(str(tmp_path), str(config_path), resp_activities.append)
        assert [(item["tool"], item["target"], item["current"], item["total"]) for item in resp_activities] == [
            ("Multiwfn", "Li", 1, 2), ("Multiwfn", "NO3", 2, 2),
        ]
        assert [result.target for result in resp_results] == ["Li", "NO3"]

        sp_activities = []
        for path in tmp_path.glob("*_opt.fchk"):
            path.unlink()
        po._g16_sp_and_mol2(str(config_path), sp_activities.append, work_dir=tmp_path)
        assert [(item["tool"], item["operation"], item["target"]) for item in sp_activities] == [
            ("G16", "单点计算与 mol2 转换", "Li"),
            ("G16", "单点计算与 mol2 转换", "NO3"),
        ]

        orca_sp_activities = []
        po._orca_sp_and_mol2(str(config_path), orca_sp_activities.append, work_dir=tmp_path)
        assert [(item["tool"], item["operation"], item["target"]) for item in orca_sp_activities] == [
            ("ORCA", "单点计算与 mol2 转换", "Li"),
            ("ORCA", "单点计算与 mol2 转换", "NO3"),
        ]

    def test_resp_rejects_an_incomplete_configured_molecule_set(self, tmp_path):
        """Step 3 must not silently accept 4/5 configured molecules."""
        from willy.quantum import chg_resp

        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps({"molecules": {"Li": {}, "NMP": {}}}))
        (tmp_path / "Li_opt.fchk").write_text("fchk")

        results = chg_resp.batch_make_chg(str(tmp_path), str(config_path))

        assert len(results) == 1
        assert results[0].success is False
        assert results[0].target == "NMP"
        assert results[0].error.kind is ErrorKind.FILE_NOT_FOUND


# ============================================================
# 运行目录隔离
# ============================================================

class TestRunWorkspace:
    """每次执行都从只读 struct/ 输入建立独立的运行工作区。"""

    def test_prepare_workspace_copies_precomputed_g16_input(self, tmp_path, monkeypatch):
        import willy.pipeline_orchestrator as po

        root = tmp_path / "project"
        struct_dir = root / "struct"
        struct_dir.mkdir(parents=True)
        source_gjf = struct_dir / "Li.gjf"
        source_gjf.write_text("#p b3lyp/6-31g\n\nLi\n\n1 1\nLi 0 0 0\n")
        source_fchk = struct_dir / "Li.fchk"
        source_fchk.write_text("optimized geometry")
        from willy.simulation.protocol import default_md_config
        source_config = {
            "residues": {"Li": 1},
            "molecules": {"Li": {"charge": 1, "spin": 1}},
            "md": default_md_config(),
            "box": {"packing_number_density_nm3": 6.0, "box_size": None, "tolerance": 2.0},
            "non_neutral_confirmed": True,
        }
        (root / "config.json").write_text(json.dumps(source_config))
        monkeypatch.setattr(po, "ROOT", root)

        run_dir = root / "md_run" / "run-1"
        orch = PipelineOrchestrator(backend="g16", use_llm=False)
        snapshot = orch._prepare_run_directory(run_dir)

        assert snapshot == run_dir / "config.json"
        saved_config = json.loads(snapshot.read_text())
        assert saved_config["residues"] == source_config["residues"]
        assert saved_config["molecules"] == source_config["molecules"]
        assert saved_config["md"]["run_seed"] > 0
        assert saved_config["execution"]["md"] == {
            "backend": "local",
            "profile": None,
            "retain_remote_run": True,
        }
        manifest = load_run_manifest(run_dir)["sections"]["registry"]["data"]
        assert manifest["config_sha256"] == sha256(snapshot.read_bytes()).hexdigest()
        assert (run_dir / "Li.fchk").read_text() == "optimized geometry"
        assert source_fchk.read_text() == "optimized geometry"

    def test_prepare_workspace_accepts_gjf_without_fchk(self, tmp_path, monkeypatch):
        """A new molecule reaches Step 1 silently when only its .gjf exists."""
        import willy.pipeline_orchestrator as po
        from willy.simulation.protocol import default_md_config

        root = tmp_path / "project"
        struct_dir = root / "struct"
        struct_dir.mkdir(parents=True)
        (struct_dir / "Li.gjf").write_text("#p b3lyp/6-31g\n\nLi\n\n1 1\nLi 0 0 0\n")
        (root / "config.json").write_text(json.dumps({
            "residues": {"Li": 1},
            "molecules": {"Li": {"charge": 1, "spin": 1}},
            "md": default_md_config(),
            "box": {"packing_number_density_nm3": 6.0, "box_size": None, "tolerance": 2.0},
            "non_neutral_confirmed": True,
        }))
        monkeypatch.setattr(po, "ROOT", root)

        run_dir = root / "md_run" / "run-1"
        orch = PipelineOrchestrator(backend="g16", use_llm=False)

        snapshot = orch._prepare_run_directory(run_dir)

        assert snapshot == run_dir / "config.json"
        assert (run_dir / "Li.gjf").read_text() == "#p b3lyp/6-31g\n\nLi\n\n1 1\nLi 0 0 0\n"
        assert not (run_dir / "Li.fchk").exists()
        assert not orch._sm._status.error

    def test_prepare_workspace_rejects_molecule_without_quantum_input(self, tmp_path, monkeypatch):
        import willy.pipeline_orchestrator as po
        from willy.simulation.protocol import default_md_config

        root = tmp_path / "project"
        (root / "struct").mkdir(parents=True)
        (root / "config.json").write_text(json.dumps({
            "residues": {"Li": 1},
            "molecules": {"Li": {"charge": 1, "spin": 1}},
            "md": default_md_config(),
            "box": {"packing_number_density_nm3": 6.0, "box_size": None, "tolerance": 2.0},
            "non_neutral_confirmed": True,
        }))
        monkeypatch.setattr(po, "ROOT", root)

        orch = PipelineOrchestrator(backend="g16", use_llm=False)
        with pytest.raises(ValueError, match="Li 缺少 .gjf 原始输入"):
            orch._prepare_run_directory(root / "md_run" / "run-1")


# ============================================================
# Step 2 产物契约
# ============================================================

class TestSinglePointMol2Contract:
    """SP 成功不等于 Step 2 成功；必须同时生成可用 .mol2。"""

    def test_g16_singlepoint_inherits_molecule_resource_override(self, tmp_path, monkeypatch):
        import willy.pipeline_orchestrator as po
        from willy.quantum import fchk_mol2, singlepoint_g16

        struct_dir = tmp_path / "struct"
        struct_dir.mkdir()
        (struct_dir / "Li.fchk").write_text("optimization result")
        opt_fchk = struct_dir / "Li_opt.fchk"
        opt_fchk.write_text("single point result")
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps({
            "molecules": {"Li": {"charge": 1, "spin": 1, "mem": "9GB", "nproc": 3}},
            "defaults": {"mem": "5GB", "nproc": 8},
        }))
        calls = []

        def fake_sp(*args, **kwargs):
            calls.append((args, kwargs))
            return StepResult("sp_g16", 2, True, outputs={"fchk": str(opt_fchk)})

        monkeypatch.setattr(singlepoint_g16, "run", fake_sp)
        monkeypatch.setattr(
            fchk_mol2, "convert",
            lambda *_args: StepResult("fchk_mol2", 2, True, outputs={"mol2": str(struct_dir / "Li.mol2")}),
        )

        result = po._g16_sp_and_mol2(str(config_path), work_dir=struct_dir)

        assert result[0].success is True
        assert calls[0][1]["mem"] == "9GB"
        assert calls[0][1]["nproc"] == 3

    def test_g16_mol2_failure_marks_step_2_failed(self, tmp_path, monkeypatch):
        import willy.pipeline_orchestrator as po
        from willy.quantum import singlepoint_g16

        struct_dir = tmp_path / "struct"
        struct_dir.mkdir()
        (struct_dir / "Li.fchk").write_text("optimization result")
        broken_opt_fchk = struct_dir / "Li_opt.fchk"
        broken_opt_fchk.write_text("not an fchk file\n")
        (tmp_path / "config.json").write_text(json.dumps({
            "molecules": {"Li": {"charge": 1, "spin": 1}},
        }))

        monkeypatch.setattr(po, "ROOT", tmp_path)
        monkeypatch.setattr(
            singlepoint_g16,
            "run",
            lambda *args, **kwargs: StepResult(
                step_name="sp_g16", step_index=2, success=True,
                outputs={"fchk": str(broken_opt_fchk)},
                artifacts=[str(broken_opt_fchk)],
            ),
        )

        result = po._g16_sp_and_mol2(str(tmp_path / "config.json"), None)[0]

        assert result.success is False
        assert result.error is not None
        assert result.error.kind is ErrorKind.UNKNOWN
        assert "mol2" not in result.outputs


# ============================================================
# _handle_batch_result
# ============================================================

class TestHandleBatchResult:
    """批量结果处理测试。"""

    @pytest.fixture
    def orch(self):
        return PipelineOrchestrator(backend="g16", use_llm=False)

    def make_batch_results(self, n_success=3, n_fail=0):
        results = []
        for i in range(n_success):
            results.append(StepResult(
                step_name="struct_maker", step_index=1, success=True,
                outputs={"fchk": f"/tmp/mol{i}.fchk"},
                artifacts=[f"/tmp/mol{i}.fchk", f"/tmp/mol{i}.log"],
                duration_s=60.0,
                target_type="molecule", target=f"mol{i}",
            ))
        for i in range(n_fail):
            results.append(StepResult(
                step_name="struct_maker", step_index=1, success=False,
                error=StepError(
                    kind=ErrorKind.GAUSSIAN_CRASH,
                    message=f"mol{i+3} 崩溃",
                    raw_output="segfault",
                ),
                target_type="molecule", target=f"mol{i+3}",
            ))
        return results

    def test_all_success(self, orch):
        results = self.make_batch_results(3, 0)
        artifacts = {}
        ok = orch._handle_batch_result(results, "struct", Path("/tmp"), artifacts, 1, 1)
        assert ok is True

    def test_some_fail_no_llm(self, orch):
        """在没有 LLM 的情况下，部分失败应返回 False。"""
        orch.use_llm = False
        results = self.make_batch_results(2, 1)
        artifacts = {}
        ok = orch._handle_batch_result(results, "struct", Path("/tmp"), artifacts, 1, 1)
        assert ok is False
    def test_molecule_failure_is_not_silently_filtered(self, orch):
        """模拟层不能跳过分子而保留旧拓扑和建盒结果。"""
        results = [
            StepResult(
                step_name="struct_maker", step_index=1, success=False,
                error=StepError(kind=ErrorKind.GAUSSIAN_CRASH,
                                message="LiTFSI 优化失败"),
                target_type="molecule", target="LiTFSI",
            ),
        ]
        artifacts = {}
        ok = orch._handle_batch_result(results, "struct", Path("/tmp"), artifacts, 1, 1)
        assert ok is False

    def test_artifacts_collected_for_success(self, orch):
        results = self.make_batch_results(2, 0)
        artifacts = {}
        orch._handle_batch_result(results, "struct", Path("/tmp"), artifacts, 1, 1)
        assert "fchk" in artifacts
        assert len(artifacts["fchk"]) == 2

    def test_empty_batch_is_a_failure(self, orch):
        ok = orch._handle_batch_result([], "SP + mol2", Path("/tmp"), {}, 1, 2)
        assert ok is False

    def test_each_failed_molecule_is_repaired_before_step_succeeds(self, orch):
        orch.use_llm = True
        mock_agent = MagicMock(name="QuantumAgent")
        mock_agent.name = "QuantumAgent"
        mock_agent.max_retries = 5
        mock_agent.handle_failure.side_effect = [
            StepResult("struct_maker", 1, True),
            StepResult("struct_maker", 1, True),
        ]
        orch._agents[1] = mock_agent
        failures = self.make_batch_results(n_success=0, n_fail=2)

        ok = orch._handle_batch_result(failures, "SP + mol2", Path("/tmp"), {}, 1, 2)

        assert ok is True
        assert mock_agent.handle_failure.call_count == 2


# ============================================================
# _handle_single_result
# ============================================================

class TestHandleSingleResult:
    """单个结果处理测试。"""

    @pytest.fixture
    def orch(self):
        return PipelineOrchestrator(backend="g16", use_llm=False)

    def test_success_collects_artifacts(self, orch):
        sr = StepResult(
            step_name="top_assembly", step_index=5, success=True,
            outputs={"topol": "/tmp/topol.top"},
            artifacts=["/tmp/topol.top"],
            duration_s=0.5,
        )
        artifacts = {}
        ok = orch._handle_single_result(sr, "主拓扑", Path("/tmp"), artifacts, 2, 5)
        assert ok is True
        assert "topol" in artifacts

    def test_failure_no_llm(self, orch):
        orch.use_llm = False
        sr = StepResult(
            step_name="top_assembly", step_index=5, success=False,
            error=StepError(kind=ErrorKind.CONFIG_INVALID,
                            message="topol.top 格式错误"),
        )
        artifacts = {}
        ok = orch._handle_single_result(sr, "主拓扑", Path("/tmp"), artifacts, 2, 5)
        assert ok is False

    def test_user_stop_request_bypasses_error_and_agent_retry(self, orch, tmp_path):
        from willy.simulation.manifest import request_safe_stop, stop_requested

        request_safe_stop(tmp_path)
        sr = StepResult(
            step_name="eq", step_index=9, success=False,
            error=StepError(ErrorKind.ENGINE_FAILURE, "eq: mdrun 失败"),
        )

        ok = orch._handle_single_result(sr, "GROMACS 三点式退火平衡", tmp_path, {}, 3, 9)

        assert ok is False
        assert orch._sm._status.state == "aborted"
        assert orch._sm._status.error == ""
        assert not stop_requested(tmp_path)

    def test_failure_without_error_details(self, orch):
        """错误为 None 时不应崩溃。"""
        orch.use_llm = False
        sr = StepResult(step_name="top_assembly", step_index=5, success=False, error=None)
        artifacts = {}
        ok = orch._handle_single_result(sr, "主拓扑", Path("/tmp"), artifacts, 2, 5)
        assert ok is False  # 不应崩溃


# ============================================================
# _invoke_agent
# ============================================================

class TestInvokeAgent:
    """Agent 调用测试。"""

    @pytest.fixture
    def orch(self):
        return PipelineOrchestrator(backend="g16", use_llm=False)

    def test_no_agent_available(self, orch):
        """agent 为 None 时必须升级，不能悬挂在 retrying。"""
        sr = StepResult(
            step_name="test", step_index=1, success=False,
            error=StepError(ErrorKind.UNKNOWN, "fixture failure"),
        )
        ok = orch._invoke_agent(sr, "test", Path("/tmp"), {}, layer_index=1)
        assert ok is False
        assert orch._sm._status.state == "escalated"
        assert orch._sm._status.retry_n == 0

    def test_agent_exception_is_escalated_and_decision_is_persisted(self, orch, tmp_path):
        run_dir = tmp_path / "md_run" / "md_agent_exception"
        run_dir.mkdir(parents=True)
        (run_dir / "config.json").write_text("{}")
        from willy.run_registry import RunRegistry

        orch._run_dir = run_dir
        orch._run_registry = RunRegistry(tmp_path)
        orch._run_registry.register_run(run_dir, backend="g16", total_steps=10)
        orch._sm.bind_status_path(run_dir / "status.json")
        orch._sm.bind_observer(orch._record_run_status)

        agent = MagicMock()
        agent.name = "SimulationAgent"
        agent.max_retries = 3
        agent.prompt_version = "simulation_agent_v1"
        agent.handle_failure.side_effect = RuntimeError("transport failed")
        orch._agents[3] = agent
        failure = StepResult(
            step_name="box", step_index=7, success=False,
            error=StepError(ErrorKind.PACKMOL_FAILED, "Packmol exited"),
        )

        ok = orch._invoke_agent(failure, "Packmol 盒子构建", run_dir, {}, layer_index=3)

        assert ok is False
        assert orch._sm._status.state == "escalated"
        assert orch._sm._status.retry_n == 0
        assert orch._sm._status.escalation["attempts_made"] == 0
        trace = (run_dir / "decision_trace.jsonl").read_text()
        assert '"result": "agent_exception"' in trace
        status = json.loads((run_dir / "status.json").read_text())
        assert status["state"] == "escalated"
        assert status["repair"] == {}

    def test_runtime_unavailable_skips_llm_parameter_repair(self, orch):
        agent = MagicMock()
        agent.name = "SimulationAgent"
        agent.max_retries = 3
        orch._agents[3] = agent
        failure = StepResult(
            step_name="box", step_index=7, success=False,
            error=StepError(
                ErrorKind.RUNTIME_UNAVAILABLE,
                "Packmol 与当前系统运行库不兼容",
            ),
        )

        ok = orch._handle_single_result(
            failure, "Packmol 盒子构建", Path("/tmp"), {}, 3, 7,
        )

        assert ok is False
        agent.handle_failure.assert_not_called()
        assert orch._sm._status.state == "escalated"
        assert orch._sm._status.escalation["attempts_made"] == 0
        assert "未执行自动参数修复" in orch._sm._status.escalation["actions_tried"][0]

    def test_agent_repair_success(self, orch):
        mock_agent = MagicMock()
        mock_agent.name = "TestAgent"
        mock_agent.max_retries = 5
        repair_result = StepResult(
            step_name="struct_maker", step_index=1, success=True,
            outputs={"fchk": "/tmp/fixed.fchk"},
            duration_s=30.0,
        )
        mock_agent.handle_failure.return_value = repair_result
        orch._agents[1] = mock_agent

        sr = StepResult(
            step_name="struct_maker", step_index=1, success=False,
            error=StepError(kind=ErrorKind.GAUSSIAN_CRASH, message="崩溃"),
        )
        artifacts = {"log": "/tmp/old.log"}
        ok = orch._invoke_agent(sr, "g16 优化", Path("/tmp"), artifacts, layer_index=1)
        assert ok is True
        assert "fchk" in artifacts

    def test_step_two_upstream_repair_requests_a_rerun(self, orch, tmp_path):
        """Creating only {name}.fchk cannot complete SP + mol2."""
        mock_agent = MagicMock()
        mock_agent.name = "QuantumAgent"
        mock_agent.max_retries = 5
        mock_agent.handle_failure.return_value = StepResult(
            step_name="struct_g16", step_index=1, success=True,
            outputs={"fchk": str(tmp_path / "NMP.fchk")},
        )
        orch._agents[1] = mock_agent
        (tmp_path / "NMP.fchk").write_text("optimized geometry")
        failed_step = StepResult(
            step_name="sp_mol2_g16", step_index=2, success=False,
            error=StepError(ErrorKind.FILE_NOT_FOUND, "NMP.fchk 不存在"),
            target_type="molecule", target="NMP",
        )

        ok = orch._invoke_agent(
            failed_step, "SP + mol2", tmp_path, {}, layer_index=1,
        )

        assert ok is True
        assert orch._rerun_step == 2

    def test_eq_upstream_mdp_repair_rolls_back_to_em_without_marking_eq_done(self, orch, tmp_path):
        """A configuration repair cannot turn an EQ failure into EQ success."""
        mock_agent = MagicMock()
        mock_agent.name = "SimulationAgent"
        mock_agent.max_retries = 3
        mock_agent.handle_failure.return_value = StepResult(
            step_name="mdp", step_index=6, success=True,
            outputs={"eq_mdp": str(tmp_path / "eq.mdp")},
            extra={"invalidated_stages": ["em", "eq", "prod"]},
        )
        orch._agents[3] = mock_agent
        for step in range(1, 9):
            orch._sm.mark_done(step)
        failed_eq = StepResult(
            step_name="eq", step_index=9, success=False,
            error=StepError(ErrorKind.EQUILIBRATION_FAILED, "EQ 验收未通过"),
            target_type="stage", target="eq",
        )

        ok = orch._invoke_agent(
            failed_eq, "GROMACS 三点式退火平衡", tmp_path, {}, layer_index=3,
        )

        assert ok is True
        assert orch._rollback_to_step == 8
        assert 9 not in orch._sm._status.done_steps
        assert orch._sm._status.done_steps == list(range(1, 8))

    def test_eq_failure_waits_for_user_confirmation_before_prod(self, tmp_path, monkeypatch):
        """An EQ failure creates a proposal; it must never auto-enter PROD."""
        import willy.pipeline_state as pstate
        from willy.simulation.protocol import default_md_config

        monkeypatch.setattr(pstate, "get_project_root", lambda: tmp_path)
        run_dir = tmp_path / "md_run" / "md_eq_repair_contract"
        run_dir.mkdir(parents=True)
        config_path = run_dir / "config.json"
        config_path.write_text(json.dumps({
            "residues": {"Li": 1},
            "molecules": {"Li": {"charge": 0, "spin": 1}},
            "md": default_md_config(),
        }))
        (run_dir / "md_manifest.json").write_text(json.dumps({
            "schema_version": 1,
            "stages": {"em": {"status": "accepted"}},
        }))

        def stage_files(stage: str) -> None:
            for suffix in ("tpr", "gro", "xtc", "edr", "cpt"):
                (run_dir / f"{stage}.{suffix}").write_text(f"{stage}-{suffix}")

        stage_files("em")
        orch = PipelineOrchestrator(backend="g16", use_llm=False)
        orch.use_llm = True
        monkeypatch.setattr(orch, "_prepare_run_directory", lambda _: config_path)

        eq_calls = 0
        prod_calls = 0

        def em_step():
            return StepResult("em", 8, True, target_type="stage", target="em")

        def eq_step():
            nonlocal eq_calls
            eq_calls += 1
            if eq_calls == 1:
                return StepResult(
                    "eq", 9, False,
                    error=StepError(ErrorKind.EQUILIBRATION_FAILED, "EQ 验收未通过"),
                    target_type="stage", target="eq",
                )
            stage_files("eq")
            manifest = json.loads((run_dir / "md_manifest.json").read_text())
            manifest["stages"]["em"] = {"status": "accepted"}
            manifest["stages"]["eq"] = {"status": "accepted"}
            (run_dir / "md_manifest.json").write_text(json.dumps(manifest))
            return StepResult("eq", 9, True, target_type="stage", target="eq")

        def prod_step():
            nonlocal prod_calls
            prod_calls += 1
            manifest = json.loads((run_dir / "md_manifest.json").read_text())
            assert manifest["stages"]["eq"]["status"] == "accepted"
            stage_files("prod")
            manifest["stages"]["prod"] = {"status": "completed"}
            (run_dir / "md_manifest.json").write_text(json.dumps(manifest))
            return StepResult("prod", 10, True, target_type="stage", target="prod")

        agent = MagicMock()
        agent.name = "SimulationAgent"
        agent.propose_eq_recovery.return_value = {
            "summary": "降低时间步长后重新验收 EQ。",
            "adjustments": [{
                "field": "dt", "after": 0.0005,
                "purpose": "降低数值不稳定风险",
            }],
        }
        orch._agents[3] = agent
        steps = [
            (f"step-{index}", lambda index=index: StepResult(f"step-{index}", index, True), None, False, 1)
            for index in range(1, 8)
        ]
        steps.extend([
            ("EM", em_step, None, False, 3),
            ("EQ", eq_step, None, False, 3),
            ("PROD", prod_step, None, False, 3),
        ])
        monkeypatch.setattr(orch, "_build_steps", lambda _: steps)

        assert orch.run(run_dir=run_dir) is False
        assert eq_calls == 1
        assert prod_calls == 0
        assert orch._sm._status.state == "awaiting_confirmation"
        assert orch._sm._status.extra["pending_action"]["restart_step"] == 9
        assert orch._sm._status.done_steps == list(range(1, 9))

    def test_confirmed_eq_action_reruns_eq_before_prod(self, tmp_path, monkeypatch):
        """An approved action rewrites MDPs then needs accepted EQ before PROD."""
        import willy.pipeline_orchestrator as po
        import willy.simulation.visualization as visualization
        from willy.pipeline_state import PipelineStateMachine
        from willy.run_registry import RunRegistry
        from willy.simulation.pending_action import create_eq_pending_action, public_pending_action
        from willy.simulation.protocol import default_md_config

        monkeypatch.setattr(po, "ROOT", tmp_path)
        run_dir = tmp_path / "md_run" / "md__202608030001"
        run_dir.mkdir(parents=True)
        config_path = run_dir / "config.json"
        config_path.write_text(json.dumps({
            "residues": {"Li": 1},
            "molecules": {"Li": {"charge": 0, "spin": 1}},
            "md": default_md_config(),
        }))
        # This recovery fixture models an already-created legacy run whose MD
        # evidence remains in md_manifest.json; new runs are v2 by default.
        (run_dir / "manifest.json").write_text(json.dumps({
            "run_id": run_dir.name, "artifacts": [], "total_steps": 10,
        }))
        registry = RunRegistry(tmp_path)
        registry.register_run(run_dir, backend="g16", total_steps=10)
        action = create_eq_pending_action(run_dir, proposal={
            "adjustments": [{"field": "dt", "after": 0.0005}],
        })
        registry.record_status(run_dir, {
            "state": "awaiting_confirmation", "step": 9,
            "step_label": "GROMACS 三点式退火平衡", "layer": "simulation",
            "error": "GROMACS 运行模拟失败：eq", "error_kind": "equilibration_failed",
            "activity": {}, "done_steps": list(range(1, 9)),
            "extra": {"run_id": run_dir.name, "pending_action": public_pending_action(action)},
        }, "run_awaiting_confirmation")
        (run_dir / "md_manifest.json").write_text(json.dumps({
            "schema_version": 1,
            "stages": {"em": {"status": "accepted"}},
        }))

        def write_stage(stage: str, status: str) -> None:
            for suffix in ("tpr", "gro", "xtc", "edr", "cpt", "log"):
                (run_dir / f"{stage}.{suffix}").write_text(f"{stage}-{suffix}")
            manifest = json.loads((run_dir / "md_manifest.json").read_text())
            manifest["stages"][stage] = {"status": status}
            (run_dir / "md_manifest.json").write_text(json.dumps(manifest))

        eq_calls = []
        prod_calls = []

        def eq_step():
            eq_calls.append(True)
            write_stage("eq", "accepted")
            return StepResult("eq", 9, True, target_type="stage", target="eq")

        def prod_step():
            prod_calls.append(True)
            assert eq_calls == [True]
            write_stage("prod", "completed")
            return StepResult("prod", 10, True, target_type="stage", target="prod")

        steps = [
            (f"step-{index}", lambda index=index: StepResult(f"step-{index}", index, True), None, False, 1)
            for index in range(1, 9)
        ] + [
            ("EQ", eq_step, None, False, 3),
            ("PROD", prod_step, None, False, 3),
        ]
        monkeypatch.setattr(po.PipelineOrchestrator, "_build_steps", lambda _self, _run: steps)
        monkeypatch.setattr(
            "willy.simulation.mdp.build_all",
            lambda **_kwargs: StepResult("mdp", 6, True),
        )
        monkeypatch.setattr(
            visualization,
            "convert_stage_gro_to_pdb",
            lambda run_dir, stage, **_kwargs: visualization.VisualizationConversionResult(
                stage, True, run_dir / "visualization" / f"{stage}.pdb"
            ),
        )

        orchestrator = po.PipelineOrchestrator(
            backend="g16", use_llm=False, confirmed_action_id=action["action_id"],
        )
        assert orchestrator.run(run_dir=run_dir) is True
        assert eq_calls == [True]
        assert prod_calls == [True]
        assert json.loads(config_path.read_text())["md"]["dt"] == 0.0005
        assert RunRegistry(tmp_path).get_run_status(run_dir.name)["state"] == "done"
        events = [
            json.loads(line)
            for line in (run_dir / "events.jsonl").read_text().splitlines()
        ]
        transition_states = [
            event["details"]["state"]
            for event in events
            if event["event_type"] in {"agent_retrying", "state_changed"}
        ]
        assert transition_states[:2] == ["retrying", "running"]

    def test_eq_success_without_manifest_acceptance_is_blocked_before_prod(self, tmp_path, monkeypatch):
        """An EQ tool result alone is insufficient to progress into PROD."""
        import willy.pipeline_state as pstate

        monkeypatch.setattr(pstate, "get_project_root", lambda: tmp_path)
        run_dir = tmp_path / "md_run" / "md_eq_missing_acceptance"
        run_dir.mkdir(parents=True)
        config_path = run_dir / "config.json"
        config_path.write_text("{}")
        (run_dir / "md_manifest.json").write_text(json.dumps({
            "schema_version": 1,
            "stages": {"em": {"status": "accepted"}, "eq": {"status": "running"}},
        }))
        for suffix in ("tpr", "gro", "xtc", "edr"):
            (run_dir / f"em.{suffix}").write_text("em")

        orch = PipelineOrchestrator(backend="g16", use_llm=False)
        orch.use_llm = True
        monkeypatch.setattr(orch, "_prepare_run_directory", lambda _: config_path)
        prod_calls = 0

        agent = MagicMock()
        agent.name = "SimulationAgent"
        agent.max_retries = 3
        agent.handle_failure.return_value = StepResult("eq", 9, True, target_type="stage", target="eq")
        orch._agents[3] = agent
        steps = [
            (f"step-{index}", lambda index=index: StepResult(f"step-{index}", index, True), None, False, 1)
            for index in range(1, 8)
        ]
        steps.extend([
            ("EM", lambda: StepResult("em", 8, True, target_type="stage", target="em"), None, False, 3),
            ("EQ", lambda: StepResult(
                "eq", 9, False,
                error=StepError(ErrorKind.EQUILIBRATION_FAILED, "EQ 验收未通过"),
                target_type="stage", target="eq",
            ), None, False, 3),
            ("PROD", lambda: (_ for _ in ()).throw(AssertionError("PROD must not start")), None, False, 3),
        ])
        monkeypatch.setattr(orch, "_build_steps", lambda _: steps)

        assert orch.run(run_dir=run_dir) is False
        assert prod_calls == 0
        assert 9 not in orch._sm._status.done_steps
        assert orch._sm._status.state == "aborted"

    def test_eq_only_scope_stops_after_accepted_eq_without_starting_prod(self, tmp_path, monkeypatch):
        """A declared EQ-only trial remains an accepted partial workflow."""
        import willy.pipeline_state as pstate
        from willy.simulation import manifest as simulation_manifest

        monkeypatch.setattr(pstate, "get_project_root", lambda: tmp_path)
        monkeypatch.setattr(simulation_manifest, "record_box_attempt", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(simulation_manifest, "box_parameters_changed", lambda *_args, **_kwargs: True)
        run_dir = tmp_path / "md_run" / "md_eq_only"
        run_dir.mkdir(parents=True)
        config_path = run_dir / "config.json"
        config_path.write_text("{}")
        orch = PipelineOrchestrator(backend="g16", use_llm=False)
        orch._stop_after_step = 9
        monkeypatch.setattr(orch, "_prepare_run_directory", lambda _: config_path)
        monkeypatch.setattr(orch, "_completion_contract_failure", lambda *_args: None)
        monkeypatch.setattr(orch, "_prod_visualization_contract_failure", lambda *_args: None)
        monkeypatch.setattr(orch, "_prod_intermediate_cleanup_contract_failure", lambda *_args: None)
        monkeypatch.setattr(orch, "_schedule_stage_visualization", lambda *_args: None)
        prod_calls = 0

        def prod_step():
            nonlocal prod_calls
            prod_calls += 1
            return StepResult("prod", 10, True, target_type="stage", target="prod")

        steps = [
            (f"step-{index}", lambda index=index: StepResult(f"step-{index}", index, True), None, False, 1)
            for index in range(1, 10)
        ] + [("PROD", prod_step, None, False, 3)]
        monkeypatch.setattr(orch, "_build_steps", lambda _: steps)

        assert orch.run(run_dir=run_dir) is True
        assert prod_calls == 0
        assert orch._sm._status.state == "done"
        assert orch._sm._status.step == 9
        assert orch._sm._status.done_steps == list(range(1, 10))
        assert orch._sm._status.extra["completion_scope"] == {
            "mode": "through_eq", "step": 9, "stage": "eq",
        }

    def test_run_registry_projects_only_the_fixed_eq_completion_scope(self, tmp_path):
        """RunRegistry must retain the scoped completion marker for the UI."""
        from willy.run_registry import RunRegistry

        registry = RunRegistry(tmp_path)
        status = registry._public_status_payload({
            "state": "done", "step": 9, "step_label": "EQ", "layer": "simulation",
            "error": "", "error_kind": "", "activity": {}, "started_at": "", "updated_at": "",
            "state_revision": 3, "total_steps": 10, "done_steps": list(range(1, 10)),
            "extra": {"completion_scope": {"mode": "through_eq", "step": 9, "stage": "eq"}},
        }, "md__202608100002")

        assert status["extra"]["completion_scope"] == {
            "mode": "through_eq", "step": 9, "stage": "eq",
        }

    def test_run_reexecutes_step_two_after_upstream_repair(self, tmp_path, monkeypatch):
        """The rerun runs Step 2 itself and only then permits downstream steps."""
        import willy.pipeline_state as pstate
        from willy.simulation import manifest as simulation_manifest

        monkeypatch.setattr(pstate, "get_project_root", lambda: tmp_path)
        monkeypatch.setattr(simulation_manifest, "record_box_attempt", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(simulation_manifest, "box_parameters_changed", lambda *_args, **_kwargs: True)
        run_dir = tmp_path / "md_run" / "md_contract_repair"
        run_dir.mkdir(parents=True)
        config_path = run_dir / "config.json"
        config_path.write_text("{}")
        orch = PipelineOrchestrator(backend="g16", use_llm=False)
        orch.use_llm = True
        monkeypatch.setattr(orch, "_prepare_run_directory", lambda _: config_path)

        step_two_calls = 0

        def step_two():
            nonlocal step_two_calls
            step_two_calls += 1
            if step_two_calls == 1:
                return [StepResult(
                    "sp_mol2_g16", 2, False,
                    error=StepError(ErrorKind.FILE_NOT_FOUND, "NMP.fchk 不存在"),
                    target_type="molecule", target="NMP",
                )]
            (run_dir / "NMP_opt.fchk").write_text("single point")
            (run_dir / "NMP.mol2").write_text("mol2")
            return [StepResult("sp_mol2_g16", 2, True, target_type="molecule", target="NMP")]

        def repaired_step_one(**_kwargs):
            (run_dir / "NMP.fchk").write_text("optimized geometry")
            return StepResult("struct_g16", 1, True, outputs={"fchk": str(run_dir / "NMP.fchk")})

        agent = MagicMock()
        agent.name = "QuantumAgent"
        agent.max_retries = 5
        agent.handle_failure.side_effect = repaired_step_one
        orch._agents[1] = agent

        steps = [
            ("结构优化", lambda: [StepResult("struct_g16", 1, True)], None, True, 1),
            ("SP + mol2", step_two, None, True, 1),
        ]
        steps.extend(
            (f"step-{index}", lambda index=index: StepResult(f"step-{index}", index, True), None, False, 3)
            for index in range(3, 11)
        )
        monkeypatch.setattr(orch, "_build_steps", lambda _: steps)
        # This legacy regression isolates the Step 2 rerun branch with
        # placeholder Step 8-10 functions.  Dedicated tests below exercise
        # the real MD manifest completion gate.
        monkeypatch.setattr(orch, "_completion_contract_failure", lambda *_args: None)
        monkeypatch.setattr(orch, "_prod_visualization_contract_failure", lambda *_args: None)

        assert orch.run(run_dir=run_dir) is True
        assert step_two_calls == 2
        assert (run_dir / "NMP_opt.fchk").is_file()
        assert (run_dir / "NMP.mol2").is_file()

    def test_agent_receives_failed_step_intermediate_artifacts(self, orch):
        """Step 2 mol2 失败时，Agent 必须收到本次生成的 *_opt.fchk。"""
        mock_agent = MagicMock()
        mock_agent.name = "QuantumAgent"
        mock_agent.max_retries = 5
        mock_agent.handle_failure.return_value = StepResult(
            step_name="sp_mol2_g16", step_index=2, success=False,
        )
        orch._agents[1] = mock_agent

        opt_fchk = "/tmp/Li_opt.fchk"
        failed_step = StepResult(
            step_name="sp_mol2_g16", step_index=2, success=False,
            error=StepError(kind=ErrorKind.UNKNOWN, message="fchk→mol2 转换失败"),
            outputs={"fchk": opt_fchk},
            artifacts=[opt_fchk],
        )

        ok = orch._invoke_agent(
            failed_step, "SP + mol2", Path("/tmp"),
            {"fchk": ["/tmp/Li.fchk"]}, layer_index=1,
        )

        assert ok is False
        assert mock_agent.handle_failure.call_args.kwargs["artifacts"]["fchk"] == opt_fchk

    def test_agent_escalation(self, orch):
        mock_agent = MagicMock()
        mock_agent.name = "TestAgent"
        mock_agent.max_retries = 5
        escalation_result = StepResult(
            step_name="struct_maker", step_index=1, success=False,
            escalated=True,
            extra={"escalation": {
                "layer": "quantum", "step": "struct_maker",
                "error_kind": "scf_not_converged",
                "attempts_made": 5,
                "actions_tried": ["retry"],
            }},
        )
        mock_agent.handle_failure.return_value = escalation_result
        orch._agents[1] = mock_agent

        sr = StepResult(
            step_name="struct_maker", step_index=1, success=False,
            error=StepError(kind=ErrorKind.SCF_NOT_CONVERGED),
        )
        ok = orch._invoke_agent(sr, "g16 优化", Path("/tmp"), {}, layer_index=1)
        assert ok is False  # 升级 = 失败
        assert orch._sm._status.state == "escalated"
        assert "当前体系" in orch._sm._status.error

    def test_agent_escalation_uses_current_failed_molecule(self, orch):
        """The final public error must not retain an earlier batch item's target."""
        mock_agent = MagicMock()
        mock_agent.name = "QuantumAgent"
        mock_agent.max_retries = 5
        mock_agent.handle_failure.return_value = StepResult(
            step_name="sp_mol2_g16", step_index=2, success=False, escalated=True,
            extra={"escalation": {"layer": "quantum", "error_kind": "file_not_found"}},
        )
        orch._agents[1] = mock_agent
        orch._set_public_error(
            StepError(ErrorKind.FILE_NOT_FOUND, "Li.fchk 不存在"), "Li",
        )
        failed_tte = StepResult(
            step_name="sp_mol2_g16", step_index=2, success=False,
            error=StepError(ErrorKind.FILE_NOT_FOUND, "TTE.fchk 不存在"),
            target_type="molecule", target="TTE",
        )

        ok = orch._invoke_agent(failed_tte, "SP + mol2", Path("/tmp"), {}, layer_index=1)

        assert ok is False
        assert orch._sm._status.state == "escalated"
        assert "TTE" in orch._sm._status.error
        assert "Li" not in orch._sm._status.error

    def test_agent_repair_failure_no_escalation(self, orch):
        """Agent 未升级的失败应中止流水线。"""
        mock_agent = MagicMock()
        mock_agent.name = "TestAgent"
        mock_agent.max_retries = 3
        fail_result = StepResult(
            step_name="struct_maker", step_index=1, success=False, escalated=False,
        )
        mock_agent.handle_failure.return_value = fail_result
        orch._agents[1] = mock_agent

        sr = StepResult(step_name="struct_maker", step_index=1, success=False)
        ok = orch._invoke_agent(sr, "g16 优化", Path("/tmp"), {}, layer_index=1)
        assert ok is False
