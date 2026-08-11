"""Namespace LigParGen atom types before assembling a multi-molecule topology.

LigParGen emits locally numbered OPLS-AA atom types for each molecule.  Those
numbers are not a global key: e.g. ``opls_806`` may describe H in one ITP and
F in another.  This module gives every component a deterministic namespace and
rewrites the type references in its private assembly copy.  The source ITP is
never modified.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import hashlib
import re


class ITPNamespaceError(ValueError):
    """Raised when an ITP cannot be safely namespaced."""


@dataclass(frozen=True)
class NamespaceResult:
    """The rewritten text metadata needed by ``top_assembly``."""

    path: Path
    residue_name: str
    mapping: dict[str, str]
    atomtype_lines: tuple[str, ...]
    renamed_count: int


_TYPE_SECTIONS: dict[str, tuple[int, ...]] = {
    # [ atoms ] stores its atom type in the second field.
    "atoms": (1,),
    # These sections use atom type names as their first fields.  The numeric
    # interaction parameters following them must remain untouched.
    "bondtypes": (0, 1),
    "constrainttypes": (0, 1),
    "angletypes": (0, 1, 2),
    "dihedraltypes": (0, 1, 2, 3),
    "pairtypes": (0, 1),
    "nonbond_params": (0, 1),
    "cmaptypes": (0, 1, 2, 3, 4),
}

_TOKEN = re.compile(r"\S+")
_SAFE_NAME = re.compile(r"[^A-Za-z0-9_]+")


def _section_name(line: str) -> str | None:
    stripped = line.strip()
    if not (stripped.startswith("[") and "]" in stripped):
        return None
    return stripped[1:stripped.index("]")].strip().lower()


def _atomtype_rows(lines: list[str]) -> list[tuple[int, str, list[str]]]:
    """Return ``(line index, data, fields)`` for valid atomtype rows."""
    rows: list[tuple[int, str, list[str]]] = []
    section = False
    for index, line in enumerate(lines):
        current = _section_name(line)
        if current is not None:
            section = current == "atomtypes"
            continue
        if not section:
            continue
        data = line.split(";", 1)[0].strip()
        if not data or data.startswith("#"):
            continue
        fields = data.split()
        if len(fields) < 2:
            raise ITPNamespaceError(f"[ atomtypes ] 定义不完整: 第 {index + 1} 行")
        rows.append((index, data, fields))
    if not rows:
        raise ITPNamespaceError("ITP 缺少有效的 [ atomtypes ] 定义")
    return rows


def _component_prefix(residue_name: str) -> str:
    if not residue_name or residue_name in {".", ".."}:
        raise ITPNamespaceError("残基名不能为空")
    safe = _SAFE_NAME.sub("_", residue_name).strip("_") or "component"
    # A short digest prevents collisions such as ``A-B`` and ``A_B`` while
    # keeping the generated type names readable in grompp diagnostics.
    digest = hashlib.sha1(residue_name.encode("utf-8")).hexdigest()[:8]
    return f"WLY_{safe}_{digest}__"


def _replace_tokens(line: str, replacements: dict[int, str]) -> tuple[str, int]:
    """Replace selected whitespace-delimited fields without touching comments."""
    code, separator, comment = line.partition(";")
    matches = list(_TOKEN.finditer(code))
    if not matches:
        return line, 0
    changed = 0
    rewritten = code
    for field_index, replacement in sorted(replacements.items(), reverse=True):
        if field_index >= len(matches):
            continue
        match = matches[field_index]
        old = match.group(0)
        if old == replacement:
            continue
        rewritten = rewritten[:match.start()] + replacement + rewritten[match.end():]
        changed += 1
    if not changed:
        return line, 0
    return rewritten + (separator + comment if separator else ""), changed


def _rewrite_text(text: str, residue_name: str, path: Path) -> tuple[str, NamespaceResult]:
    lines = text.splitlines(keepends=True)
    rows = _atomtype_rows(lines)
    prefix = _component_prefix(residue_name)
    mapping: dict[str, str] = {}
    atomtype_lines: list[str] = []
    row_indices: set[int] = set()

    for index, data, fields in rows:
        old_name = fields[0]
        new_name = mapping.setdefault(old_name, prefix + old_name)
        # A single ITP must not assign two different definitions to one local
        # type name.  Such a file is malformed rather than a cross-component
        # namespace collision.
        existing = next((line for line in atomtype_lines if line.split()[0] == new_name), None)
        rewritten_data = " ".join([new_name, *fields[1:]])
        if existing is not None and existing != rewritten_data:
            raise ITPNamespaceError(
                f"{path.name} 内 atomtype {old_name} 定义重复且参数不同"
            )
        if existing is None:
            atomtype_lines.append(rewritten_data)
        row_indices.add(index)

    current_section: str | None = None
    result: list[str] = []
    renamed_count = 0
    for index, line in enumerate(lines):
        section = _section_name(line)
        if section is not None:
            current_section = section
            result.append(line)
            continue
        if index in row_indices:
            rewritten, count = _replace_tokens(line, {0: mapping[line.split(";", 1)[0].split()[0]]})
            result.append(rewritten)
            renamed_count += count
            continue
        fields = _TYPE_SECTIONS.get(current_section or ())
        if fields is None:
            result.append(line)
            continue
        data = line.split(";", 1)[0].strip()
        if not data or data.startswith("#"):
            result.append(line)
            continue
        tokens = data.split()
        replacements = {
            field: mapping[tokens[field]]
            for field in fields
            if field < len(tokens) and tokens[field] in mapping
        }
        rewritten, count = _replace_tokens(line, replacements)
        result.append(rewritten)
        renamed_count += count

    return "".join(result), NamespaceResult(
        path=path,
        residue_name=residue_name,
        mapping=mapping,
        atomtype_lines=tuple(atomtype_lines),
        renamed_count=renamed_count,
    )


def namespace_itp(itp_path: str | Path, residue_name: str | None = None) -> NamespaceResult:
    """Rewrite one ITP in place and return its deterministic type mapping."""
    path = Path(itp_path)
    name = residue_name or path.stem
    try:
        text = path.read_text()
    except OSError as exc:
        raise ITPNamespaceError(f"无法读取 ITP: {path}: {exc}") from exc
    rewritten, result = _rewrite_text(text, name, path)
    try:
        path.write_text(rewritten)
    except OSError as exc:
        raise ITPNamespaceError(f"无法写入命名空间 ITP: {path}: {exc}") from exc
    return result


def preview_itp(itp_path: str | Path, residue_name: str | None = None) -> NamespaceResult:
    """Validate and collect namespaced atomtypes without writing the file."""
    path = Path(itp_path)
    name = residue_name or path.stem
    try:
        text = path.read_text()
    except OSError as exc:
        raise ITPNamespaceError(f"无法读取 ITP: {path}: {exc}") from exc
    _, result = _rewrite_text(text, name, path)
    return result


def namespace_all(
    itp_paths: list[str | Path] | tuple[str | Path, ...],
    residue_names: list[str] | tuple[str, ...],
) -> tuple[NamespaceResult, ...]:
    """Namespace a validated, ordered set of component ITPs."""
    if len(itp_paths) != len(residue_names):
        raise ITPNamespaceError("ITP 路径与残基名数量不一致")
    return tuple(namespace_itp(path, name) for path, name in zip(itp_paths, residue_names))


def main() -> int:
    """Small command-line wrapper used for manual run-local inspection."""
    import argparse

    parser = argparse.ArgumentParser(description="Namespace LigParGen ITP atom types")
    parser.add_argument("--residue", required=True)
    parser.add_argument("itp", type=Path)
    args = parser.parse_args()
    result = namespace_itp(args.itp, args.residue)
    print(f"{args.itp}: renamed {result.renamed_count} type references")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
