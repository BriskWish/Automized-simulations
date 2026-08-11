"""Public, redacted batch acceptance report format.

The report deliberately contains only bounded metadata.  It does not read
logs, environment files, command output, or secret material.
"""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from importlib import metadata
import json
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
from typing import Any, Iterable, Mapping


BATCH_REPORT_SCHEMA_VERSION = 1
_SECRET_KEY = re.compile(r"(?:api.?key|secret|token|password|credential|private.?key)", re.I)
_RAW_KEY = re.compile(r"(?:raw|log|stderr|stdout|traceback|command|env(?:ironment)?)", re.I)
_VERSION = re.compile(r"\b\d+(?:\.\d+){1,4}(?:[-+][0-9A-Za-z.-]+)?\b")


class BatchReportError(ValueError):
    """Raised when a public report would violate the redaction contract."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: str | Path) -> str:
    """Return a file digest without retaining its contents."""
    digest = sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def artifact_record(path: str | Path, *, root: str | Path | None = None) -> dict[str, Any]:
    """Create a bounded artifact record using only relative path, size and hash."""
    file_path = Path(path).resolve()
    if not file_path.is_file():
        raise BatchReportError(f"产物不存在: {path}")
    base = Path(root).resolve() if root is not None else file_path.parent
    try:
        relative = file_path.relative_to(base).as_posix()
    except ValueError as exc:
        raise BatchReportError("产物必须位于报告根目录内") from exc
    if relative.startswith("../") or "\\" in relative:
        raise BatchReportError("产物路径无效")
    return {
        "path": relative,
        "size_bytes": file_path.stat().st_size,
        "sha256": sha256_file(file_path),
    }


def config_record(path: str | Path, *, root: str | Path | None = None) -> dict[str, Any]:
    """Return the configuration fingerprint, never the configuration value."""
    record = artifact_record(path, root=root)
    return {"path": record["path"], "sha256": record["sha256"]}


def _package_version(name: str) -> str:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return "source"


def _safe_version(command: str) -> str:
    """Probe a version without persisting command output or executable paths."""
    executable = shutil.which(command)
    if not executable:
        return "unavailable"
    try:
        result = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unavailable"
    text = (result.stdout or result.stderr or "").splitlines()
    match = _VERSION.search(text[0] if text else "")
    return match.group(0) if match else "available"


def collect_tool_versions(commands: Iterable[str] = ("python", "pytest", "gmx", "g16", "g09", "orca", "sobtop")) -> dict[str, str]:
    """Collect allow-listed versions only; no output or paths are retained."""
    versions = {
        "python": platform.python_version(),
        "willy": _package_version("willy"),
    }
    versions.update({command: _safe_version(command) for command in commands if command not in versions})
    return versions


def _check_forbidden(value: Any, *, path: str = "report") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            key_text = str(key)
            if _SECRET_KEY.search(key_text):
                raise BatchReportError(f"报告包含秘密字段: {path}.{key_text}")
            if _RAW_KEY.search(key_text):
                raise BatchReportError(f"报告包含原始内容字段: {path}.{key_text}")
            _check_forbidden(child, path=f"{path}.{key_text}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _check_forbidden(child, path=f"{path}[{index}]")
    elif isinstance(value, str):
        lowered = value.lower()
        if any(marker in lowered for marker in ("bearer ", "api_key=", "token=", "password=")):
            raise BatchReportError(f"报告包含疑似凭据: {path}")


def validate_batch_report(report: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and detach a public report before writing or publishing it."""
    payload = json.loads(json.dumps(dict(report), ensure_ascii=False))
    if payload.get("schema_version") != BATCH_REPORT_SCHEMA_VERSION:
        raise BatchReportError("报告 schema_version 无效")
    for field in ("batch_id", "created_at", "software", "config", "stages", "acceptance"):
        if field not in payload:
            raise BatchReportError(f"报告缺少字段: {field}")
    if not isinstance(payload["stages"], list) or not payload["stages"]:
        raise BatchReportError("报告至少需要一个阶段")
    if not isinstance(payload["acceptance"], Mapping):
        raise BatchReportError("acceptance 必须是对象")
    conclusion = payload["acceptance"].get("conclusion")
    if conclusion not in {"passed", "failed", "blocked", "not_run"}:
        raise BatchReportError("验收结论无效")
    _check_forbidden(payload)
    return payload


def build_batch_report(
    *,
    batch_id: str,
    project_version: str,
    config: Mapping[str, Any],
    stages: list[Mapping[str, Any]],
    acceptance: Mapping[str, Any],
    tools: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Build a report with version, fingerprint, stage and artifact metadata."""
    report = {
        "schema_version": BATCH_REPORT_SCHEMA_VERSION,
        "batch_id": str(batch_id),
        "created_at": _now(),
        "software": {
            "project_version": str(project_version),
            "python": platform.python_version(),
            "platform": f"{platform.system()}-{platform.release()}",
            "tools": dict(tools or collect_tool_versions()),
        },
        "config": dict(config),
        "stages": [dict(stage) for stage in stages],
        "acceptance": dict(acceptance),
    }
    return validate_batch_report(report)


def write_batch_report(path: str | Path, report: Mapping[str, Any]) -> Path:
    """Atomically write one public JSON report."""
    validated = validate_batch_report(report)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_text(json.dumps(validated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)
    return target


def report_from_command_results(
    *,
    batch_id: str,
    project_version: str,
    config: Mapping[str, Any],
    results: Mapping[str, Mapping[str, Any]],
    tools: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Convert command summaries into a report without embedding their output."""
    stages = []
    checks = []
    for name, result in results.items():
        status = str(result.get("status", "not_run"))
        stages.append({
            "name": name,
            "status": status,
            "phase": str(result.get("phase", name)),
            "metrics": {
                str(key): int(value)
                for key, value in dict(result.get("metrics", {})).items()
                if str(key) in {"tests", "passed", "skipped", "failures", "errors"}
                and isinstance(value, int)
            },
            "artifacts": [dict(item) for item in result.get("artifacts", []) if isinstance(item, Mapping)],
        })
        checks.append({"name": name, "status": status})
    conclusion = "passed" if all(item["status"] == "passed" for item in checks) else "failed"
    return build_batch_report(
        batch_id=batch_id,
        project_version=project_version,
        config=config,
        stages=stages,
        acceptance={"conclusion": conclusion, "checks": checks},
        tools=tools,
    )
