"""
agent_topology.py — Layer 2: Topology Agent.

力场参数化失败时的诊断与修复 Agent。
覆盖 Sobtop（GAFF）/ LigParGen（OPLS-AA）、拓扑组装和 itp 修订。
"""

from willy.errors import ErrorKind, StepError, StepResult
from willy.layer_agent import Escalation, LayerAgent
from willy.llm_config import DEFAULT_LLM_MODEL
from willy.toolist_topology import TOPOLOGY_TOOLS, handle_topology_tool_call
from willy.action_contract import ActionToolCatalog
from willy.recovery_policy import RecoveryPolicy
from willy.llm_budget import LLMBudget

TOPOLOGY_AGENT_PROMPT = """你是 Willy Topology Agent。你处理当前运行的拓扑参数化、主拓扑组装和 ITP 修订。

## 角色
- 你接收一个表示失败的 StepResult 和当前的 config.json。
- 你诊断错误、决定纠正措施并重试该步骤。
- 当前运行的拓扑 manifest（统一 run_manifest 的 topology section 或兼容的 topology_manifest.json）是唯一输入/产物清单；不得猜测或拼接路径。
- Sobtop 仅支持 GAFF+UFF 组合；OPLS-AA 是 LigParGen/BOSS 的独立整套体系。
- 不支持 AMBER，也不支持 Sobtop 与 OPLS-AA 在同一运行内混用。
- 拓扑生成最多 4 次重试。
- **只能使用下方列出的工具。不存在 bash/shell/命令行工具，禁止编造或调用不存在的工具。**

## 可用工具
1. tools_retry_topo_gaff —— 仅为 manifest 中的 Sobtop 分子重试
2. tools_retry_topo_opls —— 仅为 manifest 中的 OPLS-AA 分子重试
3. tools_retry_top_assembly —— 重试 top_assembly 拓扑组装
4. tools_diagnose_error_topology —— 分析 Sobtop/LigParGen/top_assembly 输出以识别根本原因
5. tools_modify_config_topology —— 只修改当前 run 的合法配置快照；后端切换必须派生新 run。
6. tools_skip_molecule_topology —— 当前禁用，不能用于继续流水线。

## 决策规则
1. SOBTOP_FAILED：只使用 tools_retry_topo_gaff 重试当前 manifest 的同一 Sobtop 组件。缺 .mol2/.chg 时返回上游修复，不切换后端。
2. rc=24 只有 ITP/GRO 已完整且原子数通过校验时才视为成功；否则仍为失败。
3. OPLS-AA 失败只能用 tools_retry_topo_opls 重试同一 OPLS-AA 组件，不回退到 Sobtop。
4. 建议 OPLS-AA 替代 Sobtop 时，只能建议用户派生一个新的 OPLS-AA 运行，禁止修改当前 manifest 或混用产物。
5. 检测到 atomtype 参数冲突或跨 forcefield_family：不要继续，直接升级。
6. skip_molecule 已禁用；不要宣称下游会自动排除该分子。

## 重试限制
- 每个分子：只在其当前后端内最多 2 次重试
- top_assembly 组装：2 次重试
- 拓扑层总计：4 次

## 升级协议
```json
{
  "layer": "topology",
  "molecule": "TFSI",
  "error_kind": "sobtop_failed",
  "attempts_made": 3,
  "actions_tried": ["用 manifest 指定的 mol2+chg 重试 Sobtop", "请求上游重新生成 chg"],
  "last_raw_output": "<Sobtop stderr 最后 500 字符>",
  "recommendation": ".mol2 文件可能有错误的键连接性。再次运行 Layer 1 mol2 转换，或手动检查 mol2 文件。",
  "backup_plan": "派生一个独立的 OPLS-AA 运行，不能与当前 Sobtop 运行混用"
}
```"""


class TopologyAgent(LayerAgent):
    """Layer 2: 力场拓扑修复 Agent。"""

    def __init__(self, llm_client, max_retries=4, on_action=None, on_decision=None, model: str = DEFAULT_LLM_MODEL,
                 recovery_policy: RecoveryPolicy | None = None, tool_catalog: ActionToolCatalog | None = None,
                 llm_budget: LLMBudget | None = None):
        self._work_dir = None
        self._config_path = None
        super().__init__(
            name="topology",
            system_prompt=TOPOLOGY_AGENT_PROMPT,
            tools=TOPOLOGY_TOOLS,
            tool_handler=self._handle_tool_call,
            llm_client=llm_client,
            model=model,
            max_retries=max_retries,
            on_action=on_action,
            on_decision=on_decision,
            prompt_version="topology-agent-v1",
            recovery_policy=recovery_policy,
            tool_catalog=tool_catalog,
            llm_budget=llm_budget,
        )

    def set_workspace(self, work_dir: str, config_path: str) -> None:
        self._work_dir = work_dir
        self._config_path = config_path

    def _handle_tool_call(self, tool_name: str, args: dict) -> str:
        return handle_topology_tool_call(
            tool_name,
            args,
            work_dir=self._work_dir,
            config_path=self._config_path,
        )

    def handle_failure(self, step_result: StepResult, *args, **kwargs) -> StepResult:
        """Do not ask the LLM to repair an unsafe cross-family atomtype clash."""
        if step_result.error and step_result.error.kind is ErrorKind.ATOMTYPE_CONFLICT:
            escalation = Escalation(
                layer="topology",
                step_name=step_result.step_name,
                error_kind=ErrorKind.ATOMTYPE_CONFLICT.value,
                attempts_made=0,
                actions_tried=["停止：检测到同名 atomtype 参数冲突"],
                last_raw_output=step_result.error.raw_output,
                recommendation="当前 run 混入了不兼容的拓扑参数；请检查 manifest 并使用单一 forcefield_family 重新运行。",
                backup_plan="将需要 OPLS-AA 的体系派生为独立 OPLS-AA run，不能与 Sobtop GAFF+UFF 产物混用。",
            )
            return StepResult(
                step_name=step_result.step_name,
                step_index=step_result.step_index,
                success=False,
                error=StepError(
                    ErrorKind.ATOMTYPE_CONFLICT,
                    step_result.error.message,
                    raw_output=step_result.error.raw_output,
                    hint=escalation.recommendation,
                ),
                outputs=step_result.outputs,
                artifacts=step_result.artifacts,
                duration_s=step_result.duration_s,
                escalated=True,
                extra={"escalation": escalation.to_dict()},
            )
        return super().handle_failure(step_result, *args, **kwargs)
