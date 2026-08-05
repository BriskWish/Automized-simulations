#!/usr/bin/env python3
"""Prune completed run workspaces without touching a live pipeline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys
import tempfile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from willy.pipeline_launch import pipeline_launch_is_active


def _run_dirs(runs_dir: Path) -> list[Path]:
    return sorted(
        (path for path in runs_dir.iterdir() if path.is_dir() and path.name.startswith("md__")),
        key=lambda path: path.name,
    )


def _write_index_without(runs_dir: Path, removed_ids: set[str]) -> None:
    """Remove stale summaries while preserving a valid registry index schema."""
    index_path = runs_dir / "index.json"
    if not index_path.is_file():
        return
    try:
        payload = json.loads(index_path.read_text(encoding="utf-8"))
        runs = payload.get("runs")
        if not isinstance(runs, list):
            return
    except (OSError, json.JSONDecodeError):
        return
    payload["runs"] = [entry for entry in runs if entry.get("run_id") not in removed_ids]
    descriptor, temporary = tempfile.mkstemp(
        prefix=".index.", suffix=".tmp", dir=runs_dir,
    )
    try:
        with open(descriptor, "w", encoding="utf-8", closefd=True) as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        Path(temporary).replace(index_path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def prune_runs(root: Path, keep: int, *, apply: bool) -> list[Path]:
    """Return candidates, deleting them only after an explicit opt-in."""
    if pipeline_launch_is_active(root):
        raise RuntimeError("检测到运行中的流水线，拒绝清理 md_run")
    runs_dir = root / "md_run"
    if not runs_dir.is_dir():
        return []
    candidates = _run_dirs(runs_dir)[:-keep]
    if apply:
        for directory in candidates:
            shutil.rmtree(directory)
        _write_index_without(runs_dir, {directory.name for directory in candidates})
    return candidates


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="按保留数量清理历史 Willy run")
    parser.add_argument("--keep", type=int, default=1, help="保留最新 run 数量（默认 1）")
    parser.add_argument("--apply", action="store_true", help="确认执行删除；省略时仅展示候选项")
    options = parser.parse_args(argv)
    if options.keep < 1:
        parser.error("--keep 必须至少为 1")
    try:
        candidates = prune_runs(PROJECT_ROOT, options.keep, apply=options.apply)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    action = "已删除" if options.apply else "待删除（使用 --apply 确认）"
    if not candidates:
        print("没有需要清理的历史 run")
        return 0
    for directory in candidates:
        print(f"{action}: {directory.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
