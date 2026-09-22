"""Standard-library-only checks for the interpreter shared by Web and runners."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import importlib
import io
import json
from pathlib import Path
import subprocess
import sys
from typing import TypedDict


SUPPORTED_PYTHON = (3, 10), (3, 12)
SUPPORTED_PYTHON_LABEL = "3.10--3.12"
PYTHON_DEPENDENCIES = (
    ("willy", "Willy"),
    ("cryptography.fernet", "cryptography"),
    ("fastapi", "fastapi"),
    ("httpx", "httpx"),
    ("openai", "openai"),
    ("py3Dmol", "py3Dmol"),
    ("sklearn", "scikit-learn"),
    ("uvicorn", "uvicorn"),
)


class PythonRuntimeError(ValueError):
    """The selected interpreter cannot start the supported application."""


class PythonDependencyReport(TypedDict):
    name: str
    status: str


class PythonRuntimeReport(TypedDict):
    ready: bool
    python_version: str
    supported_python: str
    dependencies: list[PythonDependencyReport]
    error: str


def parent_python_executable() -> str:
    """Keep the parent's venv entry point, without PATH lookup or realpath."""
    if not SUPPORTED_PYTHON[0] <= tuple(sys.version_info[:2]) <= SUPPORTED_PYTHON[1]:
        raise PythonRuntimeError(f"当前 Python 版本不受支持；请使用 Python {SUPPORTED_PYTHON_LABEL}。")
    if not sys.executable:
        raise PythonRuntimeError("无法确定当前 Python 解释器；请从受支持的本地 Python 环境重新启动。")
    return sys.executable


def _probe_dependencies() -> list[PythonDependencyReport]:
    dependencies: list[PythonDependencyReport] = []
    for module_name, label in PYTHON_DEPENDENCIES:
        status = "available"
        try:
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                module = importlib.import_module(module_name)
                if module_name == "willy" and (
                    Path(module.__file__).resolve().parent != Path(__file__).resolve().parent
                ):
                    status = "runtime_unavailable"
        except ModuleNotFoundError as exc:
            status = "missing" if exc.name == module_name.split(".")[0] else "runtime_unavailable"
        except (Exception, SystemExit):
            status = "runtime_unavailable"
        dependencies.append({"name": label, "status": status})
    return dependencies


def _dependency_result(report: PythonRuntimeReport) -> PythonRuntimeReport:
    unavailable = [item["name"] for item in report["dependencies"] if item["status"] != "available"]
    report["ready"] = not unavailable
    if unavailable:
        report["error"] = (
            "当前 Python 缺少或无法加载依赖：" + "、".join(unavailable)
            + "。请使用受支持的 Python 通过项目引导脚本安装依赖，再从项目虚拟环境启动；无需 Anaconda。"
        )
    return report


def check_python_runtime() -> PythonRuntimeReport:
    """Probe actual child imports without inheriting Web's sys.path injection."""
    report: PythonRuntimeReport = {
        "ready": False,
        "python_version": ".".join(str(part) for part in sys.version_info[:3]),
        "supported_python": SUPPORTED_PYTHON_LABEL,
        "dependencies": [],
        "error": "",
    }
    try:
        interpreter = parent_python_executable()
    except PythonRuntimeError as exc:
        report["error"] = str(exc)
        return report
    report["dependencies"] = _probe_dependencies()
    if any(item["status"] != "available" for item in report["dependencies"]):
        return _dependency_result(report)
    try:
        result = subprocess.run(
            [interpreter, "-B", str(Path(__file__).absolute()), "--probe"],
            cwd=str(Path(__file__).resolve().parents[2]),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if result.returncode != 0:
            raise ValueError("runtime probe failed")
        dependencies = json.loads(result.stdout)
        if (
            not isinstance(dependencies, list)
            or len(dependencies) != len(PYTHON_DEPENDENCIES)
            or any(
                not isinstance(item, dict)
                or set(item) != {"name", "status"}
                or item["name"] != expected[1]
                or item["status"] not in {"available", "missing", "runtime_unavailable"}
                for item, expected in zip(dependencies, PYTHON_DEPENDENCIES)
            )
        ):
            raise ValueError("invalid runtime probe")
    except (OSError, ValueError, subprocess.TimeoutExpired):
        report["error"] = "当前 Python 的依赖导入检查未能完成；请检查本地环境后重试。"
        return report
    report["dependencies"] = dependencies
    return _dependency_result(report)


def require_python_runtime() -> PythonRuntimeReport:
    """Fail before importing the Web stack when its runtime is unavailable."""
    report = check_python_runtime()
    if not report["ready"]:
        raise PythonRuntimeError(report["error"])
    return report


def main() -> int:
    if sys.argv[1:] == ["--probe"]:
        parent_python_executable()
        print(json.dumps(_probe_dependencies(), ensure_ascii=False))
        return 0
    report = check_python_runtime()
    print(f"Python {report['python_version']}；支持范围：{report['supported_python']}")
    print("Python 运行时与依赖检查通过。" if report["ready"] else report["error"])
    return 0 if report["ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
