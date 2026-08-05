"""Shared GROMACS execution and artifact-contract helpers.

The public stage executors own the scientific checks for their individual
stage.  This module owns the common GROMACS input contract, command execution
and mandatory artifact verification.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional
import os
import json
import re
import selectors
import signal
import subprocess
import time

from willy.errors import ErrorKind, StepError, StepResult
from willy.env_registry import EnvironmentRegistryError, build_tool_env, require_tool
from willy.simulation.manifest import (
    RunLock,
    RunLockError,
    ManifestError,
    estimate_output_bytes,
    has_disk_capacity,
    initialize_manifest,
    load_manifest,
    prepare_stage_attempt,
    record_stage_result,
    require_prior_stage,
    stage_can_resume,
    stage_contract,
    stop_requested,
)
from willy.simulation.mdrun_eta import (
    MDRUN_HEARTBEAT_INTERVAL_S,
    begin_mdrun_eta,
    consume_mdrun_verbose_output,
    finish_mdrun_eta,
    heartbeat_mdrun_eta,
)
from willy.process_lifecycle import ProcessTerminationController, record_process_lifecycle
from willy.step_registry import EM_STEP, STEP_REGISTRY


_STAGE_STEP_INDEX = {
    stage: STEP_REGISTRY.index_for_stage(stage)
    for stage in ("em", "eq", "prod")
}
_DEFAULT_COORDINATE = {"em": "model.pdb", "eq": "em.gro", "prod": "eq.gro"}


@dataclass(frozen=True)
class GromacsInputs:
    """The complete file contract supplied to one GROMACS stage."""

    stage: str
    work_dir: Path
    topol: Path
    itps: tuple[Path, ...]
    mdp: Path
    coordinates: Path
    tpr: Path


@dataclass(frozen=True)
class StagePreparation:
    """Manifest-approved inputs and continuation strategy for one MD stage."""

    inputs: GromacsInputs
    contract: dict
    metadata: dict
    continuation_checkpoint: Path | None
    append: bool


def stage_step_index(stage: str) -> int:
    return _STAGE_STEP_INDEX.get(stage, EM_STEP)


def stage_output_paths(work_dir: Path, stage: str, tpr: Path | None = None) -> dict[str, Path]:
    """Return the required and conventional output paths for a stage."""
    return {
        "tpr": tpr or work_dir / f"{stage}.tpr",
        "gro": work_dir / f"{stage}.gro",
        "xtc": work_dir / f"{stage}.xtc",
        "edr": work_dir / f"{stage}.edr",
        "log": work_dir / f"{stage}.log",
        "cpt": work_dir / f"{stage}.cpt",
        "trr": work_dir / f"{stage}.trr",
    }


def build_stage_inputs(
    work_dir: str | Path,
    stage: str,
    *,
    topol: str | Path | None = None,
    itps: Iterable[str | Path] | None = None,
    mdp: str | Path | None = None,
    coordinates: str | Path | None = None,
    tpr: str | Path | None = None,
) -> GromacsInputs:
    """Build one explicit stage contract from a run workspace and overrides."""
    cwd = Path(work_dir).resolve()

    def resolve(value: str | Path | None, default: Path) -> Path:
        if value is None:
            return default
        candidate = Path(value)
        return candidate if candidate.is_absolute() else cwd / candidate

    topol_path = resolve(topol, cwd / "topol.top")
    mdp_path = resolve(mdp, cwd / f"{stage}.mdp")
    coordinate_path = resolve(coordinates, cwd / _DEFAULT_COORDINATE[stage])
    tpr_path = resolve(tpr, cwd / f"{stage}.tpr")
    itp_paths = tuple(
        resolve(value, cwd / Path(value).name) for value in itps
    ) if itps is not None else tuple(sorted(cwd.glob("*.itp")))

    return GromacsInputs(
        stage=stage,
        work_dir=cwd,
        topol=topol_path,
        itps=itp_paths,
        mdp=mdp_path,
        coordinates=coordinate_path,
        tpr=tpr_path,
    )


def validate_stage_inputs(inputs: GromacsInputs) -> list[str]:
    """Return all missing input files instead of letting grompp fail opaquely."""
    missing: list[str] = []
    required = {
        "工作目录": inputs.work_dir,
        "topol.top": inputs.topol,
        ".mdp": inputs.mdp,
        "起始结构": inputs.coordinates,
    }
    for label, path in required.items():
        if not path.exists():
            missing.append(f"{label}: {path}")
    if not inputs.itps:
        missing.append(".itp: 未提供任何拓扑 include 文件")
    else:
        missing.extend(f".itp: {path}" for path in inputs.itps if not path.exists())
    return missing


def prepare_stage_execution(
    stage: str,
    cwd: str | Path,
    *,
    mdp: str | Path | None = None,
    conf: str | Path | None = None,
    topol: str | Path | None = None,
    itps: Iterable[str | Path] | None = None,
    tpr: str | Path | None = None,
) -> StagePreparation:
    """Bind one stage to a manifest-approved protocol and disk preflight."""
    inputs = build_stage_inputs(
        cwd, stage, topol=topol, itps=itps, mdp=mdp, coordinates=conf, tpr=tpr,
    )
    missing = validate_stage_inputs(inputs)
    if missing:
        raise ManifestError("; ".join(missing))
    config_path = inputs.work_dir / "config.json"
    if not config_path.is_file():
        raise ManifestError("每个 GROMACS 阶段必须使用 run 内固化的 config.json")
    if not (inputs.work_dir / "md_manifest.json").is_file():
        try:
            config = json.loads(config_path.read_text())
            seed = int(config.get("md", {}).get("run_seed", 1))
        except (OSError, ValueError, json.JSONDecodeError):
            seed = 1
        initialize_manifest(inputs.work_dir, config_path, random_seed=seed)
    else:
        load_manifest(inputs.work_dir)

    parent_checkpoint: Path | None = None
    if stage in {"eq", "prod"}:
        require_prior_stage(inputs.work_dir, stage)
    if stage == "prod":
        parent_checkpoint = inputs.work_dir / "eq.cpt"
        if not parent_checkpoint.is_file() or parent_checkpoint.stat().st_size <= 0:
            raise ManifestError(
                "PROD 必须通过已验收 EQ 的非空 eq.cpt 连续启动"
            )

    contract = stage_contract(
        inputs.work_dir,
        stage,
        config_path=config_path,
        topol=inputs.topol,
        itps=inputs.itps,
        mdp=inputs.mdp,
        coordinates=inputs.coordinates,
        parent_checkpoint=parent_checkpoint,
    )
    metadata = _stage_mdp_metadata(inputs.work_dir, stage)
    estimated = estimate_output_bytes(
        _coordinate_atom_count(inputs.coordinates),
        int(metadata.get("nsteps", 1)),
        trr_enabled=bool(_load_output_trr(config_path)),
    )
    if not has_disk_capacity(inputs.work_dir, estimated):
        raise ManifestError(f"磁盘剩余空间不足，预计本阶段至少需要 {estimated} 字节")

    resume = stage == "prod" and stage_can_resume(inputs.work_dir, stage, contract)
    prepare_stage_attempt(
        inputs.work_dir,
        stage,
        contract,
        estimated_output_bytes=estimated,
    )
    return StagePreparation(
        inputs=inputs,
        contract=contract,
        metadata=metadata,
        continuation_checkpoint=(inputs.work_dir / f"{stage}.cpt") if resume else parent_checkpoint,
        append=resume,
    )


def record_stage_execution(
    preparation: StagePreparation,
    *,
    success: bool,
    outputs: dict[str, str] | None = None,
    details: dict | None = None,
    error: StepError | None = None,
) -> None:
    """Store evidence privately in the run manifest, never in public status."""
    private_details = dict(details or {})
    execution = private_details.pop("execution", None)
    error_evidence: dict[str, object] = {}
    if isinstance(execution, dict):
        error_evidence.update(execution)
    if error is not None and error.raw_output:
        error_evidence["raw_output_tail"] = error.raw_output[-2000:]
    record_stage_result(
        preparation.inputs.work_dir,
        preparation.inputs.stage,
        success=success,
        contract=preparation.contract,
        outputs=outputs,
        details=private_details or None,
        error_kind=error.kind.value if error else "",
        error_message=error.message if error else "",
        error_evidence=error_evidence or None,
    )


def _load_output_trr(config_path: Path) -> bool:
    try:
        return bool(json.loads(config_path.read_text()).get("md", {}).get("outputs", {}).get("trr", False))
    except (OSError, json.JSONDecodeError, AttributeError):
        return False


def _stage_mdp_metadata(work_dir: Path, stage: str) -> dict:
    path = work_dir / "mdp_metadata.json"
    if path.is_file():
        try:
            metadata = json.loads(path.read_text())
            value = metadata.get("stages", {}).get(stage, {})
            if isinstance(value, dict):
                return value
        except (OSError, json.JSONDecodeError):
            pass
    match = re.search(r"^\s*nsteps\s*=\s*(\d+)", (work_dir / f"{stage}.mdp").read_text(), re.MULTILINE)
    return {"nsteps": int(match.group(1)) if match else 1}


def _coordinate_atom_count(path: Path) -> int:
    try:
        if path.suffix.lower() == ".gro":
            return max(1, int(path.read_text().splitlines()[1].strip()))
        return max(1, sum(1 for line in path.read_text().splitlines() if line.startswith(("ATOM", "HETATM"))))
    except (OSError, ValueError, IndexError):
        return 1


def run_gmx(
    args: list[str],
    cwd: Path,
    timeout: Optional[int] = None,
    input_text: str | None = None,
    on_mdrun_heartbeat: Callable[[dict], None] | None = None,
) -> subprocess.CompletedProcess:
    """Run GROMACS under a run lock and honor a checkpoint-first stop request."""
    try:
        gmx = require_tool("gmx")
        gmx_env = build_tool_env("gmx")
    except EnvironmentRegistryError as exc:
        raise FileNotFoundError(str(exc)) from exc
    command = [str(gmx.executable), *args]
    with RunLock(cwd):
        if args and args[0] == "mdrun":
            return _run_mdrun_with_live_eta(
                command,
                cwd,
                timeout=timeout,
                input_text=input_text,
                args=args,
                on_heartbeat=on_mdrun_heartbeat,
            )
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.PIPE if input_text is not None else None,
            text=True,
            cwd=str(cwd),
            start_new_session=True,
            env=gmx_env,
        )
        started_at = time.monotonic()
        pending_input = input_text
        termination: ProcessTerminationController | None = None
        timed_out = False
        while True:
            remaining = None
            if timeout is not None:
                remaining = timeout - (time.monotonic() - started_at)
                if remaining <= 0 and termination is None:
                    timed_out = True
                    termination = ProcessTerminationController(process)
                    termination.request("timeout")
            try:
                stdout, stderr = process.communicate(
                    input=pending_input,
                    timeout=min(1.0, max(0.01, remaining)) if remaining is not None else 1.0,
                )
                result = subprocess.CompletedProcess(
                    args=command,
                    returncode=process.returncode,
                    stdout=stdout,
                    stderr=stderr,
                )
                if termination is not None:
                    termination.finish()
                    record_process_lifecycle(cwd, termination, command=command, returncode=result.returncode)
                if timed_out:
                    raise subprocess.TimeoutExpired(command, timeout, output=stdout, stderr=stderr)
                return result
            except subprocess.TimeoutExpired:
                pending_input = None
                if termination is None and stop_requested(cwd):
                    termination = ProcessTerminationController(process)
                    termination.request("stop_requested")
                if termination is not None:
                    termination.tick()


def _run_mdrun_with_live_eta(
    command: list[str],
    cwd: Path,
    *,
    timeout: Optional[int],
    input_text: str | None,
    args: list[str],
    on_heartbeat: Callable[[dict], None] | None,
) -> subprocess.CompletedProcess:
    """Run mdrun while recording GROMACS ETA and independent liveness facts."""
    stage = _mdrun_stage(args)
    begin_mdrun_eta(cwd, stage)
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.PIPE if input_text is not None else None,
        cwd=str(cwd),
        start_new_session=True,
        env=build_tool_env("gmx"),
    )
    if input_text is not None and process.stdin is not None:
        process.stdin.write(input_text.encode())
        process.stdin.close()

    started_at = time.monotonic()
    termination: ProcessTerminationController | None = None
    timed_out = False
    output = {"stdout": bytearray(), "stderr": bytearray()}
    carry = ""
    next_heartbeat_at = started_at
    selector = selectors.DefaultSelector()
    assert process.stdout is not None and process.stderr is not None
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    try:
        while selector.get_map():
            now = time.monotonic()
            if now >= next_heartbeat_at:
                snapshot = heartbeat_mdrun_eta(cwd, stage)
                if on_heartbeat is not None:
                    try:
                        on_heartbeat(snapshot)
                    except Exception:
                        # Status observation must never interrupt an engine run.
                        pass
                next_heartbeat_at = now + MDRUN_HEARTBEAT_INTERVAL_S
            if timeout is not None and time.monotonic() - started_at >= timeout:
                if termination is None:
                    timed_out = True
                    termination = ProcessTerminationController(process)
                    termination.request("timeout", now=now)
            if stop_requested(cwd) and termination is None:
                termination = ProcessTerminationController(process)
                termination.request("stop_requested", now=now)
            if termination is not None:
                termination.tick(now=now)

            for key, _ in selector.select(timeout=1.0):
                chunk = os.read(key.fileobj.fileno(), 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                output[key.data].extend(chunk)
                carry = consume_mdrun_verbose_output(
                    cwd, stage, chunk.decode(errors="replace"), carry=carry,
                )
        returncode = process.wait()
        stopped = termination is not None and termination.reason == "stop_requested"
        finish_mdrun_eta(cwd, stage, success=returncode == 0 and not stopped and not timed_out)
        if termination is not None:
            termination.finish()
            record_process_lifecycle(
                cwd,
                termination,
                command=command,
                returncode=returncode,
                checkpoint_exists=(cwd / f"{stage}.cpt").is_file(),
            )
        if timed_out:
            raise subprocess.TimeoutExpired(
                command, timeout,
                output=bytes(output["stdout"]), stderr=bytes(output["stderr"]),
            )
        return subprocess.CompletedProcess(
            args=command,
            returncode=returncode if not stopped else (returncode or 130),
            stdout=bytes(output["stdout"]).decode(errors="replace"),
            stderr=bytes(output["stderr"]).decode(errors="replace"),
        )
    finally:
        selector.close()


def _mdrun_stage(args: list[str]) -> str:
    """Return the public stage label encoded by the stable ``-deffnm`` argument."""
    try:
        index = args.index("-deffnm")
        value = Path(args[index + 1]).name
    except (ValueError, IndexError):
        return "mdrun"
    return value if value in _STAGE_STEP_INDEX else "mdrun"


def grompp_and_mdrun(
    stage: str,
    cwd: Path,
    mdp: str | Path | None = None,
    conf: str | Path | None = None,
    topol: str | Path | None = None,
    itps: Iterable[str | Path] | None = None,
    tpr: str | Path | None = None,
    extra_grompp: list[str] | None = None,
    extra_mdrun: list[str] | None = None,
    continuation_checkpoint: str | Path | None = None,
    append: bool = False,
    on_progress=None,
    on_heartbeat: Callable[[dict], None] | None = None,
) -> StepResult:
    """Run ``gmx grompp`` then ``gmx mdrun`` and enforce stage artifacts.

    The returned result always contains ``tpr``, ``gro``, ``xtc`` and ``edr``
    on success.  ``log`` and ``cpt`` are registered when GROMACS writes them.
    """
    started_at = time.time()
    inputs = build_stage_inputs(
        cwd, stage, topol=topol, itps=itps, mdp=mdp, coordinates=conf, tpr=tpr,
    )
    step_index = stage_step_index(stage)
    missing = validate_stage_inputs(inputs)
    if missing:
        return StepResult(
            step_name=f"md_{stage}",
            step_index=step_index,
            success=False,
            error=StepError(
                kind=ErrorKind.FILE_NOT_FOUND,
                message=f"{stage}: GROMACS 输入不完整: {'; '.join(missing)}",
                hint="确认本次 run_dir 内有 topol.top、所需 .itp、.mdp 和起始结构",
            ),
            duration_s=time.time() - started_at,
            extra={"inputs": _inputs_dict(inputs)},
        )

    grompp_args = [
        "grompp", "-f", str(inputs.mdp), "-c", str(inputs.coordinates),
        "-p", str(inputs.topol), "-o", str(inputs.tpr),
        "-po", str(inputs.work_dir / f"{stage}_out.mdp"),
    ]
    if extra_grompp:
        if "-maxwarn" in extra_grompp:
            return StepResult(
                step_name=f"md_{stage}", step_index=step_index, success=False,
                error=StepError(
                    kind=ErrorKind.INPUT_CONTRACT,
                    message="默认不允许 -maxwarn；警告白名单需先基于真实体系验收建立",
                ),
                duration_s=time.time() - started_at,
                extra={"inputs": _inputs_dict(inputs)},
            )
        grompp_args.extend(extra_grompp)
    if continuation_checkpoint is not None:
        checkpoint = Path(continuation_checkpoint)
        if not checkpoint.is_file() or checkpoint.stat().st_size <= 0:
            return StepResult(
                step_name=f"md_{stage}", step_index=step_index, success=False,
                error=StepError(
                    kind=ErrorKind.RECOVERY_CONFLICT,
                    message=f"{stage}: 指定的 checkpoint 不存在或为空: {checkpoint}",
                    hint="只能使用与本阶段协议指纹一致的非空 checkpoint 恢复",
                ),
                duration_s=time.time() - started_at,
                extra={"inputs": _inputs_dict(inputs)},
            )
        grompp_args.extend(["-t", str(checkpoint)])

    if on_progress:
        on_progress({
            "tool": "GROMACS", "operation": "输入预处理",
            "target_type": "stage", "target": stage,
            "current": 1, "total": 2,
        })

    try:
        grompp = run_gmx(grompp_args, inputs.work_dir, timeout=60)
    except FileNotFoundError:
        return _gmx_missing_result(stage, started_at, inputs)
    except RunLockError as exc:
        return _lock_conflict_result(stage, started_at, inputs, str(exc))
    except subprocess.TimeoutExpired:
        return StepResult(
            step_name=f"md_{stage}", step_index=step_index, success=False,
            error=StepError(
                kind=ErrorKind.INPUT_CONTRACT,
                message=f"{stage}: grompp 超时 (60s)",
                hint="检查 .mdp、topol.top 和 .itp 的一致性",
            ),
            duration_s=time.time() - started_at,
            extra={"inputs": _inputs_dict(inputs)},
        )
    if grompp.returncode != 0:
        raw = _stderr_tail(grompp)
        return StepResult(
            step_name=f"md_{stage}", step_index=step_index, success=False,
            error=StepError(
                kind=ErrorKind.INPUT_CONTRACT,
                message=f"{stage}: grompp 失败",
                raw_output=raw,
                hint=_grompp_hint(raw),
            ),
            duration_s=time.time() - started_at,
            extra={"inputs": _inputs_dict(inputs)},
        )

    if on_progress:
        on_progress({
            "tool": "GROMACS", "operation": "运行模拟",
            "target_type": "stage", "target": stage,
            "current": 2, "total": 2,
        })

    mdrun_args = ["mdrun", "-s", str(inputs.tpr), "-deffnm", stage, "-v"]
    if append:
        if continuation_checkpoint is None:
            return StepResult(
                step_name=f"md_{stage}", step_index=step_index, success=False,
                error=StepError(
                    kind=ErrorKind.RECOVERY_CONFLICT,
                    message=f"{stage}: -append 必须配合经验证的 checkpoint",
                ),
                duration_s=time.time() - started_at,
                extra={"inputs": _inputs_dict(inputs)},
            )
        mdrun_args.extend(["-cpi", str(continuation_checkpoint), "-append"])
    if extra_mdrun:
        mdrun_args.extend(extra_mdrun)
    try:
        mdrun_kwargs = {"on_mdrun_heartbeat": on_heartbeat} if on_heartbeat is not None else {}
        mdrun = run_gmx(mdrun_args, inputs.work_dir, **mdrun_kwargs)
    except FileNotFoundError:
        return _gmx_missing_result(stage, started_at, inputs)
    except RunLockError as exc:
        return _lock_conflict_result(stage, started_at, inputs, str(exc))
    except subprocess.TimeoutExpired:
        return StepResult(
            step_name=f"md_{stage}", step_index=step_index, success=False,
            error=StepError(
                kind=ErrorKind.TIMEOUT,
                message=f"{stage}: mdrun 超时",
                hint="减少模拟时长、增加计算资源，或检查是否需要从 checkpoint 续跑",
            ),
            outputs={"tpr": str(inputs.tpr)},
            artifacts=[str(inputs.tpr)],
            duration_s=time.time() - started_at,
            extra={
                "inputs": _inputs_dict(inputs),
                "execution": {"returncode": None, "signal": "timeout"},
            },
        )
    if mdrun.returncode != 0:
        raw = _stderr_tail(mdrun)
        return StepResult(
            step_name=f"md_{stage}", step_index=step_index, success=False,
            error=StepError(
                kind=_mdrun_error_kind(raw),
                message=f"{stage}: mdrun 失败",
                raw_output=raw,
                hint="检查 .log 中的 LINCS、NaN/Inf、域分解或 PME 报错",
            ),
            outputs={"tpr": str(inputs.tpr)},
            artifacts=[str(inputs.tpr)],
            duration_s=time.time() - started_at,
            extra={
                "inputs": _inputs_dict(inputs),
                "execution": _mdrun_failure_evidence(mdrun, raw),
            },
        )

    paths = stage_output_paths(inputs.work_dir, stage, inputs.tpr)
    trajectory_error = ""
    if stage == "em" and not paths["xtc"].is_file() and paths["gro"].is_file():
        # A minimization that converges at step 0 writes no compressed frame.
        # The stage artifact contract still requires an XTC, so serialize the
        # final structure as a one-frame trajectory without weakening it.
        try:
            converted = run_gmx(
                ["trjconv", "-f", str(paths["gro"]), "-s", str(inputs.tpr), "-o", str(paths["xtc"])],
                inputs.work_dir,
                timeout=60,
                input_text="0\n",
            )
            if converted.returncode != 0:
                trajectory_error = _stderr_tail(converted)
        except (FileNotFoundError, RunLockError, subprocess.TimeoutExpired) as exc:
            trajectory_error = str(exc)
    required_names = ("tpr", "gro", "xtc", "edr")
    missing_outputs = [
        name for name in required_names
        if not paths[name].is_file() or paths[name].stat().st_size <= 0
    ]
    if missing_outputs:
        return StepResult(
            step_name=f"md_{stage}", step_index=step_index, success=False,
            error=StepError(
                kind=ErrorKind.MDRUN_FAILED,
                message=f"{stage}: mdrun 返回成功但缺少必需产物: {', '.join(missing_outputs)}",
                raw_output=trajectory_error or _stderr_tail(mdrun),
                hint="确认 MDP 保留 nstenergy 和 nstxout-compressed，并检查磁盘空间",
            ),
            outputs={"tpr": str(inputs.tpr)},
            artifacts=[str(inputs.tpr)],
            duration_s=time.time() - started_at,
            extra={
                "inputs": _inputs_dict(inputs),
                "execution": _mdrun_failure_evidence(
                    mdrun, trajectory_error or _stderr_tail(mdrun),
                ),
            },
        )

    outputs = {name: str(paths[name]) for name in required_names}
    artifacts = [str(paths[name]) for name in required_names]
    for optional in ("log", "cpt", "trr"):
        if paths[optional].exists():
            outputs[optional] = str(paths[optional])
            artifacts.append(str(paths[optional]))
    return StepResult(
        step_name=f"md_{stage}", step_index=step_index, success=True,
        outputs=outputs, artifacts=artifacts,
        duration_s=time.time() - started_at,
        extra={"inputs": _inputs_dict(inputs)},
    )


def extract_energy_xvg(
    edr_path: Path,
    term: str,
    output_path: Path,
) -> tuple[bool, str]:
    """Extract an optional energy term with ``gmx energy`` without a shell."""
    try:
        result = run_gmx(
            ["energy", "-f", str(edr_path), "-o", str(output_path)],
            edr_path.parent,
            timeout=30,
            input_text=f"{term}\n0\n",
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, RunLockError) as exc:
        return False, str(exc)
    if result.returncode != 0 or not output_path.exists():
        return False, _stderr_tail(result)
    return True, ""


def parse_xvg(xvg_path: Path) -> tuple[list[str], list[list[float]]]:
    """Parse an XVG file into legends and columns."""
    text = xvg_path.read_text()
    lines = text.split("\n")
    legends: list[str] = []
    for line in lines:
        if line.startswith("@ s") and "legend" in line:
            match = re.search(r'"([^"]*)"', line)
            if match:
                legends.append(match.group(1))

    data: list[list[float]] = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("@") or line.startswith("#"):
            continue
        try:
            data.append([float(value) for value in line.split()])
        except ValueError:
            continue
    if not data:
        return legends, []
    return legends, [[row[index] for row in data] for index in range(len(data[0]))]


def check_last_fraction(
    xvg_path: Path,
    fraction: float = 0.2,
    max_rel_change: float = 0.10,
) -> dict:
    """Check the stability of every non-time XVG column in its final fraction."""
    legends, columns = parse_xvg(xvg_path)
    results = {}
    for index, column in enumerate(columns):
        # GROMACS writes legends for y series only; the first numeric column is time.
        if len(legends) == len(columns) - 1:
            name = "Time" if index == 0 else legends[index - 1]
        else:
            name = legends[index] if index < len(legends) else f"col_{index}"
        if name.lower() in ("time", "t") or not column:
            continue
        count = max(1, int(len(column) * fraction))
        tail = column[-count:]
        mean = sum(tail) / len(tail)
        relative_change = (max(tail) - min(tail)) / abs(mean) if abs(mean) > 1e-10 else float("inf")
        results[name] = {
            "mean": round(mean, 4),
            "min": round(min(tail), 4),
            "max": round(max(tail), 4),
            "rel_change": round(relative_change, 6),
            "ok": relative_change <= max_rel_change,
        }
    return results


def analyze_final_window(xvg_path: Path, window_ps: float) -> dict:
    """Compute block means, trend, and uncertainty for the final EQ window."""
    _, columns = parse_xvg(xvg_path)
    if len(columns) < 2 or not columns[0] or not columns[1]:
        return {"ok": False, "reason": "XVG 中没有可分析的时间序列"}
    times, values = columns[0], columns[1]
    cutoff = times[-1] - float(window_ps)
    selected = [value for time_value, value in zip(times, values) if time_value >= cutoff]
    if len(selected) < 4:
        return {"ok": False, "reason": "最终验收窗口中的采样点不足"}
    block_count = min(4, len(selected))
    base, remainder = divmod(len(selected), block_count)
    block_means: list[float] = []
    position = 0
    for index in range(block_count):
        width = base + (1 if index < remainder else 0)
        block = selected[position:position + width]
        position += width
        block_means.append(sum(block) / len(block))
    mean = sum(selected) / len(selected)
    variance = sum((value - mean) ** 2 for value in selected) / max(1, len(selected) - 1)
    sem = (variance / len(selected)) ** 0.5
    first, last = block_means[0], block_means[-1]
    drift = abs(last - first)
    relative_drift = drift / max(abs(mean), 1e-12)
    first_last_scale = max(sem * (2 ** 0.5), 1e-12)
    trend_zscore = drift / first_last_scale
    return {
        "ok": True,
        "window_ps": float(window_ps),
        "sample_count": len(selected),
        "mean": mean,
        "standard_error": sem,
        "block_means": block_means,
        "relative_drift": relative_drift,
        "trend_zscore": trend_zscore,
    }


def _inputs_dict(inputs: GromacsInputs) -> dict[str, object]:
    return {
        "topol": str(inputs.topol),
        "itps": [str(path) for path in inputs.itps],
        "mdp": str(inputs.mdp),
        "coordinates": str(inputs.coordinates),
        "tpr": str(inputs.tpr),
    }


def _stderr_tail(result: subprocess.CompletedProcess) -> str:
    return (result.stderr or result.stdout or "(no output)")[-1000:]


def _mdrun_failure_evidence(result: subprocess.CompletedProcess, raw_output: str) -> dict[str, object]:
    """Return bounded, private process facts for one failed GROMACS run."""
    returncode = result.returncode
    signal_name = ""
    if isinstance(returncode, int) and returncode < 0:
        try:
            signal_name = signal.Signals(-returncode).name
        except ValueError:
            signal_name = f"SIG{-returncode}"
    return {
        "returncode": returncode,
        "signal": signal_name,
        "raw_output_tail": raw_output[-2000:],
    }


def _gmx_missing_result(stage: str, started_at: float, inputs: GromacsInputs) -> StepResult:
    return StepResult(
        step_name=f"md_{stage}", step_index=stage_step_index(stage), success=False,
        error=StepError(
            kind=ErrorKind.DEPENDENCY_MISSING,
            message="未在当前 PATH 中找到 GROMACS 命令 gmx",
            hint="安装 GROMACS 并确认 `gmx --version` 可在启动 Willy 的同一环境中执行",
        ),
        duration_s=time.time() - started_at,
        extra={"inputs": _inputs_dict(inputs)},
    )


def _lock_conflict_result(
    stage: str,
    started_at: float,
    inputs: GromacsInputs,
    message: str,
) -> StepResult:
    return StepResult(
        step_name=f"md_{stage}", step_index=stage_step_index(stage), success=False,
        error=StepError(
            kind=ErrorKind.LOCK_CONFLICT,
            message=f"{stage}: {message}",
            hint="等待当前 run 的 GROMACS 进程结束，或使用受控停止请求写入 checkpoint 后再恢复",
        ),
        duration_s=time.time() - started_at,
        extra={"inputs": _inputs_dict(inputs)},
    )


def _mdrun_error_kind(raw_output: str) -> ErrorKind:
    lowered = raw_output.lower()
    if any(marker in lowered for marker in ("lincs", "nan", "infinity", "particle moved too far")):
        return ErrorKind.NUMERICAL_INSTABILITY
    return ErrorKind.MDRUN_FAILED


def _grompp_hint(raw_output: str) -> str:
    lowered = raw_output.lower()
    if "atomtype" in lowered or "moleculetype" in lowered:
        return "检查 topol.top 的 include 顺序及 .itp 中 atomtype/moleculetype 定义"
    if "invalid order" in lowered or "unknown left-hand" in lowered:
        return "检查 .mdp 参数名称和当前 GROMACS 版本兼容性"
    if "number of atoms" in lowered:
        return "检查起始结构和 topol.top 的原子数、分子数是否一致"
    return "检查 topol.top、.itp、.mdp 和起始结构的 GROMACS 输入一致性"
