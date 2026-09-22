"""Interpreter, dependency and Web-startup contracts without scientific runs."""

from __future__ import annotations

import ast
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from willy import python_runtime as runtime
from willy.pipeline_launch import pipeline_command


ROOT = Path(__file__).resolve().parents[1]


def _available_dependencies():
    return [{"name": label, "status": "available"} for _module, label in runtime.PYTHON_DEPENDENCIES]


@pytest.mark.parametrize("version", [(3, 8, 10), (3, 9, 20), (3, 13, 0), (4, 0, 0)])
def test_unsupported_python_is_rejected_before_imports_or_processes(monkeypatch, version):
    monkeypatch.setattr(runtime.sys, "version_info", version)
    monkeypatch.setattr(runtime, "_probe_dependencies", lambda: pytest.fail("unexpected import"))
    monkeypatch.setattr(runtime.subprocess, "run", lambda *args, **kwargs: pytest.fail("unexpected process"))

    report = runtime.check_python_runtime()

    assert report["ready"] is False
    assert "3.10--3.12" in report["error"]
    assert report["dependencies"] == []


@pytest.mark.parametrize("version", [(3, 10, 0), (3, 11, 7), (3, 12, 99)])
def test_supported_python_probes_the_same_interpreter_and_redacts_its_path(monkeypatch, version):
    monkeypatch.setattr(runtime.sys, "version_info", version)
    monkeypatch.setattr(runtime.sys, "executable", "/private/custom venv/bin/python")
    monkeypatch.setattr(runtime, "_probe_dependencies", _available_dependencies)
    calls = []
    monkeypatch.setattr(
        runtime.subprocess, "run",
        lambda command, **kwargs: calls.append((command, kwargs)) or SimpleNamespace(
            returncode=0, stdout=json.dumps(_available_dependencies()),
        ),
    )

    report = runtime.require_python_runtime()

    assert report["ready"] is True
    assert calls[0][0][0] == "/private/custom venv/bin/python"
    assert calls[0][1]["timeout"] == 30
    assert "env" not in calls[0][1]
    assert "/private/" not in json.dumps(report)


def test_missing_parent_interpreter_never_falls_back_to_path(monkeypatch):
    monkeypatch.setattr(runtime.sys, "executable", "")

    with pytest.raises(runtime.PythonRuntimeError, match="无法确定"):
        pipeline_command(ROOT, "g16")


def test_pipeline_command_preserves_a_venv_symlink_and_spaces(tmp_path, monkeypatch):
    interpreter = tmp_path / "custom environment" / "bin" / "python"
    interpreter.parent.mkdir(parents=True)
    interpreter.symlink_to(sys.executable)
    monkeypatch.setattr(runtime.sys, "executable", str(interpreter))

    command = pipeline_command(tmp_path, "orca", "--resume-from-step", "9")

    assert command == [str(interpreter), str(tmp_path / "run_pipeline.py"), "orca", "--resume-from-step", "9"]
    assert command[0] != str(interpreter.resolve())


def test_dependency_import_errors_are_classified_without_leaking_details(monkeypatch):
    def import_module(name):
        if name == "fastapi":
            raise ModuleNotFoundError("/private/secret", name="fastapi")
        if name == "sklearn":
            raise ImportError("/private/site-packages: binary incompatibility")
        print("private dependency output")
        return object()

    monkeypatch.setattr(runtime.importlib, "import_module", import_module)

    dependencies = runtime._probe_dependencies()

    assert {item["name"]: item["status"] for item in dependencies}["fastapi"] == "missing"
    assert {item["name"]: item["status"] for item in dependencies}["scikit-learn"] == "runtime_unavailable"
    assert "private" not in json.dumps(dependencies)


def test_web_source_import_does_not_hide_a_missing_child_installation(monkeypatch):
    monkeypatch.setattr(runtime, "_probe_dependencies", _available_dependencies)
    dependencies = _available_dependencies()
    dependencies[0]["status"] = "missing"
    monkeypatch.setattr(runtime.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(
        returncode=0, stdout=json.dumps(dependencies),
    ))

    with pytest.raises(runtime.PythonRuntimeError, match="Willy"):
        runtime.require_python_runtime()


def test_missing_parent_dependencies_fail_without_starting_a_child(monkeypatch):
    dependencies = _available_dependencies()
    dependencies[1]["status"] = "runtime_unavailable"
    monkeypatch.setattr(runtime, "_probe_dependencies", lambda: dependencies)
    monkeypatch.setattr(runtime.subprocess, "run", lambda *args, **kwargs: pytest.fail("unexpected process"))

    assert runtime.check_python_runtime()["ready"] is False


def test_runtime_rejects_an_import_from_a_different_checkout(tmp_path, monkeypatch):
    monkeypatch.setattr(
        runtime.importlib, "import_module",
        lambda name: SimpleNamespace(__file__=str(tmp_path / "other checkout" / "__init__.py")),
    )

    dependencies = runtime._probe_dependencies()

    assert dependencies[0] == {"name": "Willy", "status": "runtime_unavailable"}
    assert str(tmp_path) not in json.dumps(dependencies)


@pytest.mark.parametrize("failure", ["exit", "timeout", "invalid_json", "invalid_schema", "missing_executable"])
def test_runtime_probe_failures_are_bounded_and_redacted(monkeypatch, failure):
    monkeypatch.setattr(runtime, "_probe_dependencies", _available_dependencies)

    def run(*args, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired("/private/python", 30)
        if failure == "missing_executable":
            raise FileNotFoundError("/private/python")
        return SimpleNamespace(
            returncode=1 if failure == "exit" else 0,
            stdout="{}" if failure == "invalid_schema" else "/private/secret",
        )

    monkeypatch.setattr(runtime.subprocess, "run", run)

    report = runtime.check_python_runtime()

    assert report["ready"] is False
    assert "检查未能完成" in report["error"]
    assert "private" not in json.dumps(report)


def test_runtime_module_can_explain_failures_on_system_python_38_syntax():
    ast.parse(Path(runtime.__file__).read_text(encoding="utf-8"), feature_version=(3, 8))
    ast.parse((ROOT / "app.py").read_text(encoding="utf-8"), feature_version=(3, 8))


def test_python_support_and_dependencies_match_packaging_and_bootstrap():
    try:
        import tomllib
    except ModuleNotFoundError:
        import tomli as tomllib
    from tests.test_bootstrap_script import _bootstrap_module

    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]

    assert project["requires-python"] == ">=3.10,<3.13"
    assert _bootstrap_module().SUPPORTED_PYTHON == runtime.SUPPORTED_PYTHON
    assert {item.split(">=")[0] for item in project["dependencies"]} == {
        label for _module, label in runtime.PYTHON_DEPENDENCIES if label != "Willy"
    }


def test_web_missing_dependencies_produce_guidance_before_fastapi_import():
    result = subprocess.run(
        [sys.executable, "-S", str(ROOT / "app.py"), "--check-runtime"],
        cwd=ROOT, capture_output=True, text=True, timeout=15, check=False,
    )

    assert result.returncode != 0
    assert "Willy 启动检查失败" in result.stderr
    assert "fastapi" in result.stderr
    assert "无需 Anaconda" in result.stderr
    assert "Traceback" not in result.stderr


def test_web_runtime_check_does_not_start_the_web_server():
    result = subprocess.run(
        [sys.executable, str(ROOT / "app.py"), "--check-runtime"],
        cwd=ROOT, capture_output=True, text=True, timeout=45, check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "运行时与依赖检查通过" in result.stdout
    assert "Uvicorn" not in result.stderr


def test_g07_snapshot_excludes_user_runs_configuration_and_environments(tmp_path, monkeypatch):
    from tests.tools import g07_python_matrix_acceptance as matrix
    from tests.tools.g07_python_matrix_acceptance import prepare_snapshot

    source = tmp_path / "source"
    source.mkdir()
    for name in ("src", "tests", "md_run", ".venv", ".willy"):
        (source / name).mkdir()
        (source / name / "private.txt").write_text("fixture")
    (source / "src" / ".env").write_text("private fixture")
    (source / ".env").write_text("private fixture")
    (source / "config.json").write_text('{"private":"fixture"}')
    (source / "src" / "secret-link").symlink_to(source / ".env")
    for name in ("frontend", "vendor", "struct", ".git"):
        (source / name).mkdir()
    (source / "frontend" / "package.json").write_text("{}")
    (source / "vendor" / "manifest.json").write_text(json.dumps({"components": [{"files": [{"path": "fixture"}]}]}))
    (source / "vendor" / "fixture").write_text("public fixture")
    (source / "vendor" / "private.txt").write_text("not inventoried")
    (source / "struct" / "bundled.gjf").write_text("public fixture")
    (source / "struct" / "uploaded.gjf").write_text("private fixture")
    monkeypatch.setattr(matrix.subprocess, "check_output", lambda *_args: b"struct/bundled.gjf\0")
    destination = tmp_path / "snapshot"

    manifest = prepare_snapshot(source, destination)

    assert "src/private.txt" in manifest
    assert {"frontend/package.json", "vendor/manifest.json", "vendor/fixture", "struct/bundled.gjf"} <= set(manifest)
    assert "vendor/private.txt" not in manifest and "struct/uploaded.gjf" not in manifest
    assert json.loads((destination / "config.json").read_text()) != {"private": "fixture"}
    assert all(not (destination / name).exists() for name in ("md_run", ".venv", ".willy", ".env", "src/.env", "src/secret-link"))


def test_g07_environment_does_not_inherit_credentials_or_python_overrides(tmp_path, monkeypatch):
    from tests.tools.g07_python_matrix_acceptance import isolated_environment

    for name in ("OPENAI_API_KEY", "WILLY_ROOT", "PYTHONPATH", "HTTP_PROXY", "VIRTUAL_ENV", "PIP_INDEX_URL"):
        monkeypatch.setenv(name, "private-fixture")
    environment = isolated_environment(tmp_path, tmp_path / "project")

    assert "private-fixture" not in json.dumps(environment)
    assert environment["HOME"] == str(tmp_path / "home")
    assert environment["WILLY_ROOT"] == str(tmp_path / "project")
    assert environment["PYTHONNOUSERSITE"] == "1"


def test_g07_regression_summary_keeps_failures_and_skips_distinct(tmp_path):
    from tests.tools.g07_python_matrix_acceptance import pytest_counts

    path = tmp_path / "pytest.xml"
    path.write_text('<testsuites><testsuite tests="8" failures="1" errors="1" skipped="2" /></testsuites>')
    assert pytest_counts(path) == {"tests": 8, "failures": 1, "errors": 1, "skipped": 2, "passed": 4}


@pytest.mark.parametrize("mutation", ["worktree", "snapshot", "regression"])
def test_g07_snapshot_verdict_does_not_hide_failures_or_follow_moving_worktree(tmp_path, monkeypatch, mutation):
    from tests.tools import g07_python_matrix_acceptance as matrix

    monkeypatch.setattr(matrix, "IMPLEMENTATION", ("runtime.py",))
    source = tmp_path / "source"
    source.mkdir()
    (source / "runtime.py").write_text("tested snapshot")
    digest = matrix.fingerprint(source / "runtime.py")
    cases = {}
    for minor in ("3.10", "3.11", "3.12"):
        project = tmp_path / f"python-{minor}" / "project"
        project.mkdir(parents=True)
        (project / "runtime.py").write_text("tested snapshot")
        cases[minor] = {
            "tested_implementation": {"runtime.py": digest}, "probe": {"passed": True},
            "checks": {name: {"passed": True} for name in ("pip_check", "compile", "runtime", "regression", "live_probe")},
        }
    if mutation == "worktree":
        (source / "runtime.py").write_text("later worktree")
    elif mutation == "snapshot":
        (tmp_path / "python-3.10" / "project" / "runtime.py").write_text("changed snapshot")
    else:
        cases["3.10"]["checks"]["regression"]["passed"] = False

    report = matrix.summarize_cases(cases, source, tmp_path)

    assert report["g07_smoke_passed"] == (mutation != "snapshot")
    assert report["passed"] == (mutation == "worktree")
    assert report["current_worktree_covered"] == (mutation == "regression")


@pytest.mark.parametrize("suite", ["focused", "full"])
def test_g07_full_regression_uses_default_collection_without_external_opt_in(tmp_path, suite):
    from tests.tools.g07_python_matrix_acceptance import TEST_FILES, regression_command

    command = regression_command(Path(sys.executable), tmp_path / "result.xml", suite)

    assert command[:4] == [sys.executable, "-m", "pytest", "-q"]
    assert command[4:-1] == ([] if suite == "full" else [f"tests/{name}" for name in TEST_FILES])
    assert command[-1] == f"--junitxml={tmp_path / 'result.xml'}"


@pytest.mark.parametrize("mutation", ["worktree", "addition", "snapshot", "missing", "version"])
def test_g07_full_snapshot_tracks_files_outside_the_core_implementation(tmp_path, monkeypatch, mutation):
    from tests.tools import g07_python_matrix_acceptance as matrix

    monkeypatch.setattr(matrix, "IMPLEMENTATION", ("src/runtime.py",))
    source = tmp_path / "source"
    (source / "src").mkdir(parents=True)
    (source / "tests").mkdir()
    (source / "src/runtime.py").write_text("tested runtime")
    (source / "tests/check.py").write_text("tested regression")
    manifest = {path.relative_to(source).as_posix(): matrix.fingerprint(path) for path in matrix.snapshot_files(source)}
    cases = {}
    for minor in ("3.10", "3.11", "3.12"):
        project = tmp_path / f"python-{minor}" / "project"
        matrix.prepare_snapshot(source, project)
        cases[minor] = {
            "tested_implementation": {"src/runtime.py": manifest["src/runtime.py"]},
            "tested_source": dict(manifest), "probe": {"passed": True},
            "checks": {name: {"passed": True} for name in ("pip_check", "compile", "runtime", "regression", "live_probe")},
        }
    if mutation == "worktree":
        (source / "tests/check.py").write_text("later regression")
    elif mutation == "addition":
        (source / "src/new.py").write_text("untested addition")
    elif mutation == "missing":
        (tmp_path / "python-3.10/project/tests/check.py").unlink()
    else:
        changed = tmp_path / "python-3.10/project/tests/check.py"
        changed.write_text("changed regression")
        if mutation == "version":
            cases["3.10"]["tested_source"]["tests/check.py"] = matrix.fingerprint(changed)

    report = matrix.summarize_cases(cases, source, tmp_path)

    assert report["passed"] == report["g07_smoke_passed"] == (mutation in {"worktree", "addition"})
    assert report["same_tested_snapshot"] == (mutation != "version")
    assert report["current_worktree_covered"] is False
