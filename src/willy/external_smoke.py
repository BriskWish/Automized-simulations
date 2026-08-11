"""Declarative, opt-in external scientific-tool smoke preflight and evidence.

The registry never starts a scientific calculation by itself.  External input
bundles are versioned and hash-checked before a test can consume them.  The
evidence writer deliberately distinguishes a ready preflight from a completed
scientific execution so release records cannot overstate their coverage.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Iterable, Mapping, Sequence
import argparse
import json
import os
import re
import tempfile

from willy.env_registry import ResolvedTool, resolve_tool


SMOKE_EVIDENCE_SCHEMA_VERSION = 2
SMOKE_FIXTURE_SCHEMA_VERSION = 1
SMOKE_FIXTURE_MANIFEST = "fixture-manifest.json"
SMOKE_FIXTURE_ENV = "WILLY_EXTERNAL_SMOKE_FIXTURES"
SMOKE_CASES_ENV = "WILLY_EXTERNAL_SMOKE_CASES"
SMOKE_REQUIRED_ENV = "WILLY_EXTERNAL_SMOKE_REQUIRED"
SMOKE_EVIDENCE_ENV = "WILLY_EXTERNAL_SMOKE_EVIDENCE_DIR"
_SAFE_ID = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class ExternalSmokeCase:
    case_id: str
    description: str
    tools: tuple[str, ...]
    fixture_files: tuple[str, ...] = ()
    required_outputs: tuple[str, ...] = ()
    timeout_s: int = 120


@dataclass(frozen=True)
class FixtureIntegrity:
    """Hash-checked status for one case's fixture inputs."""

    ready: bool
    bundle_id: str | None
    manifest_sha256: str | None
    missing_files: tuple[str, ...]
    issues: tuple[str, ...]
    files: tuple[Mapping[str, object], ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "ready": self.ready,
            "bundle_id": self.bundle_id,
            "manifest_sha256": self.manifest_sha256,
            "missing_files": list(self.missing_files),
            "issues": list(self.issues),
            "files": [dict(item) for item in self.files],
        }


@dataclass(frozen=True)
class SmokePreflight:
    case_id: str
    ready: bool
    missing_tools: tuple[str, ...]
    missing_fixtures: tuple[str, ...]
    fixture_issues: tuple[str, ...]
    fixture_integrity: FixtureIntegrity
    capabilities: Mapping[str, Mapping[str, str | None]]

    def to_dict(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "ready": self.ready,
            "missing_tools": list(self.missing_tools),
            "missing_fixtures": list(self.missing_fixtures),
            "fixture_issues": list(self.fixture_issues),
            "fixture_integrity": self.fixture_integrity.to_dict(),
            "capabilities": {key: dict(value) for key, value in self.capabilities.items()},
        }


SMOKE_CASES: tuple[ExternalSmokeCase, ...] = (
    ExternalSmokeCase(
        "gromacs_minimal", "GROMACS 可执行、版本和最小三阶段输入的受控验收",
        ("gmx",),
        (
            "gromacs_minimal/topol.top", "gromacs_minimal/model.pdb",
            "gromacs_minimal/em.mdp", "gromacs_minimal/eq.mdp",
            "gromacs_minimal/prod.mdp",
        ),
        timeout_s=900,
    ),
    ExternalSmokeCase(
        "sobtop_ec", "Sobtop GAFF/UFF 的 EC.mol2 + EC.chg 最小验收",
        (), ("sobtop_ec/EC.mol2", "sobtop_ec/EC.chg"), ("EC.itp", "EC.gro"), timeout_s=120,
    ),
    ExternalSmokeCase(
        "g16_minimal", "Gaussian 16 + formchk 最小单分子验收",
        ("g16", "formchk"), ("g16_minimal/input.gjf",), timeout_s=900,
    ),
    ExternalSmokeCase(
        "orca_minimal", "ORCA + orca_2mkl 最小单分子验收",
        ("orca", "orca_2mkl"), ("orca_minimal/input.gjf",), timeout_s=900,
    ),
    ExternalSmokeCase(
        "multiwfn_minimal", "内置 Multiwfn RESP 最小输入验收",
        ("multiwfn",), ("multiwfn_minimal/input.fchk",), timeout_s=600,
    ),
    ExternalSmokeCase(
        "ligpargen_minimal", "LigParGen + BOSS OPLS-AA 中性分子验收",
        ("ligpargen", "boss", "obabel", "csh"), ("ligpargen_minimal/input.mol2",), timeout_s=900,
    ),
)

_BY_ID = {case.case_id: case for case in SMOKE_CASES}


def get_smoke_case(case_id: str) -> ExternalSmokeCase:
    try:
        return _BY_ID[case_id]
    except KeyError as exc:
        raise ValueError(f"未知外部 smoke case: {case_id}") from exc


def selected_smoke_cases(raw: str | None = None) -> tuple[ExternalSmokeCase, ...]:
    """Return selected cases, defaulting to all only in explicit smoke mode."""
    value = (raw if raw is not None else os.environ.get(SMOKE_CASES_ENV, "all")).strip()
    if not value or value.lower() == "all":
        return SMOKE_CASES
    ids = tuple(item.strip() for item in value.split(",") if item.strip())
    if not ids:
        raise ValueError("外部 smoke case 列表为空")
    if len(set(ids)) != len(ids):
        raise ValueError("外部 smoke case 列表包含重复项")
    return tuple(get_smoke_case(case_id) for case_id in ids)


def smoke_required() -> bool:
    return os.environ.get(SMOKE_REQUIRED_ENV, "").strip().lower() in {"1", "true", "yes"}


def fixture_root() -> Path | None:
    raw = os.environ.get(SMOKE_FIXTURE_ENV, "").strip()
    return Path(raw).expanduser().resolve() if raw else None


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_fixture_path(root: Path, relative: str) -> Path | None:
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def _fixture_integrity(case: ExternalSmokeCase, root: Path | None) -> FixtureIntegrity:
    if root is None:
        return FixtureIntegrity(False, None, None, case.fixture_files, ("fixture_root_not_configured",), ())
    if not root.is_dir():
        return FixtureIntegrity(False, None, None, case.fixture_files, ("fixture_root_not_directory",), ())

    manifest_path = root / SMOKE_FIXTURE_MANIFEST
    if not manifest_path.is_file():
        return FixtureIntegrity(False, None, None, case.fixture_files, ("fixture_manifest_missing",), ())

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return FixtureIntegrity(False, None, None, case.fixture_files, ("fixture_manifest_invalid",), ())
    if not isinstance(manifest, dict) or manifest.get("schema_version") != SMOKE_FIXTURE_SCHEMA_VERSION:
        return FixtureIntegrity(False, None, None, case.fixture_files, ("fixture_manifest_schema_invalid",), ())
    bundle_id = manifest.get("bundle_id")
    cases = manifest.get("cases")
    if not isinstance(bundle_id, str) or not bundle_id.strip() or not isinstance(cases, dict):
        return FixtureIntegrity(False, None, None, case.fixture_files, ("fixture_manifest_contract_invalid",), ())
    entry = cases.get(case.case_id)
    if not isinstance(entry, dict) or not isinstance(entry.get("files"), dict):
        return FixtureIntegrity(False, bundle_id, _file_sha256(manifest_path), case.fixture_files, ("fixture_case_missing",), ())

    expected = entry["files"]
    missing: list[str] = []
    issues: list[str] = []
    files: list[Mapping[str, object]] = []
    declared = set(case.fixture_files)
    if set(expected) != declared:
        issues.append("fixture_manifest_file_set_mismatch")

    for relative in case.fixture_files:
        path = _safe_fixture_path(root, relative)
        if path is None:
            issues.append(f"fixture_path_invalid:{relative}")
            continue
        if not path.is_file():
            missing.append(relative)
            continue
        metadata = expected.get(relative)
        if not isinstance(metadata, dict):
            issues.append(f"fixture_manifest_entry_missing:{relative}")
            continue
        expected_sha256 = metadata.get("sha256")
        expected_size = metadata.get("size_bytes")
        if not isinstance(expected_sha256, str) or not _SHA256.fullmatch(expected_sha256):
            issues.append(f"fixture_manifest_hash_invalid:{relative}")
            continue
        if not isinstance(expected_size, int) or expected_size < 0:
            issues.append(f"fixture_manifest_size_invalid:{relative}")
            continue
        actual_size = path.stat().st_size
        actual_sha256 = _file_sha256(path)
        if actual_size != expected_size:
            issues.append(f"fixture_size_mismatch:{relative}")
        if actual_sha256 != expected_sha256:
            issues.append(f"fixture_hash_mismatch:{relative}")
        files.append({"path": relative, "size_bytes": actual_size, "sha256": actual_sha256})

    return FixtureIntegrity(
        not missing and not issues,
        bundle_id,
        _file_sha256(manifest_path),
        tuple(missing),
        tuple(issues),
        tuple(files),
    )


def preflight(case: ExternalSmokeCase, *, root: str | Path | None = None) -> SmokePreflight:
    """Check declared capabilities plus a hash-verified fixture bundle only."""
    fixtures = Path(root).resolve() if root is not None else fixture_root()
    resolved: dict[str, ResolvedTool] = {tool_id: resolve_tool(tool_id) for tool_id in case.tools}
    capabilities = {tool_id: tool.as_public_dict() for tool_id, tool in resolved.items()}
    missing_tools = tuple(tool_id for tool_id, tool in resolved.items() if not tool.available)
    integrity = _fixture_integrity(case, fixtures)
    return SmokePreflight(
        case_id=case.case_id,
        ready=not missing_tools and integrity.ready,
        missing_tools=missing_tools,
        missing_fixtures=integrity.missing_files,
        fixture_issues=integrity.issues,
        fixture_integrity=integrity,
        capabilities=capabilities,
    )


def _evidence_artifact(path: Path, root: Path | None) -> dict[str, object] | None:
    if not path.is_file():
        return None
    relative = path.name
    if root is not None:
        try:
            relative = path.resolve().relative_to(root).as_posix()
        except ValueError:
            pass
    return {"path": relative, "size_bytes": path.stat().st_size, "sha256": _file_sha256(path)}


def _safe_evidence_path(value: object) -> bool:
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        return False
    return all(part not in {"", ".", ".."} for part in Path(value).parts)


def _atomic_json(path: Path, payload: Mapping[str, object]) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        Path(temporary).replace(path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def write_smoke_evidence(
    case: ExternalSmokeCase,
    preflight_result: SmokePreflight,
    *,
    success: bool,
    outputs: Iterable[Path] = (),
    root: str | Path | None = None,
    phase: str = "execution",
    execution_attempted: bool = True,
    command_label: str = "",
    failure_summary: str = "",
) -> Path | None:
    """Archive redacted, integrity-addressed evidence after an opt-in test.

    ``execution_attempted=False`` creates a preflight record, never an
    execution-success record.  Callers must supply only an operation label,
    not raw command-line arguments or logs, to keep the evidence public-safe.
    """
    raw_directory = os.environ.get(SMOKE_EVIDENCE_ENV, "").strip()
    if not raw_directory:
        return None
    if not _SAFE_ID.fullmatch(phase):
        raise ValueError("evidence phase 必须为安全标识符")
    directory = Path(raw_directory).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    base = Path(root).resolve() if root is not None else fixture_root()
    artifacts = [artifact for output in outputs if (artifact := _evidence_artifact(Path(output), base)) is not None]
    outcome = "passed" if success else "failed"
    if not execution_attempted:
        outcome = "preflight_ready" if success else "preflight_unready"
    payload = {
        "schema_version": SMOKE_EVIDENCE_SCHEMA_VERSION,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "case": asdict(case),
        "phase": phase,
        "outcome": outcome,
        "success": bool(success),
        "execution_attempted": bool(execution_attempted),
        "command_label": command_label.strip()[:120],
        "failure_summary": failure_summary.strip()[:500],
        "preflight": preflight_result.to_dict(),
        "outputs": artifacts,
    }
    path = directory / f"{case.case_id}.{phase}.json"
    _atomic_json(path, payload)
    return path


def verify_evidence(
    directory: str | Path,
    cases: Sequence[ExternalSmokeCase],
    *,
    phase: str = "preflight",
    require_execution: bool = False,
) -> tuple[str, ...]:
    """Return durable evidence-contract violations without trusting its claim."""
    root = Path(directory)
    issues: list[str] = []
    for case in cases:
        path = root / f"{case.case_id}.{phase}.json"
        if not path.is_file():
            issues.append(f"evidence_missing:{case.case_id}:{phase}")
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            issues.append(f"evidence_invalid:{case.case_id}:{phase}")
            continue
        if not isinstance(payload, dict) or payload.get("schema_version") != SMOKE_EVIDENCE_SCHEMA_VERSION:
            issues.append(f"evidence_schema_invalid:{case.case_id}:{phase}")
            continue
        case_payload = payload.get("case")
        preflight_payload = payload.get("preflight")
        if not isinstance(case_payload, dict) or not isinstance(preflight_payload, dict):
            issues.append(f"evidence_payload_invalid:{case.case_id}:{phase}")
            continue
        if payload.get("phase") != phase or case_payload.get("case_id") != case.case_id:
            issues.append(f"evidence_identity_invalid:{case.case_id}:{phase}")
        if preflight_payload.get("case_id") != case.case_id:
            issues.append(f"evidence_preflight_invalid:{case.case_id}:{phase}")
        attempted = payload.get("execution_attempted") is True
        if require_execution and not attempted:
            issues.append(f"evidence_execution_missing:{case.case_id}:{phase}")
        artifacts = payload.get("outputs")
        if not isinstance(artifacts, list):
            issues.append(f"evidence_artifacts_invalid:{case.case_id}:{phase}")
            continue
        output_names: set[str] = set()
        for artifact in artifacts:
            if (
                not isinstance(artifact, dict)
                or not _safe_evidence_path(artifact.get("path"))
                or not isinstance(artifact.get("size_bytes"), int)
                or artifact["size_bytes"] < 0
                or not _SHA256.fullmatch(str(artifact.get("sha256", "")))
            ):
                issues.append(f"evidence_artifact_invalid:{case.case_id}:{phase}")
                continue
            output_names.add(Path(str(artifact["path"])).name)
        if payload.get("success") is True and attempted:
            missing_outputs = set(case.required_outputs) - output_names
            if missing_outputs:
                issues.append(f"evidence_required_outputs_missing:{case.case_id}:{phase}")
    return tuple(issues)


def _main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Willy 外部 smoke fixture 和证据预检")
    parser.add_argument("--fixture-root", help="外部 fixture bundle 根目录")
    parser.add_argument("--cases", default="all", help="逗号分隔的 case ID，默认 all")
    parser.add_argument("--evidence-dir", help="需要校验的证据目录")
    parser.add_argument("--phase", default="preflight", help="证据阶段，默认 preflight")
    parser.add_argument("--required", action="store_true", help="预检不就绪时返回非零")
    parser.add_argument("--require-execution", action="store_true", help="证据必须来自真实执行")
    options = parser.parse_args(argv)
    cases = selected_smoke_cases(options.cases)
    if options.evidence_dir:
        issues = verify_evidence(
            options.evidence_dir, cases, phase=options.phase,
            require_execution=options.require_execution,
        )
        print(json.dumps({"issues": list(issues)}, ensure_ascii=False))
        return 1 if issues else 0

    root = Path(options.fixture_root).resolve() if options.fixture_root else fixture_root()
    results = [preflight(case, root=root) for case in cases]
    print(json.dumps([result.to_dict() for result in results], ensure_ascii=False, indent=2))
    return 1 if options.required and any(not result.ready for result in results) else 0


if __name__ == "__main__":
    raise SystemExit(_main())
