#!/usr/bin/env python3
"""Create Willy's project-local runtime and build the React workbench.

This installer deliberately does not activate or modify a caller's Python,
Node.js, shell profile, package manager, or system libraries.  All mutable
state belongs to the source checkout:

* ``.venv/`` contains the editable Python installation;
* ``.willy/`` contains pip/npm caches and a private Node.js runtime;
* ``frontend/node_modules/`` and ``frontend/dist/`` contain frontend outputs.

The bundled scientific executables still need host ABI libraries.  Those are
reported by ``ldd`` but are never installed by this script because system
packages require an explicit administrator action and vary by distribution.
"""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import platform
import re
import runpy
import shutil
import subprocess
import sys
import tarfile
import tempfile
from typing import Iterable
from urllib.error import URLError
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_NODE_VERSION = "22.16.0"
NODE_DIST_URL = "https://nodejs.org/dist"
SUPPORTED_PYTHON = runpy.run_path(str(ROOT / "src" / "willy" / "python_runtime.py"))["SUPPORTED_PYTHON"]


class BootstrapError(RuntimeError):
    """Raised for a recoverable prerequisite or isolated-installation error."""


def _label(path: Path) -> str:
    """Render a path relative to the checkout without exposing a host path."""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return path.name


def isolated_environment(root: Path, *, create_state: bool = True) -> dict[str, str]:
    """Return an environment whose installer state stays inside ``root``."""
    state = root / ".willy"
    home = state / "home"
    cache = state / "cache"
    npm_user_config = state / "npm-userconfig"
    npm_global_config = state / "npm-globalconfig"
    if create_state:
        state.mkdir(exist_ok=True)
        home.mkdir(exist_ok=True)
        cache.mkdir(exist_ok=True)
        npm_user_config.touch(exist_ok=True)
        npm_global_config.touch(exist_ok=True)

    environment = os.environ.copy()
    for name in tuple(environment):
        if name.startswith(("PIP_", "NPM_CONFIG_")):
            environment.pop(name)
    for name in (
        "CONDA_DEFAULT_ENV",
        "CONDA_PREFIX",
        "NODE_OPTIONS",
        "NODE_PATH",
        "PYTHONHOME",
        "PYTHONPATH",
        "VIRTUAL_ENV",
    ):
        environment.pop(name, None)
    environment.update(
        {
            "HOME": str(home),
            "PIP_CACHE_DIR": str(cache / "pip"),
            "PIP_CONFIG_FILE": os.devnull,
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "PYTHONNOUSERSITE": "1",
            "NPM_CONFIG_CACHE": str(cache / "npm"),
            "NPM_CONFIG_USERCONFIG": str(npm_user_config),
            "NPM_CONFIG_GLOBALCONFIG": str(npm_global_config),
            "NPM_CONFIG_PREFIX": str(state / "npm-prefix"),
            "NPM_CONFIG_UPDATE_NOTIFIER": "false",
        }
    )
    return environment


def _run(command: Iterable[str | Path], *, cwd: Path, environment: dict[str, str], label: str) -> None:
    print(f"[bootstrap] {label}", flush=True)
    completed = subprocess.run(
        [str(part) for part in command],
        cwd=str(cwd),
        env=environment,
        check=False,
    )
    if completed.returncode:
        raise BootstrapError(f"{label} failed (exit code {completed.returncode}).")


def _python_version(python: str, root: Path, environment: dict[str, str]) -> tuple[int, int]:
    completed = subprocess.run(
        [python, "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"],
        cwd=str(root),
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode:
        raise BootstrapError("The requested Python interpreter cannot be started.")
    match = re.fullmatch(r"(\d+)\.(\d+)", completed.stdout.strip())
    if not match:
        raise BootstrapError("The requested Python interpreter returned an invalid version.")
    return int(match.group(1)), int(match.group(2))


def _require_supported_python(python: str, root: Path, environment: dict[str, str]) -> None:
    version = _python_version(python, root, environment)
    if version[0] != 3 or not (SUPPORTED_PYTHON[0][1] <= version[1] <= SUPPORTED_PYTHON[1][1]):
        raise BootstrapError(
            f"Python {version[0]}.{version[1]} is unsupported; Willy requires Python 3.10--3.12."
        )


def ensure_virtual_environment(root: Path, python: str, environment: dict[str, str]) -> Path:
    """Create or validate the project-local virtual environment."""
    venv = root / ".venv"
    venv_python = venv / "bin" / "python"
    if venv.exists() and not venv_python.is_file():
        raise BootstrapError(".venv exists but is incomplete; remove that project-local directory and rerun.")
    if not venv_python.is_file():
        _run([python, "-m", "venv", venv], cwd=root, environment=environment, label="creating project virtual environment")
    return venv_python


def install_python_package(root: Path, python: Path, environment: dict[str, str]) -> None:
    _run(
        [python, "-m", "pip", "install", "--upgrade", "pip"],
        cwd=root,
        environment=environment,
        label="updating private pip",
    )
    _run(
        [python, "-m", "pip", "install", "--editable", root],
        cwd=root,
        environment=environment,
        label="installing Willy into project virtual environment",
    )
    _run(
        [python, "-m", "pip", "check"],
        cwd=root,
        environment=environment,
        label="checking private Python dependencies",
    )


def _node_architecture() -> str:
    machine = platform.machine().lower()
    if machine in {"x86_64", "amd64"}:
        return "x64"
    if machine in {"aarch64", "arm64"}:
        return "arm64"
    raise BootstrapError(f"No private Node.js archive is configured for architecture {machine!r}.")


def _node_version() -> str:
    version = os.environ.get("WILLY_BOOTSTRAP_NODE_VERSION", DEFAULT_NODE_VERSION)
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise BootstrapError("WILLY_BOOTSTRAP_NODE_VERSION must use major.minor.patch form.")
    return version


def _download(url: str) -> bytes:
    try:
        with urlopen(url, timeout=60) as response:
            return response.read()
    except URLError as exc:
        raise BootstrapError(f"Unable to download the private Node.js runtime: {exc.reason}.") from exc


def _expected_sha256(checksums: bytes, archive_name: str) -> str:
    for line in checksums.decode("utf-8", errors="replace").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("*") == archive_name:
            digest = parts[0].lower()
            if re.fullmatch(r"[0-9a-f]{64}", digest):
                return digest
    raise BootstrapError("The Node.js checksum manifest does not list the requested archive.")


def _safe_extract(archive: Path, destination: Path) -> None:
    with tarfile.open(archive, mode="r:xz") as contents:
        root = destination.resolve()
        for member in contents.getmembers():
            member_path = (destination / member.name).resolve()
            if root not in (member_path, *member_path.parents):
                raise BootstrapError("The downloaded Node.js archive contains an unsafe path.")
        contents.extractall(destination)


def ensure_private_node(root: Path, environment: dict[str, str], *, offline: bool) -> Path:
    """Download a verified Node.js runtime under ``.willy/`` when absent."""
    if sys.platform != "linux":
        raise BootstrapError("The bundled vendor executables and private Node bootstrap currently support Linux only.")
    version = _node_version()
    archive_name = f"node-v{version}-linux-{_node_architecture()}.tar.xz"
    toolchains = root / ".willy" / "toolchains"
    node_root = toolchains / archive_name.removesuffix(".tar.xz")
    npm = node_root / "bin" / "npm"
    if npm.is_file():
        return npm
    if node_root.exists():
        raise BootstrapError(
            f"{_label(node_root)} is incomplete; remove that project-local toolchain directory and rerun."
        )
    if offline:
        raise BootstrapError("Private Node.js runtime is not cached; rerun without --offline once.")

    toolchains.mkdir(parents=True, exist_ok=True)
    version_url = f"{NODE_DIST_URL}/v{version}"
    archive_url = f"{version_url}/{archive_name}"
    print("[bootstrap] downloading verified private Node.js runtime", flush=True)
    archive_bytes = _download(archive_url)
    expected = _expected_sha256(_download(f"{version_url}/SHASUMS256.txt"), archive_name)
    actual = hashlib.sha256(archive_bytes).hexdigest()
    if actual != expected:
        raise BootstrapError("Downloaded private Node.js runtime checksum does not match the official manifest.")

    with tempfile.TemporaryDirectory(prefix="node-", dir=toolchains) as temporary:
        temporary_path = Path(temporary)
        archive = temporary_path / archive_name
        archive.write_bytes(archive_bytes)
        _safe_extract(archive, temporary_path)
        extracted = temporary_path / node_root.name
        if not (extracted / "bin" / "npm").is_file():
            raise BootstrapError("Downloaded private Node.js archive has an unexpected layout.")
        extracted.rename(node_root)

    return npm


def build_frontend(root: Path, npm: Path, environment: dict[str, str]) -> None:
    frontend = root / "frontend"
    if not (frontend / "package-lock.json").is_file():
        raise BootstrapError("frontend/package-lock.json is missing; cannot create a reproducible frontend build.")
    node_bin = str(npm.parent)
    frontend_environment = environment.copy()
    frontend_environment["PATH"] = node_bin + os.pathsep + frontend_environment.get("PATH", "")
    _run(
        [npm, "ci", "--include=dev", "--no-audit", "--no-fund"],
        cwd=frontend,
        environment=frontend_environment,
        label="installing private frontend dependencies",
    )
    _run(
        [npm, "run", "build"],
        cwd=frontend,
        environment=frontend_environment,
        label="building React workbench",
    )


def _ldd_missing(binary: Path, root: Path, environment: dict[str, str]) -> tuple[str, ...] | None:
    ldd = shutil.which("ldd", path=environment.get("PATH"))
    if not ldd:
        return None
    completed = subprocess.run(
        [ldd, str(binary)],
        cwd=str(root),
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    text = f"{completed.stdout}\n{completed.stderr}"
    missing = sorted({match.group(1) for match in re.finditer(r"^\s*(\S+)\s+=>\s+not found", text, re.MULTILINE)})
    return tuple(missing)


def system_library_preflight(root: Path, environment: dict[str, str]) -> tuple[bool, tuple[str, ...]]:
    """Read bundled executable link requirements without invoking package management."""
    binaries = (
        root / "vendor" / "packmol",
        root / "vendor" / "multiwfn" / "linux-x86_64" / "3.8-dev-2025-02-14" / "Multiwfn",
    )
    missing: set[str] = set()
    checked = False
    for binary in binaries:
        if not binary.is_file():
            continue
        result = _ldd_missing(binary, root, environment)
        if result is None:
            continue
        checked = True
        missing.update(result)
    return checked, tuple(sorted(missing))


def report_system_libraries(checked: bool, missing: tuple[str, ...]) -> bool:
    if not checked:
        print("[bootstrap] system library preflight unavailable: ldd was not found.", file=sys.stderr)
        return False
    if not missing:
        print("[bootstrap] system library preflight passed", flush=True)
        return True

    packages: list[str] = []
    if "libgfortran.so.5" in missing:
        packages.append("libgfortran5")
    if "libXm.so.4" in missing:
        packages.append("libxm4")
    print("[bootstrap] missing host dynamic libraries: " + ", ".join(missing), file=sys.stderr)
    if packages:
        print(
            "[bootstrap] Ubuntu/Debian suggestion (run manually): sudo apt install -y " + " ".join(packages),
            file=sys.stderr,
        )
    print("[bootstrap] no system package or libc was changed by Willy.", file=sys.stderr)
    return False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--python",
        default=sys.executable,
        help="Python 3.10--3.12 used only to create this checkout's .venv (default: current interpreter).",
    )
    parser.add_argument("--offline", action="store_true", help="Do not download the private Node.js runtime.")
    parser.add_argument("--skip-frontend", action="store_true", help="Install Python only; do not create frontend assets.")
    parser.add_argument(
        "--check-system-libs",
        action="store_true",
        help="Only inspect bundled executable dynamic-library requirements; do not modify the checkout.",
    )
    return parser.parse_args()


def main() -> int:
    arguments = parse_args()
    environment = isolated_environment(ROOT, create_state=not arguments.check_system_libs)
    checked, missing = system_library_preflight(ROOT, environment)
    libraries_ready = report_system_libraries(checked, missing)
    if arguments.check_system_libs:
        return 0 if libraries_ready else 2

    try:
        _require_supported_python(arguments.python, ROOT, environment)
        venv_python = ensure_virtual_environment(ROOT, arguments.python, environment)
        install_python_package(ROOT, venv_python, environment)
        if not arguments.skip_frontend:
            npm = ensure_private_node(ROOT, environment, offline=arguments.offline)
            build_frontend(ROOT, npm, environment)
    except BootstrapError as exc:
        print(f"[bootstrap] error: {exc}", file=sys.stderr)
        return 1

    print("[bootstrap] project-local installation completed", flush=True)
    if not libraries_ready:
        print("[bootstrap] frontend and Python are ready; scientific vendor runtime remains unavailable.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
