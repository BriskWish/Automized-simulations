"""Deterministic post-processing for completed production trajectories.

This module consumes only artifacts in one run workspace.  It never changes
the source trajectory and writes one auditable analysis result under
``analysis/<analysis_id>/``.  The first profile covers trajectory integrity,
PBC processing, equilibration trimming, thermodynamic statistics, and a
group-level mean-square displacement (MSD).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
import hashlib
import json
import math
import os
import re
import subprocess
import tempfile
import time

from willy.errors import ErrorKind, StepError, StepResult
from willy.simulation._gmx_utils import parse_xvg, run_gmx
from willy.simulation.manifest import ManifestError, file_fingerprint, load_manifest


POSTPROCESS_STEP_INDEX = 11
_ANALYSIS_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_LAST_FRAME_RE = re.compile(
    r"Last frame\s+\d+\s+time\s+([-+0-9.eE]+)", re.IGNORECASE,
)
_FRAME_COUNT_RE = re.compile(r"There (?:were|was)\s+(\d+)\s+frame", re.IGNORECASE)
_MDP_VALUE_RE = re.compile(r"^\s*([A-Za-z0-9_-]+)\s*=\s*([^;]+)")
_THERMO_TERMS = {
    "temperature": "Temperature",
    "pressure": "Pressure",
    "density": "Density",
    "potential": "Potential",
}


@dataclass(frozen=True)
class PostprocessConfig:
    """Validated, run-local options for one production analysis."""

    analysis_id: str = "standard"
    discard_fraction: float = 0.20
    discard_time_ps: float | None = None
    pbc_group: str = "System"
    msd_group: str = "System"
    timeout_s: int = 300


class PostprocessError(RuntimeError):
    """Expected post-processing failure with a user-oriented recovery hint."""

    def __init__(
        self,
        message: str,
        hint: str,
        raw_output: str = "",
        kind: ErrorKind = ErrorKind.POSTPROCESS_FAILED,
    ):
        super().__init__(message)
        self.hint = hint
        self.raw_output = raw_output[-1000:]
        self.kind = kind


def run_postprocess(
    work_dir: str | Path,
    config: PostprocessConfig | None = None,
    on_progress: Callable[[dict], None] | None = None,
) -> StepResult:
    """Run the standard, read-only analysis profile for ``prod`` outputs.

    The source ``prod.xtc`` and ``prod.edr`` remain untouched.  The returned
    result owns only artifacts below ``analysis/<analysis_id>/``.
    """
    started_at = time.monotonic()
    cfg = config or PostprocessConfig()
    manifest_path: Path | None = None
    manifest: dict | None = None
    try:
        _validate_config(cfg)
        workspace = Path(work_dir).resolve()
        if not workspace.is_dir():
            raise PostprocessError(
                f"后处理工作目录不存在: {workspace}",
                "确认生产模拟所在的 run 目录。",
                kind=ErrorKind.FILE_NOT_FOUND,
            )

        sources = _source_paths(workspace)
        _require_sources(sources)
        production_evidence = _require_completed_production(workspace, sources)
        analysis_dir = workspace / "analysis" / cfg.analysis_id
        manifest_path = analysis_dir / "analysis_manifest.json"
        if manifest_path.exists():
            raise PostprocessError(
                f"分析结果已存在: analysis/{cfg.analysis_id}",
                "使用新的 analysis_id 创建独立分析，避免覆盖既有结果。",
                kind=ErrorKind.LOCK_CONFLICT,
            )
        analysis_dir.mkdir(parents=True, exist_ok=False)
        manifest = _manifest_base(workspace, sources, cfg, production_evidence)
        _atomic_write_json(manifest_path, manifest)

        _progress(on_progress, "检查生产轨迹", 1, 5)
        integrity = _check_trajectory(workspace, sources, cfg.timeout_s)
        discard_time_ps = _resolve_discard_time(integrity, cfg)

        _progress(on_progress, "处理周期性边界", 2, 5)
        pbc_outputs = _process_pbc(
            workspace, analysis_dir, sources, discard_time_ps, cfg,
        )

        _progress(on_progress, "提取热力学统计", 3, 5)
        thermodynamics = _extract_thermodynamics(
            workspace, analysis_dir, sources["edr"], discard_time_ps, cfg.timeout_s,
        )

        _progress(on_progress, "计算均方位移", 4, 5)
        msd = _calculate_msd(
            workspace, analysis_dir, sources["tpr"], sources["xtc"],
            discard_time_ps, cfg,
        )

        _progress(on_progress, "写入分析记录", 5, 5)
        manifest.update({
            "status": "completed",
            "completed_at": _now(),
            "parameters": {
                **manifest["parameters"],
                "discard_time_ps": discard_time_ps,
            },
            "trajectory_integrity": integrity,
            "pbc_outputs": {
                name: _relative_path(path, workspace)
                for name, path in pbc_outputs.items()
            },
            "thermodynamics": thermodynamics,
            "msd": msd,
            "operations": _operation_manifest(cfg, discard_time_ps),
        })
        _atomic_write_json(manifest_path, manifest)
        outputs = {
            "analysis_manifest": str(manifest_path),
            "trajectory_centered": str(pbc_outputs["centered"]),
            "trajectory_nojump": str(pbc_outputs["nojump"]),
            "msd": str(analysis_dir / "msd_system.xvg"),
        }
        outputs.update({
            f"thermo_{name}": str(analysis_dir / f"thermo_{name}.xvg")
            for name in thermodynamics
        })
        return StepResult(
            step_name="postprocess",
            step_index=POSTPROCESS_STEP_INDEX,
            success=True,
            outputs=outputs,
            artifacts=list(outputs.values()),
            duration_s=time.monotonic() - started_at,
            extra={"analysis": manifest},
            target_type="system",
            target="当前体系",
        )
    except PostprocessError as exc:
        _mark_manifest_failed(manifest_path, manifest, exc)
        return StepResult(
            step_name="postprocess",
            step_index=POSTPROCESS_STEP_INDEX,
            success=False,
            error=StepError(
                kind=exc.kind,
                message=str(exc),
                hint=exc.hint,
                raw_output=exc.raw_output,
            ),
            duration_s=time.monotonic() - started_at,
            target_type="system",
            target="当前体系",
        )
    except OSError as exc:
        failure = PostprocessError(
            f"后处理文件操作失败: {exc}",
            "检查 run 目录权限、分析目录和可用磁盘空间。",
        )
        _mark_manifest_failed(manifest_path, manifest, failure)
        return StepResult(
            step_name="postprocess",
            step_index=POSTPROCESS_STEP_INDEX,
            success=False,
            error=StepError(
                kind=failure.kind,
                message=str(failure),
                hint=failure.hint,
            ),
            duration_s=time.monotonic() - started_at,
            target_type="system",
            target="当前体系",
        )


def _validate_config(config: PostprocessConfig) -> None:
    if not _ANALYSIS_ID_RE.fullmatch(config.analysis_id):
        raise PostprocessError(
            "analysis_id 只能包含字母、数字、点、下划线和连字符。",
            "使用如 standard、transport-01 的分析标识。",
            kind=ErrorKind.CONFIG_INVALID,
        )
    if not 0 <= config.discard_fraction < 1:
        raise PostprocessError(
            "discard_fraction 必须在 [0, 1) 内。",
            "设置为 0 到小于 1 的比例，或使用 discard_time_ps。",
            kind=ErrorKind.CONFIG_INVALID,
        )
    if config.discard_time_ps is not None and config.discard_time_ps < 0:
        raise PostprocessError(
            "discard_time_ps 不能为负数。",
            "设置从生产轨迹起点开始计的非负皮秒数。",
            kind=ErrorKind.CONFIG_INVALID,
        )
    for label, group in (("PBC", config.pbc_group), ("MSD", config.msd_group)):
        if group.strip() and "\n" not in group:
            continue
        raise PostprocessError(
            f"{label} 原子组无效。",
            "使用当前 TPR 中存在的单个 index group 名称。",
            kind=ErrorKind.CONFIG_INVALID,
        )
    if config.timeout_s <= 0:
        raise PostprocessError(
            "后处理超时必须为正数。", "设置合理的 timeout_s。", kind=ErrorKind.CONFIG_INVALID,
        )


def _source_paths(workspace: Path) -> dict[str, Path]:
    return {
        "tpr": workspace / "prod.tpr",
        "xtc": workspace / "prod.xtc",
        "edr": workspace / "prod.edr",
        "mdp": workspace / "prod.mdp",
    }


def _manifest_base(
    workspace: Path,
    sources: dict[str, Path],
    config: PostprocessConfig,
    production_evidence: dict,
) -> dict:
    return {
        "schema_version": 1,
        "status": "running",
        "analysis_id": config.analysis_id,
        "stage": "prod",
        "created_at": _now(),
        "source_artifacts": {
            name: _artifact_fingerprint(path, workspace)
            for name, path in sources.items()
        },
        "production_acceptance": production_evidence,
        "parameters": {
            "discard_fraction": config.discard_fraction,
            "discard_time_ps": config.discard_time_ps,
            "pbc_group": config.pbc_group,
            "msd_group": config.msd_group,
        },
    }


def _mark_manifest_failed(
    manifest_path: Path | None,
    manifest: dict | None,
    error: PostprocessError,
) -> None:
    if manifest_path is None or manifest is None:
        return
    try:
        manifest.update({
            "status": "failed",
            "failed_at": _now(),
            "error": {
                "kind": error.kind.value,
                "message": str(error),
                "hint": error.hint,
            },
        })
        _atomic_write_json(manifest_path, manifest)
    except OSError:
        # The original failure remains the primary result; do not mask it.
        return


def _require_sources(sources: dict[str, Path]) -> None:
    missing = [path.name for path in sources.values() if not path.is_file() or path.stat().st_size == 0]
    if missing:
        raise PostprocessError(
            f"生产模拟产物不完整: {', '.join(missing)}",
            "先完成生产模拟，并确认 prod.tpr、prod.xtc、prod.edr、prod.mdp 非空。",
            kind=ErrorKind.FILE_NOT_FOUND,
        )


def _require_completed_production(workspace: Path, sources: dict[str, Path]) -> dict:
    """Prove that sources belong to one accepted production protocol."""
    try:
        run_manifest = load_manifest(workspace)
    except ManifestError as exc:
        raise PostprocessError(
            "缺少可验收的生产运行记录。",
            "先通过 simulation 层完成 PROD；后处理不能分析手工拼接的文件。",
            str(exc),
            kind=ErrorKind.INPUT_CONTRACT,
        ) from exc
    prod = run_manifest.get("stages", {}).get("prod", {})
    if prod.get("status") != "completed":
        raise PostprocessError(
            "生产阶段尚未在 manifest 中验收完成。",
            "完成 PROD 并确认 md_manifest.json 的 stages.prod.status 为 completed。",
            kind=ErrorKind.INPUT_CONTRACT,
        )

    expected = dict(prod.get("outputs", {}))
    contract_inputs = prod.get("contract", {}).get("inputs", {})
    expected["mdp"] = contract_inputs.get("mdp")
    actual: dict[str, dict] = {}
    for name, path in sources.items():
        recorded = expected.get(name)
        if not isinstance(recorded, dict):
            raise PostprocessError(
                f"manifest 缺少 prod.{name} 的验收指纹。",
                "从同一 run 的 PROD 阶段重新生成完整产物后再分析。",
                kind=ErrorKind.INPUT_CONTRACT,
            )
        try:
            fingerprint = file_fingerprint(path, workspace)
        except ManifestError as exc:
            raise PostprocessError(
                f"无法验证 prod.{name}。",
                "确认生产产物仍位于其原始 run 目录。",
                str(exc),
                kind=ErrorKind.INPUT_CONTRACT,
            ) from exc
        if (
            fingerprint.get("sha256") != recorded.get("sha256")
            or fingerprint.get("size_bytes") != recorded.get("size_bytes")
        ):
            raise PostprocessError(
                f"prod.{name} 与已验收 PROD 产物指纹不一致。",
                "不要混用不同运行的轨迹；使用 manifest 对应的原始 PROD 产物。",
                kind=ErrorKind.INPUT_CONTRACT,
            )
        actual[name] = fingerprint
    return {
        "run_manifest": "md_manifest.json",
        "prod_status": prod["status"],
        "contract_fingerprint": prod.get("contract", {}).get("fingerprint", ""),
        "verified_artifacts": actual,
    }


def _check_trajectory(workspace: Path, sources: dict[str, Path], timeout_s: int) -> dict:
    result = _run_gmx_checked(
        ["check", "-f", str(sources["xtc"]), "-s1", str(sources["tpr"])], workspace, timeout_s,
        "无法读取生产轨迹",
    )
    output = _command_output(result)
    last_match = _LAST_FRAME_RE.search(output)
    if not last_match:
        raise PostprocessError(
            "无法从 gmx check 输出确认轨迹末帧时间。",
            "检查 prod.xtc 是否为有效 GROMACS 轨迹。",
            output,
        )
    expected_time_ps, output_interval_ps = _expected_trajectory_time(sources["mdp"])
    observed_time_ps = float(last_match.group(1))
    tolerance_ps = max(0.001, output_interval_ps * 1.1)
    if observed_time_ps < expected_time_ps - tolerance_ps:
        raise PostprocessError(
            f"生产轨迹不完整: 末帧 {observed_time_ps:.3f} ps，预期 {expected_time_ps:.3f} ps。",
            "从 prod.cpt 续跑生产模拟，完成后再执行后处理。",
            output,
        )
    frame_match = _FRAME_COUNT_RE.search(output)
    return {
        "complete": True,
        "expected_time_ps": round(expected_time_ps, 6),
        "observed_last_time_ps": round(observed_time_ps, 6),
        "completion_tolerance_ps": round(tolerance_ps, 6),
        "frame_count": int(frame_match.group(1)) if frame_match else None,
    }


def _expected_trajectory_time(mdp_path: Path) -> tuple[float, float]:
    values: dict[str, str] = {}
    for line in mdp_path.read_text(errors="replace").splitlines():
        match = _MDP_VALUE_RE.match(line)
        if match:
            values[match.group(1).lower()] = match.group(2).strip().split()[0]
    try:
        dt = float(values["dt"])
        nsteps = int(float(values["nsteps"]))
    except (KeyError, ValueError) as exc:
        raise PostprocessError(
            "prod.mdp 缺少有效的 dt 或 nsteps。",
            "重新生成生产阶段 MDP 后再分析。",
        ) from exc
    if dt <= 0 or nsteps <= 0:
        raise PostprocessError("prod.mdp 的 dt 或 nsteps 无效。", "检查生产阶段时长和时间步长。")
    output_interval_steps = int(float(values.get("nstxout-compressed", "1")))
    if output_interval_steps <= 0:
        output_interval_steps = 1
    return dt * nsteps, dt * output_interval_steps


def _resolve_discard_time(integrity: dict, config: PostprocessConfig) -> float:
    observed = float(integrity["observed_last_time_ps"])
    value = config.discard_time_ps
    discard = float(value) if value is not None else observed * config.discard_fraction
    if discard >= observed:
        raise PostprocessError(
            "平衡段剔除时间覆盖了整段生产轨迹。",
            "减小 discard_time_ps 或 discard_fraction。",
        )
    return round(discard, 6)


def _process_pbc(
    workspace: Path,
    analysis_dir: Path,
    sources: dict[str, Path],
    discard_time_ps: float,
    config: PostprocessConfig,
) -> dict[str, Path]:
    whole = analysis_dir / ".prod_whole.xtc"
    centered = analysis_dir / "prod_centered.xtc"
    nojump = analysis_dir / "prod_nojump.xtc"
    group = f"{config.pbc_group.strip()}\n"
    _run_gmx_checked(
        [
            "trjconv", "-s", str(sources["tpr"]), "-f", str(sources["xtc"]),
            "-o", str(whole), "-pbc", "mol", "-ur", "compact",
        ], workspace, config.timeout_s, "无法生成分子完整轨迹", input_text=group,
    )
    _run_gmx_checked(
        [
            "trjconv", "-s", str(sources["tpr"]), "-f", str(whole),
            "-o", str(centered), "-center", "-pbc", "mol", "-ur", "compact",
            "-b", _as_gmx_number(discard_time_ps),
        ], workspace, config.timeout_s, "无法生成居中分析轨迹", input_text=group + group,
    )
    _run_gmx_checked(
        [
            "trjconv", "-s", str(sources["tpr"]), "-f", str(whole),
            "-o", str(nojump), "-pbc", "nojump",
        ], workspace, config.timeout_s, "无法生成连续 MSD 轨迹", input_text=group,
    )
    for path in (whole, centered, nojump):
        if not path.is_file() or path.stat().st_size == 0:
            raise PostprocessError(
                f"PBC 处理未生成有效产物: {path.name}",
                "检查生产轨迹与 prod.tpr 是否匹配。",
            )
    whole.unlink()
    return {"centered": centered, "nojump": nojump}


def _extract_thermodynamics(
    workspace: Path,
    analysis_dir: Path,
    edr_path: Path,
    discard_time_ps: float,
    timeout_s: int,
) -> dict[str, dict]:
    summaries: dict[str, dict] = {}
    for name, term in _THERMO_TERMS.items():
        xvg_path = analysis_dir / f"thermo_{name}.xvg"
        _run_gmx_checked(
            ["energy", "-f", str(edr_path), "-o", str(xvg_path)], workspace,
            timeout_s, f"无法提取 {term} 热力学数据", input_text=f"{term}\n0\n",
        )
        if not xvg_path.is_file() or xvg_path.stat().st_size == 0:
            raise PostprocessError(
                f"未生成 {term} 热力学数据。",
                "确认 prod.edr 包含该能量项并重新运行生产模拟。",
            )
        summaries[name] = {
            "term": term,
            "xvg": _relative_path(xvg_path, workspace),
            "statistics": _series_statistics(xvg_path, discard_time_ps),
        }
    return summaries


def _series_statistics(xvg_path: Path, discard_time_ps: float) -> dict:
    _, columns = parse_xvg(xvg_path)
    if len(columns) < 2:
        raise PostprocessError(
            f"{xvg_path.name} 不包含可统计的时间序列。",
            "检查 GROMACS energy 输出是否完整。",
        )
    times, values = columns[0], columns[1]
    retained = [value for timestamp, value in zip(times, values) if timestamp >= discard_time_ps]
    if not retained:
        raise PostprocessError(
            f"{xvg_path.name} 在平衡段剔除后没有数据。",
            "缩短平衡段剔除时间或延长生产模拟。",
        )
    mean = sum(retained) / len(retained)
    variance = sum((value - mean) ** 2 for value in retained) / max(1, len(retained) - 1)
    block_means = _block_means(retained)
    block_error = None
    if len(block_means) > 1:
        block_mean = sum(block_means) / len(block_means)
        block_variance = sum((value - block_mean) ** 2 for value in block_means) / (len(block_means) - 1)
        block_error = math.sqrt(block_variance / len(block_means))
    return {
        "sample_count": len(retained),
        "mean": round(mean, 6),
        "stddev": round(math.sqrt(variance), 6),
        "min": round(min(retained), 6),
        "max": round(max(retained), 6),
        "block_count": len(block_means),
        "block_standard_error": round(block_error, 6) if block_error is not None else None,
        "discard_before_ps": discard_time_ps,
    }


def _block_means(values: list[float]) -> list[float]:
    blocks = min(5, len(values))
    if blocks == 0:
        return []
    result = []
    for index in range(blocks):
        start = index * len(values) // blocks
        end = (index + 1) * len(values) // blocks
        block = values[start:end]
        if block:
            result.append(sum(block) / len(block))
    return result


def _calculate_msd(
    workspace: Path,
    analysis_dir: Path,
    tpr_path: Path,
    trajectory_path: Path,
    discard_time_ps: float,
    config: PostprocessConfig,
) -> dict:
    xvg_path = analysis_dir / "msd_system.xvg"
    _run_gmx_checked(
        [
            "msd", "-s", str(tpr_path), "-f", str(trajectory_path), "-o", str(xvg_path),
            "-b", _as_gmx_number(discard_time_ps),
        ], workspace, config.timeout_s, "无法计算均方位移", input_text=f"{config.msd_group.strip()}\n",
    )
    if not xvg_path.is_file() or xvg_path.stat().st_size == 0:
        raise PostprocessError(
            "未生成 MSD 数据。",
            "确认所选 MSD 原子组存在，并检查连续轨迹。",
        )
    _, columns = parse_xvg(xvg_path)
    if len(columns) < 2 or len(columns[0]) < 2:
        raise PostprocessError(
            "MSD 数据点不足，无法形成扩散统计。",
            "延长生产模拟或降低轨迹输出间隔。",
        )
    slope = _linear_slope(columns[0], columns[1])
    diffusion_nm2_per_ps = slope / 6.0
    return {
        "group": config.msd_group.strip(),
        "xvg": _relative_path(xvg_path, workspace),
        "sample_count": len(columns[0]),
        "time_window_ps": [round(columns[0][0], 6), round(columns[0][-1], 6)],
        "linear_slope_nm2_per_ps": round(slope, 10),
        "preliminary_diffusion_nm2_per_ps": round(diffusion_nm2_per_ps, 12),
        "preliminary_diffusion_cm2_per_s": round(diffusion_nm2_per_ps * 0.01, 12),
        "fit_note": "全保留窗口的线性拟合，仅供初步判断；需确认 MSD 线性区间后再报告物性。",
    }


def _linear_slope(xs: list[float], ys: list[float]) -> float:
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    denominator = sum((value - mean_x) ** 2 for value in xs)
    if denominator <= 0:
        raise PostprocessError("MSD 时间轴无有效跨度。", "检查轨迹是否包含多个不同时间点。")
    return sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / denominator


def _run_gmx_checked(
    args: list[str],
    workspace: Path,
    timeout_s: int,
    failure_message: str,
    input_text: str | None = None,
) -> subprocess.CompletedProcess:
    try:
        result = run_gmx(args, workspace, timeout=timeout_s, input_text=input_text)
    except FileNotFoundError as exc:
        raise PostprocessError(
            "未在当前 PATH 中找到 GROMACS 命令 gmx。",
            "安装 GROMACS 并确认启动 Willy 的同一环境可执行 gmx。",
            str(exc),
            ErrorKind.DEPENDENCY_MISSING,
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise PostprocessError(
            f"{failure_message}: 命令超时。",
            "检查轨迹大小、磁盘空间和分析资源后重试。",
            _timeout_output(exc),
            ErrorKind.TIMEOUT,
        ) from exc
    if result.returncode != 0:
        raise PostprocessError(
            failure_message,
            "确认生产轨迹、TPR 和能量文件来自同一次运行。",
            _command_output(result),
        )
    return result


def _progress(callback: Callable[[dict], None] | None, operation: str, current: int, total: int) -> None:
    if callback:
        callback({
            "tool": "GROMACS",
            "operation": operation,
            "target_type": "system",
            "target": "当前体系",
            "current": current,
            "total": total,
        })


def _operation_manifest(config: PostprocessConfig, discard_time_ps: float) -> list[dict]:
    """Record reproducible operation choices without raw command output."""
    return [
        {"tool": "gmx check", "input": "prod.xtc", "structure": "prod.tpr"},
        {
            "tool": "gmx trjconv", "output": "temporary whole trajectory",
            "pbc": "mol", "unit_cell": "compact", "group": config.pbc_group,
        },
        {
            "tool": "gmx trjconv", "output": "prod_centered.xtc",
            "pbc": "mol", "center": True, "begin_ps": discard_time_ps,
            "group": config.pbc_group,
        },
        {"tool": "gmx trjconv", "output": "prod_nojump.xtc", "pbc": "nojump", "group": config.pbc_group},
        {"tool": "gmx energy", "terms": list(_THERMO_TERMS.values())},
        {
            "tool": "gmx msd", "group": config.msd_group, "begin_ps": discard_time_ps,
            "source": "prod.xtc", "pbc": "gmx_default",
        },
    ]


def _artifact_fingerprint(path: Path, workspace: Path) -> dict:
    stat = path.stat()
    item = {
        "path": _relative_path(path, workspace),
        "size_bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "fingerprint": "size_mtime",
    }
    if path.suffix == ".mdp":
        item["sha256"] = _sha256(path)
    return item


def _relative_path(path: Path, workspace: Path) -> str:
    return path.resolve().relative_to(workspace.resolve()).as_posix()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _atomic_write_json(path: Path, payload: dict) -> None:
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temp_name, path)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise


def _command_output(result: subprocess.CompletedProcess) -> str:
    return ((result.stderr or "") + "\n" + (result.stdout or ""))[-1000:]


def _timeout_output(exc: subprocess.TimeoutExpired) -> str:
    stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else exc.stdout or ""
    stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else exc.stderr or ""
    return (stderr + "\n" + stdout)[-1000:]


def _as_gmx_number(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="分析已完成的 GROMACS 生产轨迹")
    parser.add_argument("work_dir", help="本次生产模拟的 run 目录")
    parser.add_argument("--analysis-id", default="standard", help="分析结果标识")
    parser.add_argument("--discard-fraction", type=float, default=0.20, help="默认剔除的生产轨迹比例")
    parser.add_argument("--discard-time-ps", type=float, help="显式剔除的平衡段时长（ps）")
    parser.add_argument("--msd-group", default="System", help="用于 MSD 的 GROMACS index group")
    arguments = parser.parse_args()
    result = run_postprocess(
        arguments.work_dir,
        PostprocessConfig(
            analysis_id=arguments.analysis_id,
            discard_fraction=arguments.discard_fraction,
            discard_time_ps=arguments.discard_time_ps,
            msd_group=arguments.msd_group,
        ),
    )
    if result.success:
        print(f"后处理完成: {result.outputs['analysis_manifest']}")
    else:
        print(result.error.message if result.error else "后处理失败")
        raise SystemExit(1)
