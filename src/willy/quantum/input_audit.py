"""Read-only structural audit for backend-specific quantum input files.

The Config Agent uses this module before it can freeze a molecular-system
proposal.  The parser intentionally extracts only a small, validated subset
of user-provided input: charge, multiplicity, coordinate count, and required
calculation directives.  It does not execute, rewrite, or expose source text.
"""

from __future__ import annotations

from collections.abc import Mapping
from math import isfinite
from pathlib import Path
import re
from typing import Any

from willy._paths import get_project_root


_BACKEND_SUFFIXES = {"g16": ".gjf", "g09": ".gjf", "orca": ".inp"}
_CHARGE_MULTIPLICITY = re.compile(r"^\s*([+-]?\d+)\s+(\d+)\s*$")
_ORCA_XYZ_START = re.compile(r"^\s*\*\s+xyz\s+([+-]?\d+)\s+(\d+)\s*$", re.IGNORECASE)
_ORCA_XYZFILE = re.compile(r"^\s*\*\s+xyzfile\b", re.IGNORECASE)
_ORCA_XYZ_END = re.compile(r"^\s*\*\s*$")


class QuantumInputAuditError(ValueError):
    """Raised for an invalid audit request, never for an invalid user file."""


def expected_input_suffix(backend: object) -> str:
    """Return the only raw input suffix accepted for one quantum backend."""
    normalized = str(backend or "").strip().lower()
    try:
        return _BACKEND_SUFFIXES[normalized]
    except KeyError as exc:
        raise QuantumInputAuditError("量子后端必须为 g16、g09 或 orca") from exc


def _safe_name(value: object) -> str:
    name = str(value or "").strip()
    candidate = Path(name)
    if (
        not name
        or len(name) > 128
        or candidate.name != name
        or name in {".", ".."}
        or "\x00" in name
    ):
        raise QuantumInputAuditError("分子名称无效")
    return name


def _positive_count(value: object) -> int:
    if isinstance(value, bool):
        raise QuantumInputAuditError("组分数必须为正整数")
    try:
        numeric = int(value)
    except (TypeError, ValueError) as exc:
        raise QuantumInputAuditError("组分数必须为正整数") from exc
    if numeric <= 0 or str(value).strip() not in {str(numeric), f"{numeric}.0"}:
        raise QuantumInputAuditError("组分数必须为正整数")
    return numeric


def _coordinate_count(lines: list[str]) -> tuple[int, list[str]]:
    """Validate ordinary element/x/y/z coordinate records without chemistry inference."""
    issues: list[str] = []
    count = 0
    for line in lines:
        text = line.strip()
        if not text or text.startswith(("!", "#", ";")):
            continue
        fields = text.split()
        if len(fields) < 4:
            issues.append("坐标行必须包含原子标识和三个坐标")
            continue
        try:
            values = [float(fields[index]) for index in range(1, 4)]
        except ValueError:
            issues.append("坐标必须是数值")
            continue
        if not all(isfinite(value) for value in values):
            issues.append("坐标必须是有限数值")
            continue
        count += 1
    if count == 0 and not issues:
        issues.append("未找到有效坐标")
    return count, issues


def _read_input(path: Path) -> list[str]:
    try:
        if path.stat().st_size > 2 * 1024 * 1024:
            raise ValueError("量子输入文件超过 2 MiB 审计上限")
        return path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise ValueError("量子输入文件无法读取") from exc


def _parse_gjf(path: Path) -> dict[str, Any]:
    lines = _read_input(path)
    route_index = next((index for index, line in enumerate(lines) if line.strip().startswith("#")), None)
    issues: list[str] = []
    if route_index is None:
        return {"valid": False, "issues": ["G16 输入缺少 route card"]}

    route_lines: list[str] = []
    cursor = route_index
    while cursor < len(lines) and lines[cursor].strip():
        route_lines.append(lines[cursor].strip())
        cursor += 1
    if not " ".join(route_lines).strip():
        issues.append("G16 route card 为空")

    # Gaussian's charge/multiplicity line follows the title block.  Do not
    # scan arbitrary later lines: a title such as "0 1" must not silently
    # become the molecular charge and multiplicity.
    while cursor < len(lines) and not lines[cursor].strip():
        cursor += 1
    title_start = cursor
    while cursor < len(lines) and lines[cursor].strip():
        cursor += 1
    title_lines = lines[title_start:cursor]
    direct_charge = (
        title_start < len(lines)
        and len(title_lines) == 1
        and _CHARGE_MULTIPLICITY.match(title_lines[0])
    )
    if direct_charge:
        # Permit an empty title block, which Gaussian accepts, while keeping
        # the actual charge line position deterministic.
        charge_index = title_start
    else:
        while cursor < len(lines) and not lines[cursor].strip():
            cursor += 1
        charge_index = cursor if cursor < len(lines) else None
    match = _CHARGE_MULTIPLICITY.match(lines[charge_index]) if charge_index is not None else None
    if match is None:
        return {"valid": False, "issues": [*issues, "G16 输入缺少 charge/multiplicity 行"]}
    charge = int(match.group(1))
    multiplicity = int(match.group(2))
    if multiplicity is None or multiplicity < 1:
        issues.append("自旋多重度必须为正整数")

    coordinates: list[str] = []
    for line in lines[charge_index + 1:]:
        if not line.strip():
            break
        coordinates.append(line)
    atom_count, coordinate_issues = _coordinate_count(coordinates)
    issues.extend(coordinate_issues)
    return {
        "valid": not issues,
        "charge": charge,
        "spin": multiplicity,
        "atom_count": atom_count,
        "directives": "route_card",
        "issues": issues,
    }


def _parse_orca_inp(path: Path) -> dict[str, Any]:
    lines = _read_input(path)
    keywords = [line.strip()[1:].strip() for line in lines if line.strip().startswith("!")]
    issues: list[str] = []
    if not keywords or not " ".join(keywords).strip():
        issues.append("ORCA 输入缺少 ! 关键词行")
    elif "opt" not in " ".join(keywords).casefold():
        issues.append("ORCA 结构优化输入必须包含 Opt 关键词")

    start_index = None
    charge = multiplicity = None
    for index, line in enumerate(lines):
        if _ORCA_XYZFILE.match(line):
            return {"valid": False, "issues": [*issues, "ORCA 不接受 xyzfile 外链坐标"]}
        match = _ORCA_XYZ_START.match(line)
        if match:
            start_index = index
            charge = int(match.group(1))
            multiplicity = int(match.group(2))
            break
    if start_index is None:
        return {"valid": False, "issues": [*issues, "ORCA 输入缺少内嵌 * xyz charge multiplicity 坐标块"]}
    if multiplicity is None or multiplicity < 1:
        issues.append("自旋多重度必须为正整数")

    end_index = next(
        (index for index in range(start_index + 1, len(lines)) if _ORCA_XYZ_END.match(lines[index])),
        None,
    )
    if end_index is None:
        return {"valid": False, "issues": [*issues, "ORCA * xyz 坐标块未闭合"]}
    atom_count, coordinate_issues = _coordinate_count(lines[start_index + 1:end_index])
    issues.extend(coordinate_issues)
    return {
        "valid": not issues,
        "charge": charge,
        "spin": multiplicity,
        "atom_count": atom_count,
        "directives": "orca_keywords",
        "issues": issues,
    }


def audit_quantum_inputs(
    backend: object,
    components: Mapping[str, object],
    *,
    struct_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Audit raw quantum inputs and calculate net molecular charge.

    ``components`` maps canonical molecule names to positive integer counts.
    The returned payload is safe to provide to the Config Agent: it excludes
    raw file text and all absolute paths.
    """
    normalized_backend = str(backend or "").strip().lower()
    suffix = expected_input_suffix(normalized_backend)
    if not isinstance(components, Mapping) or not components:
        raise QuantumInputAuditError("必须提供至少一个组分")
    directory = Path(struct_dir) if struct_dir is not None else get_project_root() / "struct"
    entries: list[dict[str, Any]] = []
    issues: list[str] = []
    net_charge = 0
    all_valid = True

    for raw_name, raw_count in components.items():
        try:
            name = _safe_name(raw_name)
            count = _positive_count(raw_count)
        except QuantumInputAuditError as exc:
            all_valid = False
            entries.append({"name": str(raw_name)[:128], "status": "invalid_request", "issues": [str(exc)]})
            issues.append(str(exc))
            continue
        path = directory / f"{name}{suffix}"
        if not path.is_file():
            all_valid = False
            message = f"{name} 缺少 {suffix} 原始输入"
            entries.append({"name": name, "count": count, "status": "missing", "issues": [message]})
            issues.append(message)
            continue
        try:
            parsed = _parse_gjf(path) if normalized_backend in {"g16", "g09"} else _parse_orca_inp(path)
        except ValueError:
            parsed = {"valid": False, "issues": [f"{name} 的 {suffix} 无法读取"]}
        entry = {
            "name": name,
            "count": count,
            "status": "valid" if parsed.get("valid") else "invalid",
            "charge": parsed.get("charge"),
            "spin": parsed.get("spin"),
            "atom_count": parsed.get("atom_count", 0),
            "directives": parsed.get("directives", ""),
            "issues": list(parsed.get("issues", []))[:8],
        }
        entries.append(entry)
        if not parsed.get("valid"):
            all_valid = False
            issues.extend(f"{name}: {issue}" for issue in entry["issues"])
            continue
        net_charge += count * int(parsed["charge"])

    charge_balance = "unavailable"
    if all_valid:
        charge_balance = "balanced" if net_charge == 0 else "imbalanced"
        if net_charge != 0:
            issues.append(f"体系总电荷为 {net_charge:+d}")
    return {
        "ok": all_valid,
        "backend": normalized_backend,
        "expected_suffix": suffix,
        "components": entries,
        "net_charge": net_charge if all_valid else None,
        "charge_balance": charge_balance,
        "issues": issues[:16],
    }


def audit_config_quantum_inputs(
    config: Mapping[str, object],
    *,
    struct_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Audit one workflow config without trusting its molecule charge fields."""
    if not isinstance(config, Mapping):
        raise QuantumInputAuditError("配置必须是对象")
    residues = config.get("residues")
    if not isinstance(residues, Mapping):
        raise QuantumInputAuditError("配置缺少 residues")
    return audit_quantum_inputs(config.get("backend", "g16"), residues, struct_dir=struct_dir)


def quantum_input_contract_issues(
    config: Mapping[str, object],
    audit: Mapping[str, object],
) -> list[str]:
    """Return launch-blocking issues for one already-audited configuration.

    Values declared in ``molecules`` are a plan snapshot, not an authority.
    They must exactly match the selected backend's raw input.  A charged
    system remains possible only through the existing explicit confirmation
    fields; this helper deliberately does not invent a compensation strategy.
    """
    issues: list[str] = []
    if not audit.get("ok"):
        raw_issues = audit.get("issues", [])
        if isinstance(raw_issues, list):
            return [str(issue) for issue in raw_issues[:16]] or ["量子输入审计未通过"]
        return ["量子输入审计未通过"]

    molecules = config.get("molecules")
    if not isinstance(molecules, Mapping):
        return ["配置缺少 molecules"]
    for entry in audit.get("components", []):
        if not isinstance(entry, Mapping) or entry.get("status") != "valid":
            continue
        name = entry.get("name")
        if not isinstance(name, str):
            continue
        molecule = molecules.get(name)
        if not isinstance(molecule, Mapping):
            issues.append(f"{name} 缺少分子属性")
            continue
        for field, label in (("charge", "电荷"), ("spin", "自旋多重度")):
            declared = molecule.get(field)
            observed = entry.get(field)
            if isinstance(declared, bool) or not isinstance(declared, int):
                issues.append(f"{name} 的{label}必须由原始输入审计写入")
            elif declared != observed:
                issues.append(
                    f"{name} 的{label}与 {audit.get('expected_suffix', '原始输入')} 不一致"
                )

    imbalance = audit.get("charge_balance") == "imbalanced"
    compensation = config.get("ion_compensation")
    confirmed = bool(config.get("non_neutral_confirmed")) or (
        isinstance(compensation, Mapping)
        and bool(compensation.get("confirmed") or compensation.get("strategy"))
    )
    if imbalance and not confirmed:
        net_charge = audit.get("net_charge")
        issues.append(
            f"体系总电荷为 {net_charge:+d}；请设置 ion_compensation 或明确 non_neutral_confirmed"
            if isinstance(net_charge, int)
            else "体系电荷不平衡；请设置 ion_compensation 或明确 non_neutral_confirmed"
        )
    return issues


def apply_audited_quantum_properties(
    config: Mapping[str, object],
    audit: Mapping[str, object],
) -> dict[str, Any]:
    """Copy audited charge/spin into a draft config without persisting it."""
    if not audit.get("ok"):
        raise QuantumInputAuditError("量子输入审计未通过")
    result = dict(config)
    raw_molecules = result.get("molecules")
    molecules = dict(raw_molecules) if isinstance(raw_molecules, Mapping) else {}
    for entry in audit.get("components", []):
        if not isinstance(entry, Mapping) or entry.get("status") != "valid":
            continue
        name = entry.get("name")
        if not isinstance(name, str):
            continue
        previous = molecules.get(name)
        molecule = dict(previous) if isinstance(previous, Mapping) else {}
        molecule["charge"] = int(entry["charge"])
        molecule["spin"] = int(entry["spin"])
        molecules[name] = molecule
    result["molecules"] = molecules
    return result
