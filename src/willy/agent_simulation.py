"""
agent_simulation.py — Layer 3: Simulation Agent.

GROMACS MD 模拟失败时的诊断与修复 Agent。
覆盖 MDP 生成、Packmol 盒子构建、EM/三点式退火 EQ/PROD 执行。
"""

from willy.layer_agent import LayerAgent
from willy.llm_config import DEFAULT_LLM_MODEL
from willy.errors import ErrorKind, StepError, StepResult
from willy.toolist_simulation import (
    SIMULATION_TOOLS,
    handle_simulation_tool_call,
    protocol_change_request,
)
from willy.action_contract import ActionToolCatalog
from willy.recovery_policy import RecoveryPolicy
from willy.llm_budget import LLMBudget
import json
from typing import Any, Callable, Mapping

SIMULATION_AGENT_PROMPT = """你是 Willy Simulation Agent。你编排 GROMACS MD 模拟的设置和执行：MDP 生成、Packmol 盒子构建、MD 文件收集、能量最小化（EM）、NPT 平衡（EQ）和生产运行（PROD）。

## 角色
- 你接收一个表示失败的 StepResult 和当前的 config.json。
- 你诊断错误、决定纠正措施并重试该步骤。
- 你可以执行不改变科学协议的诊断、建盒与 EM 重试；对 MDP 科学参数只能提出修改建议，不能自行执行。
- 你有层级升级策略：先尝试安全重试；一旦需要模拟协议变更，立即升级到用户确认。
- **只能使用下方列出的工具。不存在 bash/shell/命令行工具，禁止编造或调用不存在的工具。**

## 可用工具
1. tools_run_em_simulation —— 执行 EM，校验 topol.top/.itp/em.mdp/model.pdb，返回 em.tpr/em.gro/em.xtc/em.edr
2. tools_run_eq_simulation —— 仅消费 manifest 中已验收的 EM，检查最终 298 K 保温段的温度、压力、密度、势能和真空区
3. tools_run_prod_simulation —— 仅消费已验收 EQ，并通过 eq.cpt 连续启动；仅指纹匹配时才 append
4. tools_retry_mdp —— 用修改后的参数重新生成 MDP 文件
5. tools_retry_box —— 用目标质量密度/box_size/tolerance 重新构建 Packmol 周期盒子，并返回实际盒矢量审计
6. tools_retry_em —— 用修改后的参数重试能量最小化
7. tools_retry_eq —— 用修改后的参数重试 NPT 平衡
8. tools_retry_prod —— 重试生产运行
9. tools_configure_outputs_simulation —— 在 GROMACS 启动前配置可选 .trr 输出
10. tools_configure_prod_simulation —— 在 prod.tpr 生成前重写 prod.mdp
11. tools_diagnose_error_simulation —— 分析 GROMACS 日志/输出以识别具体失败模式
12. tools_modify_config_simulation —— 在 config.json 中更新 MD 参数
13. tools_migrate_md_config_simulation —— 显式迁移旧 eq_ns/prod_ns；只有在用户确认工作流授予服务端授权后才可采用 v2

## 决策规则

### 能量最小化（EM）
1. EM_NOT_CONVERGED：结构有不良接触。选项：(a) 增大 Packmol tolerance，(b) 增大 emtol，(c) 增加 nsteps。最多 3 次重试。
2. grompp 在 EM 前失败：检查 atomtype 不匹配、缺失 itp 文件或 .mdp 语法错误。修正配置并重试。

### 平衡（EQ）
1. EQUILIBRATION_FAILED：读取最终目标温度保持段的分块统计；不得把整段 EQ 平均当作验收。
2. EQ_NOT_CONVERGED（温度不稳定）：检查恒温器设置；若需调整 tau_t，提出变更并升级到用户确认。
3. 温度爆炸（>1000K）：检查初始盒子中是否有重叠原子；若需降低 dt 或增大 tau_t，提出变更并升级到用户确认。
4. 密度降到接近零：先读取本次建盒记录中的实际盒矢量、体积和初始质量密度；盒子过大时用更高目标质量密度重建盒子。
5. 密度爆炸：先读取本次建盒记录；盒子过小时用更低目标质量密度或更大 box_size 重建盒子。

### 生产（PROD）
1. RECOVERY_CONFLICT：不得人工注入 -cpi/-append；只能由 manifest 指纹一致性决定恢复。若 EQ 未验收或真空区存在，不要进入 PROD。
2. PROD 中崩溃：检查能量中的 NaN/inf 和约束；若需减小 dt，提出变更并升级到用户确认。

### 通用
1. 涉及三点退火的高温/过渡温度/目标温度、时长、压力耦合、时间步或输出精度的变化必须升级到用户；不得尝试传入确认字段或静默修改协议。只有用户确认工作流可授予服务端授权；更改全局协议后会重建受影响下游 MDP 并使对应 manifest 阶段失效。
2. grompp atomtype 错误：记录证据并升级到 Layer 2；本层没有拓扑工具，不得编造跨层调用。
2. sched_affinity 错误（WSL2）：注入 GAUSS_CDEF=0 和 OMP_NUM_THREADS=1。
3. LINCS 警告：减小 dt 或增大 lincs_iter/lincs_order。
4. PME 负载均衡警告：调整 rcoulomb 或网格间距。

## 重试限制
- EM：3 次重试（参数变更），然后重建盒子（1 次重试），然后升级
- EQ：不自动改变退火时长、温度或压力耦合；需要用户确认后才可重建 MDP
- PROD：只在 manifest 指纹一致时恢复；协议变更后不 append
- Box：3 次重试（密度/尺寸/tolerance 变更），然后升级

## 升级协议
```json
{
  "layer": "simulation",
  "step": "eq",
  "error_kind": "eq_not_converged",
  "attempts_made": 3,
  "actions_tried": ["请求确认后调整六段 EQ 时长", "请求确认后调整 eq_tau_p", "用更高数密度 (6.5 分子/nm3) 重建盒子"],
  "last_density": 1.45,
  "last_temperature": 310.2,
  "last_raw_output": "<eq.log 最后 500 字符>",
  "recommendation": "体系可能在此温度下发生相变或相分离。考虑在不同温度或用不同力场运行。",
  "backup_plan": "用 LigParGen OPLS-AA 替代 GAFF 以获得更好的液体密度预测"
}
```"""


class SimulationAgent(LayerAgent):
    """Layer 3: MD 模拟修复 Agent。"""

    def __init__(
        self,
        llm_client,
        max_retries=3,
        on_action=None,
        on_decision: Callable[[Mapping[str, object]], None] | None = None,
        on_config_updated: Callable[[dict, dict, list[str]], None] | None = None,
        model: str = DEFAULT_LLM_MODEL,
        recovery_policy: RecoveryPolicy | None = None,
        tool_catalog: ActionToolCatalog | None = None,
        llm_budget: LLMBudget | None = None,
    ):
        self._work_dir = None
        self._config_path = None
        self._on_config_updated = on_config_updated
        super().__init__(
            name="simulation",
            system_prompt=SIMULATION_AGENT_PROMPT,
            tools=SIMULATION_TOOLS,
            tool_handler=self._handle_tool_call,
            llm_client=llm_client,
            model=model,
            max_retries=max_retries,
            on_action=on_action,
            on_decision=on_decision,
            prompt_version="simulation-agent-v1",
            recovery_policy=recovery_policy,
            tool_catalog=tool_catalog,
            llm_budget=llm_budget,
        )

    def set_workspace(self, work_dir: str, config_path: str) -> None:
        self._work_dir = work_dir
        self._config_path = config_path

    def propose_eq_recovery(
        self,
        step_result: StepResult,
        config_path: str,
    ) -> dict[str, Any]:
        """Ask the model for a bounded EQ repair proposal without tools.

        The response is only a proposal.  It cannot edit configuration, start
        GROMACS, or make a user confirmation claim; the orchestrator validates
        it before persisting a pending action.
        """
        error = step_result.error
        evidence = self._eq_public_evidence(step_result.extra)
        return self._request_eq_recovery_proposal(
            config_path=config_path,
            error_kind=error.kind.value if error else "unknown",
            error_message=error.message if error else "EQ 执行失败",
            evidence=evidence,
        )

    def propose_revised_eq_recovery(
        self,
        *,
        status: Mapping[str, Any],
        current_action: Mapping[str, Any],
        user_request: str,
        config_path: str,
    ) -> dict[str, Any]:
        """Generate a replacement EQ proposal while the run remains parked.

        The caller persists the result only after the same whitelist and
        configuration-fingerprint validation used by an initial EQ proposal.
        This method never edits a run or grants confirmation itself.
        """
        error_kind = str(status.get("error_kind") or "equilibration_failed")[:80]
        error_message = str(status.get("error") or "EQ 验收未通过")[:240]
        request = str(user_request or "").replace("\n", " ").replace("\r", " ").strip()[:600]
        current = {
            key: current_action.get(key)
            for key in ("summary", "restart_step", "adjustments", "editable_parameters")
            if key in current_action
        }
        return self._request_eq_recovery_proposal(
            config_path=config_path,
            error_kind=error_kind,
            error_message=error_message,
            evidence={},
            user_request=request,
            current_action=current,
        )

    def _request_eq_recovery_proposal(
        self,
        *,
        config_path: str,
        error_kind: str,
        error_message: str,
        evidence: Mapping[str, Any],
        user_request: str = "",
        current_action: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Ask for one bounded EQ proposal or complete replacement proposal."""
        config_text = "{}"
        try:
            with open(config_path, encoding="utf-8") as handle:
                config_text = handle.read()[:4000]
        except OSError:
            pass
        revision_context = ""
        if user_request:
            revision_context = (
                "当前方案尚未确认，用户提出了替代要求。请生成完整的替换方案；"
                "不能保留或应用旧方案中的任何隐式修改。\n"
                f"当前方案：{json.dumps(current_action or {}, ensure_ascii=False)}\n"
                f"用户要求：{user_request}\n"
            )
        prompt = (
            "EQ 阶段已经失败。请阅读错误、受限的验收证据和运行配置，给出一个等待用户确认的"
            "修复方案。禁止调用工具、禁止声称已修改配置、禁止开始重跑。只返回 JSON 对象，不要 Markdown。\n"
            "允许的 field 仅为 dt、tau_t、eq_tau_p、lincs_iter、lincs_order、box_density，以及"
            "eq_segment.heat、eq_segment.hold_high、eq_segment.cool_transition、"
            "eq_segment.hold_transition、eq_segment.cool_target、eq_segment.hold_target。\n"
            "同时返回 editable_fields，列出用户可要求重新评估的字段；每项格式为"
            "{\"field\":\"tau_t\",\"purpose\":\"简短原因\"}。"
            "格式：{\"summary\":\"简短中文摘要\",\"adjustments\":[{\"field\":\"dt\","
            "\"after\":0.0005,\"purpose\":\"简短目的\"}],"
            "\"editable_fields\":[{\"field\":\"tau_t\",\"purpose\":\"简短原因\"}]}。"
            "只提出确有必要的改动，并只列允许的 field。\n\n"
            f"{revision_context}"
            f"错误类型：{error_kind}\n"
            f"错误摘要：{error_message}\n"
            f"验收证据：{json.dumps(evidence, ensure_ascii=False)}\n"
            f"运行配置：{config_text}"
        )
        try:
            response = self.llm.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "你是 MD 协议诊断顾问，只能生成可审阅的 JSON 方案。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0,
                max_tokens=500,
            )
            content = response.choices[0].message.content or ""
        except Exception:
            return {}
        return self._parse_eq_proposal(content)

    @staticmethod
    def _parse_eq_proposal(content: object) -> dict[str, Any]:
        """Accept only the small JSON vocabulary required by the action gate."""
        text = str(content or "").strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else ""
            if text.rstrip().endswith("```"):
                text = text.rstrip()[:-3].rstrip()
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return {}
        if not isinstance(payload, Mapping):
            return {}
        summary = str(payload.get("summary") or "").replace("\n", " ").strip()[:240]
        adjustments = payload.get("adjustments")
        if not isinstance(adjustments, list):
            adjustments = []
        safe_adjustments = []
        for adjustment in adjustments[:8]:
            if not isinstance(adjustment, Mapping):
                continue
            field = adjustment.get("field")
            if not isinstance(field, str) or len(field) > 64:
                continue
            purpose = str(adjustment.get("purpose") or "").replace("\n", " ").strip()[:120]
            safe_adjustments.append({
                "field": field,
                "after": adjustment.get("after"),
                "purpose": purpose,
            })
        editable = payload.get("editable_fields", payload.get("modifiable_fields", []))
        if not isinstance(editable, list):
            editable = []
        safe_editable = []
        for item in editable[:8]:
            if isinstance(item, Mapping):
                field = item.get("field")
                purpose = item.get("purpose") or item.get("reason")
            else:
                field, purpose = item, ""
            if not isinstance(field, str) or len(field) > 64:
                continue
            safe_editable.append({
                "field": field,
                "purpose": str(purpose or "").replace("\n", " ").strip()[:120],
            })
        return {
            "summary": summary,
            "adjustments": safe_adjustments,
            "editable_fields": safe_editable,
        }

    @staticmethod
    def _eq_public_evidence(extra: Mapping[str, Any] | object) -> dict[str, Any]:
        """Extract compact numerical facts without forwarding logs to the UI model."""
        if not isinstance(extra, Mapping):
            return {}
        result = extra.get("eq_result")
        details = getattr(result, "details", None)
        if not isinstance(details, Mapping):
            return {}
        series = details.get("series")
        if not isinstance(series, Mapping):
            return {}
        evidence: dict[str, Any] = {}
        for name in ("temperature", "density", "pressure", "potential"):
            values = series.get(name)
            if not isinstance(values, Mapping):
                continue
            entry = {
                key: values[key]
                for key in ("mean", "relative_drift", "trend_zscore", "ok")
                if key in values and isinstance(values[key], (int, float, bool))
            }
            if entry:
                evidence[name] = entry
        vacuum = details.get("vacuum")
        if isinstance(vacuum, Mapping) and isinstance(vacuum.get("detected"), bool):
            evidence["vacuum_detected"] = vacuum["detected"]
        return evidence

    def _handle_tool_call(self, tool_name: str, args: dict) -> str:
        requested_fields = protocol_change_request(tool_name, args)
        if requested_fields:
            result = StepResult(
                step_name="protocol_confirmation",
                step_index=0,
                success=False,
                error=StepError(
                    kind=ErrorKind.USER_CONFIRMATION_REQUIRED,
                    message="模拟协议变更需要用户确认；自动修复未写入配置。",
                ),
                extra={
                    "requires_user_confirmation": True,
                    "protocol_fields": list(requested_fields),
                },
            )
            return json.dumps(result.to_dict(), ensure_ascii=False)
        return handle_simulation_tool_call(
            tool_name,
            args,
            work_dir=self._work_dir,
            config_path=self._config_path,
            on_config_updated=self._on_config_updated,
        )
