#!/usr/bin/env python3
"""Build a Willy client bundle with a deployment-owned gateway profile."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

SOURCE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE_ROOT / "src"))

from willy.managed_gateway_bundle import ManagedGatewayBundleError, build_managed_client_bundle  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, type=Path, help="部署者提供的非 loopback HTTPS profile JSON")
    parser.add_argument("--output", required=True, type=Path, help="新的客户端部署包目录")
    parser.add_argument("--source-root", type=Path, default=SOURCE_ROOT, help="Willy 源码根目录")
    args = parser.parse_args()
    try:
        output, fingerprint = build_managed_client_bundle(args.source_root, args.profile, args.output)
    except ManagedGatewayBundleError as exc:
        parser.error(str(exc))
    print(f"已生成 Willy 客户端部署包：{output}")
    print(f"profile 指纹：{fingerprint[:16]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
