"""Section-aware, idempotent ITP post-processing."""

from __future__ import annotations

from pathlib import Path
import re


_ATOM_RESNAME = re.compile(r"^(\s*\S+\s+\S+\s+\S+\s+)(\S+)(.*)$")


def _section_name(line: str) -> str | None:
    stripped = line.strip()
    if not (stripped.startswith("[") and "]" in stripped):
        return None
    return stripped[1:stripped.index("]")].strip().lower()


def _replace_resname_in_atoms(lines: list[str], resname: str) -> tuple[list[str], bool]:
    """Update valid ``[ atoms ]`` rows without reformatting comments or sections."""
    in_atoms = False
    changed = False
    result: list[str] = []
    for line in lines:
        section = _section_name(line)
        if section is not None:
            in_atoms = section == "atoms"
            result.append(line)
            continue
        if not in_atoms:
            result.append(line)
            continue
        code, separator, comment = line.partition(";")
        line_ending = ""
        if not separator:
            if code.endswith("\r\n"):
                code, line_ending = code[:-2], "\r\n"
            elif code.endswith("\n"):
                code, line_ending = code[:-1], "\n"
        fields = code.split()
        # A legal GROMACS atom row has index/type/resnr/resname/atom/cgnr/q/m.
        if len(fields) < 8 or not fields[0].lstrip("+-").isdigit() or not fields[2].lstrip("+-").isdigit():
            result.append(line)
            continue
        match = _ATOM_RESNAME.match(code)
        if match is None or fields[3] == resname:
            result.append(line)
            continue
        result.append(match.group(1) + resname + match.group(3) + line_ending + separator + comment)
        changed = True
    return result, changed


def revise_itp(itp_path: str, residue_name: str | None = None) -> bool:
    """Remove ``[ atomtypes ]`` and normalize atom residue names once.

    The caller must collect atom types from the immutable backend ITP before
    revising its separate assembly copy.
    """
    path = Path(itp_path)
    resname = residue_name or path.stem
    original = path.read_text()
    lines = original.splitlines(keepends=True)
    result: list[str] = []
    skipping_atomtypes = False
    removed_atomtypes = False
    for line in lines:
        section = _section_name(line)
        if section is not None:
            if section == "atomtypes":
                skipping_atomtypes = True
                removed_atomtypes = True
                continue
            if skipping_atomtypes:
                skipping_atomtypes = False
        if not skipping_atomtypes:
            result.append(line)

    result, replaced_resname = _replace_resname_in_atoms(result, resname)
    revised = "".join(result)
    if removed_atomtypes or replaced_resname:
        path.write_text(revised)
        return True
    return False


def revise_all(itp_dir: str) -> int:
    """Revise all ITPs in an explicit run-local directory."""
    return sum(revise_itp(str(path)) for path in sorted(Path(itp_dir).glob("*.itp")))
