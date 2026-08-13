# Test Reports

This directory contains generated test-owned evidence only.  Release baselines
are created in a disposable repository copy by:

```bash
python -m tests.tools.release_baseline --batch-id regression-YYYYMMDD
```

Each batch report records package and tool versions, a configuration SHA-256,
stage outcomes, artifact hashes, and an acceptance conclusion. It never stores
source worktree paths, command output, raw logs, `.env` values, API keys,
tokens, or credentials. The source copy excludes `md_run/` and real `.env`
files, while its subprocesses receive only a minimal system environment and
the disposable copy's `src/`, rather than developer `WILLY_*` overrides. It
runs Python packaging, test, compile and mock-eval gates only, so it cannot
affect an active simulation. A host without
`ensurepip` records the isolated-install gate as `blocked` with a bounded
reason code rather than treating that environment limitation as a test failure.

正式 source release 必须先执行 `python3 scripts/build_release_staging.py --output <empty-dir>`，再对
该 staging 目录执行 `scripts/verify_vendor_manifest.py --artifact-root <empty-dir> --require-release-ready`。
构建器只复制清单许可的 vendor 文件且不执行二进制；不要直接发布包含整个 `vendor/` 的工作树归档。

目录约定：根目录的 `test_case_catalog.md` 是当前自动生成的测试目录；`audits/` 只保存带日期的一次性环境、网关或安全检查快照；`baselines/` 保存脱敏的发布批次摘要。设计、规范、计划和缺口台账必须回到 `docs/` 维护，不在此处创建第二份事实来源。
