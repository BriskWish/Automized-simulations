#!/usr/bin/env python3
"""Run Willy's four requested quantum/topology profiles through accepted EQ.

This is an opt-in acceptance harness, not a normal user launch path.  Every
profile receives a newly reserved run directory and an immutable local
configuration snapshot.  It never changes the project-root ``config.json``
and it deliberately disables LLM recovery so an observed failure is preserved
before the next serial profile starts.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from willy._paths import get_project_root
from willy.config_store import write_json
from willy.pipeline_launch import PipelineLaunchError, PipelineLockConflict, reserve_pipeline_launch
from willy.pipeline_orchestrator import PipelineOrchestrator
from willy.simulation.protocol import default_md_config
from willy.workflow_config import validate_config


ROOT = get_project_root()
COMPONENTS = {"Li": 50, "NO3": 30, "TFSI": 20, "EC": 200, "DME": 200}
CHARGES = {"Li": 1, "NO3": -1, "TFSI": -1, "EC": 0, "DME": 0}
PROFILES = {
    "g16_sobtop": {"backend": "g16", "topology": "sobtop"},
    "orca_sobtop": {"backend": "orca", "topology": "sobtop"},
    "g16_oplsaa": {"backend": "g16", "topology": "oplsaa"},
    "orca_oplsaa": {"backend": "orca", "topology": "oplsaa"},
}
PROFILE_ORDER = tuple(PROFILES)
EQ_SEGMENTS_NS = {
    "heat": 1.0,
    "hold_high": 1.0,
    "cool_transition": 1.0,
    "hold_transition": 1.0,
    "cool_target": 1.0,
    "hold_target": 2.0,
}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def build_trial_config(profile_id: str) -> dict[str, Any]:
    """Return the frozen, neutral 7 ns EQ-only configuration for one profile."""
    try:
        profile = PROFILES[profile_id]
    except KeyError as exc:
        raise ValueError(f"未知 profile: {profile_id}") from exc
    md = default_md_config()
    md["eq"]["segments_ns"] = deepcopy(EQ_SEGMENTS_NS)
    topology_backend = profile["topology"]
    return {
        "backend": profile["backend"],
        "molecules": {
            name: {"charge": CHARGES[name], "spin": 1}
            for name in COMPONENTS
        },
        "residues": dict(COMPONENTS),
        "md": md,
        "topology": {
            "backend": topology_backend,
            "force_field": "gaff_uff" if topology_backend == "sobtop" else "oplsaa",
            "default_lbcc": topology_backend == "oplsaa",
            "default_opt_steps": 0,
        },
        "box": {
            "box_size": None,
            "target_mass_density_g_cm3": 0.7,
            "tolerance": 2.0,
        },
        "defaults": {"mem": "5GB", "nproc": 8},
        "execution": {
            "md": {"backend": "local", "profile": None, "retain_remote_run": True},
            "stop_after_stage": "eq",
        },
        "trial": {
            "kind": "four_profile_eq_only_acceptance",
            "profile": profile_id,
            "eq_segments_ns": dict(EQ_SEGMENTS_NS),
            "llm_recovery": "disabled",
        },
    }


def _status_summary(run_dir: Path) -> dict[str, object]:
    try:
        status = json.loads((run_dir / "status.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"state": "unavailable"}
    return {
        "state": status.get("state", ""),
        "step": status.get("step"),
        "done_steps": status.get("done_steps", []),
        "error_kind": status.get("error_kind", ""),
        "error": status.get("error", ""),
        "completion_scope": status.get("extra", {}).get("completion_scope", {}),
    }


def run_profile(profile_id: str) -> dict[str, object]:
    """Run exactly one profile, always releasing its reservation."""
    config = build_trial_config(profile_id)
    issues = validate_config(config)
    if issues:
        raise RuntimeError("测试配置契约无效: " + "; ".join(issues))
    reservation = reserve_pipeline_launch(ROOT)
    orchestrator: PipelineOrchestrator | None = None
    try:
        write_json(reservation.run_dir / "config.json", config)
        reservation.mark_runner_started(os.getpid())
        print(f"[eq-matrix] START profile={profile_id} run_id={reservation.run_id}", flush=True)
        orchestrator = PipelineOrchestrator(
            backend=str(config["backend"]), use_llm=False,
        )
        success = orchestrator.run(run_dir=reservation.run_dir)
        summary = _status_summary(reservation.run_dir)
        result = {
            "profile": profile_id,
            "run_id": reservation.run_id,
            "success": success,
            "finished_at": _now(),
            **summary,
        }
        print("[eq-matrix] RESULT " + json.dumps(result, ensure_ascii=False), flush=True)
        return result
    except KeyboardInterrupt:
        if orchestrator is not None:
            orchestrator.abort_from_signal()
        raise
    except Exception as exc:
        result = {
            "profile": profile_id,
            "run_id": reservation.run_id,
            "success": False,
            "finished_at": _now(),
            "state": "runner_exception",
            "error_kind": "runner_exception",
            "error": str(exc)[:240],
        }
        print("[eq-matrix] RESULT " + json.dumps(result, ensure_ascii=False), flush=True)
        return result
    finally:
        reservation.release()


def _write_report(results: list[dict[str, object]]) -> Path:
    report = ROOT / "md_run" / f"eq_matrix_{datetime.now():%Y%m%d_%H%M%S}.json"
    write_json(report, {
        "kind": "four_profile_eq_only_acceptance",
        "started_profiles": [result["profile"] for result in results],
        "eq_segments_ns": EQ_SEGMENTS_NS,
        "results": results,
        "finished_at": _now(),
    })
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile", choices=PROFILE_ORDER, action="append",
        help="Run one profile; repeat to select an ordered subset.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Validate and print selected configurations only.")
    args = parser.parse_args(argv)
    selected = tuple(args.profile or PROFILE_ORDER)
    if args.dry_run:
        for profile_id in selected:
            config = build_trial_config(profile_id)
            issues = validate_config(config)
            print(json.dumps({"profile": profile_id, "issues": issues, "config": config}, ensure_ascii=False))
        return 0

    results: list[dict[str, object]] = []
    previous_handlers = {
        signal.SIGINT: signal.signal(signal.SIGINT, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt)),
        signal.SIGTERM: signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt)),
    }
    try:
        for profile_id in selected:
            try:
                results.append(run_profile(profile_id))
            except PipelineLockConflict:
                results.append({
                    "profile": profile_id,
                    "success": False,
                    "finished_at": _now(),
                    "state": "lock_conflict",
                    "error_kind": "lock_conflict",
                    "error": "已有任务运行",
                })
                break
            except KeyboardInterrupt:
                results.append({
                    "profile": profile_id,
                    "success": False,
                    "finished_at": _now(),
                    "state": "aborted",
                    "error_kind": "user_stop",
                    "error": "验收矩阵已中止",
                })
                break
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
    report = _write_report(results)
    print(f"[eq-matrix] REPORT {report}", flush=True)
    return 0 if results and all(bool(result.get("success")) for result in results) else 1


if __name__ == "__main__":
    sys.exit(main())
