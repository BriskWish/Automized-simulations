import json

from willy.run_provenance import RUN_PROVENANCE_FILENAME, create_or_refresh_provenance
from willy.run_metadata import create_run_manifest, load_run_manifest


def test_provenance_is_run_local_redacted_and_tracks_config_revisions(tmp_path):
    run_dir = tmp_path / "md_run" / "md_test"
    run_dir.mkdir(parents=True)
    config = run_dir / "config.json"
    config.write_text('{"md":{"run_seed":7}}', encoding="utf-8")
    (run_dir / "EC.gjf").write_text("# test", encoding="utf-8")
    capabilities = {"gmx": {"tool_id": "gmx", "status": "available", "source": "path", "version": "2025"}}

    first = create_or_refresh_provenance(
        run_dir, project_root=tmp_path, backend="g16", config_path=config,
        random_seed=7, capabilities=capabilities, llm_model="model-a",
        prompt_versions={"quantum": "quantum_agent_v1"},
    )
    config.write_text('{"md":{"run_seed":8}}', encoding="utf-8")
    second = create_or_refresh_provenance(
        run_dir, project_root=tmp_path, backend="g16", config_path=config,
        random_seed=8, capabilities=capabilities, llm_model="model-a",
        prompt_versions={"quantum": "quantum_agent_v1"},
    )

    stored = json.loads((run_dir / RUN_PROVENANCE_FILENAME).read_text(encoding="utf-8"))
    assert first["run_id"] == "md_test"
    assert second["random_seed"] == 8
    assert len(stored["config_revisions"]) == 2
    assert stored["input_fingerprints"][0]["path"] == "EC.gjf"
    assert "api_key" not in json.dumps(stored).lower()


def test_provenance_rejects_config_outside_current_run(tmp_path):
    run_dir = tmp_path / "md_run" / "md_test"
    run_dir.mkdir(parents=True)
    external_config = tmp_path / "config.json"
    external_config.write_text("{}", encoding="utf-8")
    try:
        create_or_refresh_provenance(
            run_dir, project_root=tmp_path, backend="g16", config_path=external_config,
            random_seed=1, capabilities={}, llm_model=None, prompt_versions={},
        )
    except ValueError as exc:
        assert "当前 run" in str(exc)
    else:
        raise AssertionError("外部 config 不应写入 provenance")


def test_provenance_uses_unified_private_section_when_enabled(tmp_path):
    run_dir = tmp_path / "md_run" / "md_unified"
    run_dir.mkdir(parents=True)
    config = run_dir / "config.json"
    config.write_text('{"md":{"run_seed":7}}', encoding="utf-8")
    create_run_manifest(run_dir)

    payload = create_or_refresh_provenance(
        run_dir, project_root=tmp_path, backend="g16", config_path=config,
        random_seed=7, capabilities={"gmx": {"status": "available"}},
        llm_model="model-a", prompt_versions={"simulation": "v2"},
    )

    unified = load_run_manifest(run_dir)
    stored = unified["sections"]["provenance"]["data"]
    assert payload["run_id"] == "md_unified"
    assert stored["random_seed"] == 7
    assert not (run_dir / RUN_PROVENANCE_FILENAME).exists()
