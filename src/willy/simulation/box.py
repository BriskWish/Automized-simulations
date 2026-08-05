"""
box.py
===============
Packmol .inp 文件生成器。

入参全部封装为数据类，方便后续接入 AI 模块。
组分来源统一从 config.json 的 residues 读取，与 top_assembly 保持一致。
"""

from dataclasses import dataclass
from typing import List, Literal, Optional
from pathlib import Path
import subprocess
import math
import json
import re

from willy._paths import get_project_root
from willy.errors import StepResult, StepError, ErrorKind
from willy.env_registry import EnvironmentRegistryError, build_tool_env, require_tool
from willy.step_registry import PACKMOL_STEP


DEFAULT_TARGET_MASS_DENSITY_G_CM3 = 1.5
_AMU_TO_GRAM = 1.66053906660e-24
_CM3_TO_NM3 = 1.0e21
_PBC_LENGTH_TOLERANCE_ANGSTROM = 0.02
_PBC_ANGLE_TOLERANCE_DEGREES = 0.02


# ============================================================
# 入参层 —— 单独封装，AI 只需要填这些
# ============================================================

@dataclass
class Component:
    """
    一个盒子组分（残基种类）。

    示例:
        Component(pdb="Li.pdb", count=100)
        Component(pdb="TFSI.pdb", count=100)
        Component(pdb="water.pdb", count=1000)
        Component(pdb="lysozyme.pdb", count=1, constraint="fixed", fixed_pos=(0,0,0))
    """
    pdb: str                        # .pdb 文件路径
    count: int                      # 分子个数
    residue_name: Optional[str] = None
    molecular_mass_amu: Optional[float] = None

    # 约束类型与参数
    constraint: Literal["inside_cube", "inside_box", "fixed"] = "inside_cube"
    center: tuple = (0.0, 0.0, 0.0)      # 约束中心
    half_sides: tuple = (30.0, 30.0, 30.0)  # inside_cube 的半边长 (dx, dy, dz)


@dataclass
class InpConfig:
    """
    Packmol 输入文件的全部配置。

    示例:
        config = InpConfig(
            components=[
                Component(pdb="Li.pdb", count=100),
                Component(pdb="TFSI.pdb", count=100),
            ],
            output_name="model",
        )
    """
    components: List[Component]              # 组分列表

    # —— 全局设置 ——
    tolerance: float = 2.0                   # 分子间最小距离 (Å)
    filetype: str = "pdb"                    # pdb / xyz
    # Kept only to read historical config snapshots. Explicit ``pbc`` below
    # owns the periodic box, so this value is deliberately never written.
    add_box_sides: Optional[float] = None
    output_name: str = "model"               # 输出文件名（不含扩展名）
    output_dir: str = "."                    # 输出目录
    seed: int = -1                           # 随机种子，-1 表示随机

    # —— 盒子尺寸 ——
    box_size: Optional[float] = None         # 若设了，直接使用（Å）
    target_mass_density_g_cm3: Optional[float] = DEFAULT_TARGET_MASS_DENSITY_G_CM3
    packing_number_density_nm3: Optional[float] = None  # 历史兼容字段

    # —— 其他 ——
    packmol_bin: str = str(get_project_root() / "vendor" / "packmol")


# ============================================================
# 盒子尺寸估算（硬编码公式）
# ============================================================


@dataclass(frozen=True)
class BoxPlan:
    """One auditable cubic-box decision made before Packmol starts."""

    box_size_angstrom: float
    box_volume_nm3: float
    source: str
    total_mass_amu: Optional[float]
    target_mass_density_g_cm3: Optional[float]
    packing_number_density_nm3: Optional[float]


def estimate_box_size_from_mass(
    total_mass_amu: float,
    target_mass_density_g_cm3: float = DEFAULT_TARGET_MASS_DENSITY_G_CM3,
) -> float:
    """Return a cubic edge in Angstrom from topology mass and target density.

    ``1 amu = 1.66053906660e-24 g`` and ``1 cm3 = 1e21 nm3``.  Therefore
    ``V_nm3 = total_mass_amu * 1.66053906660e-3 / density_g_cm3``.
    """
    if total_mass_amu <= 0:
        raise ValueError("拓扑总质量必须为正")
    if target_mass_density_g_cm3 <= 0:
        raise ValueError("box.target_mass_density_g_cm3 必须为正")
    volume_nm3 = (
        total_mass_amu * _AMU_TO_GRAM * _CM3_TO_NM3 / target_mass_density_g_cm3
    )
    side_angstrom = (volume_nm3 ** (1.0 / 3.0)) * 10.0
    # Packmol accepts fractional Angstrom values. Do not ceil the result:
    # doing so changes the requested mass density for small systems.
    return round(side_angstrom, 3)

def estimate_box_size(
    components: List[Component],
    packing_number_density_nm3: float = 6.0,
) -> float:
    """
    硬编码估算盒子边长。

    公式: box = ∛(N / packing_number_density_nm3) × 10 (Å)，向上取整。
    """
    if packing_number_density_nm3 <= 0:
        raise ValueError("packing_number_density_nm3 必须为正")
    total = sum(c.count for c in components)
    vol_nm3 = total / packing_number_density_nm3
    side_nm = vol_nm3 ** (1 / 3)         # 边长 (nm)
    box = math.ceil(side_nm * 10)        # → Å，向上取整
    print(f"[estimate] 总分子数={total}, 体积={vol_nm3:.1f} nm³, "
        f"数密度={packing_number_density_nm3:.4g} 分子/nm³, "
        f"边长={side_nm:.2f} nm → box={box} Å")
    return float(box)


def _total_mass_amu(components: List[Component]) -> float:
    missing = [component.residue_name or Path(component.pdb).stem
               for component in components if component.molecular_mass_amu is None]
    if missing:
        raise ValueError(
            "质量密度建盒需要每个组分的拓扑质量，缺少: " + ", ".join(missing)
        )
    total = sum(
        float(component.count) * float(component.molecular_mass_amu)
        for component in components
    )
    if total <= 0:
        raise ValueError("拓扑总质量必须为正")
    return total


def _mass_density_g_cm3(total_mass_amu: float, volume_nm3: float) -> float:
    if volume_nm3 <= 0:
        raise ValueError("盒子体积必须为正")
    return total_mass_amu * _AMU_TO_GRAM * _CM3_TO_NM3 / volume_nm3


# ============================================================
# 生成器层
# ============================================================

class InpGenerator:
    """
    .inp 文件生成器。

    用法:
        config = InpConfig(components=[...], output_name="model")
        gen = InpGenerator(config)
        gen.write("model.inp")         # 只写 inp
        gen.run()                      # 写 inp + 调 packmol
    """

    def __init__(self, config: InpConfig):
        self.config = config
        self._inp_path: Optional[Path] = None
        self._box_plan: Optional[BoxPlan] = None

    def box_plan(self) -> BoxPlan:
        """Resolve the requested cubic box once for input and audit output."""
        if self._box_plan is not None:
            return self._box_plan

        cfg = self.config
        if cfg.box_size is not None:
            side = float(cfg.box_size)
            if side <= 0:
                raise ValueError("box.box_size 必须为正")
            total_mass = None
            try:
                total_mass = _total_mass_amu(cfg.components)
            except ValueError:
                # Explicit user geometry remains executable even when an
                # imported topology omits per-atom masses. The final audit
                # records the unavailable density instead of inventing one.
                pass
            self._box_plan = BoxPlan(
                box_size_angstrom=side,
                box_volume_nm3=(side / 10.0) ** 3,
                source="explicit_box_size",
                total_mass_amu=total_mass,
                target_mass_density_g_cm3=cfg.target_mass_density_g_cm3,
                packing_number_density_nm3=cfg.packing_number_density_nm3,
            )
            return self._box_plan

        if cfg.target_mass_density_g_cm3 is not None:
            target_density = float(cfg.target_mass_density_g_cm3)
            total_mass = _total_mass_amu(cfg.components)
            side = estimate_box_size_from_mass(total_mass, target_density)
            self._box_plan = BoxPlan(
                box_size_angstrom=side,
                box_volume_nm3=(side / 10.0) ** 3,
                source="target_mass_density",
                total_mass_amu=total_mass,
                target_mass_density_g_cm3=target_density,
                packing_number_density_nm3=cfg.packing_number_density_nm3,
            )
            print(
                "[box] 初始体积将由使用默认1.5g/cm3的密度猜测"
                if target_density == DEFAULT_TARGET_MASS_DENSITY_G_CM3
                else f"[box] 初始体积由目标质量密度 {target_density:g} g/cm3 估算"
            )
            print(
                f"[box] 拓扑总质量={total_mass:.6f} amu, "
                f"体积={self._box_plan.box_volume_nm3:.6f} nm3, "
                f"边长={side:.3f} A"
            )
            return self._box_plan

        if cfg.packing_number_density_nm3 is not None:
            density = float(cfg.packing_number_density_nm3)
            side = estimate_box_size(cfg.components, density)
            total_mass = None
            try:
                total_mass = _total_mass_amu(cfg.components)
            except ValueError:
                pass
            self._box_plan = BoxPlan(
                box_size_angstrom=side,
                box_volume_nm3=(side / 10.0) ** 3,
                source="legacy_number_density",
                total_mass_amu=total_mass,
                target_mass_density_g_cm3=None,
                packing_number_density_nm3=density,
            )
            return self._box_plan

        raise ValueError("建盒需要 box.box_size、target_mass_density_g_cm3 或历史数密度")

    # ── 内容生成 ──

    def build(self) -> str:
        """根据配置生成完整的 .inp 内容字符串。"""
        cfg = self.config
        plan = self.box_plan()
        box = plan.box_size_angstrom
        lines = []

        # 全局设置
        lines.append(f"tolerance {cfg.tolerance}")
        lines.append(f"filetype {cfg.filetype}")
        # This is the one source of truth for the PDB CRYST1 and the packing
        # region. ``add_box_sides`` would otherwise create an implicit box
        # from coordinate extrema and silently change the simulation volume.
        lines.append(f"pbc {box:.3f} {box:.3f} {box:.3f}")
        if cfg.seed >= 0:
            lines.append(f"seed {cfg.seed}")
        lines.append("")

        # 输出
        out_path = Path(cfg.output_dir) / f"{cfg.output_name}.pdb"
        lines.append(f'output {out_path}')

        for comp in cfg.components:
            lines.append(f'  structure {comp.pdb}')
            lines.append(f'    number {comp.count}')

            # 约束
            if comp.constraint == "inside_cube":
                # Packmol's ``inside cube`` takes one full edge length, not
                # a half-side length. It must match the explicit PBC above.
                lines.append(f"      inside cube 0. 0. 0. {box:.3f}")

            elif comp.constraint == "inside_box":
                lines.append(f"      inside box 0. 0. 0. {box:.3f} {box:.3f} {box:.3f}")

            elif comp.constraint == "fixed":
                x, y, z = comp.center
                lines.append(f"      fixed {x:.1f} {y:.1f} {z:.1f} 0. 0. 0.")

            lines.append(f"  end structure")

        lines.append("")
        return "\n".join(lines)

    # ── 写入 ──

    def write(self, path: Optional[str] = None) -> Path:
        """
        将生成的 .inp 写入文件。

        Args:
            path: .inp 保存路径。默认为 {output_dir}/{output_name}.inp

        Returns:
            写入的文件路径
        """
        if path is None:
            path = str(Path(self.config.output_dir) / f"{self.config.output_name}.inp")

        self._inp_path = Path(path)
        content = self.build()
        self._inp_path.write_text(content)
        print(f"[box] 已写入: {self._inp_path}")
        return self._inp_path

    # ── 调用 packmol ──

    def run(self, inp_path: Optional[str] = None) -> StepResult:
        """
        写入 .inp 并调用 packmol 生成盒子。

        Args:
            inp_path: .inp 路径。不传则自动生成。

        Returns:
            StepResult (success=True 时 outputs={"pdb": path, "inp": path})
        """
        import time as _time
        _start = _time.time()

        if inp_path is None:
            inp_path = self.write()

        self._inp_path = Path(inp_path)

        # Packmol seeks on its input stream; ``-i`` is required because a
        # piped stdin fails with "Illegal seek" in the bundled Fortran build.
        cmd = [self.config.packmol_bin, "-i", str(self._inp_path)]
        print(f"[box] 执行: {self.config.packmol_bin} -i {self._inp_path}")

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=Path(self._inp_path).parent or ".",
                timeout=300,
            )
        except subprocess.TimeoutExpired:
            return StepResult(
                step_name="box", step_index=PACKMOL_STEP, success=False,
                error=StepError(kind=ErrorKind.TIMEOUT,
                                message="Packmol 超时 (300s)",
                                hint="增大 tolerance 或降低密度以减少 packing 难度"),
                artifacts=[str(self._inp_path)],
                duration_s=_time.time() - _start,
            )
        except OSError as exc:
            return StepResult(
                step_name="box", step_index=PACKMOL_STEP, success=False,
                error=StepError(
                    kind=ErrorKind.DEPENDENCY_MISSING,
                    message=f"Packmol 无法启动: {exc}",
                    hint="确认 Packmol 可执行文件存在且已接入当前 PATH",
                ),
                artifacts=[str(self._inp_path)],
                duration_s=_time.time() - _start,
            )

        if result.returncode != 0:
            raw = result.stderr[-500:] if result.stderr else result.stdout[-500:]
            print(f"[box] ❌ packmol 出错:")
            print(result.stderr)
            return StepResult(
                step_name="box", step_index=PACKMOL_STEP, success=False,
                error=StepError(kind=ErrorKind.UNKNOWN,
                                message="Packmol 盒子构建失败",
                                raw_output=raw,
                                hint="增大 box_size、降低密度、或增大 tolerance"),
                artifacts=[str(self._inp_path)],
                duration_s=_time.time() - _start,
            )

        out_path = Path(self.config.output_dir) / f"{self.config.output_name}.pdb"
        if not out_path.is_file() or out_path.stat().st_size <= 0:
            return StepResult(
                step_name="box", step_index=PACKMOL_STEP, success=False,
                error=StepError(
                    kind=ErrorKind.ENGINE_FAILURE,
                    message="Packmol 返回成功但 model.pdb 缺失或为空",
                    hint="检查 Packmol 输出路径、磁盘空间和输入 .inp",
                ),
                artifacts=[str(self._inp_path)],
                duration_s=_time.time() - _start,
            )
        expected_atoms = sum(_pdb_atom_count(Path(component.pdb)) * component.count for component in self.config.components)
        actual_atoms = _pdb_atom_count(out_path)
        if expected_atoms <= 0 or actual_atoms != expected_atoms:
            return StepResult(
                step_name="box", step_index=PACKMOL_STEP, success=False,
                error=StepError(
                    kind=ErrorKind.INPUT_CONTRACT,
                    message=f"Packmol 原子数不一致: 期望 {expected_atoms}，实际 {actual_atoms}",
                    hint="检查各组分 PDB、residues 计数和 Packmol 输出",
                ),
                artifacts=[str(out_path), str(self._inp_path)],
                duration_s=_time.time() - _start,
            )
        try:
            requested = self.box_plan()
            actual_vectors, actual_angles = _pdb_cryst1(out_path)
        except ValueError as exc:
            return StepResult(
                step_name="box", step_index=PACKMOL_STEP, success=False,
                error=StepError(
                    kind=ErrorKind.INPUT_CONTRACT,
                    message="Packmol 输出缺少有效的周期盒矢量",
                    hint="检查 Packmol pbc 设置和输出 PDB 的 CRYST1 记录",
                    raw_output=str(exc),
                ),
                artifacts=[str(out_path), str(self._inp_path)],
                duration_s=_time.time() - _start,
            )
        if (
            any(abs(value - requested.box_size_angstrom) > _PBC_LENGTH_TOLERANCE_ANGSTROM
                for value in actual_vectors)
            or any(abs(angle - 90.0) > _PBC_ANGLE_TOLERANCE_DEGREES for angle in actual_angles)
        ):
            return StepResult(
                step_name="box", step_index=PACKMOL_STEP, success=False,
                error=StepError(
                    kind=ErrorKind.INPUT_CONTRACT,
                    message="Packmol 输出盒矢量与请求的周期盒不一致",
                    hint="请检查 Packmol pbc 设置，重新生成初始盒子",
                ),
                artifacts=[str(out_path), str(self._inp_path)],
                duration_s=_time.time() - _start,
            )
        actual_volume_nm3 = (
            actual_vectors[0] * actual_vectors[1] * actual_vectors[2] / 1000.0
        )
        actual_density = None
        if requested.total_mass_amu is not None:
            actual_density = _mass_density_g_cm3(
                requested.total_mass_amu, actual_volume_nm3,
            )
        parameters = {
            "box_strategy": requested.source,
            "target_mass_density_g_cm3": requested.target_mass_density_g_cm3,
            "packing_number_density_nm3": requested.packing_number_density_nm3,
            "total_mass_amu": requested.total_mass_amu,
            "requested_box_vectors_angstrom": [requested.box_size_angstrom] * 3,
            "actual_box_vectors_angstrom": list(actual_vectors),
            "actual_box_angles_degrees": list(actual_angles),
            # Retained names keep existing rollback provenance readers valid.
            "box_size_angstrom": sum(actual_vectors) / 3.0,
            "box_volume_nm3": actual_volume_nm3,
            "actual_mass_density_g_cm3": actual_density,
            "tolerance_angstrom": self.config.tolerance,
            "seed": self.config.seed,
            "expected_molecules": sum(component.count for component in self.config.components),
            "expected_atoms": expected_atoms,
            "actual_atoms": actual_atoms,
        }
        size_kb = out_path.stat().st_size / 1024
        print(
            f"[box] ✅ 盒子已生成: {out_path} ({size_kb:.1f} KB), "
            f"实际盒矢量={actual_vectors[0]:.3f}x{actual_vectors[1]:.3f}x{actual_vectors[2]:.3f} A"
        )
        print(result.stdout)

        duration = _time.time() - _start
        return StepResult(
            step_name="box", step_index=PACKMOL_STEP, success=True,
            outputs={"pdb": str(out_path), "inp": str(self._inp_path)},
            artifacts=[str(out_path), str(self._inp_path)],
            duration_s=duration,
            extra={"box_parameters": parameters},
        )


# ============================================================
# 快捷函数 —— 方便外部一行调用
# ============================================================

def generate_inp(components: List[Component],
                 output_name: str = "model",
                 box_size: Optional[float] = None,
                 tolerance: float = 2.0,
                 add_box_sides: float = 2.0,
                 output_dir: str = ".",
                 ) -> str:
    """
    快捷函数：给定组分列表，返回 .inp 内容字符串。

    用于 AI 直接拿到 inp 内容做进一步处理。
    """
    config = InpConfig(
        components=components,
        output_name=output_name,
        box_size=box_size,
        tolerance=tolerance,
        add_box_sides=add_box_sides,
        output_dir=output_dir,
    )
    gen = InpGenerator(config)
    return gen.build()


def quick_run(components: List[Component],
              output_name: str = "model",
              box_size: Optional[float] = None,
              output_dir: str = ".",
              ) -> subprocess.CompletedProcess:
    """
    快捷函数：生成 inp 并直接调 packmol。
    """
    config = InpConfig(
        components=components,
        output_name=output_name,
        box_size=box_size,
        output_dir=output_dir,
    )
    gen = InpGenerator(config)
    return gen.run()


# ============================================================
# 从 config.json 自动加载（与 top_assembly 同源）
# ============================================================


def _pdb_cryst1(path: Path) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """Read the PDB periodic cell written by Packmol's explicit ``pbc``."""
    try:
        for line in path.read_text(errors="replace").splitlines():
            if not line.startswith("CRYST1"):
                continue
            if len(line) < 54:
                raise ValueError("CRYST1 记录长度不足")
            lengths = tuple(float(line[start:end]) for start, end in ((6, 15), (15, 24), (24, 33)))
            angles = tuple(float(line[start:end]) for start, end in ((33, 40), (40, 47), (47, 54)))
            if any(value <= 0 for value in lengths):
                raise ValueError("CRYST1 盒矢量必须为正")
            return lengths, angles
    except OSError as exc:
        raise ValueError(f"无法读取 Packmol 输出: {exc}") from exc
    except ValueError as exc:
        raise ValueError(f"CRYST1 格式无效: {exc}") from exc
    raise ValueError("未找到 CRYST1 记录")


def _pdb_atom_count(path: Path) -> int:
    try:
        return sum(1 for line in path.read_text(errors="replace").splitlines()
                   if line.startswith(("ATOM", "HETATM")))
    except OSError:
        return 0


def _gro_atom_count(path: Path) -> int:
    try:
        return int(path.read_text(errors="replace").splitlines()[1].strip())
    except (OSError, IndexError, ValueError):
        return 0


def _gro_to_pdb(gro_path: str, pdb_path: str) -> Path:
    """gmx editconf: .gro → .pdb"""
    try:
        gmx = require_tool("gmx")
        result = subprocess.run(
            [str(gmx.executable), "editconf", "-f", gro_path, "-o", pdb_path],
            capture_output=True, text=True, timeout=30, env=build_tool_env("gmx"),
        )
    except EnvironmentRegistryError as exc:
        raise RuntimeError(f"GROMACS 不可用: {exc}") from exc
    out = Path(pdb_path)
    expected_atoms = _gro_atom_count(Path(gro_path))
    actual_atoms = _pdb_atom_count(out)
    if result.returncode != 0 or not out.is_file() or out.stat().st_size <= 0:
        detail = (result.stderr or result.stdout or "").strip()[-500:]
        raise RuntimeError(f"gmx editconf 转换失败: {detail}")
    if expected_atoms <= 0 or expected_atoms != actual_atoms:
        raise RuntimeError(f"gmx editconf 原子数不一致: 期望 {expected_atoms}，实际 {actual_atoms}")
    print(f"[box]   gmx editconf: {Path(gro_path).name} → {out.name}")
    return out


def validate_box_preflight(config_path: str | Path, workspace: str | Path) -> StepResult:
    """Validate residues, topology, ITP includes, coordinates, and total charge."""
    config_file = Path(config_path)
    directory = Path(workspace)
    try:
        data = json.loads(config_file.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return StepResult("box", PACKMOL_STEP, False, error=StepError(ErrorKind.INPUT_CONTRACT, f"无法读取配置: {exc}"))
    residues = data.get("residues", {})
    molecules = data.get("molecules", {})
    box = data.get("box", {})
    if not isinstance(residues, dict) or not residues:
        return StepResult("box", PACKMOL_STEP, False, error=StepError(ErrorKind.INPUT_CONTRACT, "residues 不能为空"))
    if isinstance(box, dict) and "density" in box:
        return StepResult(
            "box", PACKMOL_STEP, False,
            error=StepError(ErrorKind.INPUT_CONTRACT, "旧 box.density 不可执行；请迁移为 packing_number_density_nm3"),
        )
    topol = directory / "topol.top"
    if not topol.is_file() or topol.stat().st_size <= 0:
        return StepResult("box", PACKMOL_STEP, False, error=StepError(ErrorKind.INPUT_CONTRACT, "缺少非空 topol.top"))
    topol_molecules = _topol_molecules(topol)
    if topol_molecules != {name: int(count) for name, count in residues.items()}:
        return StepResult(
            "box", PACKMOL_STEP, False,
            error=StepError(
                ErrorKind.INPUT_CONTRACT,
                f"topol.top [ molecules ] 与 residues 不一致: {topol_molecules} != {residues}",
            ),
        )
    missing = []
    for include in _topol_includes(topol):
        if not (directory / include).is_file():
            missing.append(include)
    for name in residues:
        if name not in molecules:
            missing.append(f"molecules.{name}")
        if not ((directory / f"{name}.pdb").is_file() or (directory / f"{name}.gro").is_file()):
            missing.append(f"{name}.pdb/.gro")
    if missing:
        return StepResult(
            "box", PACKMOL_STEP, False,
            error=StepError(ErrorKind.INPUT_CONTRACT, "建盒前置文件不完整: " + ", ".join(missing)),
        )
    try:
        charge = sum(float(residues[name]) * float(molecules[name].get("charge", 0)) for name in residues)
    except (TypeError, ValueError, AttributeError):
        charge = 0.0
    compensation = data.get("ion_compensation", {})
    confirmed = data.get("non_neutral_confirmed") is True or (
        isinstance(compensation, dict) and bool(compensation.get("confirmed") or compensation.get("strategy"))
    )
    if abs(charge) > 1e-8 and not confirmed:
        return StepResult(
            "box", PACKMOL_STEP, False,
            error=StepError(ErrorKind.INPUT_CONTRACT, f"体系总电荷 {charge:+g}，需确认非中性体系或提供补偿离子方案"),
        )
    return StepResult(
        "box", PACKMOL_STEP, True,
        extra={"preflight": {"total_charge": charge, "topol_molecules": topol_molecules}},
    )


def _topol_includes(topol: Path) -> list[str]:
    return [match.group(1) for line in topol.read_text(errors="replace").splitlines()
            if (match := re.match(r'^\s*#include\s+"([^"]+)"', line))]


def _topol_molecules(topol: Path) -> dict[str, int]:
    result: dict[str, int] = {}
    in_molecules = False
    for raw in topol.read_text(errors="replace").splitlines():
        line = raw.split(";", 1)[0].strip()
        if not line:
            continue
        if line.startswith("["):
            in_molecules = line.strip("[] ").lower() == "molecules"
            continue
        if in_molecules:
            parts = line.split()
            if len(parts) >= 2:
                try:
                    result[parts[0]] = int(parts[1])
                except ValueError:
                    continue
    return result


def _itp_molecular_mass_amu(path: Path) -> float:
    """Sum one residue's `[ atoms ]` masses from its run-local topology."""
    if not path.is_file():
        raise ValueError(f"缺少组分拓扑质量来源: {path.name}")
    in_atoms = False
    masses: list[float] = []
    for raw in path.read_text(errors="replace").splitlines():
        line = raw.split(";", 1)[0].strip()
        if not line:
            continue
        if line.startswith("["):
            in_atoms = line.strip("[] ").lower() == "atoms"
            continue
        if not in_atoms:
            continue
        parts = line.split()
        if len(parts) < 8:
            raise ValueError(f"{path.name} 的 [ atoms ] 缺少质量列")
        try:
            mass = float(parts[7])
        except ValueError as exc:
            raise ValueError(f"{path.name} 的 [ atoms ] 质量列无效") from exc
        if mass <= 0:
            raise ValueError(f"{path.name} 的 [ atoms ] 质量必须为正")
        masses.append(mass)
    if not masses:
        raise ValueError(f"{path.name} 缺少可用的 [ atoms ] 质量")
    return sum(masses)


def auto_from_config(config_path: str = "config.json",
                     gro_dir: str | None = None,
                     pdb_dir: str | None = None,
                     output_name: str = "model",
                     output_dir: str = ".",
                     ) -> InpConfig:
    """
    从 config.json 的 residues 读取组分和个数，自动构建 InpConfig。

    对每个残基，确保 .pdb 存在（从当前 run 的 ``gro_dir`` 转换）。

    Args:
        config_path: config.json 路径
        gro_dir: 当前 run 内 .gro 文件所在目录（必填）
        pdb_dir: 当前 run 内 .pdb 输出目录（必填）
        output_name: box 输出文件名
        output_dir: box 输出目录

    Returns:
        InpConfig，可直接传给 InpGenerator
    """
    if not gro_dir or not pdb_dir:
        raise ValueError("auto_from_config 必须提供当前 run 的 gro_dir 和 pdb_dir")

    ROOT = get_project_root()
    # 解析相对路径（相对于项目根目录）
    _config_path = Path(config_path)
    if not _config_path.is_absolute():
        _config_path = ROOT / config_path
    _gro_dir = Path(gro_dir)
    if not _gro_dir.is_absolute():
        _gro_dir = ROOT / gro_dir
    _pdb_dir = Path(pdb_dir)
    if not _pdb_dir.is_absolute():
        _pdb_dir = ROOT / pdb_dir

    with open(_config_path) as f:
        data = json.load(f)

    residues = data.get("residues", {})
    if not residues:
        raise ValueError("config.json 中 residues 为空")

    components = []
    for name, count in residues.items():
        pdb_path = _pdb_dir / f"{name}.pdb"
        gro_path = _gro_dir / f"{name}.gro"

        # 确保 .pdb 存在
        if not pdb_path.exists():
            if gro_path.exists():
                _gro_to_pdb(str(gro_path), str(pdb_path))
            else:
                raise FileNotFoundError(
                    f"{name}: 找不到 {pdb_path} 或 {gro_path}，"
                    f"请先运行 topo_gaff"
                )

        molecular_mass = _itp_molecular_mass_amu(_gro_dir / f"{name}.itp")
        components.append(Component(
            pdb=str(pdb_path), count=int(count), residue_name=name,
            molecular_mass_amu=molecular_mass,
        ))

    print(f"[box] 从 config.json 加载 {len(components)} 个组分")

    box = data.get("box", {})
    if not isinstance(box, dict):
        raise ValueError("config.json 中 box 必须是对象")
    if "density" in box:
        raise ValueError("旧字段 box.density 不可执行；请先显式迁移配置")
    target_mass_density = box.get("target_mass_density_g_cm3")
    legacy_density = box.get("packing_number_density_nm3")
    if target_mass_density is not None:
        target_mass_density = float(target_mass_density)
        if target_mass_density <= 0:
            raise ValueError("box.target_mass_density_g_cm3 必须为正")
    elif legacy_density is not None:
        legacy_density = float(legacy_density)
        if legacy_density <= 0:
            raise ValueError("box.packing_number_density_nm3 必须为正")
    else:
        target_mass_density = DEFAULT_TARGET_MASS_DENSITY_G_CM3
    return InpConfig(
        components=components,
        output_name=output_name,
        output_dir=output_dir,
        box_size=box.get("box_size"),
        tolerance=float(box.get("tolerance", 2.0)),
        add_box_sides=box.get("add_box_sides"),
        seed=int(box.get("seed", data.get("md", {}).get("run_seed", -1))),
        target_mass_density_g_cm3=target_mass_density,
        packing_number_density_nm3=legacy_density,
    )


# ============================================================
# 测试入口
# ============================================================

if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        raise SystemExit("用法: python -m willy.simulation.box md_run/<run_id>")
    run_dir = Path(sys.argv[1]).resolve()
    print("=" * 60)
    print("测试: 从 run 内 config.json 读取组分 → 生成 .inp → packmol")
    print("=" * 60)

    config = auto_from_config(
        config_path=str(run_dir / "config.json"),
        gro_dir=str(run_dir),
        pdb_dir=str(run_dir),
        output_dir=str(run_dir),
    )
    gen = InpGenerator(config)

    print("\n── 生成的 .inp 内容 ──")
    print(gen.build())

    print("\n── 执行 packmol ──")
    result = gen.run()
