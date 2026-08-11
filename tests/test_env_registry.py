"""Unit tests for normalized external-runtime discovery and child environments."""

from __future__ import annotations

from pathlib import Path

from willy.env_registry import (
    AVAILABLE,
    MISCONFIGURED,
    RUNTIME_UNAVAILABLE,
    build_tool_env,
    public_capabilities,
    resolve_tool,
)
from willy.run_registry import RunRegistry
from willy.run_provenance import create_or_refresh_provenance
from willy.run_metadata import load_run_manifest


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


def test_g09_uses_its_own_configured_executables_and_gaussian_environment(tmp_path, monkeypatch):
    g09 = _executable(tmp_path / "g09")
    g09_formchk = _executable(tmp_path / "g09-formchk")
    monkeypatch.setenv("WILLY_G09_BIN", str(g09))
    monkeypatch.setenv("WILLY_G09_FORMCHK_BIN", str(g09_formchk))

    executable = resolve_tool("g09")
    formchk = resolve_tool("g09_formchk")
    child_env = build_tool_env("g09_formchk", base_env={"PATH": "/usr/bin"})

    assert executable.status == AVAILABLE
    assert executable.executable == g09.resolve()
    assert formchk.status == AVAILABLE
    assert formchk.executable == g09_formchk.resolve()
    assert child_env["GAUSS_CDEF"] == "0"
    assert child_env["OMP_NUM_THREADS"] == "1"


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


def test_openbabel_uses_standard_binary_override(tmp_path, monkeypatch):
    obabel = _executable(tmp_path / "obabel")
    monkeypatch.setenv("WILLY_OBABEL_BIN", str(obabel))

    result = resolve_tool("obabel")

    assert result.status == AVAILABLE
    assert result.executable == obabel.resolve()
    assert result.source == "willy_env"


def test_multiwfn_uses_bundled_binary_and_ignores_external_configuration(tmp_path, monkeypatch):
    bundled = (
        tmp_path / "vendor" / "multiwfn" / "linux-x86_64"
        / "3.8-dev-2025-02-14" / "Multiwfn"
    )
    bundled.parent.mkdir(parents=True)
    _executable(bundled)
    monkeypatch.setenv("WILLY_MULTIWFN_BIN", "/outside/Multiwfn")
    monkeypatch.setenv("MULTIWFN_BIN", "/outside/Multiwfn")
    monkeypatch.setenv("PATH", "")

    result = resolve_tool("multiwfn", project_root=tmp_path)

    assert result.status == AVAILABLE
    assert result.source == "bundled"
    assert result.executable == bundled.resolve()


def test_boss_loader_failure_is_runtime_unavailable(tmp_path, monkeypatch):
    boss_home = tmp_path / "boss"
    boss_home.mkdir()
    _executable(boss_home / "BOSS")
    monkeypatch.setenv("WILLY_BOSS_HOME", str(boss_home))

    def unavailable(*args, **kwargs):
        raise OSError("loader missing")

    monkeypatch.setattr("willy.env_registry.subprocess.run", unavailable)

    result = resolve_tool("boss")

    assert result.status == RUNTIME_UNAVAILABLE


def test_boss_signal_termination_is_runtime_unavailable(tmp_path, monkeypatch):
    boss_home = tmp_path / "boss"
    boss_home.mkdir()
    _executable(boss_home / "BOSS")
    monkeypatch.setenv("WILLY_BOSS_HOME", str(boss_home))

    monkeypatch.setattr(
        "willy.env_registry.subprocess.run",
        lambda *args, **kwargs: type("Probe", (), {"returncode": -31})(),
    )

    result = resolve_tool("boss")

    assert result.status == RUNTIME_UNAVAILABLE
    assert "系统信号" in result.public_reason


def test_bundled_packmol_loader_failure_is_runtime_unavailable(tmp_path):
    packmol = tmp_path / "vendor" / "packmol"
    packmol.parent.mkdir(parents=True)
    packmol.write_text(
        "#!/bin/sh\n"
        "echo 'version GLIBC_2.34 not found' >&2\n"
        "exit 1\n"
    )
    packmol.chmod(0o755)

    result = resolve_tool("packmol", project_root=tmp_path)

    assert result.status == RUNTIME_UNAVAILABLE
    assert result.source == "bundled"
    assert "运行库不兼容" in result.public_reason
    assert "GLIBC" not in result.public_reason


def test_bundled_packmol_usage_exit_still_proves_runtime_started(tmp_path):
    packmol = tmp_path / "vendor" / "packmol"
    packmol.parent.mkdir(parents=True)
    packmol.write_text("#!/bin/sh\necho 'usage' >&2\nexit 2\n")
    packmol.chmod(0o755)

    result = resolve_tool("packmol", project_root=tmp_path)

    assert result.status == AVAILABLE
    assert result.source == "bundled"


def test_capability_report_is_redacted_and_run_local(tmp_path, monkeypatch):
    gmx = _executable(tmp_path / "gmx")
    monkeypatch.setenv("WILLY_GMX_BIN", str(gmx))
    payload = public_capabilities(project_root=tmp_path)

    assert "executable" not in payload["gmx"]
    assert str(gmx) not in str(payload)

    run_dir = tmp_path / "md_run" / "md__202608020999"
    run_dir.mkdir(parents=True)
    config = run_dir / "config.json"
    config.write_text('{"md":{"run_seed":1}}')
    registry = RunRegistry(tmp_path)
    registry.register_run(run_dir, backend="g16", total_steps=10)
    create_or_refresh_provenance(
        run_dir,
        project_root=tmp_path,
        backend="g16",
        config_path=config,
        random_seed=1,
        capabilities=payload,
        llm_model=None,
        prompt_versions={},
    )
    stored = str(load_run_manifest(run_dir)["sections"]["provenance"]["data"])
    assert str(gmx) not in stored
    assert not (run_dir / "environment_report.json").exists()
    assert registry.get_environment_report(run_dir.name)["capabilities"]["gmx"]["status"] == "available"
