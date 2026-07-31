"""
mdp.py
============
GROMACS .mdp 文件生成器。

每次运行批量生成 em.mdp / eq.mdp / prod.mdp → ./process/

入参从 config.json 的 md 段读取，方便后续 AI 修改。
"""

from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Collection, Optional
import json
import math

from willy._paths import get_project_root
from willy.errors import StepResult, StepError, ErrorKind


# ── 常量（不随 config 变的固有值） ──
ROOT = get_project_root()
PROCESS_DIR = ROOT / "process"


# ============================================================
# 入参层
# ============================================================

@dataclass
class MdpConfig:
    """MD 参数配置。从 config.json 的 md 段读取。"""
    dt: float = 0.001               # 时间步长 (ps)
    ref_t: float = 298.15           # 参考温度 (K)
    ref_p: float = 1.01325          # 参考压力 (bar)
    eq_ns: float = 10.0             # 平衡时长 (ns)
    prod_ns: float = 10.0           # 产出时长 (ns)
    tcoupl: str = "V-rescale"       # 热浴算法
    tau_t: float = 0.5              # 热浴耦合常数 (ps)
    pcoupl: str = "C-rescale"       # 压浴算法
    pcoupltype: str = "isotropic"   # 压力耦合类型
    compressibility: str = "8.5e-5" # 压缩系数 (bar⁻¹)
    constraints: str = "hbonds"     # 约束算法
    rcoulomb: float = 1.0           # 库仑截断 (nm)
    rvdw: float = 1.0               # VDW 截断 (nm)
    coulombtype: str = "PME"        # 长程静电算法
    vdwtype: str = "Cut-off"        # VDW 类型

    # 产出阶段的热浴/压浴参数（可能与 eq 不同）
    tau_p_prod: float = 2.0         # prod 阶段 τ_p（比 eq 宽松）


def load_mdp_config(config_path: str = "config.json") -> MdpConfig:
    """从 config.json 读取 MD 参数。"""
    with open(config_path) as f:
        data = json.load(f)
    md = data.get("md", {})
    return MdpConfig(
        dt=md.get("dt", 0.001),
        ref_t=md.get("ref_t", 298.15),
        ref_p=md.get("ref_p", 1.01325),
        eq_ns=md.get("eq_ns", 10.0),
        prod_ns=md.get("prod_ns", 10.0),
        tcoupl=md.get("tcoupl", "V-rescale"),
        tau_t=md.get("tau_t", 0.5),
        pcoupl=md.get("pcoupl", "C-rescale"),
        pcoupltype=md.get("pcoupltype", "isotropic"),
        compressibility=str(md.get("compressibility", "8.5e-5")),
        constraints=md.get("constraints", "hbonds"),
        rcoulomb=md.get("rcoulomb", 1.0),
        rvdw=md.get("rvdw", 1.0),
        coulombtype=md.get("coulombtype", "PME"),
        vdwtype=md.get("vdwtype", "Cut-off"),
        tau_p_prod=md.get("tau_p_prod", 2.0),
    )


# ============================================================
# 各阶段模板
# ============================================================

def _common_preamble(cfg: MdpConfig) -> str:
    """EM/EQ/PROD 的公共头部（PBC + 静电 + VDW）。"""
    return f"""pbc = xyz
cutoff-scheme = Verlet
coulombtype   = {cfg.coulombtype}
rcoulomb      = {cfg.rcoulomb}
vdwtype       = {cfg.vdwtype}
rvdw          = {cfg.rvdw}
DispCorr      = EnerPres"""


def _build_em(cfg: MdpConfig) -> str:
    """能量最小化 .mdp。"""
    return f"""
integrator = cg
nsteps = 10000
emtol  = 100.0
emstep = 0.01
;
nstxout   = 100
nstlog    = 50
nstenergy = 50
;
{_common_preamble(cfg)}
;
constraints = none
"""


def _build_eq(cfg: MdpConfig) -> str:
    """NPT 平衡 .mdp（含退火）。"""
    nsteps = int(cfg.eq_ns * 1000 / cfg.dt)  # ns → ps → steps

    return f"""define =
integrator = md

dt         = {cfg.dt}
nsteps     = {nsteps}
comm-grps  = system
energygrps =
;
nstxout = 0
nstvout = 0
nstfout = 0
nstlog  = 500
nstenergy = 500
nstxout-compressed = 1000
compressed-x-grps  = system
;
annealing = single
annealing_npoints = 7
annealing_time = 0 1000 3000 4000 6000 7000 12000
annealing_temp = 298 500 500 400 400 298 298
;
{_common_preamble(cfg)}
;
Tcoupl  = {cfg.tcoupl}
tau_t   = {cfg.tau_t}
tc_grps = system
ref_t   = {cfg.ref_t}
;

Pcoupl     = {cfg.pcoupl}
pcoupltype = {cfg.pcoupltype}
tau_p = 1
ref_p = {cfg.ref_p}
compressibility = {cfg.compressibility}
;
gen_vel  = no
gen_temp = {cfg.ref_t}
gen_seed = -1
;
freezegrps  =
freezedim   =
constraints = {cfg.constraints}
"""


def _build_prod(cfg: MdpConfig) -> str:
    """产出阶段 .mdp（无退火，tau_p 更宽松）。"""
    nsteps = int(cfg.prod_ns * 1000 / cfg.dt)

    return f"""define =
integrator = md

dt         = {cfg.dt}
nsteps     = {nsteps}
comm-grps  = system
energygrps =
;
nstxout = 0
nstvout = 0
nstfout = 0
nstlog  = 500
nstenergy = 500
nstxout-compressed = 1000
compressed-x-grps  = system
;
{_common_preamble(cfg)}
;
Tcoupl  = {cfg.tcoupl}
tau_t   = {cfg.tau_t}
tc_grps = system
ref_t   = {cfg.ref_t}
;

Pcoupl     = {cfg.pcoupl}
pcoupltype = {cfg.pcoupltype}
tau_p = {cfg.tau_p_prod}
ref_p = {cfg.ref_p}
compressibility = {cfg.compressibility}
;
gen_vel  = no
gen_temp = {cfg.ref_t}
gen_seed = -1
;
freezegrps  =
freezedim   =
constraints = {cfg.constraints}
"""


# ============================================================
# 批量生成
# ============================================================

_BUILDERS = {
    "em":   ("em.mdp",   _build_em),
    "eq":   ("eq.mdp",   _build_eq),
    "prod": ("prod.mdp", _build_prod),
}


def build_all(config_path: str = "config.json",
              output_dir: str = "process",
              overrides: dict = None,
              stages: Collection[str] | None = None) -> StepResult:
    """
    批量生成 em.mdp / eq.mdp / prod.mdp。

    Args:
        config_path: config.json 路径
        output_dir: 输出目录
        overrides: 参数字典，覆盖 config.json 中的对应字段（供 Agent 重试用）
        stages: 要生成的阶段；None 表示生成 em、eq、prod 全部阶段

    Returns:
        StepResult (success=True 时 outputs={"em": path, "eq": path, "prod": path})
    """
    import time as _time
    _start = _time.time()

    try:
        cfg = load_mdp_config(config_path)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        return StepResult(
            step_name="mdp", step_index=6, success=False,
            error=StepError(kind=ErrorKind.CONFIG_INVALID,
                            message=f"无法读取 config.json: {e}",
                            hint="检查 config.json 格式是否正确"),
            duration_s=_time.time() - _start,
        )

    # 应用参数覆盖
    if overrides:
        for k, v in overrides.items():
            if hasattr(cfg, k):
                setattr(cfg, k, v)

    selected_stages = tuple(_BUILDERS) if stages is None else tuple(stages)
    unknown_stages = set(selected_stages) - _BUILDERS.keys()
    if not selected_stages or unknown_stages:
        invalid = ", ".join(sorted(unknown_stages)) or "空阶段列表"
        return StepResult(
            step_name="mdp", step_index=6, success=False,
            error=StepError(kind=ErrorKind.CONFIG_INVALID,
                            message=f"未知 MDP 阶段: {invalid}",
                            hint="可用阶段为 em、eq、prod"),
            duration_s=_time.time() - _start,
        )

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    results: dict[str, Path] = {}
    artifacts: list[str] = []
    for stage in selected_stages:
        fname, builder = _BUILDERS[stage]
        content = builder(cfg).lstrip("\n")
        path = out / fname
        path.write_text(content)
        results[stage] = path
        artifacts.append(str(path))
        nsteps = content.split("nsteps")[1].split()[0] if "nsteps" in content else "N/A"
        print(f"[mdp] ✅ {fname}  (nsteps → {nsteps})")

    duration = _time.time() - _start
    print(f"[mdp] 完成: {len(results)} 个 .mdp → {out}/  ({duration:.1f}s)")
    return StepResult(
        step_name="mdp", step_index=6, success=True,
        outputs={k: str(v) for k, v in results.items()},
        artifacts=artifacts,
        duration_s=duration,
    )


# ============================================================
# 测试入口
# ============================================================

if __name__ == "__main__":
    build_all("config.json", "process")
