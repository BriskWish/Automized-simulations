#!/usr/bin/env python3
"""Run the four documented local scientific acceptance profiles serially.

The runner is deliberately conservative: it creates a fresh pipeline run for
each profile, leaves every run directory intact, and restores the caller's
root ``config.json`` even when one profile fails.  Automatic LLM repair is
disabled because this acceptance batch must record a failure and advance to
the next independent profile rather than wait for a human confirmation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
import argparse
import copy
import json
import os
import selectors
import subprocess
import sys
import time

from willy._paths import get_project_root
from willy.pipeline_launch import active_pipeline_run_id
from willy.quantum.input_audit import (
    apply_audited_quantum_properties,
    audit_config_quantum_inputs,
    quantum_input_contract_issues,
)
from willy.workflow_config import validate_config


ROOT = get_project_root()
CONFIG_PATH = ROOT / "config.json"
RUN_ROOT = ROOT / "md_run"
EQ_SEGMENTS_NS = {
    "heat": 1,
    "hold_high": 1,
    "cool_transition": 1,
    "hold_transition": 1,
    "cool_target": 1,
    "hold_target": 2,
}
ELECTROLYTE_RESIDUES = {"Li": 50, "NO3": 30, "TFSI": 20, "EC": 200, "DME": 200}
SOLVENT_RESIDUES = {"EC": 200, "FEC": 200, "EMC": 200}
MOLECULE_PROPERTIES = {
    "Li": {"charge": 1, "spin": 1},
    "NO3": {"charge": -1, "spin": 1},
    "TFSI": {"charge": -1, "spin": 1},
    "EC": {"charge": 0, "spin": 1},
    "DME": {"charge": 0, "spin": 1},
    "FEC": {"charge": 0, "spin": 1},
    "EMC": {"charge": 0, "spin": 1},
}


@dataclass(frozen=True)
class AcceptanceProfile:
    """One complete quantum-to-PROD acceptance run."""

    profile_id: str
    quantum_backend: str
    topology_backend: str
    residues: dict[str, int]


PROFILES = (
    AcceptanceProfile("g16_sobtop_electrolyte", "g16", "sobtop", ELECTROLYTE_RESIDUES),
    AcceptanceProfile("orca_sobtop_electrolyte", "orca", "sobtop", ELECTROLYTE_RESIDUES),
    AcceptanceProfile("g16_ligpargen_solvents", "g16", "oplsaa", SOLVENT_RESIDUES),
    AcceptanceProfile("orca_ligpargen_solvents", "orca", "oplsaa", SOLVENT_RESIDUES),
)


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _atomic_write(path: Path, payload: bytes) -> None:
    temporary = path.with_name(f".{path.name}.acceptance.tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def _load_status(run_dir: Path | None) -> dict[str, Any]:
    if run_dir is None:
        return {}
    path = run_dir / "status.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _public_status(run_dir: Path | None) -> dict[str, Any]:
    status = _load_status(run_dir)
    activity = status.get("activity")
    return {
        "run_id": run_dir.name if run_dir else None,
        "state": status.get("state"),
        "current_step": status.get("step", status.get("current_step")),
        "step_label": status.get("step_label"),
        "activity": activity if isinstance(activity, dict) else {},
        "error": status.get("error"),
        "updated_at": status.get("updated_at"),
    }


def _profile_config(source: dict[str, Any], profile: AcceptanceProfile) -> dict[str, Any]:
    config = copy.deepcopy(source)
    config["backend"] = profile.quantum_backend
    config["residues"] = dict(profile.residues)
    config["molecules"] = {
        name: {
            **MOLECULE_PROPERTIES[name],
            "basis": "b3lyp/6-311+g(d,p)",
            "solvent": "acetone",
            "mem": "",
            "nproc": None,
        }
        for name in profile.residues
    }
    config["topology"] = {
        "backend": profile.topology_backend,
        "force_field": "gaff_uff" if profile.topology_backend == "sobtop" else "oplsaa",
        "default_lbcc": False,
        "default_opt_steps": 0,
    }
    md = config.setdefault("md", {})
    md.pop("run_seed", None)
    eq = md.setdefault("eq", {})
    eq["segments_ns"] = dict(EQ_SEGMENTS_NS)
    prod = md.setdefault("prod", {})
    prod["duration_ns"] = 2
    execution = config.setdefault("execution", {})
    execution.pop("stop_after_stage", None)
    execution["md"] = {
        "backend": "local",
        "profile": None,
        "retain_remote_run": True,
    }

    audit = audit_config_quantum_inputs(config, struct_dir=ROOT / "struct")
    if not audit.get("ok"):
        raise ValueError("quantum input audit failed: " + "; ".join(audit.get("issues", [])))
    config = apply_audited_quantum_properties(config, audit)
    issues = quantum_input_contract_issues(config, audit)
    issues.extend(validate_config(config))
    if issues:
        raise ValueError("profile config invalid: " + "; ".join(issues))
    return config


def _write_report(path: Path, report: dict[str, Any]) -> None:
    _atomic_write(path, (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def _find_active_run() -> Path | None:
    run_id = active_pipeline_run_id(ROOT)
    if not run_id:
        return None
    run_dir = RUN_ROOT / run_id
    return run_dir if run_dir.is_dir() else None


def _monitor_interval(status: dict[str, Any]) -> int | None:
    step = status.get("step", status.get("current_step"))
    if step == 1:
        return 60
    if step == 9:
        return 20 * 60
    return None


def _run_profile(
    profile: AcceptanceProfile,
    config: dict[str, Any],
    report: dict[str, Any],
    report_path: Path,
    log_path: Path,
) -> None:
    _atomic_write(CONFIG_PATH, (json.dumps(config, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    entry: dict[str, Any] = {
        "profile_id": profile.profile_id,
        "quantum_backend": profile.quantum_backend,
        "topology_backend": profile.topology_backend,
        "residues": profile.residues,
        "eq_segments_ns": EQ_SEGMENTS_NS,
        "prod_duration_ns": 2,
        "llm_repair": "disabled_for_failure_skip",
        "started_at": _now(),
        "monitor_events": [],
    }
    report["profiles"].append(entry)
    _write_report(report_path, report)

    command = [sys.executable, "run_pipeline.py", "--no-llm", profile.quantum_backend]
    with log_path.open("a", encoding="utf-8") as log:
        log.write(f"\n[{_now()}] START {profile.profile_id}\n")
        log.flush()
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        entry["runner_pid"] = process.pid
        selector = selectors.DefaultSelector()
        if process.stdout is not None:
            selector.register(process.stdout, selectors.EVENT_READ)
        run_dir: Path | None = None
        last_monitored_stage: int | None = None
        next_monitor_at: float | None = None

        while process.poll() is None:
            if run_dir is None:
                run_dir = _find_active_run()
                if run_dir is not None:
                    entry["run_id"] = run_dir.name
                    entry["run_dir"] = str(run_dir)
                    _write_report(report_path, report)
            status = _load_status(run_dir)
            stage = status.get("step", status.get("current_step"))
            interval = _monitor_interval(status)
            if interval is None:
                last_monitored_stage = None
                next_monitor_at = None
            elif stage != last_monitored_stage:
                last_monitored_stage = stage
                next_monitor_at = time.monotonic() + interval
            elif next_monitor_at is not None and time.monotonic() >= next_monitor_at:
                event = {"at": _now(), "interval_s": interval, **_public_status(run_dir)}
                entry["monitor_events"].append(event)
                log.write(f"[{event['at']}] MONITOR {json.dumps(event, ensure_ascii=False)}\n")
                log.flush()
                _write_report(report_path, report)
                next_monitor_at = time.monotonic() + interval

            timeout = 1.0
            if next_monitor_at is not None:
                timeout = min(timeout, max(0.0, next_monitor_at - time.monotonic()))
            for key, _ in selector.select(timeout):
                line = key.fileobj.readline()
                if line:
                    log.write(line)
                    log.flush()

        if process.stdout is not None:
            for line in process.stdout:
                log.write(line)
        selector.close()
        entry["exit_code"] = process.returncode
        entry["finished_at"] = _now()
        entry["final_status"] = _public_status(run_dir)
        entry["result"] = "passed" if process.returncode == 0 else "failed"
        log.write(f"[{entry['finished_at']}] END {profile.profile_id} exit={process.returncode}\n")
    _write_report(report_path, report)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Validate profile configs without starting software.")
    args = parser.parse_args(argv)

    if active_pipeline_run_id(ROOT):
        print("An existing pipeline launch is active; serial acceptance will not start.", file=sys.stderr)
        return 2
    original = CONFIG_PATH.read_bytes()
    source = json.loads(original.decode("utf-8"))
    batch_id = datetime.now().strftime("%Y%m%d%H%M%S")
    backup_path = ROOT / f"config.json.acceptance-backup-{batch_id}"
    report_path = RUN_ROOT / f"acceptance_batch_{batch_id}.json"
    log_path = RUN_ROOT / f"acceptance_batch_{batch_id}.log"
    report: dict[str, Any] = {
        "schema_version": 1,
        "batch_id": batch_id,
        "started_at": _now(),
        "profiles": [],
        "root_config_backup": backup_path.name,
    }
    RUN_ROOT.mkdir(parents=True, exist_ok=True)
    backup_path.write_bytes(original)
    _write_report(report_path, report)

    try:
        for profile in PROFILES:
            config = _profile_config(source, profile)
            if args.dry_run:
                report["profiles"].append({
                    "profile_id": profile.profile_id,
                    "result": "config_validated",
                    "residues": profile.residues,
                })
                _write_report(report_path, report)
                continue
            _run_profile(profile, config, report, report_path, log_path)
    except KeyboardInterrupt:
        report["interrupted_at"] = _now()
        _write_report(report_path, report)
        raise
    finally:
        _atomic_write(CONFIG_PATH, original)
        report["root_config_restored_at"] = _now()
        try:
            backup_path.unlink()
            report["root_config_backup_removed"] = True
        except OSError:
            report["root_config_backup_removed"] = False
        _write_report(report_path, report)

    failures = sum(profile.get("result") == "failed" for profile in report["profiles"])
    print(f"Acceptance batch complete: {len(report['profiles'])} profiles, {failures} failures")
    print(f"Report: {report_path}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
