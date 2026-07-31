"""
box.py
===============
Packmol .inp 文件生成器。

入参全部封装为数据类，方便后续接入 AI 模块。
组分来源统一从 config.json 的 residues 读取，与 top_assembly 保持一致。
"""

from dataclasses import dataclass, field
from typing import List, Literal, Optional
from pathlib import Path
import subprocess
import math
import json

from willy._paths import get_project_root
from willy.errors import StepResult, StepError, ErrorKind


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
    add_box_sides: float = 2.0               # 盒子每边外扩 (Å)，用于后续设 PBC
    output_name: str = "model"               # 输出文件名（不含扩展名）
    output_dir: str = "."                    # 输出目录
    seed: int = -1                           # 随机种子，-1 表示随机

    # —— 盒子尺寸 ——
    box_size: Optional[float] = None         # 若设了，直接使用（Å）；None 则按 ∛N×17 估算

    # —— 其他 ——
    packmol_bin: str = str(get_project_root() / "vendor" / "packmol")


# ============================================================
# 盒子尺寸估算（硬编码公式）
# ============================================================

def estimate_box_size(components: List[Component]) -> float:
    """
    硬编码估算盒子边长。

    公式: box = ∛(N / 4) × 10 (Å)，向上取整
    密度: ~6 分子/nm³（离子液体/有机体系）
    """
    total = sum(c.count for c in components)
    vol_nm3 = total / 6.0                # 体积 (nm³)
    side_nm = vol_nm3 ** (1 / 3)         # 边长 (nm)
    box = math.ceil(side_nm * 10)        # → Å，向上取整
    print(f"[estimate] 总分子数={total}, 体积={vol_nm3:.1f} nm³, "
          f"边长={side_nm:.2f} nm → box={box} Å")
    return float(box)


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

    # ── 内容生成 ──

    def build(self) -> str:
        """根据配置生成完整的 .inp 内容字符串。"""
        cfg = self.config
        # 未指定 box_size 时硬编码估算
        box = cfg.box_size
        if box is None:
            box = estimate_box_size(cfg.components)
        lines = []

        # 全局设置
        lines.append(f"tolerance {cfg.tolerance}")
        lines.append(f"filetype {cfg.filetype}")
        if box is not None:
            lines.append(f"add_box_sides {cfg.add_box_sides}")
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
                if box is not None:
                    h = box / 2.0
                    lines.append(f"      inside cube 0. 0. 0. {h:.1f} {h:.1f} {h:.1f}")
                else:
                    cx, cy, cz = comp.center
                    dx, dy, dz = comp.half_sides
                    lines.append(f"      inside cube {cx:.1f} {cy:.1f} {cz:.1f} {dx:.1f} {dy:.1f} {dz:.1f}")

            elif comp.constraint == "inside_box":
                if box is not None:
                    lines.append(f"      inside box 0. 0. 0. {box:.1f} {box:.1f} {box:.1f}")
                else:
                    cx, cy, cz = comp.center
                    dx, dy, dz = comp.half_sides
                    lines.append(f"      inside box {cx - dx:.1f} {cy - dy:.1f} {cz - dz:.1f} "
                                 f"{cx + dx:.1f} {cy + dy:.1f} {cz + dz:.1f}")

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

        cmd = f"{self.config.packmol_bin} < {self._inp_path}"
        print(f"[box] 执行: {cmd}")

        try:
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                cwd=Path(self._inp_path).parent or ".",
                timeout=300,
            )
        except subprocess.TimeoutExpired:
            return StepResult(
                step_name="box", step_index=7, success=False,
                error=StepError(kind=ErrorKind.TIMEOUT,
                                message="Packmol 超时 (300s)",
                                hint="增大 tolerance 或降低密度以减少 packing 难度"),
                artifacts=[str(self._inp_path)],
                duration_s=_time.time() - _start,
            )

        if result.returncode != 0:
            raw = result.stderr[-500:] if result.stderr else result.stdout[-500:]
            print(f"[box] ❌ packmol 出错:")
            print(result.stderr)
            return StepResult(
                step_name="box", step_index=7, success=False,
                error=StepError(kind=ErrorKind.UNKNOWN,
                                message="Packmol 盒子构建失败",
                                raw_output=raw,
                                hint="增大 box_size、降低密度、或增大 tolerance"),
                artifacts=[str(self._inp_path)],
                duration_s=_time.time() - _start,
            )

        out_path = Path(self.config.output_dir) / f"{self.config.output_name}.pdb"
        if out_path.exists():
            size_kb = out_path.stat().st_size / 1024
            print(f"[box] ✅ 盒子已生成: {out_path} ({size_kb:.1f} KB)")
        print(result.stdout)

        duration = _time.time() - _start
        return StepResult(
            step_name="box", step_index=7, success=True,
            outputs={"pdb": str(out_path), "inp": str(self._inp_path)},
            artifacts=[str(out_path), str(self._inp_path)],
            duration_s=duration,
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

GMX_BIN = "gmx"


def _gro_to_pdb(gro_path: str, pdb_path: str) -> Path:
    """gmx editconf: .gro → .pdb"""
    subprocess.run(
        [GMX_BIN, "editconf", "-f", gro_path, "-o", pdb_path],
        capture_output=True, text=True, timeout=30,
    )
    out = Path(pdb_path)
    if out.exists():
        print(f"[box]   gmx editconf: {Path(gro_path).name} → {out.name}")
    return out


def auto_from_config(config_path: str = "config.json",
                     gro_dir: str = "topo",
                     pdb_dir: str = "topo",
                     output_name: str = "model",
                     output_dir: str = ".",
                     ) -> InpConfig:
    """
    从 config.json 的 residues 读取组分和个数，自动构建 InpConfig。

    对每个残基，确保 .pdb 存在（从 topo/{name}.gro 转换）。

    Args:
        config_path: config.json 路径
        gro_dir: .gro 文件所在目录
        pdb_dir: .pdb 输出目录
        output_name: box 输出文件名
        output_dir: box 输出目录

    Returns:
        InpConfig，可直接传给 InpGenerator
    """
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

        components.append(Component(pdb=str(pdb_path), count=count))

    print(f"[box] 从 config.json 加载 {len(components)} 个组分")

    return InpConfig(
        components=components,
        output_name=output_name,
        output_dir=output_dir,
    )


# ============================================================
# 测试入口
# ============================================================

if __name__ == "__main__":
    print("=" * 60)
    print("测试: 从 config.json 读取组分 → 生成 .inp → packmol")
    print("=" * 60)

    config = auto_from_config("config.json", output_dir=".")
    gen = InpGenerator(config)

    print("\n── 生成的 .inp 内容 ──")
    print(gen.build())

    print("\n── 执行 packmol ──")
    result = gen.run()
