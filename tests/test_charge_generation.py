"""Charged entities share one reproducible partial-charge scaling policy."""

from decimal import Decimal
import json
from pathlib import Path
import subprocess

import pytest

from willy.quantum import chg_resp
from willy.quantum.charge_files import parse_charge_text, validate_itp_charge_transfer


def _engine(monkeypatch, directory, raw):
    calls = []
    monkeypatch.setattr(chg_resp, "find_multiwfn", lambda: "Multiwfn-fixture")

    def run(command, **_kwargs):
        calls.append(command)
        source = Path(command[1])
        (directory / f"{source.stem}.chg").write_text(raw, encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(chg_resp, "run_managed_command", run)
    return calls


def _generate(monkeypatch, directory, *, name="charged_group", charge=1, factor=0.8,
              raw="N 0 0 0 1.5000000000\nH 0 0 1 -0.5000000000\n"):
    source = directory / f"{name}_opt.fchk"
    source.write_text(f"Number of atoms                            I                {len(raw.splitlines())}\n")
    calls = _engine(monkeypatch, directory, raw)
    result = chg_resp.make_chg(str(source), charge=charge, output_name=name, ion_charge_scale=factor)
    return source, result, calls


@pytest.mark.parametrize("charge,raw", [
    (1, "Li 0 0 0 1.0000000000\n"),
    (-1, "N 0 0 0 0.5000000000\nO 0 0 1 -1.5000000000\n"),
    (2, "C 0 0 0 1.2000000000\nN 0 0 1 0.8000000000\n"),
    (-2, "C 0 0 0 -1.2000000000\nN 0 0 1 -0.8000000000\n"),
])
@pytest.mark.parametrize("factor", [0.6, 0.8, 1.0])
def test_all_nonzero_charge_entities_scale_without_ion_tags(tmp_path, monkeypatch, charge, raw, factor):
    _source, result, calls = _generate(monkeypatch, tmp_path, charge=charge, factor=factor, raw=raw)
    assert result.success, result.error
    assert len(calls) == 1
    original = parse_charge_text(raw)
    effective = parse_charge_text(Path(result.outputs["chg"]).read_text())
    assert effective == [value * Decimal(str(factor)) for value in original]
    assert sum(effective) == Decimal(charge) * Decimal(str(factor))
    assert Path(result.outputs["raw_chg"]).read_text() == raw
    record = json.loads(Path(result.outputs["charge_scaling"]).read_text())
    assert record["formal_charge"] == charge
    assert record["ion_charge_scale"] == factor


@pytest.mark.parametrize("factor", [0.6, 0.8, 1.0])
def test_neutral_polar_molecule_is_byte_identical(tmp_path, monkeypatch, factor):
    raw = "C  0.000000 0.000000 0.000000 -0.2500000000\nH  0.000000 0.000000 1.000000  0.2500000000\n"
    _source, result, _calls = _generate(monkeypatch, tmp_path, charge=0, factor=factor, raw=raw)
    assert result.success
    assert Path(result.outputs["chg"]).read_bytes() == raw.encode()
    assert result.extra["charge_scaling"]["applied_scale"] == 1.0


def test_repeated_generation_and_factor_change_never_compound(tmp_path, monkeypatch):
    source, first, calls = _generate(monkeypatch, tmp_path)
    previous = Path(first.outputs["chg"]).read_bytes()
    for factor in (0.8, 0.75, 1.0, 0.6):
        result = chg_resp.make_chg(str(source), charge=1, ion_charge_scale=factor)
        assert result.success, result.error
        assert sum(parse_charge_text(Path(result.outputs["chg"]).read_text())) == Decimal(str(factor))
    assert len(calls) == 1
    archives = list((tmp_path / "old").glob("*/files/charged_group.chg"))
    assert len(archives) == 3
    assert any(path.read_bytes() == previous for path in archives)
    assert (tmp_path / "charged_group.resp.chg").read_text().endswith("-0.5000000000\n")


def test_untracked_chg_is_archived_and_not_rescaled(tmp_path, monkeypatch):
    legacy = tmp_path / "charged_group.chg"
    legacy.write_text("N 0 0 0 0.8\n")
    _source, result, calls = _generate(monkeypatch, tmp_path)
    assert result.success and len(calls) == 1
    assert sum(parse_charge_text(legacy.read_text())) == Decimal("0.8")
    assert next((tmp_path / "old").glob("*/files/charged_group.chg")).read_text() == "N 0 0 0 0.8\n"


@pytest.mark.parametrize("artifact", ["charged_group.chg", "charged_group.resp.chg", "charged_group.charge_scaling.json"])
def test_changed_charge_artifact_is_not_blindly_reused(tmp_path, monkeypatch, artifact):
    source, first, calls = _generate(monkeypatch, tmp_path)
    assert first.success
    (tmp_path / artifact).write_text("untrusted modification")
    result = chg_resp.make_chg(str(source), charge=1, ion_charge_scale=0.8)
    assert result.success and len(calls) == 2
    assert sum(parse_charge_text(Path(result.outputs["chg"]).read_text())) == Decimal("0.8")


@pytest.mark.parametrize("raw", ["", "Li 0 0\n", "Li 0 0 0 nan\n", "Li 0 inf 0 1\n", "Li 0 0 0 0.8\n"])
def test_invalid_raw_charges_never_publish_accepted_metadata(tmp_path, monkeypatch, raw):
    _source, result, _calls = _generate(monkeypatch, tmp_path, raw=raw)
    assert not result.success
    assert not (tmp_path / "charged_group.charge_scaling.json").exists()


def test_charge_count_must_match_fchk(tmp_path, monkeypatch):
    source = tmp_path / "group_opt.fchk"
    source.write_text("Number of atoms I 2\n")
    _engine(monkeypatch, tmp_path, "Li 0 0 0 1\n")
    result = chg_resp.make_chg(str(source), charge=1, ion_charge_scale=0.8)
    assert not result.success and "原子数" in result.error.message


def test_blank_lines_preserve_atom_order(tmp_path, monkeypatch):
    source = tmp_path / "group_opt.fchk"
    source.write_text("Number of atoms I 2\n")
    _engine(monkeypatch, tmp_path, "\nN 0 0 0 1.5\n\nH 0 0 1 -0.5\n")
    result = chg_resp.make_chg(str(source), charge=1, ion_charge_scale=0.8)
    assert result.success
    assert parse_charge_text(Path(result.outputs["chg"]).read_text()) == [Decimal("1.2"), Decimal("-0.4")]


@pytest.mark.parametrize("factor", [0.59, 1.01, 0.805, True, "0.80", None, float("nan")])
def test_invalid_factor_stops_before_engine_and_archive(tmp_path, monkeypatch, factor):
    _source, result, calls = _generate(monkeypatch, tmp_path, factor=factor)
    assert not result.success
    assert not calls
    assert not (tmp_path / "old").exists()


def test_charge_output_symlink_is_rejected(tmp_path, monkeypatch):
    protected = tmp_path / "original"
    protected.write_text("do not edit")
    (tmp_path / "charged_group.chg").symlink_to(protected)
    _source, result, calls = _generate(monkeypatch, tmp_path)
    assert not result.success and not calls
    assert protected.read_text() == "do not edit"


@pytest.mark.parametrize("name", ["tools_retry_chg_g16", "tools_retry_chg_g09", "tools_retry_chg_orca"])
def test_recovery_uses_frozen_scale_not_tool_overrides(tmp_path, monkeypatch, name):
    from willy.errors import StepResult
    from willy.toolist_quantum import handle_quantum_tool_call

    source = tmp_path / "charged_group_opt.fchk"
    source.write_text("input")
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"ion_charge_scale": 0.75, "molecules": {"charged_group": {"charge": 2, "spin": 1}}}))
    calls = []
    monkeypatch.setattr(chg_resp, "make_chg", lambda *_args, **kwargs: calls.append(kwargs) or StepResult("chg_resp", 3, True))
    result = json.loads(handle_quantum_tool_call(name, {"fchk_path": str(source), "ion_charge_scale": 0.6}, config_path=str(config)))
    assert result["success"]
    assert calls[0]["ion_charge_scale"] == 0.75
    assert calls[0]["charge"] == 2


def test_itp_must_consume_effective_not_formal_charge(tmp_path):
    chg = tmp_path / "group.chg"
    chg.write_text("Li 0 0 0 0.8\n")
    itp = tmp_path / "group.itp"
    itp.write_text("[ atoms ]\n1 li 1 Li Li 1 0.8000 6.941\n")
    validate_itp_charge_transfer(chg, itp)
    itp.write_text("[ atoms ]\n1 li 1 Li Li 1 1.0000 6.941\n")
    with pytest.raises(ValueError, match="未保持"):
        validate_itp_charge_transfer(chg, itp)
