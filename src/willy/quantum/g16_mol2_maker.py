"""
mol2_maker.py
=============
从 Gaussian .fchk 文件生成 .mol2 文件。

解析 .fchk 中的坐标、原子类型、键连信息，写出 Tripos .mol2 格式。
"""

from pathlib import Path
from typing import Tuple, Optional, List
import json
import math

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


# ============================================================
# .fchk 解析
# ============================================================

def _parse_fchk_block(lines: List[str], header: str, dtype: str,
                      count: int) -> List:
    """从 .fchk 内容中解析一个数据块（仅限纯数字数据行）。"""
    for i, line in enumerate(lines):
        if line.strip().startswith(header):
            values = []
            j = i + 1
            while len(values) < count and j < len(lines):
                tokens = lines[j].split()
                if not tokens:
                    j += 1
                    continue
                # 数据行以数字/-/+/E 开头；否则是下一个 section header，停止
                if not tokens[0].replace("-", "").replace("+", "").replace("E", "").replace(".", "").isdigit():
                    break
                values.extend(tokens)
                j += 1
            if dtype == "I":
                return [int(v) for v in values[:count]]
            elif dtype == "R":
                return [float(v) for v in values[:count]]
            else:
                return values[:count]
    return []


def parse_fchk(fchk_path: str) -> dict:
    """
    解析 .fchk 文件。

    Returns:
        {"natoms": int, "atomic_numbers": [int], "coords": [(x,y,z)],
         "nbonds_per_atom": [int], "bond_targets": [[int]], "bond_orders": [float]}
    """
    lines = Path(fchk_path).read_text().split("\n")

    # 原子数
    for line in lines:
        if line.strip().startswith("Number of atoms"):
            natoms = int(line.split()[-1])
            break
    else:
        raise ValueError(f"无法解析原子数: {fchk_path}")

    # MxBond —— 每原子最大键数（IBond/RBond 以此对齐）
    mxbond_raw = _parse_fchk_block(lines, "MxBond", "I", 1)
    mxbond = mxbond_raw[0] if mxbond_raw else 4  # 默认 4

    # 各种数据块
    atomic_numbers = _parse_fchk_block(lines, "Atomic numbers", "I", natoms)
    coords_raw = _parse_fchk_block(lines, "Current cartesian coordinates", "R", natoms * 3)
    nbonds = _parse_fchk_block(lines, "NBond", "I", natoms)
    # IBond/RBond 按 MxBond 填槽，读取全部后取每原子前 nbonds 个有效值
    ibond_raw = _parse_fchk_block(lines, "IBond", "I", natoms * mxbond)
    rbond_raw = _parse_fchk_block(lines, "RBond", "R", natoms * mxbond)

    # 坐标重组为 (x,y,z) 元组
    coords = [(coords_raw[i*3], coords_raw[i*3+1], coords_raw[i*3+2])
              for i in range(natoms)]

    # 键连重组（每原子前 nbonds[i] 个是有效键，0 表示空缺）
    bond_targets = []
    bond_orders = []
    cursor = 0
    for i, n in enumerate(nbonds):
        # 取 mxbond 个槽位中的前 n 个有效值
        targets = ibond_raw[cursor:cursor + n]
        orders  = rbond_raw[cursor:cursor + n]
        bond_targets.append([t - 1 for t in targets if t > 0])  # Fortran 1-indexed→0
        bond_orders.extend(orders[:len(bond_targets[-1])])
        cursor += mxbond

    return {
        "natoms": natoms,
        "atomic_numbers": atomic_numbers,
        "coords": coords,
        "nbonds_per_atom": nbonds,
        "bond_targets": bond_targets,
        "bond_orders": bond_orders,
    }


# ============================================================
# .mol2 生成
# ============================================================

def _bond_order_to_mol2_type(order: float) -> str:
    """键级 → mol2 bond type。"""
    if order >= 2.5: return "3"
    elif order >= 1.8: return "2"
    elif order >= 1.3: return "ar"
    else: return "1"


def build_mol2(data: dict, name: str) -> str:
    """
    从 parse_fchk 的数据生成 Tripos .mol2 内容。
    """
    lines = []
    lines.append(f"# {name}")
    lines.append("# Created by mol2_maker.py")
    lines.append("#")
    lines.append("")

    # 统计总键数（去重：只记 i<j）
    bonds = []
    bond_types = []
    b_idx = 0
    for i, targets in enumerate(data["bond_targets"]):
        for j in targets:
            if i < j:
                bonds.append((i, j))
                if b_idx < len(data["bond_orders"]):
                    bond_types.append(_bond_order_to_mol2_type(data["bond_orders"][b_idx]))
                else:
                    bond_types.append("1")
            b_idx += 1

    # @<TRIPOS>MOLECULE
    lines.append("@<TRIPOS>MOLECULE")
    lines.append(name)
    lines.append(f"{data['natoms']} {len(bonds)}")
    lines.append("SMALL")
    lines.append("NO_CHARGES")
    lines.append("")

    # @<TRIPOS>ATOM
    lines.append("@<TRIPOS>ATOM")
    for i in range(data["natoms"]):
        z = data["atomic_numbers"][i]
        sym = _ATOMIC_SYMBOLS.get(z, f"X{z}")
        x, y, zz = data["coords"][i]
        atom_name = f"{sym}{i+1}"
        lines.append(f"{i+1:>4d} {atom_name:<6s} "
                     f"{x:12.4f} {y:12.4f} {zz:12.4f} "
                     f"{sym:<4s}")

    # @<TRIPOS>BOND
    lines.append("@<TRIPOS>BOND")
    for bidx, ((a, b), btype) in enumerate(zip(bonds, bond_types), 1):
        lines.append(f"{bidx:>5d} {a+1:>5d} {b+1:>5d} {btype:>4s}")

    return "\n".join(lines) + "\n"


def fchk_to_mol2(fchk_path: str, output_path: str = None) -> StepResult:
    """单个 .fchk → .mol2。"""
    import time
    t0 = time.time()
    name = Path(fchk_path).stem

    if not Path(fchk_path).exists():
        return StepResult(step_name="mol2_maker", step_index=2, success=False,
                          error=StepError(kind=ErrorKind.FILE_NOT_FOUND,
                                          message=f"{fchk_path} 不存在"),
                          duration_s=time.time()-t0)

    data = parse_fchk(fchk_path)
    mol2_content = build_mol2(data, name)

    if output_path is None:
        output_path = str(Path(fchk_path).with_suffix(".mol2"))

    out = Path(output_path)
    out.write_text(mol2_content)
    bond_count = mol2_content.count("@<TRIPOS>BOND")
    print(f"[mol2_maker] ✅ {name}: {data['natoms']} atoms → {out}")

    return StepResult(step_name="mol2_maker", step_index=2, success=True,
                      outputs={"mol2": str(out)}, artifacts=[str(out)],
                      duration_s=time.time()-t0,
                      extra={"natoms": data["natoms"]})


# ============================================================
# 批量
# ============================================================

def batch_convert(struct_dir: str = "struct") -> list[StepResult]:
    """struct/ 下所有 .fchk → .mol2。返回 List[StepResult]。"""
    results: list[StepResult] = []
    for fchk in sorted(Path(struct_dir).glob("*.fchk")):
        sr = fchk_to_mol2(str(fchk))
        results.append(sr)
    ok = sum(1 for r in results if r.success)
    print(f"[mol2_maker] 完成: {ok}/{len(results)} 个 .mol2")
    return results


# ============================================================
# 测试
# ============================================================

if __name__ == "__main__":
    print("===== 测试: Li.fchk → Li.mol2 =====")
    fchk_to_mol2("struct/Li.fchk", "struct/Li.mol2")
    print("\n===== 批量转换 =====")
    batch_convert("struct")
