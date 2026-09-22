"""Regression tests for topology backend dispatch and artifact contracts."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import json
import os
import subprocess
import sys
import time
import pytest

from willy.errors import ErrorKind, StepError, StepResult
from willy.workflow_config import validate_config
from willy.topology.backends import OplsaaBackend, SobtopBackend, TopologyComponent, dispatch_topology, normalize_topology_config
from willy.topology.itp_revise import revise_itp
from willy.topology.manifest import (
    MAX_TOPOLOGY_RETRIES,
    TopologyManifestComponent,
    claim_retry,
    load_manifest,
    write_manifest,
)
from willy.run_metadata import RunMetadataError, create_run_manifest, load_run_manifest
from willy.topology.topo_opls import LigParGenInput, make_itp_gro_opls
from willy.topology.top_assembly import build
from willy.topology.topo_gaff import SobtopInput, SobtopInputBuilder, make_itp_gro
from willy.topology.validation import validate_topology_files


def _write_itp(path: Path, atomtype: str = "CT", atomtype_params: str = "6 12.01 0.0 A 0.34 0.2") -> None:
    path.write_text(
        "[ atomtypes ]\n"
        f"{atomtype}  {atomtype_params}\n"
        "; atomtype comments survive the parser\n"
        "[ moleculetype ]\n"
        f"{path.stem}  3\n\n"
        "[ atoms ]\n"
        f"1  {atomtype}  1  MOL  C1  1  0.0  12.01\n"
    )


def _write_gro(path: Path, atom_count: int = 1) -> None:
    atoms = [f"    1MOL     C{i:1d}{i:5d}   0.000   0.000   0.000\n" for i in range(1, atom_count + 1)]
    path.write_text("test\n" + str(atom_count) + "\n" + "".join(atoms) + "   1.00000   1.00000   1.00000\n")


def _write_ligpargen_gro(path: Path, temporary_prefix: str, atom_count: int = 1) -> None:
    atoms = [
        f"    1{temporary_prefix}  C{i:02d}{i:5d}   0.000   0.000   0.000\n"
        for i in range(1, atom_count + 1)
    ]
    path.write_text(
        "LIGPARGEN GENERATED GRO FILE\n"
        + str(atom_count)
        + "\n"
        + "".join(atoms)
        + "   1.00000   1.00000   1.00000\n"
    )


def _result_for(itp: Path, gro: Path, name: str = "topology") -> StepResult:
    return StepResult(name, 4, True, outputs={"itp": str(itp), "gro": str(gro)}, artifacts=[str(itp), str(gro)])


def _config(backend: str, force_field: str, residues: dict[str, int]) -> dict:
    return {
        "residues": residues,
        "molecules": {name: {"charge": charge, "spin": 1} for name, charge in ((name, 0) for name in residues)},
        "topology": {"backend": backend, "force_field": force_field},
    }


def _write_manifest_for(tmp_path: Path, backend: str, family: str, names: list[str], *, valid: bool = True) -> None:
    components = []
    for name in names:
        itp = tmp_path / f"{name}.itp"
        gro = tmp_path / f"{name}.gro"
        _write_itp(itp)
        _write_gro(gro)
        components.append(TopologyManifestComponent(
            molecule_id=name, residue_name=name, quantity=1,
            mol2=str(tmp_path / f"{name}.mol2"), chg=str(tmp_path / f"{name}.chg") if backend == "sobtop" else None,
            charge=0, spin=1, smiles=None, itp=str(itp), gro=str(gro),
            backend=backend, forcefield_family=family, success=valid, validated=valid,
        ))
    write_manifest(tmp_path, backend=backend, forcefield_family=family, components=components)


class TestUnifiedTopologyManifest:
    def test_write_uses_private_topology_section_when_unified_manifest_exists(self, tmp_path):
        create_run_manifest(tmp_path)
        component = TopologyManifestComponent(
            molecule_id="SOL", residue_name="SOL", quantity=2,
            mol2=str(tmp_path / "SOL.mol2"), chg=None, charge=0, spin=1,
            smiles=None, backend="oplsaa", forcefield_family="oplsaa",
            success=True, validated=True,
        )

        written = write_manifest(
            tmp_path,
            backend="oplsaa",
            forcefield_family="oplsaa",
            components=[component],
        )

        unified = load_run_manifest(tmp_path)
        topology = unified["sections"]["topology"]
        assert written == tmp_path / "run_manifest.json"
        assert not (tmp_path / "topology_manifest.json").exists()
        assert topology["visibility"] == "private"
        assert topology["revision"] == 1
        assert topology["data"]["backend"] == "oplsaa"
        assert load_manifest(tmp_path)["components"][0]["residue_name"] == "SOL"

    def test_legacy_topology_manifest_is_read_when_unified_manifest_is_absent(self, tmp_path):
        component = TopologyManifestComponent(
            molecule_id="A", residue_name="A", quantity=1,
            mol2=str(tmp_path / "A.mol2"), chg=str(tmp_path / "A.chg"),
            charge=0, spin=1, smiles=None, backend="sobtop", forcefield_family="gaff_uff",
        )
        write_manifest(
            tmp_path,
            backend="sobtop",
            forcefield_family="gaff_uff",
            components=[component],
        )

        manifest = load_manifest(tmp_path)

        assert manifest["backend"] == "sobtop"
        assert manifest["components"][0]["molecule_id"] == "A"

    def test_invalid_unified_manifest_never_falls_back_to_legacy_topology_data(self, tmp_path):
        component = TopologyManifestComponent(
            molecule_id="A", residue_name="A", quantity=1,
            mol2=str(tmp_path / "A.mol2"), chg=str(tmp_path / "A.chg"),
            charge=0, spin=1, smiles=None, backend="sobtop", forcefield_family="gaff_uff",
        )
        write_manifest(
            tmp_path,
            backend="sobtop",
            forcefield_family="gaff_uff",
            components=[component],
        )
        (tmp_path / "run_manifest.json").write_text("{}")

        with pytest.raises(RunMetadataError):
            load_manifest(tmp_path)

    def test_topology_assembly_reads_and_updates_unified_section(self, tmp_path):
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps(_config("sobtop", "gaff_uff", {"SOL": 2})))
        itp = tmp_path / "SOL.itp"
        gro = tmp_path / "SOL.gro"
        _write_itp(itp, atomtype="SOL")
        _write_gro(gro)
        create_run_manifest(tmp_path)
        write_manifest(
            tmp_path,
            backend="sobtop",
            forcefield_family="gaff_uff",
            components=[TopologyManifestComponent(
                molecule_id="SOL", residue_name="SOL", quantity=2,
                mol2=str(tmp_path / "SOL.mol2"), chg=str(tmp_path / "SOL.chg"),
                charge=0, spin=1, smiles=None, backend="sobtop", forcefield_family="gaff_uff",
                itp=str(itp), gro=str(gro), success=True, validated=True,
            )],
        )

        result = build(config_path=str(config_path), topo_dir=str(tmp_path))

        topology = load_run_manifest(tmp_path)["sections"]["topology"]
        assert result.success is True
        assert topology["revision"] == 2
        assert topology["data"]["components"][0]["assembly_itp"] == str(tmp_path / ".assembly_itp" / "SOL.itp")
        assert (tmp_path / "topol.top").is_file()


class TestTopologyConfig:
    def test_amber_and_unregistered_values_are_rejected(self):
        for topology in (
            {"backend": "sobtop", "force_field": "amber"},
            {"backend": "unknown", "force_field": "gaff_uff"},
            {"backend": "oplsaa", "force_field": "gaff_uff"},
        ):
            assert validate_config({"residues": {"A": 1}, "molecules": {"A": {}}, "topology": topology})

    def test_legacy_contradictory_ligpargen_gaff_migrates_to_sobtop(self):
        topology, issues, migrated = normalize_topology_config({"backend": "ligpargen", "force_field": "gaff"})
        assert issues == []
        assert migrated is True
        assert topology == {"backend": "sobtop", "force_field": "gaff_uff"}

    def test_legacy_ligpargen_opls_migrates_to_oplsaa(self):
        topology, issues, migrated = normalize_topology_config({"backend": "ligpargen", "force_field": "opls"})
        assert issues == []
        assert migrated is True
        assert topology == {"backend": "oplsaa", "force_field": "oplsaa"}


class TestTopologyDispatcher:
    def test_dispatch_rejects_path_like_residue_name(self, tmp_path):
        escaped = tmp_path.parent / "escaped.itp"
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps({
            "residues": {"../escaped": 1},
            "molecules": {"../escaped": {"charge": 0, "spin": 1}},
            "topology": {"backend": "oplsaa", "force_field": "oplsaa"},
        }))

        result = dispatch_topology(config_path, tmp_path)

        assert result[0].success is False
        assert result[0].error.kind is ErrorKind.CONFIG_INVALID
        assert escaped.exists() is False

    def test_sobtop_dispatch_uses_only_config_registered_components(self, tmp_path, monkeypatch):
        config = _config("sobtop", "gaff_uff", {"A": 2})
        config["molecules"]["A"]["charge"] = -1
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps(config))
        (tmp_path / "STALE.mol2").write_text("not an input")
        seen = []
        activities = []

        def fake_prepare(self, plan):
            return StepResult("prepare", 4, True)

        def fake_parameterize(self, component, workspace):
            seen.append(component.molecule_id)
            itp, gro = workspace / "A.itp", workspace / "A.gro"
            _write_itp(itp)
            _write_gro(gro)
            return _result_for(itp, gro, "topo_gaff")

        monkeypatch.setattr(SobtopBackend, "prepare", fake_prepare)
        monkeypatch.setattr(SobtopBackend, "parameterize", fake_parameterize)
        results = dispatch_topology(config_path, tmp_path, on_progress=activities.append)

        assert [result.success for result in results] == [True]
        assert seen == ["A"]
        manifest = load_manifest(tmp_path)
        assert manifest["backend"] == "sobtop"
        assert manifest["components"][0]["validated"] is True
        assert manifest["components"][0]["molecule_id"] == "A"
        assert manifest["components"][0]["chg"] == str(tmp_path / "A.chg")
        assert activities[-1] == {
            "tool": "Sobtop", "operation": "拓扑参数化",
            "target_type": "molecule", "target": "A", "current": 1, "total": 1,
        }

    def test_dispatch_does_not_reset_existing_retry_ledger(self, tmp_path, monkeypatch):
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps(_config("sobtop", "gaff_uff", {"A": 1})))
        write_manifest(
            tmp_path,
            backend="sobtop",
            forcefield_family="gaff_uff",
            components=[],
            retry_ledger={"sobtop:A:parameterize": 2},
        )
        monkeypatch.setattr(SobtopBackend, "prepare", lambda self, plan: StepResult("prepare", 4, True))

        def fake_parameterize(self, component, workspace):
            itp, gro = workspace / "A.itp", workspace / "A.gro"
            _write_itp(itp)
            _write_gro(gro)
            return _result_for(itp, gro, "topo_gaff")

        monkeypatch.setattr(SobtopBackend, "parameterize", fake_parameterize)
        assert all(result.success for result in dispatch_topology(config_path, tmp_path))
        assert load_manifest(tmp_path)["retry_ledger"] == {"sobtop:A:parameterize": 2}

    def test_opls_dispatch_passes_each_component_real_charge(self, tmp_path, monkeypatch):
        config = _config("oplsaa", "oplsaa", {"NEUTRAL": 1, "ANION": 1})
        config["molecules"]["NEUTRAL"]["charge"] = 0
        config["molecules"]["ANION"]["charge"] = -1
        config["topology"].update({"default_lbcc": False, "default_opt_steps": 1})
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps(config))
        inputs = []
        activities = []

        monkeypatch.setattr(OplsaaBackend, "prepare", lambda self, plan: StepResult("prepare", 4, True))

        def fake_parameterize(self, component, workspace):
            inputs.append((component.molecule_id, component.charge, component.lbcc, component.opt_steps))
            itp, gro = workspace / f"{component.residue_name}.itp", workspace / f"{component.residue_name}.gro"
            _write_itp(itp)
            _write_gro(gro)
            return _result_for(itp, gro, "topo_opls")

        monkeypatch.setattr(OplsaaBackend, "parameterize", fake_parameterize)
        assert all(result.success for result in dispatch_topology(config_path, tmp_path, on_progress=activities.append))
        assert inputs == [("NEUTRAL", 0, False, 1), ("ANION", -1, False, 1)]
        manifest = load_manifest(tmp_path)
        assert all(component["chg"] is None for component in manifest["components"])
        assert [(component["lbcc"], component["opt_steps"]) for component in manifest["components"]] == [(False, 1), (False, 1)]
        assert [(item["tool"], item["target"], item["current"], item["total"]) for item in activities[1:]] == [
            ("LigParGen", "NEUTRAL", 1, 2),
            ("LigParGen", "ANION", 2, 2),
        ]

    def test_opls_backend_passes_charge_and_opls_options_to_ligpargen(self, tmp_path, monkeypatch):
        import willy.topology.topo_opls as topo_opls

        observed = {}

        def fake_make(inp, output_dir):
            observed.update({
                "net_charge": inp.net_charge,
                "lbcc": inp.lbcc,
                "opt_steps": inp.opt_steps,
                "output_dir": output_dir,
            })
            return StepResult("topo_opls", 4, False)

        monkeypatch.setattr(topo_opls, "make_itp_gro_opls", fake_make)
        component = TopologyComponent(
            molecule_id="ANION", residue_name="ANION", quantity=1,
            mol2=tmp_path / "ANION.mol2", chg=None, charge=-1, spin=1,
            lbcc=False, opt_steps=2,
        )
        OplsaaBackend().parameterize(component, tmp_path)
        assert observed == {"net_charge": -1, "lbcc": False, "opt_steps": 2, "output_dir": str(tmp_path)}


class TestSobtopExecutor:
    def _patch_vendor(self, tmp_path, monkeypatch):
        import willy.topology.topo_gaff as topo_gaff

        vendor = tmp_path / "sobtop"
        vendor.mkdir()
        (vendor / "sobtop").write_text("placeholder")
        monkeypatch.setattr(topo_gaff, "SOBTOP_DIR", vendor)
        monkeypatch.setattr(topo_gaff, "SOBTOP_BIN", vendor / "sobtop")
        monkeypatch.setattr(topo_gaff, "_LOCK_PATH", vendor / ".lock")
        monkeypatch.setattr(topo_gaff, "check_sobtop_ready", lambda: [])
        return topo_gaff, vendor

    def _input(self, tmp_path):
        mol2 = tmp_path / "MOL.mol2"
        chg = tmp_path / "MOL.chg"
        mol2.write_text("@<TRIPOS>MOLECULE\nMOL\n")
        chg.write_text("C 0 0 0 0\n")
        return SobtopInput(str(mol2), str(chg), "MOL")

    def test_frozen_menu_sequence(self, tmp_path):
        inp = self._input(tmp_path)
        builder = SobtopInputBuilder(inp, work_dir=tmp_path / "vendor")
        assert builder.build().splitlines() == [
            str((tmp_path / "MOL.mol2").resolve()), "7", "10", str((tmp_path / "MOL.chg").resolve()),
            "0", "1", "2", "4", str(tmp_path / "vendor" / "MOL.top"),
            str(tmp_path / "vendor" / "MOL.itp"), "2", str(tmp_path / "vendor" / "MOL.gro"), "0",
        ]

    def test_old_vendor_output_cannot_make_failed_run_succeed(self, tmp_path, monkeypatch):
        topo_gaff, vendor = self._patch_vendor(tmp_path, monkeypatch)
        inp = self._input(tmp_path)
        _write_itp(vendor / "MOL.itp")
        _write_gro(vendor / "MOL.gro")
        (vendor / "MOL.top").write_text("old")
        monkeypatch.setattr(topo_gaff, "run_managed_command", lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout="failed", stderr=""))

        result = make_itp_gro(inp, str(tmp_path / "run"))

        assert result.success is False
        assert not (tmp_path / "run" / "MOL.itp").exists()
        assert not any((vendor / f"MOL.{ext}").exists() for ext in ("itp", "gro", "top"))

    def test_rc24_requires_complete_valid_current_outputs(self, tmp_path, monkeypatch):
        topo_gaff, vendor = self._patch_vendor(tmp_path, monkeypatch)
        inp = self._input(tmp_path)

        def create_valid(*args, **kwargs):
            _write_itp(vendor / "MOL.itp")
            _write_gro(vendor / "MOL.gro")
            (vendor / "MOL.top").write_text("top")
            return SimpleNamespace(returncode=24, stdout="fortran cleanup", stderr="")

        monkeypatch.setattr(topo_gaff, "run_managed_command", create_valid)
        result = make_itp_gro(inp, str(tmp_path / "run"))
        assert result.success is True
        assert result.extra["accepted_rc24"] is True
        assert (tmp_path / "run" / "MOL.itp").is_file()

    def test_rc24_accepts_current_outputs_with_coarse_mtime_resolution(self, tmp_path, monkeypatch):
        topo_gaff, vendor = self._patch_vendor(tmp_path, monkeypatch)
        inp = self._input(tmp_path)

        def create_valid(*args, **kwargs):
            outputs = [vendor / "MOL.itp", vendor / "MOL.gro", vendor / "MOL.top"]
            _write_itp(outputs[0])
            _write_gro(outputs[1])
            outputs[2].write_text("top")
            rounded_mtime = time.time_ns() - 1_000_000_000
            for path in outputs:
                os.utime(path, ns=(rounded_mtime, rounded_mtime))
            return SimpleNamespace(returncode=24, stdout="fortran cleanup", stderr="")

        monkeypatch.setattr(topo_gaff, "run_managed_command", create_valid)
        result = make_itp_gro(inp, str(tmp_path / "run"))

        assert result.success is True
        assert result.extra["accepted_rc24"] is True

    def test_rc24_with_invalid_outputs_fails_and_cleans_vendor(self, tmp_path, monkeypatch):
        topo_gaff, vendor = self._patch_vendor(tmp_path, monkeypatch)
        inp = self._input(tmp_path)

        def create_invalid(*args, **kwargs):
            _write_itp(vendor / "MOL.itp")
            _write_gro(vendor / "MOL.gro", atom_count=2)
            (vendor / "MOL.top").write_text("top")
            return SimpleNamespace(returncode=24, stdout="fortran cleanup", stderr="")

        monkeypatch.setattr(topo_gaff, "run_managed_command", create_invalid)
        result = make_itp_gro(inp, str(tmp_path / "run"))
        assert result.success is False
        assert not (tmp_path / "run" / "MOL.itp").exists()
        assert not any((vendor / f"MOL.{ext}").exists() for ext in ("itp", "gro", "top"))

    def test_startup_oserror_is_a_dependency_step_result(self, tmp_path, monkeypatch):
        topo_gaff, _ = self._patch_vendor(tmp_path, monkeypatch)
        inp = self._input(tmp_path)
        monkeypatch.setattr(topo_gaff, "run_managed_command", lambda *args, **kwargs: (_ for _ in ()).throw(FileNotFoundError("sobtop")))

        result = make_itp_gro(inp, str(tmp_path / "run"))

        assert result.success is False
        assert result.error.kind is ErrorKind.DEPENDENCY_MISSING

    def test_output_directory_must_be_explicit(self, tmp_path):
        result = make_itp_gro(SobtopInput("missing.mol2", "missing.chg", "SOL"))

        assert result.success is False
        assert result.error.kind is ErrorKind.INPUT_CONTRACT

    @pytest.mark.external
    def test_real_sobtop_minimal_integration_when_available(self, tmp_path):
        """Exercise Sobtop only with a hash-verified external EC fixture bundle."""
        from willy.external_smoke import (
            fixture_root,
            get_smoke_case,
            preflight,
            selected_smoke_cases,
            smoke_required,
            write_smoke_evidence,
        )
        from willy.topology.topo_gaff import SOBTOP_DIR, check_sobtop_ready

        case = get_smoke_case("sobtop_ec")
        if case.case_id not in {item.case_id for item in selected_smoke_cases()}:
            pytest.skip("sobtop_ec 未被 WILLY_EXTERNAL_SMOKE_CASES 选中")
        root = fixture_root()
        readiness = preflight(case, root=root)
        backend_available = not check_sobtop_ready()
        if not readiness.ready or not backend_available:
            summary = "sobtop_backend_unavailable" if not backend_available else "fixture_bundle_unready"
            write_smoke_evidence(
                case, readiness, success=False, root=root, phase="execution",
                execution_attempted=False, command_label="topology.make_itp_gro",
                failure_summary=summary,
            )
            if smoke_required():
                pytest.fail("Sobtop 或已校验的最小 EC fixture 不可用")
            pytest.skip("Sobtop 或已校验的最小 EC fixture 不可用")

        assert root is not None
        result = make_itp_gro(
            SobtopInput(str(root / "sobtop_ec" / "EC.mol2"), str(root / "sobtop_ec" / "EC.chg"), "EC"),
            str(tmp_path / "run"),
        )
        outputs = (tmp_path / "run" / "EC.itp", tmp_path / "run" / "EC.gro")
        write_smoke_evidence(
            case, readiness, success=result.success, outputs=outputs, root=tmp_path,
            phase="execution", execution_attempted=True,
            command_label="topology.make_itp_gro",
            failure_summary="sobtop_step_result_failed" if not result.success else "",
        )

        assert result.success is True, result.error.message if result.error else "Sobtop failed"
        assert all(path.is_file() for path in outputs)
        assert not any((SOBTOP_DIR / f"EC.{ext}").exists() for ext in ("itp", "gro", "top"))


class TestOplsExecutor:
    def _patch_ligpargen(self, tmp_path, monkeypatch):
        import willy.topology.topo_opls as topo_opls

        tmp_root = tmp_path / "ligpargen-tmp"
        tmp_root.mkdir()
        monkeypatch.setattr(topo_opls, "LIGPARGEN_TMP_DIR", tmp_root)
        monkeypatch.setattr(topo_opls, "check_ligpargen_ready", lambda: [])
        executables = {
            "ligpargen": tmp_root / "LigParGen",
            "obabel": tmp_root / "obabel",
            "csh": tmp_root / "csh",
        }
        monkeypatch.setattr(
            topo_opls,
            "require_tool",
            lambda tool_id: SimpleNamespace(executable=executables[tool_id]),
        )
        monkeypatch.setattr(topo_opls, "build_tool_env", lambda tool_id: {"PATH": "/usr/bin"})
        return topo_opls, tmp_root

    def test_same_residue_concurrent_runs_are_isolated(self, tmp_path, monkeypatch):
        topo_opls, tmp_root = self._patch_ligpargen(tmp_path, monkeypatch)
        prefixes = []
        barrier = Barrier(2)

        def fake_run(cmd, **kwargs):
            prefix = cmd[cmd.index("-r") + 1]
            prefixes.append(prefix)
            barrier.wait(timeout=5)
            itp = tmp_root / f"{prefix}.itp"
            gro = tmp_root / f"{prefix}.gro"
            _write_itp(itp)
            _write_gro(gro)
            return SimpleNamespace(returncode=0, stdout="ok", stderr="")

        monkeypatch.setattr(topo_opls, "run_managed_command", fake_run)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(
                    make_itp_gro_opls,
                    LigParGenInput(smiles="CC", output_name="SOL"),
                    str(tmp_path / f"run-{index}"),
                )
                for index in range(2)
            ]
        results = [future.result() for future in futures]

        assert all(result.success for result in results)
        assert len(set(prefixes)) == 2
        assert all("SOL 3" in Path(result.outputs["itp"]).read_text() for result in results)
        assert list(tmp_root.iterdir()) == []

    def test_ligpargen_gro_restores_five_column_residue_name(self, tmp_path, monkeypatch):
        topo_opls, tmp_root = self._patch_ligpargen(tmp_path, monkeypatch)
        observed = {}

        def fake_run(cmd, **kwargs):
            prefix = cmd[cmd.index("-r") + 1]
            observed["prefix"] = prefix
            _write_itp(tmp_root / f"{prefix}.itp")
            _write_ligpargen_gro(tmp_root / f"{prefix}.gro", prefix)
            return SimpleNamespace(returncode=0, stdout="ok", stderr="")

        monkeypatch.setattr(topo_opls, "run_managed_command", fake_run)

        result = make_itp_gro_opls(
            LigParGenInput(smiles="CC", output_name="LONGER"), str(tmp_path / "run"),
        )

        assert result.success is True
        atom_line = Path(result.outputs["gro"]).read_text().splitlines()[2]
        assert atom_line[:5] == "    1"
        assert atom_line[5:10] == "LONGE"
        assert observed["prefix"] not in atom_line
        assert atom_line[10:] == "  C01    1   0.000   0.000   0.000"
        assert result.extra["gro_residue_name"] == "LONGE"

    def test_ligpargen_gets_a_private_legacy_babel_compatibility_entry(self, tmp_path, monkeypatch):
        topo_opls, tmp_root = self._patch_ligpargen(tmp_path, monkeypatch)
        observed = {}

        def fake_run(cmd, **kwargs):
            observed["path"] = kwargs["env"]["PATH"]
            observed["pythonpath"] = kwargs["env"]["PYTHONPATH"]
            compat_dir = Path(observed["path"].split(":", 1)[0])
            observed["babel_wrapper"] = (compat_dir / "babel").read_text()
            observed["networkx_compat"] = (compat_dir / "sitecustomize.py").read_text()
            observed["csh_target"] = (compat_dir / "csh").readlink()
            prefix = cmd[cmd.index("-r") + 1]
            _write_itp(tmp_root / f"{prefix}.itp")
            _write_gro(tmp_root / f"{prefix}.gro")
            return SimpleNamespace(returncode=0, stdout="ok", stderr="")

        monkeypatch.setattr(topo_opls, "run_managed_command", fake_run)

        result = make_itp_gro_opls(
            LigParGenInput(smiles="CC", output_name="SOL"),
            str(tmp_path / "run"),
        )

        assert result.success is True
        assert str(tmp_root / "obabel") in observed["babel_wrapper"]
        assert "'-omol'" in observed["babel_wrapper"]
        assert "'-O'" in observed["babel_wrapper"]
        assert "DiGraph.node" in observed["networkx_compat"]
        assert "DataFrame.ix" in observed["networkx_compat"]
        assert "join_axes" in observed["networkx_compat"]
        assert "_legacy_drop" in observed["networkx_compat"]
        assert observed["csh_target"] == tmp_root / "csh"
        assert observed["path"].split(":", 1)[0] == observed["pythonpath"].split(":", 1)[0]
        assert not Path(observed["path"].split(":", 1)[0]).exists()

    def test_babel_wrapper_keeps_parent_python_with_spaces_and_unrelated_path(self, tmp_path, monkeypatch):
        from willy import python_runtime
        from willy.topology import topo_opls

        interpreter = tmp_path / "python environment" / "python"
        interpreter.parent.mkdir()
        interpreter.symlink_to(sys.executable)
        monkeypatch.setattr(python_runtime.sys, "executable", str(interpreter))
        monkeypatch.setattr(topo_opls, "LIGPARGEN_TMP_DIR", tmp_path)
        obabel = tmp_path / "Open Babel"
        obabel.write_text("#!/bin/sh\nprintf '%s\\n' \"$@\"\n")
        obabel.chmod(0o755)
        other_bin = tmp_path / "other-bin"
        other_bin.mkdir()
        wrong_python = other_bin / "python3"
        wrong_python.write_text("#!/bin/sh\nexit 87\n")
        wrong_python.chmod(0o755)

        environment = topo_opls._prepare_ligpargen_child_env({}, "SOL_probe", obabel, Path("/bin/sh"))
        wrapper = Path(environment["PATH"]) / "babel"
        result = subprocess.run(
            [str(wrapper), "-imol2", "input molecule.mol2", "-omol", "output molecule.mol"],
            env={"PATH": str(other_bin)}, capture_output=True, text=True, timeout=10, check=False,
        )

        assert result.returncode == 0, result.stderr
        assert result.stdout.splitlines() == [
            "-imol2", "input molecule.mol2", "-omol", "-O", "output molecule.mol",
        ]
        assert "/usr/bin/env python3" not in wrapper.read_text()

    def test_startup_oserror_is_a_dependency_step_result(self, tmp_path, monkeypatch):
        topo_opls, _ = self._patch_ligpargen(tmp_path, monkeypatch)
        monkeypatch.setattr(topo_opls, "run_managed_command", lambda *args, **kwargs: (_ for _ in ()).throw(FileNotFoundError("LigParGen")))

        result = make_itp_gro_opls(LigParGenInput(smiles="CC", output_name="SOL"), str(tmp_path / "run"))

        assert result.success is False
        assert result.error.kind is ErrorKind.DEPENDENCY_MISSING

    def test_output_directory_must_be_explicit(self):
        result = make_itp_gro_opls(LigParGenInput(smiles="CC", output_name="SOL"))

        assert result.success is False
        assert result.error.kind is ErrorKind.INPUT_CONTRACT

    def test_path_like_output_name_is_rejected_before_any_write(self, tmp_path):
        result = make_itp_gro_opls(
            LigParGenInput(smiles="CC", output_name="../escaped"),
            str(tmp_path / "run"),
        )

        assert result.success is False
        assert result.error.kind is ErrorKind.CONFIG_INVALID
        assert not (tmp_path / "escaped.itp").exists()
        assert not (tmp_path / "escaped.gro").exists()

    def test_existing_output_symlink_cannot_escape_run_directory(self, tmp_path, monkeypatch):
        _, _ = self._patch_ligpargen(tmp_path, monkeypatch)
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        outside = tmp_path.parent / "escaped.itp"
        (run_dir / "SOL.itp").symlink_to(outside)

        result = make_itp_gro_opls(LigParGenInput(smiles="CC", output_name="SOL"), str(run_dir))

        assert result.success is False
        assert result.error.kind is ErrorKind.CONFIG_INVALID
        assert outside.exists() is False

    def test_moleculetype_restore_failure_is_structured_and_cleans_outputs(self, tmp_path, monkeypatch):
        topo_opls, tmp_root = self._patch_ligpargen(tmp_path, monkeypatch)

        def fake_run(cmd, **kwargs):
            prefix = cmd[cmd.index("-r") + 1]
            _write_itp(tmp_root / f"{prefix}.itp")
            _write_gro(tmp_root / f"{prefix}.gro")
            return SimpleNamespace(returncode=0, stdout="ok", stderr="")

        monkeypatch.setattr(topo_opls, "run_managed_command", fake_run)
        monkeypatch.setattr(topo_opls, "_restore_moleculetype_name", lambda *_: False)

        result = make_itp_gro_opls(LigParGenInput(smiles="CC", output_name="SOL"), str(tmp_path / "run"))

        assert result.success is False
        assert result.error.kind is ErrorKind.LIGPARGEN_FAILED
        assert not (tmp_path / "run" / "SOL.itp").exists()
        assert not (tmp_path / "run" / "SOL.gro").exists()


class TestTopologyRetryLedger:
    def test_config_tool_updates_opls_options_used_by_retry(self, tmp_path, monkeypatch):
        from willy.toolist_topology import handle_topology_tool_call

        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps(_config("oplsaa", "oplsaa", {"SOL": 1})))
        _write_manifest_for(tmp_path, "oplsaa", "oplsaa", ["SOL"])
        updated = json.loads(handle_topology_tool_call(
            "tools_modify_config_topology",
            {"default_lbcc": False, "default_opt_steps": 3},
            work_dir=str(tmp_path), config_path=str(config_path),
        ))
        observed = {}
        monkeypatch.setattr(
            OplsaaBackend,
            "parameterize",
            lambda self, component, workspace: observed.update(
                {"lbcc": component.lbcc, "opt_steps": component.opt_steps},
            ) or StepResult("topo_opls", 4, False, error=StepError(ErrorKind.LIGPARGEN_FAILED, "failed")),
        )

        retried = json.loads(handle_topology_tool_call(
            "tools_retry_topo_opls", {"molecule_name": "SOL"}, work_dir=str(tmp_path),
        ))

        assert updated["ok"] is True
        assert retried["error_kind"] == ErrorKind.LIGPARGEN_FAILED.value
        assert observed == {"lbcc": False, "opt_steps": 3}
        assert load_manifest(tmp_path)["components"][0]["opt_steps"] == 3

    def test_retry_budget_is_persisted_and_enforced_by_backend_tool(self, tmp_path, monkeypatch):
        from willy.toolist_topology import handle_topology_tool_call

        _write_manifest_for(tmp_path, "sobtop", "gaff_uff", ["A"])
        monkeypatch.setattr(
            SobtopBackend,
            "parameterize",
            lambda self, component, workspace: StepResult(
                "topo_gaff", 4, False,
                error=StepError(ErrorKind.SOBTOP_FAILED, "failed"),
            ),
        )

        responses = [json.loads(handle_topology_tool_call(
            "tools_retry_topo_gaff", {"molecule_name": "A"}, work_dir=str(tmp_path),
        )) for _ in range(3)]

        assert [response["error_kind"] for response in responses[:2]] == [ErrorKind.SOBTOP_FAILED.value] * 2
        assert responses[2]["error_kind"] == ErrorKind.RETRY_LIMIT_EXCEEDED.value
        assert load_manifest(tmp_path)["retry_ledger"] == {"sobtop:A:parameterize": 2}

    def test_total_retry_budget_rejects_fifth_claim(self):
        manifest = {}
        for index in range(MAX_TOPOLOGY_RETRIES):
            granted, _ = claim_retry(
                manifest, backend="sobtop", molecule_id=f"M{index}", action="parameterize",
            )
            assert granted is True
        granted, reason = claim_retry(manifest, backend="sobtop", molecule_id="overflow", action="parameterize")
        assert granted is False
        assert "总上限" in reason


class TestArtifactAndAssemblyValidation:
    def test_successful_assembly_retry_keeps_atomtypes(self, tmp_path):
        from willy.toolist_topology import handle_topology_tool_call

        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps(_config("sobtop", "gaff_uff", {"A": 1})))
        _write_manifest_for(tmp_path, "sobtop", "gaff_uff", ["A"])

        first = build(str(config_path), str(tmp_path))
        retried = json.loads(handle_topology_tool_call(
            "tools_retry_top_assembly", {}, work_dir=str(tmp_path), config_path=str(config_path),
        ))

        source = tmp_path / "A.itp"
        assembled = tmp_path / ".assembly_itp" / "A.itp"
        topol = (tmp_path / "topol.top").read_text()
        assert first.success is True
        assert retried["success"] is True
        assert "CT" in topol
        assert "[ atomtypes ]" in source.read_text()
        assert "[ atomtypes ]" not in assembled.read_text()
        assert load_manifest(tmp_path)["components"][0]["assembly_itp"] == str(assembled)

    def test_assembly_namespace_does_not_overwrite_similarly_named_source(self, tmp_path):
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps(_config("sobtop", "gaff_uff", {"A": 1, "A.assembled": 1})))
        _write_manifest_for(tmp_path, "sobtop", "gaff_uff", ["A", "A.assembled"])
        similarly_named_source = tmp_path / "A.assembled.itp"
        source_before = similarly_named_source.read_text()

        result = build(str(config_path), str(tmp_path))

        assembly_dir = tmp_path / ".assembly_itp"
        topol = (tmp_path / "topol.top").read_text()
        assert result.success is True
        assert similarly_named_source.read_text() == source_before
        assert (assembly_dir / "A.itp").is_file()
        assert (assembly_dir / "A.assembled.itp").is_file()
        assert '#include ".assembly_itp/A.itp"' in topol
        assert '#include ".assembly_itp/A.assembled.itp"' in topol

    def test_missing_atomtypes_prevents_assembly(self, tmp_path):
        config = _config("sobtop", "gaff_uff", {"A": 1})
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps(config))
        _write_manifest_for(tmp_path, "sobtop", "gaff_uff", ["A"])
        (tmp_path / "A.itp").write_text(
            "[ moleculetype ]\nA 3\n\n[ atoms ]\n1 CT 1 A C1 1 0.0 12.01\n"
        )

        result = build(str(config_path), str(tmp_path))

        assert result.success is False
        assert "atomtypes" in result.error.message
        assert not (tmp_path / "topol.top").exists()

    def test_undefined_atomtype_reference_prevents_assembly(self, tmp_path):
        config = _config("sobtop", "gaff_uff", {"A": 1})
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps(config))
        _write_manifest_for(tmp_path, "sobtop", "gaff_uff", ["A"])
        (tmp_path / "A.itp").write_text(
            "[ atomtypes ]\nCT 6 12.01 0.0 A 0.34 0.2\n\n"
            "[ moleculetype ]\nA 3\n\n[ atoms ]\n1 HC 1 A H1 1 0.0 1.01\n"
        )

        result = build(str(config_path), str(tmp_path))

        assert result.success is False
        assert "HC" in result.error.message

    def test_moleculetype_must_match_manifest_residue(self, tmp_path):
        config = _config("sobtop", "gaff_uff", {"SOL": 1})
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps(config))
        _write_manifest_for(tmp_path, "sobtop", "gaff_uff", ["SOL"])
        (tmp_path / "SOL.itp").write_text(
            "[ atomtypes ]\nCT 6 12.01 0.0 A 0.34 0.2\n\n"
            "[ moleculetype ]\nOTHER 3\n\n[ atoms ]\n1 CT 1 OTHER C1 1 0.0 12.01\n"
        )

        result = build(str(config_path), str(tmp_path))

        assert result.success is False
        assert "OTHER" in result.error.message
        assert "SOL" in result.error.message
        assert not (tmp_path / "topol.top").exists()

    def test_missing_sections_and_atom_count_mismatch_fail(self, tmp_path):
        itp = tmp_path / "A.itp"
        gro = tmp_path / "A.gro"
        itp.write_text("[ moleculetype ]\nA 3\n")
        _write_gro(gro)
        assert validate_topology_files(itp, gro, step_name="topo", error_kind=ErrorKind.SOBTOP_FAILED).success is False
        _write_itp(itp)
        _write_gro(gro, atom_count=2)
        assert validate_topology_files(itp, gro, step_name="topo", error_kind=ErrorKind.SOBTOP_FAILED).success is False

    def test_validate_moleculetype_against_expected_residue(self, tmp_path):
        itp = tmp_path / "OTHER.itp"
        gro = tmp_path / "OTHER.gro"
        _write_itp(itp)
        _write_gro(gro)

        result = validate_topology_files(
            itp, gro, step_name="topo", error_kind=ErrorKind.SOBTOP_FAILED,
            expected_moleculetype="SOL",
        )

        assert result.success is False
        assert "应为 'SOL'" in result.error.message

    def test_manifest_missing_gro_prevents_assembly(self, tmp_path):
        config = _config("sobtop", "gaff_uff", {"A": 1})
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps(config))
        _write_manifest_for(tmp_path, "sobtop", "gaff_uff", ["A"])
        (tmp_path / "A.gro").unlink()
        result = build(str(config_path), str(tmp_path))
        assert result.success is False
        assert result.error.kind is ErrorKind.CONFIG_INVALID

    def test_manifest_artifact_tampering_prevents_assembly(self, tmp_path):
        config = _config("sobtop", "gaff_uff", {"A": 1})
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps(config))
        _write_manifest_for(tmp_path, "sobtop", "gaff_uff", ["A"])
        _write_gro(tmp_path / "A.gro", atom_count=2)

        result = build(str(config_path), str(tmp_path))

        assert result.success is False
        assert "原子数不一致" in result.error.message

    def test_gaff_uff_components_assemble_but_cross_family_is_rejected(self, tmp_path):
        config = _config("sobtop", "gaff_uff", {"GAFF": 1, "UFF": 1})
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps(config))
        _write_manifest_for(tmp_path, "sobtop", "gaff_uff", ["GAFF", "UFF"])
        success = build(str(config_path), str(tmp_path))
        assert success.success is True
        assert "#define GAFF" in (tmp_path / "topol.top").read_text()

        _write_manifest_for(tmp_path, "sobtop", "oplsaa", ["GAFF", "UFF"])
        failed = build(str(config_path), str(tmp_path))
        assert failed.success is False

    def test_atomtype_parameter_conflict_is_fatal(self, tmp_path):
        config = _config("sobtop", "gaff_uff", {"A": 1, "B": 1})
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps(config))
        _write_manifest_for(tmp_path, "sobtop", "gaff_uff", ["A", "B"])
        _write_itp(tmp_path / "B.itp", atomtype="CT", atomtype_params="6 12.01 0.0 A 0.36 0.2")

        result = build(str(config_path), str(tmp_path))
        assert result.success is False
        assert result.error.kind is ErrorKind.ATOMTYPE_CONFLICT

    def test_itp_revision_is_section_aware_and_idempotent(self, tmp_path):
        itp = tmp_path / "SOL.itp"
        itp.write_text(
            "; before\n[ atomtypes ]\nCT  6 12.01 0.0 A 0.34 0.2\n"
            "; no blank before next section\n[ moleculetype ]\nSOL 3\n\n[ atoms ]\n"
            "1  CT  1  MOL  C1  1  0.0  12.01 ; keep this comment\n\n[ bonds ]\n1 2 1\n"
        )
        assert revise_itp(str(itp)) is True
        revised = itp.read_text()
        assert "[ atomtypes ]" not in revised
        assert "; keep this comment" in revised
        assert "1  CT  1  SOL  C1" in revised
        assert "[ bonds ]\n1 2 1" in revised
        assert revise_itp(str(itp)) is False

    def test_atomtype_conflict_escalates_without_llm_retry(self):
        from willy.agent_topology import TopologyAgent
        from willy.errors import StepError

        agent = TopologyAgent(llm_client=MagicMock())
        result = agent.handle_failure(StepResult(
            "top_assembly", 5, False,
            error=StepError(ErrorKind.ATOMTYPE_CONFLICT, "CT differs across ITP files"),
        ))

        assert result.escalated is True
        assert result.extra["escalation"]["attempts_made"] == 0
        assert "独立 OPLS-AA run" in result.extra["escalation"]["backup_plan"]
