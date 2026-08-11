"""Deterministic remote-execution core tests using only an injected fake transport."""

from __future__ import annotations

from dataclasses import replace

import pytest

from willy.remote_execution import (
    DirectSshLifecycle,
    RemoteErrorCode,
    RemoteExecutionCore,
    RemoteExecutionError,
    RemoteHostKeyMismatchError,
    RemoteLifecyclePhase,
    RemotePreflightObservation,
    RemotePreflightRequest,
    RemoteProcessHandle,
    RemoteProcessState,
    RemoteProcessStatus,
    RemoteSignal,
    RemoteStartRequest,
    StageSyncReceipt,
    StageSyncRequest,
    SyncDirection,
    build_gromacs_argv,
    create_stage_sync_manifest,
    preflight_remote_execution_for_run,
    remote_execution_for_run,
)


class _FakeTransport:
    def __init__(self, observation=None):
        self.observation = observation or RemotePreflightObservation(
            connected=True,
            writable=True,
            disk_free_bytes=100_000,
            gromacs_available=True,
            gromacs_version="2025.0",
            gpu_available=True,
            transfer_available=True,
        )
        self.preflight_error = None
        self.receipt_mutator = None
        self.download_writer = None
        self.started = []
        self.signals = []
        self.statuses = {}

    def preflight(self, request):
        if self.preflight_error is not None:
            raise self.preflight_error
        return self.observation

    def sync_stage(self, request):
        if self.download_writer is not None:
            self.download_writer(request)
        receipt = StageSyncReceipt(
            request.manifest.fingerprint,
            request.manifest.files,
        )
        return self.receipt_mutator(receipt) if self.receipt_mutator else receipt

    def start_direct(self, request):
        self.started.append(request)
        handle = RemoteProcessHandle(
            request.profile_id,
            request.run_id,
            request.stage,
            request.attempt_id,
            token=f"opaque-{len(self.started)}",
        )
        self.statuses[handle.token] = RemoteProcessStatus(RemoteProcessState.RUNNING)
        return handle

    def query_direct(self, handle):
        return self.statuses[handle.token]

    def signal_direct(self, handle, signal):
        self.signals.append((handle.token, signal))


def test_preflight_normalizes_private_transport_failure_and_never_exposes_details():
    transport = _FakeTransport()
    transport.preflight_error = RemoteHostKeyMismatchError("host lab-gpu changed")

    result = RemoteExecutionCore(transport).preflight(RemotePreflightRequest("lab_gpu", "direct"))

    assert result.ready is False
    assert result.errors == (RemoteErrorCode.HOST_KEY_MISMATCH,)
    public = result.public_dict()
    assert public["errors"] == ["remote_host_key_mismatch"]
    assert "lab-gpu" not in str(public)


def test_preflight_requires_declared_resources_without_transport_details():
    transport = _FakeTransport(RemotePreflightObservation(
        connected=True,
        writable=True,
        disk_free_bytes=50,
        gromacs_available=False,
        gromacs_version="/private/lab-gpu/GROMACS 2025.0",
        gpu_available=False,
        transfer_available=False,
    ))
    request = RemotePreflightRequest(
        "lab_gpu", "direct", required_disk_bytes=100, require_gpu=True,
    )

    result = RemoteExecutionCore(transport).preflight(request)

    assert result.ready is False
    assert result.errors == (
        RemoteErrorCode.DISK_INSUFFICIENT,
        RemoteErrorCode.GROMACS_UNAVAILABLE,
        RemoteErrorCode.SYNC_FAILED,
    )
    assert result.public_dict()["gromacs_version"] == ""


def test_gromacs_command_is_argv_only_and_rejects_control_characters():
    command = build_gromacs_argv(
        "/private/gromacs/bin/gmx",
        "mdrun",
        ["-s", "eq.tpr; unexpected text", "-deffnm", "eq"],
    )

    assert command.argv == (
        "/private/gromacs/bin/gmx", "mdrun", "-s", "eq.tpr; unexpected text", "-deffnm", "eq",
    )
    assert command.public_dict() == {"operation": "mdrun"}
    with pytest.raises(RemoteExecutionError) as exc_info:
        build_gromacs_argv("gmx", "mdrun", ["-s\nother-command"])
    assert exc_info.value.code is RemoteErrorCode.INVALID_REQUEST


def test_sync_manifest_rejects_escape_and_verifies_upload_and_download_hashes(tmp_path):
    run_dir = tmp_path / "md__1"
    run_dir.mkdir()
    (run_dir / "eq.mdp").write_text("nsteps = 500\n")
    (run_dir / "topol.top").write_text("[ system ]\n")
    upload = create_stage_sync_manifest(
        run_dir,
        run_id="md__1",
        stage="eq",
        attempt_id="eq-1",
        direction=SyncDirection.UPLOAD,
        relative_paths=["eq.mdp", "topol.top"],
    )
    core = RemoteExecutionCore(_FakeTransport())

    uploaded = core.synchronize(StageSyncRequest("lab_gpu", run_dir, upload))

    assert uploaded.verified is True
    with pytest.raises(RemoteExecutionError) as exc_info:
        create_stage_sync_manifest(
            run_dir,
            run_id="md__1",
            stage="eq",
            attempt_id="eq-1",
            direction=SyncDirection.UPLOAD,
            relative_paths=["../outside"],
        )
    assert exc_info.value.code is RemoteErrorCode.INVALID_REQUEST
    (run_dir / "linked.mdp").symlink_to(run_dir / "eq.mdp")
    with pytest.raises(RemoteExecutionError) as exc_info:
        create_stage_sync_manifest(
            run_dir,
            run_id="md__1",
            stage="eq",
            attempt_id="eq-1",
            direction=SyncDirection.UPLOAD,
            relative_paths=["linked.mdp"],
        )
    assert exc_info.value.code is RemoteErrorCode.SYNC_FAILED

    output = run_dir / "eq.gro"
    output.write_text("expected remote output\n")
    download = create_stage_sync_manifest(
        run_dir,
        run_id="md__1",
        stage="eq",
        attempt_id="eq-1",
        direction=SyncDirection.DOWNLOAD,
        relative_paths=["eq.gro"],
    )
    bad_transport = _FakeTransport()
    bad_transport.download_writer = lambda request: (request.local_run_dir / "eq.gro").write_text("corrupted\n")

    downloaded = RemoteExecutionCore(bad_transport).synchronize(
        StageSyncRequest("lab_gpu", run_dir, download)
    )

    assert downloaded.verified is False
    assert downloaded.error is RemoteErrorCode.HASH_MISMATCH


def test_sync_receipt_hash_mismatch_is_rejected_before_stage_can_continue(tmp_path):
    run_dir = tmp_path / "md__2"
    run_dir.mkdir()
    (run_dir / "em.gro").write_text("coordinates\n")
    manifest = create_stage_sync_manifest(
        run_dir,
        run_id="md__2",
        stage="eq",
        attempt_id="eq-1",
        direction=SyncDirection.UPLOAD,
        relative_paths=["em.gro"],
    )
    transport = _FakeTransport()
    transport.receipt_mutator = lambda receipt: StageSyncReceipt(
        receipt.manifest_fingerprint,
        (replace(receipt.files[0], sha256="0" * 64),),
    )

    result = RemoteExecutionCore(transport).synchronize(StageSyncRequest("lab_gpu", run_dir, manifest))

    assert result.verified is False
    assert result.error is RemoteErrorCode.HASH_MISMATCH


def test_direct_ssh_lifecycle_preserves_start_query_checkpoint_first_stop_and_escalation():
    transport = _FakeTransport()
    lifecycle = DirectSshLifecycle(transport, interrupt_grace_s=3, terminate_grace_s=2)
    command = build_gromacs_argv("gmx", "mdrun", ["-s", "eq.tpr", "-deffnm", "eq"])
    request = RemoteStartRequest("lab_gpu", "md__3", "eq", "eq-1", command)

    started = lifecycle.start(request)
    handle = RemoteProcessHandle("lab_gpu", "md__3", "eq", "eq-1", "opaque-1")
    stopping = lifecycle.request_stop(handle, now=0)
    terminating = lifecycle.advance_stop(handle, now=3)
    killed = lifecycle.advance_stop(handle, now=5)

    assert started.phase is RemoteLifecyclePhase.RUNNING
    assert stopping.phase is RemoteLifecyclePhase.INTERRUPT
    assert terminating.phase is RemoteLifecyclePhase.TERMINATE
    assert killed.phase is RemoteLifecyclePhase.KILLED
    assert [signal for _, signal in transport.signals] == [
        RemoteSignal.INTERRUPT, RemoteSignal.TERMINATE, RemoteSignal.KILL,
    ]
    transport.statuses[handle.token] = RemoteProcessStatus(
        RemoteProcessState.FINISHED, returncode=130, checkpoint_available=True,
    )
    finished = lifecycle.query(handle)
    assert finished.phase is RemoteLifecyclePhase.FINISHED
    assert finished.checkpoint_available is True
    assert "opaque-1" not in str(finished.public_dict())


def test_run_binding_preserves_local_behavior_and_preflights_remote_only():
    transport = _FakeTransport()
    local = {"execution": {"md": {"backend": "local", "profile": None}}}
    remote = {
        "execution": {
            "md": {"backend": "ssh", "profile": "lab_gpu", "retain_remote_run": False}
        }
    }

    assert remote_execution_for_run(local, transport) is None
    assert preflight_remote_execution_for_run(local, transport) is None
    binding = remote_execution_for_run(remote, transport)
    assert binding is not None
    assert binding.retain_remote_run is False
    assert binding.preflight(required_disk_bytes=10).ready is True
