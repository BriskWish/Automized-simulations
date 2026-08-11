"""Loopback-only administrator control plane for a private gateway host.

The public gateway app deliberately has no administrator routes.  This app is
started separately and is bound to ``127.0.0.1`` by :mod:`willy_gateway.admin`.
It exposes operational metadata only: credentials, prompts, provider details,
and request bodies never enter its responses.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hmac
import json
from typing import Mapping

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

from .config import GatewaySettings
from .store import AccessDenied, DeviceGrant, GatewayStore, GatewayStoreError, RegistrationExpired


_MAX_TOKEN_LIMIT = 2_000_000_000
_MAX_CONCURRENCY = 1_000
_MAX_REQUESTS_PER_MINUTE = 100_000


class AdminHTTPError(RuntimeError):
    """Controlled error returned by the local administrator API."""

    def __init__(self, status_code: int, code: str):
        self.status_code = status_code
        self.code = code


def _error_response(error: AdminHTTPError) -> JSONResponse:
    return JSONResponse(status_code=error.status_code, content={"error": {"code": error.code}})


def _require_admin_secret(request: Request, settings: GatewaySettings) -> None:
    configured = settings.admin_secret
    supplied = request.headers.get("x-willy-gateway-admin-secret", "")
    if configured is None or not hmac.compare_digest(supplied, configured):
        raise AdminHTTPError(401, "administrator_authentication_required")


async def _read_json_object(request: Request) -> Mapping[str, object]:
    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise AdminHTTPError(400, "invalid_request") from None
    if not isinstance(body, dict):
        raise AdminHTTPError(400, "invalid_request")
    return body


def _positive_int(payload: Mapping[str, object], field: str, maximum: int) -> int:
    value = payload.get(field)
    if isinstance(value, bool):
        raise AdminHTTPError(400, "invalid_grant")
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise AdminHTTPError(400, "invalid_grant") from None
    if not 1 <= parsed <= maximum:
        raise AdminHTTPError(400, "invalid_grant")
    return parsed


def _expires_at(payload: Mapping[str, object]) -> str | None:
    value = payload.get("expires_at")
    if value in (None, ""):
        return None
    if not isinstance(value, str) or len(value) > 64:
        raise AdminHTTPError(400, "invalid_grant")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise AdminHTTPError(400, "invalid_grant") from None
    if parsed.tzinfo is None or parsed <= datetime.now(timezone.utc):
        raise AdminHTTPError(400, "invalid_grant")
    return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _grant_from_payload(payload: Mapping[str, object], device_id: str, settings: GatewaySettings) -> DeviceGrant:
    aliases = payload.get("model_aliases")
    if not isinstance(aliases, list) or not aliases or not all(isinstance(alias, str) for alias in aliases):
        raise AdminHTTPError(400, "invalid_grant")
    unique_aliases = tuple(sorted(set(aliases)))
    if len(unique_aliases) != len(aliases) or not set(unique_aliases).issubset(settings.model_aliases):
        raise AdminHTTPError(400, "invalid_grant")
    return DeviceGrant(
        device_id=device_id,
        model_aliases=unique_aliases,
        expires_at=_expires_at(payload),
        daily_token_limit=_positive_int(payload, "daily_token_limit", _MAX_TOKEN_LIMIT),
        monthly_token_limit=_positive_int(payload, "monthly_token_limit", _MAX_TOKEN_LIMIT),
        max_concurrency=_positive_int(payload, "max_concurrency", _MAX_CONCURRENCY),
        max_requests_per_minute=_positive_int(payload, "max_requests_per_minute", _MAX_REQUESTS_PER_MINUTE),
    )


_ADMIN_PAGE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Willy Gateway</title>
<style>
  :root { color-scheme: light; font-family: system-ui, sans-serif; color: #17212b; background: #f5f7f8; }
  body { margin: 0; }
  main { max-width: 1480px; margin: 0 auto; padding: 24px; }
  header { display: flex; align-items: end; justify-content: space-between; gap: 16px; margin-bottom: 20px; }
  h1 { font-size: 22px; font-weight: 650; margin: 0; }
  p { margin: 5px 0 0; color: #52606d; font-size: 14px; }
  #login { display: flex; align-items: center; gap: 8px; }
  input, button { box-sizing: border-box; font: inherit; }
  input { border: 1px solid #bdc8cf; border-radius: 4px; padding: 7px 8px; min-width: 90px; background: #fff; }
  button { border: 1px solid #216869; border-radius: 4px; padding: 7px 10px; background: #216869; color: #fff; cursor: pointer; }
  button.secondary { color: #1f3b4d; border-color: #93a4ae; background: #fff; }
  button.danger { color: #fff; border-color: #a3323c; background: #a3323c; }
  button:disabled { opacity: .55; cursor: wait; }
  #notice { min-height: 20px; color: #a3323c; font-size: 14px; }
  .metrics { display: grid; grid-template-columns: repeat(4, minmax(150px, 1fr)); gap: 12px; margin: 14px 0 22px; }
  .metric { border: 1px solid #d8e0e5; border-radius: 6px; padding: 13px; background: #fff; }
  .metric span { display: block; color: #5d6d78; font-size: 13px; }
  .metric strong { display: block; margin-top: 4px; font-size: 22px; font-weight: 650; }
  .table-wrap { overflow-x: auto; border: 1px solid #d8e0e5; border-radius: 6px; background: #fff; }
  table { width: 100%; min-width: 1120px; border-collapse: collapse; font-size: 13px; }
  th, td { padding: 10px; border-bottom: 1px solid #e4eaed; text-align: left; vertical-align: top; }
  th { background: #eef3f4; color: #40515c; font-weight: 650; white-space: nowrap; }
  tr:last-child td { border-bottom: 0; }
  .pending { color: #9a6700; } .approved { color: #176d4c; } .revoked, .expired { color: #a3323c; }
  .fingerprint { font-family: ui-monospace, monospace; font-size: 12px; word-break: break-all; }
  .grant { display: grid; grid-template-columns: repeat(2, minmax(110px, 1fr)); gap: 6px; min-width: 280px; }
  .grant label { color: #5d6d78; font-size: 11px; }
  .grant input, .grant select { width: 100%; margin-top: 2px; }
  .grant select { min-height: 34px; border: 1px solid #bdc8cf; border-radius: 4px; padding: 3px; background: #fff; }
  .actions { display: flex; gap: 6px; white-space: nowrap; }
  .muted { color: #647582; }
  @media (max-width: 720px) { main { padding: 14px; } header { align-items: start; flex-direction: column; } .metrics { grid-template-columns: repeat(2, minmax(130px, 1fr)); } }
</style>
</head>
<body>
<main>
  <header>
    <div><h1>Willy Gateway</h1><p>本机管理面。仅显示设备与用量元数据。</p></div>
    <div id="login"><input id="secret" type="password" autocomplete="current-password" placeholder="管理员密钥"><button id="connect">连接</button></div>
  </header>
  <div id="notice"></div>
  <section class="metrics" id="metrics"></section>
  <div class="table-wrap"><table><thead><tr><th>设备</th><th>状态</th><th>今日用量</th><th>活动 / 近 1 分钟</th><th>授权</th><th>操作</th></tr></thead><tbody id="devices"></tbody></table></div>
</main>
<script>
(() => {
  const secretInput = document.querySelector('#secret');
  const connect = document.querySelector('#connect');
  const notice = document.querySelector('#notice');
  const metrics = document.querySelector('#metrics');
  const devices = document.querySelector('#devices');
  let secret = sessionStorage.getItem('willyGatewayAdminSecret') || '';
  secretInput.value = secret;

  const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;', "'":'&#39;'}[char]));
  const format = value => Number(value || 0).toLocaleString('en-US');
  const api = async (path, options = {}) => {
    const headers = { 'X-Willy-Gateway-Admin-Secret': secret, ...(options.headers || {}) };
    const response = await fetch(path, { ...options, headers });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new Error(body.error?.code || 'request_failed');
    }
    return response.json();
  };
  const integerInput = (row, field) => {
    const raw = row.querySelector(`[data-field="${field}"]`).value.trim().replace(/[\\s,]/g, '');
    if (!/^\\d+$/.test(raw)) throw new Error('invalid_grant');
    const value = Number(raw);
    if (!Number.isSafeInteger(value) || value < 1) throw new Error('invalid_grant');
    return value;
  };
  const grantInputs = row => ({
    model_aliases: [...row.querySelector('[data-field="model_aliases"]').selectedOptions].map(option => option.value),
    daily_token_limit: integerInput(row, 'daily_token_limit'),
    monthly_token_limit: integerInput(row, 'monthly_token_limit'),
    max_concurrency: integerInput(row, 'max_concurrency'),
    max_requests_per_minute: integerInput(row, 'max_requests_per_minute'),
    expires_at: row.querySelector('[data-field="expires_at"]').value || null,
  });
  const render = data => {
    const summary = data.summary;
    const grantDefaults = data.grant_defaults || {
      daily_token_limit: 1000000,
      monthly_token_limit: 10000000,
      max_concurrency: 2,
      max_requests_per_minute: 20,
    };
    metrics.innerHTML = [
      ['待审批设备', summary.pending_devices], ['已批准设备', summary.approved_devices],
      ['接入名额', `${summary.registration_slots_used} / ${summary.registration_limit}`], ['剩余接入名额', summary.registration_slots_available],
      ['自动批准窗口', `${summary.auto_approve_window_used} / ${summary.auto_approve_limit}`], ['自动批准剩余', summary.auto_approve_slots_available],
      ['当前并发', summary.active_requests], ['近 1 分钟请求', summary.requests_last_minute],
      ['今日已结算 Token', format(summary.today_settled_tokens)], ['今日预留 Token', format(summary.today_reserved_tokens)],
    ].map(([label, value]) => `<article class="metric"><span>${label}</span><strong>${value}</strong></article>`).join('');
    devices.innerHTML = data.devices.map(device => {
      const configuredAliases = Array.isArray(device.model_aliases) ? device.model_aliases : [];
      const selectedAliases = configuredAliases.length ? configuredAliases : (!device.grant && data.model_aliases.length === 1 ? [data.model_aliases[0]] : []);
      const aliases = data.model_aliases.map(alias => `<option value="${esc(alias)}" ${selectedAliases.includes(alias) ? 'selected' : ''}>${esc(alias)}</option>`).join('');
      const grant = device.grant || {};
      const expiry = grant.expires_at || '';
      const registration = device.registration_expired ? '申请已过期' : device.status;
      const pendingExpiry = device.pending_expires_at ? `<br><span class="muted">申请截止：${esc(device.pending_expires_at)}</span>` : '';
      return `<tr data-device-id="${esc(device.device_id)}"><td><strong>${esc(device.label)}</strong><br><span class="fingerprint">${esc(device.key_fingerprint)}</span><br><span class="muted">${esc(device.client_version)} · ${esc(device.last_active_at || '尚未调用')}</span>${pendingExpiry}</td><td><strong class="${device.registration_expired ? 'expired' : esc(device.status)}">${esc(registration)}</strong></td><td>${format(device.today_settled_tokens)} 已结算<br>${format(device.today_reserved_tokens)} 预留</td><td>${format(device.active_requests)} / ${format(device.recent_requests)}</td><td><div class="grant"><label>模型<select multiple data-field="model_aliases">${aliases}</select></label><label>到期<input data-field="expires_at" type="text" placeholder="2026-08-10T00:00:00+08:00" value="${esc(expiry)}"></label><label>每日 Token<input data-field="daily_token_limit" type="text" inputmode="numeric" autocomplete="off" value="${esc(grant.daily_token_limit || grantDefaults.daily_token_limit)}"></label><label>每月 Token<input data-field="monthly_token_limit" type="text" inputmode="numeric" autocomplete="off" value="${esc(grant.monthly_token_limit || grantDefaults.monthly_token_limit)}"></label><label>最大并发<input data-field="max_concurrency" type="text" inputmode="numeric" autocomplete="off" value="${esc(grant.max_concurrency || grantDefaults.max_concurrency)}"></label><label>每分钟请求<input data-field="max_requests_per_minute" type="text" inputmode="numeric" autocomplete="off" value="${esc(grant.max_requests_per_minute || grantDefaults.max_requests_per_minute)}"></label></div></td><td><div class="actions"><button data-action="approve">批准</button><button class="danger" data-action="revoke">撤销</button></div></td></tr>`;
    }).join('') || '<tr><td colspan="6" class="muted">尚无注册设备。</td></tr>';
  };
  const refresh = async () => {
    if (!secret) return;
    try { render(await api('/api/v1/admin/snapshot')); notice.textContent = ''; }
    catch (error) { notice.textContent = `无法读取管理数据：${error.message}`; }
  };
  connect.addEventListener('click', () => { secret = secretInput.value; sessionStorage.setItem('willyGatewayAdminSecret', secret); refresh(); });
  devices.addEventListener('click', async event => {
    const action = event.target.dataset.action;
    if (!action) return;
    const row = event.target.closest('tr');
    const button = event.target;
    button.disabled = true;
    try {
      const options = action === 'approve' ? { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(grantInputs(row)) } : { method: 'POST' };
      await api(`/api/v1/admin/devices/${row.dataset.deviceId}/${action}`, options);
      await refresh();
    } catch (error) { notice.textContent = `操作未完成：${error.message}`; }
    finally { button.disabled = false; }
  });
  if (secret) refresh();
  window.setInterval(refresh, 5000);
})();
</script>
</body>
</html>"""


def _admin_snapshot(store: GatewayStore, settings: GatewaySettings) -> dict[str, object]:
    snapshot = store.admin_snapshot(
        registration_limit=settings.registration_limit,
        auto_approve_limit=settings.auto_approve_limit,
    )
    devices: list[dict[str, object]] = []
    for device in snapshot["devices"]:  # type: ignore[index]
        record = dict(device)
        grant_fields = (
            "model_aliases", "expires_at", "daily_token_limit", "monthly_token_limit",
            "max_concurrency", "max_requests_per_minute",
        )
        grant = {field: record.pop(field) for field in grant_fields}
        aliases = grant["model_aliases"]
        record["model_aliases"] = aliases if isinstance(aliases, list) else []
        record["grant"] = grant if grant["daily_token_limit"] is not None else None
        devices.append(record)
    return {
        "summary": snapshot["summary"],
        "devices": devices,
        "model_aliases": sorted(settings.model_aliases),
        "grant_defaults": {
            "daily_token_limit": settings.default_daily_token_limit,
            "monthly_token_limit": settings.default_monthly_token_limit,
            "max_concurrency": settings.default_max_concurrency,
            "max_requests_per_minute": settings.default_max_requests_per_minute,
        },
    }


def create_admin_app(settings: GatewaySettings, *, store: GatewayStore | None = None) -> FastAPI:
    """Build an admin ASGI app; the launcher is responsible for loopback binding."""
    if settings.admin_secret is None:
        raise ValueError("WILLY_GATEWAY_ADMIN_SECRET is required for the local admin application")
    gateway_store = store or GatewayStore(settings.database_path)
    gateway_store.initialize()
    gateway_store.apply_pending_registration_ttl(settings.pending_registration_ttl_s)
    app = FastAPI(title="Willy Gateway Admin", docs_url=None, redoc_url=None, openapi_url=None)

    @app.exception_handler(AdminHTTPError)
    async def controlled_error(_: Request, error: AdminHTTPError) -> JSONResponse:
        return _error_response(error)

    @app.get("/", include_in_schema=False)
    async def admin_page() -> HTMLResponse:
        return HTMLResponse(_ADMIN_PAGE, headers={"Cache-Control": "no-store"})

    @app.get("/api/v1/admin/snapshot")
    async def snapshot(request: Request) -> JSONResponse:
        _require_admin_secret(request, settings)
        return JSONResponse(_admin_snapshot(gateway_store, settings), headers={"Cache-Control": "no-store"})

    @app.post("/api/v1/admin/devices/{device_id}/approve")
    async def approve(request: Request, device_id: str) -> JSONResponse:
        _require_admin_secret(request, settings)
        grant = _grant_from_payload(await _read_json_object(request), device_id, settings)
        try:
            gateway_store.approve_device(device_id, grant)
        except AccessDenied:
            raise AdminHTTPError(404, "device_not_found") from None
        except RegistrationExpired:
            raise AdminHTTPError(409, "registration_expired") from None
        except GatewayStoreError:
            raise AdminHTTPError(400, "invalid_grant") from None
        return JSONResponse({"status": "approved", "device_id": device_id})

    @app.post("/api/v1/admin/devices/{device_id}/revoke")
    async def revoke(request: Request, device_id: str) -> JSONResponse:
        _require_admin_secret(request, settings)
        try:
            gateway_store.revoke_device(device_id)
        except AccessDenied:
            raise AdminHTTPError(404, "device_not_found") from None
        return JSONResponse({"status": "revoked", "device_id": device_id})

    return app
