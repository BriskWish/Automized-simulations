"""Contracts for public release and acceptance batch reports."""

from __future__ import annotations

import json

import pytest

from tests.reporting.batch_report import (
    BatchReportError,
    artifact_record,
    build_batch_report,
    config_record,
    validate_batch_report,
    write_batch_report,
)


def _report(tmp_path):
    config = tmp_path / "config.json"
    config.write_text('{"run_seed": 7}', encoding="utf-8")
    artifact = tmp_path / "eq.gro"
    artifact.write_text("coordinates", encoding="utf-8")
    return build_batch_report(
        batch_id="baseline-20260811",
        project_version="0.1.0",
        config=config_record(config, root=tmp_path),
        tools={"python": "3.12", "gmx": "2025.1", "g16": "unavailable"},
        stages=[{
            "name": "eq",
            "phase": "equilibration",
            "status": "passed",
            "artifacts": [artifact_record(artifact, root=tmp_path)],
        }],
        acceptance={"conclusion": "passed", "checks": [{"name": "eq", "status": "passed"}]},
    )


def test_batch_report_contains_only_fingerprints_versions_and_public_acceptance(tmp_path):
    report = _report(tmp_path)

    assert report["config"]["sha256"]
    assert report["software"]["tools"]["gmx"] == "2025.1"
    assert report["stages"][0]["artifacts"] == [{
        "path": "eq.gro",
        "size_bytes": 11,
        "sha256": artifact_record(tmp_path / "eq.gro", root=tmp_path)["sha256"],
    }]
    assert report["acceptance"]["conclusion"] == "passed"


@pytest.mark.parametrize(
    "field,value",
    [
        ("raw_log", "do not publish"),
        ("api_key", "secret"),
        ("command", "gmx mdrun"),
        ("note", "Authorization: Bearer value"),
    ],
)
def test_batch_report_rejects_logs_commands_and_secrets(tmp_path, field, value):
    report = _report(tmp_path)
    report["stages"][0][field] = value

    with pytest.raises(BatchReportError):
        validate_batch_report(report)


def test_batch_report_writer_is_json_and_atomic(tmp_path):
    target = write_batch_report(tmp_path / "reports" / "batch.json", _report(tmp_path))

    saved = json.loads(target.read_text(encoding="utf-8"))
    assert saved["schema_version"] == 1
    assert not target.with_name(".batch.json.tmp").exists()


def test_artifact_must_be_relative_to_the_report_root(tmp_path):
    external = tmp_path.parent / "external.txt"
    external.write_text("private", encoding="utf-8")

    with pytest.raises(BatchReportError):
        artifact_record(external, root=tmp_path)
