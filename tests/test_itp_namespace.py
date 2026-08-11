"""LigParGen atomtype namespace and topology assembly regression tests."""

from __future__ import annotations

import json
from pathlib import Path

from willy.topology.itp_namespace import namespace_itp
from willy.topology.manifest import TopologyManifestComponent, load_manifest, write_manifest
from willy.topology.top_assembly import build


def _write_gro(path: Path) -> None:
    path.write_text(
        "test\n1\n"
        "    1MOL     C1    1   0.000   0.000   0.000\n"
        "   1.00000   1.00000   1.00000\n"
    )


def _write_itp(path: Path, atomtype_params: str) -> None:
    path.write_text(
        "[ atomtypes ]\n"
        f"  opls_806  {atomtype_params}\n"
        "[ moleculetype ]\n"
        f"{path.stem} 3\n\n"
        "[ atoms ]\n"
        "1 opls_806 1 MOL C1 1 0.0 1.0\n\n"
        "[ nonbond_params ]\n"
        "opls_806 opls_806 1 0.3 0.2\n"
    )


def test_namespace_itp_rewrites_atom_and_parameter_references(tmp_path):
    itp = tmp_path / "FEC.itp"
    _write_itp(itp, "F806 18.9984 0.0 A 0.29 0.25")

    result = namespace_itp(itp, "FEC")
    rewritten = itp.read_text()
    new_type = next(iter(result.mapping.values()))

    assert result.mapping == {"opls_806": new_type}
    assert new_type.startswith("WLY_FEC_")
    assert f"{new_type} 1 MOL" in rewritten
    assert f"{new_type} {new_type} 1" in rewritten
    assert result.atomtype_lines == (
        f"{new_type} F806 18.9984 0.0 A 0.29 0.25",
    )


def test_opls_assembly_namespaces_conflicting_local_types(tmp_path):
    config = {
        "residues": {"EC": 1, "FEC": 1},
        "molecules": {"EC": {"charge": 0, "spin": 1}, "FEC": {"charge": 0, "spin": 1}},
        "topology": {"backend": "oplsaa", "force_field": "oplsaa"},
    }
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config))
    components = []
    for name, params in (
        ("EC", "H806 1.0080 0.0 A 0.25 0.12"),
        ("FEC", "F806 18.9984 0.0 A 0.29 0.25"),
    ):
        itp = tmp_path / f"{name}.itp"
        gro = tmp_path / f"{name}.gro"
        _write_itp(itp, params)
        _write_gro(gro)
        components.append(TopologyManifestComponent(
            molecule_id=name, residue_name=name, quantity=1,
            mol2=str(tmp_path / f"{name}.mol2"), chg=None, charge=0, spin=1,
            smiles=None, backend="oplsaa", forcefield_family="oplsaa",
            itp=str(itp), gro=str(gro), success=True, validated=True,
        ))
    write_manifest(tmp_path, backend="oplsaa", forcefield_family="oplsaa", components=components)

    result = build(config_path=str(config_path), topo_dir=str(tmp_path))

    assert result.success is True
    top = (tmp_path / "topol.top").read_text()
    ec_type = next(line.split()[0] for line in top.splitlines() if line.startswith("WLY_EC_"))
    fec_type = next(line.split()[0] for line in top.splitlines() if line.startswith("WLY_FEC_"))
    assert ec_type != fec_type
    assert ec_type in (tmp_path / ".assembly_itp" / "EC.itp").read_text()
    assert fec_type in (tmp_path / ".assembly_itp" / "FEC.itp").read_text()
    assert "[ atomtypes ]" not in (tmp_path / ".assembly_itp" / "EC.itp").read_text()
    assert "[ atomtypes ]" not in (tmp_path / ".assembly_itp" / "FEC.itp").read_text()
    manifest = load_manifest(tmp_path)
    assert manifest["components"][0]["atomtype_namespace"]
    assert manifest["components"][1]["atomtype_namespace"]
    # The immutable backend artifacts retain the original local names.
    assert "opls_806" in (tmp_path / "EC.itp").read_text()
    assert "opls_806" in (tmp_path / "FEC.itp").read_text()
