"""Retention-aware run cleanup tests."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from willy.pipeline_launch import reserve_pipeline_launch


def _pruner_module():
    path = Path(__file__).parents[1] / "scripts" / "prune_runs.py"
    spec = importlib.util.spec_from_file_location("prune_runs_test_module", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_prune_runs_requires_apply_and_updates_the_index(tmp_path):
    runs_dir = tmp_path / "md_run"
    run_ids = ["md__202608020001", "md__202608020002", "md__202608020003"]
    for run_id in run_ids:
        (runs_dir / run_id).mkdir(parents=True)
    (runs_dir / "index.json").write_text(json.dumps({
        "runs": [{"run_id": run_id} for run_id in run_ids],
    }))
    pruner = _pruner_module()

    candidates = pruner.prune_runs(tmp_path, keep=1, apply=False)
    assert [path.name for path in candidates] == run_ids[:2]
    assert all((runs_dir / run_id).is_dir() for run_id in run_ids)

    pruner.prune_runs(tmp_path, keep=1, apply=True)
    assert [path.name for path in pruner._run_dirs(runs_dir)] == run_ids[-1:]
    assert json.loads((runs_dir / "index.json").read_text())["runs"] == [
        {"run_id": run_ids[-1]},
    ]


def test_prune_runs_refuses_when_a_pipeline_lock_is_active(tmp_path):
    pruner = _pruner_module()
    reservation = reserve_pipeline_launch(tmp_path)
    try:
        with pytest.raises(RuntimeError, match="运行中的流水线"):
            pruner.prune_runs(tmp_path, keep=1, apply=True)
    finally:
        reservation.release()
