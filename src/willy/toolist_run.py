"""Read-only tools for the Run Assistant.

Every handler receives a server-selected run_id.  The LLM never supplies a
filesystem path or a different run identifier for a read operation.
"""

from __future__ import annotations

import json
from typing import Any

from willy.errors import ErrorKind
from willy.run_registry import RunRegistry, RunRegistryError


RUN_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "tools_list_runs",
            "description": "列出最近运行的 ID、状态、当前步骤和更新时间。",
            "parameters": {
                "type": "object",
                "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 50}},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tools_get_status_run",
            "description": "读取当前选中运行的最新状态快照。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tools_get_report_step",
            "description": "读取当前运行某一步的公开工序、对象、完成状态和摘要错误。",
            "parameters": {
                "type": "object",
                "properties": {"step_id": {"type": "integer", "minimum": 1, "maximum": 99}},
                "required": ["step_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tools_list_artifacts_run",
            "description": "列出当前运行已登记的产物、所属步骤和存在状态。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tools_explain_error_run",
            "description": "读取当前运行的公开工序进度与摘要错误。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tools_get_config_run",
            "description": "读取当前运行冻结的配置快照。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tools_get_box_parameters_run",
            "description": "读取当前运行最近一次 Packmol 建盒的目标密度、实际盒矢量、体积和质量密度审计记录。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tools_get_environment_run",
            "description": "读取当前运行的脱敏外部软件可用性摘要。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tools_get_md_eta_run",
            "description": "读取当前运行由 GROMACS mdrun -v 报告的 MD 预计结束时间；未产生有效预测时返回受管进程与阶段产物的脱敏运行心跳。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]


TOOL_META = {
    tool["function"]["name"]: {
        "category": "diagnostic",
        "mutating": False,
        "risk": "low",
        "layer": "run",
        "effect": "read_only",
    }
    for tool in RUN_TOOLS
}


def _response(ok: bool, payload: dict[str, Any] | None = None, *, message: str = "", kind: ErrorKind = ErrorKind.UNKNOWN) -> str:
    data: dict[str, Any] = {"ok": ok}
    if payload:
        data.update(payload)
    if not ok:
        data.update({"error": message, "error_kind": kind.value})
    return json.dumps(data, ensure_ascii=False)


def handle_run_tool_call(
    tool_name: str,
    args: dict[str, Any],
    *,
    selected_run_id: str | None,
    registry: RunRegistry | None = None,
) -> str:
    """Handle only whitelisted, read-only operations for one server-selected run."""
    known = {tool["function"]["name"] for tool in RUN_TOOLS}
    if tool_name not in known:
        return _response(False, message=f"未知运行工具: {tool_name}", kind=ErrorKind.CONFIG_INVALID)
    registry = registry or RunRegistry()
    try:
        if tool_name == "tools_list_runs":
            return _response(True, {"runs": registry.list_runs(args.get("limit", 20))})
        if not selected_run_id:
            raise RunRegistryError("请先在界面中选择一个运行")
        if tool_name == "tools_get_status_run":
            return _response(True, {"status": registry.get_run_status(selected_run_id, reconcile=False)})
        if tool_name == "tools_get_report_step":
            return _response(True, {"report": registry.get_step_report(selected_run_id, int(args["step_id"]))})
        if tool_name == "tools_list_artifacts_run":
            return _response(True, {"artifacts": registry.list_artifacts(selected_run_id)})
        if tool_name == "tools_explain_error_run":
            return _response(True, {"error_context": registry.explain_error(selected_run_id)})
        if tool_name == "tools_get_config_run":
            return _response(True, {"config": registry.get_run_config(selected_run_id)})
        if tool_name == "tools_get_box_parameters_run":
            return _response(True, {"box_parameters": registry.get_box_parameters(selected_run_id)})
        if tool_name == "tools_get_environment_run":
            return _response(True, {"environment": registry.get_environment_report(selected_run_id)})
        if tool_name == "tools_get_md_eta_run":
            return _response(True, {"md_eta": registry.get_mdrun_eta(selected_run_id)})
    except (KeyError, TypeError, ValueError, RunRegistryError) as exc:
        return _response(False, message=str(exc), kind=ErrorKind.FILE_NOT_FOUND)
    return _response(False, message=f"未实现运行工具: {tool_name}", kind=ErrorKind.UNKNOWN)
