"""
frontend_api.py
===============
前端专用 API —— 封装所有后端操作（路径、shell、文件系统），
app.py 只通过此模块访问后端，不再直接触碰路径/shell/文件系统。

用法:
  from willy.frontend_api import (
      stop_pipeline, is_pipeline_running,
      get_molecule_catalog, flatten_catalog, resolve_molecule,
      get_molecule_viewer_data, get_run_summary_markdown,
  )
"""

from __future__ import annotations
from collections.abc import Mapping
import json, math, os, re, shutil, subprocess, signal, tempfile, threading, time
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict

from willy._paths import get_project_root
from willy.llm_config import (
    DEFAULT_LLM_BASE_URL,
    DEFAULT_LLM_MODEL,
    LLMConfigError,
    create_openai_client,
    form_llm_settings,
    load_llm_settings,
    validate_llm_values,
)
from willy.run_registry import RunRegistry, RunRegistryError, RunStateConflict
from willy.step_registry import EQ_STEP, STEP_REGISTRY
from willy.pipeline_launch import (
    active_pipeline_run_id,
    cleanup_finished_launch,
    pipeline_launch_is_active,
    reserve_existing_run_launch,
    PipelineLaunchError,
    PipelineLockConflict,
)

ROOT = get_project_root()
_TRANSIENT_RUN_STATES = {"running", "retrying", "stopping"}


class LLMConnectionResult(TypedDict):
    """Redacted result suitable for rendering in the configuration UI."""

    ok: bool
    code: str
    message: str
    suggestion: str


LLM_CONNECTION_TIMEOUT_S = 12.0
_LLM_CONNECTION_TOOL_NAME = "willy_connection_check"
_LLM_CONNECTION_TOOL = {
    "type": "function",
    "function": {
        "name": _LLM_CONNECTION_TOOL_NAME,
        "description": "Confirm that the configured model can invoke an OpenAI-compatible tool.",
        "parameters": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
}
_LLM_CONNECTION_PUBLIC_ERRORS: dict[str, tuple[str, str]] = {
    "invalid_url": (
        "Base URL 格式无效",
        "使用完整 http(s) 地址，不含查询参数。",
    ),
    "invalid_configuration": (
        "LLM 配置格式无效",
        "检查 API Key、Base URL 和 Model，均不能留空或包含换行。",
    ),
    "invalid_test_api_key": (
        "本次连接测试的 API Key 为空或格式无效",
        "测试只使用当前表单值，已保存的 Key 不会回填。请重新粘贴 Key；部分兼容服务还要求 Base URL 以 /v1 结尾，请以服务商文档为准。",
    ),
    "non_json": (
        "服务未返回 OpenAI API 响应",
        "检查 Base URL；常见修复是在末尾追加 /v1。",
    ),
    "authentication": (
        "API Key 无效或无权访问该服务",
        "重新复制 Key，确认账户与模型权限。",
    ),
    "not_found": (
        "未找到 API 端点或模型",
        "检查 /v1 路径和 Model 名称。",
    ),
    "rate_limited": (
        "服务限流或余额不足",
        "稍后重试，检查配额、并发和账户余额。",
    ),
    "network": (
        "无法连接 LLM 服务",
        "检查网络、代理、防火墙和服务可达性。",
    ),
    "protocol": (
        "服务不兼容 OpenAI Chat Completions",
        "更换 OpenAI-compatible 端点或联系服务提供方。",
    ),
    "tool_call_unsupported": (
        "当前模型不支持 function calling",
        "更换支持工具调用的模型；Willy 的配置与自动修复依赖该能力。",
    ),
}

def get_llm_config_status() -> str:
    """Report LLM configuration state without ever returning a credential."""
    try:
        settings = load_llm_settings(ROOT)
    except LLMConfigError:
        return "OpenAI-compatible LLM 配置无效，请检查 Base URL、Model 和 API Key。"
    if settings is None:
        return "尚未配置 OpenAI-compatible LLM 服务。"
    source = "系统环境变量" if settings.source == "environment" else "本机 .env"
    legacy = "（已兼容旧 DeepSeek 密钥）" if settings.legacy_key else ""
    return f"OpenAI-compatible LLM 已通过{source}配置，模型：{settings.model}。{legacy}"


def get_llm_config_notice() -> str:
    """Return the configuration-page privacy notice without exposing a key."""
    try:
        settings = load_llm_settings(ROOT)
    except LLMConfigError:
        settings = None
    model = settings.model if settings is not None else "当前无可用模型"
    return (
        "Agent制作者不会以任何方式获取您的API-key。"
        "您提供的LLM只会在您电脑本地的.env配置。"
        f"当前模型：*{model}*。"
    )


def save_llm_config(api_key: str, base_url: str, model: str) -> str:
    """Atomically persist generic LLM settings without exposing the credential."""
    try:
        key, url, resolved_model = validate_llm_values(api_key, base_url, model)
    except LLMConfigError as exc:
        return f"LLM 配置无效：{exc}。"

    env_path = ROOT / ".env"
    try:
        existing_lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
        updated_lines: list[str] = []
        managed_keys = {
            "WILLY_LLM_API_KEY",
            "WILLY_LLM_BASE_URL",
            "WILLY_LLM_MODEL",
            "DEEPSEEK_API_KEY",
        }
        for line in existing_lines:
            name = line.split("=", 1)[0].strip() if "=" in line else ""
            if name not in managed_keys:
                updated_lines.append(line)
        updated_lines.extend([
            f"WILLY_LLM_API_KEY={key}",
            f"WILLY_LLM_BASE_URL={url}",
            f"WILLY_LLM_MODEL={resolved_model}",
        ])

        fd, temp_name = tempfile.mkstemp(prefix=".env-", suffix=".tmp", dir=ROOT)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write("\n".join(updated_lines) + "\n")
            os.chmod(temp_name, 0o600)
            os.replace(temp_name, env_path)
        finally:
            Path(temp_name).unlink(missing_ok=True)
    except OSError:
        return "保存失败，请检查项目目录的写入权限。"

    return "OpenAI-compatible LLM 配置已保存到本机 .env。请重启应用后生效。"


def get_api_key_status() -> str:
    """Deprecated compatibility alias for the generic LLM status API."""
    return get_llm_config_status()


def save_deepseek_api_key(api_key: str) -> str:
    """Deprecated alias retaining DeepSeek defaults for programmatic callers."""
    return save_llm_config(api_key, DEFAULT_LLM_BASE_URL, DEFAULT_LLM_MODEL)


def _connection_result(ok: bool, code: str) -> LLMConnectionResult:
    if ok:
        return {
            "ok": True,
            "code": "ok",
            "message": "连接与工具调用可用",
            "suggestion": "测试仅使用当前表单值，未保存任何配置。",
        }
    message, suggestion = _LLM_CONNECTION_PUBLIC_ERRORS[code]
    return {
        "ok": False,
        "code": code,
        "message": message,
        "suggestion": suggestion,
    }


def _object_value(value: object, field: str) -> object | None:
    if isinstance(value, Mapping):
        return value.get(field)
    return getattr(value, field, None)


def _connection_error_status(error: Exception) -> int | None:
    response = _object_value(error, "response")
    for candidate in (error, response):
        status = _object_value(candidate, "status_code")
        if isinstance(status, int):
            return status
    return None


def _connection_error_is_html(error: Exception) -> bool:
    response = _object_value(error, "response")
    headers = _object_value(response, "headers")
    content_type = _object_value(headers, "content-type")
    return isinstance(content_type, str) and "text/html" in content_type.lower()


def _classify_connection_error(error: Exception) -> str:
    """Classify an SDK failure without preserving raw errors or response bodies."""
    if _connection_error_is_html(error):
        return "non_json"

    status = _connection_error_status(error)
    if status in {401, 403}:
        return "authentication"
    if status == 404:
        return "not_found"
    if status == 429:
        return "rate_limited"

    name = error.__class__.__name__.lower()
    if "json" in name or "responsevalidation" in name:
        return "non_json"
    if "authentication" in name or "permission" in name:
        return "authentication"
    if "notfound" in name:
        return "not_found"
    if "ratelimit" in name:
        return "rate_limited"
    if "timeout" in name or "connection" in name or "transport" in name:
        return "network"
    return "protocol"


def _response_has_requested_tool_call(response: object) -> tuple[bool, bool]:
    """Return ``(has_choices, called_requested_tool)`` for SDK or test objects."""
    choices = _object_value(response, "choices")
    if not isinstance(choices, (list, tuple)) or not choices:
        return False, False
    message = _object_value(choices[0], "message")
    tool_calls = _object_value(message, "tool_calls")
    if not isinstance(tool_calls, (list, tuple)):
        return True, False
    for tool_call in tool_calls:
        function = _object_value(tool_call, "function")
        if _object_value(function, "name") == _LLM_CONNECTION_TOOL_NAME:
            return True, True
    return True, False


def test_llm_connection(api_key: str, base_url: str, model: str) -> LLMConnectionResult:
    """Test form values once without persisting credentials or logging internals."""
    try:
        settings = form_llm_settings(api_key, base_url, model)
    except LLMConfigError as error:
        error_message = str(error)
        if error_message.startswith("API Key"):
            code = "invalid_test_api_key"
        elif error_message.startswith("Base URL"):
            code = "invalid_url"
        else:
            code = "invalid_configuration"
        return _connection_result(False, code)

    try:
        client = create_openai_client(settings)
        response = client.chat.completions.create(
            model=settings.model,
            messages=[{
                "role": "user",
                "content": "Call willy_connection_check with an empty object. Do not reply with text.",
            }],
            tools=[_LLM_CONNECTION_TOOL],
            tool_choice={
                "type": "function",
                "function": {"name": _LLM_CONNECTION_TOOL_NAME},
            },
            temperature=0,
            max_tokens=16,
            timeout=LLM_CONNECTION_TIMEOUT_S,
        )
    except Exception as error:
        return _connection_result(False, _classify_connection_error(error))

    has_choices, called_requested_tool = _response_has_requested_tool_call(response)
    if not has_choices:
        return _connection_result(False, "protocol")
    if not called_requested_tool:
        return _connection_result(False, "tool_call_unsupported")
    return _connection_result(True, "ok")


def get_active_run_id() -> str | None:
    """Return only the run ID currently owned by the atomic launch lock."""
    run_id = active_pipeline_run_id(ROOT)
    if run_id:
        return run_id
    if not pipeline_launch_is_active(ROOT):
        return None
    # Pre-lock-handoff runs recorded only a PID.  During this one-way upgrade,
    # use the newest live run snapshot rather than consulting root status.json.
    for item in RunRegistry(ROOT).list_runs():
        if item.get("state") in {"running", "retrying"} and item.get("run_id"):
            return item["run_id"]
    return None


def _kill_process_group():
    """通过进程组 ID 终止流水线及其所有子进程（最可靠方式）。"""
    pid_file = ROOT / ".pipeline.pid"
    if not pid_file.exists():
        return
    try:
        pgid = int(pid_file.read_text().strip())
    except (ValueError, OSError):
        pid_file.unlink(missing_ok=True)
        return

    # 两阶段终止：SIGTERM → 等待 → SIGKILL
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pgid, sig)
        except OSError:
            break  # 进程组已不存在
        if sig == signal.SIGTERM:
            time.sleep(0.5)
    pid_file.unlink(missing_ok=True)


def _legacy_runner_pid_is_alive(pid: int) -> bool:
    """Keep a legacy PID file from reviving an unrelated recycled process."""
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:
        return b"run_pipeline.py" in Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        # Non-Linux systems may not expose procfs.  The legacy fallback remains
        # conservative there; current launches use the stronger lock identity.
        return True


def stop_pipeline(clean: bool = False) -> str:
    """Persist a user stop before asking the target pipeline to exit."""
    run_id = get_active_run_id()
    status = {}
    if run_id:
        try:
            registry = RunRegistry(ROOT)
            status = registry.request_stop(run_id)
        except RunRegistryError:
            status = {}
    if run_id and STEP_REGISTRY.is_simulation_step(status.get("step")):
        from willy.simulation.manifest import request_safe_stop
        run_dir = ROOT / "md_run" / run_id
        if run_dir.is_dir():
            request_safe_stop(run_dir)
            return "已请求安全停止：GROMACS 将优先写入 checkpoint，随后可在指纹一致时恢复。"
    if run_id:
        _kill_process_group()
        return "已请求中止当前流水线。"

    reconciled_run = reconcile_stale_pipeline_state()
    if clean:
        for artifact in ("model.inp", "model.pdb"):
            (ROOT / artifact).unlink(missing_ok=True)
        return "当前没有活动流水线；已清理临时产物。"
    if reconciled_run:
        return "当前没有活动流水线；已将遗留运行标记为中止。"
    return "当前没有运行中的流水线。"


def is_pipeline_running() -> bool:
    """检查是否有活着的流水线进程。"""
    if pipeline_launch_is_active(ROOT):
        return True

    pid_file = ROOT / ".pipeline.pid"
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            if _legacy_runner_pid_is_alive(pid):
                return True
        except ValueError:
            pass
        try:
            pid_file.unlink(missing_ok=True)
        except OSError:
            pass

    # 回退：pgrep 精确匹配
    try:
        result = subprocess.run(
            ['pgrep', '-f', r'run_pipeline\.py'],
            capture_output=True, timeout=2)
        return result.returncode == 0
    except Exception:
        return False


def is_pipeline_alive() -> bool:
    """检查流水线进程是否存活（供运行控制使用）。"""
    if pipeline_launch_is_active(ROOT):
        return True
    pid_file = ROOT / ".pipeline.pid"
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            if _legacy_runner_pid_is_alive(pid):
                return True
        except ValueError:
            pass
        try:
            pid_file.unlink(missing_ok=True)
        except OSError:
            pass
    try:
        result = subprocess.run(
            ['pgrep', '-f', r'run_pipeline\.py'],
            capture_output=True, timeout=2)
        return result.returncode == 0
    except Exception:
        return False


def reconcile_stale_pipeline_state() -> str | None:
    """Finalize only the newest transient run after verified local process exit."""
    if is_pipeline_running():
        return None
    try:
        runs = RunRegistry(ROOT).list_runs(limit=1)
        if not runs:
            return None
        run_id = runs[0].get("run_id")
        if not isinstance(run_id, str):
            return None
        registry = RunRegistry(ROOT)
        status = registry.get_run_status(run_id)
        if status.get("state") not in _TRANSIENT_RUN_STATES:
            return None
        registry.mark_aborted(run_id, event_type="run_aborted_after_process_exit")
        from willy.simulation.manifest import clear_stop_request
        clear_stop_request(registry.resolve_run_id(run_id))
        return run_id
    except (OSError, RunRegistryError):
        return None


def get_latest_run_control_state() -> str | None:
    """Return persisted control state after reconciling a dead latest process."""
    reconcile_stale_pipeline_state()
    run_id = get_active_run_id()
    if run_id is None:
        choices = RunRegistry(ROOT).list_runs(limit=1)
        run_id = choices[0].get("run_id") if choices else None
    if not isinstance(run_id, str):
        return None
    try:
        return str(RunRegistry(ROOT).get_run_status(run_id).get("state", "unknown"))
    except RunRegistryError:
        return None


# ============================================================
# 分子目录
# ============================================================

# 支持的结构文件及其格式
_STRUCT_GLOBS = [
    ("struct", "*.mol2", "mol2"),
]


def _get_charge_map() -> dict[str, int]:
    """从 knowledge_tools registry + config.json 获取分子电荷。"""
    charges: dict[str, int] = {}
    try:
        from willy.toolist_global import _registry
        for name in _registry.get_all_names():
            info = _registry.lookup(name)
            if info:
                charges[name] = info.get("charge", 0)
    except Exception:
        pass
    cfg = ROOT / "config.json"
    if cfg.exists():
        try:
            data = json.loads(cfg.read_text())
            for name, info in data.get("molecules", {}).items():
                if name not in charges:
                    charges[name] = info.get("charge", 0)
        except (json.JSONDecodeError, OSError):
            pass
    return charges


def get_molecule_catalog() -> dict[str, dict[str, str]]:
    """扫描结构文件，按电荷分类。

    Returns: {category_label: {display_name: file_path}}
    """
    charges = _get_charge_map()
    cats: dict[str, dict[str, str]] = {
        "🟢 阳离子": {},
        "🔴 阴离子": {},
        "🔵 溶剂 / 中性分子": {},
    }

    for rel_dir, pattern, fmt in _STRUCT_GLOBS:
        dir_path = ROOT / rel_dir
        if dir_path.exists():
            for p in dir_path.glob(pattern):
                name = p.stem
                if name.endswith("_run"):
                    continue
                chg = charges.get(name, 0)
                if chg > 0:
                    cats["🟢 阳离子"][name] = str(p)
                elif chg < 0:
                    cats["🔴 阴离子"][name] = str(p)
                else:
                    cats["🔵 溶剂 / 中性分子"][name] = str(p)

    # 清理空分类
    return {k: v for k, v in cats.items() if v}


def flatten_catalog(catalog: dict[str, dict[str, str]]) -> list[str]:
    """展平为 Gradio Dropdown 可用的 choice 列表。

    Example: ['🟢 阳离子', '  Li', '  ...']
    """
    choices: list[str] = []
    for cat, mols in catalog.items():
        if not mols:
            continue
        choices.append(cat)
        for name in sorted(mols.keys()):
            choices.append(f"  {name}")
    return choices


def resolve_molecule(choice: str, catalog: dict[str, dict[str, str]]) -> tuple[str | None, str | None]:
    """解析下拉选项，返回 (category, molecule_name)。非分子行返回 (None, None)。"""
    stripped = choice.strip() if choice else ""
    for cat, mols in catalog.items():
        if stripped == cat.strip():
            return (None, None)
        for name in mols:
            if stripped == name or stripped == f"  {name}".strip():
                return (cat, name)
    return (None, None)


# ============================================================
# 3D 查看器
# ============================================================

# 空状态占位（暖灰底色，与可视化区域一致）
_VIEWER_EMPTY = (
    '<div style="width:100%;height:400px;border-radius:6px;background:#eee9e2;'
    'display:flex;align-items:center;justify-content:center;'
    'color:#765f4f;font-size:14px;border:2px dashed #d9d0c6;flex-direction:column;gap:8px">'
    '<span style="font-size:28px">🔬</span>'
    '<span>从下方下拉菜单选择分子查看</span>'
    '</div>'
)


def get_molecule_viewer_data(mol_name: str) -> dict | None:
    """获取分子的 3D 查看器渲染数据。

    Returns:
        {"content": str, "format": "mol2"|"pdb", "atom_count": int, "file_path": str} | None
    """
    catalog = get_molecule_catalog()
    choices = flatten_catalog(catalog)

    if not mol_name or mol_name not in choices:
        return None

    _cat, resolved_name = resolve_molecule(mol_name, catalog)
    if resolved_name is None:
        return None

    # 找到文件路径
    file_path = None
    for cat_mols in catalog.values():
        if resolved_name in cat_mols:
            file_path = cat_mols[resolved_name]
            break

    if file_path is None or not Path(file_path).exists():
        return None

    mol_data = Path(file_path).read_text()
    ext = Path(file_path).suffix.lower()
    fmt = "mol2" if ext == ".mol2" else "pdb"
    atom_count = mol_data.count("ATOM") if mol_data else 0

    return {
        "content": mol_data,
        "format": fmt,
        "atom_count": atom_count,
        "file_path": file_path,
    }


_RUN_VISUALIZATION_FORMATS = {
    ".mol2": "mol2",
    ".pdb": "pdb",
}
_RUN_DIRECTORY_NUMBERS = re.compile(r"\d+")

DEFAULT_SPHERE_SCALE = 0.35
DEFAULT_STICK_RADIUS = 0.22
VIEWER_SPHERE_SCALE_RANGE = (0.15, 0.65)
VIEWER_STICK_RADIUS_RANGE = (0.08, 0.40)


def _run_directory_numeric_key(run_id: str) -> tuple[tuple[int, ...], str]:
    """Sort run directories by their numeric suffixes, then their full name."""
    return tuple(int(value) for value in _RUN_DIRECTORY_NUMBERS.findall(run_id)), run_id


def _visualization_run_id() -> str | None:
    """Resolve the structure source on every refresh or selection.

    The launch lock always wins while it refers to an existing run.  Once the
    lock is absent, directory names, not status timestamps or registry order,
    choose the newest run.  That makes a newly submitted project visible the
    next time the dropdown is opened.
    """
    registry = RunRegistry(ROOT)
    active_run_id = get_active_run_id()
    if isinstance(active_run_id, str):
        try:
            registry.resolve_run_id(active_run_id)
        except RunRegistryError:
            pass
        else:
            return active_run_id

    try:
        candidates = [
            directory.name
            for directory in registry.runs_dir.iterdir()
            if directory.is_dir()
        ]
    except OSError:
        return None

    valid_run_ids: list[str] = []
    for run_id in candidates:
        try:
            registry.resolve_run_id(run_id)
        except RunRegistryError:
            continue
        valid_run_ids.append(run_id)
    return max(valid_run_ids, key=_run_directory_numeric_key, default=None)


def _current_run_visualization_artifacts() -> list[dict[str, object]]:
    """List readable PDB/MOL2 files from the current visualization run.

    Each call resolves the run again: the active launch lock has priority, and
    otherwise the newest numerically named run directory is selected. Labels
    are run-relative filenames so the browser never receives an absolute path.
    """
    run_id = _visualization_run_id()
    if run_id is None:
        return []
    registry = RunRegistry(ROOT)
    try:
        run_dir = registry.resolve_run_id(run_id)
    except RunRegistryError:
        return []

    result: list[dict[str, object]] = []
    run_root = run_dir.resolve()
    for candidate in sorted(run_dir.rglob("*"), key=lambda path: path.as_posix()):
        if not candidate.is_file():
            continue
        relative = candidate.relative_to(run_dir)
        fmt = _RUN_VISUALIZATION_FORMATS.get(relative.suffix.lower())
        if fmt is None:
            continue
        try:
            path = candidate.resolve(strict=True)
            path.relative_to(run_root)
        except (OSError, ValueError):
            continue
        if not path.is_file():
            continue
        label = relative.as_posix()
        result.append({"label": label, "path": path, "format": fmt})
    return result


def get_run_visualization_choices() -> list[str]:
    """List the active run's PDB/MOL2 filenames without exposing paths."""
    return [str(item["label"]) for item in _current_run_visualization_artifacts()]


def get_run_visualization_data(choice: str | None) -> dict[str, object] | None:
    """Read one selected structure from the active run directory."""
    if not isinstance(choice, str) or not choice:
        return None
    for artifact in _current_run_visualization_artifacts():
        if artifact["label"] != choice:
            continue
        path = artifact["path"]
        try:
            content = Path(path).read_text(errors="replace")
        except OSError:
            return None
        return {
            "content": content,
            "format": artifact["format"],
            "atom_count": content.count("ATOM"),
            "label": artifact["label"],
        }
    return None


def _viewer_style_value(
    value: float | int | None,
    default: float,
    value_range: tuple[float, float],
) -> float:
    """Clamp browser-supplied viewer settings before embedding them in script data."""
    if isinstance(value, bool):
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(parsed):
        return default
    return max(value_range[0], min(value_range[1], parsed))


def _render_viewer_html(
    mol_data: str,
    fmt: str,
    atom_count: int,
    display_name: str,
    sphere_scale: float | int | None = DEFAULT_SPHERE_SCALE,
    stick_radius: float | int | None = DEFAULT_STICK_RADIUS,
) -> str:
    """Render one already-authorized PDB/MOL2 structure in the embedded viewer."""
    sphere_scale = _viewer_style_value(
        sphere_scale, DEFAULT_SPHERE_SCALE, VIEWER_SPHERE_SCALE_RANGE)
    stick_radius = _viewer_style_value(
        stick_radius, DEFAULT_STICK_RADIUS, VIEWER_STICK_RADIUS_RANGE)

    mol_json = json.dumps(mol_data)

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
html,body{{margin:0;padding:0;width:100%;height:100%;overflow:hidden;background:#eee9e2}}
#v{{width:100%;height:100%;position:absolute;top:0;left:0}}
</style></head><body>
<div id="v"></div>
<script src="https://3Dmol.org/build/3Dmol-min.js"></script>
<script>
(function(){{
  function init(){{
    if(typeof $3Dmol==="undefined"){{setTimeout(init,150);return;}}
    var v=$3Dmol.createViewer("v",{{backgroundColor:"#eee9e2"}});
    v.addModel({mol_json},"{fmt}");
    v.setStyle({{}},{{stick:{{radius:{stick_radius:.2f},colorscheme:"Jmol"}},sphere:{{scale:{sphere_scale:.2f},colorscheme:"Jmol"}}}});
    v.zoomTo();v.render();v.zoom(1.2);
  }}
  init();
}})();
</script></body></html>"""

    import html as _h
    safe_display_name = _h.escape(display_name)
    return (
        f'<div style="background:#eee9e2;border-radius:6px;overflow:hidden">'
        f'<iframe srcdoc="{_h.escape(html)}" style="width:100%;height:340px;border:none" '
        f'sandbox="allow-scripts allow-same-origin"></iframe>'
        f'<div style="text-align:center;padding:6px 0 14px;font-size:12px;color:#765f4f;'
        f'font-family:system-ui,sans-serif">{safe_display_name}</div>'
        f'</div>'
    )


def render_viewer_html(
    mol_choice: str | None = None,
    sphere_scale: float | int | None = DEFAULT_SPHERE_SCALE,
    stick_radius: float | int | None = DEFAULT_STICK_RADIUS,
) -> str:
    """Render a legacy structure-library selection for compatibility callers."""
    viewer_data = get_molecule_viewer_data(mol_choice) if mol_choice else None
    if viewer_data is None:
        return _VIEWER_EMPTY
    return _render_viewer_html(
        str(viewer_data["content"]),
        str(viewer_data["format"]),
        int(viewer_data["atom_count"]),
        Path(str(viewer_data["file_path"])).stem,
        sphere_scale,
        stick_radius,
    )


def render_run_visualization_html(
    choice: str | None,
    sphere_scale: float | int | None = DEFAULT_SPHERE_SCALE,
    stick_radius: float | int | None = DEFAULT_STICK_RADIUS,
) -> str:
    """Render the selected PDB/MOL2 artifact from the current run."""
    viewer_data = get_run_visualization_data(choice)
    if viewer_data is None:
        return _VIEWER_EMPTY.replace(
            "从下方下拉菜单选择分子查看",
            "当前运行目录暂无可读取的 PDB/MOL2 结构",
        )
    return _render_viewer_html(
        str(viewer_data["content"]),
        str(viewer_data["format"]),
        int(viewer_data["atom_count"]),
        str(viewer_data["label"]),
        sphere_scale,
        stick_radius,
    )


_STAGE_LABELS = {
    "em": "能量最小化",
    "eq": "NPT退火",
    "prod": "生产模拟",
}

_STATE_LABELS = {
    "idle": "空闲",
    "running": "运行中",
    "retrying": "处理中",
    "awaiting_confirmation": "等待用户确认调整方案",
    "stopping": "正在安全停止",
    "escalated": "未完成",
    "done": "已完成",
    "aborted": "已中止",
}


def _status_indicator(kind: str) -> str:
    """Return the compact visual marker used by the run assistant status."""
    if kind == "running":
        return (
            '<span class="pipeline-status-indicator pipeline-status-indicator--running" '
            'role="img" aria-label="运行中"><span class="pipeline-status-spinner"></span></span>'
        )
    if kind == "done":
        return (
            '<span class="pipeline-status-indicator pipeline-status-indicator--done" '
            'role="img" aria-label="已完成"><span class="pipeline-status-spinner"></span>'
            '<span class="pipeline-status-check" aria-hidden="true">✓</span></span>'
        )
    if kind == "error":
        return '<span class="pipeline-status-indicator pipeline-status-indicator--error" aria-hidden="true">×</span>'
    return '<span class="pipeline-status-indicator pipeline-status-indicator--stopped" aria-hidden="true">■</span>'


def _activity_lines(activity: dict | None, *, running: bool) -> list[str]:
    """Render the six-field public activity contract in Chinese."""
    if not isinstance(activity, dict) or not activity:
        return ["当前未记录公开工序状态。"]
    tool = activity.get("tool")
    operation = activity.get("operation")
    target = activity.get("target")
    current = activity.get("current")
    total = activity.get("total")
    if not all(isinstance(value, str) and value for value in (tool, operation, target)):
        return ["当前未记录公开工序状态。"]
    target = _STAGE_LABELS.get(target, target)
    if running:
        lines = [f"{_status_indicator('running')}正在使用 {tool} 进行{operation}"]
    else:
        lines = [f"当前工序：{tool} {operation}"]
    if isinstance(current, int) and isinstance(total, int) and total > 0:
        shown_current = "准备中" if current == 0 else str(current)
        lines.append(f"当前进度：{target}（{shown_current}/{total}）")
    else:
        lines.append(f"当前对象：{target}")
    return lines


def _mdrun_eta_summary_lines(eta: dict | None) -> list[str]:
    """Render only GROMACS-provided estimates and current liveness heartbeats."""
    if not isinstance(eta, dict):
        return []
    status = eta.get("status")
    if status == "available":
        estimate = eta.get("estimated_end_at_local", eta.get("estimated_end_at"))
        if isinstance(estimate, str):
            return [f"当前步骤预计结束：{estimate}"]
        return []
    if status == "waiting":
        observed_at = eta.get("observed_at_local", eta.get("observed_at"))
        if isinstance(observed_at, str):
            return [f"GROMACS 正在运行，暂未获得 ETA。最近心跳：{observed_at}"]
        return ["GROMACS 正在运行，暂未获得 ETA。"]
    return []


def _repair_lines(repair: dict | None) -> list[str]:
    """Render only the compact, public repair state from one run snapshot."""
    if not isinstance(repair, dict):
        return []
    attempt = repair.get("attempt")
    max_attempts = repair.get("max_attempts")
    if isinstance(attempt, int) and isinstance(max_attempts, int) and max_attempts > 0:
        shown_attempt = max(1, attempt)
        lines = [f"自动修复：第 {shown_attempt}/{max_attempts} 次"]
    else:
        lines = ["自动修复：正在分析并应用修复"]
    for adjustment in repair.get("adjustments", []):
        if not isinstance(adjustment, dict):
            continue
        name = adjustment.get("name")
        before = adjustment.get("before")
        after = adjustment.get("after")
        if all(isinstance(value, str) and value for value in (name, before, after)):
            lines.append(f"已调整：{name} {before} → {after}")
    return ["", *lines]


def refresh_molecule_choices() -> list[str]:
    """刷新分子下拉列表（上传新结构后调用）。"""
    return flatten_catalog(get_molecule_catalog())


# ============================================================
# 运行助理（只读）
# ============================================================


def list_run_choices() -> list[str]:
    """Return run IDs for the UI selector without exposing filesystem paths."""
    return [item["run_id"] for item in RunRegistry(ROOT).list_runs() if item.get("run_id")]


def latest_run_id() -> str | None:
    """Return the active run, otherwise the newest registered run ID.

    A pending action from a historical run must never win over the pipeline
    lock that identifies the project currently executing.
    """
    registry = RunRegistry(ROOT)
    active_run_id = get_active_run_id()
    if isinstance(active_run_id, str):
        try:
            registry.resolve_run_id(active_run_id)
        except RunRegistryError:
            pass
        else:
            return active_run_id
    choices = [item["run_id"] for item in registry.list_runs() if item.get("run_id")]
    return choices[0] if choices else None


def get_pending_action(run_id: str | None = None) -> dict[str, object] | None:
    """Return the public EQ proposal for one run only.

    The UI follows the newest registered run when no ID is supplied.  It must
    never scan all runs, otherwise an old awaiting-confirmation proposal can
    be rendered over a newly started project.
    """
    registry = RunRegistry(ROOT)
    selected_run_id = run_id or latest_run_id()
    if not isinstance(selected_run_id, str):
        return None
    try:
        status = registry.get_run_status(selected_run_id)
    except RunRegistryError:
        return None
    if status.get("state") != "awaiting_confirmation":
        return None
    extra = status.get("extra", {})
    action = extra.get("pending_action") if isinstance(extra, Mapping) else None
    if not isinstance(action, Mapping):
        return None
    action_id = action.get("action_id")
    summary = action.get("summary")
    restart_step = action.get("restart_step")
    revision = status.get("state_revision")
    if (
        not isinstance(action_id, str)
        or not isinstance(summary, str)
        or not STEP_REGISTRY.controlled_restart_allowed(EQ_STEP, restart_step)
        or isinstance(revision, bool)
        or not isinstance(revision, int)
        or revision < 0
    ):
        return None
    try:
        from willy.simulation.pending_action import load_pending_action
        private_action = load_pending_action(registry.resolve_run_id(selected_run_id))
    except (OSError, ValueError, RunRegistryError):
        return None
    config_fingerprint = private_action.get("config_sha256")
    if private_action.get("action_id") != action_id or not isinstance(config_fingerprint, str):
        return None
    public = {
        "run_id": selected_run_id,
        "action_id": action_id,
        "state_revision": revision,
        "config_fingerprint": config_fingerprint,
        "status": "awaiting_confirmation",
        "step_label": str(action.get("step_label", "")),
        "restart_step": restart_step,
        "summary": summary,
        "adjustments": action.get("adjustments", []) if isinstance(action.get("adjustments"), list) else [],
    }
    editable = action.get("editable_parameters")
    if isinstance(editable, list):
        public["editable_parameters"] = editable[:8]
    return public


def confirm_pending_action(
    action_id: str,
    run_id: str | None = None,
    *,
    state_revision: int | None = None,
    config_fingerprint: str | None = None,
) -> str:
    """Launch one server-validated EQ retry after explicit user approval."""
    if not isinstance(action_id, str) or not action_id.strip():
        return "确认请求无效，请刷新后重试。"
    action = get_pending_action(run_id)
    if action is None or action.get("action_id") != action_id:
        return "待确认方案已失效，请刷新工程状态。"
    registry = RunRegistry(ROOT)
    run_id = action.get("run_id")
    if not isinstance(run_id, str):
        return "待确认方案已失效，请刷新工程状态。"
    current_revision = action.get("state_revision")
    current_fingerprint = action.get("config_fingerprint")
    if (
        isinstance(current_revision, bool)
        or not isinstance(current_revision, int)
        or current_revision < 0
        or not isinstance(current_fingerprint, str)
        or not current_fingerprint
    ):
        return "待确认方案已失效，请刷新工程状态。"
    expected_revision = current_revision if state_revision is None else state_revision
    expected_fingerprint = current_fingerprint if config_fingerprint is None else config_fingerprint
    if (
        isinstance(expected_revision, bool)
        or not isinstance(expected_revision, int)
        or expected_revision != current_revision
        or not isinstance(expected_fingerprint, str)
        or expected_fingerprint != current_fingerprint
    ):
        return "待确认方案已更新，请刷新后重新确认。"
    waiting_status: dict[str, object] | None = None
    retrying_status: dict[str, object] | None = None
    try:
        run_dir = registry.resolve_run_id(run_id)
        manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
        backend = manifest.get("backend")
        if backend not in {"g16", "orca"}:
            return "运行后端无效，无法重跑。"
        reservation = reserve_existing_run_launch(ROOT, run_id)
    except (OSError, ValueError, json.JSONDecodeError, RunRegistryError, PipelineLaunchError):
        return "确认请求未送达，请刷新后重试。"
    except PipelineLockConflict:
        return "当前已有运行中的工程，请等待其结束后再确认。"

    try:
        # Publish the accepted approval before spawning the child.  Without
        # this handoff the UI can re-read the old waiting snapshot until the
        # child reaches PipelineOrchestrator.start_retry().
        from willy.simulation.pending_action import (
            pending_action_lock,
            validate_pending_action_for_launch,
        )
        with pending_action_lock(run_dir):
            waiting_status = registry.get_run_status(run_id)
            if waiting_status.get("state") != "awaiting_confirmation":
                raise RunStateConflict("待确认方案已失效")
            waiting_extra = waiting_status.get("extra", {})
            waiting_action = waiting_extra.get("pending_action") if isinstance(waiting_extra, Mapping) else None
            if not isinstance(waiting_action, Mapping) or waiting_action.get("action_id") != action_id:
                raise RunStateConflict("待确认方案已失效")
            if waiting_status.get("state_revision") != expected_revision:
                raise RunStateConflict("待确认方案已更新")
            private_action = validate_pending_action_for_launch(run_dir, action_id)
            if private_action.get("config_sha256") != expected_fingerprint:
                raise RunStateConflict("待确认方案已更新")
            retrying_status = dict(waiting_status)
            retrying_status.update({
                "state": "retrying",
                "error": "",
                "error_kind": "",
                "repair": {
                    "attempt": 1,
                    "max_attempts": 1,
                    "adjustments": waiting_action.get("adjustments", []),
                },
                "updated_at": datetime.now(timezone.utc).isoformat(),
            })
            retrying_status = registry.compare_and_swap_status(
                run_dir,
                expected_revision=expected_revision,
                status=retrying_status,
                event_type="confirmation_retry_started",
            )
            try:
                registry.append_decision_trace(run_dir, {
                    "decision_id": action_id,
                    "action_id": action_id,
                    "layer": "simulation",
                    "step": private_action.get("failure_step", EQ_STEP),
                    "error_kind": waiting_status.get("error_kind", "equilibration_failed"),
                    "policy_id": "simulation.eq.user_confirmation",
                    "selected_tool": "tools_retry_eq",
                    "tool_effect": "requires_confirmation",
                    "parameter_changes": [item.get("field") for item in private_action.get("adjustments", []) if isinstance(item, Mapping)],
                    "confirmation_source": "user",
                    "result": "confirmed",
                    "success": True,
                    "restart_step": private_action.get("restart_step", EQ_STEP),
                })
            except OSError:
                pass
            try:
                process = subprocess.Popen(
                    [
                        "python3", "run_pipeline.py", backend,
                        "--run-dir", str(reservation.run_dir),
                        "--lock-fd", str(reservation.fd),
                        "--launch-token", reservation.token,
                        "--resume-pending-action", action_id,
                    ],
                    cwd=str(ROOT),
                    start_new_session=True,
                    pass_fds=(reservation.fd,),
                )
            except OSError:
                registry.compare_and_swap_status(
                    run_dir,
                    expected_revision=retrying_status["state_revision"],
                    status=waiting_status,
                    event_type="confirmation_launch_failed",
                )
                raise
        reservation.mark_runner_started(process.pid)
        (ROOT / ".pipeline.pid").write_text(str(process.pid), encoding="utf-8")
        reservation.detach_parent()
    except (RunStateConflict, ValueError, RunRegistryError):
        reservation.release()
        return "待确认方案已更新，请刷新后重新确认。"
    except OSError:
        reservation.release()
        return "确认后启动失败，请检查运行环境后重试。"

    def _wait_then_cleanup() -> None:
        process.wait()
        pid_file = ROOT / ".pipeline.pid"
        try:
            if pid_file.read_text(encoding="utf-8").strip() == str(process.pid):
                pid_file.unlink(missing_ok=True)
        except OSError:
            pass
        cleanup_finished_launch(ROOT, run_id, reservation.token)

    threading.Thread(target=_wait_then_cleanup, daemon=True).start()
    return f"已确认方案，当前进入重跑准备；将从第 {action['restart_step']} 步重新生成参数并受控重跑。"


def revise_pending_action(
    action_id: str,
    user_request: str,
    run_id: str | None = None,
    *,
    state_revision: int | None = None,
    config_fingerprint: str | None = None,
) -> str:
    """Replace one EQ proposal after a user requests a different plan.

    This is intentionally narrower than a configuration API: it can only
    replace the *pending* action for the currently waiting run.  The proposed
    fields are generated by ``SimulationAgent`` and then passed through the
    same allowlist, range, and configuration-fingerprint gate as the original
    plan.  Neither the run configuration nor a process changes here.
    """
    if not isinstance(action_id, str) or not action_id.strip():
        return "待确认方案无效，请刷新工程状态。"
    if not isinstance(user_request, str) or not user_request.strip():
        return "请先说明希望怎样调整当前方案。"
    request = " ".join(user_request.split())
    if len(request) > 600:
        return "调整要求过长，请简要说明希望修改的参数或阶段。"

    action = get_pending_action(run_id)
    if action is None or action.get("action_id") != action_id:
        return "待确认方案已失效，请刷新工程状态。"
    selected_run_id = action.get("run_id")
    if not isinstance(selected_run_id, str):
        return "待确认方案已失效，请刷新工程状态。"
    current_revision = action.get("state_revision")
    current_fingerprint = action.get("config_fingerprint")
    if (
        isinstance(current_revision, bool)
        or not isinstance(current_revision, int)
        or current_revision < 0
        or not isinstance(current_fingerprint, str)
        or not current_fingerprint
    ):
        return "待确认方案已失效，请刷新工程状态。"
    expected_revision = current_revision if state_revision is None else state_revision
    expected_fingerprint = current_fingerprint if config_fingerprint is None else config_fingerprint
    if (
        isinstance(expected_revision, bool)
        or not isinstance(expected_revision, int)
        or expected_revision != current_revision
        or not isinstance(expected_fingerprint, str)
        or expected_fingerprint != current_fingerprint
    ):
        return "待确认方案已更新，请刷新后重新调整。"

    registry = RunRegistry(ROOT)
    try:
        run_dir = registry.resolve_run_id(selected_run_id)
        status = registry.get_run_status(selected_run_id)
        if status.get("state") != "awaiting_confirmation":
            return "当前工程不在等待确认状态，无法更新方案。"
        from willy.simulation.pending_action import validate_pending_action_for_launch
        validate_pending_action_for_launch(run_dir, action_id)
    except (OSError, ValueError, RunRegistryError):
        return "待确认方案不可用，请刷新工程状态后重试。"

    try:
        from willy.agent_config import _DS, _LLM_SETTINGS
        from willy.agent_simulation import SimulationAgent
        if _DS is None:
            return "LLM 当前不可用，原方案仍保持等待确认。"
        model = _LLM_SETTINGS.model if _LLM_SETTINGS else DEFAULT_LLM_MODEL
        proposal = SimulationAgent(_DS, model=model).propose_revised_eq_recovery(
            status=status,
            current_action=action,
            user_request=request,
            config_path=str(run_dir / "config.json"),
        )
    except Exception:
        return "无法生成新的调整方案，原方案仍保持等待确认。"

    try:
        from willy.simulation.pending_action import (
            PendingActionError,
            pending_action_lock,
            public_pending_action,
            replace_eq_pending_action,
            validate_pending_action_for_launch,
        )
        with pending_action_lock(run_dir):
            current_status = registry.get_run_status(selected_run_id)
            if (
                current_status.get("state") != "awaiting_confirmation"
                or current_status.get("state_revision") != expected_revision
            ):
                raise RunStateConflict("待确认方案已更新")
            current_action = validate_pending_action_for_launch(run_dir, action_id)
            if current_action.get("config_sha256") != expected_fingerprint:
                raise RunStateConflict("待确认方案已更新")
            replacement = replace_eq_pending_action(
                run_dir,
                action_id=action_id,
                proposal=proposal,
            )
            updated_status = dict(current_status)
            updated_extra = dict(current_status.get("extra")) if isinstance(current_status.get("extra"), Mapping) else {}
            updated_extra.update({
                "run_id": selected_run_id,
                "pending_action": public_pending_action(replacement),
            })
            updated_status.update({
                "state": "awaiting_confirmation",
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "extra": updated_extra,
            })
            registry.compare_and_swap_status(
                run_dir,
                expected_revision=expected_revision,
                status=updated_status,
                event_type="pending_action_revised",
            )
            try:
                registry.append_decision_trace(run_dir, {
                    "decision_id": replacement.get("action_id", action_id),
                    "action_id": replacement.get("action_id", action_id),
                    "layer": "simulation",
                    "step": replacement.get("failure_step", EQ_STEP),
                    "error_kind": status.get("error_kind", "equilibration_failed"),
                    "policy_id": "simulation.eq.user_revision",
                    "selected_tool": "tools_retry_eq",
                    "tool_effect": "requires_confirmation",
                    "parameter_changes": [item.get("field") for item in replacement.get("adjustments", []) if isinstance(item, Mapping)],
                    "confirmation_source": "user_revision",
                    "result": "awaiting_confirmation",
                    "success": True,
                    "restart_step": replacement.get("restart_step", EQ_STEP),
                })
            except OSError:
                pass
    except (OSError, ValueError, PendingActionError, RunRegistryError, RunStateConflict):
        return "新的调整方案未通过校验，原方案仍保持等待确认。"
    return "已按你的要求更新待确认方案；请审阅新方案后再明确确认。"


def get_run_summary_markdown(run_id: str | None) -> str:
    """Render the selected run's public engineering state for the run assistant."""
    run_id = run_id or latest_run_id()
    if run_id is None:
        return "### 工程状态\n\n暂无可读取的运行。"
    try:
        registry = RunRegistry(ROOT)
        status = registry.get_run_status(run_id)
    except RunRegistryError as exc:
        return f"⚠ 无法读取运行：{exc}"
    state = status.get("state", "unknown")
    lines = [f"### 工程状态 · 运行 {run_id}", f"状态：{_STATE_LABELS.get(state, '未知')}"]
    terminal_state = state in {"aborted", "escalated"}
    if not terminal_state:
        lines.extend(_activity_lines(status.get("activity"), running=state in {"running", "retrying"}))
    if state in {"running", "retrying"} and status.get("layer") == "simulation":
        lines.extend(_mdrun_eta_summary_lines(registry.get_mdrun_eta(run_id)))
    if state == "retrying":
        lines.extend(_repair_lines(status.get("repair")))
    if status.get("error"):
        lines.extend(["", f"{_status_indicator('error')}{status['error']}"])
    if state == "escalated":
        lines.extend(["", f"{_status_indicator('stopped')}**自动处理未完成**"])
        if status.get("error_kind") == "user_confirmation_required":
            lines.extend([
                "",
                "**等待用户确认模拟协议变更**",
                "当前运行未改写，自动修复已停止。请审阅新方案并确认后重新启动。",
            ])
    elif state == "awaiting_confirmation":
        lines.extend([
            "",
            f"{_status_indicator('stopped')}**等待用户确认调整方案**",
            "LLM 已返回方案；当前工程保持等待，未修改配置、未启动重跑，也未中止。",
        ])
    elif not status.get("error") and state == "done":
        lines.extend(["", f"{_status_indicator('done')}**全流程完成**"])
    elif not status.get("error") and state == "stopping":
        lines.extend(["", f"{_status_indicator('stopped')}**正在安全停止，等待当前工序写入 checkpoint**"])
    elif not status.get("error") and state == "aborted":
        lines.extend(["", f"{_status_indicator('stopped')}**已中止**"])
    return "\n".join(lines)


def get_run_panel_snapshot(run_id: str | None = None) -> dict[str, object]:
    """Return one run's public status and pending action from a shared ID.

    The UI refreshes status and adjustment cards together.  Resolving the run
    once prevents a newly-started project from mixing its status with a
    historical run's awaiting-confirmation action.
    """
    selected_run_id = run_id or latest_run_id()
    return {
        "run_id": selected_run_id,
        "summary": get_run_summary_markdown(selected_run_id),
        "pending_action": get_pending_action(selected_run_id),
    }


def run_assistant_pending_message(message: str) -> str:
    """Describe the current read-only response path without exposing internals."""
    from willy.agent_run import classify_run_query

    if classify_run_query(message).mode == "fast":
        return "正在读取当前运行事实..."
    return "正在读取运行事实并生成解释..."


def chat_run_assistant(run_id: str | None, message: str, history: list[dict] | None = None) -> str:
    """Answer a run question through the read-only RunAssistant."""
    run_id = run_id or latest_run_id()
    if run_id is None:
        return "当前没有可读取的运行。"
    try:
        from willy.agent_config import _DS, _LLM_SETTINGS
        from willy.agent_run import RunAssistant

        model = _LLM_SETTINGS.model if _LLM_SETTINGS else DEFAULT_LLM_MODEL
        return RunAssistant(_DS, registry=RunRegistry(ROOT), model=model).answer(
            message,
            selected_run_id=run_id,
            history=history,
        )
    except RunRegistryError as exc:
        return f"无法读取运行：{exc}"
    except Exception as exc:
        return f"运行助理暂时不可用：{str(exc)[:160]}"
