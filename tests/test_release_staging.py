"""Contracts for release staging without vendor or scientific execution."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts import build_release_staging


def test_release_staging_keeps_only_manifest_approved_vendor_files(tmp_path):
    source = Path(__file__).resolve().parents[1]
    destination = tmp_path / "release"

    # The isolated release-baseline copy deliberately has no .git directory.
    # Pass the minimal tracked source fixture explicitly so this test exercises
    # staging inclusion/exclusion rather than Git discovery.
    staged = build_release_staging.build_release_staging(
        source,
        destination,
        tracked_files=(Path("app.py"),),
        require_clean=False,
    )

    assert staged == destination
    assert (destination / "app.py").is_file()
    assert (destination / "vendor" / "packmol").is_file()
    assert (destination / "vendor" / "multiwfn").is_dir()
    assert not (destination / "vendor" / "sobtop").exists()
    assert not (destination / "vendor" / "obabel").exists()
    assert not (destination / "vendor" / "3Dmol-min.js").exists()


def test_release_staging_rejects_nonempty_destination(tmp_path):
    destination = tmp_path / "release"
    destination.mkdir()
    (destination / "leftover.txt").write_text("not safe to replace", encoding="utf-8")
    source = Path(__file__).resolve().parents[1]

    with pytest.raises(ValueError, match="must be empty"):
        build_release_staging.build_release_staging(source, destination, require_clean=False)


def test_release_staging_requires_a_clean_worktree(tmp_path, monkeypatch):
    source = Path(__file__).resolve().parents[1]
    monkeypatch.setattr(build_release_staging, "_worktree_is_clean", lambda _source: False)

    with pytest.raises(ValueError, match="must be clean"):
        build_release_staging.build_release_staging(source, tmp_path / "release")
