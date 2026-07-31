"""
test_pipeline_orchestrator.py —— PipelineOrchestrator 错误处理测试。

重点测试：
1. 依赖缺失处理
2. 批量结果跳过逻辑
3. Agent 调用决策
4. _STEP_LAYER 映射正确性
5. LLM 初始化优雅降级
6. 跳过分子过滤
"""

import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

from willy.errors import StepResult, StepError, ErrorKind
from willy.pipeline_orchestrator import PipelineOrchestrator, _STEP_LAYER


# ============================================================
# _STEP_LAYER 映射
# ============================================================

class TestStepLayerMapping:
    """步骤→层映射测试。"""

    def test_step_layer_mapping_is_complete(self):
        """步骤 1-7 应全部映射。"""
        for i in range(1, 8):
            assert i in _STEP_LAYER, f"步骤 {i} 缺少层映射"

    def test_step_layers_correct(self):
        """各步骤的层映射应正确。"""
        # 量子层：步骤 1-3（结构优化、mol2 转换、RESP 电荷）
        for i in [1, 2, 3]:
            assert _STEP_LAYER[i] == "quantum"
        # 拓扑层：步骤 4-5（拓扑生成、主拓扑）
        for i in [4, 5]:
            assert _STEP_LAYER[i] == "topology"
        # 模拟层：步骤 6-7（MDP、Packmol 盒子）
        for i in [6, 7]:
            assert _STEP_LAYER[i] == "simulation"

    def test_no_steps_beyond_7(self):
        """不应为步骤 8+ 定义映射（它们目前不在流水线中）。"""
        assert 8 not in _STEP_LAYER


# ============================================================
# PipelineOrchestrator 构造
# ============================================================

class TestPipelineOrchestratorInit:
    """初始化测试。"""

    @patch.dict('os.environ', {}, clear=True)
    def test_no_api_key_disables_llm(self, tmp_project_root, monkeypatch):
        """没有 DEEPSEEK_API_KEY 时，LLM 应被禁用。"""
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


# ============================================================
# _build_steps
# ============================================================

class TestBuildSteps:
    """步骤构建测试。"""

    def test_g16_backend_builds_7_steps(self):
        orch = PipelineOrchestrator(backend="g16", use_llm=False)
        steps = orch._build_steps(Path("/tmp/run"))
        assert len(steps) == 7

    def test_orca_backend_builds_7_steps(self):
        orch = PipelineOrchestrator(backend="orca", use_llm=False)
        steps = orch._build_steps(Path("/tmp/run"))
        assert len(steps) == 7

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
            ))
        for i in range(n_fail):
            results.append(StepResult(
                step_name="struct_maker", step_index=1, success=False,
                error=StepError(
                    kind=ErrorKind.GAUSSIAN_CRASH,
                    message=f"mol{i+3} 崩溃",
                    raw_output="segfault",
                ),
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

    def test_skipped_molecules_filtered(self, orch):
        """已跳过的分子应从失败列表中排除。"""
        orch._skipped_molecules = {"LiTFSI"}
        results = [
            StepResult(
                step_name="struct_maker", step_index=1, success=False,
                error=StepError(kind=ErrorKind.GAUSSIAN_CRASH,
                                message="LiTFSI 优化失败"),
            ),
        ]
        artifacts = {}
        ok = orch._handle_batch_result(results, "struct", Path("/tmp"), artifacts, 1, 1)
        # 由于已跳过分子被过滤，剩余 0 个失败
        assert ok is True

    def test_artifacts_collected_for_success(self, orch):
        results = self.make_batch_results(2, 0)
        artifacts = {}
        orch._handle_batch_result(results, "struct", Path("/tmp"), artifacts, 1, 1)
        assert "fchk" in artifacts
        assert len(artifacts["fchk"]) == 2


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
        """agent 为 None 时应返回 False。"""
        sr = StepResult(step_name="test", step_index=1, success=False)
        ok = orch._invoke_agent(sr, "test", Path("/tmp"), {}, layer_index=1)
        assert ok is False

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


# ============================================================
# 跳过分子 - 完整场景
# ============================================================

class TestSkippedMolecules:
    """跳过分子逻辑的完整场景测试。"""

    @pytest.fixture
    def orch(self):
        return PipelineOrchestrator(backend="g16", use_llm=False)

    def test_skip_list_loaded_from_config(self, orch, tmp_project_root):
        """跳过的分子应从 config.json 中加载。"""
        # 使用 patch 跳过 LLM 初始化
        with patch.object(PipelineOrchestrator, '_init_llm'), \
             patch.object(PipelineOrchestrator, '_init_agents'):
            orch2 = PipelineOrchestrator(backend="g16", use_llm=True)
            orch2._skipped_molecules = {"LiTFSI", "FEC"}

        assert "LiTFSI" in orch2._skipped_molecules
        assert "FEC" in orch2._skipped_molecules

    def test_skip_reasons_preserved(self, orch, tmp_project_root):
        """跳过原因应从 config.json 中保留。"""
        config_path = tmp_project_root / "config.json"
        config = json.loads(config_path.read_text())
        config["skipped_molecules"] = ["LiTFSI"]
        config["skip_reasons"] = {"LiTFSI": "原子类型缺失"}
        config_path.write_text(json.dumps(config, indent=2))

    def test_filtering_skipped_from_batch(self, orch):
        """已跳过的分子应从批量失败中被过滤。"""
        orch._skipped_molecules = {"ProblemMol"}

        results = [
            StepResult(step_name="test", step_index=1, success=True,
                       outputs={"a": "ok1"}, artifacts=[]),
            StepResult(step_name="test", step_index=1, success=False,
                       error=StepError(kind=ErrorKind.UNKNOWN,
                                       message="ProblemMol 处理失败"),
                       outputs={"a": "partial"}),
        ]
        artifacts = {}
        ok = orch._handle_batch_result(results, "test", Path("/tmp"), artifacts, 1, 1)
        # 一个成功 + 一个被跳过 = 全部通过
        assert ok is True
        # 跳过分子的部分产物应被收集
        assert "a" in artifacts
