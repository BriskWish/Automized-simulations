"""
agent_simulation.py — Layer 3: Simulation Agent.

GROMACS MD 模拟失败时的诊断与修复 Agent。
覆盖 MDP 生成、Packmol 盒子构建、EM/三点式退火 EQ/PROD 执行。
"""

from willy.layer_agent import LayerAgent
from willy.llm_config import DEFAULT_LLM_MODEL
from willy.errors import ErrorKind, StepError, StepResult
from willy.toolist_simulation import (
    MDRUN_KNOWLEDGE_TOOL,
    SIMULATION_TOOLS,
    handle_simulation_tool_call,
    protocol_change_request,
)
from willy.simulation.mdrun_knowledge import lookup_mdrun_knowledge, public_entry_index
from willy.action_contract import ActionToolCatalog
from willy.recovery_policy import RecoveryPolicy
from willy.llm_budget import LLMBudget
from willy.step_registry import PACKMOL_STEP
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
2. tools_run_eq_simulation —— 仅消费 manifest 中已验收的 EM；以最终 298 K 保温段的温度均值和势能线性斜率验收，压力、密度和真空区仅记录为诊断证据
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
14. tools_lookup_mdrun_knowledge —— 只读读取 GROMACS mdrun 知识条目；必须用索引中的 number + name，单次最多 3 条

## 决策规则

### 能量最小化（EM）
1. EM_NOT_CONVERGED：结构有不良接触。选项：(a) 增大 Packmol tolerance，(b) 增大 emtol，(c) 增加 nsteps。最多 3 次重试。
2. grompp 在 EM 前失败：检查 atomtype 不匹配、缺失 itp 文件或 .mdp 语法错误。修正配置并重试。

### 平衡（EQ）
1. EQUILIBRATION_FAILED：读取最终目标温度保持段的温度均值与势能线性斜率；不得把整段 EQ 平均当作验收。
2. EQ_NOT_CONVERGED（温度不稳定）：检查恒温器设置；若需调整 tau_t，提出变更并升级到用户确认。
3. 温度爆炸（>1000K）：检查初始盒子中是否有重叠原子；若需降低 dt 或增大 tau_t，提出变更并升级到用户确认。
4. 密度降到接近零：先读取本次建盒记录中的实际盒矢量、体积和初始质量密度；盒子过大时用更高目标质量密度重建盒子。
5. 密度爆炸：先读取本次建盒记录；盒子过小时用更低目标质量密度或更大 box_size 重建盒子。

### Packmol 建盒
1. 先读取失败步骤提供的“私有执行证据”。`failure_stage=preflight` 表示
   Packmol 尚未启动，必须按 `preflight_issues` 指出具体缺失或不一致项，不能把
   它描述为 Packmol 进程崩溃，也不能仅凭“input_contract”泛化重建盒子。
2. `failure_stage=process_start|process_exit|timeout|output_missing` 表示执行层
   或输出层问题：先报告实际阶段和返回码/输出存在性，只有用户明确要求改变盒子
   参数时才提出 `tools_retry_box`；不得把它改写成拓扑或 GROMACS 错误。
3. 仅当证据明确为 `atom_count|periodic_cell_parse|periodic_cell_mismatch`，或
   用户明确要求重建盒子时，才可选择 `tools_retry_box`。重试参数必须说明依据，
   不得凭空改变密度、box_size 或 tolerance。

### 生产（PROD）
1. RECOVERY_CONFLICT：不得人工注入 -cpi/-append；只能由 manifest 指纹一致性决定恢复。若 EQ 未验收，不要进入 PROD。
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
            prompt_version="simulation-agent-v2",
            recovery_policy=recovery_policy,
            tool_catalog=tool_catalog,
            llm_budget=llm_budget,
        )

    def set_workspace(self, work_dir: str, config_path: str) -> None:
        self._work_dir = work_dir
        self._config_path = config_path

    def _tool_request_rejection(
        self,
        tool_name: str,
        args: Mapping[str, object],
        step_result: StepResult,
    ) -> str | None:
        """Require geometry evidence before automatic retries of failed Step 7."""
        if tool_name != "tools_retry_box" or step_result.step_index != PACKMOL_STEP:
            return None
        execution = step_result.extra.get("box_execution")
        failure_stage = execution.get("failure_stage") if isinstance(execution, Mapping) else None
        allowed_stages = {
            "atom_count",
            "periodic_cell_parse",
            "periodic_cell_mismatch",
        }
        if failure_stage in allowed_stages:
            return None
        observed = str(failure_stage or "missing")
        return (
            "自动 Packmol 重试缺少明确几何证据："
            f"failure_stage={observed}；仅 atom_count、periodic_cell_parse 或 "
            "periodic_cell_mismatch 可自动提出重建盒子"
        )

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
        # An unclassified engine failure is not evidence for a numerical or
        # protocol adjustment.  Do not turn the conservative fallback into a
        # plausible-sounding guess; the orchestrator will record this as an
        # approval-required research request instead of a runnable action.
        if error is None or error.kind is ErrorKind.UNKNOWN:
            return {
                "problem": "EQ 阶段出现未分类错误，现有受限证据不足以确定可执行恢复路径。",
                "evidence": [
                    "公开错误类别为 unknown，未匹配到受控恢复规则。",
                    "当前运行配置保持冻结，尚未写入任何参数调整。",
                ],
                "unknown_error": True,
                "research_request": {
                    "status": "approval_required",
                    "reason": "需要以公开错误类别、阶段和已验收证据为边界检索权威资料。",
                    "query": "GROMACS EQ unknown failure recovery",
                },
            }
        proposal = self._request_eq_recovery_proposal(
            config_path=config_path,
            error_kind=error.kind.value if error else "unknown",
            error_message=error.message if error else "EQ 执行失败",
            evidence=evidence,
        )
        if "_knowledge_lookup_status" not in proposal:
            proposal["_knowledge_lookup_status"] = "unavailable"
        return proposal

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
        proposal = self._request_eq_recovery_proposal(
            config_path=config_path,
            error_kind=error_kind,
            error_message=error_message,
            evidence={},
            user_request=request,
            current_action=current,
        )
        if "_knowledge_lookup_status" not in proposal:
            proposal["_knowledge_lookup_status"] = "unavailable"
        return proposal

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
        """Ask for bounded EQ proposal(s) or a complete replacement bundle."""
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
        try:
            knowledge_index = public_entry_index()
        except Exception:
            knowledge_index = []
        hypotheses = self._eq_agent_hypotheses(error_kind, evidence)
        prompt = (
            "EQ 阶段已经失败。请阅读错误、受限的验收证据和运行配置。若证据支持多个可能原因，"
            "请给出 2 至 3 个相互独立、互斥的候选方案；每个方案必须包含可能原因、证据摘要和对应修改。"
            "若只有一个原因则只给一个方案。所有方案都必须等待用户选择和明确确认，不能默认选择。"
            "你可以调用下方唯一的只读知识工具，但不能调用任何执行、配置或诊断工具。"
            "知识工具每次最多读取 3 条，本次实际失败诊断最多调用 2 次；超过预算后必须继续生成方案，"
            "并将未验证推断明确标出。只返回 JSON 对象，不要 Markdown。\n"
            "允许的 field 仅为 dt、tau_t、eq_tau_p、lincs_iter、lincs_order、box_density，以及"
            "eq_segment.heat、eq_segment.hold_high、eq_segment.cool_transition、"
            "eq_segment.hold_transition、eq_segment.cool_target、eq_segment.hold_target。\n"
            "用户所说的 tau_p 在此只能表示 EQ 压浴，输出时必须写为 eq_tau_p；"
            "用户所说的 hold/最终保温段，输出时必须写为 eq_segment.hold_target。"
            "同时返回 editable_fields，列出用户可要求重新评估的字段；每项格式为"
            "{\"field\":\"tau_t\",\"purpose\":\"简短原因\"}。"
            "单方案格式：{\"summary\":\"简短中文摘要\",\"adjustments\":[{\"field\":\"dt\","
            "\"after\":0.0005,\"purpose\":\"简短目的\"}],"
            "\"editable_fields\":[{\"field\":\"tau_t\",\"purpose\":\"简短原因\"}]}。"
            "多方案格式：{\"problem_summary\":\"多因素问题摘要\",\"options\":["
            "{\"title\":\"方案一：...\",\"cause\":\"可能原因\",\"evidence\":\"证据摘要\","
            "\"summary\":\"该方案摘要\",\"adjustments\":[...],\"editable_fields\":[...],"
            "\"knowledge_entries\":[{\"number\":10,\"name\":\"索引中的完整名称\"}],"
            "\"knowledge_status\":\"retrieved|not_matched|unavailable\","
            "\"advice_source\":\"knowledge_base|llm_unverified\","
            "\"compatibility_notice\":\"版本兼容提醒\"}]}。"
            "最多 3 个方案；每个方案的 adjustments 必须自洽，不能把不同方案的参数混在一起。"
            "knowledge_entries 只能引用工具实际返回的条目；不得把未读取的条目写成已命中。"
            "若工具未命中或不可用，knowledge_status 必须不是 retrieved，advice_source 必须为 llm_unverified。"
            "只提出确有必要的改动，并只列允许的 field。\n\n"
            "## 强制返回契约\n"
            "必须返回一个 JSON 对象，并且必须同时含有 problem、evidence、current_step_retry、"
            "upstream_retry 四个字段。evidence 必须是 2 至 6 条简短、可由本次公开错误、验收统计、"
            "已读取知识条目或冻结配置直接支持的字符串；不能把假设写成证据。\n"
            "current_step_retry 表示从第 9 步 EQ 重新验收，必须包含 applicable、summary、"
            "adjustments、evidence；其中 adjustments 只能使用允许 field。\n"
            "upstream_retry 表示仅在现有证据支持时打回第 7 步 Packmol 后重跑，必须包含 applicable、"
            "summary、adjustments、evidence。若不适用，applicable 必须为 false，adjustments 为空，"
            "并在 summary 和 evidence 说明当前没有支持回退的证据；不得凑出参数修改。\n"
            "每条方案证据必须对应本方案，且不能声称已执行、已确认或已联网。EQ 当前步调参和打回前序"
            "流程都属于高风险科学协议变更：只能供人工审核，不能建议自动重跑。\n"
            "未知错误或本地知识无法支持任一方案时，返回 unknown_error=true 和 "
            "research_request={\"status\":\"approval_required\",\"reason\":\"...\",\"query\":\"...\"}；"
            "此时两个方案都应 applicable=false 且 adjustments 为空。你不能访问互联网，也不能编造检索"
            "结论。只有未来经批准的外部检索结论能够明确落入上述两种方案，并再次经过相同白名单校验时，"
            "才可以替换待确认方案。\n"
            "推荐格式：{\"problem\":\"问题摘要\",\"evidence\":[\"证据一\"],"
            "\"current_step_retry\":{\"applicable\":true,\"summary\":\"...\","
            "\"adjustments\":[{\"field\":\"dt\",\"after\":0.0005,\"purpose\":\"...\"}],"
            "\"evidence\":[\"...\"]},\"upstream_retry\":{\"applicable\":false,"
            "\"summary\":\"当前证据不支持打回前序流程\",\"adjustments\":[],"
            "\"evidence\":[\"...\"]},\"editable_fields\":[...]}。\n\n"
            f"{revision_context}"
            f"错误类型：{error_kind}\n"
            f"阶段：eq\n"
            f"错误摘要：{error_message}\n"
            f"验收证据：{json.dumps(evidence, ensure_ascii=False)}\n"
            f"Agent 未验证假设（仅供你判断，不是知识库结论）：{json.dumps(hypotheses, ensure_ascii=False)}\n"
            f"可检索条目索引（只能按 number + name 请求）：{json.dumps(knowledge_index, ensure_ascii=False)}\n"
            f"运行配置：{config_text}"
        )
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": "你是 MD 协议诊断顾问，只能生成可审阅的 JSON 方案。"},
            {"role": "user", "content": prompt},
        ]
        retrieved: dict[int, dict[str, Any]] = {}
        lookup_attempted = False
        lookup_calls = 0
        content = ""
        # A model may need one response after its final tool result to emit JSON.
        for _round in range(4):
            try:
                if self.llm_budget is not None:
                    self.llm_budget.before_call()
                kwargs = {
                    "model": self.model,
                    "messages": messages,
                    "tools": [MDRUN_KNOWLEDGE_TOOL],
                    "tool_choice": "auto",
                    "temperature": 0,
                    "max_tokens": 900,
                }
                if self.llm_budget is not None:
                    kwargs["timeout"] = self.llm_budget.call_timeout_s
                response = self.llm.chat.completions.create(**kwargs)
                if self.llm_budget is not None:
                    self.llm_budget.record_success()
            except Exception:
                return {}
            message = response.choices[0].message
            tool_calls = getattr(message, "tool_calls", None) or []
            if not tool_calls:
                content = getattr(message, "content", None) or ""
                break
            assistant_calls: list[dict[str, Any]] = []
            for tool_call in tool_calls:
                function = getattr(tool_call, "function", None)
                if function is None and isinstance(tool_call, Mapping):
                    function = tool_call.get("function", {})
                name = getattr(function, "name", None) or (function.get("name") if isinstance(function, Mapping) else "")
                arguments = getattr(function, "arguments", None) or (function.get("arguments", "{}") if isinstance(function, Mapping) else "{}")
                call_id = getattr(tool_call, "id", None) or (tool_call.get("id", "kb-call") if isinstance(tool_call, Mapping) else "kb-call")
                assistant_calls.append({
                    "id": str(call_id),
                    "type": "function",
                    "function": {"name": str(name), "arguments": str(arguments)},
                })
            messages.append({"role": "assistant", "content": getattr(message, "content", None) or "", "tool_calls": assistant_calls})
            for tool_call in tool_calls:
                function = getattr(tool_call, "function", None)
                if function is None and isinstance(tool_call, Mapping):
                    function = tool_call.get("function", {})
                name = getattr(function, "name", None) or (function.get("name") if isinstance(function, Mapping) else "")
                arguments = getattr(function, "arguments", None) or (function.get("arguments", "{}") if isinstance(function, Mapping) else "{}")
                call_id = getattr(tool_call, "id", None) or (tool_call.get("id", "kb-call") if isinstance(tool_call, Mapping) else "kb-call")
                if name != "tools_lookup_mdrun_knowledge":
                    result = {"ok": False, "lookup_status": "unavailable", "entries": [], "errors": ["proposal 阶段只允许知识库只读工具"]}
                elif lookup_calls >= 2:
                    result = {"ok": False, "lookup_status": "budget_exhausted", "entries": [], "errors": ["本次失败诊断最多读取 2 次知识库"]}
                else:
                    lookup_calls += 1
                    lookup_attempted = True
                    try:
                        parsed_args = json.loads(str(arguments))
                    except json.JSONDecodeError:
                        parsed_args = {}
                    result = lookup_mdrun_knowledge(parsed_args.get("entries") if isinstance(parsed_args, Mapping) else None)
                    for entry in result.get("entries", []) if isinstance(result, Mapping) else []:
                        if isinstance(entry, Mapping) and isinstance(entry.get("number"), int):
                            retrieved[int(entry["number"])] = dict(entry)
                messages.append({"role": "tool", "tool_call_id": str(call_id), "content": json.dumps(result, ensure_ascii=False)})
        proposal = self._parse_eq_proposal(content)
        if not proposal:
            return {}
        proposal["_knowledge_lookup_attempted"] = lookup_attempted
        proposal["_knowledge_lookup_status"] = "retrieved" if retrieved else ("not_matched" if lookup_attempted else "unavailable")
        proposal["_retrieved_entries"] = list(retrieved.values())[:6]
        return proposal

    @staticmethod
    def _parse_eq_proposal(content: object) -> dict[str, Any]:
        """Accept one legacy proposal or up to three bounded diagnostic options."""
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
        def safe_points(value: object) -> list[str]:
            if not isinstance(value, list):
                return []
            points: list[str] = []
            for item in value[:6]:
                text = str(item or "").replace("\n", " ").replace("\r", " ").strip()[:240]
                if text and text not in points:
                    points.append(text)
            return points

        def parse_option(raw: object, *, plan_kind: str = "current_step_retry") -> dict[str, Any]:
            if not isinstance(raw, Mapping):
                return {}
            adjustments = []
            raw_adjustments = raw.get("adjustments")
            for adjustment in raw_adjustments[:8] if isinstance(raw_adjustments, list) else []:
                if not isinstance(adjustment, Mapping):
                    continue
                field = adjustment.get("field")
                if not isinstance(field, str) or len(field) > 64:
                    continue
                adjustments.append({
                    "field": field,
                    "after": adjustment.get("after"),
                    "purpose": str(adjustment.get("purpose") or "").replace("\n", " ").strip()[:120],
                })
            editable = raw.get("editable_fields", raw.get("modifiable_fields", []))
            safe_editable = []
            for item in editable[:8] if isinstance(editable, list) else []:
                if isinstance(item, Mapping):
                    field = item.get("field")
                    purpose = item.get("purpose") or item.get("reason")
                else:
                    field, purpose = item, ""
                if isinstance(field, str) and len(field) <= 64:
                    safe_editable.append({
                        "field": field,
                        "purpose": str(purpose or "").replace("\n", " ").strip()[:120],
                    })
            return {
                "plan_kind": plan_kind,
                "title": str(raw.get("title") or "").replace("\n", " ").strip()[:120],
                "cause": str(raw.get("cause") or "").replace("\n", " ").strip()[:240],
                "evidence": str(raw.get("evidence") or "").replace("\n", " ").strip()[:300],
                "evidence_points": safe_points(raw.get("evidence_points", raw.get("evidence"))),
                "summary": str(raw.get("summary") or "").replace("\n", " ").strip()[:240],
                "adjustments": adjustments,
                "editable_fields": safe_editable,
                "knowledge_entries": raw.get("knowledge_entries", []) if isinstance(raw.get("knowledge_entries"), list) else [],
                "knowledge_status": str(raw.get("knowledge_status") or "").strip()[:32],
                "advice_source": str(raw.get("advice_source") or "").strip()[:32],
                "compatibility_notice": str(raw.get("compatibility_notice") or "").replace("\n", " ").strip()[:300],
            }

        def structured_option(key: str, plan_kind: str) -> dict[str, Any]:
            raw = payload.get(key)
            if not isinstance(raw, Mapping) or raw.get("applicable") is not True:
                return {}
            return parse_option(raw, plan_kind=plan_kind)

        structured = [
            structured_option("current_step_retry", "current_step_retry"),
            structured_option("upstream_retry", "upstream_retry"),
        ]
        structured = [item for item in structured if item]
        if structured:
            return {
                "problem": str(payload.get("problem") or payload.get("problem_summary") or "").replace("\n", " ").strip()[:240],
                "evidence": safe_points(payload.get("evidence")),
                "research_request": payload.get("research_request") if isinstance(payload.get("research_request"), Mapping) else {},
                "unknown_error": payload.get("unknown_error") is True,
                "options": structured,
            }

        raw_options = payload.get("options")
        if isinstance(raw_options, list):
            options = [parse_option(item) for item in raw_options[:3]]
            options = [item for item in options if item]
            return {
                "problem": str(payload.get("problem") or payload.get("problem_summary") or payload.get("summary") or "").replace("\n", " ").strip()[:240],
                "evidence": safe_points(payload.get("evidence")),
                "research_request": payload.get("research_request") if isinstance(payload.get("research_request"), Mapping) else {},
                "unknown_error": payload.get("unknown_error") is True,
                "options": options,
            }
        parsed = parse_option(payload)
        if parsed:
            parsed["problem"] = str(payload.get("problem") or payload.get("problem_summary") or payload.get("summary") or "").replace("\n", " ").strip()[:240]
            parsed["evidence_points"] = safe_points(payload.get("evidence")) or parsed["evidence_points"]
            parsed["research_request"] = payload.get("research_request") if isinstance(payload.get("research_request"), Mapping) else {}
            parsed["unknown_error"] = payload.get("unknown_error") is True
        return parsed

    @staticmethod
    def _eq_agent_hypotheses(error_kind: str, evidence: Mapping[str, Any]) -> list[dict[str, str]]:
        """Return deliberately unverified hypotheses for model comparison."""
        hypotheses: list[dict[str, str]] = []
        if evidence.get("vacuum_detected") is True:
            hypotheses.append({"hypothesis": "初始建盒密度或盒体积可能与体系不匹配", "status": "unverified"})
        if error_kind in {"eq_not_converged", "equilibration_failed", "mdrun_failed", "numerical_instability"}:
            hypotheses.append({"hypothesis": "积分步长、约束或温度耦合可能导致数值不稳定", "status": "unverified"})
            hypotheses.append({"hypothesis": "压力耦合响应或初始体积可能导致密度/体积漂移", "status": "unverified"})
        return hypotheses[:3]

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
                for key in (
                    "mean",
                    "linear_slope_per_ps",
                    "relative_slope_per_ns",
                    "ok",
                )
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
