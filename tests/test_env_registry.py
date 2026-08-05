"""Unit tests for normalized external-runtime discovery and child environments."""

from __future__ import annotations

from pathlib import Path

from willy.env_registry import (
    AVAILABLE,
    MISCONFIGURED,
    build_tool_env,
    public_capabilities,
    resolve_tool,
)
from willy.run_registry import RunRegistry


def _executable(path: Path) -> Path:
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(0o755)
    return path


def test_standard_binary_override_precedes_path(tmp_path, monkeypatch):
    configured = _executable(tmp_path / "configured-gmx")
    path_bin = _executable(tmp_path / "gmx")
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("WILLY_GMX_BIN", str(configured))

    result = resolve_tool("gmx")

    assert path_bin.exists()
    assert result.status == AVAILABLE
    assert result.executable == configured.resolve()
    assert result.source == "willy_env"


def test_dotenv_standard_override_is_loaded_from_project_root(tmp_path, monkeypatch):
    gmx = _executable(tmp_path / "gmx-custom")
    (tmp_path / ".env").write_text(f"WILLY_GMX_BIN={gmx}\nUNRELATED_SECRET=not-read\n")
    monkeypatch.delenv("WILLY_GMX_BIN", raising=False)
    monkeypatch.setenv("PATH", "")

    result = resolve_tool("gmx", project_root=tmp_path)

    assert result.status == AVAILABLE
    assert result.source == "dotenv"
    assert result.executable == gmx.resolve()


def test_dotenv_value_uses_process_environment_before_dotenv(tmp_path, monkeypatch):
    from willy.env_registry import dotenv_value

    (tmp_path / ".env").write_text("WILLY_SERVER_PORT=8999\n")
    monkeypatch.setenv("WILLY_SERVER_PORT", "9000")

    assert dotenv_value("WILLY_SERVER_PORT", project_root=tmp_path) == "9000"
    monkeypatch.delenv("WILLY_SERVER_PORT")
    assert dotenv_value("WILLY_SERVER_PORT", project_root=tmp_path) == "8999"


def test_invalid_explicit_binary_is_configuration_error(monkeypatch):
    monkeypatch.setenv("WILLY_GMX_BIN", "/missing/gmx")

    result = resolve_tool("gmx")

    assert result.status == MISCONFIGURED
    assert result.source == "willy_env"


def test_project_root_never_falls_back_to_current_working_directory(tmp_path, monkeypatch):
    from willy._paths import ProjectRootError, get_project_root

    monkeypatch.delenv("WILLY_ROOT", raising=False)
    monkeypatch.delenv("MDAUTO_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)

    assert get_project_root() != tmp_path

    missing = tmp_path / "missing-root"
    monkeypatch.setenv("WILLY_ROOT", str(missing))
    try:
        get_project_root()
    except ProjectRootError:
        pass
    else:
        raise AssertionError("无效 WILLY_ROOT 不得静默回退到当前目录")


def test_orca_home_resolves_companions_and_child_library_path(tmp_path, monkeypatch):
    _executable(tmp_path / "orca")
    companion = _executable(tmp_path / "orca_2mkl")
    monkeypatch.setenv("WILLY_ORCA_HOME", str(tmp_path))
    monkeypatch.delenv("WILLY_ORCA_BIN", raising=False)
    monkeypatch.delenv("WILLY_ORCA_2MKL_BIN", raising=False)

    result = resolve_tool("orca_2mkl")
    child_env = build_tool_env("orca_2mkl", base_env={"LD_LIBRARY_PATH": "/base"})

    assert result.status == AVAILABLE
    assert result.executable == companion.resolve()
    assert child_env["LD_LIBRARY_PATH"] == f"{tmp_path.resolve()}:/base"


def test_ligpargen_child_gets_bossdir_without_global_mutation(tmp_path, monkeypatch):
    ligpargen = _executable(tmp_path / "LigParGen")
    boss_home = tmp_path / "boss"
    boss_home.mkdir()
    _executable(boss_home / "BOSS")
    monkeypatch.setenv("WILLY_LIGPARGEN_BIN", str(ligpargen))
    monkeypatch.setenv("WILLY_BOSS_HOME", str(boss_home))
    monkeypatch.delenv("BOSSdir", raising=False)

    child_env = build_tool_env("ligpargen", base_env={"PATH": "/usr/bin"})

    assert child_env["BOSSdir"] == str(boss_home.resolve())
    assert "BOSSdir" not in __import__("os").environ


def test_capability_report_is_redacted_and_run_local(tmp_path, monkeypatch):
    gmx = _executable(tmp_path / "gmx")
    monkeypatch.setenv("WILLY_GMX_BIN", str(gmx))
    payload = public_capabilities(project_root=tmp_path)

    assert "executable" not in payload["gmx"]
    assert str(gmx) not in str(payload)

    run_dir = tmp_path / "md_run" / "md__202608020999"
    registry = RunRegistry(tmp_path)
    registry.register_run(run_dir, backend="g16", total_steps=10)
    registry.record_environment_report(run_dir, payload)
    stored = (run_dir / "environment_report.json").read_text()
    assert str(gmx) not in stored
