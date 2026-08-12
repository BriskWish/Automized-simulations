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
    ActionEffect,
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

    # These outcomes are decided by the server-side state and policy
    # contracts.  Letting a model attempt to recover them can bypass a user
    # confirmation, fork, or input boundary.
    _SERVER_TERMINAL_ERROR_KINDS = frozenset({
        ErrorKind.DEPENDENCY_MISSING,
        ErrorKind.DEPENDENCY_NO_EXEC,
        ErrorKind.RUNTIME_UNAVAILABLE,
        ErrorKind.INPUT_CONTRACT,
        ErrorKind.USER_CONFIRMATION_REQUIRED,
        ErrorKind.LOCK_CONFLICT,
        ErrorKind.RECOVERY_CONFLICT,
    })

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
        rule = None
        if self.recovery_policy is not None:
            rule = self.recovery_policy.rule_for(
                layer=self.name,
                error_kind=ctx.error_kind,
                step=step_result.step_index,
            )
            if rule is not None:
                ctx.max_attempts = min(ctx.max_attempts, rule.max_attempts)

        terminal = self._initial_terminal_reason(step_result)
        if terminal:
            if step_result.error and step_result.error.kind is ErrorKind.USER_CONFIRMATION_REQUIRED:
                return self._escalate_for_user_confirmation(step_result, ctx)
            return self._terminal_escalation(step_result, ctx, terminal)

        # Confirmation/fork routes are service-owned transitions.  Do not
        # ask the model to diagnose or select an action first: that would
        # consume budget and make an already-confirmation-gated workflow look
        # like an automatic repair attempt.
        if rule is not None and rule.requires_confirmation:
            return self._escalate_for_user_confirmation(step_result, ctx)
        if rule is not None and rule.fork_only:
            return self._terminal_escalation(step_result, ctx, "requires_fork")

        # Contract-backed production agents always have a diagnostic tool.
        # Keep a legacy fallback for direct base-class consumers used by small
        # integrations: their first permitted retry tool acts as a no-op
        # diagnosis, while real repair remains two-phase when diagnostics exist.
        diagnostic_tools = self._diagnostic_tool_schemas()
        legacy_single_phase = False
        if not diagnostic_tools:
            if self.recovery_policy is None:
                diagnostic_tools = self._legacy_diagnostic_tool_schemas()
                legacy_single_phase = bool(diagnostic_tools)
            if not diagnostic_tools:
                return self._terminal_escalation(step_result, ctx, "diagnostic_tool_unavailable")

        # Each repair attempt has two server-driven phases.  Fresh messages
        # intentionally prevent raw output, paths, complete config, and old
        # model prose from accumulating into an uncontrolled long prompt.
        while ctx.attempts < ctx.max_attempts:
            parsed_diagnosis: dict | None = None
            if not legacy_single_phase:
                diagnostic = diagnostic_tools[0]
                diagnosis_result = self._request_tool_call(
                    phase="DIAGNOSE",
                    tools=[diagnostic],
                    step_result=step_result,
                    ctx=ctx,
                )
                if diagnosis_result["terminal"] is not None:
                    return self._terminal_escalation(step_result, ctx, diagnosis_result["terminal"])

                tool_name = diagnosis_result["tool_name"]
                args = diagnosis_result["args"]
                try:
                    raw_diagnosis = self._dispatch_tool(
                        tool_name,
                        args,
                        step_result=step_result,
                        ctx=ctx,
                        run_dir=run_dir,
                        config_path=config_path,
                    )
                except Exception as exc:
                    return self._terminal_escalation(step_result, ctx, "diagnostic_tool_failure", detail=str(exc))
                parsed_diagnosis = self._parse_tool_result(raw_diagnosis)
                policy_terminal = self._terminal_from_tool_result(parsed_diagnosis)
                if policy_terminal:
                    if policy_terminal == "user_confirmation_required":
                        return self._escalate_for_user_confirmation(step_result, ctx)
                    return self._terminal_escalation(step_result, ctx, policy_terminal)
                if isinstance(parsed_diagnosis, dict) and parsed_diagnosis.get("_step_result") and not parsed_diagnosis.get("success"):
                    return self._terminal_escalation(step_result, ctx, "diagnostic_tool_failure")

            recovery_tools, recovery_terminal = self._recovery_tool_schemas(step_result, ctx)
            if legacy_single_phase:
                recovery_tools = self._legacy_recovery_tool_schemas()
            if recovery_terminal:
                return self._terminal_escalation(step_result, ctx, recovery_terminal)
            if not recovery_tools:
                return self._terminal_escalation(step_result, ctx, "no_retry_safe_recovery_action")

            repair_request = self._request_tool_call(
                phase="RECOVER",
                tools=recovery_tools,
                step_result=step_result,
                ctx=ctx,
                diagnosis=self._compact_tool_evidence(parsed_diagnosis),
            )
            if repair_request["terminal"] is not None:
                return self._terminal_escalation(step_result, ctx, repair_request["terminal"])

            tool_name = repair_request["tool_name"]
            args = repair_request["args"]
            try:
                raw_repair = self._dispatch_tool(
                    tool_name,
                    args,
                    step_result=step_result,
                    ctx=ctx,
                    run_dir=run_dir,
                    config_path=config_path,
                )
            except Exception as exc:
                self._record_failed_attempt(ctx, state_machine, f"{tool_name} → 工具执行异常")
                if ctx.exhausted:
                    return self._escalate(step_result, ctx)
                continue

            parsed_repair = self._parse_tool_result(raw_repair)
            policy_terminal = self._terminal_from_tool_result(parsed_repair)
            if policy_terminal:
                if policy_terminal == "user_confirmation_required":
                    return self._escalate_for_user_confirmation(step_result, ctx)
                return self._terminal_escalation(step_result, ctx, policy_terminal)
            if not isinstance(parsed_repair, dict) or not parsed_repair.get("_step_result"):
                return self._terminal_escalation(step_result, ctx, "repair_result_invalid")
            if parsed_repair.get("success"):
                return self._step_result_from_tool_data(parsed_repair, step_result)

            action = f"{tool_name} → {str(parsed_repair.get('error_message') or 'failed')[:80]}"
            self._record_failed_attempt(
                ctx,
                state_machine,
                action,
                raw_output=parsed_repair.get("raw_output", ""),
            )

        return self._escalate(step_result, ctx)

    def _initial_terminal_reason(self, step_result: StepResult) -> str | None:
        """Return a server-owned terminal boundary before any model call."""
        error = step_result.error
        if error is None:
            return None
        if error.kind in self._SERVER_TERMINAL_ERROR_KINDS:
            return f"server_terminal_{error.kind.value}"
        return None

    @staticmethod
    def _tool_name(schema: Mapping[str, object]) -> str:
        function = schema.get("function") if isinstance(schema, Mapping) else None
        return str(function.get("name") or "") if isinstance(function, Mapping) else ""

    def _diagnostic_tool_schemas(self) -> list[dict]:
        """Expose only one diagnostic tool in DIAGNOSE, never repair tools."""
        diagnostic: list[dict] = []
        for schema in self.tools:
            name = self._tool_name(schema)
            if not name or "diagnose" not in name:
                continue
            if self.recovery_policy is not None:
                try:
                    declaration = self.tool_catalog.require(name)
                except (ActionContractError, KeyError):
                    continue
                if declaration.layer != self.name or not declaration.is_read_only:
                    continue
            diagnostic.append(schema)
        return diagnostic[:1]

    def _legacy_diagnostic_tool_schemas(self) -> list[dict]:
        """Compatibility only for unconfigured base-class test/integration use."""
        for schema in self.tools:
            name = self._tool_name(schema)
            if name and "retry" in name:
                return [schema]
        return []

    def _legacy_recovery_tool_schemas(self) -> list[dict]:
        return [
            schema for schema in self.tools
            if self._tool_name(schema) and "retry" in self._tool_name(schema)
        ][:8]

    def _recovery_tool_schemas(
        self,
        step_result: StepResult,
        ctx: RetryContext | None = None,
    ) -> tuple[list[dict], str | None]:
        """Return policy-whitelisted retry-safe repair tools for RECOVER."""
        allowed_names: set[str] | None = None
        if self.recovery_policy is not None:
            rule = self.recovery_policy.rule_for(
                layer=self.name,
                error_kind=step_result.error.kind if step_result.error else ErrorKind.UNKNOWN,
                step=step_result.step_index,
            )
            if rule is None:
                return [], "no_matching_recovery_policy"
            if not rule.allowed_tools:
                return [], "no_policy_allowed_recovery_action"
            allowed_names = set(rule.allowed_tools)

        # The policy accepts a declared tool only after argument validation.
        # Here we additionally narrow the schema list to actions whose base
        # effect can be automatic. Confirmation/fork actions must never be
        # offered as a recovery candidate.

        recovery: list[dict] = []
        for schema in self.tools:
            name = self._tool_name(schema)
            if not name or "diagnose" in name:
                continue
            if allowed_names is not None and name not in allowed_names:
                continue
            if self.recovery_policy is not None:
                try:
                    declaration = self.tool_catalog.require(name)
                except (ActionContractError, KeyError):
                    continue
                if declaration.layer != self.name or declaration.effect is not ActionEffect.RETRY_SAFE:
                    continue
                if any(token in name for token in ("modify_config", "set_backend", "skip_molecule", "configure_", "migrate_")):
                    continue
            elif "retry" not in name:
                # Legacy test agents without a contract may still exercise a
                # bounded retry tool, but config/skip/write tools stay hidden.
                continue
            recovery.append(schema)
        # One repair action per attempt keeps the LLM decision surface narrow.
        return recovery[:1], None

    def _request_tool_call(
        self,
        *,
        phase: str,
        tools: list[dict],
        step_result: StepResult,
        ctx: RetryContext,
        diagnosis: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        """Require exactly one allowed tool call in one bounded Agent phase."""
        allowed_names = [self._tool_name(tool) for tool in tools]
        prompt = self._build_phase_context(
            phase=phase,
            step_result=step_result,
            ctx=ctx,
            allowed_tools=allowed_names,
            diagnosis=diagnosis,
        )
        try:
            if self.llm_budget is not None:
                self.llm_budget.before_call()
            call_kwargs = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": prompt},
                ],
                "tools": tools,
                # Each controlled phase exposes exactly one tool. ``required``
                # is the provider-compatible enforcement mode; the server
                # still validates that the returned call has this exact name
                # and valid arguments before dispatch.
                "tool_choice": "required",
                "temperature": 0.0,
            }
            if self.llm_budget is not None:
                call_kwargs["timeout"] = self.llm_budget.call_timeout_s
            response = self.llm.chat.completions.create(**call_kwargs)
            if self.llm_budget is not None:
                self.llm_budget.record_success()
            choices = getattr(response, "choices", None) or []
            if not choices or not getattr(choices[0], "message", None):
                return {"terminal": "model_protocol_failure"}
            calls = getattr(choices[0].message, "tool_calls", None) or []
        except (LLMBudgetExceeded, LLMCircuitOpen) as exc:
            self._budget_stop_reason = str(exc)
            return {"terminal": "llm_budget_exhausted"}
        except Exception as exc:
            if self.llm_budget is not None:
                self.llm_budget.record_failure()
            self._budget_stop_reason = self._exception_summary(exc)
            outcome = "model_transport_failure" if self._is_transport_exception(exc) else "model_protocol_failure"
            return {"terminal": outcome}

        if not calls:
            return {"terminal": "model_no_tool_call"}
        if len(calls) != 1:
            return {"terminal": "model_multiple_tool_calls"}

        call = calls[0]
        name = str(getattr(getattr(call, "function", None), "name", "") or "")
        if name not in allowed_names:
            return {"terminal": "model_disallowed_tool"}
        try:
            args = json.loads(str(getattr(getattr(call, "function", None), "arguments", "{}")))
        except json.JSONDecodeError:
            return {"terminal": "model_invalid_tool_arguments"}
        if not isinstance(args, dict):
            return {"terminal": "model_invalid_tool_arguments"}
        return {"terminal": None, "tool_name": name, "args": args}

    def _build_phase_context(
        self,
        *,
        phase: str,
        step_result: StepResult,
        ctx: RetryContext,
        allowed_tools: list[str],
        diagnosis: Mapping[str, object] | None = None,
    ) -> str:
        """Build the minimal per-phase prompt; never include paths or raw logs."""
        error = step_result.error
        evidence = {
            "error_message": self._safe_text(error.message if error else "", 240),
            "hint": self._safe_text(error.hint if error else "", 180),
            "step_extra_keys": sorted(str(key) for key in step_result.extra)[:12],
            "output_keys": sorted(str(key) for key in step_result.outputs)[:12],
        }
        if diagnosis:
            evidence["diagnosis"] = dict(diagnosis)
        payload = {
            "phase": phase,
            "current_state": {
                "layer": self.name,
                "step": step_result.step_index,
                "step_name": self._safe_text(step_result.step_name, 100),
                "error_kind": error.kind.value if error else ErrorKind.UNKNOWN.value,
            },
            "verified_evidence": evidence,
            "allowed_tools": allowed_tools,
            "remaining_budget": {
                "repair_attempts_remaining": max(0, ctx.max_attempts - ctx.attempts),
                "repair_attempt_limit": ctx.max_attempts,
                "llm": self.llm_budget.snapshot() if self.llm_budget is not None else {"managed_by": "service"},
            },
            "unique_next_action": (
                "必须且只能调用一个诊断工具。"
                if phase == "DIAGNOSE"
                else (
                    "这是服务端已授权的当前执行阶段，必须立即且只能调用一个允许的 "
                    "retry_safe 修复工具；不要解释、等待确认或返回普通文本。"
                )
            ),
            "execution_target": (
                "从已验证错误摘要中提取当前对象名填入工具参数；不得猜测新的对象或路径。"
                if phase == "RECOVER" else ""
            ),
            "prohibited": [
                "不得输出纯文本替代工具调用",
                "不得调用未列出的工具",
                "不得修改配置、启动流程、绕过确认或派生运行",
                "不得要求或暴露完整配置、路径、原始日志或产物清单",
            ],
        }
        return "受控恢复状态（服务端生成，必须遵守）：\n" + json.dumps(
            payload, ensure_ascii=False, separators=(",", ":")
        )

    @staticmethod
    def _safe_text(value: object, limit: int) -> str:
        text = str(value or "").replace("\n", " ").replace("\r", " ").strip()
        if "/" in text or "\\" in text:
            return "已记录服务端错误摘要"
        return text[:limit]

    @staticmethod
    def _parse_tool_result(payload: object) -> dict | None:
        try:
            parsed = json.loads(str(payload))
        except (TypeError, json.JSONDecodeError):
            return None
        return parsed if isinstance(parsed, dict) else None

    @staticmethod
    def _compact_tool_evidence(payload: Mapping[str, object] | None) -> dict[str, object]:
        if not isinstance(payload, Mapping):
            return {"status": "unavailable"}
        result: dict[str, object] = {}
        for key in ("source", "severity", "issues", "evidence", "hint", "error_kind", "error_message"):
            value = payload.get(key)
            if isinstance(value, list):
                result[key] = [str(item).replace("\n", " ")[:160] for item in value[:3]]
            elif value not in (None, ""):
                result[key] = str(value).replace("\n", " ")[:240]
        return result or {"status": "completed"}

    def _terminal_from_tool_result(self, payload: Mapping[str, object] | None) -> str | None:
        if not isinstance(payload, Mapping):
            return None
        extra = payload.get("extra") if isinstance(payload.get("extra"), Mapping) else {}
        kind = str(payload.get("error_kind") or "")
        if extra.get("requires_user_confirmation") or kind == ErrorKind.USER_CONFIRMATION_REQUIRED.value:
            return "user_confirmation_required"
        if extra.get("requires_fork"):
            return "requires_fork"
        if extra.get("policy_denied"):
            return "policy_denied"
        if kind in {
            ErrorKind.INPUT_CONTRACT.value,
            ErrorKind.RUNTIME_UNAVAILABLE.value,
            ErrorKind.DEPENDENCY_MISSING.value,
            ErrorKind.DEPENDENCY_NO_EXEC.value,
            ErrorKind.LOCK_CONFLICT.value,
        }:
            return f"server_terminal_{kind}"
        return None

    @staticmethod
    def _exception_summary(exc: Exception) -> str:
        return f"{type(exc).__name__}: {str(exc)}".replace("\n", " ")[:160]

    @staticmethod
    def _is_transport_exception(exc: Exception) -> bool:
        name = type(exc).__name__.lower()
        detail = str(exc).lower()
        markers = (
            "connection", "timeout", "transport", "network", "socket", "ssl",
            "dns", "httpx", "api connection", "api timeout", "超时", "连接",
        )
        return any(marker in name or marker in detail for marker in markers)

    def _record_system_decision(
        self,
        step_result: StepResult,
        ctx: RetryContext,
        result: str,
        candidate_tools: list[str] | tuple[str, ...] = (),
    ) -> None:
        if self.on_decision is None:
            return
        payload = {
            "layer": self.name,
            "step": step_result.step_index,
            "error_kind": ctx.error_kind.value,
            "policy_id": "server_recovery_phase",
            "candidate_tools": list(candidate_tools)[:8],
            "model_id": self.model,
            "prompt_version": self.prompt_version,
            "attempt": ctx.attempts,
            "result": result,
            "success": False,
        }
        try:
            self.on_decision(payload)
        except Exception:
            pass

    def _terminal_escalation(
        self,
        step_result: StepResult,
        ctx: RetryContext,
        reason: str,
        *,
        detail: str = "",
    ) -> StepResult:
        self._record_system_decision(step_result, ctx, reason)
        result = self._escalate(step_result, ctx)
        result.extra["recovery_terminal_reason"] = reason
        if detail:
            result.extra["recovery_terminal_detail"] = self._safe_text(detail, 160)
        return result

    @staticmethod
    def _step_result_from_tool_data(step_data: Mapping[str, object], fallback: StepResult) -> StepResult:
        """Keep the repair tool's identity instead of claiming failed-step success."""
        return StepResult(
            step_name=str(step_data.get("step_name") or fallback.step_name),
            step_index=int(step_data.get("step_index", fallback.step_index)),
            success=True,
            outputs=step_data.get("outputs", {}) if isinstance(step_data.get("outputs"), dict) else {},
            artifacts=step_data.get("artifacts", []) if isinstance(step_data.get("artifacts"), list) else [],
            duration_s=float(step_data.get("duration_s", 0) or 0),
            extra=step_data.get("extra", {}) if isinstance(step_data.get("extra"), dict) else {},
            target_type=str(step_data.get("target_type") or ""),
            target=str(step_data.get("target") or ""),
        )

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
            # Direct unit/integration callers may omit a real run directory;
            # the production orchestrator always supplies md__<id>.
            run_id=Path(run_dir).name or "run",
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
            state_machine.start_retry(self.name, ctx.attempts, ctx.max_attempts)
        if self.on_action is not None:
            self.on_action(action)

    def _build_context(
        self,
        step_result: StepResult,
        config_text: str,
        run_dir: str,
        artifacts: dict,
    ) -> str:
        """Compatibility context for callers that still inspect this helper.

        ``handle_failure`` uses ``_build_phase_context`` instead.  Keep this
        method compact as well so test/debug callers do not accidentally
        reintroduce full configuration, filesystem paths, or raw log tails.
        """
        err = step_result.error
        payload = {
            "failure": {
                "step": self._safe_text(step_result.step_name, 100),
                "index": step_result.step_index,
                "error_kind": err.kind.value if err else ErrorKind.UNKNOWN.value,
                "error_message": self._safe_text(err.message if err else "", 240),
                "hint": self._safe_text(err.hint if err else "", 180),
            },
            "evidence_keys": {
                "failed_step_outputs": sorted(str(key) for key in step_result.outputs)[:12],
                "step_extra": sorted(str(key) for key in step_result.extra)[:12],
                "artifacts": sorted(str(key) for key in artifacts)[:12],
                "config_available": bool(config_text and config_text != "(config.json 不可用)"),
            },
            "notice": "该兼容摘要不包含路径、原始日志、完整 config 或产物列表。",
        }
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    def _escalate(self, step_result: StepResult, ctx: RetryContext) -> StepResult:
        """生成升级后的 StepResult。"""
        err = step_result.error or StepError(
            kind=ErrorKind.UNKNOWN, message="未知错误",
        )
        if ctx.attempts:
            recommendation = (
                f"{self.name} 层自动修复已耗尽（{ctx.attempts}/{ctx.max_attempts} 次重试）。"
                "⚠️ 流水线在此步骤停止，MD 模拟不会继续执行。"
                "建议：人工检查本次运行的日志与 manifest，修复输入或配置后重新运行。"
            )
        else:
            recommendation = (
                f"{self.name} 层未执行受控修复动作即升级。"
                "流水线在此步骤停止，MD 模拟不会继续执行。"
                "建议：检查运行环境、LLM 服务或输入契约后重新运行。"
            )
        escalation = Escalation(
            layer=self.name,
            step_name=step_result.step_name,
            error_kind=err.kind.value,
            attempts_made=ctx.attempts,
            actions_tried=ctx.actions_tried,
            last_raw_output=ctx.last_raw_output or err.raw_output,
            recommendation=recommendation,
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
        self._record_system_decision(step_result, ctx, "user_confirmation_required")
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
