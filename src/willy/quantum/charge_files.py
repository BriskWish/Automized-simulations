"""Validated raw RESP charges and reproducible effective-charge artifacts."""

from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from pathlib import Path
import re
from uuid import uuid4

from willy.charge_scaling import validate_ion_charge_scale
from willy.config_store import write_json, write_text
from willy.topology.validation import validate_topology_output_name


CHARGE_SCHEMA_VERSION = 1
CHARGE_TOLERANCE = Decimal("0.00001")


def charge_paths(directory: Path, name: str) -> tuple[Path, Path, Path]:
    validate_topology_output_name(name)
    paths = tuple(directory / f"{name}{suffix}" for suffix in (".chg", ".resp.chg", ".charge_scaling.json"))
    if any(path.is_symlink() or path.resolve().parent != directory.resolve() for path in paths):
        raise ValueError("电荷产物必须是工程目录内的独立文件")
    return paths


def parse_charge_text(text: str) -> list[Decimal]:
    charges = []
    for line in text.splitlines():
        fields = line.split()
        if not fields:
            continue
        if len(fields) != 5 or not re.fullmatch(r"[A-Z][a-z]?", fields[0]):
            raise ValueError("CHG 必须逐行包含元素、三个坐标与原子电荷")
        try:
            values = [Decimal(value.replace("D", "E").replace("d", "e")) for value in fields[1:]]
        except InvalidOperation as exc:
            raise ValueError("CHG 包含无法解析的数值") from exc
        if not all(value.is_finite() and abs(value) <= Decimal("1e12") for value in values):
            raise ValueError("CHG 数值必须有限且在有效范围内")
        charges.append(values[-1])
    if not charges:
        raise ValueError("CHG 不得为空")
    return charges


def _fingerprint(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise ValueError("电荷来源与产物必须是独立文件")
    content = path.read_bytes()
    if not content:
        raise ValueError("电荷来源与产物不得为空")
    return {"name": path.name, "sha256": sha256(content).hexdigest(), "size_bytes": len(content)}


def _validate_identity(charge: int, spin: int) -> None:
    if type(charge) is not int or type(spin) is not int or spin < 1:
        raise ValueError("电荷修正必须使用审计后的整数净电荷和正整数自旋多重度")


def _raw_charges(text: str, fchk: Path, charge: int) -> list[Decimal]:
    charges = parse_charge_text(text)
    if abs(sum(charges) - Decimal(charge)) > CHARGE_TOLERANCE:
        raise ValueError("原始 RESP 电荷总和与审计后的净电荷不一致")
    with fchk.open(encoding="utf-8", errors="strict") as handle:
        for line in handle:
            match = re.match(r"^Number of atoms\s+I\s+(\d+)\s*$", line)
            if match:
                if int(match[1]) != len(charges):
                    raise ValueError("CHG 原子数与波函数输入不一致")
                break
    return charges


def scaled_charge_text(text: str, charge: int, factor: float) -> str:
    charges = parse_charge_text(text)
    if charge == 0 or factor == 1.0:
        return text
    scale = Decimal(str(factor))
    rows = []
    values = iter(charges)
    for line in text.splitlines(keepends=True):
        if line.strip():
            effective_charge = next(values) * scale
            line = re.sub(r"\S+(\s*)$", lambda match: f"{effective_charge:.12f}" + match[1], line)
        rows.append(line)
    return "".join(rows)


def cached_charge_record(directory: Path, name: str, fchk: Path, charge: int, spin: int) -> dict | None:
    paths = charge_paths(directory, name)
    effective, raw, metadata = paths
    if not all(path.is_file() for path in paths):
        return None
    try:
        record = json.loads(metadata.read_text(encoding="utf-8"))
        if not isinstance(record, dict) or record.get("schema_version") != CHARGE_SCHEMA_VERSION:
            return None
        if record.get("formal_charge") != charge or record.get("spin") != spin or record.get("name") != name:
            return None
        previous_scale = validate_ion_charge_scale(record.get("ion_charge_scale"))
        if record.get("source") != _fingerprint(fchk) or record.get("raw") != _fingerprint(raw):
            return None
        if record.get("effective") != _fingerprint(effective):
            return None
        text = raw.read_text(encoding="utf-8")
        charges = _raw_charges(text, fchk, charge)
        if effective.read_text(encoding="utf-8") != scaled_charge_text(text, charge, previous_scale):
            return None
        if record.get("atom_count") != len(charges) or record.get("applied_scale") != (previous_scale if charge else 1.0):
            return None
        return record
    except (OSError, ValueError, TypeError):
        return None


def archive_charge_files(directory: Path, name: str, fchk: Path) -> None:
    from willy.step_contracts import archive_files

    candidates = set(charge_paths(directory, name))
    candidates.update(directory / candidate for candidate in (f"{fchk.stem}.chg", "gau.chg"))
    archive_files(directory, candidates, f"charges-{uuid4().hex}", step=3)


def publish_charge_files(directory: Path, name: str, fchk: Path, charge: int, spin: int,
                         factor: float, raw_text: str) -> dict:
    _validate_identity(charge, spin)
    factor = validate_ion_charge_scale(factor)
    effective, raw, metadata = charge_paths(directory, name)
    charges = _raw_charges(raw_text, fchk, charge)
    effective_text = scaled_charge_text(raw_text, charge, factor)
    effective_total = sum(parse_charge_text(effective_text))
    if abs(effective_total - Decimal(charge) * Decimal(str(factor))) > CHARGE_TOLERANCE:
        raise ValueError("修正后的电荷总和不符合缩放约定")
    source = _fingerprint(fchk)
    write_text(raw, raw_text)
    write_text(effective, effective_text)
    record = {
        "schema_version": CHARGE_SCHEMA_VERSION, "name": name,
        "formal_charge": charge, "spin": spin, "ion_charge_scale": factor,
        "applied_scale": factor if charge else 1.0, "atom_count": len(charges),
        "raw_total_charge": float(sum(charges)), "effective_total_charge": float(effective_total),
        "source": source, "raw": _fingerprint(raw), "effective": _fingerprint(effective),
    }
    write_json(metadata, record)
    return record


def validate_charge_record(directory: Path, name: str, charge: int, spin: int, factor: float) -> dict:
    _validate_identity(charge, spin)
    factor = validate_ion_charge_scale(factor)
    record = cached_charge_record(directory, name, directory / f"{name}_opt.fchk", charge, spin)
    if record is None or record["ion_charge_scale"] != factor:
        raise ValueError(f"{name}: 电荷缩放记录与当前配置或原始文件不一致，须从 Step 3 重建")
    return record


def validate_itp_charge_transfer(chg_path: Path, itp_path: Path) -> None:
    expected = parse_charge_text(chg_path.read_text(encoding="utf-8"))
    actual = []
    in_atoms = False
    for line in itp_path.read_text(encoding="utf-8").splitlines():
        text = line.split(";", 1)[0].strip()
        if text.startswith("["):
            in_atoms = text.strip("[] ").lower() == "atoms"
            continue
        if not text or text.startswith("#") or not in_atoms:
            continue
        fields = text.split()
        if len(fields) < 8:
            raise ValueError("Sobtop ITP 原子电荷字段不完整")
        try:
            actual.append(Decimal(fields[6]))
        except InvalidOperation as exc:
            raise ValueError("Sobtop ITP 电荷无法解析") from exc
    if len(actual) != len(expected) or any(not value.is_finite() or abs(value - source) > Decimal("0.000051") for value, source in zip(actual, expected)):
        raise ValueError("Sobtop ITP 未保持修正后的 CHG 原子电荷，禁止进入模拟")
