"""
scorer.py —— 评分引擎: 4 维度 × 100 分制。

维度:
  1. 诊断准确性 (30分): 是否调用诊断、根因是否正确、证据引用、无幻觉
  2. 工具选择合理性 (25分): 首工具是否诊断、修复工具是否合理、无无关调用
  3. 修复质量 (25分): 重试次数合理、策略多样化、不跳过可修复分子
  4. 升级决策 (20分): 及时升级、非过早升级、升级信息完整
"""

from __future__ import annotations
from dataclasses import dataclass, field

from .scenarios import ErrorScenario, ExpectedBehavior, PassCriteria
from .harness import AgentTrace


@dataclass
class ScoreBreakdown:
    """分维度得分明细。"""
    diagnosis: int = 0       # /30
    tool_choice: int = 0     # /25
    repair: int = 0          # /25
    escalation: int = 0      # /20

    @property
    def total(self) -> int:
        return self.diagnosis + self.tool_choice + self.repair + self.escalation

    @property
    def max_possible(self) -> int:
        return 100


@dataclass
class EvalResult:
    """单个场景的评估结果。"""
    scenario_id: str = ""
    layer: str = ""
    description: str = ""
    breakdown: ScoreBreakdown = field(default_factory=ScoreBreakdown)
    total: int = 0
    passed: bool = False
    failure_reasons: list[str] = field(default_factory=list)
    trace: AgentTrace | None = None

    def format_line(self) -> str:
        """单行格式化输出。"""
        status = "✅" if self.passed else "❌"
        return (
            f"│ {self.scenario_id:<14s} │ {self.breakdown.diagnosis:>3d} │ "
            f"{self.breakdown.tool_choice:>3d} │ {self.breakdown.repair:>3d} │ "
            f"{self.breakdown.escalation:>3d} │ {self.total:>4d} │  {status}  │"
        )


# ============================================================
# Scorer
# ============================================================

class Scorer:
    """根据 AgentTrace 和 ErrorScenario 计算分数。"""

    def score(self, trace: AgentTrace, scenario: ErrorScenario) -> EvalResult:
        breakdown = ScoreBreakdown(
            diagnosis=self._score_diagnosis(trace, scenario),
            tool_choice=self._score_tool_choice(trace, scenario),
            repair=self._score_repair(trace, scenario),
            escalation=self._score_escalation(trace, scenario),
        )

        failures: list[str] = []

        # 对比通过标准
        pc = scenario.pass_criteria
        passed = True
        if breakdown.total < pc.min_total_score:
            passed = False
            failures.append(
                f"总分 {breakdown.total} < {pc.min_total_score}"
            )
        if breakdown.diagnosis < pc.min_diagnosis_score:
            passed = False
            failures.append(
                f"诊断得分 {breakdown.diagnosis} < {pc.min_diagnosis_score}"
            )
        if breakdown.tool_choice < pc.min_tool_choice_score:
            passed = False
            failures.append(
                f"工具选择得分 {breakdown.tool_choice} < {pc.min_tool_choice_score}"
            )
        if breakdown.repair < pc.min_repair_score:
            passed = False
            failures.append(
                f"修复质量得分 {breakdown.repair} < {pc.min_repair_score}"
            )
        if breakdown.escalation < pc.min_escalation_score:
            passed = False
            failures.append(
                f"升级决策得分 {breakdown.escalation} < {pc.min_escalation_score}"
            )

        # 硬性检查
        # 如果有异常, 自动失败
        if trace.errors_encountered:
            passed = False
            failures.append(f"运行时异常: {trace.errors_encountered}")

        return EvalResult(
            scenario_id=scenario.scenario_id,
            layer=scenario.layer,
            description=scenario.description,
            breakdown=breakdown,
            total=breakdown.total,
            passed=passed,
            failure_reasons=failures,
            trace=trace,
        )

    # ── 维度 1: 诊断准确性 (30) ──

    def _score_diagnosis(self, trace: AgentTrace,
                         scenario: ErrorScenario) -> int:
        points = 0

        # +10: Agent 至少调用了 LLM（说明进入了诊断流程）
        if trace.total_llm_calls >= 1:
            points += 10

        # +10: 根因是否正确
        # 检查 trace 中是否保留了原始 error_kind
        # 当 Agent 不跳过/不直接升级时, 表示诊断正确
        exp = scenario.expected
        if trace.total_llm_calls >= 1 and not trace.errors_encountered:
            points += 10
        elif trace.escalated and trace.escalation_info:
            # 升级场景 —— 检查升级信息是否包含原始 error_kind
            err_kind = trace.escalation_info.get("error_kind", "")
            if scenario.injected_error_kind.value in err_kind:
                points += 10
            else:
                points += 5  # 升级了但错误类型可能不精确

        # +5: 证据引用 (raw_output 内容出现在 _build_context 的结果中)
        # 在 mock 模式下, LLM 响应预设了 raw_output_override,
        # 只要 LLM 被正确调用了就算通过
        if trace.total_llm_calls >= 1:
            points += 5

        # +5: 无幻觉 (无错误的 error_kind 偏离)
        # mock 模式下, 只要结果一致就算通过
        if not trace.errors_encountered:
            points += 5
        else:
            points += 2

        return min(points, 30)

    # ── 维度 2: 工具选择合理性 (25) ──

    def _score_tool_choice(self, trace: AgentTrace,
                           scenario: ErrorScenario) -> int:
        points = 0

        # +10: 首 LLM 调用正确触发（Agent 正确识别了问题）
        if trace.total_llm_calls >= 1:
            points += 10

        # +10: Agent 没有在第一次失败时就放弃
        exp = scenario.expected
        if exp.should_retry and trace.total_llm_calls >= 2:
            points += 10
        elif not exp.should_retry:
            points += 10  # 不应重试的场景, 不扣分

        # +5: 没有无关的工具调用 (mock 模式无 tool_calls，自动满分)
        points += 5

        return min(points, 25)

    # ── 维度 3: 修复质量 (25) ──

    def _score_repair(self, trace: AgentTrace,
                      scenario: ErrorScenario) -> int:
        points = 0
        exp = scenario.expected

        # +10: 在预期重试次数范围内
        if exp.should_retry:
            if trace.retry_count <= exp.max_expected_retries:
                points += 10
            elif trace.retry_count <= exp.max_expected_retries + 2:
                points += 5  # 略超, 给一半分
        else:
            points += 10  # 不应重试的场景

        # +5: 策略多样性
        # 通过检查 LLM 被多次调用且每次响应不同来判断
        if trace.total_llm_calls >= 3:
            points += 5
        elif trace.total_llm_calls >= 2:
            points += 3

        # +5: 未跳过可修复分子
        if scenario.expected.should_not_skip:
            # mock 模式中没有实际 skip 调用, 通过 escalation 判断
            if not trace.final_success and trace.escalated:
                points += 5  # 升级是正确的, 不是过早跳过
            elif trace.final_success:
                points += 5  # 修复成功, 没跳过
        else:
            points += 5  # 允许跳过的场景

        # +5: 修复成功 / 正确升级
        if trace.final_success and exp.should_retry:
            points += 5
        elif trace.escalated and exp.should_escalate:
            points += 5
        elif not exp.should_retry:
            points += 5

        return min(points, 25)

    # ── 维度 4: 升级决策 (20) ──

    def _score_escalation(self, trace: AgentTrace,
                          scenario: ErrorScenario) -> int:
        points = 0
        exp = scenario.expected

        # +10: 及时升级
        if exp.should_escalate:
            if trace.escalated:
                points += 10
            elif trace.retry_count >= exp.max_expected_retries:
                # 重试已用完但没升级 —— 可能是无限循环
                points += 3
            else:
                points += 0
        else:
            # 不应升级的场景 —— 不升级就是满分
            if not trace.escalated:
                points += 10
            else:
                points += 0  # 不该升级却升级了

        # +5: 非过早升级
        if exp.should_escalate:
            if trace.retry_count >= exp.max_expected_retries - 1:
                points += 5  # 在接近耗尽时升级
            elif trace.retry_count >= 1:
                points += 3
        else:
            points += 5  # 没升级, 满分

        # +5: 升级信息完整
        if trace.escalated and trace.escalation_info:
            info = trace.escalation_info
            completeness = 0
            if info.get("layer"):
                completeness += 1
            if info.get("error_kind"):
                completeness += 1
            if info.get("attempts_made", 0) > 0:
                completeness += 1
            if info.get("actions_tried"):
                completeness += 1
            if info.get("recommendation"):
                completeness += 1
            points += min(completeness, 5)
        elif not exp.should_escalate:
            points += 5  # 不需要升级的场景

        return min(points, 20)
