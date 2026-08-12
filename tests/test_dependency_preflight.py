"""Focused tests for the advisory grouped dependency preflight."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from willy.dependency_preflight import run_dependency_preflight
from willy.env_registry import AVAILABLE, MISSING, ResolvedTool, TOOL_SPECS


def _vendor_sobtop(root: Path) -> None:
    sobtop = root / "vendor" / "sobtop"
    sobtop.mkdir(parents=True)
    for name in ("sobtop", "atomtype"):
        path = sobtop / name
        path.write_text("#!/bin/sh\nexit 0\n")
        path.chmod(0o755)
    for name in ("sobtop.ini", "LJ_param.dat", "bonded_param.dat"):
        (sobtop / name).write_text("fixture\n")
    for name in ("obabel", "obabel.bin", "libopenbabel.so.7", "libcoordgen.so.3"):
        path = root / "vendor" / name
        path.write_text("fixture\n")
        if name in {"obabel", "obabel.bin"}:
            path.chmod(0o755)


def _resolver(available: set[str], root: Path):
    def resolve(tool_id: str, *, project_root: Path):
        assert project_root == root
        spec = TOOL_SPECS[tool_id]
        if tool_id not in available:
            return ResolvedTool(tool_id, spec.label, MISSING, source="path", public_reason="未找到可执行文件")
        path = root / "external" / tool_id
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/bin/sh\nexit 0\n")
        path.chmod(0o755)
        home = path.parent if tool_id in {"orca", "boss"} else None
        source = "bundled" if tool_id in {"multiwfn", "packmol"} else "path"
        return ResolvedTool(tool_id, spec.label, AVAILABLE, executable=path, home=home, source=source)

    return resolve


def test_preflight_accepts_one_complete_route_per_group_and_persists_path_defaults(tmp_path, monkeypatch):
    _vendor_sobtop(tmp_path)
    available = {"g16", "formchk", "multiwfn", "gmx", "packmol"}
    monkeypatch.setattr("willy.dependency_preflight.resolve_tool", _resolver(available, tmp_path))
    (tmp_path / ".env").write_text("UNRELATED_SETTING=keep\n")

    report = run_dependency_preflight(tmp_path)

    assert report["advisory"] is True
    assert report["ready"] is True
    assert [group["ready"] for group in report["groups"]] == [True, True, True]
    assert "当前依赖满足完成完整 MD 流程的最小链路" in report["markdown"]
    assert "G16（满足；来源：path）" in report["markdown"]
    assert "内置 Multiwfn（满足；来源：bundled）" in report["markdown"]
    assert "本次已写入默认配置：WILLY_G16_BIN, WILLY_FORMCHK_BIN, WILLY_GMX_BIN。" in report["markdown"]
    assert report["written_defaults"] == ["WILLY_G16_BIN", "WILLY_FORMCHK_BIN", "WILLY_GMX_BIN"]
    dotenv = (tmp_path / ".env").read_text()
    assert "UNRELATED_SETTING=keep" in dotenv
    assert "WILLY_G16_BIN=" in dotenv
    assert "WILLY_FORMCHK_BIN=" in dotenv
    assert "WILLY_GMX_BIN=" in dotenv
    assert "WILLY_MULTIWFN_BIN" not in dotenv
    assert stat.S_IMODE((tmp_path / ".env").stat().st_mode) == 0o600


def test_preflight_does_not_override_existing_willy_values_or_missing_tools(tmp_path, monkeypatch):
    _vendor_sobtop(tmp_path)
    available = {"g16", "formchk", "multiwfn", "gmx", "packmol"}
    monkeypatch.setattr("willy.dependency_preflight.resolve_tool", _resolver(available, tmp_path))
    monkeypatch.setenv("WILLY_G16_BIN", "/environment/owned/g16")
    (tmp_path / ".env").write_text("WILLY_GMX_BIN=/configured/gmx\nOTHER_SETTING=keep\n")

    report = run_dependency_preflight(tmp_path)

    assert report["ready"] is True
    assert report["written_defaults"] == ["WILLY_FORMCHK_BIN"]
    dotenv = (tmp_path / ".env").read_text()
    assert "WILLY_GMX_BIN=/configured/gmx" in dotenv
    assert "WILLY_G16_BIN=" not in dotenv
    assert "WILLY_FORMCHK_BIN=" in dotenv


def test_preflight_reports_missing_quantum_group_without_blocking_or_writing_missing_values(tmp_path, monkeypatch):
    _vendor_sobtop(tmp_path)
    available = {"multiwfn", "gmx", "packmol"}
    monkeypatch.setattr("willy.dependency_preflight.resolve_tool", _resolver(available, tmp_path))

    report = run_dependency_preflight(tmp_path)

    assert report["advisory"] is True
    assert report["ready"] is False
    assert report["groups"][0]["ready"] is False
    assert report["groups"][1]["ready"] is True
    assert report["groups"][2]["ready"] is True
    assert report["written_defaults"] == ["WILLY_GMX_BIN"]
    assert "当前依赖不满足完成完整 MD 流程的最小链路" in report["markdown"]
    assert "G16（不满足；来源：path）" in report["markdown"]
    assert "推荐安装：安装并配置下列任一量子链路" in report["markdown"]
    assert "本次已写入默认配置：WILLY_GMX_BIN。" in report["markdown"]
    assert os.path.exists(tmp_path / ".env")


def test_preflight_accepts_orca_and_opls_alternatives_when_sobtop_is_incomplete(tmp_path, monkeypatch):
    # Deliberately omit Sobtop files: a complete OPLS route must still satisfy
    # topology, just as ORCA must independently satisfy the quantum group.
    available = {
        "orca", "orca_2mkl", "multiwfn", "ligpargen", "boss", "obabel", "csh", "gmx", "packmol",
    }
    monkeypatch.setattr("willy.dependency_preflight.resolve_tool", _resolver(available, tmp_path))

    report = run_dependency_preflight(tmp_path)
    quantum, topology, simulation = report["groups"]

    assert report["ready"] is True
    assert quantum["alternatives"][2]["ready"] is True
    assert topology["alternatives"][0]["ready"] is False
    assert topology["alternatives"][1]["ready"] is True
    assert simulation["ready"] is True
    assert "WILLY_ORCA_HOME" in report["written_defaults"]
    assert "WILLY_BOSS_HOME" in report["written_defaults"]


def test_frontend_preflight_uses_frontend_root(tmp_path, monkeypatch):
    import willy.frontend_api as frontend_api

    sentinel = {"advisory": True, "ready": False}
    monkeypatch.setattr(frontend_api, "ROOT", tmp_path)
    monkeypatch.setattr(
        "willy.dependency_preflight.run_dependency_preflight",
        lambda root: sentinel if root == tmp_path else {},
    )

    assert frontend_api.run_local_dependency_preflight() is sentinel
