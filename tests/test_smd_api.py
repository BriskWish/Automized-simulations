"""Solvent API registration and project-isolated catalog contracts."""

import pytest
from fastapi.testclient import TestClient

import app


@pytest.fixture
def solvent_client(tmp_path, monkeypatch):
    monkeypatch.setattr(app.frontend_api, "ROOT", tmp_path)
    monkeypatch.setattr(app.frontend_api, "reconcile_stale_pipeline_states", lambda **_kwargs: [])
    with TestClient(app.app) as client:
        yield client


def test_register_lookup_and_default_numbering(solvent_client):
    response = solvent_client.post("/api/solvents", json={"epsilon": 20, "epsinf": 1.8})
    assert response.status_code == 200
    record = response.json()["solvent"]
    assert record["name"] == "default_1" and record["manual"] is True
    response = solvent_client.get("/api/solvents", params={"query": "DEFAULT_1"})
    assert response.json()["match"] == record
    response = solvent_client.post("/api/solvents", json={"epsilon": 30, "epsinf": 2})
    assert response.json()["solvent"]["name"] == "default_2"
    catalog = solvent_client.get("/api/solvents").json()["candidates"]
    assert len(catalog) == 186


@pytest.mark.parametrize("payload", [
    {"name": "AcEtOnE", "epsilon": 20, "epsinf": 1.8},
    {"name": "bad", "epsilon": 1, "epsinf": 2},
    {"name": "bad", "epsilon": True, "epsinf": 1},
    {"name": "bad", "epsilon": 20},
    {"name": "bad", "epsilon": "NaN", "epsinf": 1},
])
def test_rejected_registration_does_not_change_catalog(solvent_client, payload):
    before = solvent_client.get("/api/solvents").json()
    response = solvent_client.post("/api/solvents", json=payload)
    assert response.status_code == 400
    assert solvent_client.get("/api/solvents").json() == before


def test_registered_name_collision_is_case_insensitive(solvent_client):
    assert solvent_client.post("/api/solvents", json={"name": "Mix", "epsilon": 20, "epsinf": 1.8}).status_code == 200
    response = solvent_client.post("/api/solvents", json={"name": "mIx", "epsilon": 21, "epsinf": 2})
    assert response.status_code == 400
    assert solvent_client.get("/api/solvents", params={"query": "Mix"}).json()["match"]["epsilon"] == "20"


def test_molecule_catalog_only_exposes_auditable_raw_inputs(solvent_client, tmp_path):
    struct_dir = tmp_path / "struct"
    struct_dir.mkdir()
    (struct_dir / "EC.gjf").write_text("#p\n\nEC\n\n0 1\n\n")
    (struct_dir / "EC.inp").write_text("! HF\n* xyz 0 1\n*\n")
    (struct_dir / "Li.inp").write_text("! HF\n* xyz 1 1\nLi 0 0 0\n*\n")
    (struct_dir / "EC_run.gjf").write_text("generated input")
    (struct_dir / "EC.mol2").write_text("generated artifact")

    response = solvent_client.get("/api/molecules")

    assert response.status_code == 200
    assert response.json() == {
        "molecules": [
            {"name": "EC", "input_suffixes": [".gjf", ".inp"]},
            {"name": "Li", "input_suffixes": [".inp"]},
        ],
    }
