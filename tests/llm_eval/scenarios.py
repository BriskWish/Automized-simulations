"""
scenarios.py —— 18 个受控错误场景定义。

每个场景包含:
  - 注入的 ErrorKind 和 raw_output
  - 预期的 Agent 行为（应诊断、应重试、应使用的工具类别）
  - Mock LLM 响应序列（控制重试次数和最终结果）
  - 评分通过标准
"""

from __future__ import annotations
from dataclasses import dataclass, field
from willy.errors import ErrorKind


# ============================================================
# 数据模型
# ============================================================

@dataclass
class ExpectedBehavior:
    """场景预期行为。"""
    should_retry: bool = True
    should_escalate: bool = False
    max_expected_retries: int = 5          # 重试次数上限
    expected_tool_categories: list[str] = field(default_factory=list)
    # "diagnostic", "retry", "config_modify", "skip_molecule"
    acceptable_actions: list[str] = field(default_factory=list)
    should_not_skip: bool = True           # 不应过早跳过分子
    should_not_modify_config_before_diagnosis: bool = True


@dataclass
class PassCriteria:
    """场景通过标准。"""
    min_total_score: int = 60
    min_diagnosis_score: int = 15          # /30
    min_tool_choice_score: int = 10        # /25
    min_repair_score: int = 10             # /25
    min_escalation_score: int = 5          # /20


@dataclass
class MockLLMResponse:
    """单次 LLM 调用的 mock 响应。"""
    call_index: int
    # 如果 non-None: LLM 返回一段包含 _step_result 的 JSON 文本
    # 由 harness 构造完整消息
    outcome: str                           # "success" | "failure" | "escalate"
    error_kind_override: str | None = None  # 覆盖原始 ErrorKind（测试幻觉检测）
    hint_override: str | None = None        # 覆盖 hint
    raw_output_override: str | None = None  # 覆盖 raw_output


@dataclass
class ErrorScenario:
    """单个受控错误注入场景。"""
    scenario_id: str
    layer: str                             # "quantum" | "topology" | "simulation"
    step_name: str
    step_index: int
    description: str                       # 人类可读的场景描述

    # 注入的错误
    injected_error_kind: ErrorKind
    injected_error_message: str
    injected_raw_output: str = ""
    injected_hint: str = ""

    # Mock LLM 响应序列
    llm_responses: list[MockLLMResponse] = field(default_factory=list)

    # 预期行为
    expected: ExpectedBehavior = field(default_factory=ExpectedBehavior)

    # 通过标准
    pass_criteria: PassCriteria = field(default_factory=PassCriteria)

    # 被跳过的分子名（仅适用于 skip_molecule 场景）
    skip_molecule_name: str = ""

    @property
    def total_llm_calls(self) -> int:
        return len(self.llm_responses)

    @property
    def final_outcome(self) -> str:
        """"repair_success" | "escalated" | "skip" """
        if self.skip_molecule_name:
            return "skip"
        last = self.llm_responses[-1] if self.llm_responses else None
        if last and last.outcome == "escalate":
            return "escalated"
        if any(r.outcome == "success" for r in self.llm_responses):
            return "repair_success"
        return "escalated"


# ============================================================
# 场景工厂辅助函数
# ============================================================

def _failure_resp(call_index: int, error_kind: str, message: str = "",
                  hint: str = "", raw: str = "") -> MockLLMResponse:
    """构造失败响应。"""
    return MockLLMResponse(
        call_index=call_index, outcome="failure",
        error_kind_override=error_kind, hint_override=hint,
        raw_output_override=raw,
    )


def _success_resp(call_index: int) -> MockLLMResponse:
    """构造成功响应。"""
    return MockLLMResponse(call_index=call_index, outcome="success")


def _escalate_resp(call_index: int) -> MockLLMResponse:
    """构造升级响应。"""
    return MockLLMResponse(call_index=call_index, outcome="escalate")


# ============================================================
# 场景定义
# ============================================================

SCENARIOS: list[ErrorScenario] = []


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Layer 1: Quantum Agent  (6 场景)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SCENARIOS.append(ErrorScenario(
    scenario_id="q_scf_001",
    layer="quantum", step_name="struct_maker", step_index=1,
    description="SCF 不收敛 → 重试2次 → 第2次添加 scf=xqc 后成功",
    injected_error_kind=ErrorKind.SCF_NOT_CONVERGED,
    injected_error_message="SCF 在 128 次迭代后未收敛于 LiTFSI",
    injected_raw_output="Cycle 128: E= -1234.5678 deltaE= 0.0012\nError termination via Lnk1e",
    injected_hint="",
    llm_responses=[
        _failure_resp(0, "scf_not_converged",
                       message="SCF 仍未收敛（第1次重试：添加 scf=xqc）",
                       hint="尝试更换基组"),
        _success_resp(1),
    ],
    expected=ExpectedBehavior(
        should_retry=True, should_escalate=False,
        max_expected_retries=2,
        expected_tool_categories=["diagnostic", "retry"],
        acceptable_actions=["添加 scf=xqc", "更换基组"],
        should_not_skip=True,
    ),
    pass_criteria=PassCriteria(min_total_score=60),
))

SCENARIOS.append(ErrorScenario(
    scenario_id="q_crash_002",
    layer="quantum", step_name="struct_maker", step_index=1,
    description="Gaussian segfault 崩溃 → 5次重试无果 → 升级",
    injected_error_kind=ErrorKind.GAUSSIAN_CRASH,
    injected_error_message="g16 段错误崩溃于 LiTFSI",
    injected_raw_output="segmentation fault\nError termination via Lnk1e",
    llm_responses=[
        _failure_resp(i, "gaussian_crash",
                       message=f"Gaussian 第{i+1}次重试仍然崩溃",
                       hint="减少内存/更换基组/检查输入结构")
        for i in range(5)
    ] + [_escalate_resp(5)],
    expected=ExpectedBehavior(
        should_retry=True, should_escalate=True,
        max_expected_retries=5,
        expected_tool_categories=["diagnostic", "retry"],
        should_not_skip=True,
    ),
    pass_criteria=PassCriteria(min_total_score=70,
                                min_escalation_score=15),
))

SCENARIOS.append(ErrorScenario(
    scenario_id="q_formchk_003",
    layer="quantum", step_name="struct_maker", step_index=1,
    description="Formchk 失败 → 重试1次 → 成功",
    injected_error_kind=ErrorKind.FORMCHK_FAILED,
    injected_error_message="formchk 转换 .chk → .fchk 失败",
    injected_raw_output="Error reading .chk file\nformchk: permission denied",
    llm_responses=[
        _failure_resp(0, "formchk_failed",
                       message="formchk 重试仍失败",
                       hint="检查文件权限和磁盘空间"),
        _success_resp(1),
    ],
    expected=ExpectedBehavior(
        should_retry=True, should_escalate=False,
        max_expected_retries=2,
        expected_tool_categories=["diagnostic", "retry"],
        acceptable_actions=["重新运行 formchk", "检查 chk 文件完整性"],
        should_not_skip=True,
    ),
    pass_criteria=PassCriteria(min_total_score=60),
))

SCENARIOS.append(ErrorScenario(
    scenario_id="q_orca_004",
    layer="quantum", step_name="struct_maker", step_index=1,
    description="ORCA 内存不足崩溃 → 增加内存重试 → 成功",
    injected_error_kind=ErrorKind.ORCA_CRASH,
    injected_error_message="ORCA 内存不足 (OOM)",
    injected_raw_output="ORCA finished by error termination in SCF\nOut of memory",
    llm_responses=[
        _failure_resp(0, "orca_crash",
                       message="ORCA 仍崩溃（已增加500MB内存）",
                       hint="继续增加内存或减少基组"),
        _success_resp(1),
    ],
    expected=ExpectedBehavior(
        should_retry=True, should_escalate=False,
        max_expected_retries=2,
        expected_tool_categories=["diagnostic", "retry", "config_modify"],
        acceptable_actions=["增加内存", "reduce memory", "减小基组"],
        should_not_skip=True,
    ),
    pass_criteria=PassCriteria(min_total_score=60),
))

SCENARIOS.append(ErrorScenario(
    scenario_id="q_geom_005",
    layer="quantum", step_name="struct_maker", step_index=1,
    description="几何优化不收敛 → 添加 opt=calcfc → 成功",
    injected_error_kind=ErrorKind.GEOM_NOT_CONVERGED,
    injected_error_message="几何优化在 50 步后未收敛",
    injected_raw_output="Maximum Force 0.001500 NO\nOptimization stopped.",
    llm_responses=[
        _failure_resp(0, "geom_not_converged",
                       message="几何仍未收敛（opt=calcfc）",
                       hint="尝试 opt=gdiis 或减小步长"),
        _success_resp(1),
    ],
    expected=ExpectedBehavior(
        should_retry=True, should_escalate=False,
        max_expected_retries=2,
        expected_tool_categories=["diagnostic", "retry"],
        acceptable_actions=["opt=calcfc", "减小步长", "更换优化算法"],
        should_not_skip=True,
    ),
    pass_criteria=PassCriteria(min_total_score=60),
))

SCENARIOS.append(ErrorScenario(
    scenario_id="q_resp_006",
    layer="quantum", step_name="chg_maker", step_index=3,
    description="RESP 电荷拟合失败 → 尝试更换溶剂 → 仍失败 → 升级",
    injected_error_kind=ErrorKind.RESP_FAILED,
    injected_error_message="Multiwfn RESP 计算失败",
    injected_raw_output="Error in module ESP\nNo convergence in RESP fitting",
    llm_responses=[
        _failure_resp(i, "resp_failed",
                       message=f"RESP 第{i+1}次重试失败",
                       hint="尝试更换溶剂或检查 chg 文件格式")
        for i in range(3)
    ] + [_escalate_resp(3)],
    expected=ExpectedBehavior(
        should_retry=True, should_escalate=True,
        max_expected_retries=3,
        expected_tool_categories=["diagnostic", "retry", "config_modify"],
        should_not_skip=True,
    ),
    pass_criteria=PassCriteria(min_total_score=70,
                                min_escalation_score=15),
))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Layer 2: Topology Agent  (6 场景)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SCENARIOS.append(ErrorScenario(
    scenario_id="t_sobtop_007",
    layer="topology", step_name="topo_gaff", step_index=4,
    description="Sobtop 失败 → 使用 manifest 中同一 Sobtop 后端重试2次 → 第3次成功",
    injected_error_kind=ErrorKind.SOBTOP_FAILED,
    injected_error_message="Sobtop 处理 LiTFSI 时退出",
    injected_raw_output="Fortran runtime error: Cannot open mol2 file",
    llm_responses=[
        _failure_resp(0, "sobtop_failed",
                       message="Sobtop 仍失败（使用 manifest 中的 mol2+chg）",
                       hint="检查 mol2 文件格式"),
        _failure_resp(1, "sobtop_failed",
                       message="Sobtop 仍失败（检查 mol2）",
                       hint="请求上游检查 mol2/chg 后继续 Sobtop 重试"),
        _success_resp(2),
    ],
    expected=ExpectedBehavior(
        should_retry=True, should_escalate=False,
        max_expected_retries=3,
        expected_tool_categories=["diagnostic", "retry"],
        acceptable_actions=["manifest", "检查 mol2", "Sobtop 重试"],
        should_not_skip=True,
    ),
    pass_criteria=PassCriteria(min_total_score=60),
))

SCENARIOS.append(ErrorScenario(
    scenario_id="t_rc24_008",
    layer="topology", step_name="topo_gaff", step_index=4,
    description="Sobtop rc=24 且 ITP/GRO 完整有效 → 接受当前结果 → 继续流程",
    # 实际执行器只在本次 ITP/GRO 通过校验时接受 rc=24。
    injected_error_kind=ErrorKind.SOBTOP_FAILED,
    injected_error_message="Sobtop 退出码 24（已知 Fortran 清理错误, 非致命）",
    injected_raw_output="Fortran runtime error: rc=24\nOutput files generated successfully",
    llm_responses=[
        _success_resp(0),  # Agent 正确识别非致命错误, 第一次就恢复
    ],
    expected=ExpectedBehavior(
        should_retry=True, should_escalate=False,
        max_expected_retries=1,
        expected_tool_categories=["diagnostic"],
        acceptable_actions=["检查输出文件", "忽略 rc=24", "非致命错误"],
        should_not_skip=True,
    ),
    pass_criteria=PassCriteria(min_total_score=60,
                                min_diagnosis_score=15),
))

SCENARIOS.append(ErrorScenario(
    scenario_id="t_ligpargen_009",
    layer="topology", step_name="topo_opls", step_index=4,
    description="LigParGen 失败 → 在同一 OPLS-AA 后端重试 → 成功",
    injected_error_kind=ErrorKind.LIGPARGEN_FAILED,
    injected_error_message="LigParGen 无法生成 OPLS 参数",
    injected_raw_output="LigParGen: No valid parameters found for this molecule",
    llm_responses=[
        _failure_resp(0, "ligpargen_failed",
                       message="LigParGen 失败",
                       hint="检查 BOSS 后在当前 OPLS-AA 后端重试"),
        _success_resp(1),
    ],
    expected=ExpectedBehavior(
        should_retry=True, should_escalate=False,
        max_expected_retries=2,
        expected_tool_categories=["diagnostic", "retry"],
        acceptable_actions=["BOSS", "OPLS-AA", "重试"],
        should_not_skip=True,
    ),
    pass_criteria=PassCriteria(min_total_score=60),
))

SCENARIOS.append(ErrorScenario(
    scenario_id="t_atomtype_010",
    layer="topology", step_name="top_assembly", step_index=5,
    description="atomtype CX 参数冲突 → 停止组装并升级",
    injected_error_kind=ErrorKind.ATOMTYPE_CONFLICT,
    injected_error_message="atomtype CX 在两个 ITP 中参数不一致",
    injected_raw_output="ERROR: atomtype CX not found in atomtype database",
    llm_responses=[
        _failure_resp(0, "grompp_failed",
                       message="atomtype 仍缺失",
                       hint="检测到跨 forcefield_family 或 atomtype 参数冲突，必须升级"),
        _escalate_resp(1),
    ],
    expected=ExpectedBehavior(
        should_retry=False, should_escalate=True,
        max_expected_retries=0,
        expected_tool_categories=[],
        acceptable_actions=["冲突", "升级", "检查力场"],
        should_not_skip=True,
    ),
    pass_criteria=PassCriteria(min_total_score=60, min_escalation_score=10),
))

SCENARIOS.append(ErrorScenario(
    scenario_id="t_mol2_corrupt_011",
    layer="topology", step_name="topo_gaff", step_index=4,
    description="mol2 文件损坏无法修复 → 请求上游修复并升级",
    injected_error_kind=ErrorKind.FILE_NOT_FOUND,
    injected_error_message="mol2 文件格式损坏，无法解析",
    injected_raw_output="mol2 文件缺少 @<TRIPOS>ATOM 段",
    llm_responses=[
        _failure_resp(0, "file_not_found",
                       message="mol2 文件无法修复",
                       hint="请求量子层重新生成 mol2；当前拓扑运行不能跳过分子"),
        _escalate_resp(1),
    ],
    expected=ExpectedBehavior(
        should_retry=True, should_escalate=True,
        max_expected_retries=1,
        expected_tool_categories=["diagnostic"],
        acceptable_actions=["重新生成 mol2", "上游", "升级"],
        should_not_skip=True,
    ),
    pass_criteria=PassCriteria(min_total_score=60,
                                min_escalation_score=10),
))

SCENARIOS.append(ErrorScenario(
    scenario_id="t_assembly_012",
    layer="topology", step_name="top_assembly", step_index=5,
    description="主拓扑组装 itp 冲突 → 多次重试 → 升级",
    injected_error_kind=ErrorKind.CONFIG_INVALID,
    injected_error_message="topol.top 中 itp 文件冲突",
    injected_raw_output="Duplicate moleculetype definition in itp",
    llm_responses=[
        _failure_resp(i, "config_invalid",
                       message=f"拓扑冲突第{i+1}次重试",
                       hint="检查 itp 文件去重")
        for i in range(4)
    ] + [_escalate_resp(4)],
    expected=ExpectedBehavior(
        should_retry=True, should_escalate=True,
        max_expected_retries=4,
        expected_tool_categories=["diagnostic", "config_modify", "retry"],
        should_not_skip=True,
    ),
    pass_criteria=PassCriteria(min_total_score=70,
                                min_escalation_score=15),
))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Layer 3: Simulation Agent  (6 场景)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SCENARIOS.append(ErrorScenario(
    scenario_id="s_em_013",
    layer="simulation", step_name="md_em", step_index=8,
    description="EM 未收敛 → 增加 nsteps → 降低 emtol → 成功",
    injected_error_kind=ErrorKind.EM_NOT_CONVERGED,
    injected_error_message="EM 在 10000 步后 Fmax 仍 > 100",
    injected_raw_output="Energy minimization has NOT converged\nFmax = 450 at step 10000",
    llm_responses=[
        _failure_resp(0, "em_not_converged",
                       message="EM 仍未收敛（nsteps=20000）",
                       hint="进一步增加 nsteps 或降低 emtol"),
        _success_resp(1),
    ],
    expected=ExpectedBehavior(
        should_retry=True, should_escalate=False,
        max_expected_retries=2,
        expected_tool_categories=["diagnostic", "retry", "config_modify"],
        acceptable_actions=["增加 nsteps", "降低 emtol", "调整约束"],
        should_not_skip=True,
    ),
    pass_criteria=PassCriteria(min_total_score=60),
))

SCENARIOS.append(ErrorScenario(
    scenario_id="s_eq_014",
    layer="simulation", step_name="md_eq", step_index=9,
    description="EQ 密度不收敛 → 服务端冻结调整方案并等待用户确认",
    injected_error_kind=ErrorKind.EQ_NOT_CONVERGED,
    injected_error_message="EQ 阶段密度波动过大",
    injected_raw_output="Density (SI): mean=850 std=120\ntau_p not sufficient",
    llm_responses=[
        _failure_resp(0, "eq_not_converged",
                       message="密度仍未收敛（增大 tau_p=2.0）",
                       hint="尝试 tau_p=4.0"),
        _success_resp(1),
    ],
    expected=ExpectedBehavior(
        should_retry=False, should_escalate=True,
        max_expected_retries=0,
        expected_tool_categories=[],
        acceptable_actions=["冻结调整方案", "等待用户确认"],
        should_not_skip=True,
    ),
    pass_criteria=PassCriteria(min_total_score=60),
))

SCENARIOS.append(ErrorScenario(
    scenario_id="s_nan_015",
    layer="simulation", step_name="md_prod", step_index=10,
    description="PROD NaN 异常 → 服务端停止自动续跑并要求用户确认方案",
    injected_error_kind=ErrorKind.MDRUN_FAILED,
    injected_error_message="mdrun 检测到 NaN，模拟数值不稳定",
    injected_raw_output="NaN detected in force calculation at step 50000\natom 342",
    llm_responses=[
        _failure_resp(0, "mdrun_failed",
                       message="NaN 仍出现（dt=0.0005）",
                       hint="进一步减小 dt 或检查初始结构"),
        _success_resp(1),
    ],
    expected=ExpectedBehavior(
        should_retry=False, should_escalate=True,
        max_expected_retries=0,
        expected_tool_categories=[],
        acceptable_actions=["停止自动续跑", "等待用户确认"],
        should_not_skip=True,
    ),
    pass_criteria=PassCriteria(min_total_score=60),
))

SCENARIOS.append(ErrorScenario(
    scenario_id="s_temp_016",
    layer="simulation", step_name="md_eq", step_index=9,
    description="EQ 温度爆炸 → 服务端冻结调整方案并等待用户确认",
    injected_error_kind=ErrorKind.MDRUN_FAILED,
    injected_error_message="温度爆炸到 9999K，模拟崩溃",
    injected_raw_output="Temperature: 9999.0 K at step 1000\nLINCS warnings: 5000",
    llm_responses=[
        _failure_resp(0, "mdrun_failed",
                       message="温度仍过高（退火调整后）",
                       hint="检查初始结构原子重叠"),
        _success_resp(1),
    ],
    expected=ExpectedBehavior(
        should_retry=False, should_escalate=True,
        max_expected_retries=0,
        expected_tool_categories=[],
        acceptable_actions=["冻结调整方案", "等待用户确认"],
        should_not_skip=True,
    ),
    pass_criteria=PassCriteria(min_total_score=60),
))

SCENARIOS.append(ErrorScenario(
    scenario_id="s_grompp_017",
    layer="simulation", step_name="md_em", step_index=8,
    description="grompp atomtype 错误 → 诊断 → 修复 itp → 成功",
    injected_error_kind=ErrorKind.GROMPP_FAILED,
    injected_error_message="grompp 失败: atomtype 不匹配",
    injected_raw_output="ERROR: atomtype CX not found in itp\nCheck topology",
    llm_responses=[
        _failure_resp(0, "grompp_failed",
                       message="grompp 仍失败",
                       hint="检查 itp 文件 atomtype 定义"),
        _success_resp(1),
    ],
    expected=ExpectedBehavior(
        should_retry=True, should_escalate=False,
        max_expected_retries=2,
        expected_tool_categories=["diagnostic", "config_modify", "retry"],
        acceptable_actions=["修复 atomtype", "检查 itp", "修改 topology"],
        should_not_skip=True,
    ),
    pass_criteria=PassCriteria(min_total_score=60),
))

SCENARIOS.append(ErrorScenario(
    scenario_id="s_eq_persist_018",
    layer="simulation", step_name="md_eq", step_index=9,
    description="EQ 持续失败 → 服务端保持等待确认，不消耗自动重试",
    injected_error_kind=ErrorKind.EQ_NOT_CONVERGED,
    injected_error_message="EQ 反复失败，所有参数调整无效",
    injected_raw_output="Temperature drift: +50K over 5ns\nDensity drift: -5% over 5ns",
    llm_responses=[
        _failure_resp(i, "eq_not_converged",
                       message=f"EQ 第{i+1}次重试失败",
                       hint="尝试不同策略: tau_p, ref_t, 退火方案")
        for i in range(3)
    ] + [_escalate_resp(3)],
    expected=ExpectedBehavior(
        should_retry=False, should_escalate=True,
        max_expected_retries=0,
        expected_tool_categories=[],
        should_not_skip=True,
    ),
    pass_criteria=PassCriteria(min_total_score=60,
                                min_escalation_score=10),
))
