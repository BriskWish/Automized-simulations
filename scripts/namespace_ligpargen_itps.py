#!/usr/bin/env python3
"""Namespace one LigParGen ITP for safe multi-component assembly.

The production pipeline calls :mod:`willy.topology.itp_namespace` directly;
this wrapper is provided for inspecting or repairing an explicit run-local
assembly copy without modifying the backend source ITP.
"""

from willy.topology.itp_namespace import main


if __name__ == "__main__":
    raise SystemExit(main())
