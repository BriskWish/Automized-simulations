"""Explicit trusted baselines for synthetic run fixtures."""

import json

from willy.config_store import write_json
from willy.simulation.protocol import canonical_json_fingerprint
from willy.step_contracts import CONTRACT_FILENAME, fingerprint, initialize_contracts


def seed_contracts(directory):
    payload = initialize_contracts(directory)
    config = json.loads((directory / "config.json").read_text())
    payload["config_signature"] = canonical_json_fingerprint(config)
    for path in directory.rglob("*"):
        if not path.is_file() or path.stat().st_size == 0 or "old" in path.relative_to(directory).parts:
            continue
        if path.name != "config.json" and path.suffix not in {
            ".itp", ".top", ".gro", ".pdb", ".mdp", ".tpr", ".xtc", ".edr", ".cpt", ".trr",
            ".gjf", ".inp", ".mol2", ".fchk", ".molden", ".chg", ".log",
        }:
            continue
        producer = 0
        if path.suffix in {".fchk", ".molden"}:
            producer = 2 if "_opt" in path.stem else 1
        elif path.suffix == ".mol2":
            producer = 2
        elif path.suffix == ".chg":
            producer = 3
        elif path.suffix in {".itp", ".gro"}:
            producer = 5 if ".assembly_itp" in path.parts else 4
        elif path.suffix == ".top":
            producer = 5
        elif path.suffix == ".mdp":
            producer = 6
        elif path.name == "model.pdb":
            producer = 7
        if path.stem in {"em", "eq", "prod"} and path.suffix != ".mdp":
            producer = {"em": 8, "eq": 9, "prod": 10}[path.stem]
        record = fingerprint(directory, path)
        payload["artifacts"][record["path"]] = {**record, "producer_step": producer, "source": "fixture", "accepted": True}
    write_json(directory / CONTRACT_FILENAME, payload)
