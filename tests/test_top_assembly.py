"""top_assembly 的运行目录契约测试。"""

import json

from willy.topology.top_assembly import build


def test_build_revises_itps_in_requested_topology_directory(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"residues": {"SOL": 2}}))
    itp_path = tmp_path / "SOL.itp"
    itp_path.write_text(
        "[ atomtypes ]\n"
        "SOL  1  1.0  0.0  A  0.1  0.1\n\n"
        "[ moleculetype ]\n"
        "SOL  3\n\n"
        "[ atoms ]\n"
        "1  SOL  1  MOL  S1  1  0.0  1.0\n"
    )

    result = build(config_path=str(config_path), topo_dir=str(tmp_path))

    assert result.success is True
    assert result.outputs["topol"] == str(tmp_path / "topol.top")
    revised = itp_path.read_text()
    assert "[ atomtypes ]" not in revised
    assert " SOL " in revised
