"""Contracts for the project-local installer script."""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "bootstrap.py"


def _bootstrap_module():
    spec = importlib.util.spec_from_file_location("willy_bootstrap", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_bootstrap_environment_confines_package_manager_state_to_project(tmp_path):
    bootstrap = _bootstrap_module()

    environment = bootstrap.isolated_environment(tmp_path)

    assert environment["HOME"] == str(tmp_path / ".willy" / "home")
    assert environment["PIP_CACHE_DIR"] == str(tmp_path / ".willy" / "cache" / "pip")
    assert environment["NPM_CONFIG_CACHE"] == str(tmp_path / ".willy" / "cache" / "npm")
    assert environment["NPM_CONFIG_PREFIX"] == str(tmp_path / ".willy" / "npm-prefix")
    assert environment["PIP_CONFIG_FILE"] == "/dev/null"
    assert environment["NPM_CONFIG_USERCONFIG"] == str(tmp_path / ".willy" / "npm-userconfig")
    assert environment["NPM_CONFIG_GLOBALCONFIG"] == str(tmp_path / ".willy" / "npm-globalconfig")
    assert (tmp_path / ".willy" / "home").is_dir()


def test_bootstrap_download_names_allow_only_supported_linux_architectures(monkeypatch):
    bootstrap = _bootstrap_module()

    monkeypatch.setattr(bootstrap.platform, "machine", lambda: "x86_64")
    assert bootstrap._node_architecture() == "x64"
    monkeypatch.setattr(bootstrap.platform, "machine", lambda: "aarch64")
    assert bootstrap._node_architecture() == "arm64"
    monkeypatch.setattr(bootstrap.platform, "machine", lambda: "ppc64le")
    try:
        bootstrap._node_architecture()
    except bootstrap.BootstrapError as exc:
        assert "ppc64le" in str(exc)
    else:
        raise AssertionError("unsupported architecture must be rejected")


def test_bootstrap_system_library_preflight_is_read_only_and_reports_missing_links(tmp_path, monkeypatch):
    bootstrap = _bootstrap_module()
    packmol = tmp_path / "vendor" / "packmol"
    multiwfn = tmp_path / "vendor" / "multiwfn" / "linux-x86_64" / "3.8-dev-2025-02-14" / "Multiwfn"
    packmol.parent.mkdir(parents=True)
    multiwfn.parent.mkdir(parents=True)
    packmol.write_text("fixture", encoding="utf-8")
    multiwfn.write_text("fixture", encoding="utf-8")
    commands: list[list[str]] = []

    def fake_ldd(binary, root, environment):
        commands.append([str(binary), str(root), environment["HOME"]])
        return ("libXm.so.4", "libgfortran.so.5")

    monkeypatch.setattr(bootstrap, "_ldd_missing", fake_ldd)
    environment = bootstrap.isolated_environment(tmp_path, create_state=False)

    checked, missing = bootstrap.system_library_preflight(tmp_path, environment)

    assert checked is True
    assert missing == ("libXm.so.4", "libgfortran.so.5")
    assert [entry[0] for entry in commands] == [str(packmol), str(multiwfn)]
    assert not (tmp_path / ".willy").exists()
    assert not (tmp_path / ".venv").exists()
    assert not (tmp_path / "frontend").exists()


def test_bootstrap_never_executes_a_system_package_manager():
    source = SCRIPT.read_text(encoding="utf-8").lower()
    tree = ast.parse(source)
    commands: list[list[str]] = []
    for call in ast.walk(tree):
        if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name) or call.func.id != "_run":
            continue
        if not call.args or not isinstance(call.args[0], ast.List):
            continue
        commands.append(
            [element.value for element in call.args[0].elts if isinstance(element, ast.Constant) and isinstance(element.value, str)]
        )
    command_text = " ".join(" ".join(command) for command in commands)

    assert "run manually" in source
    assert all(name not in command_text for name in ("sudo", "apt", "dnf", "yum", "pacman"))
