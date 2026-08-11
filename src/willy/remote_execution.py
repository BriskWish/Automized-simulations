"""Deterministic, transport-independent building blocks for remote GROMACS.

This module deliberately contains no SSH client, subprocess invocation, host
name, remote directory, credential, or shell command construction.  A later
transport adapter owns those private details and implements :class:`RemoteTransport`.
The orchestration-facing values here expose only a profile ID, stage identity,
fixed argv, fingerprints, and normalized error facts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Protocol, Sequence
import re
import time


REMOTE_EXECUTION_SCHEMA_VERSION = 1
REMOTE_SYNC_SCHEMA_VERSION = 1
REMOTE_STAGES = frozenset({"box", "em", "eq", "prod", "postprocess"})
GROMACS_SUBCOMMANDS = frozenset({
    "editconf", "grompp", "mdrun", "check", "trjconv", "energy", "msd",
})
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_VERSION_RE = re.compile(r"^[0-9][0-9A-Za-z._ -]{0,119}$")
_PUBLIC_OPERATIONS = frozenset({
    "remote_execution", "remote_preflight", "remote_start", "remote_query",
    "remote_stop", "remote_sync", "remote_lifecycle", "sync_manifest",
    "sync_validation", "sync_receipt", "sync_schema", "sync_path", "sync_size",
    "sync_sha256", "sync_files", "sync_direction", "gromacs_command", "remote_handle",
    "execution_backend", "execution_profile", "retain_remote_run", "launcher_kind",
    "profile_id", "run_id", "attempt_id", "stage", "required_disk_bytes",
})


class RemoteErrorCode(str, Enum):
    """Stable, public-safe reasons for remote execution failures."""

    CONNECTION_FAILED = "remote_connection_failed"
    HOST_KEY_MISMATCH = "remote_host_key_mismatch"
    SYNC_FAILED = "remote_sync_failed"
    DISK_INSUFFICIENT = "remote_disk_insufficient"
    GROMACS_UNAVAILABLE = "remote_gromacs_unavailable"
    JOB_CANCELLED = "remote_job_cancelled"
    JOB_TIMEOUT = "remote_job_timeout"
    INVALID_REQUEST = "remote_request_invalid"
    HASH_MISMATCH = "remote_sync_hash_mismatch"
    PROTOCOL_ERROR = "remote_protocol_error"
    EXECUTION_FAILED = "remote_execution_failed"


_ERROR_FACTS: dict[RemoteErrorCode, tuple[str, str]] = {
    RemoteErrorCode.CONNECTION_FAILED: ("远程连接不可用", "检查已登记的远程 profile 和 SSH agent。"),
    RemoteErrorCode.HOST_KEY_MISMATCH: ("远程主机身份校验失败", "确认 SSH known_hosts 中的主机身份后重试。"),
    RemoteErrorCode.SYNC_FAILED: ("远程阶段文件同步失败", "检查远程连接、磁盘空间和阶段输入后重试。"),
    RemoteErrorCode.DISK_INSUFFICIENT: ("远程磁盘空间不足", "释放远程工作区空间或选择具有足够空间的 profile。"),
    RemoteErrorCode.GROMACS_UNAVAILABLE: ("远程 GROMACS 不可用", "检查 profile 对应的 GROMACS 安装和版本。"),
    RemoteErrorCode.JOB_CANCELLED: ("远程模拟已取消", "确认是否需要从已保存的检查点重新启动。"),
    RemoteErrorCode.JOB_TIMEOUT: ("远程模拟超时", "调整资源或阶段时长后重新提交。"),
    RemoteErrorCode.INVALID_REQUEST: ("远程执行请求无效", "刷新任务配置并重新选择远程 profile。"),
    RemoteErrorCode.HASH_MISMATCH: ("远程阶段文件校验失败", "重新同步该阶段，勿使用未校验的输出继续下游步骤。"),
    RemoteErrorCode.PROTOCOL_ERROR: ("远程执行协议无效", "检查远程执行器与 transport 的版本兼容性。"),
    RemoteErrorCode.EXECUTION_FAILED: ("远程执行失败", "检查远程任务状态后重试或改用本地执行。"),
}


class RemoteExecutionError(RuntimeError):
    """Error with a stable code and no requirement to retain private details."""

    def __init__(
        self,
        code: RemoteErrorCode,
        *,
        operation: str = "remote_execution",
        cause: BaseException | None = None,
    ) -> None:
        self.code = code
        self.operation = _safe_operation(operation)
        self.cause = cause
        super().__init__(_ERROR_FACTS[code][0])

    def public_dict(self) -> dict[str, str]:
        message, hint = _ERROR_FACTS[self.code]
        return {
            "code": self.code.value,
            "operation": self.operation,
            "message": message,
            "hint": hint,
        }


class RemoteHostKeyMismatchError(RuntimeError):
    """A transport may raise this when strict host-key validation rejects a peer."""


class RemoteJobCancelledError(RuntimeError):
    """A transport may raise this when a remote process was cancelled externally."""


class RemoteJobTimeoutError(TimeoutError):
    """A transport may raise this when a remote process reached its time limit."""


def normalize_remote_error(error: BaseException, *, operation: str) -> RemoteExecutionError:
    """Map transport/private failures to an allowlisted public error code."""
    if isinstance(error, RemoteExecutionError):
        return error
    if isinstance(error, RemoteHostKeyMismatchError):
        code = RemoteErrorCode.HOST_KEY_MISMATCH
    elif isinstance(error, RemoteJobCancelledError):
        code = RemoteErrorCode.JOB_CANCELLED
    elif isinstance(error, (RemoteJobTimeoutError, TimeoutError)):
        code = RemoteErrorCode.JOB_TIMEOUT
    elif isinstance(error, ConnectionError):
        code = RemoteErrorCode.CONNECTION_FAILED
    elif isinstance(error, OSError):
        code = RemoteErrorCode.SYNC_FAILED if operation.startswith("remote_sync") else RemoteErrorCode.CONNECTION_FAILED
    else:
        code = RemoteErrorCode.EXECUTION_FAILED
    return RemoteExecutionError(code, operation=operation, cause=error)


def _safe_operation(value: object) -> str:
    text = str(value).strip()
    return text if text in _PUBLIC_OPERATIONS else "remote_execution"


def _require_identifier(value: object, field_name: str) -> str:
    text = str(value)
    if not _IDENTIFIER_RE.fullmatch(text):
        raise RemoteExecutionError(RemoteErrorCode.INVALID_REQUEST, operation=field_name)
    return text


def _require_stage(value: object) -> str:
    stage = str(value)
    if stage not in REMOTE_STAGES:
        raise RemoteExecutionError(RemoteErrorCode.INVALID_REQUEST, operation="stage")
    return stage


def _relative_path(value: str | Path) -> str:
    raw = str(value).replace("\\", "/")
    path = PurePosixPath(raw)
    if not raw or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise RemoteExecutionError(RemoteErrorCode.INVALID_REQUEST, operation="sync_path")
    return path.as_posix()


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _resolved_run_file(run_dir: Path, relative_path: str) -> Path:
    declared = run_dir / relative_path
    if declared.is_symlink():
        raise RemoteExecutionError(RemoteErrorCode.SYNC_FAILED, operation="sync_path")
    candidate = declared.resolve()
    try:
        candidate.relative_to(run_dir.resolve())
    except ValueError as exc:
        raise RemoteExecutionError(RemoteErrorCode.INVALID_REQUEST, operation="sync_path") from exc
    return candidate


def _safe_version(value: object) -> str:
    text = str(value).strip().replace("\n", " ").replace("\r", " ")
    return text if _VERSION_RE.fullmatch(text) else ""


def _nonnegative_int(value: object, operation: str) -> int:
    if isinstance(value, bool):
        raise RemoteExecutionError(RemoteErrorCode.INVALID_REQUEST, operation=operation)
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise RemoteExecutionError(RemoteErrorCode.INVALID_REQUEST, operation=operation) from exc
    if number < 0:
        raise RemoteExecutionError(RemoteErrorCode.INVALID_REQUEST, operation=operation)
    return number


@dataclass(frozen=True)
class RemotePreflightRequest:
    """The profile-independent preflight request exposed to a transport."""

    profile_id: str
    launcher_kind: str
    required_disk_bytes: int = 0
    require_gpu: bool = False
    require_transfer: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "profile_id", _require_identifier(self.profile_id, "profile_id"))
        if self.launcher_kind not in {"direct", "slurm"}:
            raise RemoteExecutionError(RemoteErrorCode.INVALID_REQUEST, operation="launcher_kind")
        object.__setattr__(self, "required_disk_bytes", _nonnegative_int(
            self.required_disk_bytes, "required_disk_bytes",
        ))


@dataclass(frozen=True)
class RemotePreflightObservation:
    """Private transport facts represented only as booleans/counts here."""

    connected: bool
    writable: bool
    disk_free_bytes: int
    gromacs_available: bool
    gromacs_version: str = ""
    gpu_available: bool = False
    transfer_available: bool = True


@dataclass(frozen=True)
class RemotePreflightResult:
    """Sanitized preflight result safe for status and Agent context."""

    profile_id: str
    launcher_kind: str
    ready: bool
    gromacs_available: bool
    gpu_available: bool
    transfer_available: bool
    errors: tuple[RemoteErrorCode, ...] = ()
    gromacs_version: str = ""

    def public_dict(self) -> dict[str, Any]:
        return {
            "schema_version": REMOTE_EXECUTION_SCHEMA_VERSION,
            "profile_id": self.profile_id,
            "launcher_kind": self.launcher_kind,
            "ready": self.ready,
            "gromacs_available": self.gromacs_available,
            "gpu_available": self.gpu_available,
            "transfer_available": self.transfer_available,
            "gromacs_version": self.gromacs_version[:120],
            "errors": [code.value for code in self.errors],
        }


def evaluate_preflight(
    request: RemotePreflightRequest,
    observation: RemotePreflightObservation | None = None,
    *,
    error: BaseException | None = None,
) -> RemotePreflightResult:
    """Normalize a transport preflight observation or failure deterministically."""
    if error is not None:
        normalized = normalize_remote_error(error, operation="remote_preflight")
        return RemotePreflightResult(
            profile_id=request.profile_id,
            launcher_kind=request.launcher_kind,
            ready=False,
            gromacs_available=False,
            gpu_available=False,
            transfer_available=False,
            errors=(normalized.code,),
        )
    if observation is None:
        raise RemoteExecutionError(RemoteErrorCode.PROTOCOL_ERROR, operation="remote_preflight")
    errors: list[RemoteErrorCode] = []
    if not observation.connected or not observation.writable:
        errors.append(RemoteErrorCode.CONNECTION_FAILED)
    try:
        disk_free_bytes = _nonnegative_int(observation.disk_free_bytes, "remote_preflight")
    except RemoteExecutionError:
        return RemotePreflightResult(
            profile_id=request.profile_id,
            launcher_kind=request.launcher_kind,
            ready=False,
            gromacs_available=False,
            gpu_available=False,
            transfer_available=False,
            errors=(RemoteErrorCode.PROTOCOL_ERROR,),
        )
    if disk_free_bytes < request.required_disk_bytes:
        errors.append(RemoteErrorCode.DISK_INSUFFICIENT)
    if not observation.gromacs_available:
        errors.append(RemoteErrorCode.GROMACS_UNAVAILABLE)
    if request.require_gpu and not observation.gpu_available:
        errors.append(RemoteErrorCode.GROMACS_UNAVAILABLE)
    if request.require_transfer and not observation.transfer_available:
        errors.append(RemoteErrorCode.SYNC_FAILED)
    return RemotePreflightResult(
        profile_id=request.profile_id,
        launcher_kind=request.launcher_kind,
        ready=not errors,
        gromacs_available=bool(observation.gromacs_available),
        gpu_available=bool(observation.gpu_available),
        transfer_available=bool(observation.transfer_available),
        errors=tuple(dict.fromkeys(errors)),
        gromacs_version=_safe_version(observation.gromacs_version),
    )


class SyncDirection(str, Enum):
    UPLOAD = "upload"
    DOWNLOAD = "download"


@dataclass(frozen=True)
class StageSyncFile:
    """One run-relative file fingerprint declared for a remote stage transfer."""

    relative_path: str
    size_bytes: int
    sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "relative_path", _relative_path(self.relative_path))
        object.__setattr__(self, "size_bytes", _nonnegative_int(self.size_bytes, "sync_size"))
        if not _SHA256_RE.fullmatch(str(self.sha256)):
            raise RemoteExecutionError(RemoteErrorCode.INVALID_REQUEST, operation="sync_sha256")

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.relative_path,
            "size_bytes": int(self.size_bytes),
            "sha256": self.sha256,
        }


@dataclass(frozen=True)
class StageSyncManifest:
    """A content-addressed file declaration for exactly one remote stage attempt."""

    run_id: str
    stage: str
    attempt_id: str
    direction: SyncDirection
    files: tuple[StageSyncFile, ...]
    schema_version: int = REMOTE_SYNC_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _require_identifier(self.run_id, "run_id"))
        object.__setattr__(self, "stage", _require_stage(self.stage))
        object.__setattr__(self, "attempt_id", _require_identifier(self.attempt_id, "attempt_id"))
        try:
            object.__setattr__(self, "direction", SyncDirection(self.direction))
        except ValueError as exc:
            raise RemoteExecutionError(RemoteErrorCode.INVALID_REQUEST, operation="sync_direction") from exc
        object.__setattr__(self, "files", tuple(self.files))
        if self.schema_version != REMOTE_SYNC_SCHEMA_VERSION:
            raise RemoteExecutionError(RemoteErrorCode.PROTOCOL_ERROR, operation="sync_schema")
        paths = [item.relative_path for item in self.files]
        if not paths or len(paths) != len(set(paths)):
            raise RemoteExecutionError(RemoteErrorCode.INVALID_REQUEST, operation="sync_files")

    @property
    def fingerprint(self) -> str:
        digest = sha256()
        for item in sorted(self.files, key=lambda record: record.relative_path):
            digest.update(f"{item.relative_path}\0{item.size_bytes}\0{item.sha256}\n".encode())
        return digest.hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "stage": self.stage,
            "attempt_id": self.attempt_id,
            "direction": self.direction.value,
            "fingerprint": self.fingerprint,
            "files": [item.to_dict() for item in self.files],
        }


def create_stage_sync_manifest(
    run_dir: str | Path,
    *,
    run_id: str,
    stage: str,
    attempt_id: str,
    direction: SyncDirection,
    relative_paths: Iterable[str | Path],
) -> StageSyncManifest:
    """Fingerprint declared run-local files without allowing path escape."""
    directory = Path(run_dir).resolve()
    records: list[StageSyncFile] = []
    for value in relative_paths:
        relative_path = _relative_path(value)
        source = _resolved_run_file(directory, relative_path)
        if not source.is_file():
            raise RemoteExecutionError(RemoteErrorCode.SYNC_FAILED, operation="sync_manifest")
        records.append(StageSyncFile(relative_path, source.stat().st_size, _sha256_file(source)))
    return StageSyncManifest(run_id, stage, attempt_id, direction, tuple(records))


@dataclass(frozen=True)
class StageSyncReceipt:
    """Transport acknowledgement that intentionally omits remote paths."""

    manifest_fingerprint: str
    files: tuple[StageSyncFile, ...]

    def __post_init__(self) -> None:
        if not _SHA256_RE.fullmatch(str(self.manifest_fingerprint)):
            raise RemoteExecutionError(RemoteErrorCode.PROTOCOL_ERROR, operation="sync_receipt")


@dataclass(frozen=True)
class StageSyncRequest:
    """Private local workspace plus a public-safe transfer declaration."""

    profile_id: str
    local_run_dir: Path
    manifest: StageSyncManifest

    def __post_init__(self) -> None:
        object.__setattr__(self, "profile_id", _require_identifier(self.profile_id, "profile_id"))
        object.__setattr__(self, "local_run_dir", Path(self.local_run_dir).resolve())


@dataclass(frozen=True)
class StageSyncResult:
    profile_id: str
    run_id: str
    stage: str
    attempt_id: str
    direction: SyncDirection
    verified: bool
    error: RemoteErrorCode | None = None

    def public_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "run_id": self.run_id,
            "stage": self.stage,
            "attempt_id": self.attempt_id,
            "direction": self.direction.value,
            "verified": self.verified,
            "error": self.error.value if self.error else "",
        }


def validate_stage_sync_manifest(run_dir: str | Path, manifest: StageSyncManifest) -> None:
    """Verify local files still match their declaration before or after transfer."""
    directory = Path(run_dir).resolve()
    for item in manifest.files:
        candidate = _resolved_run_file(directory, item.relative_path)
        if not candidate.is_file():
            raise RemoteExecutionError(RemoteErrorCode.SYNC_FAILED, operation="sync_validation")
        if candidate.stat().st_size != item.size_bytes or _sha256_file(candidate) != item.sha256:
            raise RemoteExecutionError(RemoteErrorCode.HASH_MISMATCH, operation="sync_validation")


def validate_sync_receipt(manifest: StageSyncManifest, receipt: StageSyncReceipt) -> None:
    """Require exactly the acknowledged files and hashes for one declaration."""
    if receipt.manifest_fingerprint != manifest.fingerprint:
        raise RemoteExecutionError(RemoteErrorCode.HASH_MISMATCH, operation="sync_receipt")
    expected = {item.relative_path: item for item in manifest.files}
    actual = {item.relative_path: item for item in receipt.files}
    if set(expected) != set(actual):
        raise RemoteExecutionError(RemoteErrorCode.HASH_MISMATCH, operation="sync_receipt")
    for path, item in expected.items():
        received = actual[path]
        if received.size_bytes != item.size_bytes or received.sha256 != item.sha256:
            raise RemoteExecutionError(RemoteErrorCode.HASH_MISMATCH, operation="sync_receipt")


class RemoteSignal(str, Enum):
    INTERRUPT = "SIGINT"
    TERMINATE = "SIGTERM"
    KILL = "SIGKILL"


class RemoteProcessState(str, Enum):
    RUNNING = "running"
    FINISHED = "finished"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class RemoteLifecyclePhase(str, Enum):
    RUNNING = "running"
    INTERRUPT = "interrupt"
    TERMINATE = "terminate"
    KILLED = "killed"
    FINISHED = "finished"


@dataclass(frozen=True)
class RemoteGromacsCommand:
    """A fixed argv invocation; it has no shell representation by design."""

    operation: str
    argv: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.operation not in GROMACS_SUBCOMMANDS or len(self.argv) < 2:
            raise RemoteExecutionError(RemoteErrorCode.INVALID_REQUEST, operation="gromacs_command")
        if self.argv[1] != self.operation:
            raise RemoteExecutionError(RemoteErrorCode.INVALID_REQUEST, operation="gromacs_command")
        for argument in self.argv:
            if not isinstance(argument, str) or not argument or "\x00" in argument or "\n" in argument or "\r" in argument:
                raise RemoteExecutionError(RemoteErrorCode.INVALID_REQUEST, operation="gromacs_command")

    def public_dict(self) -> dict[str, str]:
        return {"operation": self.operation}


def build_gromacs_argv(
    executable: str | Path,
    operation: str,
    arguments: Sequence[str | Path] = (),
) -> RemoteGromacsCommand:
    """Build argv without quoting, concatenating, or evaluating a shell string."""
    executable_text = str(executable)
    argv = (executable_text, str(operation), *(str(value) for value in arguments))
    return RemoteGromacsCommand(str(operation), argv)


@dataclass(frozen=True)
class RemoteProcessHandle:
    """Opaque transport handle; never serialize this value into public status."""

    profile_id: str
    run_id: str
    stage: str
    attempt_id: str
    token: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "profile_id", _require_identifier(self.profile_id, "profile_id"))
        object.__setattr__(self, "run_id", _require_identifier(self.run_id, "run_id"))
        object.__setattr__(self, "stage", _require_stage(self.stage))
        object.__setattr__(self, "attempt_id", _require_identifier(self.attempt_id, "attempt_id"))
        if not self.token:
            raise RemoteExecutionError(RemoteErrorCode.PROTOCOL_ERROR, operation="remote_handle")


@dataclass(frozen=True)
class RemoteProcessStatus:
    state: RemoteProcessState
    returncode: int | None = None
    checkpoint_available: bool = False

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "state", RemoteProcessState(self.state))
        except ValueError as exc:
            raise RemoteExecutionError(RemoteErrorCode.PROTOCOL_ERROR, operation="remote_query") from exc
        if self.returncode is not None and (
            isinstance(self.returncode, bool) or not isinstance(self.returncode, int)
        ):
            raise RemoteExecutionError(RemoteErrorCode.PROTOCOL_ERROR, operation="remote_query")


@dataclass(frozen=True)
class RemoteStartRequest:
    profile_id: str
    run_id: str
    stage: str
    attempt_id: str
    command: RemoteGromacsCommand

    def __post_init__(self) -> None:
        object.__setattr__(self, "profile_id", _require_identifier(self.profile_id, "profile_id"))
        object.__setattr__(self, "run_id", _require_identifier(self.run_id, "run_id"))
        object.__setattr__(self, "stage", _require_stage(self.stage))
        object.__setattr__(self, "attempt_id", _require_identifier(self.attempt_id, "attempt_id"))


class RemoteTransport(Protocol):
    """Private adapter boundary. Implementations may use SSH, rsync or fakes."""

    def preflight(self, request: RemotePreflightRequest) -> RemotePreflightObservation:
        """Return observation facts without exposing transport configuration."""

    def sync_stage(self, request: StageSyncRequest) -> StageSyncReceipt:
        """Transfer one declared stage file set and acknowledge fingerprints."""

    def start_direct(self, request: RemoteStartRequest) -> RemoteProcessHandle:
        """Start the fixed argv under a remote direct-execution wrapper."""

    def query_direct(self, handle: RemoteProcessHandle) -> RemoteProcessStatus:
        """Return a direct remote wrapper state for one opaque handle."""

    def signal_direct(self, handle: RemoteProcessHandle, signal: RemoteSignal) -> None:
        """Signal the wrapper's remote process group, not an arbitrary process."""


class RemoteExecutionCore:
    """Small coordinator for preflight and verified stage transfer."""

    def __init__(self, transport: RemoteTransport) -> None:
        self._transport = transport

    def preflight(self, request: RemotePreflightRequest) -> RemotePreflightResult:
        try:
            observation = self._transport.preflight(request)
        except Exception as exc:
            return evaluate_preflight(request, error=exc)
        return evaluate_preflight(request, observation)

    def synchronize(self, request: StageSyncRequest) -> StageSyncResult:
        manifest = request.manifest
        try:
            if manifest.direction is SyncDirection.UPLOAD:
                validate_stage_sync_manifest(request.local_run_dir, manifest)
            receipt = self._transport.sync_stage(request)
            validate_sync_receipt(manifest, receipt)
            if manifest.direction is SyncDirection.DOWNLOAD:
                validate_stage_sync_manifest(request.local_run_dir, manifest)
        except Exception as exc:
            normalized = normalize_remote_error(exc, operation="remote_sync")
            return StageSyncResult(
                request.profile_id, manifest.run_id, manifest.stage, manifest.attempt_id,
                manifest.direction, False, normalized.code,
            )
        return StageSyncResult(
            request.profile_id, manifest.run_id, manifest.stage, manifest.attempt_id,
            manifest.direction, True,
        )


@dataclass(frozen=True)
class RemoteExecutionForRun:
    """A validated remote binding for one frozen run configuration."""

    backend: str
    profile_id: str
    launcher_kind: str
    retain_remote_run: bool
    core: RemoteExecutionCore = field(repr=False, compare=False)

    def preflight(
        self,
        *,
        required_disk_bytes: int = 0,
        require_gpu: bool = False,
        require_transfer: bool = True,
    ) -> RemotePreflightResult:
        return self.core.preflight(RemotePreflightRequest(
            profile_id=self.profile_id,
            launcher_kind=self.launcher_kind,
            required_disk_bytes=required_disk_bytes,
            require_gpu=require_gpu,
            require_transfer=require_transfer,
        ))


def remote_execution_for_run(
    run_config: Mapping[str, Any],
    transport: RemoteTransport,
    *,
    launcher_kind: str = "direct",
) -> RemoteExecutionForRun | None:
    """Return a remote binding only when the frozen config explicitly selects it.

    ``None`` is the compatibility path for local runs.  The function does not
    discover profiles, read credentials, or mutate configuration; profile
    resolution remains the responsibility of the private registry adapter.
    """
    execution = run_config.get("execution", {}) if isinstance(run_config, Mapping) else {}
    md = execution.get("md", {}) if isinstance(execution, Mapping) else {}
    backend = md.get("backend", "local") if isinstance(md, Mapping) else "local"
    if backend == "local":
        return None
    if backend not in {"ssh", "slurm"}:
        raise RemoteExecutionError(RemoteErrorCode.INVALID_REQUEST, operation="execution_backend")
    profile_id = md.get("profile") if isinstance(md, Mapping) else None
    if not isinstance(profile_id, str) or not profile_id:
        raise RemoteExecutionError(RemoteErrorCode.INVALID_REQUEST, operation="execution_profile")
    retain_remote_run = md.get("retain_remote_run", True)
    if not isinstance(retain_remote_run, bool):
        raise RemoteExecutionError(RemoteErrorCode.INVALID_REQUEST, operation="retain_remote_run")
    expected_launcher = "direct" if backend == "ssh" else "slurm"
    if launcher_kind != expected_launcher:
        raise RemoteExecutionError(RemoteErrorCode.INVALID_REQUEST, operation="launcher_kind")
    return RemoteExecutionForRun(
        backend=backend,
        profile_id=_require_identifier(profile_id, "profile_id"),
        launcher_kind=launcher_kind,
        retain_remote_run=retain_remote_run,
        core=RemoteExecutionCore(transport),
    )


def preflight_remote_execution_for_run(
    run_config: Mapping[str, Any],
    transport: RemoteTransport,
    *,
    launcher_kind: str = "direct",
    required_disk_bytes: int = 0,
    require_gpu: bool = False,
    require_transfer: bool = True,
) -> RemotePreflightResult | None:
    """Preflight a selected remote run, returning ``None`` for local execution."""
    binding = remote_execution_for_run(run_config, transport, launcher_kind=launcher_kind)
    if binding is None:
        return None
    return binding.preflight(
        required_disk_bytes=required_disk_bytes,
        require_gpu=require_gpu,
        require_transfer=require_transfer,
    )


@dataclass
class _ManagedDirectProcess:
    handle: RemoteProcessHandle
    phase: RemoteLifecyclePhase = RemoteLifecyclePhase.RUNNING
    phase_started_at: float | None = None
    signals: list[RemoteSignal] = field(default_factory=list)


@dataclass(frozen=True)
class RemoteLifecycleSnapshot:
    """Public-safe lifecycle fact; the opaque handle remains private."""

    profile_id: str
    run_id: str
    stage: str
    attempt_id: str
    phase: RemoteLifecyclePhase
    process_state: RemoteProcessState
    returncode: int | None
    checkpoint_available: bool
    signals: tuple[RemoteSignal, ...]

    def public_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "stage": self.stage,
            "attempt_id": self.attempt_id,
            "phase": self.phase.value,
            "process_state": self.process_state.value,
            "returncode": self.returncode,
            "checkpoint_available": self.checkpoint_available,
            "signals": [signal.value for signal in self.signals],
        }


class DirectSshLifecycle:
    """Deterministic start/query/checkpoint-first stop state machine for SSH."""

    def __init__(
        self,
        transport: RemoteTransport,
        *,
        interrupt_grace_s: float = 30.0,
        terminate_grace_s: float = 10.0,
    ) -> None:
        if interrupt_grace_s < 0 or terminate_grace_s < 0:
            raise ValueError("remote lifecycle grace periods must be non-negative")
        self._transport = transport
        self._interrupt_grace_s = float(interrupt_grace_s)
        self._terminate_grace_s = float(terminate_grace_s)
        self._processes: dict[str, _ManagedDirectProcess] = {}

    @staticmethod
    def _key(handle: RemoteProcessHandle) -> str:
        return "\0".join((handle.profile_id, handle.run_id, handle.stage, handle.attempt_id, handle.token))

    def start(self, request: RemoteStartRequest) -> RemoteLifecycleSnapshot:
        try:
            handle = self._transport.start_direct(request)
        except Exception as exc:
            raise normalize_remote_error(exc, operation="remote_start") from exc
        expected = (request.profile_id, request.run_id, request.stage, request.attempt_id)
        actual = (handle.profile_id, handle.run_id, handle.stage, handle.attempt_id)
        if actual != expected:
            raise RemoteExecutionError(RemoteErrorCode.PROTOCOL_ERROR, operation="remote_start")
        key = self._key(handle)
        if key in self._processes:
            raise RemoteExecutionError(RemoteErrorCode.PROTOCOL_ERROR, operation="remote_start")
        managed = _ManagedDirectProcess(handle)
        self._processes[key] = managed
        return self._snapshot(managed, RemoteProcessStatus(RemoteProcessState.RUNNING))

    def query(self, handle: RemoteProcessHandle) -> RemoteLifecycleSnapshot:
        managed = self._managed(handle)
        try:
            status = self._transport.query_direct(handle)
        except Exception as exc:
            raise normalize_remote_error(exc, operation="remote_query") from exc
        if status.state in {RemoteProcessState.FINISHED, RemoteProcessState.FAILED, RemoteProcessState.CANCELLED}:
            managed.phase = RemoteLifecyclePhase.FINISHED
        return self._snapshot(managed, status)

    def request_stop(self, handle: RemoteProcessHandle, *, now: float | None = None) -> RemoteLifecycleSnapshot:
        managed = self._managed(handle)
        status = self.query(handle)
        if status.phase is RemoteLifecyclePhase.FINISHED:
            return status
        if managed.phase is RemoteLifecyclePhase.RUNNING:
            self._send(managed, RemoteSignal.INTERRUPT)
            managed.phase = RemoteLifecyclePhase.INTERRUPT
            managed.phase_started_at = time.monotonic() if now is None else now
        return self.query(handle)

    def stop(self, handle: RemoteProcessHandle, *, now: float | None = None) -> RemoteLifecycleSnapshot:
        """Request checkpoint-first stop; callers advance escalation by polling."""
        return self.request_stop(handle, now=now)

    def advance_stop(self, handle: RemoteProcessHandle, *, now: float | None = None) -> RemoteLifecycleSnapshot:
        managed = self._managed(handle)
        snapshot = self.query(handle)
        if snapshot.phase is RemoteLifecyclePhase.FINISHED:
            return snapshot
        moment = time.monotonic() if now is None else now
        started = managed.phase_started_at if managed.phase_started_at is not None else moment
        elapsed = moment - started
        if managed.phase is RemoteLifecyclePhase.INTERRUPT and elapsed >= self._interrupt_grace_s:
            self._send(managed, RemoteSignal.TERMINATE)
            managed.phase = RemoteLifecyclePhase.TERMINATE
            managed.phase_started_at = moment
        elif managed.phase is RemoteLifecyclePhase.TERMINATE and elapsed >= self._terminate_grace_s:
            self._send(managed, RemoteSignal.KILL)
            managed.phase = RemoteLifecyclePhase.KILLED
            managed.phase_started_at = moment
        return self.query(handle)

    def _managed(self, handle: RemoteProcessHandle) -> _ManagedDirectProcess:
        try:
            return self._processes[self._key(handle)]
        except KeyError as exc:
            raise RemoteExecutionError(RemoteErrorCode.PROTOCOL_ERROR, operation="remote_lifecycle") from exc

    def _send(self, managed: _ManagedDirectProcess, signal: RemoteSignal) -> None:
        try:
            self._transport.signal_direct(managed.handle, signal)
        except Exception as exc:
            raise normalize_remote_error(exc, operation="remote_stop") from exc
        managed.signals.append(signal)

    @staticmethod
    def _snapshot(managed: _ManagedDirectProcess, status: RemoteProcessStatus) -> RemoteLifecycleSnapshot:
        handle = managed.handle
        return RemoteLifecycleSnapshot(
            profile_id=handle.profile_id,
            run_id=handle.run_id,
            stage=handle.stage,
            attempt_id=handle.attempt_id,
            phase=managed.phase,
            process_state=status.state,
            returncode=status.returncode,
            checkpoint_available=status.checkpoint_available,
            signals=tuple(managed.signals),
        )
