"""Deterministic final-hold time coverage for EQ acceptance."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
import math
import re


EQ_ACCEPTANCE_POLICY = "eq-final-1ns-five-blocks-v1"
EQ_WINDOW_NS = 1.0
EQ_BLOCK_COUNT = 5


class EQCoverageError(ValueError):
    """Required EQ time evidence is incomplete or inconsistent."""


@dataclass(frozen=True)
class EQWindow:
    dt_ps: float
    initial_step: int
    final_step: int
    initial_time_ps: float
    energy_stride: int
    hold_start_ps: float

    @property
    def end_ps(self) -> float:
        return self.initial_time_ps + self.final_step * self.dt_ps

    @property
    def start_ps(self) -> float:
        return self.end_ps - EQ_WINDOW_NS * 1000.0

    @property
    def interval_ps(self) -> float:
        return self.dt_ps * self.energy_stride

    @property
    def tolerance_ps(self) -> float:
        return min(self.interval_ps / 100.0, max(1e-6, abs(self.end_ps) * 1e-9))

    @classmethod
    def from_mdp(cls, path: Path, metadata: Mapping, target_temperature: float) -> EQWindow:
        try:
            fields = {}
            for line in path.read_text().splitlines():
                content = line.split(";", 1)[0].strip()
                if not content or "=" not in content:
                    continue
                name, value = content.split("=", 1)
                name = name.strip().lower().replace("-", "_")
                if name in fields:
                    raise ValueError("duplicate MDP field")
                fields[name] = value.strip()
            dt_ps = float(fields["dt"])
            nsteps = int(fields["nsteps"])
            energy_stride = int(fields["nstenergy"])
            initial_step = int(fields.get("init_step", "0"))
            initial_time_ps = float(fields.get("tinit", "0"))
            points = [float(value) for value in fields["annealing_time"].split()]
            temperatures = [float(value) for value in fields["annealing_temp"].split()]
            if fields["annealing"] != "single" or int(fields["annealing_npoints"]) != len(points):
                raise ValueError("unsupported annealing schedule")
            if len(points) < 2 or len(points) != len(temperatures):
                raise ValueError("invalid annealing points")
            if not all(math.isfinite(value) for value in (
                dt_ps, initial_time_ps, target_temperature, *points, *temperatures,
            )):
                raise ValueError("non-finite protocol")
            if dt_ps <= 0 or nsteps <= 0 or energy_stride <= 0 or initial_step < 0:
                raise ValueError("invalid time controls")
            window = cls(dt_ps, initial_step, initial_step + nsteps, initial_time_ps, energy_stride, points[-2])
            if not all(math.isfinite(value) for value in (window.end_ps, window.interval_ps)):
                raise ValueError("time overflow")
            if any(later <= earlier for earlier, later in zip(points, points[1:])):
                raise ValueError("unordered annealing points")
            if not math.isclose(points[-1], window.end_ps, rel_tol=0, abs_tol=window.tolerance_ps):
                raise ValueError("annealing end does not match duration")
            if any(not math.isclose(value, target_temperature, rel_tol=0, abs_tol=1e-6) for value in temperatures[-2:]):
                raise ValueError("final segment is not a target-temperature hold")
            if window.hold_start_ps > window.start_ps + window.tolerance_ps:
                raise ValueError("target hold shorter than one nanosecond")
            if window.hold_start_ps < initial_time_ps + initial_step * dt_ps - window.tolerance_ps:
                raise ValueError("target hold outside current EQ stage")
            if int(metadata["nsteps"]) != nsteps:
                raise ValueError("metadata step mismatch")
            if not math.isclose(float(metadata["actual_ns"]) * 1000.0, nsteps * dt_ps, rel_tol=0, abs_tol=window.tolerance_ps):
                raise ValueError("metadata duration mismatch")
            declared_points = metadata["annealing_time_ps"]
            if len(declared_points) != len(points) or any(
                not math.isclose(float(declared), observed, rel_tol=0, abs_tol=window.tolerance_ps)
                for declared, observed in zip(declared_points, points)
            ):
                raise ValueError("metadata annealing mismatch")
            return window
        except (OSError, KeyError, TypeError, ValueError, OverflowError) as exc:
            raise EQCoverageError("EQ 时间协议缺失、不一致或最终目标保温段不足 1 ns") from exc

    def completion(self, log_path: Path) -> dict:
        try:
            text = log_path.read_text(errors="replace")
            progress = list(re.finditer(r"(?:^|\n)\s*Step\s+Time\s*\n\s*(\S+)\s+(\S+)", text))
            if not progress or text.rfind("Finished mdrun") < progress[-1].end():
                raise ValueError("normal completion missing")
            final_step = int(progress[-1].group(1))
            final_time = float(progress[-1].group(2))
            if final_step != self.final_step or not math.isclose(final_time, self.end_ps, rel_tol=0, abs_tol=self.tolerance_ps):
                raise ValueError("early or inconsistent completion")
            return {"normal_end": True, "final_step": final_step, "observed_end_ps": final_time}
        except (OSError, ValueError, OverflowError) as exc:
            raise EQCoverageError("EQ 未正常完成或实际结束步数、时间与协议不一致") from exc

    def select(self, times: list[float]) -> tuple[list[int], list[list[int]], dict]:
        if not times or not all(math.isfinite(value) for value in times):
            raise EQCoverageError("EQ 能量时间序列缺失或包含非有限值")
        if any(later <= earlier for earlier, later in zip(times, times[1:])):
            raise EQCoverageError("EQ 能量时间必须严格递增，拒绝乱序或重复时间")
        if not math.isclose(times[-1], self.end_ps, rel_tol=0, abs_tol=self.tolerance_ps):
            raise EQCoverageError("EQ 能量实际终点与协议不一致，拒绝提前结束或越界数据")
        initial_ps = self.initial_time_ps + self.initial_step * self.dt_ps
        if times[0] < initial_ps - self.tolerance_ps:
            raise EQCoverageError("EQ 能量时间超出本阶段范围")
        selected = [index for index, value in enumerate(times) if value >= self.start_ps - self.tolerance_ps]
        first_grid = math.ceil((self.start_ps - self.initial_time_ps - self.tolerance_ps) / self.interval_ps)
        last_grid = self.final_step // self.energy_stride
        grid_count = max(0, last_grid - first_grid + 1)
        terminal_extra = self.final_step % self.energy_stride != 0
        expected_count = grid_count + int(terminal_extra)
        if len(selected) != expected_count:
            raise EQCoverageError("EQ 最后 1 ns 采样不完整，存在缺段、缺失样本或额外时间点")
        expected_times = [self.initial_time_ps + index * self.interval_ps for index in range(first_grid, last_grid + 1)]
        if terminal_extra:
            expected_times.append(self.end_ps)
        groups: list[list[int]] = [[] for _ in range(EQ_BLOCK_COUNT)]
        width_ps = EQ_WINDOW_NS * 1000.0 / EQ_BLOCK_COUNT
        for position, (source_index, expected_time) in enumerate(zip(selected, expected_times)):
            if not math.isclose(times[source_index], expected_time, rel_tol=0, abs_tol=self.tolerance_ps):
                raise EQCoverageError("EQ 窗口存在采样缺口或时间点不符合实际输出频率")
            block = min(EQ_BLOCK_COUNT - 1, max(0, int((expected_time - self.start_ps + self.tolerance_ps) / width_ps)))
            groups[block].append(position)
        if any(len(group) < 2 for group in groups):
            raise EQCoverageError("EQ 五段必须各自具备有效覆盖，单个瞬时点不能作为分段证据")
        blocks = []
        for index, group in enumerate(groups):
            blocks.append({
                "start_ps": self.start_ps + index * width_ps,
                "end_ps": self.start_ps + (index + 1) * width_ps,
                "relative_end_ns": round(-EQ_WINDOW_NS + (index + 1) * EQ_WINDOW_NS / EQ_BLOCK_COUNT, 8),
                "sample_count": len(group),
                "observed_start_ps": times[selected[group[0]]],
                "observed_end_ps": times[selected[group[-1]]],
                "complete": True,
            })
        return selected, groups, {
            "policy": EQ_ACCEPTANCE_POLICY,
            "complete": True,
            "expected_start_ps": self.start_ps,
            "expected_end_ps": self.end_ps,
            "observed_start_ps": times[selected[0]],
            "observed_end_ps": times[selected[-1]],
            "hold_start_ps": self.hold_start_ps,
            "sample_interval_ps": self.interval_ps,
            "expected_sample_count": expected_count,
            "sample_count": len(selected),
            "blocks": blocks,
        }


def has_eq_coverage_evidence(record: Mapping) -> bool:
    try:
        result = record["details"]["eq_result"]
        details = result["details"]
        if (
            result["converged"] is not True
            or details["acceptance_policy"] != EQ_ACCEPTANCE_POLICY
            or details["auto_acceptance"] is not True
        ):
            return False
        completion = details["completion"]
        if completion["normal_end"] is not True or type(completion["final_step"]) is not int or completion["final_step"] <= 0:
            return False
        if details["acceptance_window_ns"] != EQ_WINDOW_NS:
            return False
        for name in ("temperature", "potential"):
            stats = details["series"][name]
            coverage = stats["coverage"]
            blocks = coverage["blocks"]
            if (
                stats["ok"] is not True or stats["window_ps"] != EQ_WINDOW_NS * 1000.0
                or coverage["complete"] is not True or coverage["policy"] != EQ_ACCEPTANCE_POLICY
            ):
                return False
            if len(blocks) != EQ_BLOCK_COUNT or coverage["sample_count"] != coverage["expected_sample_count"]:
                return False
            if coverage["sample_count"] != stats["sample_count"]:
                return False
            start_ps, end_ps = coverage["expected_start_ps"], coverage["expected_end_ps"]
            tolerance_ps = min(coverage["sample_interval_ps"] / 100.0, max(1e-6, abs(end_ps) * 1e-9))
            if (
                not math.isclose(end_ps - start_ps, EQ_WINDOW_NS * 1000.0, rel_tol=0, abs_tol=tolerance_ps)
                or coverage["hold_start_ps"] > start_ps + tolerance_ps
            ):
                return False
            if coverage["sample_interval_ps"] <= 0 or any(not math.isclose(
                observed, end_ps, rel_tol=0, abs_tol=tolerance_ps,
            ) for observed in (coverage["observed_end_ps"], completion["observed_end_ps"])):
                return False
            block_width_ps = EQ_WINDOW_NS * 1000.0 / EQ_BLOCK_COUNT
            for index, block in enumerate(blocks):
                if any(not math.isclose(observed, expected, rel_tol=0, abs_tol=tolerance_ps) for observed, expected in (
                    (block["start_ps"], start_ps + index * block_width_ps),
                    (block["end_ps"], start_ps + (index + 1) * block_width_ps),
                )):
                    return False
            if any(block["complete"] is not True or block["sample_count"] < 2 for block in blocks):
                return False
            if sum(block["sample_count"] for block in blocks) != stats["sample_count"]:
                return False
            if not all(math.isfinite(float(value)) for value in (
                stats["mean"], stats["relative_slope_per_ns"], start_ps, end_ps,
                coverage["hold_start_ps"], coverage["sample_interval_ps"],
                *(block["mean"] for block in blocks),
            )):
                return False
        return True
    except (KeyError, TypeError, ValueError, OverflowError):
        return False
