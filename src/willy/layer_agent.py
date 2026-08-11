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
from hashlib import sha256
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, Callable, Mapping

from willy.errors import StepResult, StepError, ErrorKind, RetryContext
from willy.llm_config import DEFAULT_LLM_MODEL
from willy.action_contract import (
    ActionContractError,
    ActionProposal,
    ActionToolCatalog,
    ExecutedAction,
    ParameterChange,
    ValidatedAction,
    build_default_tool_catalog,
)
from willy.recovery_policy import RecoveryDecision, RecoveryPolicy
from willy.llm_budget import LLMBudget, LLMBudgetExceeded, LLMCircuitOpen


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
        on_decision: Callable[[Mapping[str, object]], None] = None,
        model: str = DEFAULT_LLM_MODEL,
        prompt_version: str = "",
        recovery_policy: RecoveryPolicy | None = None,
        tool_catalog: ActionToolCatalog | None = None,
        llm_budget: LLMBudget | None = None,
    ):
        self.name = name
        self.system_prompt = system_prompt
        self.tools = tools
        self.handle_tool = tool_handler
        self.llm = llm_client
        self.model = model
        self.max_retries = max_retries
        self.on_action = on_action
        self.on_decision = on_decision
        self.prompt_version = prompt_version or f"{name}_agent_v1"
        self.tool_catalog = tool_catalog or build_default_tool_catalog()
        self.recovery_policy = recovery_policy
        self.llm_budget = llm_budget
        self._budget_stop_reason = ""

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
            工具成功 → 工具实际执行步骤的 StepResult(success=True)
            失败 → StepResult(success=False, escalated=True)

        The returned ``StepResult`` deliberately keeps the tool's own step
        identity.  A repair action can rebuild an upstream prerequisite; it
        must never be relabelled as the failed downstream pipeline step.
        ``PipelineOrchestrator`` owns the subsequent rerun and completion
        decision.
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
                if self.llm_budget is not None:
                    self.llm_budget.before_call()
                call_kwargs = {
                    "model": self.model,
                    "messages": messages,
                    "tools": self.tools,
                    "temperature": 0.1,
                }
                if self.llm_budget is not None:
                    call_kwargs["timeout"] = self.llm_budget.call_timeout_s
                r = self.llm.chat.completions.create(**call_kwargs)
                if self.llm_budget is not None:
                    self.llm_budget.record_success()
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
                        tool_result = self._dispatch_tool(
                            t.function.name,
                            args,
                            step_result=step_result,
                            ctx=ctx,
                            run_dir=run_dir,
                            config_path=config_path,
                        )
                        messages.append({
                            "role": "tool",
                            "tool_call_id": t.id,
                            "content": tool_result,
                        })

                        # 检查工具返回是否包含 StepResult
                        try:
                            parsed = json.loads(tool_result)
                            if isinstance(parsed, dict) and parsed.get("_step_result"):
                                # _step_result 是平铺标记，数据字段在顶层
                                step_data = parsed
                                if (
                                    step_data.get("error_kind") == ErrorKind.USER_CONFIRMATION_REQUIRED.value
                                    or step_data.get("extra", {}).get("requires_user_confirmation") is True
                                ):
                                    return self._escalate_for_user_confirmation(step_result, ctx)
                                if step_data.get("success"):
                                    # Keep the tool's actual step identity.
                                    # An upstream EM/MDP repair is useful, but
                                    # it is not a successful EQ execution.
                                    return StepResult(
                                        step_name=str(step_data.get("step_name") or step_result.step_name),
                                        step_index=int(step_data.get("step_index", step_result.step_index)),
                                        success=True,
                                        outputs=step_data.get("outputs", {}) if isinstance(step_data.get("outputs"), dict) else {},
                                        artifacts=step_data.get("artifacts", []) if isinstance(step_data.get("artifacts"), list) else [],
                                        duration_s=float(step_data.get("duration_s", 0) or 0),
                                        extra=step_data.get("extra", {}) if isinstance(step_data.get("extra"), dict) else {},
                                        target_type=str(step_data.get("target_type") or ""),
                                        target=str(step_data.get("target") or ""),
                                    )
                                else:
                                    # 工具失败 —— 记录动作并继续
                                    action = f"{t.function.name} → {step_data.get('error_message', 'failed')[:80]}"
                                    self._record_failed_attempt(
                                        ctx, state_machine, action,
                                        raw_output=step_data.get("raw_output", ""),
                                    )
                                    if ctx.attempts >= self.max_retries:
                                        return self._escalate(step_result, ctx)
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
                if self.llm_budget is not None and not isinstance(e, (LLMBudgetExceeded, LLMCircuitOpen)):
                    self.llm_budget.record_failure()
                    self._budget_stop_reason = str(e)[:120]
                elif isinstance(e, (LLMBudgetExceeded, LLMCircuitOpen)):
                    self._budget_stop_reason = str(e)
                    break
                self._record_failed_attempt(
                    ctx,
                    state_machine,
                    f"LLM 调用异常: {str(e)[:100]}",
                )
                # 通信异常也会消耗与工具失败相同的修复预算，避免界面显示 0/N。
                if ctx.attempts >= self.max_retries:
                    break

        # ── 所有重试耗尽 − 升级 ──
        return self._escalate(step_result, ctx)

    def _dispatch_tool(
        self,
        tool_name: str,
        args: dict,
        *,
        step_result: StepResult,
        ctx: RetryContext,
        run_dir: str,
        config_path: str,
    ) -> str:
        """Validate and authorize one LLM-requested tool before dispatch."""
        if self.recovery_policy is None:
            return self.handle_tool(tool_name, args)
        try:
            declaration = self.tool_catalog.require(tool_name)
            proposal = self._build_action_proposal(
                declaration=declaration,
                tool_name=tool_name,
                args=args,
                step_result=step_result,
                run_dir=run_dir,
                config_path=config_path,
            )
        except (ActionContractError, KeyError, ValueError):
            return json.dumps({
                "_step_result": True,
                "success": False,
                "step_name": step_result.step_name,
                "step_index": step_result.step_index,
                "error_kind": ErrorKind.CONFIG_INVALID.value,
                "error_message": "工具未登记在恢复策略中",
                "extra": {"policy_denied": True},
            }, ensure_ascii=False)
        decision = self.recovery_policy.authorize(
            layer=self.name,
            error_kind=ctx.error_kind,
            step=step_result.step_index,
            tool=declaration,
            arguments=args,
            attempt=ctx.attempts,
        )
        try:
            validated = self.tool_catalog.validate(proposal, policy_id=decision.policy_id)
        except ActionContractError:
            return json.dumps({
                "_step_result": True,
                "success": False,
                "step_name": step_result.step_name,
                "step_index": step_result.step_index,
                "error_kind": ErrorKind.CONFIG_INVALID.value,
                "error_message": "工具提案不符合动作契约",
                "extra": {"policy_denied": True},
            }, ensure_ascii=False)
        rejection = self._tool_request_rejection(tool_name, args, step_result)
        if rejection:
            self._record_decision(
                validated, decision, attempt=ctx.attempts,
                result="evidence_rejected", success=False,
            )
            return json.dumps({
                "_step_result": True,
                "success": False,
                "step_name": step_result.step_name,
                "step_index": step_result.step_index,
                "error_kind": ErrorKind.INPUT_CONTRACT.value,
                "error_message": rejection,
                "extra": {
                    "policy_denied": True,
                    "evidence_rejected": True,
                    "policy_id": decision.policy_id,
                },
            }, ensure_ascii=False)
        if not decision.allowed:
            self._record_decision(
                validated, decision, attempt=ctx.attempts,
                result="policy_denied", success=False,
            )
            kind = ErrorKind.USER_CONFIRMATION_REQUIRED if decision.requires_confirmation else ErrorKind.CONFIG_INVALID
            return json.dumps({
                "_step_result": True,
                "success": False,
                "step_name": step_result.step_name,
                "step_index": step_result.step_index,
                "error_kind": kind.value,
                "error_message": decision.reason,
                "extra": {
                    "policy_denied": True,
                    "policy_id": decision.policy_id,
                    "requires_user_confirmation": decision.requires_confirmation,
                    "requires_fork": decision.fork_only,
                    "restart_step": decision.restart_step,
                },
            }, ensure_ascii=False)
        tool_result = self.handle_tool(tool_name, args)
        self._record_execution(validated, decision, tool_result, attempt=ctx.attempts)
        return tool_result

    def _tool_request_rejection(
        self,
        tool_name: str,
        args: Mapping[str, object],
        step_result: StepResult,
    ) -> str | None:
        """Let a layer reject an otherwise valid tool proposal from evidence."""
        return None

    def _build_action_proposal(
        self,
        *,
        declaration,
        tool_name: str,
        args: Mapping[str, object],
        step_result: StepResult,
        run_dir: str,
        config_path: str,
    ) -> ActionProposal:
        """Build a bounded in-memory proposal; only the recorder may persist it."""
        fingerprint = ""
        try:
            fingerprint = sha256(Path(config_path).read_bytes()).hexdigest()
        except OSError:
            pass
        changes = tuple(
            ParameterChange(field_name=field, after=args[field])
            for field in declaration.parameter_effects
            if args.get(field) is not None
        )
        return ActionProposal(
            run_id=Path(run_dir).name,
            layer=self.name,
            tool_name=tool_name,
            arguments=args,
            failed_step=step_result.step_index,
            parameter_changes=changes,
            config_fingerprint=fingerprint,
            model_id=self.model,
            prompt_version=self.prompt_version,
        )

    def _record_decision(
        self,
        action: ValidatedAction,
        decision: RecoveryDecision,
        *,
        attempt: int,
        result: str,
        success: bool | None,
    ) -> None:
        if self.on_decision is None:
            return
        payload: dict[str, object] = {
            "decision_id": action.decision_id,
            "action_id": action.decision_id,
            "layer": action.proposal.layer,
            "step": action.proposal.failed_step,
            "policy_id": decision.policy_id,
            "selected_tool": action.proposal.tool_name,
            "tool_effect": action.effective_effect.value,
            "parameter_changes": [change.field_name for change in action.proposal.parameter_changes],
            "model_id": action.proposal.model_id,
            "prompt_version": action.proposal.prompt_version,
            "attempt": attempt,
            "restart_step": decision.restart_step,
            "requires_confirmation": decision.requires_confirmation,
            "requires_fork": decision.fork_only,
            "result": result,
        }
        if success is not None:
            payload["success"] = success
        try:
            self.on_decision(payload)
        except Exception:
            pass

    def _record_execution(
        self,
        action: ValidatedAction,
        decision: RecoveryDecision,
        tool_result: object,
        *,
        attempt: int,
    ) -> None:
        try:
            parsed = json.loads(str(tool_result))
        except (TypeError, json.JSONDecodeError):
            self._record_decision(action, decision, attempt=attempt, result="tool_returned", success=None)
            return
        if not isinstance(parsed, dict) or not parsed.get("_step_result"):
            self._record_decision(action, decision, attempt=attempt, result="tool_returned", success=None)
            return
        success = bool(parsed.get("success"))
        summary = str(
            parsed.get("error_message") or ("工具执行成功" if success else "工具执行失败")
        ).replace("\n", " ").replace("\r", " ").strip()[:240]
        output_keys = tuple(
            str(key) for key in parsed.get("outputs", {})
            if isinstance(parsed.get("outputs"), dict)
        )
        error_kind = "" if success else str(parsed.get("error_kind") or ErrorKind.UNKNOWN.value)
        try:
            execution = ExecutedAction(
                action=action,
                success=success,
                result_summary=summary,
                output_keys=output_keys,
                error_kind=error_kind,
            )
        except ActionContractError:
            self._record_decision(
                action, decision, attempt=attempt,
                result="execution_result_invalid", success=False,
            )
            return
        self._record_decision(
            action,
            decision,
            attempt=attempt,
            result="executed",
            success=execution.success,
        )

    def _record_failed_attempt(
        self,
        ctx: RetryContext,
        state_machine,
        action: str,
        *,
        raw_output: object = "",
    ) -> None:
        """Record every failed repair attempt under one bounded retry budget."""
        ctx.attempts += 1
        ctx.actions_tried.append(action)
        if isinstance(raw_output, str) and raw_output:
            ctx.last_raw_output = raw_output[-500:]
        if state_machine is not None:
            state_machine.start_retry(self.name, ctx.attempts, self.max_retries)
        if self.on_action is not None:
            self.on_action(action)

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

        if step_result.outputs:
            parts.append(
                "\n## 失败步骤保留的产物\n"
                f"{json.dumps(step_result.outputs, indent=2, default=str)}\n"
            )
        if step_result.extra:
            # Extra data is bounded, structured evidence (not raw logs).  It
            # lets the model distinguish a preflight contract failure from an
            # actual external-process failure without guessing from text.
            parts.append(
                "\n## 私有执行证据（仅用于诊断）\n"
                f"{json.dumps(step_result.extra, ensure_ascii=False, indent=2, default=str)}\n"
            )
        parts.append(f"\n## config.json\n```json\n{config_text[:2000]}\n```\n")
        parts.append(f"\n## 运行目录\n{run_dir}\n")
        if artifacts:
            parts.append(f"\n## 已有产物\n{json.dumps(artifacts, indent=2, default=str)}\n")
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
            recommendation=(
                f"{self.name} 层自动修复已耗尽（{ctx.attempts}/{ctx.max_attempts} 次重试）。"
                f"⚠️ 流水线在此步骤停止，MD 模拟不会继续执行。"
                f"建议：人工检查本次运行的日志与 manifest，修复输入或配置后重新运行。"
            ),
            backup_plan=(
                "保留本次运行产物供排查；修复上游输入或配置后，重新运行对应步骤。"
            ),
        )

        return StepResult(
            step_name=step_result.step_name,
            step_index=step_result.step_index,
            success=False,
            error=StepError(
                kind=err.kind,
                message=f"[{self.name}] 自动修复失败（{ctx.attempts}/{ctx.max_attempts} 次重试后升级）",
                raw_output=err.raw_output,
                hint="⚠ MD 不会继续！请人工检查并修复后重新运行: " + (err.hint or ""),
            ),
            outputs=step_result.outputs,
            artifacts=step_result.artifacts,
            duration_s=step_result.duration_s,
            escalated=True,
            extra={
                "escalation": escalation.to_dict(),
                "llm_budget": self.llm_budget.snapshot() if self.llm_budget is not None else {},
                "llm_budget_stop_reason": self._budget_stop_reason,
            },
        )

    def _escalate_for_user_confirmation(
        self,
        step_result: StepResult,
        ctx: RetryContext,
    ) -> StepResult:
        """Stop repair when a tool proposes a scientific protocol change."""
        action = "检测到模拟协议变更请求，已升级为用户确认"
        if action not in ctx.actions_tried:
            ctx.actions_tried.append(action)
        if self.on_action:
            self.on_action(action)
        escalation = Escalation(
            layer=self.name,
            step_name=step_result.step_name,
            error_kind=ErrorKind.USER_CONFIRMATION_REQUIRED.value,
            attempts_made=ctx.attempts,
            actions_tried=ctx.actions_tried,
            recommendation=(
                "本次修复需要修改模拟协议。当前运行未改写，请审阅新的方案并由用户确认后重新启动。"
            ),
            backup_plan="保持当前协议并停止自动修复；保留本次运行产物供排查。",
        )
        return StepResult(
            step_name=step_result.step_name,
            step_index=step_result.step_index,
            success=False,
            error=StepError(
                kind=ErrorKind.USER_CONFIRMATION_REQUIRED,
                message="模拟协议变更需要用户确认；自动修复已停止。",
                hint="请审阅新的方案并由用户确认后重新启动。",
            ),
            outputs=step_result.outputs,
            artifacts=step_result.artifacts,
            duration_s=step_result.duration_s,
            escalated=True,
            extra={"escalation": escalation.to_dict()},
        )
