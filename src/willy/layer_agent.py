"""
layer_agent.py
==============
各层 LLM Agent 的基类 —— LLM loop、重试计数、结构化升级。

用法（子类）:
  class QuantumAgent(LayerAgent):
      def __init__(self, llm_client):
          super().__init__(
              name="quantum",
              system_prompt=QUANTUM_AGENT_PROMPT,
              tools=QUANTUM_TOOLS,
              tool_handler=handle_quantum_tool_call,
              llm_client=llm_client,
              max_retries=5,
          )
"""

from __future__ import annotations
import json
from dataclasses import dataclass, field
from typing import Optional, Callable

from willy.errors import StepResult, StepError, ErrorKind, RetryContext


@dataclass
class Escalation:
    """Agent 放弃自助修复后向用户提供的结构化信息。"""
    layer: str
    step_name: str
    error_kind: str
    attempts_made: int
    actions_tried: list[str] = field(default_factory=list)
    last_raw_output: str = ""
    recommendation: str = ""
    backup_plan: str = ""

    def to_dict(self) -> dict:
        return {
            "layer": self.layer,
            "step": self.step_name,
            "error_kind": self.error_kind,
            "attempts_made": self.attempts_made,
            "actions_tried": self.actions_tried,
            "last_raw_output": self.last_raw_output[-500:],
            "recommendation": self.recommendation,
            "backup_plan": self.backup_plan,
        }


class LayerAgent:
    """
    流水线层 LLM Agent 基类。

    每个子类定义一个层（quantum / topology / simulation），
    实现失败时的诊断→修复→重试循环。

    用法:
      agent = QuantumAgent(llm_client)
      result = agent.handle_failure(
          step_result=failed_result,
          config_path="config.json",
          run_dir=Path("md_run/md_xxx"),
          artifacts={"mol2": "/path/to/Li.mol2"},
      )
    """

    def __init__(
        self,
        name: str,
        system_prompt: str,
        tools: list[dict],
        tool_handler: Callable[[str, dict], str],
        llm_client,
        max_retries: int = 5,
        on_action: Callable[[str], None] = None,
    ):
        self.name = name
        self.system_prompt = system_prompt
        self.tools = tools
        self.handle_tool = tool_handler
        self.llm = llm_client
        self.max_retries = max_retries
        self.on_action = on_action

    def handle_failure(
        self,
        step_result: StepResult,
        config_path: str = "config.json",
        run_dir: str = ".",
        artifacts: dict = None,
        state_machine=None,
    ) -> StepResult:
        """
        处理流水线步骤失败。

        1. 构造 LLM 上下文（system prompt + 失败信息）
        2. 进入 tool-calling 循环
        3. 每次 tool 调用返回新的 StepResult
        4. 循环直到成功或耗尽重试

        Args:
            step_result: 失败的 StepResult
            config_path: config.json 路径
            run_dir: 运行目录
            artifacts: 已积累的产物映射

        Returns:
            成功 → StepResult(success=True)
            失败 → StepResult(success=False, escalated=True)
        """
        if artifacts is None:
            artifacts = {}

        ctx = RetryContext(
            layer=self.name,
            step_name=step_result.step_name,
            error_kind=step_result.error.kind if step_result.error else ErrorKind.UNKNOWN,
            max_attempts=self.max_retries,
        )

        # ── 构造初始上下文 ──
        config_text = ""
        try:
            with open(config_path) as f:
                config_text = f.read()
        except (FileNotFoundError, OSError):
            config_text = "(config.json 不可用)"

        user_context = self._build_context(step_result, config_text, run_dir, artifacts)

        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_context},
        ]

        # ── LLM Tool-Calling 循环 ──
        for _attempt in range(self.max_retries * 3):  # 每轮最多 3 次 LLM 调用
            try:
                r = self.llm.chat.completions.create(
                    model="deepseek-v4-pro",
                    messages=messages,
                    tools=self.tools,
                    temperature=0.1,
                )
                msg = r.choices[0].message

                if msg.tool_calls:
                    # 追加 assistant 消息
                    tcalls = [
                        {
                            "id": t.id,
                            "type": "function",
                            "function": {
                                "name": t.function.name,
                                "arguments": t.function.arguments,
                            },
                        }
                        for t in msg.tool_calls
                    ]
                    messages.append({
                        "role": "assistant",
                        "content": msg.content or "",
                        "tool_calls": tcalls,
                    })

                    for t in msg.tool_calls:
                        args = json.loads(t.function.arguments)
                        tool_result = self.handle_tool(t.function.name, args)
                        messages.append({
                            "role": "tool",
                            "tool_call_id": t.id,
                            "content": tool_result,
                        })

                        # 检查工具返回是否包含 StepResult
                        try:
                            parsed = json.loads(tool_result)
                            if isinstance(parsed, dict) and parsed.get("_step_result"):
                                step_data = parsed["_step_result"]
                                if step_data.get("success"):
                                    # 成功 —— 返回重建的 StepResult
                                    return StepResult(
                                        step_name=step_result.step_name,
                                        step_index=step_result.step_index,
                                        success=True,
                                        outputs=step_data.get("outputs", {}),
                                        artifacts=step_data.get("artifacts", []),
                                        duration_s=step_data.get("duration_s", 0),
                                    )
                                else:
                                    # 工具失败 —— 记录动作并继续
                                    ctx.attempts += 1
                                    if state_machine:
                                        state_machine.start_retry(self.name, ctx.attempts, self.max_retries)
                                    action = f"{t.function.name} → {step_data.get('error_message', 'failed')[:80]}"
                                    ctx.actions_tried.append(action)
                                    if self.on_action:
                                        self.on_action(action)
                                    if step_data.get("raw_output"):
                                        ctx.last_raw_output = step_data["raw_output"][-500:]
                        except (json.JSONDecodeError, KeyError):
                            pass  # 非 StepResult 的工具输出，忽略

                else:
                    # LLM 给出了文字回复 —— 可能是最终诊断
                    content = msg.content or ""
                    # 检查是否是放弃信号
                    if "escalat" in content.lower() or "cannot fix" in content.lower():
                        break
                    # 否则将回复追加为上下文，继续循环
                    messages.append({"role": "assistant", "content": content})
                    messages.append({
                        "role": "user",
                        "content": "请使用可用工具来修复此问题。如果无法修复，请生成升级信息。",
                    })

            except Exception as e:
                ctx.actions_tried.append(f"LLM 调用异常: {str(e)[:100]}")
                # 如果是通信错误，值得再试一次
                if ctx.attempts >= self.max_retries:
                    break

        # ── 所有重试耗尽 − 升级 ──
        return self._escalate(step_result, ctx)

    def _build_context(
        self,
        step_result: StepResult,
        config_text: str,
        run_dir: str,
        artifacts: dict,
    ) -> str:
        """构造发送给 LLM 的初始用户消息。"""
        err = step_result.error
        parts = [
            f"## 失败步骤\n"
            f"- 步骤: {step_result.step_name} (index={step_result.step_index})\n"
            f"- 错误类型: {err.kind.value if err else 'unknown'}\n"
            f"- 错误消息: {err.message if err else 'no message'}\n",
        ]
        if err and err.hint:
            parts.append(f"- 初步建议: {err.hint}\n")
        if err and err.raw_output:
            parts.append(f"\n## 原始输出（尾部）\n```\n{err.raw_output[-500:]}\n```\n")

        parts.append(f"\n## config.json\n```json\n{config_text[:2000]}\n```\n")
        parts.append(f"\n## 运行目录\n{run_dir}\n")
        if artifacts:
            parts.append(f"\n## 已有产物\n{json.dumps(artifacts, indent=2)}\n")
        parts.append("\n请诊断问题，使用可用工具修复并重试。如果无法修复，请生成升级信息。")
        return "\n".join(parts)

    def _escalate(self, step_result: StepResult, ctx: RetryContext) -> StepResult:
        """生成升级后的 StepResult。"""
        err = step_result.error or StepError(
            kind=ErrorKind.UNKNOWN, message="未知错误",
        )
        escalation = Escalation(
            layer=self.name,
            step_name=step_result.step_name,
            error_kind=err.kind.value,
            attempts_made=ctx.attempts,
            actions_tried=ctx.actions_tried,
            last_raw_output=ctx.last_raw_output or err.raw_output,
            recommendation=f"{self.name} 层自动修复已耗尽。请人工介入检查。",
            backup_plan="检查日志文件并手动运行相应步骤。",
        )

        return StepResult(
            step_name=step_result.step_name,
            step_index=step_result.step_index,
            success=False,
            error=StepError(
                kind=err.kind,
                message=f"[{self.name}] 自动修复失败（{ctx.attempts}/{ctx.max_attempts} 次重试后升级）",
                raw_output=err.raw_output,
                hint="请人工检查: " + (err.hint or ""),
            ),
            outputs=step_result.outputs,
            artifacts=step_result.artifacts,
            duration_s=step_result.duration_s,
            escalated=True,
            extra={"escalation": escalation.to_dict()},
        )
