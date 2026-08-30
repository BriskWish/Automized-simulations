"""Regression tests for the deterministic quantum-input upload boundary."""

import json

import pytest

from willy.quantum.input_audit import audit_quantum_inputs
from willy.structure_uploads import (
    StructureUploadError,
    core_structure_name,
    normalize_uploaded_structure,
)


def _knowledge_doc(root):
    docs = root / "docs"
    docs.mkdir()
    path = docs / "knowledge_molecules.md"
    path.write_text("# 分子知识库\n", encoding="utf-8")
    return path


@pytest.mark.parametrize(("filename", "expected"), [
    ("Li+.gjf", "Li"),
    ("Li⁺.gjf", "Li"),
    ("Ca2+.gjf", "Ca"),
    ("Ca²⁺.gjf", "Ca"),
    ("NO3-.inp", "NO3"),
    ("SO4^2-.inp", "SO4"),
])
def test_core_structure_name_strips_only_charge_notation(filename, expected):
    assert core_structure_name(filename) == expected


def test_upload_normalizes_gaussian_input_and_updates_name_registry(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    _knowledge_doc(root)
    source = tmp_path / "Ca2+.gjf"
    source.write_text(
        "%mem=64GB\n%nprocshared=48\n#p M062X/def2TZVP Opt\n\n"
        "calcium source\n\n2 1\nCa 0.0 0.0 0.0\n\n",
        encoding="utf-8",
    )

    entry = normalize_uploaded_structure(source, project_root=root)

    assert entry.name == "Ca"
    assert (entry.format, entry.charge, entry.spin, entry.atom_count) == (".gjf", 2, 1, 1)
    normalized = (root / "struct" / "Ca.gjf").read_text(encoding="utf-8")
    assert "%mem" not in normalized
    assert "%nproc" not in normalized
    assert "M062X" not in normalized
    assert "#p B3LYP/6-311+G(d,p) Opt" in normalized
    assert "2 1\nCa 0.0 0.0 0.0" in normalized

    catalog = json.loads((root / "struct" / ".willy_uploaded_structures.json").read_text())
    assert catalog["entries"] == [{
        "name": "Ca", "format": ".gjf", "charge": 2, "spin": 1, "atom_count": 1,
    }]
    knowledge = (root / "docs" / "knowledge_molecules.md").read_text(encoding="utf-8")
    assert "| Ca |" in knowledge
    assert "Ca2+" not in knowledge
    audit = audit_quantum_inputs("g16", {"Ca": 1}, struct_dir=root / "struct")
    assert audit["ok"] is True
    assert audit["components"][0]["charge"] == 2


def test_upload_normalizes_orca_input_without_resource_directives(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    _knowledge_doc(root)
    source = tmp_path / "Cl-.inp"
    source.write_text(
        "! PBE0 def2-SVP Opt\n%maxcore 8000\n%pal nprocs 16 end\n\n"
        "* xyz -1 1\nCl 0.0 0.0 0.0\n*\n",
        encoding="utf-8",
    )

    entry = normalize_uploaded_structure(source, project_root=root)

    assert entry.name == "Cl"
    normalized = (root / "struct" / "Cl.inp").read_text(encoding="utf-8")
    assert "%maxcore" not in normalized
    assert "%pal" not in normalized
    assert "PBE0" not in normalized
    assert "! B3LYP 6-311+G(d,p) Opt" in normalized
    assert "* xyz -1 1\nCl 0.0 0.0 0.0\n*" in normalized


def test_upload_rejects_same_filename_and_reports_related_core_names(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    _knowledge_doc(root)
    first = tmp_path / "Ca2+.gjf"
    first.write_text(
        "#p B3LYP/6-311+G(d,p) Opt\n\nCa\n\n2 1\nCa 0 0 0\n\n",
        encoding="utf-8",
    )
    normalize_uploaded_structure(first, project_root=root)
    same_name = tmp_path / "copy" / "Ca.gjf"
    same_name.parent.mkdir()
    same_name.write_text(
        "#p B3LYP/6-311+G(d,p) Opt\n\nCa\n\n2 1\nCa 1 0 0\n\n",
        encoding="utf-8",
    )

    with pytest.raises(StructureUploadError) as exc_info:
        normalize_uploaded_structure(same_name, project_root=root)

    message = str(exc_info.value)
    assert "文件名 Ca.gjf 已存在" in message
    assert "相关核心文件名：Ca" in message
    assert "Ca 1 0 0" not in (root / "struct" / "Ca.gjf").read_text(encoding="utf-8")


def test_upload_rejects_charge_variant_when_normalized_core_already_exists(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    _knowledge_doc(root)
    (root / "struct").mkdir()
    (root / "struct" / "Ca.gjf").write_text("existing", encoding="utf-8")
    source = tmp_path / "Ca2+.gjf"
    source.write_text(
        "#p B3LYP/6-311+G(d,p) Opt\n\nCa\n\n2 1\nCa 0 0 0\n\n",
        encoding="utf-8",
    )

    with pytest.raises(StructureUploadError, match="规范化后的核心文件名 Ca 与已存在的 Ca.gjf 冲突"):
        normalize_uploaded_structure(source, project_root=root)


def test_upload_rejects_non_quantum_structure_without_creating_registry_entry(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    _knowledge_doc(root)
    source = tmp_path / "Ca2+.xyz"
    source.write_text("1\ncalcium\nCa 0 0 0\n", encoding="utf-8")

    with pytest.raises(StructureUploadError, match="仅支持可审计"):
        normalize_uploaded_structure(source, project_root=root)

    assert not (root / "struct").exists()
