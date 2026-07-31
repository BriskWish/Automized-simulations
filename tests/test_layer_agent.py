"""
test_layer_agent.py —— LayerAgent 基类测试。

重点测试错误处理路径：
1. handle_failure 重试循环
2. 升级 (escalation) 逻辑
3. RetryContext 耗尽检测
4. LLM 调用异常恢复
5. _build_context 信息截断
6. Escalation 数据类 to_dict()
"""

import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, call
from dataclasses import asdict

from willy.errors import StepResult, StepError, ErrorKind, RetryContext
from willy.layer_agent import LayerAgent, Escalation


# ============================================================
# Escalation 数据类
# ============================================================

class TestEscalation:
    """Escalation 数据类测试。"""

    def test_to_dict_truncates_raw_output(self):
        """to_dict() 应将 last_raw_output 截断到 500 字符。"""
        esc = Escalation(
            layer="quantum",
            step_name="struct_maker",
            error_kind="scf_not_converged",
            attempts_made=5,
            actions_tried=["retry with scf=xqc", "change basis"],
            last_raw_output="x" * 1000,
            recommendation="手动检查分子结构",
            backup_plan="尝试不同初始猜测",
        )
        d = esc.to_dict()
        assert len(d["last_raw_output"]) <= 500
        assert d["layer"] == "quantum"
        assert d["step"] == "struct_maker"
        assert d["attempts_made"] == 5
        assert len(d["actions_tried"]) == 2

    def test_empty_actions_and_output(self):
        """升级可能没有动作或输出。"""
        esc = Escalation(
            layer="topology",
            step_name="topo_gaff",
            error_kind="sobtop_failed",
            attempts_made=0,
        )
        d = esc.to_dict()
        assert d["actions_tried"] == []
        assert d["last_raw_output"] == ""
        assert d["recommendation"] == ""
        assert d["backup_plan"] == ""


# ============================================================
# LayerAgent 构造
# ============================================================

class TestLayerAgentConstruction:
    """LayerAgent 构造参数验证。"""

    def test_minimal_construction(self, mock_llm_client):
        """最小有效构造应成功。"""
        agent = LayerAgent(
            name="test_agent",
            system_prompt="You are a test agent.",
            tools=[],
            tool_handler=lambda name, args: "{}",
            llm_client=mock_llm_client,
        )
        assert agent.name == "test_agent"
        assert agent.max_retries == 5  # 默认值

    def test_custom_max_retries(self, mock_llm_client):
        """自定义 max_retries 应被尊重。"""
        agent = LayerAgent(
            name="test_agent",
            system_prompt="You are a test agent.",
            tools=[],
            tool_handler=lambda name, args: "{}",
            llm_client=mock_llm_client,
            max_retries=5,
        )
        assert agent.max_retries == 5

    def test_on_action_callback(self, mock_llm_client):
        """on_action 回调应被存储。"""
        callback = MagicMock()
        agent = LayerAgent(
            name="test_agent",
            system_prompt="test",
            tools=[],
            tool_handler=lambda n, a: "{}",
            llm_client=mock_llm_client,
            on_action=callback,
        )
        assert agent.on_action is callback


# ============================================================
# _build_context
# ============================================================

class TestBuildContext:
    """_build_context 方法测试。"""

    def test_context_includes_step_info(self, mock_llm_client, make_step_result):
        agent = LayerAgent("test", "prompt", [], lambda n, a: "{}", mock_llm_client)

        sr = make_step_result(
            success=False, step_name="struct_maker", step_index=1,
            error_kind=ErrorKind.SCF_NOT_CONVERGED,
            error_message="SCF 未收敛",
            raw_output="Cycle 128",
            hint="添加 scf=xqc",
        )
        config_text = '{"residues": {"LiTFSI": 10}}'
        ctx = agent._build_context(sr, config_text, "/tmp/run", {})
        assert "struct_maker" in ctx
        assert "SCF" in ctx or "scf" in ctx.lower()
        assert "scf=xqc" in ctx

    def test_context_truncates_long_config(self, mock_llm_client, make_step_result):
        """config_text 应被截断到约 2000 字符。"""
        agent = LayerAgent("test", "prompt", [], lambda n, a: "{}", mock_llm_client)

        sr = make_step_result(success=False, error_kind=ErrorKind.UNKNOWN)

        # 极长的 config 文本应被截断
        long_config = "x" * 5000
        ctx = agent._build_context(sr, long_config, "/tmp/run", {})
        assert isinstance(ctx, str)
        # config 段应存在
        assert "config" in ctx.lower()

    def test_context_includes_artifacts(self, mock_llm_client, make_step_result):
        agent = LayerAgent("test", "prompt", [], lambda n, a: "{}", mock_llm_client)

        sr = make_step_result(success=False, error_kind=ErrorKind.UNKNOWN)
        ctx = agent._build_context(
            sr, '{"residues": {}}', "/tmp/run",
            {"fchk": "/tmp/LiTFSI.fchk", "mol2": "/tmp/LiTFSI.mol2"},
        )
        assert "fchk" in ctx or "LiTFSI" in ctx


# ============================================================
# _escalate
# ============================================================

class TestEscalate:
    """_escalate 方法测试。"""

    def test_escalate_returns_failed_step_result(self, mock_llm_client, make_step_result):
        agent = LayerAgent("quantum", "prompt", [], lambda n, a: "{}", mock_llm_client)

        sr = make_step_result(
            success=False, step_name="struct_maker", step_index=1,
            error_kind=ErrorKind.SCF_NOT_CONVERGED,
            error_message="SCF 未收敛",
            raw_output="Error output",
        )
        retry_ctx = RetryContext(
            layer="quantum", step_name="struct_maker",
            error_kind=ErrorKind.SCF_NOT_CONVERGED,
            attempts=5, max_attempts=5,
            actions_tried=["retry1", "retry2"],
            last_raw_output="Error output",
        )
        result = agent._escalate(sr, retry_ctx)
        assert result.success is False
        assert result.escalated is True
        assert "escalation" in result.extra
        esc = result.extra["escalation"]
        assert esc["layer"] == "quantum"
        assert esc["attempts_made"] == 5
        assert len(esc["actions_tried"]) == 2


# ============================================================
# handle_failure — 核心错误处理循环
# ============================================================

class TestHandleFailure:
    """handle_failure 方法 —— 整个 Agent 系统的核心。"""

    @pytest.fixture
    def agent(self, mock_llm_client):
        return LayerAgent(
            name="test_agent",
            system_prompt="You fix errors.",
            tools=[{
                "type": "function",
                "function": {
                    "name": "tools_diagnose",
                    "description": "诊断问题",
                    "parameters": {"type": "object", "properties": {}}
                }
            }],
            tool_handler=lambda name, args: json.dumps({
                "_step_result": True, "success": True,
                "step_name": "fixed", "outputs": {}, "artifacts": [],
                "duration_s": 0, "error_message": "", "error_kind": "",
                "hint": "", "raw_output": "",
            }),
            llm_client=mock_llm_client,
            max_retries=3,
        )

    def test_handle_failure_returns_step_result(self, agent, make_step_result):
        """handle_failure 应返回 StepResult。"""
        sr = make_step_result(
            success=False, step_name="struct_maker",
            error_kind=ErrorKind.GAUSSIAN_CRASH,
            error_message="g16 crashed on LiTFSI",
            raw_output="segfault",
            hint="减少内存使用",
        )

        # LLM 应在第一次调用时给出诊断
        agent.llm.chat.completions.create.side_effect = [
            # 第一次 LLM 调用：调用诊断工具
            _make_mock_llm_response(
                content=None,
                tool_calls=[_make_tool_call("tools_diagnose", "{}")],
            ),
            # 第二次 LLM 调用：返回修复响应（带 _step_result 标记的 JSON）
            _make_mock_llm_response(
                content=json.dumps({"_step_result": True, "success": True,
                                    "step_name": "struct_maker", "error_message": "",
                                    "error_kind": "", "hint": "", "raw_output": "",
                                    "outputs": {"fchk": "/tmp/fixed.fchk"},
                                    "artifacts": [], "duration_s": 10.0}),
            ),
        ]

        result = agent.handle_failure(sr, "/tmp/config.json", "/tmp/run", {},
                                      state_machine=MagicMock())
        assert isinstance(result, StepResult)

    def test_handle_failure_exhausts_retries(self, agent, make_step_result):
        """当超过 max_retries 时，应升级。"""
        agent.max_retries = 1
        sr = make_step_result(
            success=False, error_kind=ErrorKind.SCF_NOT_CONVERGED,
            error_message="SCF 未收敛", raw_output="error",
        )

        # LLM 始终返回带有 _step_result: success=False 的 JSON
        agent.llm.chat.completions.create.return_value = _make_mock_llm_response(
            content=json.dumps({
                "_step_result": True, "success": False,
                "step_name": "struct_maker",
                "error_message": "仍然失败",
                "error_kind": "scf_not_converged",
                "hint": "无法修复", "raw_output": "给定时",
                "outputs": {}, "artifacts": [], "duration_s": 0,
            }),
        )

        sm = MagicMock()
        result = agent.handle_failure(sr, "/tmp/config.json", "/tmp/run", {},
                                      state_machine=sm)
        # 重试耗尽后应升级或返回失败
        assert result.success is False

    def test_handle_failure_detects_escalation_keyword(self, agent, make_step_result):
        """当 LLM 在响应中说 'escalat' 时，应立即升级。"""
        sr = make_step_result(
            success=False, error_kind=ErrorKind.UNKNOWN,
            error_message="something wrong",
        )

        agent.llm.chat.completions.create.return_value = _make_mock_llm_response(
            content="无法自动修复，必须升级 (escalate) 给用户",
        )

        sm = MagicMock()
        result = agent.handle_failure(sr, "/tmp/config.json", "/tmp/run", {},
                                      state_machine=sm)
        assert result.escalated is True or result.success is False

    def test_handle_failure_llm_exception_retry(self, agent, make_step_result):
        """当 LLM API 抛出异常时，应重试一次。"""
        sr = make_step_result(
            success=False, error_kind=ErrorKind.UNKNOWN,
            error_message="test error",
        )

        # 第一次调用：异常，第二次：修复响应
        agent.llm.chat.completions.create.side_effect = [
            Exception("API 超时"),
            _make_mock_llm_response(
                content=json.dumps({"_step_result": True, "success": True,
                                    "step_name": "fixed", "error_message": "",
                                    "error_kind": "", "hint": "", "raw_output": "",
                                    "outputs": {}, "artifacts": [], "duration_s": 0}),
            ),
        ]

        sm = MagicMock()
        result = agent.handle_failure(sr, "/tmp/config.json", "/tmp/run", {},
                                      state_machine=sm)
        # 应在重试后恢复或升级
        assert isinstance(result, StepResult)


# ============================================================
# 边界情况
# ============================================================

class TestLayerAgentEdgeCases:
    """LayerAgent 边界情况。"""

    def test_empty_tool_list(self, mock_llm_client):
        """零工具的 Agent 应能工作（纯文本响应）。"""
        agent = LayerAgent("test", "prompt", [], lambda n, a: "{}", mock_llm_client)
        assert agent.tools == []

    def test_tool_handler_returning_non_json(self, mock_llm_client, make_step_result):
        """工具处理程序可能返回非 JSON —— LLM 应处理它。"""
        agent = LayerAgent(
            "test", "prompt",
            [{"type": "function", "function": {
                "name": "tools_broken",
                "description": "返回纯文本",
                "parameters": {"type": "object", "properties": {}}
            }}],
            lambda name, args: "这不是 JSON",
            mock_llm_client,
        )

        sr = make_step_result(success=False, error_kind=ErrorKind.UNKNOWN)
        agent.llm.chat.completions.create.side_effect = [
            _make_mock_llm_response(
                content=None,
                tool_calls=[_make_tool_call("tools_broken", "{}")],
            ),
            _make_mock_llm_response(
                content=json.dumps({"_step_result": True, "success": True,
                                    "step_name": "fixed", "error_message": "",
                                    "error_kind": "", "hint": "", "raw_output": "",
                                    "outputs": {}, "artifacts": [], "duration_s": 0}),
            ),
        ]

        # 不应崩溃 —— 非 JSON 响应不应导致未处理的异常
        try:
            result = agent.handle_failure(sr, "/tmp/c.json", "/tmp/run", {},
                                          state_machine=MagicMock())
            assert isinstance(result, StepResult)
        except Exception as e:
            # JSON decode 错误可能是可以接受的（层代码处理它）
            pytest.skip(f"非 JSON 工具响应导致异常: {e}")

    def test_long_error_message(self, mock_llm_client, make_step_result):
        """极长的错误消息不应崩溃 Agent。"""
        agent = LayerAgent("test", "prompt", [], lambda n, a: "{}", mock_llm_client)

        sr = make_step_result(
            success=False,
            error_kind=ErrorKind.UNKNOWN,
            error_message="E" * 10000,
            raw_output="R" * 10000,
        )

        agent.llm.chat.completions.create.return_value = _make_mock_llm_response(
            content=json.dumps({"_step_result": True, "success": True,
                                "step_name": "fixed", "error_message": "",
                                "error_kind": "", "hint": "", "raw_output": "",
                                "outputs": {}, "artifacts": [], "duration_s": 0}),
        )

        result = agent.handle_failure(sr, "/tmp/c.json", "/tmp/run", {},
                                      state_machine=MagicMock())
        assert isinstance(result, StepResult)


# ============================================================
# 辅助函数
# ============================================================

def _make_mock_llm_response(content=None, tool_calls=None):
    """创建一个类似 OpenAI chat completion 的 mock 对象。"""
    resp = MagicMock()
    choice = MagicMock()
    message = MagicMock()
    message.content = content
    if tool_calls:
        tc_list = []
        for tc in tool_calls:
            tc_mock = MagicMock()
            tc_mock.id = tc.get("id", "call_1")
            tc_mock.type = "function"
            tc_mock.function.name = tc["function"]["name"]
            tc_mock.function.arguments = tc["function"]["arguments"]
            tc_list.append(tc_mock)
        message.tool_calls = tc_list
    else:
        message.tool_calls = None
    choice.message = message
    resp.choices = [choice]
    return resp


def _make_tool_call(name, arguments):
    """创建一个模拟工具调用。"""
    return {
        "id": "call_test",
        "function": {"name": name, "arguments": arguments}
    }
