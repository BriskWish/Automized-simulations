"""Versioned step admission, explicit input adoption and old-output archives."""

from __future__ import annotations

from datetime import datetime, timezone
from contextvars import ContextVar
import json
from pathlib import Path
import re
import shutil
from typing import Any, Callable, Mapping
from uuid import uuid4

from willy.run_store import run_transaction
from willy.simulation.manifest import file_fingerprint
from willy.simulation.protocol import canonical_json_fingerprint
from willy.step_registry import STEP_REGISTRY


CONTRACT_FILENAME = "step_contracts.json"
HASH_POLICY = "verified_or_declared_inputs"
_INCLUDE = re.compile(r'^\s*#\s*include\s+"([^"\r\n]+)"', re.MULTILINE)
_ACTIVE_STEP: ContextVar[tuple[str, int] | None] = ContextVar("willy_active_step", default=None)


class StepContractError(ValueError):
    """A step cannot consume an unregistered or changed input."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def local_path(directory: Path, value: str | Path) -> Path:
    candidate = Path(value)
    candidate = candidate if candidate.is_absolute() else directory / candidate
    resolved = candidate.resolve()
    if not resolved.is_relative_to(directory.resolve()):
        raise StepContractError("输入路径必须位于当前工程内")
    return resolved


def fingerprint(directory: Path, value: str | Path) -> dict[str, Any]:
    path = local_path(directory, value)
    if not path.is_file() or path.stat().st_size <= 0:
        raise StepContractError(f"必要文件缺失或为空：{path.relative_to(directory)}")
    before = path.stat()
    result = file_fingerprint(path, directory)
    after = path.stat()
    if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
        raise StepContractError("文件在校验期间发生变化，请停止编辑后重新声明")
    return {key: result[key] for key in ("path", "sha256", "size_bytes")}


def _config(directory: Path) -> dict[str, Any]:
    from willy.workflow_config import validate_config

    try:
        value = json.loads(local_path(directory, "config.json").read_text(encoding="utf-8"))
        if validate_config(value):
            raise StepContractError("工程配置未通过现有契约检查")
        return value
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        raise StepContractError("工程配置不可读取或无效") from exc


def load_contracts(run_dir: str | Path) -> dict[str, Any]:
    directory = Path(run_dir).resolve()
    try:
        payload = json.loads(local_path(directory, CONTRACT_FILENAME).read_text(encoding="utf-8"))
        if (
            payload.get("schema_version") != 1 or payload.get("run_id") != directory.name
            or not isinstance(payload.get("artifacts"), dict)
            or not isinstance(payload.get("steps"), dict)
            or not isinstance(payload.get("adoptions"), dict)
            or not isinstance(payload.get("declaration_history"), list)
        ):
            raise ValueError("invalid contract document")
        for name in ("artifacts", "steps", "adoptions"):
            if any(not isinstance(value, dict) for value in payload[name].values()):
                raise ValueError("invalid contract record")
        return payload
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        raise StepContractError("工程缺少有效步骤哈希基线，请用 /inputs 明确声明当前输入") from exc


def initialize_contracts(run_dir: str | Path) -> dict[str, Any]:
    directory = Path(run_dir).resolve()
    with run_transaction(directory) as store:
        if (directory / CONTRACT_FILENAME).exists():
            return load_contracts(directory)
        config = _config(directory)
        payload = {
            "schema_version": 1, "run_id": directory.name, "created_at": _now(),
            "config_signature": canonical_json_fingerprint(config),
            "artifacts": {}, "steps": {}, "adoptions": {}, "declaration_history": [],
        }
        for path in step_input_paths(directory, 1):
            record = fingerprint(directory, path)
            payload["artifacts"][record["path"]] = {**record, "producer_step": 0, "source": "initial_input", "accepted": True}
        store.write_json(CONTRACT_FILENAME, payload)
        return payload


def _topology_inputs(directory: Path) -> set[Path]:
    pending = [directory / "topol.top", *directory.glob("*.itp")]
    visited: set[Path] = set()
    while pending:
        path = local_path(directory, pending.pop())
        if path in visited:
            continue
        visited.add(path)
        if path.is_file():
            for include in _INCLUDE.findall(path.read_text(encoding="utf-8", errors="replace")):
                pending.append(local_path(directory, path.parent / include))
        if len(visited) > 512:
            raise StepContractError("拓扑引用数量超过准入上限")
    return visited


def step_input_paths(run_dir: str | Path, step: int) -> tuple[Path, ...]:
    STEP_REGISTRY.step(step)
    directory = Path(run_dir).resolve()
    config = _config(directory)
    backend = config.get("backend", "g16")
    names = config["molecules"]
    paths: set[Path] = {directory / "config.json"}
    if step == 1:
        paths.update(directory / f"{name}{'.inp' if backend == 'orca' else '.gjf'}" for name in names)
    elif step == 2:
        paths.update(directory / f"{name}{'.molden' if backend == 'orca' else '.fchk'}" for name in names)
    elif step == 3:
        paths.update(directory / f"{name}{suffix}" for name in names for suffix in ("_opt.fchk", ".mol2"))
    elif step == 4:
        from willy.topology.backends import TopologyPlan

        for component in TopologyPlan.from_config(config, directory).components:
            paths.add(component.mol2)
            if component.chg is not None:
                paths.add(component.chg)
                metadata = directory / f"{component.molecule_id}.charge_scaling.json"
                if (config.get("ion_charge_scale", 1.0) != 1.0 or metadata.exists()) and not declared_input_matches(directory, 4, component.chg):
                    paths.update((metadata, directory / f"{component.molecule_id}.resp.chg", directory / f"{component.molecule_id}_opt.fchk"))
    elif step == 5:
        from willy.topology.manifest import load_manifest

        try:
            components = {item["residue_name"]: item for item in load_manifest(directory)["components"]}
            for name in config["residues"]:
                component = components[name]
                if not component.get("success") or not component.get("validated"):
                    raise StepContractError("拓扑组件未通过上游验收")
                paths.update(local_path(directory, component[key]) for key in ("itp", "gro"))
        except (KeyError, TypeError, AttributeError) as exc:
            raise StepContractError("拓扑组件登记不完整") from exc
    elif step >= 7:
        paths.update(_topology_inputs(directory))
        if step == 7:
            for name in config["residues"]:
                pdb = directory / f"{name}.pdb"
                paths.add(pdb if pdb.exists() else directory / f"{name}.gro")
        else:
            stage = STEP_REGISTRY.stage_for(step)
            paths.add(directory / f"{stage}.mdp")
            paths.add(directory / {8: "model.pdb", 9: "em.gro", 10: "eq.gro"}[step])
            if step == 10:
                paths.add(directory / "eq.cpt")
    return tuple(sorted(local_path(directory, path) for path in paths))


def step_output_paths(run_dir: str | Path, step: int) -> tuple[Path, ...]:
    directory = Path(run_dir).resolve()
    config = _config(directory)
    names = config["molecules"]
    files: set[str] = set()
    if step == 1:
        suffix = ".molden" if config.get("backend") == "orca" else ".fchk"
        files.update(f"{name}{suffix}" for name in names)
    elif step == 2:
        suffixes = ("_opt.fchk", "_opt.molden", ".mol2") if config.get("backend") == "orca" else ("_opt.fchk", ".mol2")
        files.update(f"{name}{suffix}" for name in names for suffix in suffixes)
    elif step == 3:
        files.update(f"{name}.chg" for name in names)
        for name in names:
            for suffix in (".resp.chg", ".charge_scaling.json"):
                if "ion_charge_scale" in config or (directory / f"{name}{suffix}").exists():
                    files.add(f"{name}{suffix}")
    elif step == 4:
        files.update(f"{name}.{suffix}" for name in config["residues"] for suffix in ("itp", "gro"))
    elif step == 5:
        files.add("topol.top")
        files.update(f".assembly_itp/{name}.itp" for name in config["residues"])
    elif step == 6:
        files.update(f"{stage}.mdp" for stage in ("em", "eq", "prod"))
    elif step == 7:
        files.add("model.pdb")
    else:
        stage = STEP_REGISTRY.stage_for(step)
        files.update(f"{stage}.{suffix}" for suffix in ("tpr", "gro", "xtc", "edr"))
        if step >= 9:
            files.add(f"{stage}.cpt")
    return tuple(sorted(directory / name for name in files))


def _matches(observed: Mapping[str, Any], expected: Mapping[str, Any]) -> bool:
    return all(observed.get(key) == expected.get(key) for key in ("sha256", "size_bytes"))


def declared_input_matches(run_dir: str | Path, step: int, value: str | Path) -> bool:
    directory = Path(run_dir).resolve()
    try:
        observed = fingerprint(directory, value)
        adoption = load_contracts(directory)["adoptions"].get(observed["path"], {})
        return step in adoption.get("steps", []) and _matches(observed, adoption)
    except (StepContractError, OSError, TypeError):
        return False


def validate_step_inputs(run_dir: str | Path, step: int, *, input_paths: tuple[Path, ...] | None = None) -> dict[str, dict[str, Any]]:
    directory = Path(run_dir).resolve()
    payload = load_contracts(directory)
    if canonical_json_fingerprint(_config(directory)) != payload.get("config_signature"):
        raise StepContractError("参数与原工程稳定配置不一致；请使用 /fork 创建调参分支")
    observations = {}
    for path in step_input_paths(directory, step) if input_paths is None else input_paths:
        observed = fingerprint(directory, path)
        key = observed["path"]
        expected = payload["artifacts"].get(key, {})
        adoption = payload["adoptions"].get(key, {})
        adopted = step in adoption.get("steps", []) and _matches(observed, adoption)
        trusted = expected.get("accepted") is True and _matches(observed, expected)
        if not adopted and not trusted:
            raise StepContractError(f"第 {step} 步输入缺少可信基线或哈希变化：{key}；请先声明新输入")
        observations[key] = observed
    if step == 4:
        config = _config(directory)
        if config.get("topology", {}).get("backend", "sobtop") == "sobtop":
            from willy.quantum.charge_files import parse_charge_text, validate_charge_record

            for name, molecule in config["molecules"].items():
                if declared_input_matches(directory, 4, f"{name}.chg"):
                    parse_charge_text((directory / f"{name}.chg").read_text(encoding="utf-8"))
                elif config.get("ion_charge_scale", 1.0) != 1.0 or (directory / f"{name}.charge_scaling.json").exists():
                    validate_charge_record(directory, name, molecule["charge"], molecule["spin"], config.get("ion_charge_scale", 1.0))
    return observations


def _append_compatible(directory: Path, step: int) -> bool:
    if step != 10 or not (directory / "prod.cpt").is_file():
        return False
    from willy.simulation._gmx_utils import build_stage_inputs
    from willy.simulation.manifest import stage_can_resume, stage_contract

    inputs = build_stage_inputs(directory, "prod")
    contract = stage_contract(
        directory, "prod", config_path=directory / "config.json", topol=inputs.topol,
        itps=inputs.itps, mdp=inputs.mdp, coordinates=inputs.coordinates,
        parent_checkpoint=directory / "eq.cpt",
    )
    compatible = stage_can_resume(directory, "prod", contract)
    if compatible:
        from willy.simulation.manifest import load_manifest

        record = load_manifest(directory)["stages"]["prod"]
        recorded = {entry["path"]: entry for entry in record.get("outputs", {}).values()}
        suffixes = ["cpt", "tpr", "xtc", "edr", "log"]
        if _config(directory).get("md", {}).get("outputs", {}).get("trr", False):
            suffixes.append("trr")
        for suffix in suffixes:
            name = f"prod.{suffix}"
            observed = fingerprint(directory, name)
            if not _matches(observed, recorded.get(name, {})) and not declared_input_matches(directory, 10, name):
                raise StepContractError(f"PROD 续接文件哈希缺少中断基线或变化：{name}；请重新声明或归档后重跑")
    return compatible


def recover_archives(run_dir: str | Path) -> None:
    directory = Path(run_dir).resolve()
    if (directory / "old").is_symlink():
        raise StepContractError("old/ 必须是当前工程内的独立目录")
    from willy.config_store import write_json

    for journal in sorted((directory / "old").glob("*/archive.json")):
        payload = json.loads(local_path(directory, journal).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise StepContractError("归档记录损坏，禁止开始工作区作业")
        if payload.get("state") == "complete":
            continue
        if payload.get("state") != "moving" or not isinstance(payload.get("files"), list):
            raise StepContractError("归档记录损坏，禁止开始工作区作业")
        for value in payload["files"]:
            if not isinstance(value, str) or Path(value).is_absolute():
                raise StepContractError("归档路径无效")
            source = local_path(directory, value)
            if source.name in {"config.json", CONTRACT_FILENAME} or source.is_relative_to(directory / "old"):
                raise StepContractError("归档记录不能移动活动配置或归档目录")
            target = local_path(directory, journal.parent / "files" / value)
            if not target.is_relative_to(journal.parent / "files"):
                raise StepContractError("归档路径无效")
            if source.exists() == target.exists():
                raise StepContractError("归档中断且文件状态有冲突，请先核对 old/ 与工作区")
            if source.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                source.replace(target)
        payload["state"] = "complete"
        write_json(journal, payload)


def archive_files(run_dir: str | Path, candidates: set[Path], attempt_id: str, *, step: int) -> list[str]:
    directory = Path(run_dir).resolve()
    recover_archives(directory)
    if any(path.absolute() != local_path(directory, path) for path in candidates):
        raise StepContractError("待归档产物必须是独立文件，不能通过符号链接覆盖输入")
    candidates = {local_path(directory, path) for path in candidates if path.exists() or path.is_symlink()}
    if any(path.name in {"config.json", CONTRACT_FILENAME} for path in candidates):
        raise StepContractError("禁止移动活动配置或准入记录")
    if not candidates:
        return []
    archive = local_path(directory, directory / "old" / attempt_id)
    archive.mkdir(parents=True, exist_ok=False)
    for name in ("config.json", CONTRACT_FILENAME, "run_manifest.json", "md_manifest.json", "topology_manifest.json", "status.json"):
        source = directory / name
        if source.is_file():
            context = archive / "context"
            context.mkdir(exist_ok=True)
            shutil.copy2(local_path(directory, source), context / name)
    records = [str(path.relative_to(directory)) for path in sorted(candidates)]
    from willy.config_store import write_json

    write_json(archive / "archive.json", {"state": "moving", "step": step, "files": records})
    for value in records:
        source = directory / value
        target = archive / "files" / value
        target.parent.mkdir(parents=True, exist_ok=True)
        source.replace(target)
    write_json(archive / "archive.json", {"state": "complete", "step": step, "files": records})
    return records


def archive_step_outputs(run_dir: str | Path, step: int, attempt_id: str, *, append: bool = False) -> list[str]:
    directory = Path(run_dir).resolve()
    candidates: set[Path] = set()
    for current in range(step, 11):
        if append and current == 10:
            continue
        candidates.update(step_output_paths(directory, current))
        stage = STEP_REGISTRY.stage_for(current)
        if stage:
            candidates.update(directory / f"{stage}.{suffix}" for suffix in ("log", "cpt", "trr"))
            candidates.add(directory / f"{stage}_out.mdp")
            candidates.add(directory / "visualization" / f"{stage}.pdb")
    if step <= 9:
        candidates.update(directory / name for name in ("density.xvg", "temp.xvg", "pressure.xvg", "potential.xvg"))
    if step <= 7:
        candidates.add(directory / "model.inp")
    config = _config(directory)
    if step <= 3:
        candidates.update(directory / f"{name}{suffix}" for name in config["molecules"] for suffix in (".resp.chg", ".charge_scaling.json"))
    if step <= 2:
        suffixes = ("_opt.chk", "_opt.log", "_opt.out", "_opt.gbw")
        if step == 1:
            suffixes += (".chk", ".log", ".out", ".gbw")
        candidates.update(directory / f"{name}{suffix}" for name in config["molecules"] for suffix in suffixes)
    if step <= 4:
        candidates.update(directory / f"{name}.pdb" for name in config["residues"])
    return archive_files(directory, candidates, attempt_id, step=step)


def begin_step(run_dir: str | Path, step: int) -> str:
    directory = Path(run_dir).resolve()
    input_paths = step_input_paths(directory, step)
    validate_step_inputs(directory, step, input_paths=input_paths)
    append = _append_compatible(directory, step)
    with run_transaction(directory) as store:
        recover_archives(directory)
        observations = validate_step_inputs(directory, step, input_paths=input_paths)
        payload = load_contracts(directory)
        old = payload["steps"].get(str(step), {})
        history = list(old.get("history", []))
        if old:
            history.append({key: value for key, value in old.items() if key != "history"})
        attempt_id = f"step-{step}-{uuid4().hex}"
        archived = archive_step_outputs(directory, step, attempt_id, append=append)
        if observations != validate_step_inputs(directory, step, input_paths=input_paths):
            raise StepContractError("输入在归档期间变化，请重新声明")
        for name in archived:
            payload["artifacts"].pop(name, None)
            payload["adoptions"].pop(name, None)
        for key, entry in payload["artifacts"].items():
            if entry.get("producer_step", 0) >= step:
                entry["accepted"] = False
        payload["steps"][str(step)] = {
            "status": "running", "attempt_id": attempt_id, "started_at": _now(),
            "inputs": observations, "append": append, "history": history,
        }
        store.write_json(CONTRACT_FILENAME, payload)
        return attempt_id


def step_is_active(run_dir: str | Path, step: int) -> bool:
    return _ACTIVE_STEP.get() == (str(Path(run_dir).resolve()), step)


def finish_step(run_dir: str | Path, step: int, attempt_id: str, result: object, *, recovered: bool = False) -> None:
    directory = Path(run_dir).resolve()
    results = result if isinstance(result, list) else [result]
    success = bool(results) and all(getattr(item, "success", False) for item in results)
    with run_transaction(directory) as store:
        payload = load_contracts(directory)
        record = payload["steps"].get(str(step), {})
        allowed = {"running", "failed"} if recovered else {"running"}
        if record.get("attempt_id") != attempt_id or record.get("status") not in allowed:
            raise StepContractError("步骤执行编号已变化，禁止登记旧调用结果")
        required = step_output_paths(directory, step)
        if success:
            for path in required:
                fingerprint(directory, path)
            if step == 3:
                from willy.quantum.charge_files import validate_charge_record

                config = _config(directory)
                for name, molecule in config["molecules"].items():
                    if "ion_charge_scale" in config or (directory / f"{name}.charge_scaling.json").exists():
                        validate_charge_record(directory, name, molecule["charge"], molecule["spin"], config.get("ion_charge_scale", 1.0))
        paths = set(required)
        generated_coordinates: set[Path] = set()
        if step == 7:
            generated_coordinates = {
                directory / f"{name}.pdb" for name in _config(directory)["residues"]
                if f"{name}.pdb" not in record.get("inputs", {})
            }
            paths.update(generated_coordinates)
        for item in results:
            for path in getattr(item, "outputs", {}).values():
                if isinstance(path, str):
                    paths.add(local_path(directory, path))
        outputs = {}
        for path in paths:
            if path.is_file() and path.stat().st_size > 0:
                observed = fingerprint(directory, path)
                outputs[observed["path"]] = observed
                payload["artifacts"][observed["path"]] = {
                    **observed, "producer_step": 4 if path in generated_coordinates else step,
                    "attempt_id": attempt_id,
                    "source": "derived_component_coordinate" if path in generated_coordinates else "willy_step",
                    "accepted": success,
                }
                payload["adoptions"].pop(observed["path"], None)
        if step == 1:
            for path in step_input_paths(directory, 1):
                if path.name != "config.json":
                    observed = fingerprint(directory, path)
                    payload["artifacts"][observed["path"]] = {**observed, "producer_step": 0, "source": "prepared_quantum_input", "accepted": True}
        record.update(status="accepted" if success else "failed", outputs=outputs, completed_at=_now())
        store.write_json(CONTRACT_FILENAME, payload)


def accept_recovered_step(run_dir: str | Path, step: int) -> None:
    from willy.errors import StepResult

    directory = Path(run_dir).resolve()
    if not (directory / CONTRACT_FILENAME).exists():
        return
    payload = load_contracts(directory)
    record = payload["steps"].get(str(step), {})
    if record.get("status") == "accepted":
        return
    validate_step_inputs(directory, step)
    finish_step(directory, step, record.get("attempt_id", ""), StepResult(
        STEP_REGISTRY.step(step).step_id, step, True,
    ), recovered=True)


def execute_step(run_dir: str | Path, step: int, function: Callable[[], object]) -> object:
    from willy.errors import ErrorKind, StepError, StepResult

    attempt_id = ""
    token = _ACTIVE_STEP.set((str(Path(run_dir).resolve()), step))
    try:
        attempt_id = begin_step(run_dir, step)
        result = function()
        finish_step(run_dir, step, attempt_id, result)
        return result
    except (StepContractError, OSError, ValueError) as exc:
        failure = StepResult(
            STEP_REGISTRY.step(step).step_id, step, False,
            error=StepError(ErrorKind.INPUT_CONTRACT, str(exc), hint="核对当前输入，或明确声明新输入；调参请创建分支"),
        )
        if attempt_id:
            try:
                finish_step(run_dir, step, attempt_id, failure)
            except (OSError, ValueError):
                pass
        return failure
    finally:
        _ACTIVE_STEP.reset(token)
