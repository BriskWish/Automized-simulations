"""Contracts for the isolated, non-scientific release baseline."""

from __future__ import annotations

from types import SimpleNamespace

from tests.tools import release_baseline


def test_isolated_install_gate_uses_only_python_packaging_commands(tmp_path, monkeypatch):
    commands: list[list[str]] = []

    def fake_run(command, **_kwargs):
        commands.append(list(command))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(release_baseline.subprocess, "run", fake_run)
    monkeypatch.setattr(release_baseline.importlib.util, "find_spec", lambda _name: object())

    result = release_baseline._run_isolated_install(
        "isolated_install",
        source_root=tmp_path,
        env={},
    )

    assert result["status"] == "passed"
    assert commands[0][1:] == ["-m", "venv", str(tmp_path / ".release-venv")]
    assert commands[1][1:4] == ["-m", "pip", "install"]
    assert commands[2][1:4] == ["-m", "pip", "install"]
    assert commands[3][1:] == ["-m", "pip", "check"]
    assert commands[4][1:] == ["-c", "import willy"]
    joined = " ".join(" ".join(command) for command in commands).lower()
    assert all(name not in joined for name in ("gmx", "gromacs", "packmol", "g16", "orca", "sobtop", "ligpargen"))
    assert not (tmp_path / ".release-venv").exists()


def test_release_baseline_excludes_active_run_directories():
    assert "md_run" in release_baseline._IGNORED_DIRECTORIES


def test_release_baseline_excludes_real_dotenv_files_but_keeps_public_template(tmp_path):
    ignored = release_baseline._copy_ignore(
        str(tmp_path),
        [".env", ".env.local", ".env.example", "md_run"],
    )

    assert {".env", ".env.local", "md_run"}.issubset(ignored)
    assert ".env.example" not in ignored


def test_release_baseline_environment_removes_willy_and_unrelated_secrets(monkeypatch):
    monkeypatch.setenv("PATH", "/system/bin")
    monkeypatch.setenv("WILLY_GMX_BIN", "/private/gmx")
    monkeypatch.setenv("API_KEY", "private")

    environment = release_baseline._baseline_environment()

    assert environment["PATH"] == "/system/bin"
    assert "WILLY_GMX_BIN" not in environment
    assert "API_KEY" not in environment


def test_release_baseline_binds_python_imports_to_the_disposable_copy(tmp_path):
    environment = release_baseline._baseline_environment()
    copy_root = tmp_path / "copied-repository"
    environment["PYTHONPATH"] = str(copy_root / "src")

    assert environment["PYTHONPATH"] == str(copy_root / "src")


def test_isolated_install_reports_missing_venv_capability_without_running_commands(tmp_path, monkeypatch):
    monkeypatch.setattr(release_baseline.importlib.util, "find_spec", lambda _name: None)
    monkeypatch.setattr(release_baseline.subprocess, "run", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not run")))

    result = release_baseline._run_isolated_install("isolated_install", source_root=tmp_path, env={})

    assert result["status"] == "blocked"
    assert result["reason_code"] == "python_venv_unavailable"
