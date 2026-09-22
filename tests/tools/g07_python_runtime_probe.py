"""Real local Web restarts and managed resume without scientific execution."""

from __future__ import annotations

import argparse
from hashlib import sha256
from importlib.metadata import version
import json
import os
from pathlib import Path
import platform
import shlex
import signal
import socket
import subprocess
import sys
import time
from urllib.request import Request, urlopen


RUN_ID = "md__202609080001"
RUNNER = '''import argparse
import json
import os
from pathlib import Path
import platform
import sys
from willy.controlled_launch import wait_for_launch_permission
from willy.pipeline_launch import adopt_pipeline_launch
from willy.run_registry import RunRegistry
parser = argparse.ArgumentParser()
parser.add_argument("backend")
parser.add_argument("--run-dir", type=Path)
parser.add_argument("--lock-fd", type=int)
parser.add_argument("--launch-token")
parser.add_argument("--resume-from-step", type=int)
arguments = parser.parse_args()
wait_for_launch_permission()
root = Path(__file__).parent
reservation = adopt_pipeline_launch(root, arguments.run_dir, arguments.lock_fd, arguments.launch_token)
try:
    registry = RunRegistry(root)
    status = registry.get_run_status(arguments.run_dir.name, reconcile=False)
    status.update(state="aborted", step=arguments.resume_from_step, activity={})
    registry.record_status(arguments.run_dir, status, "g07_no_science_runner_finished")
    receipt = {
        "python_version": platform.python_version(), "is_venv": sys.prefix != sys.base_prefix,
        "same_interpreter": sys.executable == os.environ["G07_EXPECTED_PYTHON"],
        "same_prefix": sys.prefix == os.environ["G07_EXPECTED_PREFIX"],
        "resume_step": arguments.resume_from_step, "launch_lock_adopted": True,
    }
    (arguments.run_dir / "g07-runner.json").write_text(json.dumps(receipt))
finally:
    reservation.release()
'''


def serve(root: Path, port: int) -> None:
    import app as application
    import uvicorn

    application.frontend_api.ROOT = root

    @application.app.get("/api/g07-identity")
    def identity():
        return {"pid": os.getpid(), "python_version": platform.python_version(),
                "same_interpreter": sys.executable == os.environ["G07_EXPECTED_PYTHON"],
                "same_prefix": sys.prefix == os.environ["G07_EXPECTED_PREFIX"]}

    application.app.router.routes.insert(0, application.app.router.routes.pop())
    uvicorn.run(application.app, host="127.0.0.1", port=port, log_level="error", access_log=False)


def _request(port: int, route: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    request = Request(f"http://127.0.0.1:{port}{route}", data=data,
                      headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=10) as response:
        return json.loads(response.read())


def web_and_resume(project: Path, workspace: Path) -> dict:
    from tests.test_run_control import _aborted_run
    from willy.pipeline_launch import pipeline_launch_is_active

    fixture = workspace / "resume-fixture"
    _, directory = _aborted_run(fixture, run_id=RUN_ID)
    (fixture / "run_pipeline.py").write_text(RUNNER)
    canary = workspace / "path-canary"
    canary.mkdir()
    marker = workspace / "path-python-used"
    for name in ("python", "python3"):
        executable = canary / name
        executable.write_text(f"#!/bin/sh\nprintf used > {shlex.quote(str(marker))}\nexit 97\n")
        executable.chmod(0o755)
    environment = dict(os.environ, G07_EXPECTED_PYTHON=sys.executable, G07_EXPECTED_PREFIX=sys.prefix,
                       PATH=f"{canary}:/usr/bin:/bin")
    sessions = []
    identities = []
    for index in range(2):
        receipt_path = directory / "g07-runner.json"
        receipt_path.unlink(missing_ok=True)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        with (workspace / f"web-{index}.log").open("wb") as output:
            process = subprocess.Popen([
                sys.executable, "-m", "tests.tools.g07_python_runtime_probe", "--serve-root", str(fixture), "--port", str(port),
            ], cwd=project, env=environment, stdout=output, stderr=output, start_new_session=True)
            try:
                deadline = time.monotonic() + 45
                while True:
                    try:
                        health = _request(port, "/api/health")
                        identity = _request(port, "/api/g07-identity")
                        break
                    except (OSError, ValueError):
                        if process.poll() is not None or time.monotonic() >= deadline:
                            raise RuntimeError("Web startup did not become healthy") from None
                        time.sleep(0.1)
                if identity["pid"] != process.pid:
                    raise RuntimeError("Unexpected loopback server identity")
                identities.append(process.pid)
                reply = _request(port, f"/api/runs/{RUN_ID}/chat", {"message": "/resume"})
                deadline = time.monotonic() + 20
                while not receipt_path.is_file() or pipeline_launch_is_active(fixture):
                    if time.monotonic() >= deadline:
                        raise RuntimeError("Managed resume did not finish")
                    time.sleep(0.05)
                receipt = json.loads(receipt_path.read_text())
                sessions.append({
                    "healthy": health.get("status") == "ok" and health.get("surface") == "assistant-ui",
                    "same_web_interpreter": identity["same_interpreter"] and identity["same_prefix"],
                    "resume_accepted": "已接受 /resume" in reply.get("text", ""),
                    "resume": receipt,
                    "public_reply_redacted": str(workspace) not in json.dumps(reply, ensure_ascii=False),
                })
            finally:
                if process.poll() is None:
                    process.send_signal(signal.SIGTERM)
                    try:
                        process.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait(timeout=5)
    passed = len(set(identities)) == 2 and not marker.exists() and all(
        session["healthy"] and session["same_web_interpreter"] and session["resume_accepted"]
        and session["public_reply_redacted"] and session["resume"]["same_interpreter"]
        and session["resume"]["same_prefix"] and session["resume"]["is_venv"]
        and session["resume"]["resume_step"] == 8 and session["resume"]["launch_lock_adopted"]
        for session in sessions
    )
    return {"passed": passed, "sessions": sessions, "distinct_web_processes": len(set(identities)) == 2,
            "path_fallback_used": marker.exists(), "runner": "no-science controlled process with real launch gate and lock adoption"}


def run_probe(project: Path, workspace: Path) -> dict:
    from tests.tools.g07_local_wsl_acceptance import run_acceptance
    from willy.python_runtime import check_python_runtime

    workspace.mkdir(parents=True, exist_ok=False)
    report = {"python_version": platform.python_version(), "is_venv": sys.prefix != sys.base_prefix,
              "system": platform.system(), "machine": platform.machine(),
              "distribution": {key: value for key, value in platform.freedesktop_os_release().items() if key in {"ID", "VERSION_ID"}},
              "runtime": check_python_runtime(),
              "dependencies": {name: version(name) for name in (
                  "willy", "cryptography", "fastapi", "httpx", "openai", "py3Dmol", "scikit-learn", "uvicorn", "pytest",
              )}}
    empty = workspace / "missing-dependencies"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(empty)], check=True)
    missing = subprocess.run([str(empty / "bin" / "python"), str(project / "app.py"), "--check-runtime"],
                             cwd=project, capture_output=True, text=True, timeout=30, check=False)
    missing_safe = missing.returncode != 0 and "Willy 启动检查失败" in missing.stderr and "fastapi" in missing.stderr and all(
        marker not in missing.stderr for marker in ("Traceback", str(project), str(workspace))
    )
    report["missing_dependencies"] = {"passed": missing_safe, "returncode": missing.returncode,
                                      "public_guidance_sha256": sha256(missing.stderr.encode()).hexdigest()}
    matrix = run_acceptance(project)
    report["error_matrix"] = matrix
    report["web_restart_resume"] = web_and_resume(project, workspace)
    report["passed"] = (report["is_venv"] and report["runtime"]["ready"] and missing_safe
                        and matrix["acceptance"] == "passed" and report["web_restart_resume"]["passed"])
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve-root", type=Path)
    parser.add_argument("--port", type=int)
    parser.add_argument("--project", type=Path)
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    if arguments.serve_root is not None:
        serve(arguments.serve_root, arguments.port)
        return 0
    report = run_probe(arguments.project, arguments.workspace)
    arguments.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"python_version": report["python_version"], "passed": report["passed"]}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
