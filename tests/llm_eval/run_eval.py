"""
run_eval.py —— 批量评估入口。

运行所有 18 个场景, 生成汇总报告。

用法:
  python3 -m tests.llm_eval.run_eval
  python3 -m tests.llm_eval.run_eval --layer quantum     # 仅测试某一层
  python3 -m tests.llm_eval.run_eval --verbose           # 显示每个场景的轨迹
"""

from __future__ import annotations
import json
import sys
import tempfile
from pathlib import Path

from .scenarios import SCENARIOS
from .harness import AgentHarness
from .scorer import Scorer, EvalResult


def run_all_scenarios(layer_filter: str = None,
                      verbose: bool = False) -> list[EvalResult]:
    """运行所有（或过滤后的）场景, 返回评估结果列表。"""
    scenarios = SCENARIOS
    if layer_filter:
        scenarios = [s for s in SCENARIOS if s.layer == layer_filter]
        if not scenarios:
            print(f"⚠ 未找到 layer='{layer_filter}' 的场景")
            return []

    results: list[EvalResult] = []
    scorer = Scorer()

    for i, scenario in enumerate(scenarios, 1):
        print(f"\r[{i}/{len(scenarios)}] {scenario.scenario_id} ...", end="", flush=True)

        with tempfile.TemporaryDirectory() as tmp:
            harness = AgentHarness(scenario, Path(tmp))
            trace = harness.run()
            result = scorer.score(trace, scenario)
            results.append(result)

        if verbose:
            _print_trace(trace)

    print(f"\r{' ' * 60}\r", end="")  # 清除进度行
    return results


def print_report(results: list[EvalResult]):
    """打印结构化评估报告。"""
    if not results:
        return

    # ── 按层分组 ──
    layers = {"quantum": [], "topology": [], "simulation": []}
    for r in results:
        layers[r.layer].append(r)

    print()
    print("═" * 72)
    print("  LLM Agent 错误处理评估报告 (Phase 1 — Mock Mode)")
    print("═" * 72)

    for layer_name, layer_results in layers.items():
        if not layer_results:
            continue

        layer_label = {
            "quantum": "Layer 1: Quantum Agent (量子化学)",
            "topology": "Layer 2: Topology Agent (力场拓扑)",
            "simulation": "Layer 3: Simulation Agent (MD 模拟)",
        }[layer_name]

        # ── 表头 ──
        print(f"\n  {layer_label}")
        print("  ┌" + "─" * 14 + "┬" + "─" * 5 + "┬" + "─" * 5
              + "┬" + "─" * 5 + "┬" + "─" * 5 + "┬" + "─" * 6 + "┬" + "─" * 6 + "┐")
        print("  │ Scenario      │ Diag│ Tool│Repr │ Esc │ Total│ Status │")
        print("  ├" + "─" * 14 + "┼" + "─" * 5 + "┼" + "─" * 5
              + "┼" + "─" * 5 + "┼" + "─" * 5 + "┼" + "─" * 6 + "┼" + "─" * 6 + "┤")

        # ── 场景行 ──
        for r in layer_results:
            print(f"  {r.format_line()}")

        # ── 分层汇总 ──
        print("  ├" + "─" * 14 + "┼" + "─" * 5 + "┼" + "─" * 5
              + "┼" + "─" * 5 + "┼" + "─" * 5 + "┼" + "─" * 6 + "┼" + "─" * 6 + "┤")

        avg_diag = sum(r.breakdown.diagnosis for r in layer_results) / len(layer_results)
        avg_tool = sum(r.breakdown.tool_choice for r in layer_results) / len(layer_results)
        avg_repair = sum(r.breakdown.repair for r in layer_results) / len(layer_results)
        avg_esc = sum(r.breakdown.escalation for r in layer_results) / len(layer_results)
        avg_total = sum(r.total for r in layer_results) / len(layer_results)
        passed_count = sum(1 for r in layer_results if r.passed)
        layer_total = len(layer_results)

        status_str = f"{passed_count}/{layer_total}"
        print(f"  │ {'Avg':<13s} │ {avg_diag:>3.0f} │ {avg_tool:>3.0f} │ "
              f"{avg_repair:>3.0f} │ {avg_esc:>3.0f} │ {avg_total:>4.0f} │ "
              f"{status_str:>5s} │")
        print("  └" + "─" * 14 + "┴" + "─" * 5 + "┴" + "─" * 5
              + "┴" + "─" * 5 + "┴" + "─" * 5 + "┴" + "─" * 6 + "┴" + "─" * 6 + "┘")

    # ── 全局汇总 ──
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    avg_score = sum(r.total for r in results) / total if total else 0
    avg_d = sum(r.breakdown.diagnosis for r in results) / total if total else 0
    avg_t = sum(r.breakdown.tool_choice for r in results) / total if total else 0
    avg_r = sum(r.breakdown.repair for r in results) / total if total else 0
    avg_e = sum(r.breakdown.escalation for r in results) / total if total else 0

    print(f"\n  ═══════════════════════════════════════════════════════════════")
    print(f"    总场景数: {total}")
    print(f"    通过: {passed}  ({passed/total*100:.0f}%)" if total else "    通过: 0")
    print(f"    总分均值: {avg_score:.1f}/100")
    print(f"    诊断: {avg_d:.1f}/30  │  工具选择: {avg_t:.1f}/25  "
          f"│  修复: {avg_r:.1f}/25  │  升级: {avg_e:.1f}/20")
    print(f"  ═══════════════════════════════════════════════════════════════")

    # ── 失败项详情 ──
    failed_results = [r for r in results if not r.passed]
    if failed_results:
        print(f"\n  🔴 未通过场景详情:")
        for r in failed_results:
            print(f"    • {r.scenario_id}: {r.description[:80]}")
            print(f"      得分: {r.total}/100 "
                  f"(D:{r.breakdown.diagnosis} T:{r.breakdown.tool_choice} "
                  f"R:{r.breakdown.repair} E:{r.breakdown.escalation})")
            for reason in r.failure_reasons:
                print(f"      └─ {reason}")

    # ── 关键发现 ──
    print(f"\n  📊 关键发现:")
    _print_insights(results)


def public_report(results: list[EvalResult]) -> dict[str, object]:
    """Return a bounded report without prompts, traces, tool arguments or logs."""
    by_layer: dict[str, dict[str, object]] = {}
    for layer in ("quantum", "topology", "simulation"):
        grouped = [result for result in results if result.layer == layer]
        if not grouped:
            continue
        by_layer[layer] = {
            "total": len(grouped),
            "passed": sum(1 for result in grouped if result.passed),
            "average_score": round(sum(result.total for result in grouped) / len(grouped), 2),
        }
    return {
        "schema_version": 1,
        "mode": "mock",
        "total": len(results),
        "passed": sum(1 for result in results if result.passed),
        "average_score": round(sum(result.total for result in results) / len(results), 2) if results else 0,
        "layers": by_layer,
        "scenarios": [
            {
                "scenario_id": result.scenario_id,
                "layer": result.layer,
                "passed": result.passed,
                "score": result.total,
                "breakdown": {
                    "diagnosis": result.breakdown.diagnosis,
                    "tool_choice": result.breakdown.tool_choice,
                    "repair": result.breakdown.repair,
                    "escalation": result.breakdown.escalation,
                },
            }
            for result in results
        ],
    }


def write_public_report(path: str | Path, results: list[EvalResult]) -> Path:
    """Atomically write the summary used by release baselines."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_text(json.dumps(public_report(results), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)
    return target


def _print_trace(trace):
    """打印单场景的详细轨迹 (verbose 模式)。"""
    print(f"\n    [{trace.scenario_id}] LLM 调用 {trace.total_llm_calls} 次, "
          f"重试 {trace.retry_count} 次, "
          f"结果: {'✅ 成功' if trace.final_success else '❌ ' + ('升级' if trace.escalated else '失败')}")
    if trace.escalation_info:
        info = trace.escalation_info
        print(f"      升级: layer={info.get('layer')}, "
              f"attempts={info.get('attempts_made')}, "
              f"actions={info.get('actions_tried')}")
    if trace.errors_encountered:
        for err in trace.errors_encountered:
            print(f"      ⚠  异常: {err}")


def _print_insights(results: list[EvalResult]):
    """从结果中提取关键见解。"""
    failed = [r for r in results if not r.passed]

    # 分析各维度弱点
    low_diag = [r for r in results if r.breakdown.diagnosis < 15]
    low_tool = [r for r in results if r.breakdown.tool_choice < 12]
    low_repair = [r for r in results if r.breakdown.repair < 12]
    low_esc = [r for r in results if r.breakdown.escalation < 10]

    if low_diag:
        print(f"    • 诊断薄弱 ({len(low_diag)} 场景): "
              f"Agent 可能未正确调用诊断工具")
    if low_tool:
        print(f"    • 工具选择偏差 ({len(low_tool)} 场景): "
              f"首工具非诊断或使用了不相关的工具")
    if low_repair:
        print(f"    • 修复质量不足 ({len(low_repair)} 场景): "
              f"过早放弃或策略单一")
    if low_esc:
        print(f"    • 升级决策问题 ({len(low_esc)} 场景): "
              f"过早升级或升级信息不完整")

    # 按层分析
    for layer in ["quantum", "topology", "simulation"]:
        layer_results = [r for r in results if r.layer == layer]
        if not layer_results:
            continue
        avg = sum(r.total for r in layer_results) / len(layer_results)
        label = {"quantum": "量子化学", "topology": "拓扑", "simulation": "模拟"}[layer]
        print(f"    • {label}层 平均分: {avg:.1f}/100 "
              f"({sum(1 for r in layer_results if r.passed)}/{len(layer_results)} 通过)")

    if not failed:
        print(f"    • ✅ 所有场景均通过!")


# ============================================================
# 入口
# ============================================================

if __name__ == "__main__":
    layer_filter = None
    verbose = False
    json_output = None

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--layer" and i + 1 < len(args):
            layer_filter = args[i + 1]
            i += 2
        elif args[i] == "--verbose":
            verbose = True
            i += 1
        elif args[i] == "--json-output" and i + 1 < len(args):
            json_output = args[i + 1]
            i += 2
        elif args[i] == "--help":
            print("用法: python3 -m tests.llm_eval.run_eval [--layer quantum|topology|simulation] [--verbose] [--json-output PATH]")
            sys.exit(0)
        else:
            i += 1

    results = run_all_scenarios(layer_filter=layer_filter, verbose=verbose)
    print_report(results)
    if json_output:
        write_public_report(json_output, results)

    # 返回码: 有未通过则 1
    failed = sum(1 for r in results if not r.passed)
    if failed > 0:
        sys.exit(1)
