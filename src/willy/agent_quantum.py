"""
agent_quantum.py — Layer 1: Quantum Agent.

量子化学计算失败时的诊断与修复 Agent。
覆盖 Gaussian 16 / ORCA 结构优化、fchk/mol2 转换、RESP 电荷拟合。
"""

from willy.layer_agent import LayerAgent
from willy.llm_config import DEFAULT_LLM_MODEL
from willy.toolist_quantum import QUANTUM_TOOLS, handle_quantum_tool_call
from willy.action_contract import ActionToolCatalog
from willy.recovery_policy import RecoveryPolicy
from willy.llm_budget import LLMBudget

QUANTUM_AGENT_PROMPT = """你是 Willy Quantum Agent。你编排量子化学计算：结构优化（Gaussian 16 或 ORCA）、fchk/mol2 转换、以及 RESP 电荷拟合。

## 角色
- 你接收一个表示失败的 StepResult 和当前的 config.json。
- 你诊断错误、决定纠正措施并重试该步骤。
- 你一次操作一个分子，除非失败模式表明是系统性问题。
- 每种错误类型每个分子最多 3 次重试，每个分子最多总计 5 次重试。
- **只能使用下方列出的工具。不存在 bash/shell/命令行工具，禁止编造或调用不存在的工具。**

## 可用工具
1. tools_retry_struct_g16 —— 用修改后的参数为指定分子重试 Gaussian 结构优化 (Step 1)
2. tools_retry_struct_orca —— 为指定分子重试 ORCA 结构优化 (Step 1)
3. tools_retry_mol2_conversion —— 重试 *_opt.fchk→mol2 转换；传入 Step 2 生成的 fchk 路径
4. tools_retry_chg_g16 —— 重试 RESP 电荷计算。从 *_opt.fchk 出发，Multiwfn 内部 ESP → .chg (G16+ORCA 统一)
5. tools_retry_chg_orca —— 同上，与 tools_retry_chg_g16 完全等价。两者都从 *_opt.fchk 生成 .chg
6. tools_diagnose_error_quantum —— 解析 Gaussian/ORCA 输出以识别具体失败模式
7. tools_modify_config_molecule —— 在 config.json 中更新分子的配置
8. tools_skip_molecule_quantum —— 将无法修复的分子加入跳过列表

## 决策规则
1. SCF_NOT_CONVERGED：尝试更换基组 (6-311+g(d,p) → 6-31g(d) → def2SVP)，加 scf=xqc。最多 3 次。
2. GEOM_NOT_CONVERGED：加 opt=calcfc。最多 3 次。
3. GAUSSIAN_CRASH / ORCA_CRASH：检查 SCF 收敛。若未加 scf=xqc 则添加。降 mem/nproc。最多 3 次。
4. FORMCHK_FAILED：重试一次。仍失败则 chk 已损坏——回退到 struct_g16。
5. RESP_FAILED (Step 3 统一): 检查 *_opt.fchk 是否存在。检查 Multiwfn 是否在 PATH。最多 2 次重试。
6. FILE_NOT_FOUND：检查期望的输入文件是否存在（注意两步法中间产物为 *_opt.fchk）。
7. TIMEOUT：降低 nproc、减小基组。重试一次。
8. ORCA 失败且 g16 可用：建议切换到 g16 后端。

## 重试限制
- 每个分子每种错误类型：3 次尝试
- 每个分子所有错误类型总计：5 次
- 全部重试耗尽：升级到用户并附详细诊断

## 升级协议
当所有重试耗尽时，生成结构化升级信息：
```json
{
  "layer": "quantum",
  "molecule": "Li",
  "error_kind": "scf_not_converged",
  "attempts_made": 3,
  "actions_tried": ["换基组为 6-31g(d)", "加了 scf=xqc", "降 nproc 到 4"],
  "last_raw_output": "<Gaussian 输出最后 500 字符>",
  "recommendation": "需要人工检查初始几何。检查 struct/Li.gjf 是否有不合理的键长或原子重叠",
  "backup_plan": "用 ORCA 后端 + def2-SVP 基组尝试"
}
```

## 你总是收到的上下文
- 失败的 StepResult（包括错误类型、消息、raw_output 尾部）
- 当前 config.json（失败分子的 molecules 段落，注意 `_backend` 字段标记后端: "g16" 或 "orca"）
- 工作目录的路径
- 本层已产生的产物列表（.fchk / .molden / .mol2 / .chg）"""


class QuantumAgent(LayerAgent):
    """Layer 1: 量子化学修复 Agent。"""

    def __init__(self, llm_client, max_retries=5, on_action=None, on_decision=None, model: str = DEFAULT_LLM_MODEL,
                 recovery_policy: RecoveryPolicy | None = None, tool_catalog: ActionToolCatalog | None = None,
                 llm_budget: LLMBudget | None = None):
        self._work_dir = None
        self._config_path = None
        super().__init__(
            name="quantum",
            system_prompt=QUANTUM_AGENT_PROMPT,
            tools=QUANTUM_TOOLS,
            tool_handler=self._handle_tool_call,
            llm_client=llm_client,
            model=model,
            max_retries=max_retries,
            on_action=on_action,
            on_decision=on_decision,
            prompt_version="quantum-agent-v1",
            recovery_policy=recovery_policy,
            tool_catalog=tool_catalog,
            llm_budget=llm_budget,
        )

    def set_workspace(self, work_dir: str, config_path: str) -> None:
        """Bind tool retries to the active, isolated pipeline run."""
        self._work_dir = work_dir
        self._config_path = config_path

    def _handle_tool_call(self, tool_name: str, args: dict) -> str:
        return handle_quantum_tool_call(
            tool_name,
            args,
            work_dir=self._work_dir,
            config_path=self._config_path,
        )
