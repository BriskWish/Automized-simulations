"""Normalize user-provided quantum inputs before they enter ``struct/``.

The uploaded source is not retained verbatim.  A normalized input keeps only
the coordinate block plus the file-authoritative charge and multiplicity;
method, memory and CPU settings are supplied later by the frozen workflow
configuration.  This keeps uploaded structures safe to expose by core name
without allowing arbitrary resource directives to become execution settings.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
import json
import re
import unicodedata

from willy._paths import get_project_root
from willy.config_store import write_json, write_text
from willy.quantum.input_audit import inspect_quantum_input_file


CATALOG_FILENAME = ".willy_uploaded_structures.json"
_KNOWLEDGE_RELATIVE_PATH = Path("docs") / "knowledge_molecules.md"
_KNOWLEDGE_START = "<!-- WILLY_UPLOADED_STRUCTURES_START -->"
_KNOWLEDGE_END = "<!-- WILLY_UPLOADED_STRUCTURES_END -->"
_SUPPORTED_SUFFIXES = frozenset({".gjf", ".inp"})
_SAFE_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,127}$")
_ELEMENT_ION = re.compile(r"^([A-Z][a-z]?)(?:\d+)?[+-]+$")
_FORMULA_CHARGE = re.compile(r"^(.+?)(?:\^\d+)?[+-]+$")


class StructureUploadError(ValueError):
    """Raised when an upload cannot become a safe normalized structure."""


@dataclass(frozen=True)
class UploadedStructure:
    """Public metadata retained for one normalized uploaded structure."""

    name: str
    format: str
    charge: int
    spin: int
    atom_count: int

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "format": self.format,
            "charge": self.charge,
            "spin": self.spin,
            "atom_count": self.atom_count,
        }


def supported_upload_suffixes() -> tuple[str, ...]:
    """Return the quantum input suffixes accepted by the upload boundary."""
    return tuple(sorted(_SUPPORTED_SUFFIXES))


def core_structure_name(filename: str | Path) -> str:
    """Derive a safe filename key while removing only conventional ion charge.

    ``Li+`` and ``Ca2+`` therefore become ``Li`` and ``Ca``.  Formula ions
    such as ``NO3-`` retain the formula digit, while ``SO4^2-`` becomes
    ``SO4``.  The result is intentionally ASCII because it is also used as a
    config key and a quantum input stem.
    """
    raw = unicodedata.normalize("NFKC", Path(str(filename)).stem).strip()
    raw = raw.translate(str.maketrans({"−": "-", "＋": "+"}))
    if not raw:
        raise StructureUploadError("上传文件缺少可用的核心文件名")

    element_match = _ELEMENT_ION.fullmatch(raw)
    if element_match:
        raw = element_match.group(1)
    else:
        formula_match = _FORMULA_CHARGE.fullmatch(raw)
        if formula_match:
            raw = formula_match.group(1)

    normalized = re.sub(r"[^A-Za-z0-9_]+", "_", raw).strip("_")
    if not _SAFE_NAME.fullmatch(normalized):
        raise StructureUploadError(
            "核心文件名只能包含英文字母、数字和下划线，且必须以字母开头"
        )
    return normalized


def load_uploaded_structures(project_root: str | Path | None = None) -> list[UploadedStructure]:
    """Read validated upload metadata; malformed records are ignored safely."""
    root = Path(project_root) if project_root is not None else get_project_root()
    catalog = root / "struct" / CATALOG_FILENAME
    try:
        raw = json.loads(catalog.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return []
    records = raw.get("entries") if isinstance(raw, Mapping) else None
    if not isinstance(records, list):
        return []
    entries: list[UploadedStructure] = []
    for record in records:
        if not isinstance(record, Mapping):
            continue
        try:
            name = str(record["name"])
            suffix = str(record["format"])
            charge = int(record["charge"])
            spin = int(record["spin"])
            atom_count = int(record["atom_count"])
        except (KeyError, TypeError, ValueError):
            continue
        if (
            not _SAFE_NAME.fullmatch(name)
            or suffix not in _SUPPORTED_SUFFIXES
            or spin < 1
            or atom_count < 1
        ):
            continue
        entries.append(UploadedStructure(name, suffix, charge, spin, atom_count))
    return sorted(entries, key=lambda entry: entry.name.casefold())


def normalize_uploaded_structure(
    source_path: str | Path,
    *,
    project_root: str | Path | None = None,
) -> UploadedStructure:
    """Validate and normalize one uploaded G16/G09 or ORCA input.

    Existing core names are never overwritten.  An upload must be renamed by
    the user when it would otherwise replace a bundled or previously uploaded
    structure, keeping the input catalog reproducible.
    """
    source = Path(source_path)
    if not source.is_file():
        raise StructureUploadError(f"上传文件 {source.name} 不存在")
    suffix = source.suffix.casefold()
    if suffix not in _SUPPORTED_SUFFIXES:
        allowed = "、".join(supported_upload_suffixes())
        raise StructureUploadError(f"仅支持可审计的原始量子输入：{allowed}")

    try:
        inspected = inspect_quantum_input_file(source)
    except ValueError as exc:
        raise StructureUploadError(f"{source.name} 未通过量子输入审计：{exc}") from exc
    if not inspected["valid"]:
        details = "；".join(str(issue) for issue in inspected["issues"][:4])
        raise StructureUploadError(f"{source.name} 未通过量子输入审计：{details}")

    root = Path(project_root) if project_root is not None else get_project_root()
    struct_dir = root / "struct"
    struct_dir.mkdir(parents=True, exist_ok=True)
    name = core_structure_name(source.name)
    target = struct_dir / f"{name}{suffix}"
    if target.exists():
        related = "、".join(_related_structure_names(struct_dir, name))
        if source.name == target.name:
            reason = f"文件名 {target.name} 已存在，已拒绝覆盖"
        else:
            reason = (
                f"{source.name} 规范化后的核心文件名 {name} 与已存在的 {target.name} 冲突，"
                "已拒绝覆盖"
            )
        raise StructureUploadError(f"{reason}。相关核心文件名：{related}")

    charge = int(inspected["charge"])
    spin = int(inspected["spin"])
    coordinates = tuple(str(line) for line in inspected["coordinates"])
    content = _normalized_input_content(name, suffix, charge, spin, coordinates)
    write_text(target, content)

    entry = UploadedStructure(
        name=name,
        format=suffix,
        charge=charge,
        spin=spin,
        atom_count=int(inspected["atom_count"]),
    )
    _save_uploaded_structure(root, entry)
    _update_knowledge_uploaded_names(root, load_uploaded_structures(root))
    return entry


def _normalized_input_content(
    name: str,
    suffix: str,
    charge: int,
    spin: int,
    coordinates: Iterable[str],
) -> str:
    body = "\n".join(coordinates)
    if suffix == ".gjf":
        return (
            "#p B3LYP/6-311+G(d,p) Opt\n\n"
            f"{name}\n\n"
            f"{charge} {spin}\n"
            f"{body}\n\n"
        )
    return (
        "! B3LYP 6-311+G(d,p) Opt\n\n"
        f"* xyz {charge} {spin}\n"
        f"{body}\n"
        "*\n"
    )


def _related_structure_names(struct_dir: Path, name: str, limit: int = 5) -> list[str]:
    """Return stable nearby core names for an upload-name conflict message."""
    names = {
        path.stem
        for suffix in _SUPPORTED_SUFFIXES
        for path in struct_dir.glob(f"*{suffix}")
        if not path.stem.endswith("_run")
    }
    ranked = sorted(
        (
            SequenceMatcher(None, name.casefold(), candidate.casefold()).ratio(),
            candidate,
        )
        for candidate in names
    )
    ordered = [
        candidate
        for score, candidate in sorted(
            ranked,
            key=lambda item: (-item[0], item[1].casefold()),
        )
        if score > 0
    ]
    return ordered[:limit] or [name]


def _save_uploaded_structure(root: Path, entry: UploadedStructure) -> None:
    existing = {record.name: record for record in load_uploaded_structures(root)}
    existing[entry.name] = entry
    payload = {
        "schema_version": 1,
        "entries": [record.as_dict() for record in sorted(
            existing.values(), key=lambda item: item.name.casefold(),
        )],
    }
    write_json(root / "struct" / CATALOG_FILENAME, payload)


def _update_knowledge_uploaded_names(root: Path, entries: Iterable[UploadedStructure]) -> None:
    """Refresh only the explicitly managed core-name section in the knowledge doc."""
    path = root / _KNOWLEDGE_RELATIVE_PATH
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return
    rows = ["| 核心文件名 |", "|---|"]
    rows.extend(f"| {entry.name} |" for entry in entries)
    section = "\n".join([
        "## 十、已上传结构",
        "",
        "该区段由上传通道维护，只暴露可执行结构的核心文件名。"
        "文件格式、电荷和自旋由 `struct/.willy_uploaded_structures.json` 记录，"
        "每次启动仍以原始量子输入审计为准。",
        "",
        _KNOWLEDGE_START,
        *rows,
        _KNOWLEDGE_END,
        "",
    ])
    start = content.find(_KNOWLEDGE_START)
    end = content.find(_KNOWLEDGE_END)
    if start >= 0 and end >= start:
        block_start = content.rfind("## ", 0, start)
        block_start = block_start if block_start >= 0 else start
        block_end = end + len(_KNOWLEDGE_END)
        while block_end < len(content) and content[block_end] == "\n":
            block_end += 1
        updated = f"{content[:block_start].rstrip()}\n\n{section}{content[block_end:]}"
    else:
        updated = f"{content.rstrip()}\n\n{section}"
    write_text(path, updated)
