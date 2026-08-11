"""Local-only contracts for strict remote execution profile parsing."""

from __future__ import annotations

import json

import pytest

from willy.remote_registry import (
    RemoteProfileNotFound,
    RemoteProfileUnavailable,
    RemoteRegistryError,
    get_public_execution_snapshot,
    load_remote_registry,
    parse_execution_md,
    public_remote_capabilities,
    remote_profiles_path,
)


def _direct_profile() -> dict:
    return {
        "ssh_host_alias": "lab-gpu",
        "remote_run_root": "/scratch/willy",
        "transfer": {"method": "rsync", "partial": True},
        "gromacs": {
            "command": "/opt/gromacs/2025.0/bin/gmx",
            "setup_script": "/opt/gromacs/2025.0/bin/GMXRC",
            "gpu_policy": "auto",
        },
        "launcher": {"kind": "direct"},
    }


def _slurm_profile() -> dict:
    profile = _direct_profile()
    profile["launcher"] = {
        "kind": "slurm",
        "partition": "gpu",
        "account": "project_a",
        "qos": "normal",
        "gpus": 1,
        "cpus": 10,
        "memory_mb": 32000,
        "walltime": "24:00:00",
    }
    return profile


def _write_registry(tmp_path, payload: dict):
    directory = tmp_path / "remote-profiles"
    directory.mkdir(parents=True)
    directory.chmod(0o700)
    path = directory / "remote_profiles.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    path.chmod(0o600)
    return path


def _registry_payload(profile: dict | None = None) -> dict:
    return {
        "schema_version": 1,
        "profiles": {"lab_gpu": profile or _direct_profile()},
    }


def test_local_execution_never_requires_a_profile_file(tmp_path):
    missing = tmp_path / "missing" / "remote_profiles.json"

    execution = parse_execution_md(None, profile_path=missing)

    assert execution.backend == "local"
    assert execution.profile is None
    assert execution.retain_remote_run is True


def test_registry_uses_explicit_environment_override(tmp_path, monkeypatch):
    path = _write_registry(tmp_path, _registry_payload())
    monkeypatch.setenv("WILLY_REMOTE_PROFILES_FILE", str(path))

    assert remote_profiles_path() == path
    registry = load_remote_registry()
    assert registry.resolve("lab_gpu").launcher.kind == "direct"
    with pytest.raises(TypeError):
        registry.profiles["other"] = registry.resolve("lab_gpu")


def test_public_capabilities_are_redacted(tmp_path):
    path = _write_registry(tmp_path, _registry_payload())

    capabilities = public_remote_capabilities(path)
    serialized = json.dumps(capabilities, ensure_ascii=False)

    assert capabilities == {
        "available": True,
        "profiles": {
            "lab_gpu": {
                "profile": "lab_gpu",
                "launcher": "direct",
                "transfer": "rsync",
                "partial_transfer": True,
                "gpu_policy": "auto",
            }
        },
        "reason": "",
    }
    for secret in ("lab-gpu", "/scratch/willy", "/opt/gromacs", "GMXRC"):
        assert secret not in serialized


def test_public_execution_snapshot_marks_registered_profile_unprobed(tmp_path):
    path = _write_registry(tmp_path, _registry_payload())

    snapshot = get_public_execution_snapshot(path)

    assert snapshot == {
        "profiles": [{
            "profile_id": "lab_gpu",
            "mode": "ssh",
            "available": True,
            "connection_state": "unknown",
        }],
        "registry_configured": True,
    }
    serialized = json.dumps(snapshot, ensure_ascii=False)
    for secret in ("lab-gpu", "/scratch/willy", "/opt/gromacs", "GMXRC"):
        assert secret not in serialized


def test_missing_or_insecure_profile_file_is_not_available(tmp_path):
    missing = tmp_path / "missing" / "remote_profiles.json"
    assert public_remote_capabilities(missing)["available"] is False

    path = _write_registry(tmp_path, _registry_payload())
    path.chmod(0o644)
    with pytest.raises(RemoteProfileUnavailable, match="0600"):
        load_remote_registry(path)

    path.chmod(0o600)
    path.parent.chmod(0o755)
    with pytest.raises(RemoteProfileUnavailable, match="0700"):
        load_remote_registry(path)


def test_registry_rejects_duplicate_json_fields_and_symlinked_parent(tmp_path):
    directory = tmp_path / "duplicate"
    directory.mkdir()
    directory.chmod(0o700)
    duplicate = directory / "remote_profiles.json"
    duplicate.write_text('{"schema_version": 1, "schema_version": 1, "profiles": {}}')
    duplicate.chmod(0o600)
    with pytest.raises(RemoteRegistryError, match="重复字段"):
        load_remote_registry(duplicate)

    secure = _write_registry(tmp_path / "secure", _registry_payload())
    link_parent = tmp_path / "linked"
    link_parent.symlink_to(secure.parent, target_is_directory=True)
    with pytest.raises(RemoteProfileUnavailable, match="目录不能是符号链接"):
        load_remote_registry(link_parent / secure.name)


def test_profile_schema_rejects_secrets_shell_fields_and_non_strict_host_policy(tmp_path):
    profile = _direct_profile()
    profile["identity_file"] = "/home/user/.ssh/id_ed25519"
    path = _write_registry(tmp_path, _registry_payload(profile))

    with pytest.raises(RemoteRegistryError, match="不允许的字段"):
        load_remote_registry(path)

    profile = _direct_profile()
    profile["ssh"] = {"host_key_policy": "accept-new"}
    path = _write_registry(tmp_path / "policy", _registry_payload(profile))
    with pytest.raises(RemoteRegistryError, match="必须为 strict"):
        load_remote_registry(path)


def test_remote_execution_requires_registered_matching_launcher(tmp_path):
    path = _write_registry(tmp_path, _registry_payload())

    ssh = parse_execution_md(
        {"backend": "ssh", "profile": "lab_gpu", "retain_remote_run": False},
        profile_path=path,
    )
    assert ssh.backend == "ssh"
    assert ssh.profile == "lab_gpu"
    assert ssh.retain_remote_run is False

    with pytest.raises(RemoteProfileNotFound, match="未登记"):
        parse_execution_md(
            {"backend": "ssh", "profile": "missing", "retain_remote_run": True},
            profile_path=path,
        )
    with pytest.raises(RemoteRegistryError, match="不匹配"):
        parse_execution_md(
            {"backend": "slurm", "profile": "lab_gpu", "retain_remote_run": True},
            profile_path=path,
        )
    with pytest.raises(RemoteRegistryError, match="profile 必须为 null"):
        parse_execution_md(
            {"backend": "local", "profile": "lab_gpu", "retain_remote_run": True},
            profile_path=path,
        )


def test_slurm_profile_requires_structured_resources(tmp_path):
    profile = _slurm_profile()
    path = _write_registry(tmp_path, _registry_payload(profile))

    execution = parse_execution_md(
        {"backend": "slurm", "profile": "lab_gpu", "retain_remote_run": True},
        profile_path=path,
    )
    assert execution.backend == "slurm"

    profile = _slurm_profile()
    profile["launcher"]["gpus"] = "one"
    path = _write_registry(tmp_path / "bad-resource", _registry_payload(profile))
    with pytest.raises(RemoteRegistryError, match="launcher.gpus"):
        load_remote_registry(path)
