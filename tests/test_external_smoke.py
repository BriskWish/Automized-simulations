"""External smoke registry tests and opt-in preflight gate."""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest

from willy.external_smoke import (
    ExternalSmokeCase,
    SMOKE_EVIDENCE_SCHEMA_VERSION,
    SMOKE_CASES,
    get_smoke_case,
    preflight,
    selected_smoke_cases,
    smoke_required,
    verify_evidence,
    write_smoke_evidence,
)


def _write_fixture_manifest(root: Path, case: ExternalSmokeCase, *, bundle_id: str = "test-bundle") -> None:
    files = {}
    for relative in case.fixture_files:
        path = root / relative
        if path.is_file():
            files[relative] = {
                "sha256": sha256(path.read_bytes()).hexdigest(),
                "size_bytes": path.stat().st_size,
            }
    (root / "fixture-manifest.json").write_text(json.dumps({
        "schema_version": 1,
        "bundle_id": bundle_id,
        "cases": {case.case_id: {"files": files}},
    }), encoding="utf-8")


def test_smoke_registry_has_unique_case_ids_and_declared_timeout():
    assert len({case.case_id for case in SMOKE_CASES}) == len(SMOKE_CASES)
    assert all(case.tools or case.fixture_files for case in SMOKE_CASES)
    assert all(case.timeout_s > 0 for case in SMOKE_CASES)


def test_g01_minimal_fixture_initializer_matches_the_declared_bundle(tmp_path):
    from tests.tools.g01_gromacs_minimal_acceptance import initialize_fixture_bundle

    case = get_smoke_case("gromacs_minimal")
    bundle = initialize_fixture_bundle(tmp_path / "g01-fixture")
    manifest = json.loads((bundle / "fixture-manifest.json").read_text(encoding="utf-8"))

    assert set(manifest["cases"]["gromacs_minimal"]["files"]) == set(case.fixture_files)
    assert all((bundle / relative).is_file() for relative in case.fixture_files)
    assert initialize_fixture_bundle(bundle) == bundle


def test_smoke_preflight_is_read_only_and_reports_missing_fixture(tmp_path):
    case = ExternalSmokeCase("fixture_only", "fixture", (), ("input.dat",))
    result = preflight(case, root=tmp_path)
    assert not result.ready
    assert result.missing_fixtures == ("input.dat",)
    assert "fixture_manifest_missing" in result.fixture_issues


def test_smoke_preflight_accepts_only_hash_verified_declared_fixture(tmp_path):
    case = ExternalSmokeCase("fixture_only", "fixture", (), ("input.dat",))
    (tmp_path / "input.dat").write_text("input", encoding="utf-8")
    _write_fixture_manifest(tmp_path, case)

    result = preflight(case, root=tmp_path)

    assert result.ready
    assert result.fixture_integrity.bundle_id == "test-bundle"
    assert result.fixture_integrity.files[0]["sha256"] == sha256(b"input").hexdigest()


def test_smoke_preflight_rejects_tampered_fixture_bundle(tmp_path):
    case = ExternalSmokeCase("fixture_only", "fixture", (), ("input.dat",))
    input_path = tmp_path / "input.dat"
    input_path.write_text("input", encoding="utf-8")
    _write_fixture_manifest(tmp_path, case)
    input_path.write_text("tampered", encoding="utf-8")

    result = preflight(case, root=tmp_path)

    assert not result.ready
    assert "fixture_hash_mismatch:input.dat" in result.fixture_issues


def test_smoke_evidence_is_opt_in_and_redacted(tmp_path, monkeypatch):
    case = ExternalSmokeCase("fixture_only", "fixture", (), ("input.dat",))
    (tmp_path / "input.dat").write_text("input", encoding="utf-8")
    _write_fixture_manifest(tmp_path, case)
    preflight_result = preflight(case, root=tmp_path)
    monkeypatch.setenv("WILLY_EXTERNAL_SMOKE_EVIDENCE_DIR", str(tmp_path / "evidence"))
    output = tmp_path / "result.txt"
    output.write_text("ok", encoding="utf-8")
    evidence = write_smoke_evidence(
        case, preflight_result, success=True, outputs=(output,), root=tmp_path,
        phase="execution", command_label="fixture-only-test",
    )
    assert evidence is not None
    payload = json.loads(evidence.read_text(encoding="utf-8"))
    assert payload["schema_version"] == SMOKE_EVIDENCE_SCHEMA_VERSION
    assert payload["execution_attempted"] is True
    assert payload["outcome"] == "passed"
    assert payload["success"] is True
    assert payload["outputs"] == [{
        "path": "result.txt", "size_bytes": 2,
        "sha256": sha256(b"ok").hexdigest(),
    }]
    assert verify_evidence(tmp_path / "evidence", (case,), phase="execution", require_execution=True) == ()


def test_evidence_verifier_rejects_preflight_record_as_execution(tmp_path, monkeypatch):
    case = ExternalSmokeCase("fixture_only", "fixture", (), ())
    _write_fixture_manifest(tmp_path, case)
    result = preflight(case, root=tmp_path)
    monkeypatch.setenv("WILLY_EXTERNAL_SMOKE_EVIDENCE_DIR", str(tmp_path / "evidence"))
    write_smoke_evidence(
        case, result, success=True, root=tmp_path,
        phase="preflight", execution_attempted=False,
    )

    assert verify_evidence(
        tmp_path / "evidence", (case,), phase="preflight", require_execution=True,
    ) == ("evidence_execution_missing:fixture_only:preflight",)


def test_evidence_verifier_requires_declared_outputs_for_successful_execution(tmp_path, monkeypatch):
    case = get_smoke_case("sobtop_ec")
    result = preflight(case, root=tmp_path)
    monkeypatch.setenv("WILLY_EXTERNAL_SMOKE_EVIDENCE_DIR", str(tmp_path / "evidence"))
    write_smoke_evidence(
        case, result, success=True, root=tmp_path,
        phase="execution", execution_attempted=True,
    )

    assert verify_evidence(
        tmp_path / "evidence", (case,), phase="execution", require_execution=True,
    ) == ("evidence_required_outputs_missing:sobtop_ec:execution",)


def test_evidence_verifier_rejects_path_traversal_in_tampered_artifact(tmp_path, monkeypatch):
    case = ExternalSmokeCase("fixture_only", "fixture", (), ())
    _write_fixture_manifest(tmp_path, case)
    result = preflight(case, root=tmp_path)
    monkeypatch.setenv("WILLY_EXTERNAL_SMOKE_EVIDENCE_DIR", str(tmp_path / "evidence"))
    evidence = write_smoke_evidence(
        case, result, success=True, root=tmp_path,
        phase="execution", execution_attempted=True,
    )
    assert evidence is not None
    payload = json.loads(evidence.read_text(encoding="utf-8"))
    payload["outputs"] = [{
        "path": "../secret", "size_bytes": 1,
        "sha256": sha256(b"x").hexdigest(),
    }]
    evidence.write_text(json.dumps(payload), encoding="utf-8")

    assert verify_evidence(
        tmp_path / "evidence", (case,), phase="execution", require_execution=True,
    ) == ("evidence_artifact_invalid:fixture_only:execution",)


def test_selected_smoke_cases_rejects_duplicate_ids():
    case_id = SMOKE_CASES[0].case_id
    with pytest.raises(ValueError, match="重复"):
        selected_smoke_cases(f"{case_id},{case_id}")


@pytest.mark.external
@pytest.mark.parametrize("case", selected_smoke_cases(), ids=lambda case: case.case_id)
def test_external_smoke_preflight_gate(case):
    """An acceptance machine can make missing tools/fixtures a hard failure."""
    result = preflight(case)
    write_smoke_evidence(
        case,
        result,
        success=result.ready,
        phase="preflight",
        execution_attempted=False,
        command_label="external-preflight",
        failure_summary=(
            f"tools={','.join(result.missing_tools) or '-'}; "
            f"fixtures={','.join(result.missing_fixtures) or '-'}; "
            f"fixture_issues={','.join(result.fixture_issues) or '-'}"
        ) if not result.ready else "",
    )
    if not result.ready:
        message = (
            f"{case.case_id} 未就绪: tools={','.join(result.missing_tools) or '-'}; "
            f"fixtures={','.join(result.missing_fixtures) or '-'}; "
            f"fixture_issues={','.join(result.fixture_issues) or '-'}"
        )
        if smoke_required():
            pytest.fail(message)
        pytest.skip(message)
    assert result.ready


@pytest.mark.external
def test_g07_local_wsl_error_matrix_is_redacted():
    """复测真实 Packmol 启动与受控错误注入的公开/私有边界。"""
    from tests.tools.g07_local_wsl_acceptance import run_acceptance

    payload = run_acceptance(Path.cwd())

    assert payload["acceptance"] in {"passed", "conditional"}
    assert payload["environment"]["real_packmol"]["started"] is True
    assert payload["environment"]["abi_injection"]["runtime_unavailable"] is True
    assert payload["environment"]["dependency_injection"]["missing"] is True
    assert len(payload["scenarios"]) == 6
    assert all(
        item["public_projection_redacted"]
        and item["private_records_redacted"]
        and item["private_decision_contract"]
        for item in payload["scenarios"]
    )
    assert payload["llm_configuration_error"]["safe"] is True
    assert payload["llm_configuration_error"]["run_artifacts_created"] is False
