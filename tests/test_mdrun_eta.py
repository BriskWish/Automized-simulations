"""Public ETA snapshots produced from GROMACS verbose mdrun output."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os

from willy.simulation.mdrun_eta import (
    MDRUN_ETA_FILENAME,
    begin_mdrun_eta,
    consume_mdrun_verbose_output,
    finish_mdrun_eta,
    heartbeat_mdrun_eta,
)


def test_verbose_carriage_return_output_updates_eta_snapshot(tmp_path):
    begin_mdrun_eta(tmp_path, "prod")
    carry = consume_mdrun_verbose_output(
        tmp_path,
        "prod",
        "\rstep 2840, remaining wall clock time: 12",
        observed_at=datetime(2026, 8, 2, 8, 0, tzinfo=timezone.utc),
    )
    carry = consume_mdrun_verbose_output(
        tmp_path,
        "prod",
        ".5 s\r",
        carry=carry,
        observed_at=datetime(2026, 8, 2, 8, 0, tzinfo=timezone.utc),
    )

    snapshot = json.loads((tmp_path / MDRUN_ETA_FILENAME).read_text())

    assert snapshot == {
        "schema_version": 2,
        "stage": "prod",
        "status": "available",
        "step": 2840,
        "remaining_seconds": 12.5,
        "observed_at": "2026-08-02T08:00:00+00:00",
        "eta_observed_at": "2026-08-02T08:00:00+00:00",
        "estimated_end_at": "2026-08-02T08:00:12.500000+00:00",
        "last_progress_at": "2026-08-02T08:00:00+00:00",
        "last_progress_step": 2840,
        "process_alive": True,
        "source": "gmx_verbose",
    }
    assert carry.endswith("12.5 s\r")


def test_eta_snapshot_stays_waiting_without_a_gromacs_prediction(tmp_path):
    begin_mdrun_eta(tmp_path, "eq")
    consume_mdrun_verbose_output(tmp_path, "eq", "\rstep 100\r")

    snapshot = json.loads((tmp_path / MDRUN_ETA_FILENAME).read_text())

    assert snapshot["status"] == "waiting"
    assert snapshot["stage"] == "eq"
    assert "estimated_end_at" not in snapshot


def test_heartbeat_refreshes_liveness_and_reads_gromacs_2025_log_progress(tmp_path):
    log = tmp_path / "eq.log"
    log.write_text(
        "           Step           Time\n"
        "        3177000     3177.00000\n",
    )
    updated_at = datetime(2026, 8, 2, 8, 1, tzinfo=timezone.utc)
    os.utime(log, (updated_at.timestamp(), updated_at.timestamp()))
    begin_mdrun_eta(tmp_path, "eq")

    snapshot = heartbeat_mdrun_eta(
        tmp_path,
        "eq",
        observed_at=datetime(2026, 8, 2, 8, 2, tzinfo=timezone.utc),
    )

    assert snapshot["status"] == "waiting"
    assert snapshot["observed_at"] == "2026-08-02T08:02:00+00:00"
    assert snapshot["last_progress_at"] == "2026-08-02T08:01:00+00:00"
    assert snapshot["last_progress_step"] == 3177000
    assert snapshot["process_alive"] is True
    assert snapshot["source"] == "stage_artifacts"
    assert "estimated_end_at" not in snapshot


def test_gromacs_duration_variant_updates_eta_without_deriving_from_step_rate(tmp_path):
    begin_mdrun_eta(tmp_path, "eq")

    consume_mdrun_verbose_output(
        tmp_path,
        "eq",
        "Step=3177000; remaining wallclock time = 00:01:02.5",
        observed_at=datetime(2026, 8, 2, 8, 0, tzinfo=timezone.utc),
    )

    snapshot = json.loads((tmp_path / MDRUN_ETA_FILENAME).read_text())
    assert snapshot["step"] == 3177000
    assert snapshot["remaining_seconds"] == 62.5
    assert snapshot["estimated_end_at"] == "2026-08-02T08:01:02.500000+00:00"


def test_gromacs_2025_will_finish_eta_is_parsed_from_chunked_verbose_output(tmp_path, monkeypatch):
    """GROMACS 2025 prints an absolute ctime ETA after PME tuning."""
    import willy.simulation.mdrun_eta as mdrun_eta

    observed_at = datetime(2026, 8, 3, 15, 0, tzinfo=timezone.utc)
    finish_at = datetime(2026, 8, 3, 17, 0, 54, tzinfo=timezone.utc)
    monkeypatch.setattr(mdrun_eta.time, "mktime", lambda _value: finish_at.timestamp())
    begin_mdrun_eta(tmp_path, "prod")

    carry = consume_mdrun_verbose_output(
        tmp_path,
        "prod",
        "step 8880: timed with pme grid 80 80 80, coulomb cutoff 1.000: 245.2 M-cycles\n"
        "              optimal pme grid 80 80 80, coulomb cutoff 1.000\n"
        "step 16800, will finish Tue Aug  ",
        observed_at=observed_at,
    )
    consume_mdrun_verbose_output(
        tmp_path,
        "prod",
        "4 01:00:54 2026\r",
        carry=carry,
        observed_at=observed_at,
    )

    snapshot = json.loads((tmp_path / MDRUN_ETA_FILENAME).read_text())
    assert snapshot["status"] == "available"
    assert snapshot["step"] == 16800
    assert snapshot["remaining_seconds"] == 7254.0
    assert snapshot["estimated_end_at"] == "2026-08-03T17:00:54+00:00"
    assert snapshot["source"] == "gmx_verbose"


def test_eta_snapshot_closes_when_mdrun_ends(tmp_path):
    begin_mdrun_eta(tmp_path, "em")
    finish_mdrun_eta(tmp_path, "em", success=True)

    snapshot = json.loads((tmp_path / MDRUN_ETA_FILENAME).read_text())

    assert snapshot["status"] == "finished"
    assert snapshot["stage"] == "em"
    assert snapshot["finished_at"] == snapshot["observed_at"]
    assert snapshot["process_alive"] is False
