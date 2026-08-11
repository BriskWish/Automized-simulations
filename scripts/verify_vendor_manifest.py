#!/usr/bin/env python3
"""Validate the bundled vendor inventory without running scientific software."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from willy.vendor_manifest import audit_vendor_manifest, format_vendor_manifest_audit


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=ROOT)
    parser.add_argument(
        "--require-release-ready",
        action="store_true",
        help="treat pending source/license/distribution evidence as a failure",
    )
    parser.add_argument("--json", action="store_true", help="emit a machine-readable summary")
    args = parser.parse_args()

    audit = audit_vendor_manifest(args.project_root)
    if args.json:
        print(json.dumps(audit.as_dict(), ensure_ascii=False, sort_keys=True))
    else:
        print(format_vendor_manifest_audit(audit))
    if not audit.integrity_ok:
        return 1
    return 0 if not args.require_release_ready or audit.release_ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
