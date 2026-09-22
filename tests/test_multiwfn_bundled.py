"""Contract tests for Willy's bundled Multiwfn runtime integration."""

from __future__ import annotations

import hashlib
import json
import subprocess

from willy.errors import ErrorKind


def _molden_atoms(*rows: str) -> str:
    return "[Molden Format]\n[Atoms] Angs\n" + "\n".join(rows) + "\n[GTO]\n"


def test_molden_mol2_preserves_multiwfn_connectivity_and_atom_order(tmp_path, monkeypatch):
    from willy.quantum import molden_mol2

    source = tmp_path / "EC_opt.molden"
    source.write_text(_molden_atoms(
        "C 1 6 0.0 0.0 0.0",
        "O 2 8 1.2 0.0 0.0",
        "H 3 1 0.0 1.0 0.0",
    ), encoding="utf-8")
    commands = []

    def fake_run(_command, *, input_text, **_kwargs):
        commands.append(input_text)
        return subprocess.CompletedProcess(
            [], 0,
            stdout=(
                "    1C   ---    2O  :   1.99200   Nearest integer:  2\n"
                "    1C   ---    3H  :   0.99200   Nearest integer:  1\n"
                " #    1:         1(C )    2(O )    2.16489044\n"
                " #    2:         1(C )    3(H )    0.95849886\n"
            ), stderr="",
        )

    monkeypatch.setattr(molden_mol2, "find_multiwfn", lambda: "bundled-multiwfn")
    monkeypatch.setattr(molden_mol2, "build_tool_env", lambda _tool: {})
    monkeypatch.setattr(molden_mol2, "run_managed_command", fake_run)

    result = molden_mol2.convert(str(source))
    assert result.success is True
    content = source.with_name("EC.mol2").read_text(encoding="utf-8")
    assert "3 2" in content
    assert "C1" in content
    assert "    1     1     2    2" in content
    assert "    2     1     3    1" in content
    assert commands == ["100\n9\n\nn\n0\n9\n1\nn\n0\nq\n"]


def test_molden_mol2_allows_single_atom_without_bonds(tmp_path, monkeypatch):
    from willy.quantum import molden_mol2

    source = tmp_path / "Li_opt.molden"
    source.write_text(_molden_atoms("Li 1 3 0.0 0.0 0.0"), encoding="utf-8")
    monkeypatch.setattr(molden_mol2, "find_multiwfn", lambda: "bundled-multiwfn")
    monkeypatch.setattr(molden_mol2, "build_tool_env", lambda _tool: {})
    monkeypatch.setattr(
        molden_mol2,
        "run_managed_command",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, stdout="", stderr=""),
    )

    result = molden_mol2.convert(str(source))
    assert result.success is True
    assert "1 0" in source.with_name("Li.mol2").read_text(encoding="utf-8")


def test_molden_mol2_rejects_missing_connectivity_for_multi_atom(tmp_path, monkeypatch):
    from willy.quantum import molden_mol2

    source = tmp_path / "broken_opt.molden"
    source.write_text(_molden_atoms(
        "C 1 6 0.0 0.0 0.0", "O 2 8 1.2 0.0 0.0",
    ), encoding="utf-8")
    monkeypatch.setattr(molden_mol2, "find_multiwfn", lambda: "bundled-multiwfn")
    monkeypatch.setattr(molden_mol2, "build_tool_env", lambda _tool: {})
    monkeypatch.setattr(
        molden_mol2,
        "run_managed_command",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, stdout="", stderr=""),
    )

    result = molden_mol2.convert(str(source))
    assert result.success is False
    assert "未识别到任何键连接" in result.error.message
    assert not source.with_name("broken.mol2").exists()


def test_extract_xyz_rejects_nonzero_multiwfn_exit(tmp_path, monkeypatch):
    from willy.quantum import _orca_utils

    source = tmp_path / "input.fchk"
    source.write_text("placeholder", encoding="utf-8")
    monkeypatch.setattr(_orca_utils, "find_multiwfn", lambda: "bundled-multiwfn")
    monkeypatch.setattr(_orca_utils, "build_tool_env", lambda _tool: {})
    monkeypatch.setattr(
        _orca_utils,
        "run_managed_command",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 2, stdout="", stderr=""),
    )

    try:
        _orca_utils.extract_xyz(str(source), str(tmp_path))
    except RuntimeError as exc:
        assert "执行失败" in str(exc)
    else:
        raise AssertionError("非零 Multiwfn 退出码不得被视为坐标导出成功")


def test_resp_uses_clean_exit_sequence_and_publishes_charge(tmp_path, monkeypatch):
    from willy.quantum import chg_resp

    fchk = tmp_path / "sample_opt.fchk"
    fchk.write_text("placeholder", encoding="utf-8")
    calls: list[str] = []

    def fake_run(_command, *, input_text, cwd, **_kwargs):
        calls.append(input_text)
        (tmp_path / "sample_opt.chg").write_text("Li 0.0 0.0 0.0 0.0000000000\n", encoding="utf-8")
        assert cwd == str(tmp_path)
        return subprocess.CompletedProcess([], 0, stdout="", stderr="")

    monkeypatch.setattr(chg_resp, "find_multiwfn", lambda: "bundled-multiwfn")
    monkeypatch.setattr(chg_resp, "run_managed_command", fake_run)

    result = chg_resp.make_chg(str(fchk), workdir=str(tmp_path), output_name="sample")

    assert result.success is True
    assert calls == ["7\n18\n2\ny\n0\n0\nq\n"]
    assert (tmp_path / "sample.chg").is_file()


def test_resp_maps_nonzero_multiwfn_exit_to_resp_failure(tmp_path, monkeypatch):
    from willy.quantum import chg_resp

    fchk = tmp_path / "sample_opt.fchk"
    fchk.write_text("placeholder", encoding="utf-8")
    monkeypatch.setattr(chg_resp, "find_multiwfn", lambda: "bundled-multiwfn")
    monkeypatch.setattr(
        chg_resp,
        "run_managed_command",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 1, stdout="", stderr=""),
    )

    result = chg_resp.make_chg(str(fchk), workdir=str(tmp_path), output_name="sample")

    assert result.success is False
    assert result.error is not None
    assert result.error.kind is ErrorKind.RESP_FAILED


def test_sobtop_optional_multiwfn_command_targets_bundled_runtime():
    """Sobtop 的可选 Multiwfn 菜单不得回退到系统 PATH。"""
    from willy._paths import get_project_root

    content = (get_project_root() / "vendor" / "sobtop" / "sobtop.ini").read_text(encoding="utf-8")

    assert 'Multiwfn_cmd= "../multiwfn/linux-x86_64/3.8-dev-2025-02-14/Multiwfn"' in content


def test_bundled_payload_matches_manifest_and_has_no_machine_paths():
    """发布包必须可校验，且配置不能嵌入构建机的量子程序路径。"""
    from willy._paths import get_project_root

    bundle = get_project_root() / "vendor" / "multiwfn" / "linux-x86_64" / "3.8-dev-2025-02-14"
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))

    for filename, expected in manifest["files"].items():
        payload = bundle / filename
        assert payload.stat().st_size == expected["size_bytes"]
        assert hashlib.sha256(payload.read_bytes()).hexdigest() == expected["sha256"]

    values = {}
    for raw_line in (bundle / "settings.ini").read_text(encoding="utf-8").splitlines():
        key, separator, value = raw_line.partition("=")
        if separator:
            values[key.strip()] = value.split("//", maxsplit=1)[0].strip()
    for key in ("gaupath", "formchkpath", "orcapath", "orca_2mklpath", "dftd3path"):
        assert values[key] == "none"
