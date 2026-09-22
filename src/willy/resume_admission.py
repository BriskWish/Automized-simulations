"""Read-only admission of existing runs against stable step-input baselines."""

from __future__ import annotations

from dataclasses import dataclass
from contextlib import nullcontext
from pathlib import Path
from typing import Mapping
import json
import re

from willy.charge_scaling import validate_ion_charge_scale
from willy.run_control import RunControlError, safe_restart_step
from willy.simulation.manifest import ManifestError, existing_run_lock, RunLockError, load_manifest
from willy.step_registry import EM_STEP, EQ_STEP, MDP_STEP, PACKMOL_STEP, PROD_STEP
from willy.topology.validation import validate_topology_output_name
from willy.workflow_config import validate_config
from willy.step_contracts import HASH_POLICY, StepContractError, validate_step_inputs


RESUME_HASH_POLICY = HASH_POLICY
_TOPOL_INCLUDE = re.compile(r'^\s*#\s*include\s+"([^"\r\n]+)"', re.MULTILINE)


class ResumeAdmissionError(RunControlError):
    """A bounded public reason why a same-run replay cannot start."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ResumeAdmission:
    restart_step: int
    checked_files: int

    def public_summary(self) -> dict[str, object]:
        return {
            "ok": True,
            "restart_step": self.restart_step,
            "checked_files": self.checked_files,
            "hash_policy": RESUME_HASH_POLICY,
        }


def validate_resume_admission(
    run_dir: str | Path,
    *,
    backend: str,
    status: Mapping[str, object],
    restart_step: int,
    lock_held: bool = False,
) -> ResumeAdmission:
    """Check reused inputs, explicit hash declarations and upstream permissions."""
    directory = Path(run_dir).resolve()
    if not directory.is_dir():
        raise ResumeAdmissionError("run_missing", "当前运行目录不可用")
    if isinstance(restart_step, bool) or not isinstance(restart_step, int) or not 1 <= restart_step <= PROD_STEP:
        raise ResumeAdmissionError("invalid_progress", "续跑起点必须是有效步骤")
    done = status.get("done_steps")
    if (
        not isinstance(done, list)
        or any(isinstance(step, bool) or not isinstance(step, int) for step in done)
        or len(done) != len(set(done))
        or sorted(done) != list(range(1, restart_step))
        or safe_restart_step(status) != restart_step
    ):
        raise ResumeAdmissionError("invalid_progress", "完成步骤必须是连续前缀，续跑起点必须是首个未完成步骤")
    try:
        with nullcontext() if lock_held else existing_run_lock(directory):
            admission = _validate_inputs(directory, backend, restart_step)
            validate_step_inputs(directory, restart_step)
            return admission
    except RunLockError as exc:
        raise ResumeAdmissionError("run_busy", "当前运行仍有受管模拟进程占用") from exc
    except StepContractError as exc:
        raise ResumeAdmissionError("input_contract", str(exc)) from exc


def _validate_inputs(directory: Path, backend: str, restart_step: int) -> ResumeAdmission:
    checked: set[Path] = set()

    def require(value: str | Path, label: str) -> Path:
        candidate = Path(value)
        candidate = candidate if candidate.is_absolute() else directory / candidate
        try:
            resolved = candidate.resolve()
            if not resolved.is_relative_to(directory):
                raise ResumeAdmissionError("unsafe_path", f"{label}必须位于当前运行目录内")
            if not resolved.is_file():
                raise ResumeAdmissionError("input_missing", f"{label}缺失或不是普通文件")
            with resolved.open("rb") as handle:
                if not handle.read(1):
                    raise ResumeAdmissionError("input_empty", f"{label}为空，不能用于续跑")
        except (OSError, RuntimeError) as exc:
            raise ResumeAdmissionError("input_unreadable", f"{label}不可读取") from exc
        checked.add(resolved)
        return resolved

    def optional(value: str | Path, label: str) -> None:
        path = directory / value
        if path.exists() or path.is_symlink():
            require(path, label)

    config_path = require("config.json", "运行配置")
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
        if validate_config(config):
            raise ResumeAdmissionError("invalid_config", "运行配置未通过现有配置契约校验")
        validate_ion_charge_scale(config.get("ion_charge_scale", 1.0))
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        if isinstance(exc, ResumeAdmissionError):
            raise
        raise ResumeAdmissionError("invalid_config", "运行配置不是可读取的有效 JSON 配置") from exc
    if backend not in {"g16", "g09", "orca"} or config.get("backend", "g16") != backend:
        raise ResumeAdmissionError("backend_mismatch", "运行配置后端与已登记运行后端不一致")
    molecules = config["molecules"]
    residues = config["residues"]
    try:
        for name in {*molecules, *residues}:
            validate_topology_output_name(name)
    except ValueError as exc:
        raise ResumeAdmissionError("invalid_config", "配置中的组分标识不适用于运行文件") from exc

    if restart_step <= 3:
        initial_suffix = ".molden" if backend == "orca" else ".fchk"
        for name in molecules:
            if restart_step == 1:
                require(f"{name}{'.inp' if backend == 'orca' else '.gjf'}", "量子原始输入")
            if restart_step == 2:
                require(f"{name}{initial_suffix}", "量子优化产物")
            if restart_step == 3:
                require(f"{name}_opt.fchk", "RESP 波函数输入")
            if restart_step == 3:
                require(f"{name}.mol2", "分子 MOL2 输入")

    if restart_step == 4:
        from willy.topology.backends import TopologyPlan

        try:
            plan = TopologyPlan.from_config(config, directory)
        except (ValueError, TypeError) as exc:
            raise ResumeAdmissionError("invalid_config", "拓扑组分配置未通过现有契约校验") from exc
        for component in plan.components:
            require(component.mol2, "分子 MOL2 输入")
            if component.chg is not None:
                require(component.chg, "RESP 电荷输入")

    if restart_step == 5:
        from willy.topology.manifest import load_manifest as load_topology_manifest

        try:
            topology = load_topology_manifest(directory)
            components = {item["residue_name"]: item for item in topology["components"]}
            for name in residues:
                component = components[name]
                if component.get("success") is not True or component.get("validated") is not True:
                    raise ResumeAdmissionError("upstream_unaccepted", "拓扑组件尚未通过上游验收")
                for kind in ("itp", "gro"):
                    require(component[kind], "已验收拓扑组件产物")
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            if isinstance(exc, ResumeAdmissionError):
                raise
            raise ResumeAdmissionError("manifest_invalid", "拓扑验收登记不完整或不可读取") from exc

    if restart_step >= MDP_STEP:
        topol = require("topol.top", "主拓扑输入")
        try:
            includes = _TOPOL_INCLUDE.findall(topol.read_text(encoding="utf-8", errors="replace"))
        except OSError as exc:
            raise ResumeAdmissionError("input_unreadable", "主拓扑输入不可读取") from exc
        for include in includes:
            require(include, "主拓扑直接引用的组装输入")
        itps = list(directory.glob("*.itp"))
        if not itps:
            raise ResumeAdmissionError("input_missing", "缺少可复用的 ITP 输入")
        for path in itps:
            require(path, "ITP 输入")
        if restart_step <= PACKMOL_STEP:
            for name in residues:
                require(f"{name}.itp", "建盒组分拓扑")
                pdb = directory / f"{name}.pdb"
                require(pdb if pdb.exists() or pdb.is_symlink() else f"{name}.gro", "建盒组分坐标")
        if restart_step > MDP_STEP:
            for stage, step in (("em", EM_STEP), ("eq", EQ_STEP), ("prod", PROD_STEP)):
                if step >= restart_step:
                    require(f"{stage}.mdp", "待执行阶段 MDP 输入")
        if restart_step == EM_STEP:
            require("model.pdb", "初始盒坐标")

    if restart_step in {EQ_STEP, PROD_STEP}:
        parent = "em" if restart_step == EQ_STEP else "eq"
        try:
            record = load_manifest(directory)["stages"].get(parent, {})
            if record.get("status") != "accepted":
                raise ResumeAdmissionError("upstream_unaccepted", f"{parent.upper()} 尚未取得上游验收许可")
        except (ManifestError, OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            if isinstance(exc, ResumeAdmissionError):
                raise
            raise ResumeAdmissionError("manifest_invalid", "MD 上游验收登记不完整或不可读取") from exc
        suffixes = ("tpr", "gro", "xtc", "edr", "cpt") if parent == "eq" else ("tpr", "gro", "xtc", "edr")
        for suffix in suffixes:
            require(f"{parent}.{suffix}", f"{parent.upper()} 必需产物")
    if restart_step == PROD_STEP:
        checkpoint = directory / "prod.cpt"
        if checkpoint.exists() or checkpoint.is_symlink():
            for suffix in ("cpt", "tpr", "xtc", "edr", "log"):
                require(f"prod.{suffix}", "PROD checkpoint 续接文件")
            if config.get("md", {}).get("outputs", {}).get("trr", False):
                require("prod.trr", "PROD TRR 续接轨迹")

    return ResumeAdmission(restart_step, len(checked))
