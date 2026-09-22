#!/usr/bin/env python3
"""Generate the versioned test-case catalog from collected pytest node IDs."""

from __future__ import annotations

import ast
import argparse
from collections import defaultdict
from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "tests" / "reports" / "test_case_catalog.md"
NODE_ID = re.compile(r"^tests/(?:[^:]+/)*test_[^:]+\.py(?:::.+)+$")
COLLECTION_SUMMARY = re.compile(r"^(\d+) tests collected")

CATEGORIES = {
    "A": {
        "title": "UI 与前端交互",
        "files": {"test_app_ui.py", "test_frontend_api.py", "test_browser_workflow.py", "test_e2e_gate.py", "test_smd_api.py"},
        "description": "FastAPI/React 工作台、确认式操作、可视化、审计日志、状态展示、公开错误边界与浏览器验收。",
    },
    "B": {
        "title": "环境与基础错误模型",
        "files": {
            "test_dependency_preflight.py", "test_env_checker.py", "test_env_registry.py", "test_python_runtime.py",
            "test_vendor_manifest.py", "test_errors.py", "test_bootstrap_script.py",
        },
        "description": "依赖发现、配置页依赖预检、环境变量优先级、vendor 完整性/发布证据、能力报告脱敏与结构化错误协议。",
    },
    "C": {
        "title": "配置与全局工具",
        "files": {"test_workflow_config.py", "test_toolist_global.py", "test_structure_uploads.py", "test_smd_plan.py", "test_charge_scale_config.py"},
        "description": "config v2、迁移、分子知识库、结构上传规范化与 Layer 0 工具 schema/handler。",
    },
    "D": {
        "title": "Agent 与 LLM 行为",
        "files": {
            "test_action_contract.py", "test_agent_config.py", "test_layer_agent.py",
            "test_llm_budget.py", "test_llm_config.py", "test_llm_eval_report.py",
            "test_llm_eval_matrix.py", "test_llm_eval_live_report.py",
            "test_llm_eval_live_config_report.py",
            "test_llm_eval_live_protocol_report.py",
        },
        "description": "OpenAI-compatible 配置、LayerAgent 的上下文、受控动作、预算、重试、升级、历史 mock 场景与离线 LLM 测试矩阵。",
    },
    "E": {
        "title": "量子与跨层 Tool 契约",
        "files": {
            "test_log_parsers.py", "test_struct_orca.py", "test_toolist_quantum_topology.py",
            "test_quantum_input_audit.py", "test_multiwfn_bundled.py",
            "test_smd_solvents.py",
            "test_charge_generation.py",
        },
        "description": "量子/引擎日志解析、ORCA 结构产物、量子工具、后端原始输入审计与跨层 tool 返回协议。",
    },
    "F": {
        "title": "拓扑后端与组装",
        "files": {"test_top_assembly.py", "test_topology_contract.py", "test_itp_namespace.py"},
        "description": "Sobtop/OPLS 后端、manifest、重试账本、ITP 校验和主拓扑组装。",
    },
    "G": {
        "title": "模拟执行、ETA 与后处理",
        "files": {
            "test_mdrun_eta.py",
            "test_postprocess.py",
            "test_simulation_execution.py",
            "test_eq_acceptance.py",
            "test_gmx_process.py",
            "test_simulation_protocol.py",
            "test_toolist_simulation.py",
            "test_mdrun_knowledge.py",
            "test_stage_visualization.py",
        },
        "description": "EM/EQ/PROD 协议、GROMACS 适配、ETA、阶段产物和分析流程。",
    },
    "H": {
        "title": "流水线编排与状态机",
        "files": {
            "test_pending_action.py", "test_pipeline_orchestrator.py", "test_pipeline_state.py",
            "test_multi_factor_pending_action.py",
            "test_recovery_contracts.py", "test_recovery_policy.py", "test_step_registry.py",
            "test_structured_log.py", "test_structured_log_integration.py",
        },
        "description": "步骤构建、唯一步骤注册、失败修复、run workspace、公开状态和恢复边界。",
    },
    "I": {
        "title": "运行管理、启动控制与运行助理",
        "files": {
            "test_pipeline_launch.py", "test_process_lifecycle.py", "test_prune_runs.py",
            "test_run_assistant.py", "test_run_metadata.py", "test_run_provenance.py", "test_run_store.py",
            "test_run_control.py",
            "test_resume_admission.py", "test_run_faults.py",
            "test_step_contracts.py", "test_input_declaration.py", "test_controlled_launch.py",
            "test_branching.py",
            "test_charge_scale_branches.py",
            "test_proposal_workspace.py",
            "test_remote_execution.py", "test_remote_registry.py",
        },
        "description": "运行预留、远程 profile/transport、互斥启动、进程生命周期、保留清理、审计、provenance、RunStore 与只读助理。",
    },
    "J": {
        "title": "外部 Smoke 与发布证据",
        "files": {
            "test_external_smoke.py", "test_external_profile_evidence.py", "test_batch_report.py",
            "test_release_baseline.py", "test_release_staging.py", "test_documentation_consistency.py",
        },
        "description": "外部工具预检、四条 profile 的只读终态证据、fixture 完整性、脱敏批次报告、证据归档和 required 发布门禁。",
    },
    "K": {
        "title": "托管 LLM 网关归档",
        "files": {
            "test_gateway.py", "test_gateway_admin.py", "test_gateway_offline_acceptance.py",
            "test_gateway_archive.py", "test_managed_gateway.py", "test_managed_gateway_bundle.py",
        },
        "description": "为后续版本保留的服务端限额设备申请、客户端私钥、短期令牌、签名/nonce、防重放、模型白名单、额度账本、管理员控制面、启动冻结和离线 fake-upstream 脱敏边界。",
    },
}

FILE_TARGETS = {
    "test_smd_api.py": "app.py Gaussian 溶剂库查询与登记接口",
    "test_smd_plan.py": "willy.agent_config / workflow_config 溶剂方案、快照和后端边界",
    "test_smd_solvents.py": "willy.quantum.smd_solvents 溶剂库、Gaussian 渲染与 Generic 验收证据",
    "test_charge_generation.py": "willy.quantum.chg_resp / charge_files 原始与有效电荷产物",
    "test_charge_scale_config.py": "willy.charge_scaling / agent_config 电荷缩放配置与方案",
    "test_charge_scale_branches.py": "willy.branching 电荷缩放独立分支与失效范围",
    "test_app_ui.py": "app.py FastAPI 工作台 API 契约",
    "test_browser_workflow.py": "React 生产构建与浏览器验收",
    "test_agent_config.py": "willy.agent_config",
    "test_action_contract.py": "willy.action_contract",
    "test_env_checker.py": "willy.env_checker",
    "test_dependency_preflight.py": "willy.dependency_preflight 配置页依赖预检",
    "test_python_runtime.py": "willy.python_runtime 父解释器与 Web 运行时契约",
    "test_env_registry.py": "willy.env_registry",
    "test_bootstrap_script.py": "scripts.bootstrap project-local installer",
    "test_vendor_manifest.py": "willy.vendor_manifest / bundled vendor release inventory",
    "test_errors.py": "willy.errors",
    "test_frontend_api.py": "willy.frontend_api",
    "test_layer_agent.py": "willy.layer_agent.LayerAgent",
    "test_llm_budget.py": "willy.llm_budget",
    "test_llm_config.py": "willy.llm_config OpenAI-compatible 配置",
    "test_llm_eval_report.py": "tests.llm_eval 脱敏基线报告",
    "test_llm_eval_matrix.py": "tests.llm_eval.matrix 离线 LLM 测试矩阵",
    "test_llm_eval_live_report.py": "tests.llm_eval.live_runner 脱敏实时评测报告",
    "test_llm_eval_live_config_report.py": "tests.llm_eval.live_config_runner 脱敏配置实时评测报告",
    "test_llm_eval_live_protocol_report.py": "tests.llm_eval.live_protocol_runner 脱敏工具协议实测报告",
    "test_workflow_config.py": "willy.workflow_config 与 MD 配置协议",
    "test_structure_uploads.py": "willy.structure_uploads 量子输入上传规范化",
    "test_log_parsers.py": "willy.log_parsers",
    "test_struct_orca.py": "willy.quantum.struct_orca",
    "test_mdrun_eta.py": "willy.simulation.mdrun_eta",
    "test_gmx_process.py": "willy.simulation.gmx_process 辅助命令时限、重试与回收",
    "test_pipeline_launch.py": "willy.pipeline_launch",
    "test_prune_runs.py": "scripts.prune_runs",
    "test_pipeline_orchestrator.py": "willy.pipeline_orchestrator",
    "test_pipeline_state.py": "willy.pipeline_state",
    "test_process_lifecycle.py": "willy.process_lifecycle",
    "test_pending_action.py": "willy.simulation.pending_action",
    "test_multi_factor_pending_action.py": "willy.simulation.pending_action multi-factor confirmation",
    "test_postprocess.py": "willy.simulation.postprocess",
    "test_run_assistant.py": "willy.run_registry / toolist_run / agent_run",
    "test_run_control.py": "willy.run_control / frontend_api controlled resume, fork and switch",
    "test_resume_admission.py": "willy.resume_admission / stable same-run inputs and hash admission",
    "test_step_contracts.py": "willy.step_contracts / whole-pipeline input hashes and old-output archives",
    "test_input_declaration.py": "willy.input_declaration / LLM-scoped input adoption and format checks",
    "test_controlled_launch.py": "willy.controlled_launch / fail-closed process start gate and cleanup",
    "test_branching.py": "willy.branching / independent full-context snapshots and inherited evidence",
    "test_proposal_workspace.py": "willy.proposal_workspace durable plan/temp workspace lifecycle",
    "test_run_metadata.py": "willy.run_metadata schema-v2 / migration / section CAS",
    "test_run_provenance.py": "willy.run_provenance",
    "test_run_store.py": "willy.run_store",
    "test_run_faults.py": "willy.run_faults / fault evidence and refresh reconciliation",
    "test_remote_execution.py": "willy.remote_execution",
    "test_remote_registry.py": "willy.remote_registry",
    "test_recovery_contracts.py": "willy.action_contract / recovery contract",
    "test_recovery_policy.py": "willy.recovery_policy",
    "test_step_registry.py": "willy.step_registry",
    "test_structured_log.py": "willy.structured_log",
    "test_structured_log_integration.py": "willy.structured_log integration",
    "test_simulation_execution.py": "willy.simulation 执行适配层",
    "test_eq_acceptance.py": "willy.simulation.eq_acceptance 五段覆盖、有效数值与 PROD 许可",
    "test_simulation_protocol.py": "willy.simulation.protocol / manifest",
    "test_toolist_global.py": "willy.toolist_global",
    "test_toolist_quantum_topology.py": "willy.toolist_quantum / toolist_topology",
    "test_quantum_input_audit.py": "willy.quantum.input_audit / backend-specific raw inputs",
    "test_multiwfn_bundled.py": "bundled Multiwfn 运行契约",
    "test_toolist_simulation.py": "willy.toolist_simulation",
    "test_mdrun_knowledge.py": "willy.simulation.mdrun_knowledge / SimulationAgent knowledge boundary",
    "test_stage_visualization.py": "willy.simulation.visualization",
    "test_top_assembly.py": "willy.topology.top_assembly",
    "test_itp_namespace.py": "willy.topology.itp_namespace / OPLS-AA assembly namespace",
    "test_topology_contract.py": "willy.topology 后端与产物契约",
    "test_external_smoke.py": "willy.external_smoke / self-hosted CI gate",
    "test_external_profile_evidence.py": "willy.external_profile_evidence / four-profile terminal evidence",
    "test_batch_report.py": "tests.reporting.batch_report",
    "test_documentation_consistency.py": "documentation acceptance consistency gate",
    "test_release_baseline.py": "tests.tools.release_baseline isolated release gate",
    "test_release_staging.py": "scripts.build_release_staging / approved vendor staging",
    "test_e2e_gate.py": "tests.e2e browser gate policy",
    "test_gateway.py": "willy_gateway FastAPI/SQLite gateway contract",
    "test_gateway_admin.py": "willy_gateway loopback administrator control plane",
    "test_gateway_offline_acceptance.py": "willy_gateway 离线 fake-upstream 验收",
    "test_gateway_archive.py": "willy_gateway 后续版本归档启动边界",
    "test_managed_gateway.py": "willy.managed_gateway device identity and token-refreshing client",
    "test_managed_gateway_bundle.py": "willy.managed_gateway_bundle release client packaging",
}

FILE_CATEGORY = {
    file_name: category_id
    for category_id, category in CATEGORIES.items()
    for file_name in category["files"]
}


def _one_line(value: str) -> str:
    return " ".join(value.split()).replace("|", "\\|")


def _collect_node_ids() -> list[str]:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stdout + result.stderr)

    node_ids = [line.strip() for line in result.stdout.splitlines() if NODE_ID.fullmatch(line.strip())]
    summary_count = next(
        (int(match.group(1)) for line in result.stdout.splitlines() if (match := COLLECTION_SUMMARY.match(line))),
        None,
    )
    if summary_count is None or summary_count != len(node_ids):
        raise RuntimeError(
            f"pytest collection mismatch: summary={summary_count}, parsed={len(node_ids)}"
        )
    return node_ids


def _source_index(file_path: str) -> dict[tuple[str, str], tuple[str, str]]:
    path = ROOT / file_path
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    entries: dict[tuple[str, str], tuple[str, str]] = {}

    def register(node: ast.FunctionDef | ast.AsyncFunctionDef, class_name: str = "") -> None:
        if node.name.startswith("test_"):
            entries[(class_name, node.name)] = (
                ast.get_docstring(node) or "",
                ast.get_source_segment(path.read_text(encoding="utf-8"), node) or "",
            )

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            register(node)
        elif isinstance(node, ast.ClassDef):
            for member in node.body:
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    register(member, node.name)
    return entries


def _content(function_name: str, docstring: str, parameter: str) -> str:
    if docstring:
        summary = _one_line(docstring).split("。", maxsplit=1)[0]
        text = f"验证：{summary}"
    else:
        text = f"验证 `{function_name.removeprefix('test_')}` 行为"
    if parameter:
        text += f"；参数集 `{parameter}`"
    return text


def _method(source: str, parameter: str) -> str:
    methods: list[str] = []
    if "pytest.mark.external" in source:
        methods.append("真实 external smoke")
    if parameter:
        methods.append("pytest 参数化")
    if "tmp_path" in source:
        methods.append("tmp_path 隔离工作区")
    if "monkeypatch" in source:
        methods.append("monkeypatch")
    if any(token in source for token in ("patch(", "MagicMock", "mock_")):
        methods.append("mock/patch")
    if "pytest.raises" in source:
        methods.append("异常断言")
    if not methods:
        methods.append("进程内行为断言")
    return " + ".join(methods)


def _pytest_rows(node_ids: list[str]) -> dict[str, list[tuple[str, str, str, str]]]:
    cache: dict[str, dict[tuple[str, str], tuple[str, str]]] = {}
    rows: dict[str, list[tuple[str, str, str, str]]] = defaultdict(list)

    for number, node_id in enumerate(node_ids, start=1):
        parts = node_id.split("::")
        file_path = parts[0]
        file_name = Path(file_path).name
        class_name = parts[-2] if len(parts) == 3 else ""
        function_with_parameter = parts[-1]
        function_name, separator, parameter = function_with_parameter.partition("[")
        parameter = parameter[:-1] if separator else ""
        category = FILE_CATEGORY.get(file_name)
        if category is None:
            raise RuntimeError(f"No catalog category for {file_name}")
        if file_path not in cache:
            cache[file_path] = _source_index(file_path)
        docstring, source = cache[file_path].get((class_name, function_name), ("", ""))
        target = FILE_TARGETS[file_name]
        if class_name:
            target += f" / {class_name}"
        rows[category].append(
            (f"T{number:03d}", node_id, target, _content(function_name, docstring, parameter), _method(source, parameter))
        )
    return rows


def _llm_rows() -> list[tuple[str, str, str, str, str]]:
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "src"))
    from tests.llm_eval.scenarios import SCENARIOS

    rows = []
    for number, scenario in enumerate(SCENARIOS, start=1):
        rows.append(
            (
                f"L{number:03d}",
                f"LLM Eval::{scenario.scenario_id}",
                f"LayerAgent / {scenario.layer}",
                f"注入 `{scenario.injected_error_kind.value}`：{scenario.description}",
                "预录 MockLLMResponse + mock tool executor + 评分阈值",
            )
        )
    return rows


def _render_table(rows: list[tuple[str, str, str, str, str]]) -> list[str]:
    lines = ["| 编号 | 执行 ID | 测试对象 | 测试内容 | 测试方式 |", "|---|---|---|---|---|"]
    for row in rows:
        lines.append("| " + " | ".join(_one_line(cell) for cell in row) + " |")
    return lines


def main(output: Path | None = None) -> None:
    node_ids = _collect_node_ids()
    pytest_rows = _pytest_rows(node_ids)
    llm_rows = _llm_rows()
    total_rows = len(node_ids) + len(llm_rows)

    lines = [
        "# Willy 测试用例集",
        "",
        "> 维护角色：6 号测试工程师",
        f"> 来源：`python3 -m pytest --collect-only -q` 收集到 {len(node_ids)} 条 pytest 用例；另有 {len(llm_rows)} 条离线 LLM mock eval 场景；本台账共 {total_rows} 条记录。",
        "",
        "## 使用说明",
        "",
        "- `T001` 起的记录与 pytest node ID 一一对应；参数化变体按独立用例编号。",
        "- `L001` 起的记录是 `tests.llm_eval.scenarios` 中的受控 LLM 行为场景，不计入 pytest 收集数。",
        "- 测试方式由用例源码中的参数化、`tmp_path`、`monkeypatch`、mock/patch、异常断言与 external 标记归纳；详细断言以 node ID 对应源码为准。",
        "- 重新生成：`python3 -m tests.tools.generate_test_case_catalog --output tests/reports/test_case_catalog.md`。每次增删测试或 LLM 场景后必须重新生成并核对总数。",
        "",
        "## 分类总览",
        "",
        "| 分类 | 记录数 | 覆盖范围 |",
        "|---|---:|---|",
    ]

    for category_id, category in CATEGORIES.items():
        count = len(pytest_rows[category_id]) + (len(llm_rows) if category_id == "D" else 0)
        lines.append(f"| {category_id} | {count} | {category['title']}：{category['description']} |")

    for category_id, category in CATEGORIES.items():
        lines.extend(["", f"## {category_id}. {category['title']}", "", category["description"], ""])
        category_rows = list(pytest_rows[category_id])
        if category_id == "D":
            category_rows.extend(llm_rows)
        lines.extend(_render_table(category_rows))

    lines.append("")
    target = (output or OUTPUT).resolve()
    try:
        target.relative_to(ROOT)
    except ValueError as exc:
        raise RuntimeError("catalog output must remain inside the repository") from exc
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {target.relative_to(ROOT)}: {len(node_ids)} pytest + {len(llm_rows)} LLM = {total_rows} records")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="repository-relative output path")
    args = parser.parse_args()
    main(args.output)
