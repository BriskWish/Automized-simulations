"""Regression coverage for ORCA structure-artifact handling."""

from __future__ import annotations

import subprocess
from pathlib import Path


def _fake_orca_process(calls: list[list[str]], runtime_inputs: list[str] | None = None):
    def fake_run(command, *, cwd, **_kwargs):
        commands = [str(value) for value in command]
        calls.append(commands)
        workdir = Path(cwd)
        if commands[0] == "orca":
            stem = Path(commands[1]).stem
            if runtime_inputs is not None:
                runtime_inputs.append((workdir / commands[1]).read_text(encoding="utf-8"))
            (workdir / f"{stem}.gbw").write_text("gbw", encoding="utf-8")
            return subprocess.CompletedProcess(
                commands, 0, stdout="****ORCA TERMINATED NORMALLY****", stderr="",
            )
        assert commands[0] == "orca_2mkl"
        (workdir / f"{commands[1]}.molden.input").write_text("molden", encoding="utf-8")
        return subprocess.CompletedProcess(commands, 0, stdout="", stderr="")

    return fake_run


def test_single_atom_opt_uses_private_sp_input_and_preserves_raw_input(tmp_path, monkeypatch):
    from willy.quantum import struct_orca

    raw_input = tmp_path / "Li.inp"
    original = "! B3LYP 6-311+G(d,p) Opt\n\n* xyz 1 1\nLi 0 0 0\n*\n"
    raw_input.write_text(original, encoding="utf-8")
    calls: list[list[str]] = []
    runtime_inputs: list[str] = []
    monkeypatch.setattr(struct_orca, "find_orca", lambda: "orca")
    monkeypatch.setattr(struct_orca, "find_orca_2mkl", lambda: "orca_2mkl")
    monkeypatch.setattr(struct_orca, "get_orca_env", lambda: {})
    monkeypatch.setattr(struct_orca, "run_managed_command", _fake_orca_process(calls, runtime_inputs))

    result = struct_orca.run_one("Li", {"charge": 1, "spin": 1}, {}, str(tmp_path))

    assert result.success is True
    assert result.extra == {"nproc": 8, "single_atom_fallback": "sp"}
    assert raw_input.read_text(encoding="utf-8") == original
    assert calls[0] == ["orca", "Li__single_atom_sp__willy_run.inp"]
    assert not (tmp_path / "Li__single_atom_sp.inp").exists()
    assert not (tmp_path / "Li__single_atom_sp__willy_run.inp").exists()
    assert "%maxcore 5000" in runtime_inputs[0]
    assert "%pal nprocs 8 end" in runtime_inputs[0]
    assert not list(tmp_path.glob("Li__single_atom_sp.*"))
    assert (tmp_path / "Li.gbw").exists()
    assert (tmp_path / "Li.molden").exists()


def test_multiatom_opt_keeps_original_orca_input(tmp_path, monkeypatch):
    from willy.quantum import struct_orca

    (tmp_path / "Li.inp").write_text(
        "! B3LYP 6-311+G(d,p) Opt\n\n* xyz 0 1\nH 0 0 0\nH 0 0 0.74\n*\n",
        encoding="utf-8",
    )
    calls: list[list[str]] = []
    runtime_inputs: list[str] = []
    monkeypatch.setattr(struct_orca, "find_orca", lambda: "orca")
    monkeypatch.setattr(struct_orca, "find_orca_2mkl", lambda: "orca_2mkl")
    monkeypatch.setattr(struct_orca, "get_orca_env", lambda: {})
    monkeypatch.setattr(struct_orca, "run_managed_command", _fake_orca_process(calls, runtime_inputs))

    result = struct_orca.run_one(
        "Li", {"nproc": 3, "mem": "9GB"}, {"nproc": 8, "mem": "5GB"}, str(tmp_path),
    )

    assert result.success is True
    assert result.extra == {"nproc": 3}
    assert calls[0] == ["orca", "Li__willy_run.inp"]
    runtime = tmp_path / "Li__willy_run.inp"
    assert not runtime.exists()
    assert "%maxcore 9000" in runtime_inputs[0]
    assert "%pal nprocs 3 end" in runtime_inputs[0]
    assert "%pal" not in (tmp_path / "Li.inp").read_text(encoding="utf-8")
