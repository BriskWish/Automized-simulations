"""Revalidate copied EQ evidence with managed GROMACS energy extraction."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import platform
import re
import shutil
import tempfile

from willy.simulation._gmx_utils import run_gmx
from willy.simulation.eq import EQResult, _acceptance_details
from willy.simulation.eq_acceptance import EQ_ACCEPTANCE_POLICY, EQWindow
from willy.simulation.manifest import (
    ManifestError, file_fingerprint, initialize_manifest, load_mdp_metadata,
    record_stage_result, require_prior_stage,
)
from willy.simulation.mdp import load_mdp_config
from willy.structured_log import STRUCTURED_LOG_FILENAME


ROOT = Path(__file__).resolve().parents[2]
SOURCE_FILES = ("config.json", "eq.mdp", "eq.edr", "eq.log", "eq.cpt", "eq.gro", "eq.tpr")
IMPLEMENTATION_FILES = (
    "src/willy/simulation/eq_acceptance.py", "src/willy/simulation/eq.py",
    "src/willy/simulation/_gmx_utils.py", "src/willy/simulation/manifest.py",
    "src/willy/simulation/protocol.py", "src/willy/simulation/gmx_process.py",
    "tests/test_eq_acceptance.py", "tests/test_simulation_execution.py",
    "tests/test_simulation_protocol.py", "tests/tools/g09_eq_coverage_acceptance.py",
)


def fingerprint(path: Path) -> dict:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"sha256": digest.hexdigest(), "size_bytes": path.stat().st_size}


def regression_summary(path: Path) -> dict:
    match = re.search(r"^(\d+) passed(?:, (\d+) skipped)? in ([\d.]+)s", path.read_text(), re.MULTILINE)
    if match is None:
        raise RuntimeError("A successful pytest summary is required")
    return {"passed": int(match.group(1)), "skipped": int(match.group(2) or 0),
            "duration_s": float(match.group(3)), "output": fingerprint(path)}


def inspect_source(source: Path, *, expected_accepted: bool) -> dict:
    metadata = load_mdp_metadata(source)["stages"]["eq"]
    before = {name: fingerprint(source / name) if (source / name).is_file() else None for name in SOURCE_FILES}
    manifest_name = "run_manifest.json" if (source / "run_manifest.json").is_file() else "md_manifest.json"
    before[manifest_name] = fingerprint(source / manifest_name)
    with tempfile.TemporaryDirectory(prefix="willy-g09-") as temporary:
        directory = Path(temporary)
        for name in SOURCE_FILES:
            if before[name] is not None:
                shutil.copy2(source / name, directory / name)
        configuration = load_mdp_config(str(directory / "config.json"))
        eq = configuration.values["eq"]
        window = EQWindow.from_mdp(directory / "eq.mdp", metadata, float(eq["target_temperature"]))
        version_result = run_gmx(["--version"], directory)
        version_match = re.search(r"GROMACS version:\s*([^\s]+)", version_result.stdout)
        if version_result.returncode != 0 or version_match is None:
            raise RuntimeError("GROMACS version evidence unavailable")
        details, issues = _acceptance_details(
            directory, float(metadata["segments"]["hold_target"]["actual_ns"]),
            eq["acceptance"], float(eq["target_temperature"]), window=window,
        )
        accepted = not issues
        initialize_manifest(directory, directory / "config.json", random_seed=configuration.run_seed,
                            versions={"gromacs": version_match.group(1)})
        outputs = {suffix: str(directory / f"eq.{suffix}") for suffix in ("tpr", "gro", "edr", "log", "cpt")}
        outputs.update({f"{name}_xvg": str(directory / filename) for name, filename in (
            ("temperature", "temp.xvg"), ("potential", "potential.xvg"),
        ) if (directory / filename).is_file()})
        record_stage_result(
            directory, "eq", success=accepted,
            contract={"inputs": {"mdp": file_fingerprint(directory / "eq.mdp", directory)}},
            outputs=outputs, details={"eq_result": asdict(EQResult(converged=accepted, details=details))},
        )
        try:
            require_prior_stage(directory, "prod")
            production_permission = True
        except ManifestError:
            production_permission = False
        public_series = {name: {key: stats[key] for key in (
            "ok", "sample_count", "mean", "relative_slope_per_ns", "coverage",
        ) if key in stats} for name, stats in details.get("series", {}).items()}
        generated = {filename: fingerprint(directory / filename) for filename in (
            "temp.xvg", "potential.xvg", "density.xvg", "pressure.xvg",
        ) if (directory / filename).is_file()}
        log_text = (directory / "eq.log").read_text(errors="replace")
        progress = list(re.finditer(r"(?:^|\n)\s*Step\s+Time\s*\n\s*(\d+)\s+([\d.]+)", log_text))
        observed = {"step": int(progress[-1].group(1)), "time_ps": float(progress[-1].group(2))} if progress else {}
        events = [json.loads(line) for line in (directory / STRUCTURED_LOG_FILENAME).read_text().splitlines()]
        starts = sum(event["event_code"] == "process_started" for event in events)
        finishes = sum(event["event_code"] == "process_finished" and event.get("outcome") == "succeeded" for event in events)
        managed = starts == finishes and starts >= (5 if expected_accepted else 1)
        result = {
            "expected_accepted": expected_accepted, "accepted": accepted,
            "production_permission": production_permission,
            "gromacs_version": version_match.group(1),
            "managed_auxiliary_execution": bool(managed),
            "managed_process_count": starts,
            "expected_end_ps": window.end_ps, "observed_end": observed,
            "acceptance_issues": issues, "series": public_series,
            "source_artifacts": before, "extracted_artifacts": generated,
        }
    after = {name: fingerprint(source / name) if (source / name).is_file() else None for name in before}
    result["originals_unchanged"] = before == after
    result["passed"] = accepted == expected_accepted and production_permission == expected_accepted and before == after and bool(managed)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--complete-run", type=Path, required=True)
    parser.add_argument("--early-run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--targeted-log", type=Path)
    parser.add_argument("--default-log", type=Path)
    arguments = parser.parse_args()
    report = {
        "schema_version": 1, "gap_id": "G-09",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "policy": EQ_ACCEPTANCE_POLICY, "python_version": platform.python_version(),
        "scope": "Current-code acceptance revalidation of isolated copies of real EQ artifacts; no mdrun, PROD or ten-step rerun; original runs and permissions are unchanged.",
        "implementation": {name: fingerprint(ROOT / name) for name in IMPLEMENTATION_FILES},
        "cases": {
            "complete_eq": inspect_source(arguments.complete_run.resolve(), expected_accepted=True),
            "early_eq": inspect_source(arguments.early_run.resolve(), expected_accepted=False),
        },
    }
    report["passed"] = all(case["passed"] for case in report["cases"].values())
    report["regressions"] = {name: regression_summary(path) for name, path in (
        ("eq_protocol_execution", arguments.targeted_log), ("pytest_default", arguments.default_log),
    ) if path is not None}
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"gap_id": "G-09", "passed": report["passed"]}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
