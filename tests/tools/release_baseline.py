"""Build a deterministic release baseline in an isolated repository copy.

The source worktree is never used as a test workspace.  In particular, the
copy excludes ``md_run/`` and all existing test outputs so an active scientific
calculation cannot be inspected, stopped, or modified by release gating.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
from typing import Any
import xml.etree.ElementTree as ET

from tests.reporting.batch_report import (
    artifact_record,
    collect_tool_versions,
    config_record,
    report_from_command_results,
    write_batch_report,
)


ROOT = Path(__file__).resolve().parents[2]
_IGNORED_DIRECTORIES = frozenset({
    ".git", ".pytest_cache", ".venv", "__pycache__", "md_run", "test-results",
})
_BASELINE_ENVIRONMENT_KEYS = frozenset({
    "HOME", "LANG", "LC_ALL", "PATH", "SYSTEMROOT", "TEMP", "TMP", "TMPDIR", "TZ",
})


def _copy_ignore(directory: str, names: list[str]) -> set[str]:
    ignored = {name for name in names if name in _IGNORED_DIRECTORIES}
    # Keep public templates such as .env.example, but never copy an actual
    # dotenv file into a release-test workspace.
    ignored.update(
        name
        for name in names
        if name == ".env" or (name.startswith(".env.") and name != ".env.example")
    )
    current = Path(directory).resolve()
    source_root = Path(os.environ.get("WILLY_BASELINE_SOURCE", ROOT)).resolve()
    if current == source_root / "tests" and "reports" in names:
        ignored.add("reports")
    return ignored


def _baseline_environment() -> dict[str, str]:
    """Return the minimal host environment needed for Python-only gates.

    In particular, do not inherit ``WILLY_*`` overrides from a developer's
    active application session.  The gates receive their isolated paths only
    after this function returns.
    """
    return {
        key: value
        for key, value in os.environ.items()
        if key in _BASELINE_ENVIRONMENT_KEYS
    }


def _project_version(root: Path) -> str:
    with (root / "pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle).get("project", {})
    value = project.get("version") if isinstance(project, dict) else None
    return value if isinstance(value, str) and value else "source"


def _run(name: str, command: list[str], *, cwd: Path, env: dict[str, str]) -> dict[str, Any]:
    """Run one gate, retaining only exit code, elapsed time and declared artifacts."""
    started = time.monotonic()
    completed = subprocess.run(command, cwd=cwd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    return {
        "name": name,
        "returncode": completed.returncode,
        "duration_s": round(time.monotonic() - started, 3),
        "status": "passed" if completed.returncode == 0 else "failed",
    }


def _run_isolated_install(
    name: str,
    *,
    source_root: Path,
    env: dict[str, str],
) -> dict[str, Any]:
    """Install the source copy in a disposable venv and run only Python gates.

    The venv lives under the temporary baseline copy.  It has no access to
    ``md_run`` and this routine deliberately contains no scientific-tool
    command, fixture or environment discovery.
    """
    started = time.monotonic()
    if importlib.util.find_spec("ensurepip") is None:
        return {
            "name": name,
            "returncode": 0,
            "duration_s": 0.0,
            "status": "blocked",
            "reason_code": "python_venv_unavailable",
        }
    venv_dir = source_root / ".release-venv"
    environment = dict(env)
    environment["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    environment["PIP_NO_INPUT"] = "1"
    commands = (
        [sys.executable, "-m", "venv", str(venv_dir)],
        [str(venv_dir / "bin" / "python"), "-m", "pip", "install", "--upgrade", "pip"],
        [str(venv_dir / "bin" / "python"), "-m", "pip", "install", "-e", ".[test]"],
        [str(venv_dir / "bin" / "python"), "-m", "pip", "check"],
        [str(venv_dir / "bin" / "python"), "-c", "import willy"],
    )
    returncode = 0
    try:
        for command in commands:
            completed = subprocess.run(
                command,
                cwd=source_root,
                env=environment,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            if completed.returncode:
                returncode = completed.returncode
                break
    finally:
        shutil.rmtree(venv_dir, ignore_errors=True)
    return {
        "name": name,
        "returncode": returncode,
        "duration_s": round(time.monotonic() - started, 3),
        "status": "passed" if returncode == 0 else "failed",
        "reason_code": "" if returncode == 0 else "isolated_install_failed",
    }


def _copy_artifact(source: Path, destination: Path, *, copy_root: Path) -> dict[str, Any] | None:
    if not source.is_file():
        return None
    relative = source.resolve().relative_to(copy_root.resolve())
    target = destination / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return artifact_record(target, root=destination)


def _pytest_summary(path: Path) -> dict[str, int]:
    """Read only JUnit counters; failure bodies and captured output are ignored."""
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    for suite in suites:
        for key in totals:
            try:
                totals[key] += int(suite.attrib.get(key, "0"))
            except ValueError:
                pass
    totals["passed"] = max(0, totals["tests"] - totals["failures"] - totals["errors"] - totals["skipped"])
    return totals


def create_baseline(source: Path, destination: Path, *, batch_id: str) -> Path:
    """Run all deterministic release gates in a disposable source copy."""
    source = source.resolve()
    destination = destination.resolve()
    if not (source / "pyproject.toml").is_file() or not (source / "tests").is_dir():
        raise ValueError("source must be a Willy repository root")
    if destination == source:
        raise ValueError("baseline destination cannot be the repository root")
    try:
        destination.relative_to(source)
    except ValueError:
        pass
    else:
        try:
            destination.relative_to(source / "tests" / "reports")
        except ValueError as exc:
            raise ValueError("source-local baseline reports must remain under tests/reports") from exc
    destination.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="willy-regression-") as temporary:
        copy_root = Path(temporary) / "repo"
        previous_source = os.environ.get("WILLY_BASELINE_SOURCE")
        os.environ["WILLY_BASELINE_SOURCE"] = str(source)
        try:
            shutil.copytree(source, copy_root, ignore=_copy_ignore)
        finally:
            if previous_source is None:
                os.environ.pop("WILLY_BASELINE_SOURCE", None)
            else:
                os.environ["WILLY_BASELINE_SOURCE"] = previous_source

        copied_reports = copy_root / "tests" / "reports"
        copied_reports.mkdir(parents=True, exist_ok=True)
        (copy_root / "test-results").mkdir(parents=True, exist_ok=True)
        copied_junit = copy_root / "test-results" / "pytest.xml"
        copied_catalog = copied_reports / "test_case_catalog.md"
        copied_eval = copied_reports / "mock_llm_eval.json"
        environment = _baseline_environment()
        environment.update({
            # The copy has not necessarily been installed yet (for example
            # when ensurepip is unavailable), so every Python-only gate must
            # import this copy rather than an editable package on the host.
            "PYTHONPATH": str(copy_root / "src"),
            "WILLY_ROOT": str(copy_root),
            "WILLY_EXTERNAL_SMOKE_FIXTURES": str(copy_root / "tests" / "fixtures" / "external"),
            "WILLY_EXTERNAL_SMOKE_EVIDENCE_DIR": str(copy_root / "tests" / "reports" / "external-smoke"),
        })
        # Keep the release baseline limited to Python-level gates.  In
        # particular, none of these commands may invoke a scientific tool or
        # inspect an active run directory.
        commands = {
            "pytest": [sys.executable, "-m", "pytest", "-q", f"--junitxml={copied_junit.relative_to(copy_root)}"],
            "compileall": [sys.executable, "-m", "compileall", "-q", "src", "tests"],
            "test_case_catalog": [sys.executable, "-m", "tests.tools.generate_test_case_catalog", "--output", str(copied_catalog.relative_to(copy_root))],
            "mock_llm_eval": [sys.executable, "-m", "tests.llm_eval.run_eval", "--json-output", str(copied_eval.relative_to(copy_root))],
        }
        results: dict[str, dict[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=len(commands) + 1, thread_name_prefix="release-gate") as executor:
            pending = {
                executor.submit(_run, name, command, cwd=copy_root, env=environment): name
                for name, command in commands.items()
            }
            pending[executor.submit(
                _run_isolated_install,
                "isolated_install",
                source_root=copy_root,
                env=environment,
            )] = "isolated_install"
            for future in as_completed(pending):
                name = pending[future]
                results[name] = future.result()

        for name, artifact in {
            "pytest": copied_junit,
            "test_case_catalog": copied_catalog,
            "mock_llm_eval": copied_eval,
        }.items():
            copied = _copy_artifact(artifact, destination, copy_root=copy_root)
            if copied is not None:
                results[name]["artifacts"] = [copied]
        if copied_junit.is_file():
            results["pytest"]["metrics"] = _pytest_summary(copied_junit)

        report = report_from_command_results(
            batch_id=batch_id,
            project_version=_project_version(copy_root),
            config=config_record(copy_root / "config.json", root=copy_root),
            results=results,
            tools={
                **collect_tool_versions(("pytest",)),
                # The active interpreter can carry stale editable metadata;
                # a source baseline must identify the copied source itself.
                "willy": _project_version(copy_root),
            },
        )
        return write_batch_report(destination / "batch_report.json", report)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT, help="repository to copy and verify")
    parser.add_argument("--batch-id", default=datetime.now(timezone.utc).strftime("regression-%Y%m%dT%H%M%SZ"))
    parser.add_argument("--output-root", type=Path, default=ROOT / "tests" / "reports" / "baselines")
    args = parser.parse_args()
    destination = args.output_root / args.batch_id
    report = create_baseline(args.source, destination, batch_id=args.batch_id)
    print(report)


if __name__ == "__main__":
    main()
