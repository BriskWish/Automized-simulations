"""Isolated, opt-in Python installation and local G-07 verification matrix."""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[2]
TEST_FILES = (
    "test_python_runtime.py", "test_dependency_preflight.py", "test_errors.py",
    "test_controlled_launch.py", "test_run_control.py", "test_app_ui.py",
    "test_frontend_api.py", "test_pipeline_launch.py", "test_pipeline_orchestrator.py",
    "test_simulation_execution.py", "test_eq_acceptance.py", "test_llm_config.py",
)
IMPLEMENTATION = (
    "app.py", "pyproject.toml", "src/willy/python_runtime.py",
    "src/willy/frontend_api.py", "src/willy/controlled_launch.py",
    "src/willy/pipeline_launch.py", "src/willy/pipeline_orchestrator.py",
    "tests/tools/g07_local_wsl_acceptance.py",
    "tests/tools/g07_python_matrix_acceptance.py", "tests/tools/g07_python_runtime_probe.py",
)


def fingerprint(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _ignore(directory: str, names: list[str]) -> set[str]:
    excluded = {".git", ".venv", ".willy", "md_run", "__pycache__", "node_modules", ".pytest_cache", "test-results"}
    return {name for name in names if name in excluded or name.startswith(".env")
            or name.endswith(".egg-info") or (Path(directory) / name).is_symlink()}


def snapshot_files(source: Path) -> list[Path]:
    files = []
    for name in ("src", "tests", "docs", "scripts", "assets", "frontend", ".github", "benchmarks"):
        root = source / name
        if not root.is_dir() or root.is_symlink():
            continue
        for directory, directories, filenames in os.walk(root):
            excluded = _ignore(directory, directories + filenames)
            directories[:] = [entry for entry in directories if entry not in excluded]
            files.extend(Path(directory) / entry for entry in filenames if entry not in excluded)
    for name in ("app.py", "run_pipeline.py", "pyproject.toml", "README.md", "PROJECT_OVERVIEW.md", "LICENSE.md", ".gitignore", ".env.example"):
        if (source / name).is_file() and not (source / name).is_symlink():
            files.append(source / name)
    vendor_manifest = source / "vendor" / "manifest.json"
    vendor_names = {"packmol"}
    if vendor_manifest.is_file():
        vendor_names.add("manifest.json")
        vendor_names.update(entry["path"] for component in json.loads(vendor_manifest.read_text())["components"]
                            for entry in component["files"])
    for name in sorted(vendor_names):
        path = source / "vendor" / name
        if not path.resolve().is_relative_to((source / "vendor").resolve()):
            raise ValueError("Vendor snapshot path escapes its directory")
        if path.is_file() and not path.is_symlink():
            files.append(path)
    if (source / ".git").exists():
        tracked = subprocess.check_output(["git", "-C", str(source), "ls-files", "-z", "--", "struct"])
        for name in tracked.decode().split("\0"):
            path = source / name
            if path.suffix in {".gjf", ".inp"} and path.is_file() and not path.is_symlink():
                files.append(path)
    return sorted(files)


def prepare_snapshot(source: Path, destination: Path) -> dict[str, str]:
    destination.mkdir(parents=True, exist_ok=False)
    manifest = {}
    for path in snapshot_files(source):
        relative = path.relative_to(source)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        manifest[relative.as_posix()] = fingerprint(target)
    (destination / "config.json").write_text('{"backend":"g16","residues":{},"molecules":{}}\n')
    return manifest


def isolated_environment(case: Path, project: Path) -> dict[str, str]:
    environment = {name: value for name, value in os.environ.items() if name in {
        "LANG", "LC_ALL", "TZ", "SYSTEMROOT",
    }}
    for name in ("home", "tmp", "cache"):
        (case / name).mkdir(parents=True, exist_ok=True)
    environment.update({
        "HOME": str(case / "home"), "TMPDIR": str(case / "tmp"),
        "XDG_CACHE_HOME": str(case / "cache"), "PATH": "/usr/bin:/bin",
        "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1",
        "PIP_CONFIG_FILE": os.devnull, "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "WILLY_ROOT": str(project), "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1",
    })
    return environment


def checked_command(command: list[str], case: Path, label: str, *, timeout: int = 300) -> dict:
    started = time.monotonic()
    environment = isolated_environment(case, case / "project")
    process = subprocess.Popen(command, cwd=case / "project", env=environment,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
    try:
        output, _ = process.communicate(timeout=timeout)
        returncode = process.returncode
    except subprocess.TimeoutExpired:
        process.send_signal(signal.SIGINT)
        try:
            output, _ = process.communicate(timeout=20)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            output, _ = process.communicate(timeout=5)
        returncode = 124
    (case / "logs").mkdir(exist_ok=True)
    (case / "logs" / f"{label}.log").write_bytes(output)
    return {"passed": returncode == 0, "returncode": returncode,
            "duration_s": round(time.monotonic() - started, 3), "output_sha256": sha256(output).hexdigest()}


def pytest_counts(path: Path) -> dict[str, int]:
    suites = ET.parse(path).getroot()
    counts = {name: sum(int(suite.get(name, "0")) for suite in suites) for name in (
        "tests", "failures", "errors", "skipped",
    )}
    counts["passed"] = counts["tests"] - counts["failures"] - counts["errors"] - counts["skipped"]
    return counts


def regression_command(interpreter: Path, junit_path: Path, suite: str) -> list[str]:
    targets = [] if suite == "full" else [f"tests/{name}" for name in TEST_FILES]
    return [str(interpreter), "-m", "pytest", "-q", *targets, f"--junitxml={junit_path}"]


def verify_case(interpreter: Path, case: Path, suite: str = "focused") -> dict:
    project = case / "project"
    implementation = {name: fingerprint(project / name) for name in IMPLEMENTATION}
    tested_source = json.loads((case / "source-manifest.json").read_text())
    attempt = str(time.time_ns())
    junit_path = case / f"pytest-{attempt}.xml"
    probe_path = case / f"probe-result-{attempt}.json"
    checks = {}
    checks["pip_check"] = checked_command([str(interpreter), "-m", "pip", "check"], case, "pip-check")
    checks["compile"] = checked_command([str(interpreter), "-m", "compileall", "-q", "src", "app.py", "run_pipeline.py"], case, "compile")
    checks["runtime"] = checked_command([str(interpreter), "app.py", "--check-runtime"], case, "runtime")
    checks["regression"] = checked_command(regression_command(interpreter, junit_path, suite), case, "regression", timeout=900)
    checks["regression"]["suite"] = suite
    if junit_path.is_file():
        checks["regression"]["counts"] = pytest_counts(junit_path)
        checks["regression"]["failed_cases"] = [
            f"{item.get('classname', '')}.{item.get('name', '')}"
            for item in ET.parse(junit_path).iter("testcase")
            if item.find("failure") is not None or item.find("error") is not None
        ]
        checks["regression"]["skipped_cases"] = [
            {"case": f"{item.get('classname', '')}.{item.get('name', '')}",
             "reason": item.find("skipped").get("message", "")}
            for item in ET.parse(junit_path).iter("testcase") if item.find("skipped") is not None
        ]
    checks["live_probe"] = checked_command([
        str(interpreter), "-m", "tests.tools.g07_python_runtime_probe",
        "--project", str(project), "--workspace", str(case / f"probe-{time.time_ns()}"),
        "--output", str(probe_path),
    ], case, "live-probe")
    probe = json.loads(probe_path.read_text()) if probe_path.is_file() else {}
    return {
        "passed": all(check["passed"] for check in checks.values()),
        "g07_smoke_passed": all(check["passed"] for name, check in checks.items() if name != "regression"),
        "checks": checks, "probe": probe,
        "tested_implementation": implementation,
        "tested_source": tested_source,
    }


def summarize_cases(cases: dict, source: Path, workspace: Path) -> dict:
    cases = deepcopy(cases)
    current = {name: fingerprint(source / name) for name in IMPLEMENTATION}
    reference = next(iter(cases.values()))["tested_implementation"]
    complete_matrix = set(cases) == {"3.10", "3.11", "3.12"}
    same_snapshot = all(case["tested_implementation"] == reference for case in cases.values())
    for minor, case in cases.items():
        tested = case["tested_implementation"]
        unchanged = set(tested) == set(IMPLEMENTATION) and all(
            fingerprint(workspace / f"python-{minor}" / "project" / name) == digest
            for name, digest in tested.items()
        )
        case["tested_snapshot_unchanged"] = unchanged
        case["tested_implementation_matches_source"] = tested == current
        checks = case["checks"]
        complete_checks = set(checks) == {"pip_check", "compile", "runtime", "regression", "live_probe"}
        valid = unchanged and complete_checks and case.get("probe", {}).get("passed") is True
        case["passed"] = valid and all(check["passed"] for check in checks.values())
        case["g07_smoke_passed"] = valid and all(check["passed"] for name, check in checks.items() if name != "regression")
    report = {
        "schema_version": 1, "gap_id": "G-07", "recorded_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Frozen local-host source snapshot, independent Python installations, real loopback Web restarts and managed no-science resume runners; scientific/LLM failures are controlled injections, not remote-platform acceptance or coverage of later worktree changes.",
        "complete_version_matrix": complete_matrix, "same_tested_snapshot": same_snapshot,
        "implementation": reference, "reporting_implementation_sha256": fingerprint(Path(__file__)),
        "source_changes_after_snapshot": {name: {"tested_sha256": digest, "worktree_sha256": current[name]}
                                          for name, digest in reference.items() if current[name] != digest},
        "current_worktree_covered": reference == current and same_snapshot and all(case["tested_snapshot_unchanged"] for case in cases.values()),
        "cases": cases,
        "passed": complete_matrix and same_snapshot and all(case["passed"] for case in cases.values()),
        "g07_smoke_passed": complete_matrix and same_snapshot and all(case["g07_smoke_passed"] for case in cases.values()),
        "isolation": {"source_runs_copied": False, "source_config_copied": False,
                      "source_dotenv_copied": False, "source_python_environment_modified": False},
    }
    if any("tested_source" in case for case in cases.values()):
        source_reference = next(iter(cases.values())).get("tested_source", {})
        current_source = {path.relative_to(source).as_posix(): fingerprint(path) for path in snapshot_files(source)}
        same_source = bool(source_reference) and all(case.get("tested_source") == source_reference for case in cases.values())
        for minor, case in cases.items():
            tested = case.pop("tested_source", {})
            unchanged = bool(tested) and all(
                (workspace / f"python-{minor}" / "project" / name).is_file()
                and fingerprint(workspace / f"python-{minor}" / "project" / name) == digest
                for name, digest in tested.items()
            )
            case["source_manifest_sha256"] = sha256(json.dumps(tested, sort_keys=True).encode()).hexdigest()
            case["tested_snapshot_unchanged"] &= unchanged
            case["passed"] &= unchanged
            case["g07_smoke_passed"] &= unchanged
        report["source_manifest"] = source_reference
        report["same_tested_snapshot"] &= same_source
        report["source_changes_after_snapshot"] = {
            name: {"tested_sha256": source_reference.get(name), "worktree_sha256": current_source.get(name)}
            for name in sorted(set(source_reference) | set(current_source))
            if source_reference.get(name) != current_source.get(name)
        }
        report["current_worktree_covered"] = same_source and source_reference == current_source and all(
            case["tested_snapshot_unchanged"] for case in cases.values())
        report["passed"] &= same_source and all(case["passed"] for case in cases.values())
        report["g07_smoke_passed"] &= same_source and all(case["g07_smoke_passed"] for case in cases.values())
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", type=Path, action="append", required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--source", type=Path, default=ROOT)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--suite", choices=("focused", "full"), default="focused")
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    cases = {}
    for interpreter in arguments.python:
        version = subprocess.check_output([str(interpreter), "-c", "import sys; print('.'.join(map(str, sys.version_info[:2])))"], text=True).strip()
        if version not in {"3.10", "3.11", "3.12"} or version in cases:
            raise ValueError("Distinct supported Python versions are required")
        case = arguments.workspace / f"python-{version}"
        case.mkdir(parents=True, exist_ok=True)
        if arguments.prepare:
            manifest = prepare_snapshot(arguments.source, case / "project")
            (case / "source-manifest.json").write_text(json.dumps(manifest, sort_keys=True))
            subprocess.run([str(interpreter), "-m", "venv", str(case / "venv")], check=True,
                           env=isolated_environment(case, case / "project"))
            cases[version] = {"prepared": True}
        else:
            print(f"Verifying Python {version}", flush=True)
            cases[version] = verify_case(interpreter, case, arguments.suite)
    if arguments.prepare:
        print(json.dumps(cases))
        return 0
    report = summarize_cases(cases, arguments.source, arguments.workspace)
    if arguments.output is None:
        parser.error("--output is required for verification")
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"gap_id": "G-07", "passed": report["passed"]}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
