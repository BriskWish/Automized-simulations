"""
fchk_mol2.py
============
统一的 fchk → mol2 转换 (G16 + G09 + ORCA 共用)。

输入: *_opt.fchk (G16、G09 或 ORCA 的 TZ SP 结果)
输出: {name}.mol2

纯 Python 解析，零外部依赖。
"""

from pathlib import Path
from typing import List
import json

from willy.errors import StepResult, StepError, ErrorKind

# ── 原子序数 → 元素符号 ──
_ATOMIC_SYMBOLS = {
    1:"H",2:"He",3:"Li",4:"Be",5:"B",6:"C",7:"N",8:"O",9:"F",10:"Ne",
    11:"Na",12:"Mg",13:"Al",14:"Si",15:"P",16:"S",17:"Cl",18:"Ar",
    19:"K",20:"Ca",21:"Sc",22:"Ti",23:"V",24:"Cr",25:"Mn",26:"Fe",
    27:"Co",28:"Ni",29:"Cu",30:"Zn",31:"Ga",32:"Ge",33:"As",34:"Se",
    35:"Br",36:"Kr",37:"Rb",38:"Sr",39:"Y",40:"Zr",41:"Nb",42:"Mo",
    43:"Tc",44:"Ru",45:"Rh",46:"Pd",47:"Ag",48:"Cd",49:"In",50:"Sn",
    51:"Sb",52:"Te",53:"I",54:"Xe",55:"Cs",56:"Ba",57:"La",72:"Hf",
    73:"Ta",74:"W",75:"Re",76:"Os",77:"Ir",78:"Pt",79:"Au",80:"Hg",
    81:"Tl",82:"Pb",83:"Bi",
}


def _parse_fchk_block(lines: List[str], header: str, dtype: str,
                      count: int) -> List:
    for i, line in enumerate(lines):
        if line.strip().startswith(header):
            values = []
            j = i + 1
            while len(values) < count and j < len(lines):
                tokens = lines[j].split()
                if not tokens:
                    j += 1; continue
                if not tokens[0].replace("-","").replace("+","").replace("E","").replace(".","").isdigit():
                    break
                values.extend(tokens)
                j += 1
            if dtype == "I": return [int(v) for v in values[:count]]
            elif dtype == "R": return [float(v) for v in values[:count]]
            else: return values[:count]
    return []


def _parse_fchk_scalar(lines: List[str], header: str) -> int:
    """读取 fchk 标量字段，例如 ``MxBond I 4``。"""
    for line in lines:
        if line.strip().startswith(header):
            try:
                return int(line.split()[-1])
            except (IndexError, ValueError) as exc:
                raise ValueError(f"无法解析 {header} 字段") from exc
    raise ValueError(f"缺少 {header} 字段")


def _require_block(name: str, values: List, expected_count: int) -> None:
    """确保 fchk 数组字段完整，避免生成不含真实键连接的 mol2。"""
    if len(values) != expected_count:
        raise ValueError(
            f"{name} 数据不完整：期望 {expected_count} 项，实际 {len(values)} 项"
        )


def parse_fchk(fchk_path: str) -> dict:
    lines = Path(fchk_path).read_text().split("\n")
    for line in lines:
        if line.strip().startswith("Number of atoms"):
            natoms = int(line.split()[-1]); break
    else:
        raise ValueError(f"无法解析原子数: {fchk_path}")
    if natoms <= 0:
        raise ValueError(f"原子数必须大于零: {fchk_path}")

    mxbond = _parse_fchk_scalar(lines, "MxBond")
    if mxbond <= 0:
        raise ValueError(f"MxBond 必须大于零: {fchk_path}")

    atomic_numbers = _parse_fchk_block(lines, "Atomic numbers", "I", natoms)
    coords_raw = _parse_fchk_block(lines, "Current cartesian coordinates", "R", natoms * 3)
    nbonds = _parse_fchk_block(lines, "NBond", "I", natoms)
    ibond_raw = _parse_fchk_block(lines, "IBond", "I", natoms * mxbond)
    rbond_raw = _parse_fchk_block(lines, "RBond", "R", natoms * mxbond)

    _require_block("Atomic numbers", atomic_numbers, natoms)
    _require_block("Current cartesian coordinates", coords_raw, natoms * 3)
    _require_block("NBond", nbonds, natoms)
    _require_block("IBond", ibond_raw, natoms * mxbond)
    _require_block("RBond", rbond_raw, natoms * mxbond)

    coords = [(coords_raw[i*3], coords_raw[i*3+1], coords_raw[i*3+2]) for i in range(natoms)]
    bond_targets, bond_orders = [], []
    for atom_index, bond_count in enumerate(nbonds):
        if bond_count < 0 or bond_count > mxbond:
            raise ValueError(
                f"NBond[{atom_index}]={bond_count} 不在 0..{mxbond} 范围内"
            )

        start = atom_index * mxbond
        targets = ibond_raw[start:start + mxbond]
        orders = rbond_raw[start:start + mxbond]
        active_bonds = [(target, order) for target, order in zip(targets, orders)
                        if target > 0]
        if len(active_bonds) != bond_count:
            raise ValueError(
                f"NBond[{atom_index}]={bond_count} 与 IBond 连接数不一致"
            )
        if any(target > natoms for target, _ in active_bonds):
            raise ValueError(f"IBond 包含超出原子范围的连接: atom {atom_index + 1}")

        bond_targets.append([target - 1 for target, _ in active_bonds])
        bond_orders.append([order for _, order in active_bonds])

    return {"natoms": natoms, "atomic_numbers": atomic_numbers, "coords": coords,
            "nbonds_per_atom": nbonds, "bond_targets": bond_targets, "bond_orders": bond_orders}


def _bond_order_to_mol2_type(order: float) -> str:
    if order >= 2.5: return "3"
    elif order >= 1.8: return "2"
    elif order >= 1.3: return "ar"
    else: return "1"


def build_mol2(data: dict, name: str) -> str:
    lines = [f"# {name}", "# Created by fchk_mol2.py", "#", ""]
    bonds, bond_types = [], []
    for i, targets in enumerate(data["bond_targets"]):
        for j, order in zip(targets, data["bond_orders"][i]):
            if i < j:
                bonds.append((i, j))
                bond_types.append(_bond_order_to_mol2_type(order))
    lines.append("@<TRIPOS>MOLECULE")
    lines.append(name)
    lines.append(f"{data['natoms']} {len(bonds)}")
    lines.append("SMALL\nNO_CHARGES\n")
    lines.append("@<TRIPOS>ATOM")
    for i in range(data["natoms"]):
        z = data["atomic_numbers"][i]
        sym = _ATOMIC_SYMBOLS.get(z, f"X{z}")
        x, y, zz = data["coords"][i]
        lines.append(f"{i+1:>4d} {sym}{i+1:<5d} {x:12.4f} {y:12.4f} {zz:12.4f} {sym:<4s}")
    lines.append("@<TRIPOS>BOND")
    for bidx, ((a, b), btype) in enumerate(zip(bonds, bond_types), 1):
        lines.append(f"{bidx:>5d} {a+1:>5d} {b+1:>5d} {btype:>4s}")
    return "\n".join(lines) + "\n"


def convert(fchk_path: str, output_path: str = None) -> StepResult:
    """单个 *_opt.fchk → .mol2。"""
    import time
    t0 = time.time()
    name = Path(fchk_path).stem.replace("_opt", "")

    if not Path(fchk_path).exists():
        return StepResult(step_name="fchk_mol2", step_index=2, success=False,
                          error=StepError(kind=ErrorKind.FILE_NOT_FOUND,
                                          message=f"{fchk_path} 不存在"),
                          duration_s=time.time()-t0)

    try:
        data = parse_fchk(fchk_path)
        mol2_content = build_mol2(data, name)

        if output_path is None:
            output_path = str(Path(fchk_path).parent / f"{name}.mol2")
        out = Path(output_path)
        out.write_text(mol2_content)
    except (OSError, ValueError, IndexError) as exc:
        return StepResult(
            step_name="fchk_mol2", step_index=2, success=False,
            error=StepError(
                kind=ErrorKind.UNKNOWN,
                message=f"{name}: fchk→mol2 转换失败: {exc}",
                hint="确认 *_opt.fchk 完整且包含原子、坐标和键连接信息。",
            ),
            duration_s=time.time() - t0,
        )

    print(f"[fchk_mol2] ✅ {name}: {data['natoms']} atoms → {out}")

    return StepResult(step_name="fchk_mol2", step_index=2, success=True,
                      outputs={"mol2": str(out)}, artifacts=[str(out)],
                      duration_s=time.time()-t0, extra={"natoms": data["natoms"]})


def batch_convert(struct_dir: str = "struct", config_path: str = "config.json",
                  on_progress=None) -> list[StepResult]:
    """批量: struct/ 下 *_opt.fchk → .mol2（仅处理 config.json 中注册的分子）。"""
    results: list[StepResult] = []
    try:
        with open(config_path) as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}
    registered = set(cfg.get("molecules", {}).keys())
    fchks = [f for f in sorted(Path(struct_dir).glob("*_opt.fchk"))
             if f.stem.replace("_opt", "") in registered or not registered]
    if not fchks:
        print(f"[fchk_mol2] ⚠ {struct_dir}/ 下没有 *_opt.fchk")
        return [StepResult(
            step_name="fchk_mol2", step_index=2, success=False,
            error=StepError(
                kind=ErrorKind.FILE_NOT_FOUND,
                message=f"{struct_dir}/ 下没有可转换的 *_opt.fchk",
                hint="确认本次运行的 Step 2 已生成完整 FCHK 文件。",
            ),
        )]
    total = len(fchks)
    for i, fchk in enumerate(fchks, 1):
        name = fchk.stem.replace("_opt", "")
        if on_progress:
            on_progress({
                "tool": "格式转换", "operation": "mol2 转换",
                "target_type": "molecule", "target": name,
                "current": i, "total": total,
            })
        sr = convert(str(fchk))
        sr.target_type = "molecule"
        sr.target = name
        results.append(sr)
    ok = sum(1 for r in results if r.success)
    print(f"[fchk_mol2] 完成: {ok}/{total} 个 .mol2")
    return results
