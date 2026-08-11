# Test Reports

This directory contains generated test-owned evidence only.  Release baselines
are created in a disposable repository copy by:

```bash
python -m tests.tools.release_baseline --batch-id regression-YYYYMMDD
```

Each batch report records package and tool versions, a configuration SHA-256,
stage outcomes, artifact hashes, and an acceptance conclusion.  It never
stores source worktree paths, command output, raw logs, `.env` values, API
keys, tokens, or credentials.  The source copy excludes `md_run/`, so running
the command cannot affect an active simulation.

目录约定：根目录的 `test_case_catalog.md` 是当前自动生成的测试目录；`audits/` 只保存带日期的一次性环境、网关或安全检查快照；`baselines/` 保存脱敏的发布批次摘要。设计、规范、计划和缺口台账必须回到 `docs/` 维护，不在此处创建第二份事实来源。
