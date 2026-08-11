"""Topology Agent tools constrained to the current run manifest."""

from __future__ import annotations

import json
from pathlib import Path

from willy.errors import DiagnosisResult, ErrorKind, StepError, StepResult
from willy.config_store import write_json
from willy.topology.backends import (
    OplsaaBackend,
    SobtopBackend,
    TopologyComponent,
    normalize_topology_config,
)
from willy.topology.manifest import (
    claim_retry,
    manifest_lock,
    TopologyManifestComponent,
    component_for_name,
    load_manifest,
    manifest_exists,
    write_manifest,
)


TOPOLOGY_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "tools_retry_topo_gaff",
            "description": "仅对当前 manifest 中的 Sobtop GAFF+UFF 组件重试。",
            "parameters": {
                "type": "object",
                "properties": {"molecule_name": {"type": "string"}},
                "required": ["molecule_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tools_retry_topo_opls",
            "description": "仅对当前 manifest 中的 OPLS-AA 组件重试。",
            "parameters": {
                "type": "object",
                "properties": {"molecule_name": {"type": "string"}},
                "required": ["molecule_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tools_retry_top_assembly",
            "description": "基于当前 run_dir 的拓扑 manifest 重试主拓扑组装。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tools_diagnose_error_topology",
            "description": "结合当前 manifest 和原始输出诊断拓扑失败。",
            "parameters": {
                "type": "object",
                "properties": {
                    "error_source": {"type": "string", "enum": ["sobtop", "ligpargen", "top_assembly", "itp_revise"]},
                    "molecule_name": {"type": "string"},
                    "raw_output": {"type": "string"},
                },
                "required": ["error_source", "raw_output"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tools_modify_config_topology",
            "description": "修改当前运行快照的合法拓扑配置；后端切换必须新建运行。",
            "parameters": {
                "type": "object",
                "properties": {
                    "backend": {"type": "string", "enum": ["sobtop", "oplsaa"]},
                    "force_field": {"type": "string", "enum": ["gaff_uff", "oplsaa"]},
                    "default_lbcc": {"type": "boolean"},
                    "default_opt_steps": {"type": "integer", "enum": [0, 1, 2, 3]},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tools_skip_molecule_topology",
            "description": "当前已禁用。拓扑层不会在未原子同步 manifest/residues 时跳过分子。",
            "parameters": {
                "type": "object",
                "properties": {"molecule_name": {"type": "string"}, "reason": {"type": "string"}},
                "required": ["molecule_name"],
            },
        },
    },
]

TOOL_META = {
    "tools_retry_topo_gaff": {"category": "action", "mutating": True, "risk": "high", "layer": "topology", "effect": "retry_safe"},
    "tools_retry_topo_opls": {"category": "action", "mutating": True, "risk": "high", "layer": "topology", "effect": "retry_safe"},
    "tools_retry_top_assembly": {"category": "action", "mutating": True, "risk": "medium", "layer": "topology", "effect": "retry_safe"},
    "tools_diagnose_error_topology": {"category": "diagnostic", "mutating": False, "risk": "low", "layer": "topology", "effect": "read_only"},
    "tools_modify_config_topology": {"category": "config", "mutating": True, "risk": "medium", "layer": "topology", "effect": "requires_fork"},
    "tools_skip_molecule_topology": {"category": "disabled", "mutating": False, "risk": "none", "layer": "topology", "effect": "read_only", "enabled": False},
}


def _tool_failure(message: str, kind: ErrorKind = ErrorKind.CONFIG_INVALID) -> str:
    return json.dumps(StepResult(
        step_name="topology_tool", step_index=4, success=False,
        error=StepError(kind, message),
    ).to_dict(), ensure_ascii=False)


def _workspace(work_dir: str | None) -> Path:
    if not work_dir:
        raise ValueError("Topology Agent 必须提供本次 run_dir")
    return Path(work_dir)


def _component_from_manifest(entry: dict) -> TopologyComponent:
    return TopologyComponent(
        molecule_id=entry["molecule_id"], residue_name=entry["residue_name"], quantity=int(entry["quantity"]),
        mol2=Path(entry["mol2"]), chg=Path(entry["chg"]) if entry.get("chg") else None,
        charge=int(entry.get("charge", 0)), spin=int(entry.get("spin", 1)), smiles=entry.get("smiles"),
        lbcc=bool(entry.get("lbcc", False)), opt_steps=int(entry.get("opt_steps", 0)),
    )


def _write_manifest_dict(workspace: Path, manifest: dict) -> None:
    components = [TopologyManifestComponent(**entry) for entry in manifest["components"]]
    write_manifest(workspace, backend=manifest["backend"], forcefield_family=manifest["forcefield_family"],
                   components=components, retry_ledger=manifest.get("retry_ledger", {}))


def _retry_backend(workspace: Path, molecule_name: str, backend_name: str) -> StepResult:
    try:
        with manifest_lock(workspace):
            manifest = load_manifest(workspace)
            entry = component_for_name(manifest, molecule_name)
            if entry is None:
                return StepResult("topology_tool", 4, False, error=StepError(ErrorKind.CONFIG_INVALID, f"manifest 中没有 {molecule_name}"))
            if manifest.get("backend") != backend_name or entry.get("backend") != backend_name:
                return StepResult(
                    "topology_tool", 4, False,
                    error=StepError(
                        ErrorKind.CONFIG_INVALID,
                        "当前运行禁止跨后端重试；若需 OPLS-AA，请从配置快照派生一个新的 OPLS-AA run。",
                    ),
                )
            granted, reason = claim_retry(
                manifest,
                backend=backend_name,
                molecule_id=entry["molecule_id"],
                action="parameterize",
            )
            if not granted:
                return StepResult(
                    "topology_tool", 4, False,
                    error=StepError(ErrorKind.RETRY_LIMIT_EXCEEDED, reason),
                )
            _write_manifest_dict(workspace, manifest)

            backend = SobtopBackend() if backend_name == "sobtop" else OplsaaBackend()
            component = _component_from_manifest(entry)
            result = backend.parameterize(component, workspace)
            if result.success:
                result = backend.validate(component, result.outputs)
            if result.success:
                entry.update({"itp": result.outputs["itp"], "assembly_itp": None, "gro": result.outputs["gro"], "success": True, "validated": True, "error": ""})
            else:
                entry.update({"itp": None, "assembly_itp": None, "gro": None, "success": False, "validated": False,
                              "error": result.error.message if result.error else "重试失败"})
            _write_manifest_dict(workspace, manifest)
            return result
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return StepResult("topology_tool", 4, False, error=StepError(ErrorKind.FILE_NOT_FOUND, f"无法读取 manifest: {exc}"))


def handle_topology_tool_call(
    tool_name: str,
    args: dict,
    work_dir: str | None = None,
    config_path: str | None = None,
) -> str:
    """Dispatch topology tools without accepting arbitrary input/output paths."""
    if tool_name not in {tool["function"]["name"] for tool in TOPOLOGY_TOOLS}:
        return json.dumps({"error": f"未知工具: {tool_name}"}, ensure_ascii=False)
    workspace = Path(work_dir) if work_dir else None

    if tool_name == "tools_diagnose_error_topology":
        raw = args.get("raw_output", "")
        entry = None
        if workspace is not None and args.get("molecule_name") and manifest_exists(workspace):
            try:
                entry = component_for_name(load_manifest(workspace), args["molecule_name"])
            except (OSError, json.JSONDecodeError, ValueError):
                pass
        issues: list[str] = []
        hint = "检查 manifest 中记录的本次 .mol2/.chg 和后端日志。"
        lower = raw.lower()
        if "atomtype" in lower:
            issues.append("atomtype 缺失或参数冲突")
        if "rc=24" in lower or "fortran" in lower:
            issues.append("Sobtop rc=24 仅在 ITP/GRO 完整并通过校验时可接受")
        if "cannot open" in lower:
            issues.append("后端无法读取 manifest 指定的输入文件")
        if entry and not entry.get("chg") and args["error_source"] == "sobtop":
            issues.append("Sobtop 组件缺少 .chg")
        if not issues:
            issues.append("未识别特定模式；请检查原始输出")
        result = DiagnosisResult(source="topology", severity="error", issues=issues, hint=hint,
                                 extra={"manifest_component": entry or {}})
        return json.dumps(result.to_dict(), ensure_ascii=False)

    try:
        workspace = _workspace(work_dir)
    except ValueError as exc:
        return _tool_failure(str(exc))
    active_config = Path(config_path) if config_path else workspace / "config.json"

    if tool_name == "tools_retry_topo_gaff":
        return json.dumps(_retry_backend(workspace, args["molecule_name"], "sobtop").to_dict(), ensure_ascii=False)
    if tool_name == "tools_retry_topo_opls":
        return json.dumps(_retry_backend(workspace, args["molecule_name"], "oplsaa").to_dict(), ensure_ascii=False)
    if tool_name == "tools_retry_top_assembly":
        from willy.topology.top_assembly import build
        try:
            with manifest_lock(workspace):
                manifest = load_manifest(workspace)
                granted, reason = claim_retry(
                    manifest,
                    backend=str(manifest.get("backend", "unknown")),
                    molecule_id="__topology__",
                    action="assembly",
                )
                if not granted:
                    return _tool_failure(reason, ErrorKind.RETRY_LIMIT_EXCEEDED)
                _write_manifest_dict(workspace, manifest)
                return json.dumps(build(config_path=str(active_config), topo_dir=str(workspace)).to_dict(), ensure_ascii=False)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            return _tool_failure(f"无法读取 manifest: {exc}", ErrorKind.FILE_NOT_FOUND)
    if tool_name == "tools_modify_config_topology":
        try:
            config = json.loads(active_config.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            return json.dumps({"ok": False, "error": f"无法读取 config.json: {exc}"}, ensure_ascii=False)
        topology = dict(config.get("topology", {}))
        for field in ("backend", "force_field", "default_lbcc", "default_opt_steps"):
            if field in args:
                topology[field] = args[field]
        normalized, issues, _ = normalize_topology_config(topology)
        if issues:
            return json.dumps({"ok": False, "error": "; ".join(issues)}, ensure_ascii=False)
        if manifest_exists(workspace):
            try:
                with manifest_lock(workspace):
                    manifest = load_manifest(workspace)
                    if (normalized["backend"], normalized["force_field"]) != (
                        manifest.get("backend"), manifest.get("forcefield_family"),
                    ):
                        return json.dumps({
                            "ok": False,
                            "error": "当前运行不能切换 forcefield_family；请派生一个新的 OPLS-AA 或 Sobtop run。",
                        }, ensure_ascii=False)
                    for entry in manifest.get("components", []):
                        if "default_lbcc" in args:
                            entry["lbcc"] = normalized["default_lbcc"]
                        if "default_opt_steps" in args:
                            entry["opt_steps"] = normalized["default_opt_steps"]
                    _write_manifest_dict(workspace, manifest)
            except (OSError, json.JSONDecodeError, ValueError) as exc:
                return json.dumps({"ok": False, "error": f"无法读取 manifest: {exc}"}, ensure_ascii=False)
        config["topology"] = normalized
        write_json(active_config, config)
        return json.dumps({"ok": True, "current_topology": normalized}, ensure_ascii=False)
    if tool_name == "tools_skip_molecule_topology":
        return json.dumps({
            "ok": False,
            "error": "Topology skip_molecule 已禁用：无法原子同步 manifest 与 residues 时不得跳过。",
        }, ensure_ascii=False)
    return json.dumps({"error": f"未知工具: {tool_name}"}, ensure_ascii=False)
