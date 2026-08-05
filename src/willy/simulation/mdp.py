"""Versioned GROMACS MDP generation for EM, NPT EQ, and PROD."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Collection
import json

from willy.errors import ErrorKind, StepError, StepResult
from willy.simulation.protocol import (
    EQ_SEGMENT_NAMES,
    MDConfigError,
    eq_annealing_points,
    eq_total_ns,
    ns_to_nsteps,
    require_valid_md_config,
)
from willy.step_registry import MDP_STEP


@dataclass(frozen=True)
class MdpConfig:
    """Validated v2 protocol plus a run-specific random seed."""

    values: dict[str, Any]
    run_seed: int
    warnings: tuple[str, ...]

    @property
    def dt(self) -> float:
        return float(self.values["dt"])


def _lines(*lines: str) -> str:
    return chr(10).join(lines) + chr(10)


def load_mdp_config(config_path: str = "config.json") -> MdpConfig:
    """Read only the v2 protocol; legacy duration fields are rejected."""
    with open(config_path) as handle:
        data = json.load(handle)
    normalized, warnings = require_valid_md_config(data.get("md", {}))
    raw_seed = normalized.get("run_seed", normalized.get("seed", 1))
    try:
        run_seed = int(raw_seed)
    except (TypeError, ValueError) as exc:
        raise MDConfigError("md.run_seed 必须是整数") from exc
    if run_seed <= 0:
        raise MDConfigError("md.run_seed 必须为正整数")
    return MdpConfig(normalized, run_seed, warnings)


def _common_preamble(cfg: MdpConfig) -> str:
    md = cfg.values
    return _lines(
        "pbc = xyz",
        "cutoff-scheme = Verlet",
        f"coulombtype   = {md['coulombtype']}",
        f"rcoulomb      = {md['rcoulomb']}",
        f"vdwtype       = {md['vdwtype']}",
        f"rvdw          = {md['rvdw']}",
        f"DispCorr      = {md['dispersion_correction']}",
    ).rstrip()


def _trajectory_output_controls(cfg: MdpConfig) -> str:
    trr_interval = 1000 if cfg.values["outputs"].get("trr", False) else 0
    return _lines(
        f"nstxout   = {trr_interval}",
        f"nstvout   = {trr_interval}",
        f"nstfout   = {trr_interval}",
        "nstxout-compressed = 1000",
        "compressed-x-grps  = system",
        "nstlog = 500",
        "nstenergy = 500",
        "nstcheckpoint = 1000",
    ).rstrip()


def _coupling_controls(
    cfg: MdpConfig,
    *,
    tau_p: float,
    temperature: float,
    gen_vel: bool,
) -> str:
    md = cfg.values
    return _lines(
        f"Tcoupl  = {md['tcoupl']}",
        f"tau_t   = {md['tau_t']}",
        "tc_grps = system",
        f"ref_t   = {temperature}",
        ";",
        f"Pcoupl     = {md['pcoupl']}",
        f"pcoupltype = {md['pcoupltype']}",
        f"tau_p = {tau_p}",
        f"ref_p = {md['ref_p']}",
        f"compressibility = {md['compressibility']}",
        ";",
        f"gen_vel  = {'yes' if gen_vel else 'no'}",
        f"gen_temp = {temperature}",
        f"gen_seed = {cfg.run_seed}",
        ";",
        f"constraints = {md['constraints']}",
        f"lincs_iter = {md['lincs_iter']}",
        f"lincs_order = {md['lincs_order']}",
    ).rstrip()


def _build_em(cfg: MdpConfig) -> tuple[str, dict[str, Any]]:
    md = cfg.values
    content = _lines(
        "; Willy MD schema v2 | stage=em",
        "integrator = cg",
        f"nsteps = {md['nsteps']}",
        f"emtol  = {md['emtol']}",
        f"emstep = {md['emstep']}",
        ";",
        _trajectory_output_controls(cfg),
        ";",
        _common_preamble(cfg),
        ";",
        "constraints = none",
    )
    return content, {"nsteps": int(md["nsteps"]), "actual_ns": 0.0}


def _format_points(points: list[float]) -> str:
    return " ".join(f"{value:.9g}" for value in points)


def _build_eq(cfg: MdpConfig) -> tuple[str, dict[str, Any]]:
    md = cfg.values
    eq = md["eq"]
    times_ps, temperatures, actual_segments = eq_annealing_points(md)
    nsteps, actual_total_ns = ns_to_nsteps(eq_total_ns(md), cfg.dt)
    requested_total_ns = eq_total_ns(md)
    content = _lines(
        "; Willy MD schema v2 | stage=eq",
        f"; requested_total_ns = {requested_total_ns:.9g}",
        f"; actual_total_ns = {actual_total_ns:.9g}",
        "integrator = md",
        f"dt = {cfg.dt}",
        f"nsteps = {nsteps}",
        "comm-grps = system",
        "energygrps =",
        ";",
        _trajectory_output_controls(cfg),
        ";",
        "annealing = single",
        "annealing_npoints = 7",
        f"annealing_time = {_format_points(times_ps)}",
        f"annealing_temp = {_format_points(temperatures)}",
        ";",
        _common_preamble(cfg),
        ";",
        _coupling_controls(
            cfg,
            tau_p=float(eq["tau_p"]),
            temperature=float(eq["target_temperature"]),
            gen_vel=True,
        ),
    )
    return content, {
        "requested_ns": requested_total_ns,
        "actual_ns": actual_total_ns,
        "nsteps": nsteps,
        "annealing_time_ps": times_ps,
        "annealing_temperature_k": temperatures,
        "segments": {
            name: {
                "requested_ns": float(eq["segments_ns"][name]),
                "actual_ns": actual_segments[name],
            }
            for name in EQ_SEGMENT_NAMES
        },
        "acceptance_window_ns": float(eq["acceptance"]["window_ns"]),
    }


def _build_prod(cfg: MdpConfig) -> tuple[str, dict[str, Any]]:
    md = cfg.values
    prod = md["prod"]
    nsteps, actual_ns = ns_to_nsteps(float(prod["duration_ns"]), cfg.dt)
    content = _lines(
        "; Willy MD schema v2 | stage=prod",
        f"; requested_total_ns = {float(prod['duration_ns']):.9g}",
        f"; actual_total_ns = {actual_ns:.9g}",
        "integrator = md",
        f"dt = {cfg.dt}",
        f"nsteps = {nsteps}",
        "comm-grps = system",
        "energygrps =",
        ";",
        _trajectory_output_controls(cfg),
        ";",
        _common_preamble(cfg),
        ";",
        _coupling_controls(
            cfg,
            tau_p=float(prod["tau_p"]),
            temperature=float(prod["temperature"]),
            gen_vel=False,
        ),
    )
    return content, {
        "requested_ns": float(prod["duration_ns"]),
        "actual_ns": actual_ns,
        "nsteps": nsteps,
        "temperature": float(prod["temperature"]),
    }


_BUILDERS = {
    "em": ("em.mdp", _build_em),
    "eq": ("eq.mdp", _build_eq),
    "prod": ("prod.mdp", _build_prod),
}


def _apply_overrides(values: dict[str, Any], overrides: dict[str, Any] | None) -> dict[str, Any]:
    """Apply only explicit v2 paths; legacy aliases are deliberately rejected."""
    if not overrides:
        return values
    result = json.loads(json.dumps(values))
    aliases = {
        "eq_target_temperature": ("eq", "target_temperature"),
        "prod_duration_ns": ("prod", "duration_ns"),
        "prod_temperature": ("prod", "temperature"),
        "eq_tau_p": ("eq", "tau_p"),
        "prod_tau_p": ("prod", "tau_p"),
    }
    for key, value in overrides.items():
        if key in {"eq_ns", "prod_ns", "ref_t", "tau_p_prod", "tau_p"}:
            raise MDConfigError(f"{key} 是旧 MD 参数；请使用 v2 分段字段")
        if key in aliases:
            section, field = aliases[key]
            result[section][field] = value
        elif key == "eq_segments_ns":
            if not isinstance(value, dict):
                raise MDConfigError("eq_segments_ns 必须是六段对象")
            result["eq"]["segments_ns"].update(value)
        elif key == "eq_acceptance":
            if not isinstance(value, dict):
                raise MDConfigError("eq_acceptance 必须是对象")
            result["eq"]["acceptance"].update(value)
        elif key in result:
            result[key] = value
        else:
            raise MDConfigError(f"不支持的 MDP 覆盖字段: {key}")
    return result


def build_all(
    config_path: str = "config.json",
    output_dir: str = "process",
    overrides: dict | None = None,
    stages: Collection[str] | None = None,
    on_progress=None,
) -> StepResult:
    """Build MDPs and record rounded physical durations in mdp_metadata.json."""
    import time as _time

    started_at = _time.time()
    try:
        loaded = load_mdp_config(config_path)
        values = _apply_overrides(loaded.values, overrides)
        values, override_warnings = require_valid_md_config(values)
        cfg = MdpConfig(values, loaded.run_seed, tuple((*loaded.warnings, *override_warnings)))
    except (FileNotFoundError, json.JSONDecodeError, MDConfigError) as exc:
        return StepResult(
            step_name="mdp", step_index=MDP_STEP, success=False,
            error=StepError(
                kind=ErrorKind.INPUT_CONTRACT,
                message=f"MD 协议配置无效: {exc}",
                hint="使用 v2 md.eq/md.prod 配置；旧 eq_ns/prod_ns 必须先显式迁移",
            ),
            duration_s=_time.time() - started_at,
        )

    selected = tuple(_BUILDERS) if stages is None else tuple(stages)
    unknown = set(selected) - _BUILDERS.keys()
    if not selected or unknown:
        invalid = ", ".join(sorted(unknown)) or "空阶段列表"
        return StepResult(
            step_name="mdp", step_index=MDP_STEP, success=False,
            error=StepError(ErrorKind.INPUT_CONTRACT, f"未知 MDP 阶段: {invalid}"),
            duration_s=_time.time() - started_at,
        )

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    outputs: dict[str, str] = {}
    artifacts: list[str] = []
    metadata = {
        "schema_version": 1,
        "md_schema_version": values["schema_version"],
        "run_seed": cfg.run_seed,
        "protocol_controls": {
            "tcoupl": values["tcoupl"],
            "pcoupl": values["pcoupl"],
            "pcoupltype": values["pcoupltype"],
            "compressibility": values["compressibility"],
            "rcoulomb": values["rcoulomb"],
            "rvdw": values["rvdw"],
            "dispersion_correction": values["dispersion_correction"],
        },
        "stages": {},
        "warnings": list(cfg.warnings),
    }
    for current, stage in enumerate(selected, 1):
        if on_progress:
            on_progress({
                "tool": "MDP", "operation": "参数生成",
                "target_type": "stage", "target": stage,
                "current": current, "total": len(selected),
            })
        filename, builder = _BUILDERS[stage]
        content, stage_metadata = builder(cfg)
        path = out / filename
        path.write_text(content.lstrip(chr(10)))
        outputs[stage] = str(path)
        artifacts.append(str(path))
        metadata["stages"][stage] = stage_metadata

    metadata_path = out / "mdp_metadata.json"
    previous: dict[str, Any] = {}
    if metadata_path.exists():
        try:
            previous = json.loads(metadata_path.read_text())
        except json.JSONDecodeError:
            previous = {}
    prior_stages = previous.get("stages", {})
    if isinstance(prior_stages, dict):
        prior_stages.update(metadata["stages"])
        metadata["stages"] = prior_stages
    previous.update(metadata)
    metadata_path.write_text(json.dumps(previous, ensure_ascii=False, indent=2) + chr(10))
    outputs["metadata"] = str(metadata_path)
    artifacts.append(str(metadata_path))
    return StepResult(
        step_name="mdp", step_index=MDP_STEP, success=True,
        outputs=outputs,
        artifacts=artifacts,
        duration_s=_time.time() - started_at,
        extra={"warnings": list(cfg.warnings), "mdp_metadata": metadata},
    )


if __name__ == "__main__":
    build_all("config.json", "process")
