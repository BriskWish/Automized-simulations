"""top_assembly 的运行目录契约测试。"""

import json

from willy.topology.top_assembly import build


def test_build_revises_itps_in_requested_topology_directory(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "residues": {"SOL": 2},
        "molecules": {"SOL": {"charge": 0, "spin": 1}},
        "topology": {"backend": "sobtop", "force_field": "gaff_uff"},
    }))
    itp_path = tmp_path / "SOL.itp"
    itp_path.write_text(
        "[ atomtypes ]\n"
        "SOL  1  1.0  0.0  A  0.1  0.1\n\n"
        "[ moleculetype ]\n"
        "SOL  3\n\n"
        "[ atoms ]\n"
        "1  SOL  1  MOL  S1  1  0.0  1.0\n"
    )
    gro_path = tmp_path / "SOL.gro"
    gro_path.write_text("SOL\n1\n    1SOL     S1    1   0.000   0.000   0.000\n   1.00000   1.00000   1.00000\n")
    (tmp_path / "topology_manifest.json").write_text(json.dumps({
        "version": 1,
        "backend": "sobtop",
        "forcefield_family": "gaff_uff",
        "components": [{
            "molecule_id": "SOL", "residue_name": "SOL", "quantity": 2,
            "mol2": str(tmp_path / "SOL.mol2"), "chg": str(tmp_path / "SOL.chg"),
            "charge": 0, "spin": 1, "smiles": None,
            "itp": str(itp_path), "gro": str(gro_path), "backend": "sobtop",
            "forcefield_family": "gaff_uff", "success": True, "validated": True, "error": "",
        }],
    }))

    result = build(config_path=str(config_path), topo_dir=str(tmp_path))

    assert result.success is True
    assert result.outputs["topol"] == str(tmp_path / "topol.top")
    source = itp_path.read_text()
    assembled_path = tmp_path / ".assembly_itp" / "SOL.itp"
    assembled = assembled_path.read_text()
    assert "[ atomtypes ]" in source
    assert " MOL " in source
    assert "[ atomtypes ]" not in assembled
    assert " SOL " in assembled
    assert '#include ".assembly_itp/SOL.itp"' in (tmp_path / "topol.top").read_text()


def test_build_rejects_a_shared_topology_directory():
    result = build()

    assert result.success is False
    assert result.error.kind.value == "input_contract"
