"""G-07/T-07 local WSL acceptance harness.

The harness exercises the public run projection and the run-local private
audit files with bounded synthetic failures.  Only the Packmol start probe is
real; all other failures are injected after the run has been registered, so no
scientific command or network request is started by this acceptance check.
"""

from __future__ import annotations

import argparse
import json
import platform
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from willy.agent_simulation import SimulationAgent
from willy.errors import ErrorKind, StepError, StepResult
from willy.env_registry import AVAILABLE, RUNTIME_UNAVAILABLE, probe_executable_runtime, resolve_tool
from willy.frontend_api import get_run_panel_snapshot, save_llm_config
from willy.pipeline_orchestrator import PipelineOrchestrator
from willy.run_registry import RunRegistry
from willy.simulation.protocol import default_md_config
from willy.step_registry import STEP_REGISTRY


_MARKERS = ("/private/g07", "gmx grompp", "API_KEY=g07-secret", "raw engine output")


def _distribution() -> dict[str, str]:
    try:
        release = platform.freedesktop_os_release()
    except (AttributeError, OSError):
        return {}
    return {
        key: release[key]
        for key in ("ID", "VERSION_ID")
        if isinstance(release.get(key), str) and release[key]
    }


class _EqProposalAgent:
    """Deterministic proposal boundary; it never writes or executes anything."""

    name = "SimulationAgent"

    @staticmethod
    def propose_eq_recovery(_result: StepResult, _config_path: str) -> dict[str, object]:
        return {
            "summary": "受控 EQ 方案",
            "adjustments": [{
                "field": "dt",
                "after": 0.0005,
                "purpose": "降低数值不稳定风险",
            }],
        }


def _fake_llm() -> MagicMock:
    client = MagicMock()
    message = SimpleNamespace(content="", tool_calls=None)
    client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=message)],
    )
    return client


def _bound_orchestrator(root: Path, run_id: str, step: int, label: str) -> tuple[PipelineOrchestrator, Path]:
    run_dir = root / "md_run" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "config.json").write_text(json.dumps({
        "backend": "g16",
        "residues": {"Li": 1},
        "molecules": {"Li": {"charge": 0, "spin": 1}},
        "md": default_md_config(),
    }), encoding="utf-8")
    orchestrator = PipelineOrchestrator(backend="g16", use_llm=False)
    orchestrator._run_dir = run_dir
    orchestrator._run_config_path = run_dir / "config.json"
    orchestrator._run_registry = RunRegistry(root)
    orchestrator._run_registry.register_run(run_dir, backend="g16", total_steps=10)
    orchestrator._sm.bind_status_path(run_dir / "status.json")
    orchestrator._sm.bind_observer(orchestrator._record_run_status)
    orchestrator._sm.set_extra(run_id=run_id)
    orchestrator._sm.set_step(step, label, "simulation")
    activity = {
        7: ("Packmol", "初始盒子构建", "system", "当前体系"),
        8: ("GROMACS", "输入预处理", "stage", "em"),
        9: ("GROMACS", "运行模拟", "stage", "eq"),
        10: ("GROMACS", "运行模拟", "stage", "prod"),
    }[step]
    orchestrator._sm.set_activity(*activity, current=0, total=1)
    return orchestrator, run_dir


def _simulation_agent(orchestrator: PipelineOrchestrator, client: MagicMock | None = None) -> SimulationAgent:
    return SimulationAgent(
        llm_client=client or _fake_llm(),
        model="g07-test-model",
        on_action=orchestrator._sm.add_action,
        on_decision=orchestrator._record_agent_decision,
        on_config_updated=orchestrator._record_simulation_config_update,
        recovery_policy=orchestrator._recovery_policy,
        tool_catalog=orchestrator._tool_catalog,
    )


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _scenario(
    root: Path,
    name: str,
    step: int,
    label: str,
    kind: ErrorKind,
    *,
    agent_kind: str | None = None,
) -> dict[str, object]:
    orchestrator, run_dir = _bound_orchestrator(root, f"md_g07_{name}", step, label)
    if agent_kind == "simulation":
        orchestrator.use_llm = True
        orchestrator._agents[3] = _simulation_agent(orchestrator)
    elif agent_kind == "eq":
        orchestrator.use_llm = True
        orchestrator._agents[3] = _EqProposalAgent()  # type: ignore[assignment]
    failure = StepResult(
        step_name=name,
        step_index=step,
        success=False,
        error=StepError(
            kind,
            f"{name} failed at /private/g07/run.log; gmx grompp --bad; API_KEY=g07-secret",
            raw_output="raw engine output /private/g07/run.log",
            hint="raw engine output should never cross the public boundary",
        ),
        target_type="stage",
        target="当前体系" if step == 7 else {8: "em", 9: "eq", 10: "prod"}[step],
    )
    orchestrator._record_step_result(failure, label, source="g07_injected")
    handled = orchestrator._handle_single_result(
        failure, label, run_dir, {}, 3, step,
    )

    status = json.loads((run_dir / "status.json").read_text(encoding="utf-8"))
    events = _read_jsonl(run_dir / "events.jsonl")
    decisions = _read_jsonl(run_dir / "decision_trace.jsonl")
    structured = _read_jsonl(run_dir / "logs" / "structured.jsonl")

    # The panel snapshot is the same projection consumed by the web workbench.
    import willy.frontend_api as frontend_api
    old_root = frontend_api.ROOT
    frontend_api.ROOT = root
    try:
        panel = get_run_panel_snapshot(run_dir.name)
    finally:
        frontend_api.ROOT = old_root

    private_records = [*events, *decisions, *structured]
    private_text = json.dumps(private_records, ensure_ascii=False)
    public_text = json.dumps({
        "status": status,
        "panel": panel,
    }, ensure_ascii=False)
    decision_facts = [
        {
            "run_id": item.get("run_id"),
            "step": item.get("step"),
            "error_kind": item.get("error_kind"),
        }
        for item in decisions
        if all(key in item for key in ("run_id", "step", "error_kind"))
    ]
    return {
        "name": name,
        "step": step,
        "error_kind": kind.value,
        "handled": handled,
        "state": status.get("state"),
        "public_error": status.get("error"),
        "public_error_event": panel.get("error_event"),
        "decision_facts": decision_facts,
        "event_count": len(events),
        "structured_event_count": len(structured),
        "public_projection_redacted": not any(marker in public_text for marker in _MARKERS),
        "private_records_redacted": not any(marker in private_text for marker in _MARKERS),
        "private_decision_contract": any(
            fact == {"run_id": run_dir.name, "step": step, "error_kind": kind.value}
            for fact in decision_facts
        ),
    }


def run_acceptance(project_root: str | Path) -> dict[str, object]:
    """Run the G-07 matrix in an isolated temporary run registry."""
    root = Path(project_root).resolve()
    with tempfile.TemporaryDirectory(prefix="willy-g07-") as temp:
        isolated = Path(temp)
        scenarios = [
            _scenario(isolated, "input", 7, "Packmol 盒子构建", ErrorKind.INPUT_CONTRACT,
                      agent_kind="simulation"),
            _scenario(isolated, "dependency", 7, "Packmol 盒子构建", ErrorKind.DEPENDENCY_MISSING),
            _scenario(isolated, "abi", 7, "Packmol 盒子构建", ErrorKind.RUNTIME_UNAVAILABLE),
            _scenario(isolated, "grompp", 8, "GROMACS 输入预处理", ErrorKind.GROMPP_FAILED,
                      agent_kind="simulation"),
            _scenario(isolated, "eq", 9, "GROMACS 三点式退火平衡", ErrorKind.EQUILIBRATION_FAILED,
                      agent_kind="eq"),
            _scenario(isolated, "prod", 10, "GROMACS 生产模拟", ErrorKind.MDRUN_FAILED,
                      agent_kind="simulation"),
        ]

        # The real bundled executable is probed, while no raw stdout/stderr is
        # retained.  A non-zero usage exit is still a successful start probe.
        real_probe = probe_executable_runtime(root / "vendor" / "packmol", label="Packmol")
        abi_root = isolated / "abi-bundle"
        abi_binary = abi_root / "vendor" / "packmol"
        abi_binary.parent.mkdir(parents=True)
        abi_binary.write_text("#!/bin/sh\necho 'version GLIBC_2.34 not found' >&2\nexit 1\n", encoding="utf-8")
        abi_binary.chmod(0o755)
        abi_runtime_probe = probe_executable_runtime(abi_binary, label="Packmol")
        abi_probe = resolve_tool("packmol", project_root=abi_root)
        missing_probe = resolve_tool("packmol", project_root=isolated / "missing-bundle")

        import willy.frontend_api as frontend_api
        old_root = frontend_api.ROOT
        frontend_api.ROOT = isolated
        before_runs = {
            path.name for path in (isolated / "md_run").iterdir()
            if path.is_dir()
        }
        try:
            llm_message = save_llm_config("\n", "https://example.invalid/v1", "g07-model")
        finally:
            frontend_api.ROOT = old_root
        after_runs = {
            path.name for path in (isolated / "md_run").iterdir()
            if path.is_dir()
        }
        llm_config = {
            "public_message": llm_message,
            "safe": "g07-secret" not in llm_message and "example.invalid" not in llm_message,
            "run_artifacts_created": bool(after_runs - before_runs),
        }

        result = {
            "schema_version": 1,
            "acceptance": "passed",
            "scope": "local WSL bounded G-07/T-07 error matrix",
            "environment": {
                "system": platform.system(),
                "release": platform.release(),
                "machine": platform.machine(),
                "distribution": _distribution(),
                "real_packmol": {
                    "status": real_probe.status,
                    "classification": real_probe.classification,
                    "started": real_probe.status == AVAILABLE,
                    "returncode": real_probe.returncode,
                },
                "abi_injection": {
                    "status": abi_runtime_probe.status,
                    "classification": abi_runtime_probe.classification,
                    "runtime_unavailable": abi_runtime_probe.status == RUNTIME_UNAVAILABLE,
                    "public_reason": abi_probe.public_reason,
                },
                "dependency_injection": {
                    "status": missing_probe.status,
                    "missing": missing_probe.status == "missing",
                },
            },
            "scenarios": scenarios,
            "llm_configuration_error": llm_config,
        }
        if result["environment"]["real_packmol"]["status"] != AVAILABLE:  # type: ignore[index]
            result["acceptance"] = "conditional"
        if not all(
            bool(item["public_projection_redacted"] and item["private_records_redacted"]
                   and item["private_decision_contract"])
            for item in scenarios
        ) or not llm_config["safe"] or llm_config["run_artifacts_created"]:
            result["acceptance"] = "failed"
        return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    payload = run_acceptance(args.project_root)
    rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if payload["acceptance"] in {"passed", "conditional"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
