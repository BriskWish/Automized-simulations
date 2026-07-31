"""
phase2_runner.py —— Phase 2: 真实 LLM (DeepSeek) + Mock 工具执行。

核心变化 (vs Phase 1):
  - LLM 调用真实 DeepSeek API (真实的 tool-calling 决策)
  - 工具执行结果由 MockToolExecutor 返回 (不运行外部程序)
  - 验证 Agent 的工具选择、诊断逻辑、重试策略是否合理

Token 估算: ~490K tokens 总计 (~$0.18)
耗时: ~60s (4 线程并行)

用法:
  python3 -m tests.llm_eval.phase2_runner
  python3 -m tests.llm_eval.phase2_runner --layer quantum
  python3 -m tests.llm_eval.phase2_runner --scenario q_scf_001  # 单场景
  python3 -m tests.llm_eval.phase2_runner --dry-run              # 估算 token
"""

from __future__ import annotations
import json
import os
import sys
import time
import threading
from dataclasses import dataclass, field
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

from willy.errors import StepResult, StepError, ErrorKind, DiagnosisResult
from .scenarios import SCENARIOS, ErrorScenario, MockLLMResponse
from .harness import AgentTrace, ToolCallRecord
from .scorer import Scorer, EvalResult

# ============================================================
# MockToolExecutor —— 受控的工具执行结果
# ============================================================

class MockToolExecutor:
    """
    模拟工具执行, 返回受控的 StepResult / DiagnosisResult。

    状态机:
      - diagnose: 始终成功 (返回相关诊断信息)
      - retry:  按 attempt 计数返回预设结果
        - attempt 0..N-1: failure (与 scenario 的 failure 响应匹配)
        - attempt N:      success
        - attempt > N:    failure → Agent 应升级
      - modify_config: 始终成功
      - skip_molecule:  始终成功
    """

    def __init__(self, scenario: ErrorScenario):
        self._scenario = scenario
        self._repair_attempt = 0        # retry 工具调用次数
        self._diagnose_count = 0
        self._lock = threading.Lock()
        self.call_records: list[ToolCallRecord] = []

        # 统计: 此场景有多少个 failure 响应, 第几次应成功
        self._failure_count = sum(
            1 for r in scenario.llm_responses if r.outcome == "failure"
        )
        self._has_success = any(
            r.outcome == "success" for r in scenario.llm_responses
        )
        self._has_escalate = any(
            r.outcome == "escalate" for r in scenario.llm_responses
        )

    def handle(self, tool_name: str, args: dict) -> str:
        """模拟工具调用, 返回 JSON 字符串。"""
        with self._lock:
            return self._handle_locked(tool_name, args)

    def _handle_locked(self, tool_name: str, args: dict) -> str:
        # ── 诊断工具 ──
        if "diagnose" in tool_name:
            self._diagnose_count += 1
            dr = self._make_diagnosis()
            self.call_records.append(ToolCallRecord(
                call_index=len(self.call_records),
                tool_name=tool_name, arguments=args,
                response_ok=True, response_success=True,
                response_error_kind="",
                response_message="diagnosis",
            ))
            return json.dumps(dr.to_dict(), ensure_ascii=False)

        # ── 配置修改工具 ──
        if "modify_config" in tool_name or "set_backend" in tool_name:
            self.call_records.append(ToolCallRecord(
                call_index=len(self.call_records),
                tool_name=tool_name, arguments=args,
                response_ok=True, response_success=True,
                response_error_kind="",
                response_message="config updated",
            ))
            return json.dumps({"ok": True, "updated_fields": list(args.keys())},
                              ensure_ascii=False)

        # ── 跳过分子工具 ──
        if "skip_molecule" in tool_name:
            name = args.get("molecule_name", "unknown")
            self.call_records.append(ToolCallRecord(
                call_index=len(self.call_records),
                tool_name=tool_name, arguments=args,
                response_ok=True, response_success=True,
                response_error_kind="",
                response_message=f"skipped {name}",
            ))
            return json.dumps({
                "ok": True, "molecule": name,
                "reason": args.get("reason", ""),
                "warning": f"⚠ {name} 已跳过",
            }, ensure_ascii=False)

        # ── 重试工具 ──
        if "retry" in tool_name:
            attempt = self._repair_attempt
            self._repair_attempt += 1

            # 决定本次重试结果
            if self._has_success and attempt == self._failure_count:
                # 正好在 failure 次数之后 → 成功
                outcome = "success"
            elif attempt < self._failure_count:
                # 仍在失败窗口内
                outcome = "failure"
            elif self._has_escalate:
                # 所有重试已耗尽
                outcome = "failure"
            else:
                outcome = "failure"

            sr = self._make_retry_result(outcome, attempt)
            self.call_records.append(ToolCallRecord(
                call_index=len(self.call_records),
                tool_name=tool_name, arguments=args,
                response_ok=True,
                response_success=sr.success,
                response_error_kind=sr.error.kind.value if sr.error else "",
                response_message=sr.error.message if sr.error else "success",
            ))
            return json.dumps(self._step_to_dict(sr), ensure_ascii=False)

        # ── 未知工具 ──
        self.call_records.append(ToolCallRecord(
            call_index=len(self.call_records),
            tool_name=tool_name, arguments=args,
            response_ok=False, response_success=False,
            response_error_kind="unknown_tool",
            response_message=f"未知工具: {tool_name}",
        ))
        return json.dumps({"error": f"未知工具: {tool_name}"})

    # ── 辅助方法 ──

    def _make_diagnosis(self) -> DiagnosisResult:
        s = self._scenario
        evidence = []
        if s.injected_raw_output:
            evidence = [s.injected_raw_output[:200]]
        return DiagnosisResult(
            source=s.layer,
            severity="error",
            issues=[f"{s.injected_error_kind.value}: {s.injected_error_message[:100]}"],
            evidence=evidence,
            hint=s.injected_hint or f"检查 {s.step_name} 的输入和配置",
        )

    def _make_retry_result(self, outcome: str, attempt: int) -> StepResult:
        s = self._scenario
        if outcome == "success":
            return StepResult(
                step_name=s.step_name, step_index=s.step_index,
                success=True,
                outputs={"repaired": f"/tmp/{s.scenario_id}_repaired"},
                artifacts=[f"/tmp/{s.scenario_id}_repaired"],
                duration_s=1.0,
            )
        else:
            return StepResult(
                step_name=s.step_name, step_index=s.step_index,
                success=False,
                error=StepError(
                    kind=s.injected_error_kind,
                    message=f"[{s.layer}] 第{attempt+1}次重试: "
                            f"{s.injected_error_message[:80]}",
                    raw_output=s.injected_raw_output,
                    hint=f"尝试不同的修复策略 (已尝试 {attempt+1} 次)",
                ),
            )

    @staticmethod
    def _step_to_dict(sr: StepResult) -> dict:
        err = sr.error
        return {
            "_step_result": True,
            "success": sr.success,
            "step_name": sr.step_name,
            "outputs": sr.outputs,
            "artifacts": sr.artifacts,
            "duration_s": sr.duration_s,
            "error_message": err.message if err else "",
            "error_kind": err.kind.value if err else "",
            "hint": err.hint if err else "",
            "raw_output": err.raw_output if err else "",
        }


# ============================================================
# Phase2Harness
# ============================================================

class Phase2Harness:
    """
    Phase 2 评估核心: 真实 LLM + Mock 工具。

    - 使用真实的 DeepSeek API 进行 tool-calling
    - 工具执行结果由 MockToolExecutor 控制
    - 验证 Agent 的决策质量
    """

    def __init__(self, scenario: ErrorScenario, tmp_path: Path,
                 llm_client, model: str = "deepseek-chat"):
        self.scenario = scenario
        self.tmp_path = tmp_path
        self.llm_client = llm_client
        self.model = model
        self.trace = AgentTrace(
            scenario_id=scenario.scenario_id,
            layer=scenario.layer,
        )
        self._mock_executor = MockToolExecutor(scenario)

    def run(self) -> AgentTrace:
        """执行评估。"""
        # ── 准备环境 ──
        config_path = self.tmp_path / "config.json"
        config_path.write_text(json.dumps({
            "backend": "g16",
            "residues": {"LiTFSI": 10, "FEC": 120},
            "molecules": {
                "LiTFSI": {"charge": 0, "spin": 1,
                           "basis": "b3lyp/6-311+g(d,p)", "solvent": "acetone"},
                "FEC": {"charge": 0, "spin": 1,
                        "basis": "b3lyp/6-311+g(d,p)", "solvent": "acetone"},
            },
            "md": {"dt": 0.001, "ref_t": 298.15, "ref_p": 1.01325,
                   "eq_ns": 10, "prod_ns": 10},
        }, indent=2))

        run_dir = self.tmp_path / "md_run"
        run_dir.mkdir(exist_ok=True)

        # ── 创建 Agent 并注入 mock 工具处理器 ──
        agent = self._create_layer_agent()
        agent.handle_tool = self._mock_executor.handle  # ← 关键注入

        # ── 构造注入错误 ──
        sr = StepResult(
            step_name=self.scenario.step_name,
            step_index=self.scenario.step_index,
            success=False,
            error=StepError(
                kind=self.scenario.injected_error_kind,
                message=self.scenario.injected_error_message,
                raw_output=self.scenario.injected_raw_output,
                hint=self.scenario.injected_hint,
            ),
        )

        # ── 执行 ──
        from unittest.mock import MagicMock
        sm = MagicMock()
        t_start = time.time()

        try:
            result = agent.handle_failure(
                step_result=sr,
                config_path=str(config_path),
                run_dir=str(run_dir),
                artifacts={},
                state_machine=sm,
            )
        except Exception as e:
            self.trace.errors_encountered.append(
                f"{type(e).__name__}: {str(e)[:200]}"
            )
            result = StepResult(
                step_name=self.scenario.step_name,
                step_index=self.scenario.step_index,
                success=False, escalated=True,
                extra={"escalation": {"error": str(e)[:200]}},
            )

        self.trace.duration_s = time.time() - t_start
        self.trace.final_success = result.success
        self.trace.escalated = result.escalated
        self.trace.max_retries_configured = agent.max_retries
        self.trace.total_tool_calls = len(self._mock_executor.call_records)
        self.trace.tool_calls = self._mock_executor.call_records
        self.trace.retry_count = self._mock_executor._repair_attempt

        # LLM 调用次数通过监控 API 调用来估算
        # 简化: tool_calls 数量 + 最终响应
        self.trace.total_llm_calls = (
            self._mock_executor._diagnose_count +
            self._mock_executor._repair_attempt +
            1  # 最终响应
        )

        if result.escalated and "escalation" in result.extra:
            self.trace.escalation_info = result.extra["escalation"]

        return self.trace

    def _create_layer_agent(self):
        """创建 Agent 并注入真实 LLM client。"""
        kw = dict(llm_client=self.llm_client, max_retries=5)

        if self.scenario.layer == "quantum":
            from willy.agent_quantum import QuantumAgent
            return QuantumAgent(**kw)
        elif self.scenario.layer == "topology":
            from willy.agent_topology import TopologyAgent
            return TopologyAgent(**kw)
        elif self.scenario.layer == "simulation":
            from willy.agent_simulation import SimulationAgent
            return SimulationAgent(**kw)
        raise ValueError(f"未知 layer: {self.scenario.layer}")


# ============================================================
# LLM 客户端初始化
# ============================================================

def _init_llm_client():
    """从环境变量或 .env 文件初始化 DeepSeek 客户端。"""
    from willy._paths import get_project_root

    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        env_file = get_project_root() / ".env"
        if env_file.exists():
            for line in env_file.read_text().split("\n"):
                if line.startswith("DEEPSEEK_API_KEY="):
                    api_key = line.split("=", 1)[1].strip().strip('"').strip("'")

    if not api_key:
        raise RuntimeError(
            "DEEPSEEK_API_KEY 未设置。请在 .env 文件中设置或导出环境变量。"
        )

    from openai import OpenAI
    return OpenAI(api_key=api_key, base_url="https://api.deepseek.com"), api_key


# ============================================================
# 并行运行
# ============================================================

def run_phase2(
    scenarios: list[ErrorScenario],
    max_workers: int = 4,
    model: str = "deepseek-chat",
    dry_run: bool = False,
) -> list[EvalResult]:
    """并行执行所有场景, 返回评估结果。"""
    client, api_key = _init_llm_client()
    scorer = Scorer()
    results: list[EvalResult] = []
    results_lock = threading.Lock()

    if dry_run:
        _print_token_estimate(scenarios)
        return []

    total = len(scenarios)
    completed = 0
    progress_lock = threading.Lock()

    def run_one(idx: int, scenario: ErrorScenario) -> EvalResult:
        nonlocal completed
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            harness = Phase2Harness(scenario, Path(tmp), client, model)
            trace = harness.run()
            result = scorer.score(trace, scenario)

        with progress_lock:
            nonlocal completed
            completed += 1
            status = "✅" if result.passed else "❌"
            print(f"\r[{completed}/{total}] {scenario.scenario_id} "
                  f"{status} ({result.total}/100, "
                  f"tools={trace.total_tool_calls}, "
                  f"t={trace.duration_s:.0f}s)", end="", flush=True)

        return result

    print(f"\n  Phase 2 评估: {total} 场景, {max_workers} 线程, "
          f"模型: {model}")
    print(f"  ─────────────────────────────────────────────")

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(run_one, i, s): s
            for i, s in enumerate(scenarios)
        }
        for future in as_completed(futures):
            result = future.result()
            with results_lock:
                results.append(result)

    # 按 scenario_id 排序
    results.sort(key=lambda r: r.scenario_id)
    print(f"\r{' ' * 70}\r", end="")
    return results


def _print_token_estimate(scenarios: list[ErrorScenario]):
    """估算 token 消耗 (不实际调用 API)。"""
    # 每个 LLM 调用的估算:
    # - system prompt: ~1500 tokens
    # - error context: ~1500 tokens
    # - tool definitions: ~800 tokens
    # - tool results: ~300 tokens each
    # - final response: ~100 tokens
    print("\n  Token 消耗估算 (Dry Run)")
    print("  ────────────────────────")

    total_input = 0
    total_output = 0
    for s in scenarios:
        n_llm_calls = len(s.llm_responses) + 1  # +initial call
        # 每次调用: system + context + tools + previous results
        input_tokens = n_llm_calls * 3500 + (n_llm_calls - 1) * 500
        output_tokens = n_llm_calls * 200
        total_input += input_tokens
        total_output += output_tokens

    print(f"  场景数:        {len(scenarios)}")
    print(f"  预估输入 token:  {total_input/1000:.0f}K")
    print(f"  预估输出 token:  {total_output/1000:.0f}K")
    print(f"  预估总 token:    {(total_input+total_output)/1000:.0f}K")
    # DeepSeek pricing
    cost = total_input/1e6 * 0.27 + total_output/1e6 * 1.10
    print(f"  预估费用:        ${cost:.2f}")
    print()


# ============================================================
# 报告
# ============================================================

def print_phase2_report(results: list[EvalResult]):
    """打印 Phase 2 报告, 包含与 Phase 1 的对比视角。"""
    if not results:
        return

    layers = {"quantum": [], "topology": [], "simulation": []}
    for r in results:
        layers[r.layer].append(r)

    print()
    print("═" * 78)
    print("  LLM Agent 错误处理评估报告 (Phase 2 — Real LLM + Mock Tools)")
    print("═" * 78)

    for layer_name, layer_results in layers.items():
        if not layer_results:
            continue
        label = {
            "quantum": "Layer 1: Quantum Agent",
            "topology": "Layer 2: Topology Agent",
            "simulation": "Layer 3: Simulation Agent",
        }[layer_name]

        print(f"\n  {label}")
        print("  ┌" + "─" * 14 + "┬" + "─" * 5 + "┬" + "─" * 5
              + "┬" + "─" * 5 + "┬" + "─" * 5 + "┬" + "─" * 6
              + "┬" + "─" * 7 + "┬" + "─" * 6 + "┐")
        print("  │ Scenario      │ Diag│ Tool│Repr │ Esc │ Total"
              "│ Tools  │ Status │")
        print("  ├" + "─" * 14 + "┼" + "─" * 5 + "┼" + "─" * 5
              + "┼" + "─" * 5 + "┼" + "─" * 5 + "┼" + "─" * 6
              + "┼" + "─" * 7 + "┼" + "─" * 6 + "┤")

        for r in layer_results:
            n_tools = r.trace.total_tool_calls if r.trace else 0
            status = "✅" if r.passed else "❌"
            print(f"  │ {r.scenario_id:<14s} │ {r.breakdown.diagnosis:>3d} │ "
                  f"{r.breakdown.tool_choice:>3d} │ {r.breakdown.repair:>3d} │ "
                  f"{r.breakdown.escalation:>3d} │ {r.total:>4d} │ "
                  f"{n_tools:>5d} │  {status}  │")

        avg_d = sum(r.breakdown.diagnosis for r in layer_results) / len(layer_results)
        avg_t = sum(r.breakdown.tool_choice for r in layer_results) / len(layer_results)
        avg_r = sum(r.breakdown.repair for r in layer_results) / len(layer_results)
        avg_e = sum(r.breakdown.escalation for r in layer_results) / len(layer_results)
        avg_total = sum(r.total for r in layer_results) / len(layer_results)
        passed = sum(1 for r in layer_results if r.passed)
        total_n = len(layer_results)

        print("  ├" + "─" * 14 + "┼" + "─" * 5 + "┼" + "─" * 5
              + "┼" + "─" * 5 + "┼" + "─" * 5 + "┼" + "─" * 6
              + "┼" + "─" * 7 + "┼" + "─" * 6 + "┤")
        print(f"  │ {'Avg':<13s} │ {avg_d:>3.0f} │ {avg_t:>3.0f} │ "
              f"{avg_r:>3.0f} │ {avg_e:>3.0f} │ {avg_total:>4.0f} │ "
              f"{'':>5s} │ {passed}/{total_n}  │")
        print("  └" + "─" * 14 + "┴" + "─" * 5 + "┴" + "─" * 5
              + "┴" + "─" * 5 + "┴" + "─" * 5 + "┴" + "─" * 6
              + "┴" + "─" * 7 + "┴" + "─" * 6 + "┘")

    # ── 全局汇总 ──
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    avg_score = sum(r.total for r in results) / total if total else 0
    avg_tools = sum(r.trace.total_tool_calls for r in results if r.trace) / total if total else 0
    avg_time = sum(r.trace.duration_s for r in results if r.trace) / total if total else 0

    print(f"\n  ═══════════════════════════════════════════════════════════════")
    print(f"    总场景: {total}  |  通过: {passed} ({passed/total*100:.0f}%)  "
          f"|  均分: {avg_score:.0f}/100")
    print(f"    平均工具调用: {avg_tools:.1f}  |  平均耗时: {avg_time:.0f}s")
    print(f"  ═══════════════════════════════════════════════════════════════")

    # ── 失败详情 ──
    failed = [r for r in results if not r.passed]
    if failed:
        print(f"\n  🔴 未通过 ({len(failed)}):")
        for r in failed:
            print(f"    • {r.scenario_id}: {r.description[:80]}")
            print(f"      得分: {r.total}/100 | 原因: {'; '.join(r.failure_reasons)}")
            if r.trace and r.trace.tool_calls:
                tools_used = [tc.tool_name for tc in r.trace.tool_calls]
                print(f"      调用的工具: {tools_used}")
            if r.trace and r.trace.errors_encountered:
                for err in r.trace.errors_encountered:
                    print(f"      ⚠  {err}")
    else:
        print(f"\n  ✅ 所有场景通过!")

    # ── 工具使用分析 ──
    _print_tool_usage_analysis(results)


def _print_tool_usage_analysis(results: list[EvalResult]):
    """分析 LLM 实际调用了哪些工具。"""
    print(f"\n  📊 工具使用分析:")
    tool_counts: dict[str, int] = {}
    for r in results:
        if r.trace:
            for tc in r.trace.tool_calls:
                tool_counts[tc.tool_name] = tool_counts.get(tc.tool_name, 0) + 1

    if tool_counts:
        for name, count in sorted(tool_counts.items(),
                                   key=lambda x: -x[1]):
            print(f"    • {name}: {count} 次")

    # 异常行为检测
    skipped = [r for r in results
               if r.trace and any("skip" in tc.tool_name
                                  for tc in r.trace.tool_calls)]
    if skipped:
        print(f"\n  ⚠  调用 skip_molecule 的场景: "
              f"{[r.scenario_id for r in skipped]}")

    # 工具选择错误检测
    for r in results:
        if not r.trace or not r.trace.tool_calls:
            continue
        tools = [tc.tool_name for tc in r.trace.tool_calls]
        # 检查跨层工具调用
        if r.layer == "quantum" and any("simulation" in t or "topology" in t
                                         for t in tools):
            print(f"  ⚠  {r.scenario_id}: 跨层工具调用 {tools}")


# ============================================================
# CLI
# ============================================================

if __name__ == "__main__":
    layer_filter = None
    scenario_filter = None
    dry_run = False
    max_workers = 4

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--layer" and i + 1 < len(args):
            layer_filter = args[i + 1]; i += 2
        elif args[i] == "--scenario" and i + 1 < len(args):
            scenario_filter = args[i + 1]; i += 2
        elif args[i] == "--workers" and i + 1 < len(args):
            max_workers = int(args[i + 1]); i += 2
        elif args[i] == "--dry-run":
            dry_run = True; i += 1
        elif args[i] == "--help":
            print("Phase 2 LLM 评估")
            print("  --layer quantum|topology|simulation  按层过滤")
            print("  --scenario q_scf_001               单场景测试")
            print("  --workers 4                        并行线程数")
            print("  --dry-run                          仅估算 token")
            sys.exit(0)
        else:
            i += 1

    scenarios = SCENARIOS
    if layer_filter:
        scenarios = [s for s in scenarios if s.layer == layer_filter]
    if scenario_filter:
        scenarios = [s for s in scenarios if s.scenario_id == scenario_filter]

    if not scenarios:
        print("⚠ 无匹配的场景")
        sys.exit(1)

    results = run_phase2(scenarios, max_workers=max_workers,
                         dry_run=dry_run)
    if results:
        print_phase2_report(results)
        failed = sum(1 for r in results if not r.passed)
        if failed > 0:
            sys.exit(1)
