from __future__ import annotations

import json

import pytest

from willy.managed_gateway_bundle import ManagedGatewayBundleError, build_managed_client_bundle


def test_client_bundle_includes_profile_and_excludes_local_runtime_state(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "README.md").write_text("client", encoding="utf-8")
    (source / ".env").write_text("WILLY_LLM_API_KEY=must-not-ship", encoding="utf-8")
    (source / ".venv").mkdir()
    (source / ".venv" / "pyvenv.cfg").write_text("local runtime", encoding="utf-8")
    (source / ".agents").mkdir()
    (source / ".agents" / "state.json").write_text("local state", encoding="utf-8")
    (source / "logs").mkdir()
    (source / "logs" / "gateway.log").write_text("runtime log", encoding="utf-8")
    (source / ".pipeline.pid").write_text("1234", encoding="utf-8")
    (source / "decision_trace.jsonl").write_text("runtime trace", encoding="utf-8")
    (source / "managed_gateway.json").write_text("local profile", encoding="utf-8")
    (source / "gateway.sqlite3").write_text("database", encoding="utf-8")
    profile = tmp_path / "release-profile.json"
    profile.write_text(json.dumps({
        "schema_version": 1,
        "profile_id": "release-gateway",
        "label": "Release gateway",
        "base_url": "https://gateway.example.test:8789",
        "model": "willy-default",
    }), encoding="utf-8")

    output, fingerprint = build_managed_client_bundle(source, profile, tmp_path / "client")

    assert output == (tmp_path / "client").resolve()
    assert len(fingerprint) == 64
    assert json.loads((output / "managed_gateway.json").read_text(encoding="utf-8"))["base_url"] == "https://gateway.example.test:8789"
    assert (output / "README.md").exists()
    assert not (output / ".env").exists()
    assert not (output / ".venv").exists()
    assert not (output / ".agents").exists()
    assert not (output / "logs").exists()
    assert not (output / ".pipeline.pid").exists()
    assert not (output / "decision_trace.jsonl").exists()
    assert not (output / "gateway.sqlite3").exists()


def test_client_bundle_rejects_loopback_or_plain_http_profile(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps({
        "schema_version": 1,
        "profile_id": "local-gateway",
        "label": "Local",
        "base_url": "http://127.0.0.1:8789",
        "model": "willy-default",
    }), encoding="utf-8")

    with pytest.raises(ManagedGatewayBundleError, match="非 loopback HTTPS"):
        build_managed_client_bundle(source, profile, tmp_path / "client")


def test_client_bundle_rejects_profile_inside_source_tree(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    profile = source / "release-profile.json"
    profile.write_text(json.dumps({
        "schema_version": 1,
        "profile_id": "release-gateway",
        "label": "Release gateway",
        "base_url": "https://gateway.example.test",
        "model": "willy-default",
    }), encoding="utf-8")

    with pytest.raises(ManagedGatewayBundleError, match="source_root 外"):
        build_managed_client_bundle(source, profile, tmp_path / "client")
