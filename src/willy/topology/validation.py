"""Validation helpers for the topology artifact contract."""

from __future__ import annotations

from pathlib import Path

from willy.errors import ErrorKind, StepError, StepResult


def validate_topology_output_name(output_name: str) -> str:
    """Reject names that could escape a topology run directory."""
    if (
        not isinstance(output_name, str)
        or not output_name
        or output_name in {".", ".."}
        or "/" in output_name
        or "\\" in output_name
        or Path(output_name).name != output_name
    ):
        raise ValueError(f"非法拓扑输出名: {output_name!r}")
    return output_name


def run_output_path(directory: str | Path, output_name: str, suffix: str) -> Path:
    """Return a verified output path whose resolved parent is ``directory``."""
    name = validate_topology_output_name(output_name)
    root = Path(directory).resolve()
    output = root / f"{name}{suffix}"
    if output.resolve().parent != root:
        raise ValueError(f"拓扑输出路径逃出 run_dir: {output}")
    return output


def _section_name(line: str) -> str | None:
    stripped = line.strip()
    if not (stripped.startswith("[") and "]" in stripped):
        return None
    return stripped[1:stripped.index("]")].strip().lower()


def count_itp_atoms(itp_path: str | Path) -> int:
    """Return the number of data rows in the ITP ``[ atoms ]`` section."""
    in_atoms = False
    count = 0
    for line in Path(itp_path).read_text().splitlines():
        section = _section_name(line)
        if section is not None:
            if section == "atoms":
                in_atoms = True
                continue
            if in_atoms:
                break
        if not in_atoms:
            continue
        data = line.split(";", 1)[0].strip()
        if not data:
            continue
        if len(data.split()) >= 8:
            count += 1
    return count


def parse_itp_moleculetype(itp_path: str | Path) -> str:
    """Return the molecule type declared by the first legal data row."""
    in_section = False
    for line in Path(itp_path).read_text().splitlines():
        section = _section_name(line)
        if section is not None:
            if section == "moleculetype":
                in_section = True
                continue
            if in_section:
                break
        if not in_section:
            continue
        data = line.split(";", 1)[0].strip()
        if not data or data.startswith("#"):
            continue
        fields = data.split()
        if len(fields) < 2:
            raise ValueError("ITP 的 [ moleculetype ] 段没有合法数据行")
        return fields[0]
    raise ValueError("ITP 的 [ moleculetype ] 段没有合法数据行")


def parse_gro_atom_count(gro_path: str | Path) -> int:
    """Parse the atom count from a GROMACS GRO file."""
    lines = Path(gro_path).read_text().splitlines()
    if len(lines) < 3:
        raise ValueError("GRO 文件至少应有标题、原子数和一个盒子行")
    try:
        count = int(lines[1].strip())
    except ValueError as exc:
        raise ValueError(f"GRO 原子数无法解析: {lines[1]!r}") from exc
    if count < 1:
        raise ValueError(f"GRO 原子数必须大于零: {count}")
    if len(lines) < count + 3:
        raise ValueError(f"GRO 声明 {count} 个原子，但文件只有 {len(lines)} 行")
    return count


def validate_topology_files(
    itp_path: str | Path,
    gro_path: str | Path,
    *,
    step_name: str,
    error_kind: ErrorKind,
    step_index: int = 4,
    expected_moleculetype: str | None = None,
) -> StepResult:
    """Validate the required ITP sections and ITP/GRO atom-count agreement."""
    itp = Path(itp_path)
    gro = Path(gro_path)
    missing = [str(path) for path in (itp, gro) if not path.is_file()]
    if missing:
        return StepResult(
            step_name=step_name,
            step_index=step_index,
            success=False,
            error=StepError(
                kind=ErrorKind.FILE_NOT_FOUND,
                message=f"拓扑产物缺失: {', '.join(missing)}",
                hint="重新运行当前拓扑后端；不可复用旧的 vendor 临时输出。",
            ),
        )

    try:
        sections = {
            section
            for line in itp.read_text().splitlines()
            if (section := _section_name(line)) is not None
        }
        required_sections = {"moleculetype", "atoms"}
        missing_sections = required_sections - sections
        if missing_sections:
            raise ValueError(
                "ITP 缺少 " + ", ".join(f"[ {name} ]" for name in sorted(missing_sections))
            )
        moleculetype = parse_itp_moleculetype(itp)
        if expected_moleculetype is not None and moleculetype != expected_moleculetype:
            raise ValueError(
                f"ITP [ moleculetype ]={moleculetype!r}，应为 {expected_moleculetype!r}"
            )
        itp_atoms = count_itp_atoms(itp)
        if itp_atoms < 1:
            raise ValueError("ITP 的 [ atoms ] 段没有合法原子数据行")
        gro_atoms = parse_gro_atom_count(gro)
        if itp_atoms != gro_atoms:
            raise ValueError(f"ITP/GRO 原子数不一致: {itp_atoms} != {gro_atoms}")
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        return StepResult(
            step_name=step_name,
            step_index=step_index,
            success=False,
            error=StepError(
                kind=error_kind,
                message=f"拓扑产物校验失败: {exc}",
                hint="检查后端输出的 [ moleculetype ]、[ atoms ] 和 GRO 原子数。",
            ),
        )

    return StepResult(
        step_name=step_name,
        step_index=step_index,
        success=True,
        outputs={"itp": str(itp), "gro": str(gro)},
        artifacts=[str(itp), str(gro)],
        extra={
            "itp_atom_count": itp_atoms,
            "gro_atom_count": gro_atoms,
            "moleculetype": moleculetype,
        },
    )
