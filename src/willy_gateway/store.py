"""Transactional, prompt-free gateway state for private development deployments."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from typing import Iterator
import uuid

from .security import public_key_fingerprint


class GatewayStoreError(RuntimeError):
    """Base error intentionally suitable for mapping to a generic API error."""


class RegistrationDenied(GatewayStoreError):
    pass


class RegistrationLimitReached(GatewayStoreError):
    pass


class RegistrationExpired(GatewayStoreError):
    pass


class AccessDenied(GatewayStoreError):
    pass


class QuotaExceeded(GatewayStoreError):
    pass


class RequestStateError(GatewayStoreError):
    pass


@dataclass(frozen=True)
class Device:
    device_id: str
    public_key: bytes
    key_fingerprint: str
    status: str
    label: str
    client_version: str
    pending_expires_at: str | None = None


@dataclass(frozen=True)
class DeviceGrant:
    device_id: str
    model_aliases: tuple[str, ...]
    expires_at: str | None
    daily_token_limit: int
    monthly_token_limit: int
    max_concurrency: int
    max_requests_per_minute: int


@dataclass(frozen=True)
class Reservation:
    request_id: str
    reserved_tokens: int


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _as_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _as_day(value: datetime) -> str:
    return value.astimezone(timezone.utc).date().isoformat()


class GatewayStore:
    """SQLite implementation of the quota/accounting contract.

    ``BEGIN IMMEDIATE`` makes a single SQLite database process-safe for the
    private test deployment. Production must use the same operations on
    PostgreSQL and retain Redis only as an auxiliary nonce/rate cache.
    """

    def __init__(self, database_path: str | Path):
        self.path = Path(database_path)

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                PRAGMA foreign_keys=ON;

                CREATE TABLE IF NOT EXISTS devices (
                    device_id TEXT PRIMARY KEY,
                    public_key BLOB NOT NULL UNIQUE,
                    key_fingerprint TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'revoked')),
                    label TEXT NOT NULL,
                    client_version TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    pending_expires_at TEXT,
                    last_active_at TEXT,
                    revoked_at TEXT
                );
                CREATE TABLE IF NOT EXISTS device_grants (
                    device_id TEXT PRIMARY KEY REFERENCES devices(device_id) ON DELETE CASCADE,
                    model_aliases TEXT NOT NULL,
                    expires_at TEXT,
                    daily_token_limit INTEGER NOT NULL CHECK (daily_token_limit > 0),
                    monthly_token_limit INTEGER NOT NULL CHECK (monthly_token_limit > 0),
                    max_concurrency INTEGER NOT NULL CHECK (max_concurrency > 0),
                    max_requests_per_minute INTEGER NOT NULL CHECK (max_requests_per_minute > 0)
                );
                CREATE TABLE IF NOT EXISTS used_nonces (
                    device_id TEXT NOT NULL REFERENCES devices(device_id) ON DELETE CASCADE,
                    nonce TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    PRIMARY KEY (device_id, nonce)
                );
                CREATE TABLE IF NOT EXISTS revoked_tokens (
                    jti TEXT PRIMARY KEY,
                    expires_at TEXT NOT NULL,
                    revoked_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS usage_requests (
                    request_id TEXT PRIMARY KEY,
                    device_id TEXT NOT NULL REFERENCES devices(device_id),
                    token_jti TEXT NOT NULL,
                    model_alias TEXT NOT NULL,
                    state TEXT NOT NULL CHECK (state IN ('reserved', 'unknown', 'settled', 'failed', 'expired')),
                    reserved_tokens INTEGER NOT NULL CHECK (reserved_tokens >= 0),
                    input_tokens INTEGER,
                    output_tokens INTEGER,
                    total_tokens INTEGER,
                    created_at TEXT NOT NULL,
                    finalized_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_usage_device_created
                    ON usage_requests(device_id, created_at);
                CREATE TABLE IF NOT EXISTS usage_daily (
                    device_id TEXT NOT NULL REFERENCES devices(device_id),
                    model_alias TEXT NOT NULL,
                    usage_day TEXT NOT NULL,
                    settled_tokens INTEGER NOT NULL DEFAULT 0 CHECK (settled_tokens >= 0),
                    reserved_tokens INTEGER NOT NULL DEFAULT 0 CHECK (reserved_tokens >= 0),
                    PRIMARY KEY (device_id, model_alias, usage_day)
                );
                CREATE TABLE IF NOT EXISTS audit_events (
                    event_id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    device_id TEXT REFERENCES devices(device_id),
                    created_at TEXT NOT NULL,
                    metadata TEXT NOT NULL
                );
                """
            )
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(devices)")}
            if "pending_expires_at" not in columns:
                connection.execute("ALTER TABLE devices ADD COLUMN pending_expires_at TEXT")

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            yield connection
        finally:
            connection.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except Exception:
                connection.rollback()
                raise
            else:
                connection.commit()

    @staticmethod
    def _record_event(connection: sqlite3.Connection, event_type: str, device_id: str | None, metadata: dict[str, object]) -> None:
        connection.execute(
            "INSERT INTO audit_events(event_id, event_type, device_id, created_at, metadata) VALUES (?, ?, ?, ?, ?)",
            (uuid.uuid4().hex, event_type, device_id, _as_timestamp(_utc_now()), json.dumps(metadata, sort_keys=True)),
        )

    def request_registration(
        self,
        *,
        public_key: bytes,
        label: str,
        client_version: str,
        registration_limit: int,
        pending_ttl_s: int,
        auto_approve_limit: int = 0,
        auto_approve_model_aliases: tuple[str, ...] = (),
        auto_approve_daily_token_limit: int | None = None,
        auto_approve_monthly_token_limit: int | None = None,
        auto_approve_max_concurrency: int | None = None,
        auto_approve_max_requests_per_minute: int | None = None,
    ) -> Device:
        """Atomically reserve a slot and optionally auto-approve early devices."""
        now = _utc_now()
        now_text = _as_timestamp(now)
        expires_at = _as_timestamp(now + timedelta(seconds=pending_ttl_s))
        with self._transaction() as connection:
            key_fingerprint = public_key_fingerprint(public_key)
            existing = connection.execute(
                """SELECT device_id, public_key, key_fingerprint, status, label, client_version, pending_expires_at
                FROM devices WHERE public_key = ?""",
                (public_key,),
            ).fetchone()
            if existing is not None:
                device = Device(**dict(existing))
                if device.status == "revoked":
                    raise RegistrationDenied("设备已被撤销")
                if device.status == "pending" and device.pending_expires_at is not None and device.pending_expires_at <= now_text:
                    connection.execute(
                        """UPDATE devices SET label = ?, client_version = ?, pending_expires_at = ?
                        WHERE device_id = ?""",
                        (label, client_version, expires_at, device.device_id),
                    )
                    self._record_event(connection, "device_registration_renewed", device.device_id, {"client_version": client_version})
                    return Device(
                        device_id=device.device_id,
                        public_key=device.public_key,
                        key_fingerprint=device.key_fingerprint,
                        status=device.status,
                        label=label,
                        client_version=client_version,
                        pending_expires_at=expires_at,
                    )
                return device
            occupied = connection.execute(
                """SELECT COUNT(*) AS count FROM devices
                WHERE status = 'approved'
                   OR (status = 'pending' AND (pending_expires_at IS NULL OR pending_expires_at > ?))""",
                (now_text,),
            ).fetchone()["count"]
            if occupied >= registration_limit:
                raise RegistrationLimitReached("注册名额已满")
            device_id = uuid.uuid4().hex
            registration_count = connection.execute(
                "SELECT COUNT(*) AS count FROM audit_events WHERE event_type = 'device_registration_requested'"
            ).fetchone()["count"]
            auto_approve_cap = min(max(0, auto_approve_limit), registration_limit)
            auto_approve = (
                registration_count < auto_approve_cap
                and bool(auto_approve_model_aliases)
                and auto_approve_daily_token_limit is not None
                and auto_approve_monthly_token_limit is not None
                and auto_approve_max_concurrency is not None
                and auto_approve_max_requests_per_minute is not None
            )
            status = "approved" if auto_approve else "pending"
            pending_expires_at = None if auto_approve else expires_at
            try:
                connection.execute(
                    """INSERT INTO devices(device_id, public_key, key_fingerprint, status, label, client_version, created_at, pending_expires_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (device_id, public_key, key_fingerprint, status, label, client_version, now_text, pending_expires_at),
                )
            except sqlite3.IntegrityError as exc:
                raise RegistrationDenied("设备已登记") from exc
            self._record_event(
                connection,
                "device_registration_requested",
                device_id,
                {"client_version": client_version, "auto_approved": auto_approve},
            )
            if auto_approve:
                connection.execute(
                    """INSERT INTO device_grants(device_id, model_aliases, expires_at, daily_token_limit, monthly_token_limit,
                       max_concurrency, max_requests_per_minute) VALUES (?, ?, NULL, ?, ?, ?, ?)""",
                    (
                        device_id,
                        json.dumps(sorted(auto_approve_model_aliases)),
                        auto_approve_daily_token_limit,
                        auto_approve_monthly_token_limit,
                        auto_approve_max_concurrency,
                        auto_approve_max_requests_per_minute,
                    ),
                )
                self._record_event(
                    connection,
                    "device_auto_approved",
                    device_id,
                    {"models": sorted(auto_approve_model_aliases)},
                )
        return Device(device_id, public_key, key_fingerprint, status, label, client_version, pending_expires_at)

    def apply_pending_registration_ttl(self, pending_ttl_s: int) -> None:
        """Give pre-self-service pending rows the configured expiry after a schema upgrade."""
        now = _utc_now()
        expires_at = _as_timestamp(now + timedelta(seconds=pending_ttl_s))
        with self._transaction() as connection:
            connection.execute(
                """UPDATE devices SET pending_expires_at = ?
                WHERE status = 'pending' AND pending_expires_at IS NULL""",
                (expires_at,),
            )

    @staticmethod
    def _grant_from_row(row: sqlite3.Row) -> DeviceGrant:
        try:
            aliases = json.loads(row["model_aliases"])
        except json.JSONDecodeError as exc:  # pragma: no cover - persistent corruption guard
            raise GatewayStoreError("授权记录无效") from exc
        if not isinstance(aliases, list) or not all(isinstance(alias, str) for alias in aliases):
            raise GatewayStoreError("授权记录无效")
        return DeviceGrant(
            device_id=row["device_id"], model_aliases=tuple(aliases), expires_at=row["expires_at"],
            daily_token_limit=row["daily_token_limit"], monthly_token_limit=row["monthly_token_limit"],
            max_concurrency=row["max_concurrency"], max_requests_per_minute=row["max_requests_per_minute"],
        )

    def approve_device(self, device_id: str, grant: DeviceGrant) -> None:
        """Administrative operation; HTTP exposure awaits independent OIDC auth."""
        if grant.device_id != device_id or not grant.model_aliases:
            raise GatewayStoreError("授权配置无效")
        now = _utc_now()
        with self._transaction() as connection:
            device = connection.execute(
                "SELECT status, pending_expires_at FROM devices WHERE device_id = ?", (device_id,)
            ).fetchone()
            if device is None:
                raise AccessDenied("设备不存在")
            if device["status"] == "pending" and device["pending_expires_at"] is not None and device["pending_expires_at"] <= _as_timestamp(now):
                raise RegistrationExpired("待审批申请已过期")
            connection.execute(
                """INSERT INTO device_grants(device_id, model_aliases, expires_at, daily_token_limit, monthly_token_limit,
                   max_concurrency, max_requests_per_minute) VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(device_id) DO UPDATE SET model_aliases=excluded.model_aliases, expires_at=excluded.expires_at,
                   daily_token_limit=excluded.daily_token_limit, monthly_token_limit=excluded.monthly_token_limit,
                   max_concurrency=excluded.max_concurrency, max_requests_per_minute=excluded.max_requests_per_minute""",
                (device_id, json.dumps(sorted(grant.model_aliases)), grant.expires_at, grant.daily_token_limit,
                 grant.monthly_token_limit, grant.max_concurrency, grant.max_requests_per_minute),
            )
            connection.execute(
                "UPDATE devices SET status = 'approved', pending_expires_at = NULL, revoked_at = NULL WHERE device_id = ?",
                (device_id,),
            )
            self._record_event(connection, "device_approved", device_id, {"models": sorted(grant.model_aliases)})

    def revoke_device(self, device_id: str) -> None:
        with self._transaction() as connection:
            result = connection.execute(
                "UPDATE devices SET status = 'revoked', revoked_at = ? WHERE device_id = ?", (_as_timestamp(_utc_now()), device_id)
            )
            if result.rowcount != 1:
                raise AccessDenied("设备不存在")
            self._record_event(connection, "device_revoked", device_id, {})

    def get_device(self, device_id: str) -> Device:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT device_id, public_key, key_fingerprint, status, label, client_version FROM devices WHERE device_id = ?",
                (device_id,),
            ).fetchone()
        if row is None:
            raise AccessDenied("设备不存在")
        return Device(**dict(row))

    def _current_grant(self, connection: sqlite3.Connection, device_id: str, now: datetime) -> DeviceGrant:
        device = connection.execute("SELECT status FROM devices WHERE device_id = ?", (device_id,)).fetchone()
        if device is None or device["status"] != "approved":
            raise AccessDenied("设备未获授权")
        row = connection.execute("SELECT * FROM device_grants WHERE device_id = ?", (device_id,)).fetchone()
        if row is None:
            raise AccessDenied("设备未获授权")
        grant = self._grant_from_row(row)
        if grant.expires_at is not None and grant.expires_at <= _as_timestamp(now):
            raise AccessDenied("设备授权已过期")
        return grant

    def consume_nonce(self, device_id: str, nonce: str, expires_at: datetime) -> bool:
        now = _utc_now()
        with self._transaction() as connection:
            connection.execute("DELETE FROM used_nonces WHERE expires_at <= ?", (_as_timestamp(now),))
            try:
                connection.execute(
                    "INSERT INTO used_nonces(device_id, nonce, expires_at) VALUES (?, ?, ?)",
                    (device_id, nonce, _as_timestamp(expires_at)),
                )
            except sqlite3.IntegrityError:
                return False
        return True

    def validate_access(self, claims: dict[str, object]) -> DeviceGrant:
        device_id, jti, key_fingerprint = claims.get("device_id"), claims.get("jti"), claims.get("key_fingerprint")
        if not isinstance(device_id, str) or not isinstance(jti, str) or not isinstance(key_fingerprint, str):
            raise AccessDenied("访问令牌无效")
        now = _utc_now()
        with self._transaction() as connection:
            revoked = connection.execute("SELECT 1 FROM revoked_tokens WHERE jti = ?", (jti,)).fetchone()
            if revoked is not None:
                raise AccessDenied("访问令牌已撤销")
            device = connection.execute(
                "SELECT key_fingerprint FROM devices WHERE device_id = ?", (device_id,)
            ).fetchone()
            if device is None or device["key_fingerprint"] != key_fingerprint:
                raise AccessDenied("访问令牌授权失效")
            grant = self._current_grant(connection, device_id, now)
            token_models = claims.get("models")
            if not isinstance(token_models, list) or not set(token_models).issubset(set(grant.model_aliases)):
                raise AccessDenied("访问令牌授权失效")
            connection.execute("UPDATE devices SET last_active_at = ? WHERE device_id = ?", (_as_timestamp(now), device_id))
        return grant

    def active_grant_for_device(self, device_id: str) -> DeviceGrant:
        """Return the current device grant after its signed token request verifies."""
        with self._transaction() as connection:
            return self._current_grant(connection, device_id, _utc_now())

    def revoke_token(self, jti: str, expires_at: datetime) -> None:
        with self._transaction() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO revoked_tokens(jti, expires_at, revoked_at) VALUES (?, ?, ?)",
                (jti, _as_timestamp(expires_at), _as_timestamp(_utc_now())),
            )

    @staticmethod
    def _release_row(connection: sqlite3.Connection, row: sqlite3.Row, state: str, now: datetime) -> None:
        connection.execute(
            """UPDATE usage_daily SET reserved_tokens = MAX(0, reserved_tokens - ?)
            WHERE device_id = ? AND model_alias = ? AND usage_day = ?""",
            (row["reserved_tokens"], row["device_id"], row["model_alias"], _as_day(datetime.fromisoformat(row["created_at"]))),
        )
        connection.execute(
            "UPDATE usage_requests SET state = ?, finalized_at = ? WHERE request_id = ?",
            (state, _as_timestamp(now), row["request_id"]),
        )

    def _expire_unknown_reservations(self, connection: sqlite3.Connection, now: datetime, ttl_s: int) -> None:
        cutoff = _as_timestamp(now - timedelta(seconds=ttl_s))
        rows = connection.execute(
            "SELECT * FROM usage_requests WHERE state = 'unknown' AND created_at <= ?", (cutoff,)
        ).fetchall()
        for row in rows:
            self._release_row(connection, row, "expired", now)

    def reserve(
        self,
        *,
        request_id: str,
        device_id: str,
        token_jti: str,
        model_alias: str,
        token_estimate: int,
        unknown_reservation_ttl_s: int,
    ) -> Reservation:
        if token_estimate <= 0:
            raise GatewayStoreError("预留额度无效")
        now = _utc_now()
        with self._transaction() as connection:
            self._expire_unknown_reservations(connection, now, unknown_reservation_ttl_s)
            grant = self._current_grant(connection, device_id, now)
            if model_alias not in grant.model_aliases:
                raise AccessDenied("模型未获授权")
            active_count = connection.execute(
                "SELECT COUNT(*) AS count FROM usage_requests WHERE device_id = ? AND state IN ('reserved', 'unknown')",
                (device_id,),
            ).fetchone()["count"]
            if active_count >= grant.max_concurrency:
                raise QuotaExceeded("并发额度已用尽")
            minute_start = _as_timestamp(now - timedelta(minutes=1))
            recent_count = connection.execute(
                "SELECT COUNT(*) AS count FROM usage_requests WHERE device_id = ? AND created_at >= ?",
                (device_id, minute_start),
            ).fetchone()["count"]
            if recent_count >= grant.max_requests_per_minute:
                raise QuotaExceeded("速率额度已用尽")
            day = _as_day(now)
            day_total = connection.execute(
                "SELECT COALESCE(SUM(settled_tokens + reserved_tokens), 0) AS total FROM usage_daily WHERE device_id = ? AND usage_day = ?",
                (device_id, day),
            ).fetchone()["total"]
            month_prefix = f"{day[:7]}%"
            month_total = connection.execute(
                "SELECT COALESCE(SUM(settled_tokens + reserved_tokens), 0) AS total FROM usage_daily WHERE device_id = ? AND usage_day LIKE ?",
                (device_id, month_prefix),
            ).fetchone()["total"]
            if day_total + token_estimate > grant.daily_token_limit or month_total + token_estimate > grant.monthly_token_limit:
                raise QuotaExceeded("Token 额度不足")
            connection.execute(
                """INSERT INTO usage_daily(device_id, model_alias, usage_day, settled_tokens, reserved_tokens)
                VALUES (?, ?, ?, 0, ?)
                ON CONFLICT(device_id, model_alias, usage_day) DO UPDATE SET reserved_tokens = reserved_tokens + excluded.reserved_tokens""",
                (device_id, model_alias, day, token_estimate),
            )
            connection.execute(
                """INSERT INTO usage_requests(request_id, device_id, token_jti, model_alias, state, reserved_tokens, created_at)
                VALUES (?, ?, ?, ?, 'reserved', ?, ?)""",
                (request_id, device_id, token_jti, model_alias, token_estimate, _as_timestamp(now)),
            )
        return Reservation(request_id=request_id, reserved_tokens=token_estimate)

    def settle(self, request_id: str, *, input_tokens: int, output_tokens: int, total_tokens: int) -> None:
        if min(input_tokens, output_tokens, total_tokens) < 0:
            raise GatewayStoreError("上游用量无效")
        now = _utc_now()
        with self._transaction() as connection:
            row = connection.execute("SELECT * FROM usage_requests WHERE request_id = ?", (request_id,)).fetchone()
            if row is None:
                raise RequestStateError("请求不存在")
            if row["state"] == "settled":
                return
            if row["state"] not in {"reserved", "unknown"}:
                raise RequestStateError("请求不可结算")
            day = _as_day(datetime.fromisoformat(row["created_at"]))
            connection.execute(
                """UPDATE usage_daily SET reserved_tokens = MAX(0, reserved_tokens - ?),
                   settled_tokens = settled_tokens + ? WHERE device_id = ? AND model_alias = ? AND usage_day = ?""",
                (row["reserved_tokens"], total_tokens, row["device_id"], row["model_alias"], day),
            )
            connection.execute(
                """UPDATE usage_requests SET state = 'settled', input_tokens = ?, output_tokens = ?, total_tokens = ?, finalized_at = ?
                WHERE request_id = ?""",
                (input_tokens, output_tokens, total_tokens, _as_timestamp(now), request_id),
            )

    def release(self, request_id: str) -> None:
        now = _utc_now()
        with self._transaction() as connection:
            row = connection.execute("SELECT * FROM usage_requests WHERE request_id = ?", (request_id,)).fetchone()
            if row is None:
                raise RequestStateError("请求不存在")
            if row["state"] in {"failed", "expired"}:
                return
            if row["state"] != "reserved":
                raise RequestStateError("请求不可释放")
            self._release_row(connection, row, "failed", now)

    def mark_unknown(self, request_id: str) -> None:
        with self._transaction() as connection:
            result = connection.execute(
                "UPDATE usage_requests SET state = 'unknown' WHERE request_id = ? AND state = 'reserved'", (request_id,)
            )
            if result.rowcount != 1:
                raise RequestStateError("请求不可标为未知")

    def usage_snapshot(self, device_id: str) -> dict[str, object]:
        now = _utc_now()
        day = _as_day(now)
        with self._transaction() as connection:
            grant = self._current_grant(connection, device_id, now)
            daily = connection.execute(
                "SELECT COALESCE(SUM(settled_tokens), 0) AS settled, COALESCE(SUM(reserved_tokens), 0) AS reserved FROM usage_daily WHERE device_id = ? AND usage_day = ?",
                (device_id, day),
            ).fetchone()
            monthly = connection.execute(
                "SELECT COALESCE(SUM(settled_tokens), 0) AS settled, COALESCE(SUM(reserved_tokens), 0) AS reserved FROM usage_daily WHERE device_id = ? AND usage_day LIKE ?",
                (device_id, f"{day[:7]}%"),
            ).fetchone()
        return {
            "models": list(grant.model_aliases),
            "daily": {"settled_tokens": daily["settled"], "reserved_tokens": daily["reserved"], "limit": grant.daily_token_limit},
            "monthly": {"settled_tokens": monthly["settled"], "reserved_tokens": monthly["reserved"], "limit": grant.monthly_token_limit},
        }

    def admin_snapshot(self, *, registration_limit: int, auto_approve_limit: int = 0) -> dict[str, object]:
        """Return only operational metadata suitable for the local admin page."""
        now = _utc_now()
        day = _as_day(now)
        now_text = _as_timestamp(now)
        minute_start = _as_timestamp(now - timedelta(minutes=1))
        with self._connection() as connection:
            states = {
                row["status"]: row["count"]
                for row in connection.execute("SELECT status, COUNT(*) AS count FROM devices GROUP BY status")
            }
            active = connection.execute(
                "SELECT COUNT(*) AS count FROM usage_requests WHERE state IN ('reserved', 'unknown')"
            ).fetchone()["count"]
            minute_requests = connection.execute(
                "SELECT COUNT(*) AS count FROM usage_requests WHERE created_at >= ?", (minute_start,)
            ).fetchone()["count"]
            daily = connection.execute(
                "SELECT COALESCE(SUM(settled_tokens), 0) AS settled, COALESCE(SUM(reserved_tokens), 0) AS reserved FROM usage_daily WHERE usage_day = ?",
                (day,),
            ).fetchone()
            active_pending = connection.execute(
                """SELECT COUNT(*) AS count FROM devices
                WHERE status = 'pending' AND (pending_expires_at IS NULL OR pending_expires_at > ?)""",
                (now_text,),
            ).fetchone()["count"]
            expired_pending = connection.execute(
                """SELECT COUNT(*) AS count FROM devices
                WHERE status = 'pending' AND pending_expires_at IS NOT NULL AND pending_expires_at <= ?""",
                (now_text,),
            ).fetchone()["count"]
            auto_approved = connection.execute(
                "SELECT COUNT(*) AS count FROM audit_events WHERE event_type = 'device_auto_approved'"
            ).fetchone()["count"]
            registration_count = connection.execute(
                "SELECT COUNT(*) AS count FROM audit_events WHERE event_type = 'device_registration_requested'"
            ).fetchone()["count"]
            auto_approve_cap = min(max(0, auto_approve_limit), registration_limit)
            rows = connection.execute(
                """SELECT d.device_id, d.key_fingerprint, d.status, d.label, d.client_version, d.created_at, d.last_active_at,
                   d.pending_expires_at,
                   CASE WHEN d.status = 'pending' AND d.pending_expires_at IS NOT NULL AND d.pending_expires_at <= ? THEN 1 ELSE 0 END AS registration_expired,
                   g.model_aliases, g.expires_at, g.daily_token_limit, g.monthly_token_limit, g.max_concurrency,
                   g.max_requests_per_minute,
                   COALESCE(day_usage.settled_tokens, 0) AS today_settled_tokens,
                   COALESCE(day_usage.reserved_tokens, 0) AS today_reserved_tokens,
                   COALESCE(active_usage.active_requests, 0) AS active_requests,
                   COALESCE(recent_usage.recent_requests, 0) AS recent_requests
                   FROM devices d
                   LEFT JOIN device_grants g ON g.device_id = d.device_id
                   LEFT JOIN (
                       SELECT device_id, SUM(settled_tokens) AS settled_tokens, SUM(reserved_tokens) AS reserved_tokens
                       FROM usage_daily WHERE usage_day = ? GROUP BY device_id
                   ) day_usage ON day_usage.device_id = d.device_id
                   LEFT JOIN (
                       SELECT device_id, COUNT(*) AS active_requests FROM usage_requests
                       WHERE state IN ('reserved', 'unknown') GROUP BY device_id
                   ) active_usage ON active_usage.device_id = d.device_id
                   LEFT JOIN (
                       SELECT device_id, COUNT(*) AS recent_requests FROM usage_requests
                       WHERE created_at >= ? GROUP BY device_id
                   ) recent_usage ON recent_usage.device_id = d.device_id
                   ORDER BY CASE d.status WHEN 'pending' THEN 0 WHEN 'approved' THEN 1 ELSE 2 END, d.created_at DESC""",
                (now_text, day, minute_start),
            ).fetchall()
        devices: list[dict[str, object]] = []
        for row in rows:
            record = dict(row)
            aliases = record.pop("model_aliases")
            record["model_aliases"] = json.loads(aliases) if isinstance(aliases, str) else []
            devices.append(record)
        return {
            "summary": {
                "pending_devices": active_pending,
                "approved_devices": states.get("approved", 0),
                "revoked_devices": states.get("revoked", 0),
                "expired_pending_devices": expired_pending,
                "registration_limit": registration_limit,
                "registration_slots_used": active_pending + states.get("approved", 0),
                "registration_slots_available": max(0, registration_limit - active_pending - states.get("approved", 0)),
                "auto_approve_limit": auto_approve_cap,
                "auto_approve_window_used": min(registration_count, auto_approve_cap),
                "auto_approved_devices": auto_approved,
                "auto_approve_slots_available": max(0, auto_approve_cap - registration_count),
                "active_requests": active,
                "requests_last_minute": minute_requests,
                "today_settled_tokens": daily["settled"],
                "today_reserved_tokens": daily["reserved"],
            },
            "devices": devices,
        }
