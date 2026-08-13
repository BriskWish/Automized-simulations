#!/usr/bin/env python3
"""Create a source-release staging tree with only approved vendor payloads.

The builder copies Git-tracked non-vendor files, then copies only vendor files
declared ``release_ready`` in ``vendor/manifest.json``.  It never invokes a
vendor executable or a scientific workflow.  The resulting directory is
immediately checked by the same release-artifact audit used in CI.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from willy.vendor_manifest import (  # noqa: E402
    audit_release_artifact,
    audit_vendor_manifest,
    release_artifact_ready,
    release_ready_vendor_files,
)


def _tracked_files(source: Path) -> tuple[Path, ...]:
    """Return only repository-tracked, source-relative regular file paths."""
    result = subprocess.run(
        ["git", "-C", str(source), "ls-files", "-z"],
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise ValueError("release staging requires a Git worktree")
    files: list[Path] = []
    for raw in result.stdout.split(b"\0"):
        if not raw:
            continue
        try:
            relative = Path(raw.decode("utf-8"))
        except UnicodeDecodeError as exc:
            raise ValueError("Git tracked path is not UTF-8") from exc
        if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
            raise ValueError("Git tracked path is unsafe")
        files.append(relative)
    return tuple(files)


def _worktree_is_clean(source: Path) -> bool:
    """Require the release source to exactly match its recorded Git revision."""
    result = subprocess.run(
        ["git", "-C", str(source), "status", "--porcelain=v1", "--untracked-files=all"],
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise ValueError("release staging requires a Git worktree")
    return not result.stdout


def _copy_file(source: Path, destination: Path, relative: Path) -> None:
    origin = (source / relative).resolve()
    try:
        origin.relative_to(source)
    except ValueError as exc:
        raise ValueError(f"release source path escapes root: {relative}") from exc
    if origin.is_symlink() or not origin.is_file():
        raise ValueError(f"release source file is unavailable: {relative}")
    target = destination / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(origin, target)


def build_release_staging(
    source: str | Path,
    destination: str | Path,
    *,
    tracked_files: Iterable[Path] | None = None,
    require_clean: bool = True,
) -> Path:
    """Build and audit an empty staging destination without running software."""
    source_root = Path(source).resolve()
    destination_root = Path(destination).resolve()
    if not source_root.is_dir():
        raise ValueError("release source directory is unavailable")
    if destination_root == source_root:
        raise ValueError("release staging destination cannot be the source directory")
    if destination_root.exists() and any(destination_root.iterdir()):
        raise ValueError("release staging destination must be empty")
    destination_root.mkdir(parents=True, exist_ok=True)
    if require_clean and not _worktree_is_clean(source_root):
        raise ValueError("release staging source worktree must be clean")

    audit = audit_vendor_manifest(source_root)
    if not audit.integrity_ok:
        raise ValueError("vendor manifest integrity must pass before release staging")
    approved_vendor_files = {Path(path) for path in release_ready_vendor_files(source_root)}
    files = tuple(tracked_files) if tracked_files is not None else _tracked_files(source_root)
    for relative in files:
        if relative.parts and relative.parts[0] == "vendor":
            continue
        _copy_file(source_root, destination_root, relative)
    for relative in approved_vendor_files:
        _copy_file(source_root / "vendor", destination_root / "vendor", relative)
    _copy_file(source_root / "vendor", destination_root / "vendor", Path("manifest.json"))

    artifact_issues = audit_release_artifact(destination_root, manifest_project_root=source_root)
    if not release_artifact_ready(audit, artifact_issues):
        raise ValueError("release staging did not satisfy the vendor artifact contract")
    return destination_root


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT, help="Git worktree to stage")
    parser.add_argument("--output", type=Path, required=True, help="empty staging directory to create")
    args = parser.parse_args()
    print(build_release_staging(args.source, args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
