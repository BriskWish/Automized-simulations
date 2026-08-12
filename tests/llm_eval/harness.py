"""
harness.py —— AgentHarness: 注入受控错误、拦截 LLM 调用、收集行为轨迹。

核心设计:
  - 使用真实 LayerAgent (QuantumAgent / TopologyAgent / SimulationAgent)
  - 替换 llm_client 为 MockLLMClient, 按场景返回预设响应
  - 记录所有 LLM 调用、重试次数、升级决策
  - 零外部依赖, 纯内存运行
"""

from __future__ import annotations
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import MagicMock

from willy.errors import StepResult, StepError, ErrorKind, RetryContext
from willy.layer_agent import LayerAgent, Escalation

from .scenarios import ErrorScenario, MockLLMResponse


# ============================================================
# AgentTrace —— 完整行为轨迹
# ============================================================

@dataclass
class ToolCallRecord:
    """单次工具调用记录。"""
    call_index: int
    tool_name: str
    arguments: dict
    response_ok: bool          # 工具返回结果是否包含 _step_result
    response_success: bool     # 工具返回的 success 字段
    response_error_kind: str
    response_message: str


@dataclass
class AgentTrace:
    """Agent 完整行为轨迹。"""
    scenario_id: str = ""
    layer: str = ""

    # LLM 调用统计
    total_llm_calls: int = 0
    total_tool_calls: int = 0
    tool_calls: list[ToolCallRecord] = field(default_factory=list)

    # 重试统计
    retry_count: int = 0           # 实际递增加的重试计数
    max_retries_configured: int = 5

    # 结果
    final_success: bool = False
    escalated: bool = False
    escalation_info: dict = field(default_factory=dict)
    recovery_terminal_reason: str = ""

    # 耗时
    duration_s: float = 0.0
    provider_call_durations_s: list[float] = field(default_factory=list)

    # 异常
    errors_encountered: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "scenario_id": self.scenario_id,
            "layer": self.layer,
            "total_llm_calls": self.total_llm_calls,
            "total_tool_calls": self.total_tool_calls,
            "retry_count": self.retry_count,
            "max_retries_configured": self.max_retries_configured,
            "final_success": self.final_success,
            "escalated": self.escalated,
            "escalation_info": self.escalation_info,
            "recovery_terminal_reason": self.recovery_terminal_reason,
            "duration_s": self.duration_s,
            "provider_call_durations_s": self.provider_call_durations_s,
            "errors_encountered": self.errors_encountered,
            "tool_calls": [
                {
                    "tool": tc.tool_name,
                    "success": tc.response_success,
                    "error_kind": tc.response_error_kind,
                }
                for tc in self.tool_calls
            ],
        }


# ============================================================
# MockLLMClient —— 受控 LLM 响应
# ============================================================

class MockLLMClient:
    """
    模拟 OpenAI 兼容客户端, 按场景预设返回响应。

    每次 create() 根据服务端阶段返回唯一允许的工具调用。
    场景响应序列只描述 retry 工具的成败，不再伪造模型文本
    ``_step_result`` 来取得评分。
    """

    def __init__(self, responses: list[MockLLMResponse],
                 scenario: ErrorScenario):
        self._responses = responses
        self._scenario = scenario
        self._call_count = 0
        self.chat = self._Chat()

    def _build_response(self, mr: MockLLMResponse, **kwargs) -> MagicMock:
        """构造一个模拟 OpenAI chat completion 响应。"""
        resp = MagicMock()
        choice = MagicMock()
        message = MagicMock()

        tools = kwargs.get("tools") or []
        names = [
            str(tool.get("function", {}).get("name") or "")
            for tool in tools if isinstance(tool, dict)
        ]
        diagnostic = next((name for name in names if "diagnose" in name), "")
        selected = diagnostic or self._preferred_recovery_tool(names)
        if not selected:
            message.content = None
            message.tool_calls = None
        else:
            args = self._tool_arguments(selected)
            tool_call = MagicMock()
            tool_call.id = f"call_{mr.call_index}"
            tool_call.type = "function"
            tool_call.function.name = selected
            tool_call.function.arguments = json.dumps(args, ensure_ascii=False)
            message.content = None
            message.tool_calls = [tool_call]

        choice.message = message
        resp.choices = [choice]
        return resp

    def _tool_arguments(self, tool_name: str) -> dict:
        """Return schema-compatible deterministic arguments for offline eval."""
        scenario = self._scenario
        if "diagnose_error_quantum" in tool_name:
            return {"log_path": "result.log", "error_kind": scenario.injected_error_kind.value}
        if "diagnose_error_topology" in tool_name:
            return {"error_source": "sobtop", "raw_output": "controlled evidence"}
        if "diagnose_error_simulation" in tool_name:
            step = {
                "md_em": "em",
                "md_eq": "eq",
                "md_prod": "prod",
            }.get(scenario.step_name, scenario.step_name)
            if step not in {"em", "eq", "prod"}:
                step = "em"
            return {"step": step, "work_dir": "run"}
        if "retry_struct" in tool_name or "retry_topo" in tool_name:
            return {"molecule_name": "LiTFSI"}
        if "retry_mol2" in tool_name or "retry_chg" in tool_name:
            if "retry_chg" in tool_name:
                # charge/spin are optional but change a retry into a fork.
                # The normal retry contract must retain the audited values.
                return {"fchk_path": "repaired.fchk"}
            return {"fchk_path": "repaired.fchk"}
        if "top_assembly" in tool_name:
            return {}
        if "retry" in tool_name:
            return {"work_dir": "run"}
        return {}

    def _preferred_recovery_tool(self, names: list[str]) -> str:
        """Choose a policy-valid mock repair representative for this scenario."""
        scenario = self._scenario
        if scenario.layer == "quantum":
            if scenario.step_index == 2:
                return next((name for name in names if "mol2" in name), "")
            if scenario.step_index == 3:
                return next((name for name in names if "retry_chg" in name), "")
            return next((name for name in names if "retry_struct" in name), "")
        if scenario.layer == "topology":
            if scenario.step_name in {"top_assembly", "topology_assemble"}:
                return next((name for name in names if "top_assembly" in name), "")
            if scenario.injected_error_kind.value == "ligpargen_failed":
                return next((name for name in names if "topo_opls" in name), "")
            return next((name for name in names if "topo_gaff" in name), "")
        if scenario.layer == "simulation":
            if scenario.step_name in {"em", "md_em"}:
                return next((name for name in names if "retry_em" in name), "")
            if scenario.step_name in {"eq", "md_eq"}:
                return next((name for name in names if "retry_eq" in name), "")
            if scenario.step_name in {"prod", "md_prod"}:
                return next((name for name in names if "retry_prod" in name), "")
            return next((name for name in names if "retry" in name), "")
        return next((name for name in names if "retry" in name), "")

    class _Chat:
        def __init__(self):
            self.completions = self._Completions()

        class _Completions:
            def __init__(self):
                self._parent = None

            def create(self, **kwargs):
                """每次调用返序列中下一个响应。"""
                parent = self._parent
                harness = getattr(parent, '_harness', None)
                if harness is None:
                    raise RuntimeError("MockLLMClient 未正确绑定到 harness")

                if harness._call_count >= len(harness._responses):
                    # 超出预设响应 —— 返回升级
                    mr = MockLLMResponse(
                        call_index=harness._call_count,
                        outcome="escalate",
                    )
                else:
                    mr = harness._responses[harness._call_count]

                harness._call_count += 1
                return harness._build_response(mr, **kwargs)


# ============================================================
# AgentHarness —— 核心评估引擎
# ============================================================

class AgentHarness:
    """
    评估一个 ErrorScenario 的完整运行。

    用法:
        scenario = SCENARIOS[0]
        harness = AgentHarness(scenario, tmp_path)
        trace = harness.run()
        # trace 包含完整的 Agent 行为轨迹
    """

    def __init__(self, scenario: ErrorScenario, tmp_path: Path):
        self.scenario = scenario
        self.tmp_path = tmp_path
        self.trace = AgentTrace(
            scenario_id=scenario.scenario_id,
            layer=scenario.layer,
        )

        # Mock LLM 客户端
        self._responses = list(scenario.llm_responses)
        self._call_count = 0
        self.mock_llm = MockLLMClient(scenario.llm_responses, scenario)

        # 连接内部引用
        self.mock_llm.chat.completions._parent = self.mock_llm
        # 让 completions 能访问 harness
        self.mock_llm.chat.completions._parent._harness = self
        # 给 _harness 赋值
        self.mock_llm._harness = self

    def _build_response(self, mr: MockLLMResponse, **kwargs) -> MagicMock:
        """委托给 MockLLMClient。"""
        return self.mock_llm._build_response(mr, **kwargs)

    def run(self) -> AgentTrace:
        """执行评估, 返回 AgentTrace。"""
        import willy._paths

        # ── 准备环境 ──
        config_path = self.tmp_path / "config.json"
        if not config_path.exists():
            config_path.write_text(json.dumps({
                "backend": "g16",
                "residues": {"LiTFSI": 10, "FEC": 120},
                "molecules": {
                    "LiTFSI": {"charge": 0, "spin": 1,
                               "basis": "b3lyp/6-311+g(d,p)"},
                    "FEC": {"charge": 0, "spin": 1,
                            "basis": "b3lyp/6-311+g(d,p)"},
                },
                "md": {"dt": 0.001, "ref_t": 298.15},
            }, indent=2))

        run_dir = self.tmp_path / "md_run"
        run_dir.mkdir(exist_ok=True)

        # ── 创建 Agent ──
        agent = self._create_layer_agent()

        # ── 构造 StepResult ──
        sr = StepResult(
            step_name=self.scenario.step_name,
            step_index=self.scenario.step_index,
            success=False,
            error=StepError(
                kind=self.scenario.injected_error_kind,
                message=self.scenario.injected_error_message,
                raw_output=self.scenario.injected_raw_output,
                hint=self.scenario.injected_hint,
            ),
        )

        # ── 运行 ──
        t_start = time.time()
        sm = MagicMock()  # 模拟 state_machine
        try:
            result = agent.handle_failure(
                step_result=sr,
                config_path=str(config_path),
                run_dir=str(run_dir),
                artifacts={},
                state_machine=sm,
            )
        except Exception as e:
            self.trace.errors_encountered.append(f"{type(e).__name__}: {e}")
            result = StepResult(
                step_name=self.scenario.step_name,
                step_index=self.scenario.step_index,
                success=False,
                escalated=True,
                extra={"escalation": {"error": str(e)}},
            )

        self.trace.duration_s = time.time() - t_start

        # ── 提取结果 ──
        self.trace.total_llm_calls = self._call_count
        self.trace.final_success = result.success
        self.trace.escalated = result.escalated
        self.trace.recovery_terminal_reason = str(
            result.extra.get("recovery_terminal_reason", "")
        )
        self.trace.max_retries_configured = agent.max_retries

        if result.escalated and "escalation" in result.extra:
            self.trace.escalation_info = result.extra["escalation"]

        # A retry is a real repair-tool invocation, not an LLM call.
        self.trace.total_tool_calls = len(getattr(agent, "_offline_tool_calls", []))
        self.trace.tool_calls = list(getattr(agent, "_offline_tool_calls", []))
        self.trace.retry_count = sum(
            1 for call in self.trace.tool_calls if "diagnose" not in call.tool_name
        )

        return self.trace

    def _create_layer_agent(self) -> LayerAgent:
        """根据场景的 layer 创建对应的 Agent 子类。"""
        from willy.action_contract import build_default_tool_catalog
        from willy.recovery_policy import default_recovery_policy
        catalog = build_default_tool_catalog()
        policy = default_recovery_policy(catalog)
        records: list[ToolCallRecord] = []

        def handler(tool_name: str, args: dict) -> str:
            is_diagnosis = "diagnose" in tool_name
            if is_diagnosis:
                payload = {
                    "_diagnosis": True,
                    "source": self.scenario.layer,
                    "severity": "error",
                    "issues": [self.scenario.injected_error_kind.value],
                    "evidence": ["controlled evidence"],
                }
                records.append(ToolCallRecord(
                    len(records), tool_name, args, True, True, "", "diagnosis",
                ))
                return json.dumps(payload, ensure_ascii=False)
            # A policy-safe repair can be named ``run_em`` as well as
            # ``retry_*``. Both consume one bounded repair attempt.
            attempt = sum(1 for call in records if "diagnose" not in call.tool_name)
            expected = self.scenario.llm_responses
            outcome = expected[attempt].outcome if attempt < len(expected) else "escalate"
            success = outcome == "success"
            payload = {
                "_step_result": True,
                "success": success,
                "step_name": self.scenario.step_name,
                "step_index": self.scenario.step_index,
                "outputs": {"repaired": "artifact"} if success else {},
                "artifacts": [],
                "duration_s": 0.5,
                "error_message": "" if success else self.scenario.injected_error_kind.value,
                "error_kind": "" if success else self.scenario.injected_error_kind.value,
            }
            records.append(ToolCallRecord(
                len(records), tool_name, args, True, success,
                payload["error_kind"], payload["error_message"] or "success",
            ))
            return json.dumps(payload, ensure_ascii=False)

        if self.scenario.layer == "quantum":
            from willy.agent_quantum import QuantumAgent
            agent = QuantumAgent(
                llm_client=self.mock_llm,
                max_retries=self.scenario.expected.max_expected_retries,
                recovery_policy=policy, tool_catalog=catalog,
            )
        elif self.scenario.layer == "topology":
            from willy.agent_topology import TopologyAgent
            agent = TopologyAgent(
                llm_client=self.mock_llm,
                max_retries=self.scenario.expected.max_expected_retries,
                recovery_policy=policy, tool_catalog=catalog,
            )
        elif self.scenario.layer == "simulation":
            from willy.agent_simulation import SimulationAgent
            agent = SimulationAgent(
                llm_client=self.mock_llm,
                max_retries=self.scenario.expected.max_expected_retries,
                recovery_policy=policy, tool_catalog=catalog,
            )
        else:
            raise ValueError(f"未知 layer: {self.scenario.layer}")
        agent.handle_tool = handler
        agent._offline_tool_calls = records
        return agent
