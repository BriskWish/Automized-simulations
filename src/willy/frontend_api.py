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
import importlib
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
    load_llm_provider_mode,
)
from willy.managed_gateway import (
    ManagedGatewayError,
    ManagedIdentityStore,
    load_managed_gateway_profile,
    managed_gateway_usage_snapshot,
    managed_identity_status,
    request_managed_registration,
)
from willy.run_registry import RunRegistry, RunRegistryError, RunStateConflict
from willy.step_registry import EQ_STEP, STEP_REGISTRY
from willy.simulation.mdrun_eta import MDRUN_HEARTBEAT_INTERVAL_S
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
_REMOTE_REGISTRY_MODULE = "willy.remote_registry"
_REMOTE_REGISTRY_SNAPSHOT_FUNCTION = "get_public_execution_snapshot"
_EXECUTION_MODES = ("local", "ssh", "slurm")
_EXECUTION_MODE_LABELS = {
    "local": "本机执行",
    "ssh": "SSH 执行",
    "slurm": "Slurm 调度",
}
_EXECUTION_CONNECTION_STATES = {
    "ready", "not_configured", "unavailable", "unknown",
}
_EXECUTION_PROFILE_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")


def _refresh_agent_llm_client() -> None:
    """Keep the in-process Config Agent aligned with a just-saved local mode."""
    try:
        from willy.agent_config import refresh_llm_client

        refresh_llm_client()
    except Exception:
        # Persistence is still valid even when an optional interactive agent is unavailable.
        pass


class LLMConnectionResult(TypedDict):
    """Redacted result suitable for rendering in the configuration UI."""

    ok: bool
    code: str
    message: str
    suggestion: str


class ExecutionProfile(TypedDict):
    """Redacted execution-profile data allowed to cross the frontend boundary."""

    profile_id: str
    mode: str
    label: str
    available: bool
    connection_state: str


class ExecutionProfileSnapshot(TypedDict):
    """Read-only execution selection data for the remote-task page."""

    registry_state: str
    profiles: list[ExecutionProfile]
    selected_mode: str
    selected_profile_id: str
    summary: str


class ExecutionProposalContext(TypedDict):
    """Safe selection intent sent to Config Agent for server-side validation."""

    execution_mode: str
    execution_profile_id: str
    available: bool
    summary: str


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
    "managed_not_selected": (
        "当前未选择托管网关模式",
        "在配置页选择“托管网关”后再检查服务。",
    ),
    "managed_not_registered": (
        "本机尚未登记到托管网关",
        "在配置页申请接入，并等待网关管理员批准。",
    ),
    "managed_not_approved": (
        "设备尚未获得托管网关批准",
        "请让网关管理员在本机管理页面批准该设备。",
    ),
    "managed_gateway_protocol": (
        "托管网关返回的状态无效",
        "请联系网关管理员检查服务版本和运行状态。",
    ),
    "managed_gateway_rejected": (
        "托管网关拒绝了状态查询",
        "请联系网关管理员检查设备授权状态。",
    ),
    "managed_gateway_unreachable": (
        "无法连接托管网关",
        "检查网关地址、网络路径和网关进程状态。",
    ),
    "managed_gateway_timeout": (
        "托管网关连接超时",
        "检查网关负载、网络路径和防火墙设置后重试。",
    ),
}


def get_llm_provider_mode() -> str:
    """Return a UI-safe mode even when a local file is temporarily malformed."""
    try:
        return load_llm_provider_mode(ROOT)
    except LLMConfigError:
        return "byok"

def get_llm_config_status() -> str:
    """Report LLM configuration state without ever returning a credential."""
    try:
        settings = load_llm_settings(ROOT)
    except LLMConfigError:
        return "OpenAI-compatible LLM 配置无效，请检查 Base URL、Model 和 API Key。"
    if settings is None:
        return "尚未配置 OpenAI-compatible LLM 服务。"
    if getattr(settings, "provider_mode", "byok") == "managed":
        profile = getattr(settings, "managed_profile", None)
        label = getattr(profile, "label", "托管网关")
        try:
            state = managed_identity_status(profile) if profile is not None else "not_configured"
        except ManagedGatewayError:
            state = "not_configured"
        state_label = {
            "not_registered": "尚未注册设备",
            "pending_or_approved": "设备已登记，等待或已获得管理员批准",
            "not_configured": "托管配置不可用",
        }.get(state, "状态未知")
        return f"托管 LLM 网关：{label}；{state_label}；模型：{settings.model}。"
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
    if settings is not None and getattr(settings, "provider_mode", "byok") == "managed":
        return (
            "当前使用托管 LLM 网关；设备私钥只保存在本机受保护身份文件，"
            "接入申请由服务器计数并由管理员批准。LLM prompt 会经过网关运营者控制的服务。"
            f"当前模型别名：*{model}*。"
        )
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
            "WILLY_LLM_MODE",
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

    _refresh_agent_llm_client()
    return "OpenAI-compatible LLM 配置已保存到本机 .env。"


def save_llm_mode(mode: str) -> str:
    """Persist only the local provider mode; managed endpoint fields stay immutable."""
    normalized = mode.strip().lower() if isinstance(mode, str) else ""
    if normalized not in {"byok", "managed"}:
        return "LLM 模式无效。"
    env_path = ROOT / ".env"
    try:
        existing_lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
        updated_lines = [
            line for line in existing_lines
            if line.split("=", 1)[0].strip() != "WILLY_LLM_MODE"
        ]
        if normalized == "managed":
            updated_lines.append("WILLY_LLM_MODE=managed")
        fd, temp_name = tempfile.mkstemp(prefix=".env-", suffix=".tmp", dir=ROOT)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write("\n".join(updated_lines) + ("\n" if updated_lines else ""))
            os.chmod(temp_name, 0o600)
            os.replace(temp_name, env_path)
        finally:
            Path(temp_name).unlink(missing_ok=True)
    except OSError:
        return "LLM 模式保存失败，请检查项目目录的写入权限。"
    if normalized == "managed":
        try:
            load_managed_gateway_profile(ROOT)
        except ManagedGatewayError:
            # The mode selection is valid even if a deployment profile arrives later.
            pass
    _refresh_agent_llm_client()
    return "已选择托管 LLM 网关。" if normalized == "managed" else "已选择自带 API Key。"


def get_managed_gateway_status() -> dict[str, object]:
    """Return a redacted local managed-profile/identity status for the UI."""
    try:
        profile = load_managed_gateway_profile(ROOT)
        state = managed_identity_status(profile)
    except ManagedGatewayError as error:
        return {"ok": False, "code": error.code, "message": "托管网关配置尚不可用，请联系部署者。"}
    return {
        "ok": True,
        "code": "ok",
        "profile_id": profile.profile_id,
        "label": profile.label,
        "model": profile.model,
        "device_state": state,
    }


def request_managed_gateway_registration() -> str:
    """Submit this machine's public key for one server-counted registration slot."""
    try:
        profile = load_managed_gateway_profile(ROOT)
        status = request_managed_registration(profile, store=ManagedIdentityStore())
    except ManagedGatewayError as error:
        messages = {
            "managed_registration_denied": "该设备身份已被网关拒绝或撤销。",
            "managed_registration_limit_reached": "接入申请已达服务器名额上限。",
            "managed_registration_rate_limited": "申请次数过多，请稍后再试。",
            "managed_access_denied": "该设备尚未获得网关批准。",
            "managed_gateway_unreachable": "无法连接托管网关。",
            "managed_gateway_timeout": "托管网关连接超时。",
        }
        return messages.get(error.code, "托管设备申请未完成，请联系网关管理员。")
    _refresh_agent_llm_client()
    return "设备公钥已发送，当前为待审批状态；请等待网关管理员批准。" if status == "pending" else "本机设备已获得网关批准。"


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


def test_managed_gateway_connection() -> LLMConnectionResult:
    """Check the gateway and return this device's remaining token quota."""
    try:
        settings = load_llm_settings(ROOT)
    except LLMConfigError:
        return _connection_result(False, "managed_not_registered")
    if settings is None or getattr(settings, "provider_mode", "byok") != "managed":
        return _connection_result(False, "managed_not_selected")
    profile = getattr(settings, "managed_profile", None)
    if profile is None:
        return _connection_result(False, "managed_gateway_protocol")
    try:
        usage = managed_gateway_usage_snapshot(profile)
    except ManagedGatewayError as error:
        if error.code == "managed_device_not_registered":
            return _connection_result(False, "managed_not_registered")
        if error.code in {"managed_access_denied", "managed_quota_exceeded"}:
            return _connection_result(False, "managed_not_approved")
        return _connection_result(False, error.code if error.code in _LLM_CONNECTION_PUBLIC_ERRORS else "network")
    except Exception:
        return _connection_result(False, "network")

    def remaining(period: str) -> int:
        details = usage.get(period) if isinstance(usage, Mapping) else None
        if not isinstance(details, Mapping):
            raise ValueError("invalid usage period")
        limit = details.get("limit")
        settled = details.get("settled_tokens")
        reserved = details.get("reserved_tokens")
        values = (limit, settled, reserved)
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in values):
            raise ValueError("invalid usage values")
        if limit < 1:
            raise ValueError("invalid usage limit")
        return max(0, limit - settled - reserved)

    try:
        daily_remaining = remaining("daily")
        monthly_remaining = remaining("monthly")
    except ValueError:
        return _connection_result(False, "managed_gateway_protocol")
    return {
        "ok": True,
        "code": "ok",
        "message": "托管网关连接成功",
        "suggestion": f"本日剩余 Token：{daily_remaining:,}；本月剩余 Token：{monthly_remaining:,}。",
    }


def _fallback_execution_profiles() -> list[ExecutionProfile]:
    """Provide safe choices before the optional remote registry is installed."""
    return [
        {
            "profile_id": "local-default",
            "mode": "local",
            "label": _EXECUTION_MODE_LABELS["local"],
            "available": True,
            "connection_state": "ready",
        },
        {
            "profile_id": "ssh-not-configured",
            "mode": "ssh",
            "label": _EXECUTION_MODE_LABELS["ssh"],
            "available": False,
            "connection_state": "not_configured",
        },
        {
            "profile_id": "slurm-not-configured",
            "mode": "slurm",
            "label": _EXECUTION_MODE_LABELS["slurm"],
            "available": False,
            "connection_state": "not_configured",
        },
    ]


def _remote_registry_snapshot() -> tuple[object | None, str]:
    """Read a local registry capability view, never a connection API.

    ``get_public_execution_snapshot`` is the preferred narrow contract.  The
    ``public_remote_capabilities`` fallback keeps independently upgraded
    frontends compatible with the initial registry implementation.  Both
    registry functions are required to be local, read-only metadata reads.
    """
    try:
        module = importlib.import_module(_REMOTE_REGISTRY_MODULE)
        reader = getattr(module, _REMOTE_REGISTRY_SNAPSHOT_FUNCTION, None)
        if not callable(reader):
            reader = getattr(module, "public_remote_capabilities", None)
        if not callable(reader):
            return None, "unavailable"
        snapshot = reader()
    except Exception:
        # The UI must remain usable while the execution subsystem is absent,
        # misconfigured, or upgraded independently.
        return None, "unavailable"
    return snapshot, "ready" if isinstance(snapshot, Mapping) else "unavailable"


def _safe_execution_mode(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    mode = value.strip().lower()
    return mode if mode in _EXECUTION_MODES else None


def _safe_connection_state(value: object, available: bool) -> str:
    if isinstance(value, str) and value.strip().lower() in _EXECUTION_CONNECTION_STATES:
        return value.strip().lower()
    return "unknown" if available else "not_configured"


def _safe_profile_id(value: object, *, mode: str, index: int) -> str:
    if isinstance(value, str):
        candidate = value.strip()
        if _EXECUTION_PROFILE_ID.fullmatch(candidate):
            return candidate
    return f"{mode}-{index + 1}"


def _public_execution_profiles(snapshot: object | None) -> list[ExecutionProfile]:
    """Whitelist remote registry data so hosts, paths, keys and commands stay private."""
    profiles: list[ExecutionProfile] = []
    raw_profiles = snapshot.get("profiles") if isinstance(snapshot, Mapping) else None
    registry_configured = (
        isinstance(snapshot, Mapping)
        and snapshot.get("available") is True
    )
    if isinstance(raw_profiles, Mapping):
        # Initial registry releases expose a profile-ID keyed capability map.
        # ``direct`` is SSH transport; the map itself has no connection result.
        for index, (raw_profile_id, raw_profile) in enumerate(raw_profiles.items()):
            if not isinstance(raw_profile, Mapping):
                continue
            launcher = raw_profile.get("launcher")
            mode = "ssh" if launcher == "direct" else "slurm" if launcher == "slurm" else None
            if mode is None:
                continue
            profile_id = _safe_profile_id(raw_profile_id, mode=mode, index=index)
            if any(item["profile_id"] == profile_id for item in profiles):
                profile_id = f"{mode}-{index + 1}"
            profiles.append({
                "profile_id": profile_id,
                "mode": mode,
                "label": _EXECUTION_MODE_LABELS[mode],
                "available": registry_configured,
                # A parsed local entry is only registered.  Connectivity is
                # exclusively established by the later deterministic preflight.
                "connection_state": "unknown" if registry_configured else "not_configured",
            })
    elif isinstance(raw_profiles, (list, tuple)):
        for index, raw_profile in enumerate(raw_profiles):
            if not isinstance(raw_profile, Mapping):
                continue
            mode = _safe_execution_mode(raw_profile.get("mode"))
            if mode is None:
                continue
            profile_id = _safe_profile_id(
                raw_profile.get("profile_id", raw_profile.get("id")),
                mode=mode,
                index=index,
            )
            if any(item["profile_id"] == profile_id for item in profiles):
                profile_id = f"{mode}-{index + 1}"
            available = raw_profile.get("available") is True
            connection_state = _safe_connection_state(
                raw_profile.get("connection_state", raw_profile.get("status")),
                available,
            )
            if mode != "local" and available:
                # Profile discovery is never a network probe, even when an
                # older registry reports a generic "ready" boolean.
                connection_state = "unknown"
            profiles.append({
                "profile_id": profile_id,
                "mode": mode,
                # Labels are derived from the mode, never copied from a host alias.
                "label": _EXECUTION_MODE_LABELS[mode],
                "available": available,
                "connection_state": connection_state,
            })

    for fallback in _fallback_execution_profiles():
        if not any(item["mode"] == fallback["mode"] for item in profiles):
            profiles.append(fallback)
    return profiles


def _select_public_execution_profile(
    profiles: list[ExecutionProfile],
    selected_mode: str | None,
    selected_profile_id: str | None,
) -> ExecutionProfile:
    """Resolve a browser selection without persisting or invoking remote work."""
    requested_mode = _safe_execution_mode(selected_mode)
    if isinstance(selected_profile_id, str):
        for profile in profiles:
            if profile["profile_id"] == selected_profile_id:
                if requested_mode is None or profile["mode"] == requested_mode:
                    return profile
    if requested_mode is not None:
        for profile in profiles:
            if profile["mode"] == requested_mode:
                return profile
    return next(profile for profile in profiles if profile["mode"] == "local")


def _execution_profile_summary(profile: ExecutionProfile, registry_state: str) -> str:
    mode_label = profile["label"]
    if profile["mode"] == "local":
        return f"已选择{mode_label}。此页面不会启动进程或提交任务。"
    if profile["available"] and registry_state == "ready":
        return (
            f"已选择{mode_label}配置，已在本机登记，尚未进行连接预检。"
            "连接详情已隐藏；此页面不会发起连接或提交任务。"
        )
    return f"已选择{mode_label}配置，但当前不可用或尚未配置。此页面不会发起连接或提交任务。"


def get_execution_profile_snapshot(
    selected_mode: str | None = None,
    selected_profile_id: str | None = None,
) -> ExecutionProfileSnapshot:
    """Return a redacted, read-only execution-profile selection snapshot.

    The optional registry contract is intentionally narrow:
    ``willy.remote_registry.get_public_execution_snapshot()`` may return only
    public profile IDs, modes, booleans and coarse connection states.  The
    initial ``public_remote_capabilities()`` map is also supported.  This
    facade never exposes raw registry fields and never calls execution APIs.
    """
    raw_snapshot, registry_state = _remote_registry_snapshot()
    profiles = _public_execution_profiles(raw_snapshot)
    profile = _select_public_execution_profile(profiles, selected_mode, selected_profile_id)
    return {
        "registry_state": registry_state,
        "profiles": profiles,
        "selected_mode": profile["mode"],
        "selected_profile_id": profile["profile_id"],
        "summary": _execution_profile_summary(profile, registry_state),
    }


def get_execution_profile_proposal_context(
    selected_mode: str | None,
    selected_profile_id: str | None,
) -> ExecutionProposalContext:
    """Prepare a safe selection intent for the Config Agent handoff.

    The UI supplies this selection intent to the Config Agent.  That backend
    independently resolves the profile against its private registry before it
    freezes ``config.execution.md``; this helper never mutates a plan or
    launches a pipeline.
    """
    snapshot = get_execution_profile_snapshot(selected_mode, selected_profile_id)
    profile = next(
        item for item in snapshot["profiles"]
        if item["profile_id"] == snapshot["selected_profile_id"]
    )
    return {
        "execution_mode": snapshot["selected_mode"],
        "execution_profile_id": snapshot["selected_profile_id"],
        "available": profile["available"],
        "summary": snapshot["summary"],
    }


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


def _legacy_pipeline_group_is_alive(pgid: int) -> bool:
    """Recognize children in a legacy runner's dedicated process group."""
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _latest_transient_run_has_live_evidence() -> bool:
    """Check the newest run-local liveness snapshot without changing status."""
    try:
        registry = RunRegistry(ROOT)
        latest = registry.list_runs(limit=1)
        run_id = latest[0].get("run_id") if latest else None
        if not isinstance(run_id, str):
            return False
        status = registry.get_run_status(run_id, reconcile=False)
        if status.get("state") not in _TRANSIENT_RUN_STATES:
            return False
        return bool(registry.get_live_stage_evidence(run_id, status).get("active"))
    except (OSError, RunRegistryError):
        return False


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
            if _legacy_runner_pid_is_alive(pid) or _legacy_pipeline_group_is_alive(pid):
                return True
        except ValueError:
            pass
        try:
            pid_file.unlink(missing_ok=True)
        except OSError:
            pass

    # A runner can briefly disappear from the project lock while its managed
    # GROMACS child is still writing the run-local heartbeat.  Keep launch and
    # stop controls conservative in that window.
    return _latest_transient_run_has_live_evidence()


def is_pipeline_alive() -> bool:
    """检查流水线进程是否存活（供运行控制使用）。"""
    if pipeline_launch_is_active(ROOT):
        return True
    pid_file = ROOT / ".pipeline.pid"
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            if _legacy_runner_pid_is_alive(pid) or _legacy_pipeline_group_is_alive(pid):
                return True
        except ValueError:
            pass
        try:
            pid_file.unlink(missing_ok=True)
        except OSError:
            pass
    return _latest_transient_run_has_live_evidence()


def reconcile_stale_pipeline_state() -> str | None:
    """Finalize a stale run only after run-local liveness evidence is stale."""
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
        status = registry.get_run_status(run_id, reconcile=False)
        if status.get("state") not in _TRANSIENT_RUN_STATES:
            return None
        evidence = registry.get_live_stage_evidence(run_id, status)
        if evidence.get("active"):
            return None
        expected_revision = status.get("state_revision")
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 0:
            return None

        # Re-read after the evidence check.  A heartbeat or stage result that
        # arrived meanwhile must win over this stale-process conclusion.
        latest_status = registry.get_run_status(run_id, reconcile=False)
        if (
            latest_status.get("state") not in _TRANSIENT_RUN_STATES
            or latest_status.get("state_revision") != expected_revision
        ):
            return None
        registry.mark_aborted(
            run_id,
            event_type="run_aborted_after_process_exit",
            expected_revision=expected_revision,
            event_details={
                "source": "stale_reconciliation",
                "reason": "runner_and_run_local_evidence_stale",
                "stage": evidence.get("stage"),
                "evidence_source": evidence.get("source"),
                "evidence_age_s": evidence.get("evidence_age_s"),
                "checked_revision": expected_revision,
                "grace_s": max(60.0, MDRUN_HEARTBEAT_INTERVAL_S * 3.0),
            },
        )
        from willy.simulation.manifest import clear_stop_request
        clear_stop_request(registry.resolve_run_id(run_id))
        return run_id
    except (OSError, RunRegistryError):
        return None


def get_latest_run_control_state() -> str | None:
    """Return persisted control state without mutating the run.

    Stale-process reconciliation is an explicit maintenance action, never a
    side effect of the frontend polling timer.
    """
    run_id = get_active_run_id()
    if run_id is None:
        choices = RunRegistry(ROOT).list_runs(limit=1)
        run_id = choices[0].get("run_id") if choices else None
    if not isinstance(run_id, str):
        return None
    try:
        return str(RunRegistry(ROOT).get_run_status(run_id, reconcile=False).get("state", "unknown"))
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
    '<div class="structure-viewer-empty" style="width:100%;height:100%;box-sizing:border-box;border-radius:6px;background:#eee9e2;'
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
VIEWER_SPHERE_RENDER_FACTOR = 0.5
_VIEWER_ELEMENT_LEGEND = {
    "H": ("氢", "#FFFFFF"),
    "C": ("碳", "#909090"),
    "N": ("氮", "#3050F8"),
    "O": ("氧", "#FF0D0D"),
    "F": ("氟", "#90E050"),
    "P": ("磷", "#FF8000"),
    "S": ("硫", "#FFFF30"),
    "Cl": ("氯", "#1FF01F"),
    "Br": ("溴", "#A62929"),
    "I": ("碘", "#940094"),
    "Li": ("锂", "#CC80FF"),
    "Na": ("钠", "#AB5CF2"),
    "Mg": ("镁", "#8AFF00"),
    "Ca": ("钙", "#3DFF00"),
    "Zn": ("锌", "#7D80B0"),
}


def _canonical_viewer_element(value: str) -> str | None:
    """Normalize a PDB/MOL2 element token to one rendered by the viewer."""
    token = value.strip().split(".", 1)[0]
    match = re.match(r"[A-Za-z]{1,2}", token)
    if match is None:
        return None
    symbol = match.group(0)
    canonical = symbol[0].upper() + symbol[1:].lower()
    return canonical if canonical in _VIEWER_ELEMENT_LEGEND else None


def _pdb_with_explicit_elements(mol_data: str) -> str:
    """Normalize PDB atom names and element columns for deterministic 3Dmol parsing."""
    normalized_lines: list[str] = []
    for line in mol_data.splitlines():
        if not line.startswith(("ATOM  ", "HETATM")):
            normalized_lines.append(line)
            continue
        # Atom names are authoritative for the Packmol and GROMACS PDB files
        # accepted by this viewer: H4 -> H, O2 -> O and Li100 -> Li.
        element = _canonical_viewer_element(line[12:16])
        if element is None:
            element = _canonical_viewer_element(line[76:78])
        if element is None:
            normalized_lines.append(line)
            continue
        padded = line.ljust(78)
        normalized_lines.append(
            f"{padded[:12]}{element:<4}{padded[16:76]}{element:>2}{padded[78:]}"
        )
    suffix = "\n" if mol_data.endswith("\n") else ""
    return "\n".join(normalized_lines) + suffix


def _viewer_legend_elements(mol_data: str, fmt: str) -> list[str]:
    """Return displayed elements in the Jmol legend order for PDB or MOL2 data."""
    elements: set[str] = set()
    if fmt == "pdb":
        for line in mol_data.splitlines():
            if not line.startswith(("ATOM  ", "HETATM")):
                continue
            element = _canonical_viewer_element(line[12:16])
            if element is None:
                element = _canonical_viewer_element(line[76:78])
            if element is not None:
                elements.add(element)
    elif fmt == "mol2":
        in_atom_section = False
        for line in mol_data.splitlines():
            if line.startswith("@<TRIPOS>ATOM"):
                in_atom_section = True
                continue
            if line.startswith("@<TRIPOS>"):
                in_atom_section = False
                continue
            if not in_atom_section:
                continue
            fields = line.split()
            element = _canonical_viewer_element(fields[5]) if len(fields) > 5 else None
            if element is None and len(fields) > 1:
                element = _canonical_viewer_element(fields[1])
            if element is not None:
                elements.add(element)
    return [symbol for symbol in _VIEWER_ELEMENT_LEGEND if symbol in elements]


def _viewer_legend_html(mol_data: str, fmt: str) -> str:
    """Build the structure-local atom color legend for the dedicated UI area."""
    elements = _viewer_legend_elements(mol_data, fmt)
    if not elements:
        return ""
    items = []
    for symbol in elements:
        name, color = _VIEWER_ELEMENT_LEGEND[symbol]
        items.append(
            '<span style="display:inline-flex;align-items:center;gap:0.32rem;white-space:nowrap">'
            f'<span aria-hidden="true" style="width:0.72rem;height:0.72rem;border-radius:50%;'
            'background:radial-gradient(circle at 30% 28%,rgba(255,255,255,.95) 0 7%,'
            f'rgba(255,255,255,.28) 8%,transparent 25%),radial-gradient(circle at 68% 72%,'
            f'rgba(0,0,0,.46),transparent 58%),{color};border:1px solid #9c958c;'
            'box-shadow:inset -1px -1px 1px rgba(0,0,0,.25),0 1px 1px rgba(0,0,0,.18);'
            'box-sizing:border-box"></span>'
            f"{symbol} {name}</span>"
        )
    return (
        '<div aria-label="原子颜色图例" style="display:flex;flex-wrap:wrap;align-items:center;justify-content:center;'
        'gap:0.35rem 0.8rem;padding:0;color:#5b5148;'
        'font:12px system-ui,sans-serif;line-height:1.25">'
        + "".join(items)
        + "</div>"
    )


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


def get_run_visualization_run_choices() -> list[str]:
    """List valid ``md_run`` directories for the visualization run selector."""
    registry = RunRegistry(ROOT)
    try:
        candidates = [
            directory.name
            for directory in registry.runs_dir.iterdir()
            if directory.is_dir()
        ]
    except OSError:
        return []

    valid: list[str] = []
    for run_id in candidates:
        try:
            registry.resolve_run_id(run_id)
        except RunRegistryError:
            continue
        valid.append(run_id)

    valid.sort(key=_run_directory_numeric_key, reverse=True)
    active_run_id = get_active_run_id()
    if active_run_id in valid:
        valid.remove(active_run_id)
        valid.insert(0, active_run_id)
    return valid


def _current_run_visualization_artifacts(run_id: str | None = None) -> list[dict[str, object]]:
    """List readable PDB/MOL2 files from one authorized visualization run.

    When no run_id is supplied, the active launch lock has priority and the
    newest numerically named run directory is selected for compatibility.
    Labels are run-relative filenames so the browser never receives paths.
    """
    run_id = run_id or _visualization_run_id()
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
        if any(part.startswith(".") for part in relative.parts):
            continue
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


def get_run_visualization_choices(run_id: str | None = None) -> list[str]:
    """List one run's PDB/MOL2 filenames without exposing paths."""
    return [str(item["label"]) for item in _current_run_visualization_artifacts(run_id)]


def get_run_visualization_file_choices(run_id: str | None = None) -> list[str]:
    """Explicit alias for the right-hand visualization file selector."""
    return get_run_visualization_choices(run_id)


def get_run_visualization_data(
    choice: str | None,
    run_id: str | None = None,
) -> dict[str, object] | None:
    """Read one selected structure from the selected run directory."""
    if not isinstance(choice, str) or not choice:
        return None
    for artifact in _current_run_visualization_artifacts(run_id):
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
    sphere_scale = (
        _viewer_style_value(
            sphere_scale, DEFAULT_SPHERE_SCALE, VIEWER_SPHERE_SCALE_RANGE)
        * VIEWER_SPHERE_RENDER_FACTOR
    )
    stick_radius = _viewer_style_value(
        stick_radius, DEFAULT_STICK_RADIUS, VIEWER_STICK_RADIUS_RANGE)

    if fmt == "pdb":
        mol_data = _pdb_with_explicit_elements(mol_data)
    mol_json = json.dumps(mol_data)
    element_colors_json = json.dumps({
        symbol: color for symbol, (_name, color) in _VIEWER_ELEMENT_LEGEND.items()
    })

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
    var model=v.addModel({mol_json},"{fmt}",{{keepH:true}});
    v.setStyle({{}},{{stick:{{radius:{stick_radius:.2f},colorscheme:"Jmol"}},sphere:{{scale:{sphere_scale:.2f},colorscheme:"Jmol"}}}});
    // Use the same fixed color table as the legend for all known elements.
    // This does not depend on the remote 3Dmol build's Jmol palette.
    var elementColors={element_colors_json};
    var metalCations=["Li","Na","Mg","Ca","Zn"];
    model.selectedAtoms({{}}).forEach(function(atom){{
      var rawElement=String(atom.elem||atom.atom||"").replace(/[0-9]+$/,"");
      var element=rawElement.charAt(0).toUpperCase()+rawElement.slice(1,2).toLowerCase();
      var color=elementColors[element];
      if(color){{
        v.setStyle({{serial:atom.serial}},{{
          stick:{{radius:{stick_radius:.2f},color:color}},
          sphere:{{scale:{sphere_scale:.2f},color:color}}
        }});
      }}
      if(metalCations.indexOf(element)>=0){{
        v.setStyle({{serial:atom.serial}},{{sphere:{{scale:{sphere_scale:.2f},color:color||"#909090"}}}});
      }}
    }});
    v.zoomTo();v.render();v.zoom(1.2);
  }}
  init();
}})();
</script></body></html>"""

    import html as _h
    safe_display_name = _h.escape(display_name)
    return (
        f'<div class="structure-viewer-frame" style="background:#eee9e2;border-radius:6px;display:grid;'
        f'grid-template-rows:minmax(0,1fr) auto;height:43.2rem;min-height:0;overflow:hidden">'
        f'<iframe srcdoc="{_h.escape(html)}" style="width:100%;height:100%;border:none" '
        f'sandbox="allow-scripts allow-same-origin"></iframe>'
        f'<div class="structure-viewer-name">{safe_display_name}</div>'
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
    *,
    run_id: str | None = None,
) -> str:
    """Render the selected PDB/MOL2 artifact from one run."""
    viewer_data = get_run_visualization_data(choice, run_id)
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


def render_run_visualization_legend_html(
    choice: str | None,
    *,
    run_id: str | None = None,
) -> str:
    """Render a selected run artifact's atom-color legend outside the viewer."""
    viewer_data = get_run_visualization_data(choice, run_id)
    if viewer_data is None:
        return ""
    return _viewer_legend_html(
        str(viewer_data["content"]),
        str(viewer_data["format"]),
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
        status = registry.get_run_status(selected_run_id, reconcile=False)
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
    if action.get("knowledge_status") in {"retrieved", "not_matched", "unavailable"} and action.get("advice_source") in {"knowledge_base", "llm_unverified"}:
        public["knowledge_status"] = action["knowledge_status"]
        public["knowledge_entries"] = action.get("knowledge_entries", [])[:3] if isinstance(action.get("knowledge_entries"), list) else []
        public["advice_source"] = action["advice_source"]
        public["compatibility_notice"] = str(action.get("compatibility_notice") or "")[:300]
    if isinstance(action.get("options"), list):
        public["options"] = action["options"][:3]
        public["selected_option_id"] = action.get("selected_option_id")
        public["selection_required"] = bool(action.get("selection_required", False))
    editable = action.get("editable_parameters")
    if isinstance(editable, list):
        public["editable_parameters"] = editable[:8]
    return public


def select_pending_action_option(
    action_id: str,
    option_id: str,
    run_id: str | None = None,
    *,
    state_revision: int | None = None,
    config_fingerprint: str | None = None,
) -> str:
    """Select one candidate while keeping the run in awaiting_confirmation."""
    action = get_pending_action(run_id)
    if action is None or action.get("action_id") != action_id:
        return "待确认方案已失效，请刷新工程状态。"
    options = action.get("options")
    if not isinstance(options, list) or len(options) < 2:
        if action.get("selected_option_id") == option_id:
            return "已选择该方案，请回复“确认重跑”以执行。"
        return "当前只有一个可执行方案。"
    if action.get("selected_option_id") == option_id:
        return "已选择该方案，请回复“确认重跑”以执行。"
    expected_revision = action.get("state_revision") if state_revision is None else state_revision
    expected_fingerprint = action.get("config_fingerprint") if config_fingerprint is None else config_fingerprint
    selected_run_id = action.get("run_id")
    if not isinstance(selected_run_id, str) or not isinstance(expected_revision, int) or not isinstance(expected_fingerprint, str):
        return "待确认方案已失效，请刷新工程状态。"
    registry = RunRegistry(ROOT)
    try:
        run_dir = registry.resolve_run_id(selected_run_id)
        from willy.simulation.pending_action import (
            PendingActionError,
            pending_action_lock,
            select_pending_action_option as persist_option,
            load_pending_action,
            public_pending_action,
        )
        with pending_action_lock(run_dir):
            status = registry.get_run_status(selected_run_id, reconcile=False)
            if status.get("state") != "awaiting_confirmation" or status.get("state_revision") != expected_revision:
                raise RunStateConflict("待确认方案已更新")
            private_action = load_pending_action(run_dir)
            if private_action.get("action_id") != action_id or private_action.get("config_sha256") != expected_fingerprint:
                raise RunStateConflict("待确认方案已更新")
            selected = persist_option(run_dir, action_id, option_id)
            updated = dict(status)
            extra = dict(status.get("extra")) if isinstance(status.get("extra"), Mapping) else {}
            extra["pending_action"] = public_pending_action(selected)
            updated["extra"] = extra
            registry.compare_and_swap_status(
                run_dir, expected_revision=expected_revision, status=updated,
                event_type="pending_action_option_selected",
            )
    except (OSError, ValueError, RunRegistryError, PendingActionError, RunStateConflict):
        return "方案选择已失效，请刷新工程状态后重试。"
    ordinal = option_id.rsplit("_", 1)[-1]
    return f"已选择方案{ordinal}；当前仍等待确认，请回复“确认方案{ordinal}”或“确认重跑”。"


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
        manifest = registry._read_registry_manifest(run_dir)
        backend = manifest.get("backend")
        if backend not in {"g16", "g09", "orca"}:
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
            waiting_status = registry.get_run_status(run_id, reconcile=False)
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

    def rejected(reason: str) -> str:
        """Record and expose a bounded validation reason without raw LLM text."""
        public_reason = " ".join(str(reason or "").split())[:360]
        if not public_reason or "/" in public_reason or "\\" in public_reason:
            public_reason = "替代方案未形成可执行的受限 EQ 参数修改"
        try:
            registry.append_decision_trace(run_dir, {
                "decision_id": action_id,
                "action_id": action_id,
                "layer": "simulation",
                "step": EQ_STEP,
                "error_kind": status.get("error_kind", "equilibration_failed"),
                "policy_id": "simulation.eq.user_revision",
                "selected_tool": "tools_retry_eq",
                "tool_effect": "requires_confirmation",
                "result": "rejected_validation",
                "success": False,
                "rejection_reason": public_reason,
            })
        except (OSError, ValueError, RunRegistryError):
            pass
        return f"新的调整方案未通过校验：{public_reason}。原方案仍保持等待确认。"

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
        status = registry.get_run_status(selected_run_id, reconcile=False)
        if status.get("state") != "awaiting_confirmation":
            return "当前工程不在等待确认状态，无法更新方案。"
        from willy.simulation.pending_action import load_pending_action
        current_action = load_pending_action(run_dir)
        if current_action.get("action_id") != action_id or current_action.get("config_sha256") != current_fingerprint:
            raise RunStateConflict("待确认方案已更新")
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
            load_pending_action,
        )
        with pending_action_lock(run_dir):
            current_status = registry.get_run_status(selected_run_id, reconcile=False)
            if (
                current_status.get("state") != "awaiting_confirmation"
                or current_status.get("state_revision") != expected_revision
            ):
                raise RunStateConflict("待确认方案已更新")
            current_action = load_pending_action(run_dir)
            if current_action.get("state") != "pending" or current_action.get("action_id") != action_id:
                raise PendingActionError("待确认方案已失效或不匹配")
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
    except PendingActionError as exc:
        return rejected(str(exc))
    except RunStateConflict:
        return rejected("待确认方案已更新或状态版本已变化，请刷新后重新提交调整")
    except (OSError, ValueError, RunRegistryError):
        return rejected("运行状态或冻结配置校验失败，请刷新后重新提交调整")
    return "已按你的要求更新待确认方案；请审阅新方案后再明确确认。"


def get_run_summary_markdown(run_id: str | None, *, include_error: bool = True) -> str:
    """Render the selected run's public engineering state for the run assistant."""
    run_id = run_id or latest_run_id()
    if run_id is None:
        return "### 工程状态\n\n暂无可读取的运行。"
    try:
        registry = RunRegistry(ROOT)
        status = registry.get_run_status(run_id, reconcile=False)
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
    if include_error and status.get("error"):
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


def _public_run_error_event(
    run_id: str,
    status: Mapping[str, object],
) -> dict[str, str] | None:
    """Return one public, immutable-in-UI error event for a status revision."""
    error = status.get("error")
    if not isinstance(error, str) or not error.strip():
        return None
    revision = status.get("state_revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        return None
    state = status.get("state")
    lines = [
        f"#### 工程错误 · 运行 {run_id}",
        f"{_status_indicator('error')}{error.strip()}",
    ]
    if state == "awaiting_confirmation":
        lines.extend([
            "",
            "已生成新的待确认调整方案；当前未修改配置，未启动重跑。",
        ])
    elif state == "escalated":
        lines.extend(["", "自动处理未完成，当前工程未继续执行。"])
    return {
        "event_id": f"{run_id}:error:{revision}",
        "content": "\n".join(lines),
    }


def _run_status_event_id(
    run_id: str | None,
    status: Mapping[str, object] | None,
    pending_action: Mapping[str, object] | None,
    error_event: Mapping[str, object] | None,
) -> str:
    """Identify a user-visible status transition without tracking heartbeats."""
    state = status.get("state") if isinstance(status, Mapping) else "unavailable"
    step = status.get("step") if isinstance(status, Mapping) else None
    action_id = pending_action.get("action_id") if isinstance(pending_action, Mapping) else None
    error_id = error_event.get("event_id") if isinstance(error_event, Mapping) else None
    safe_state = state if isinstance(state, str) else "unknown"
    safe_step = str(step) if isinstance(step, int) and not isinstance(step, bool) else "none"
    safe_action = action_id if isinstance(action_id, str) and action_id else "none"
    safe_error = error_id if isinstance(error_id, str) and error_id else "none"
    return f"{run_id or 'none'}:status:{safe_state}:{safe_step}:{safe_action}:{safe_error}"


def get_run_panel_snapshot(run_id: str | None = None) -> dict[str, object]:
    """Return one run's public status and pending action from a shared ID.

    The UI refreshes status and adjustment cards together.  Resolving the run
    once prevents a newly-started project from mixing its status with a
    historical run's awaiting-confirmation action.
    """
    selected_run_id = run_id or latest_run_id()
    summary = get_run_summary_markdown(selected_run_id)
    live_summary = get_run_summary_markdown(selected_run_id, include_error=False)
    status: Mapping[str, object] | None = None
    if isinstance(selected_run_id, str):
        try:
            status = RunRegistry(ROOT).get_run_status(selected_run_id, reconcile=False)
        except RunRegistryError:
            status = None
    pending_action = get_pending_action(selected_run_id)
    error_event = (
        _public_run_error_event(selected_run_id, status)
        if isinstance(selected_run_id, str) and isinstance(status, Mapping)
        else None
    )
    return {
        "run_id": selected_run_id,
        "summary": summary,
        "live_summary": live_summary,
        "pending_action": pending_action,
        "error_event": error_event,
        "status_event_id": _run_status_event_id(
            selected_run_id, status, pending_action, error_event
        ),
        # App-owned visual event history is enabled only for this richer snapshot.
        "timeline_events": True,
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
