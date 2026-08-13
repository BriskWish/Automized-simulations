#!/usr/bin/env python3
"""Validate the bundled vendor inventory without running scientific software."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from willy.vendor_manifest import (
    audit_release_artifact,
    audit_vendor_manifest,
    format_vendor_manifest_audit,
    release_artifact_ready,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=ROOT)
    parser.add_argument(
        "--require-release-ready",
        action="store_true",
        help="treat pending source/license/distribution evidence as a failure",
    )
    parser.add_argument(
        "--artifact-root",
        type=Path,
        help="audit an already-built release staging directory for declared vendor inclusions/exclusions",
    )
    parser.add_argument("--json", action="store_true", help="emit a machine-readable summary")
    args = parser.parse_args()

    audit = audit_vendor_manifest(args.project_root)
    artifact_issues: tuple[str, ...] = ()
    if args.artifact_root is not None:
        artifact_issues = audit_release_artifact(
            args.artifact_root,
            manifest_project_root=args.project_root,
        )
    artifact_ready = (
        args.artifact_root is not None and release_artifact_ready(audit, artifact_issues)
    )
    if args.json:
        payload = audit.as_dict()
        if args.artifact_root is not None:
            payload["artifact_root"] = str(args.artifact_root)
            payload["artifact_issues"] = list(artifact_issues)
            payload["artifact_release_ready"] = artifact_ready
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        print(format_vendor_manifest_audit(audit))
        if args.artifact_root is not None:
            print(f"release artifact readiness: {'ready' if artifact_ready else 'blocked'}")
            for issue in artifact_issues:
                print(f"  ! {issue}")
    if artifact_issues:
        return 1
    if not audit.integrity_ok:
        return 1
    if not args.require_release_ready:
        return 0
    return 0 if (artifact_ready if args.artifact_root is not None else audit.release_ready) else 1


if __name__ == "__main__":
    raise SystemExit(main())
