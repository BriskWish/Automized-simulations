"""G-01b real managed GROMACS acceptance harness.

It creates a tiny, non-scientific three-atom fixture bundle, verifies its
manifest, then executes EM -> EQ -> PROD through Willy's grompp_and_mdrun
API. The harness verifies integration boundaries, not physical properties.
"""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Mapping

from willy.external_smoke import (
    SMOKE_EVIDENCE_ENV,
    get_smoke_case,
    preflight,
    verify_evidence,
    write_smoke_evidence,
)
from willy.simulation._gmx_utils import grompp_and_mdrun


_CASE_ID = "gromacs_minimal"
_FIXTURE_FILES = {
    "gromacs_minimal/topol.top": """[ defaults ]
1 2 yes 0.5 0.5

#include "g01_minimal.itp"

[ system ]
Willy G-01 minimal argon trimer

[ molecules ]
AR3 1
""",
    "gromacs_minimal/g01_minimal.itp": """[ atomtypes ]
; name  mass     charge ptype sigma  epsilon
AR       39.948   0.000  A     0.340  0.238

[ moleculetype ]
; name  nrexcl
AR3     0

[ atoms ]
; nr  type  resnr residue atom cgnr charge  mass
1     AR    1     AR      AR1  1    0.000   39.948
2     AR    1     AR      AR2  2    0.000   39.948
3     AR    1     AR      AR3  3    0.000   39.948
""",
    "gromacs_minimal/model.pdb": """TITLE     Willy G-01 minimal argon trimer
CRYST1   10.000   10.000   10.000  90.00  90.00  90.00 P 1           1
ATOM      1  AR1 AR3 A   1       2.000   2.000   2.000  1.00  0.00           Ar
ATOM      2  AR2 AR3 A   1       5.000   5.000   5.000  1.00  0.00           Ar
ATOM      3  AR3 AR3 A   1       8.000   2.000   5.000  1.00  0.00           Ar
TER
END
""",
    "gromacs_minimal/em.mdp": """integrator               = steep
nsteps                   = 10
emtol                    = 1000
emstep                   = 0.01
cutoff-scheme            = Verlet
nstlist                  = 1
rlist                    = 0.4
coulombtype              = Cut-off
rcoulomb                 = 0.4
vdwtype                  = Cut-off
rvdw                     = 0.4
pbc                      = xyz
nstxout-compressed       = 1
nstenergy                = 1
nstlog                   = 1
""",
    "gromacs_minimal/eq.mdp": """integrator               = md
nsteps                   = 10
dt                       = 0.002
cutoff-scheme            = Verlet
nstlist                  = 1
rlist                    = 0.4
coulombtype              = Cut-off
rcoulomb                 = 0.4
vdwtype                  = Cut-off
rvdw                     = 0.4
pbc                      = xyz
tcoupl                   = no
pcoupl                   = no
gen_vel                  = yes
gen_temp                 = 300
gen_seed                 = 20260815
nstxout-compressed       = 1
nstenergy                = 1
nstlog                   = 1
""",
    "gromacs_minimal/prod.mdp": """integrator               = md
nsteps                   = 10
dt                       = 0.002
cutoff-scheme            = Verlet
nstlist                  = 1
rlist                    = 0.4
coulombtype              = Cut-off
rcoulomb                 = 0.4
vdwtype                  = Cut-off
rvdw                     = 0.4
pbc                      = xyz
tcoupl                   = no
pcoupl                   = no
gen_vel                  = no
nstxout-compressed       = 1
nstenergy                = 1
nstlog                   = 1
""",
}


def _sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def initialize_fixture_bundle(root: str | Path) -> Path:
    """Create the declared fixture only when existing files are identical."""
    bundle = Path(root).resolve()
    for relative, content in _FIXTURE_FILES.items():
        destination = bundle / relative
        expected = content.encode("ascii")
        if destination.exists() and destination.read_bytes() != expected:
            raise ValueError(f"fixture 已存在且内容不匹配: {relative}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            destination.write_bytes(expected)

    files = {
        relative: {
            "sha256": _sha256(bundle / relative),
            "size_bytes": (bundle / relative).stat().st_size,
        }
        for relative in _FIXTURE_FILES
    }
    manifest = {
        "schema_version": 1,
        "bundle_id": "willy-g01-minimal-argon-v1",
        "cases": {_CASE_ID: {"files": files}},
    }
    manifest_path = bundle / "fixture-manifest.json"
    expected_manifest = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if manifest_path.exists() and manifest_path.read_text(encoding="utf-8") != expected_manifest:
        raise ValueError("fixture-manifest.json 已存在且内容不匹配")
    if not manifest_path.exists():
        manifest_path.write_text(expected_manifest, encoding="utf-8")
    return bundle


def _copy_fixture_to_run(bundle: Path, run_dir: Path) -> None:
    for source in (bundle / _CASE_ID).iterdir():
        if source.is_file():
            shutil.copyfile(source, run_dir / source.name)
    (run_dir / "config.json").write_text(
        json.dumps({"defaults": {"nproc": 1}}, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _artifact_record(path: Path, root: Path) -> dict[str, object] | None:
    if not path.is_file() or path.stat().st_size <= 0:
        return None
    try:
        relative = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return None
    return {
        "path": relative,
        "size_bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _event_codes(run_dir: Path) -> list[str]:
    path = run_dir / "logs" / "structured.jsonl"
    if not path.is_file():
        return []
    codes: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        code = payload.get("event_code") if isinstance(payload, Mapping) else None
        if isinstance(code, str):
            codes.append(code)
    return codes


def _run_stage(run_dir: Path, stage: str, timeout_s: int) -> tuple[bool, dict[str, object], list[Path]]:
    coordinate = {"em": "model.pdb", "eq": "em.gro", "prod": "eq.gro"}[stage]
    outcome = grompp_and_mdrun(
        stage,
        run_dir,
        conf=coordinate,
        nproc=1,
        mdrun_timeout_s=timeout_s,
    )
    artifacts = [Path(item) for item in outcome.artifacts]
    record: dict[str, object] = {
        "stage": stage,
        "success": outcome.success,
        "step_name": outcome.step_name,
        "duration_s": round(outcome.duration_s, 3),
        "artifact_names": sorted(path.name for path in artifacts if path.is_file()),
    }
    if outcome.error is not None:
        record["error_kind"] = outcome.error.kind.value
        record["error"] = outcome.error.message[:240]
    return outcome.success, record, artifacts


def run_acceptance(
    fixture_root: str | Path,
    *,
    workspace_root: str | Path | None = None,
    evidence_dir: str | Path | None = None,
    timeout_s: int = 120,
) -> dict[str, object]:
    """Run the real three-stage smoke and return only redacted evidence."""
    bundle = Path(fixture_root).resolve()
    case = get_smoke_case(_CASE_ID)
    readiness = preflight(case, root=bundle)
    report: dict[str, object] = {
        "schema_version": 1,
        "acceptance": "failed",
        "scope": "G-01b managed GROMACS minimal EM/EQ/PROD execution",
        "fixture_preflight": readiness.to_dict(),
        "execution_attempted": False,
        "stages": [],
        "structured_event_codes": [],
        "artifacts": [],
        "evidence_verification_issues": [],
    }
    if not readiness.ready:
        report["failure_summary"] = "fixture 或 GROMACS 工具预检未通过"
        return report

    base = Path(workspace_root).resolve() if workspace_root is not None else None
    if base is not None:
        base.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="willy-g01-gmx-", dir=str(base) if base else None) as temporary:
        run_dir = Path(temporary)
        _copy_fixture_to_run(bundle, run_dir)
        report["execution_attempted"] = True
        artifacts: list[Path] = []
        for stage in ("em", "eq", "prod"):
            success, stage_report, stage_artifacts = _run_stage(run_dir, stage, timeout_s)
            report["stages"].append(stage_report)  # type: ignore[index]
            artifacts.extend(stage_artifacts)
            if not success:
                report["failure_summary"] = f"{stage} 阶段未完成"
                break

        report["structured_event_codes"] = _event_codes(run_dir)
        report["artifacts"] = [
            item for item in (_artifact_record(path, run_dir) for path in artifacts) if item
        ]
        success = len(report["stages"]) == 3 and all(  # type: ignore[arg-type]
            bool(item.get("success")) for item in report["stages"]  # type: ignore[index]
        )
        if evidence_dir is not None:
            previous = os.environ.get(SMOKE_EVIDENCE_ENV)
            os.environ[SMOKE_EVIDENCE_ENV] = str(Path(evidence_dir).resolve())
            try:
                write_smoke_evidence(
                    case,
                    readiness,
                    success=success,
                    outputs=artifacts,
                    root=run_dir,
                    phase="execution",
                    execution_attempted=True,
                    command_label="willy-g01-minimal-em-eq-prod",
                    failure_summary=str(report.get("failure_summary", "")),
                )
                report["evidence_verification_issues"] = list(
                    verify_evidence(
                        Path(evidence_dir), (case,), phase="execution", require_execution=True,
                    )
                )
            finally:
                if previous is None:
                    os.environ.pop(SMOKE_EVIDENCE_ENV, None)
                else:
                    os.environ[SMOKE_EVIDENCE_ENV] = previous
        report["acceptance"] = "passed" if success and not report["evidence_verification_issues"] else "failed"
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture-root", type=Path, required=True)
    parser.add_argument("--initialize-fixture", action="store_true")
    parser.add_argument("--workspace-root", type=Path)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout-s", type=int, default=120)
    args = parser.parse_args()
    if args.timeout_s < 10 or args.timeout_s > 900:
        parser.error("--timeout-s 必须在 10 到 900 秒之间")
    if args.initialize_fixture:
        initialize_fixture_bundle(args.fixture_root)
    report = run_acceptance(
        args.fixture_root,
        workspace_root=args.workspace_root,
        evidence_dir=args.evidence_dir,
        timeout_s=args.timeout_s,
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if report["acceptance"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
