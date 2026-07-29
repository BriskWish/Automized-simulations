"""
itp_reviser.py
==============
itp 文件修订器。

功能:
  1. 删除 [ atomtypes ] 段（atomtype 已迁移到主 .top）
  2. 将 [ atoms ] 中所有 RESNAME 改为残基名（itp 文件名）
"""

from pathlib import Path


def _replace_resname_in_atoms(lines: list[str], resname: str) -> list[str]:
    """
    在 [ atoms ] 段内，将每行原子条目的 resname 字段替换为指定值。

    GROMACS [ atoms ] 格式:
        index  type  resnr  resname  atom  cgnr  charge  mass
    """
    in_atoms = False
    result = []

    for line in lines:
        # 检测是否进入/离开 [ atoms ] 段
        stripped = line.strip()
        if stripped.startswith("[") and "atoms" in stripped.lower():
            in_atoms = True
            result.append(line)
            continue
        if in_atoms and stripped.startswith("["):
            in_atoms = False
            result.append(line)
            continue

        if not in_atoms:
            result.append(line)
            continue

        # 跳过注释和空行
        if stripped == "" or stripped.startswith(";"):
            result.append(line)
            continue

        # 原子数据行：第 4 个字段是 resname
        parts = line.split()
        if len(parts) >= 8:
            parts[3] = resname
            # 重建行，保持可读对齐
            new_line = (f"{parts[0]:>6s} {parts[1]:>6s} {parts[2]:>6s} "
                        f"{parts[3]:>6s} {parts[4]:>6s} {parts[5]:>6s} "
                        f"{parts[6]:>12s} {parts[7]:>12s}")
            result.append(new_line)
        else:
            result.append(line)

    return result


def revise_itp(itp_path: str) -> bool:
    """
    修订单个 .itp 文件:
      1. 删除 [ atomtypes ] 段
      2. RESNAME → 残基名

    Returns:
        True 表示有修改，False 表示无需修改
    """
    path = Path(itp_path)
    resname = path.stem  # 文件名去扩展名 = 残基名
    content = path.read_text()
    lines = content.split("\n")
    modified = False

    # ── Step 1: 删除 [ atomtypes ] 段 ──
    start = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[") and "atomtypes" in stripped.lower():
            start = i
            break

    if start is not None:
        end = None
        for i in range(start + 1, len(lines)):
            if lines[i].strip() == "":
                end = i
                break
        if end is None:
            end = len(lines) - 1

        del lines[start:end + 1]
        modified = True
        print(f"[itp_reviser] ✅ {path.name}: 已删除 [atomtypes] 段")
    else:
        print(f"[itp_reviser] ⏭  {path.name}: 无 [atomtypes] 段，跳过删除")

    # ── Step 2: 替换 RESNAME ──
    lines = _replace_resname_in_atoms(lines, resname)
    # 只有在确实发生了替换时才标记
    # (检查 resname 是否不同于原来的 "MOL")
    modified = True  # 即使 atomtypes 已删，resname 替换也要写回

    path.write_text("\n".join(lines))
    print(f"[itp_reviser] ✅ {path.name}: RESNAME → {resname}")

    return modified


def revise_all(itp_dir: str = "topo") -> int:
    """
    批量修订目录下所有 .itp 文件。

    Returns:
        修订的文件数量
    """
    count = 0
    for itp in sorted(Path(itp_dir).glob("*.itp")):
        if revise_itp(str(itp)):
            count += 1
    print(f"[itp_reviser] 完成: {count} 个文件已修订")
    return count


if __name__ == "__main__":
    revise_all("topo")
