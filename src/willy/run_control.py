"""Deterministic command and parameter validation for Run Assistant controls.

The chat layer must never infer a destructive control action from prose.  This
module recognizes only explicit ``/resume``, ``/fork`` and ``/switch``
commands, then maps each supported configuration field to its owning workflow
step or validates the single allowed run identifier.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
import re
import shlex
from typing import Any

from willy.step_registry import MDP_STEP, PACKMOL_STEP, STEP_REGISTRY


_COMMAND_RE = re.compile(r"^/(?P<kind>resume|fork|switch)(?:\s+(?P<body>.*))?$", re.IGNORECASE | re.DOTALL)
_PATH_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_-]*)*$")
_SWITCH_RUN_ID_RE = re.compile(r"^(?:md__)?(?P<suffix>\d{12})$")
_MAX_CHANGES = 16


class RunControlError(ValueError):
    """Raised when an explicit run-control command is malformed or unsafe."""


@dataclass(frozen=True)
class RunControlCommand:
    kind: str
    changes: dict[tuple[str, ...], Any]
    target_run_id: str | None = None


@dataclass(frozen=True)
class ForkPlan:
    """Validated fork facts with no executable paths or parameter values."""

    restart_step: int
    stopped_step: int
    parameter_paths: tuple[str, ...]


def parse_run_control_command(message: object) -> RunControlCommand | None:
    """Parse one explicit chat command, returning ``None`` for ordinary prose."""
    if not isinstance(message, str):
        return None
    match = _COMMAND_RE.fullmatch(message.strip())
    if match is None:
        return None
    kind = match.group("kind").lower()
    body = (match.group("body") or "").strip()
    if kind == "resume":
        if body:
            raise RunControlError("/resume 不接受参数；请使用 /fork 修改参数后创建子运行")
        return RunControlCommand(kind="resume", changes={})
    if kind == "switch":
        target_run_id = _parse_switch_run_id(body)
        return RunControlCommand(kind="switch", changes={}, target_run_id=target_run_id)
    if not body:
        raise RunControlError("/fork 必须给出至少一个参数，例如 /fork md.eq.tau_p=2")
    return RunControlCommand(kind="fork", changes=_parse_changes(body))


def _parse_switch_run_id(body: str) -> str:
    """Normalize the sole positional argument accepted by ``/switch``."""
    try:
        tokens = shlex.split(body)
    except ValueError as exc:
        raise RunControlError("/switch 参数引号不完整") from exc
    if len(tokens) != 1:
        raise RunControlError(
            "/switch 仅接受一个运行编号，例如 /switch md__202608150002"
        )
    match = _SWITCH_RUN_ID_RE.fullmatch(tokens[0])
    if match is None:
        raise RunControlError(
            "/switch 运行编号无效；仅接受 md__YYYYMMDDHHMM 或 YYYYMMDDHHMM"
        )
    return f"md__{match.group('suffix')}"


def safe_restart_step(status: Mapping[str, object]) -> int:
    """Return the first unfinished, hence last known-safe, workflow step."""
    done = status.get("done_steps", [])
    completed = {
        step for step in done if isinstance(step, int) and not isinstance(step, bool)
        and 1 <= step <= STEP_REGISTRY.total_steps
    } if isinstance(done, list) else set()
    for step in range(1, STEP_REGISTRY.total_steps + 1):
        if step not in completed:
            return step
    raise RunControlError("该运行已完成全部步骤，不能作为中止续跑的来源")


def stopped_step(status: Mapping[str, object], *, restart_step: int) -> int:
    """Use the recorded interrupted step when valid, otherwise safe restart."""
    value = status.get("step")
    if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= STEP_REGISTRY.total_steps:
        return max(value, restart_step)
    return restart_step


def validate_fork_changes(
    config: Mapping[str, object],
    changes: Mapping[tuple[str, ...], object],
    *,
    stopped_at: int,
) -> ForkPlan:
    """Validate existing, changed fields are owned by the stopped/later stage."""
    if not changes:
        raise RunControlError("/fork 必须包含参数修改")
    if len(changes) > _MAX_CHANGES:
        raise RunControlError(f"一次 /fork 最多修改 {_MAX_CHANGES} 个参数")

    paths: list[str] = []
    for path, value in changes.items():
        existing = _value_at_path(config, path)
        if existing == value:
            raise RunControlError(f"参数 {'.'.join(path)} 未发生变化")
        owner_step = parameter_owner_step(path)
        rendered = ".".join(path)
        if owner_step < stopped_at:
            raise RunControlError(
                f"参数 {rendered} 属于第 {owner_step} 步，早于上次停止的第 {stopped_at} 步"
            )
        paths.append(rendered)

    # Forks intentionally replay the protocol construction boundary.  This
    # gives the child its own MDP and simulation manifest rather than reusing
    # a parent's stage permission or checkpoint under changed parameters.
    owner_steps = [parameter_owner_step(path) for path in changes]
    restart_step = min(_fork_restart_step(step) for step in owner_steps)
    return ForkPlan(
        restart_step=restart_step,
        stopped_step=stopped_at,
        parameter_paths=tuple(sorted(paths)),
    )


def parameter_owner_step(path: tuple[str, ...]) -> int:
    """Return the workflow step which first consumes a supported config path."""
    if not path:
        raise RunControlError("参数路径为空")
    root = path[0]
    if root == "topology":
        return 4
    if root == "box":
        return PACKMOL_STEP
    if root == "execution":
        return MDP_STEP
    if root == "md":
        if len(path) >= 2 and path[1] == "eq":
            return 9
        if len(path) >= 2 and path[1] == "prod":
            return 10
        if len(path) >= 2 and path[1] == "run_seed":
            raise RunControlError("md.run_seed 是冻结的运行身份，不能通过 /fork 修改")
        return MDP_STEP
    raise RunControlError(
        f"参数 {'.'.join(path)} 不支持通过 /fork 修改；只允许 topology、box、md 或 execution"
    )


def apply_fork_changes(config: Mapping[str, object], changes: Mapping[tuple[str, ...], object]) -> dict[str, Any]:
    """Return a detached config copy with already-validated existing paths changed."""
    copied = json.loads(json.dumps(config, ensure_ascii=False))
    if not isinstance(copied, dict):
        raise RunControlError("原运行配置格式无效")
    for path, value in changes.items():
        target: dict[str, Any] = copied
        for key in path[:-1]:
            child = target.get(key)
            if not isinstance(child, dict):
                raise RunControlError(f"参数 {'.'.join(path)} 在原运行配置中不存在")
            target = child
        if path[-1] not in target:
            raise RunControlError(f"参数 {'.'.join(path)} 在原运行配置中不存在")
        target[path[-1]] = value
    return copied


def _parse_changes(body: str) -> dict[tuple[str, ...], Any]:
    if body.startswith("{"):
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise RunControlError("/fork 的 JSON 参数无效") from exc
        if not isinstance(payload, Mapping):
            raise RunControlError("/fork 的 JSON 参数必须是对象")
        flattened: dict[tuple[str, ...], Any] = {}
        _flatten_json_changes(payload, (), flattened)
        return _validate_change_paths(flattened)

    try:
        tokens = shlex.split(body)
    except ValueError as exc:
        raise RunControlError("/fork 参数引号不完整") from exc
    if not tokens:
        raise RunControlError("/fork 必须包含参数修改")
    changes: dict[tuple[str, ...], Any] = {}
    for token in tokens:
        if "=" not in token:
            raise RunControlError("/fork 参数必须使用 path=value 形式")
        raw_path, raw_value = token.split("=", 1)
        path = _parse_path(raw_path)
        if path in changes:
            raise RunControlError(f"参数 {raw_path} 被重复指定")
        changes[path] = _decode_scalar(raw_value)
    return _validate_change_paths(changes)


def _flatten_json_changes(value: object, prefix: tuple[str, ...], output: dict[tuple[str, ...], Any]) -> None:
    if isinstance(value, Mapping):
        if not value:
            raise RunControlError(f"参数 {'.'.join(prefix) or '根'} 不能为空对象")
        for raw_key, child in value.items():
            if not isinstance(raw_key, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", raw_key):
                raise RunControlError("/fork JSON 参数名无效")
            _flatten_json_changes(child, (*prefix, raw_key), output)
        return
    if not prefix:
        raise RunControlError("/fork 的 JSON 参数必须是对象")
    if prefix in output:
        raise RunControlError(f"参数 {'.'.join(prefix)} 被重复指定")
    output[prefix] = value


def _validate_change_paths(changes: dict[tuple[str, ...], Any]) -> dict[tuple[str, ...], Any]:
    if not changes:
        raise RunControlError("/fork 必须包含参数修改")
    if len(changes) > _MAX_CHANGES:
        raise RunControlError(f"一次 /fork 最多修改 {_MAX_CHANGES} 个参数")
    return changes


def _parse_path(value: str) -> tuple[str, ...]:
    if not _PATH_RE.fullmatch(value):
        raise RunControlError("/fork 参数路径无效")
    return tuple(value.split("."))


def _decode_scalar(value: str) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _value_at_path(config: Mapping[str, object], path: tuple[str, ...]) -> object:
    value: object = config
    for key in path:
        if not isinstance(value, Mapping) or key not in value:
            raise RunControlError(f"参数 {'.'.join(path)} 在原运行配置中不存在")
        value = value[key]
    return value


def _fork_restart_step(owner_step: int) -> int:
    if owner_step <= 4:
        return owner_step
    # All current forkable runtime parameters need a fresh MDP bundle.  This
    # also creates a child-owned MD manifest before EM/EQ/PROD permissions.
    return MDP_STEP
