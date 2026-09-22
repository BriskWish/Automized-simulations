"""Run an opt-in, isolated EC optimization using the production SMD renderer."""

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import tempfile

from willy.config_store import write_json
from willy.env_registry import build_tool_env, require_tool
from willy.process_lifecycle import run_managed_command
from willy.quantum.smd_solvents import register_manual_solvent
from willy.quantum.struct_g16 import _build_gjf, _parse_gjf


ROOT = Path(__file__).resolve().parents[2]


def main():
    directory = Path(tempfile.mkdtemp(prefix="willy-smd-acceptance-"))
    scratch = directory / "scratch"
    scratch.mkdir()
    solvent = register_manual_solvent("Generic_validation", 20, 1.8, project_root=directory)
    parsed = _parse_gjf(ROOT / "struct" / "EC.gjf")
    parsed["route"] = "#p B3LYP/6-31G(d) Opt=(MaxCycles=100) SCF=XQC"
    rendered = _build_gjf(
        parsed, "B3LYP/6-31G(d)", "1GB", 1, "ec_generic",
        solvent=solvent.name, solvent_ref=solvent.as_dict(), project_root=directory,
    )
    (directory / "ec_generic.gjf").write_text(rendered)
    gaussian = require_tool("g16")
    environment = build_tool_env("g16")
    environment["GAUSS_SCRDIR"] = str(scratch)
    result = run_managed_command(
        [str(gaussian.executable)], cwd=directory, input_text=rendered,
        env=environment, timeout=300, run_dir=directory,
    )
    output = result.stdout or ""
    (directory / "ec_generic.log").write_text(output)
    checks = {
        "exit_success": result.returncode == 0,
        "optimization_completed": "Optimization completed." in output,
        "normal_termination": "Normal termination of Gaussian 16" in output,
        "no_error_termination": "Error termination" not in output,
        "generic_solvent": bool(re.search(r"Solvent\s+: Generic", output)),
        "epsilon_applied": bool(re.search(r"Eps\s+=\s+20\.000000", output)),
        "epsinf_applied": bool(re.search(r"Eps\(infinity\)\s+=\s+1\.800000", output)),
    }
    report = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "passed": all(checks.values()),
        "scope": "G16 EC optimization and Generic/Read dielectric syntax, not full SMD parameter calibration",
        "resources": {"nproc": 1, "mem": "1GB", "timeout_s": 300},
        "solvent": solvent.as_dict(),
        "input": rendered,
        "input_sha256": sha256(rendered.encode()).hexdigest(),
        "log_sha256": sha256(output.encode()).hexdigest(),
        "checks": checks,
        "evidence": [line.strip() for line in output.splitlines() if any(marker in line for marker in (
            "Solvent              :", "Eps                           =", "Eps(infinity)",
            "Optimization completed.", "Normal termination of Gaussian 16",
        ))],
    }
    report_path = ROOT / "tests" / "reports" / "audits" / "gaussian_smd_generic_20260921.json"
    write_json(report_path, report)
    print(json.dumps({"passed": report["passed"], "work_dir": str(directory), "report": str(report_path)}))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
