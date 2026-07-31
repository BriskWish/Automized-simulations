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

    # 耗时
    duration_s: float = 0.0

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
            "duration_s": self.duration_s,
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

    每次 create() 调用返回序列中的下一个响应。
    响应格式为文本 JSON (含 _step_result 标记),
    由 LayerAgent.handle_failure() 解析驱动重试循环。
    """

    def __init__(self, responses: list[MockLLMResponse],
                 scenario: ErrorScenario):
        self._responses = responses
        self._scenario = scenario
        self._call_count = 0
        self.chat = self._Chat()

    def _build_response(self, mr: MockLLMResponse) -> MagicMock:
        """构造一个模拟 OpenAI chat completion 响应。"""
        resp = MagicMock()
        choice = MagicMock()
        message = MagicMock()

        if mr.outcome == "escalate":
            # 返回包含 "escalat" 关键词的纯文本
            message.content = (
                "无法自动修复此错误。已尝试所有可用策略, "
                "必须升级 (escalate) 给用户进行手动干预。\n"
                "建议: 检查输入文件或使用不同的计算方法。"
            )
            message.tool_calls = None
        else:
            # 返回带 _step_result 标记的 JSON
            err = self._scenario.injected_error_kind
            kind_str = mr.error_kind_override or err.value
            is_success = mr.outcome == "success"
            message.content = json.dumps({
                "_step_result": True,
                "success": is_success,
                "step_name": self._scenario.step_name,
                "outputs": {"fchk": "/tmp/repaired.fchk"} if is_success else {},
                "artifacts": ["/tmp/repaired.fchk"] if is_success else [],
                "duration_s": 0.5,
                "error_message": "" if is_success else (
                    f"[{self._scenario.layer}] 第{mr.call_index+1}次修复尝试: "
                    f"{kind_str}"
                ),
                "error_kind": "" if is_success else kind_str,
                "hint": mr.hint_override or "",
                "raw_output": mr.raw_output_override or "",
            }, ensure_ascii=False)
            message.tool_calls = None

        choice.message = message
        resp.choices = [choice]
        return resp

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
                return harness._build_response(mr)


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

    def _build_response(self, mr: MockLLMResponse) -> MagicMock:
        """委托给 MockLLMClient。"""
        return self.mock_llm._build_response(mr)

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
        self.trace.max_retries_configured = agent.max_retries

        if result.escalated and "escalation" in result.extra:
            self.trace.escalation_info = result.extra["escalation"]

        # ── 估算重试次数 ──
        # 从 mock 响应中统计 failure 和 success
        retries = 0
        for i, mr in enumerate(self.scenario.llm_responses):
            if i >= self._call_count:
                break
            if mr.outcome == "failure":
                retries += 1
            elif mr.outcome == "success":
                break
        self.trace.retry_count = retries

        return self.trace

    def _create_layer_agent(self) -> LayerAgent:
        """根据场景的 layer 创建对应的 Agent 子类。"""
        if self.scenario.layer == "quantum":
            from willy.agent_quantum import QuantumAgent
            return QuantumAgent(
                llm_client=self.mock_llm,
                max_retries=self.scenario.expected.max_expected_retries,
            )
        elif self.scenario.layer == "topology":
            from willy.agent_topology import TopologyAgent
            return TopologyAgent(
                llm_client=self.mock_llm,
                max_retries=self.scenario.expected.max_expected_retries,
            )
        elif self.scenario.layer == "simulation":
            from willy.agent_simulation import SimulationAgent
            return SimulationAgent(
                llm_client=self.mock_llm,
                max_retries=self.scenario.expected.max_expected_retries,
            )
        else:
            raise ValueError(f"未知 layer: {self.scenario.layer}")
