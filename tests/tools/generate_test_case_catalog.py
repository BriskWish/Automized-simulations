"""Canonical tests/ entry point for generating the test-case catalog."""

from __future__ import annotations

from pathlib import Path
import argparse

from . import catalog_impl

ROOT = Path(__file__).resolve().parents[2]


def main(output: Path | None = None) -> None:
    """Generate the catalog from the actual pytest collection result."""
    target = output or (ROOT / "tests" / "reports" / "test_case_catalog.md")
    catalog_impl.main(target)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="repository-relative output path")
    args = parser.parse_args()
    main(args.output)
