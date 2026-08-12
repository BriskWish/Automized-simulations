#!/usr/bin/env python3
"""Retired gateway-bundle entry point retained for future maintenance."""

from __future__ import annotations

import argparse


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    # Retain historical options so old automation reaches the explicit
    # release-boundary error rather than an unrelated argument error.
    parser.add_argument("--profile", help=argparse.SUPPRESS)
    parser.add_argument("--output", help=argparse.SUPPRESS)
    parser.add_argument("--source-root", help=argparse.SUPPRESS)
    parser.parse_args()
    parser.error("当前版本不支持生成托管网关客户端部署包；相关构建代码仅作为后续版本归档保留。")


if __name__ == "__main__":
    raise SystemExit(main())
