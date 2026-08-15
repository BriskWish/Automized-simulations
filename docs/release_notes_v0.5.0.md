# Willy v0.5.0 发布记录

> 发布日期：2026-08-15 · 基线父提交：`7cf6f94`（v0.4.1） · 发布标识：Git tag `v0.5.0`

## 发布范围

本版本同步以下已实现变更：

- 运行助理支持严格 `/resume`、`/fork` 和 `/switch`；`/resume` 沿用原 run 的安全断点，`/fork` 创建带 `parent_run_id` 的独立子 run，`/switch` 只切换工程展示绑定。
- 运行助理对话按 `run_id` 原子保存到 `run_assistant_history.json`；刷新、fork、resume 和 switch 均遵循工程编号，不串历史。
- 配置确认启动前扫描当前进程可用 CPU affinity。默认 `nproc` 使用 `min(8, 可用核数)`；显式值超过可用核数时写回上限并警告，但不阻塞启动。fork 和运行时旧快照也执行同一边界兜底。
- GROMACS 通过显式 `WILLY_GMX_BIN`、项目 `.env` 或继承的 `PATH` 解析，与其他外部二进制保持同等优先级。
- G-01 停止回归使用足够的 SIGINT 确认窗口，并补充带 `fixture-manifest.json` 的最小受管 GROMACS EM/EQ/PROD 验收 harness。

## 可溯源工作记录

| 项目 | 记录 |
|---|---|
| 发布来源 | Git tag `v0.5.0`；tag 指向的提交是本版本唯一源码修订标识 |
| 基线 | `v0.4.1` / `7cf6f94` |
| Python | 3.12.3 |
| 系统 | Ubuntu/WSL2 内核 `6.18.33.2-microsoft-standard-WSL2`，x86_64 |
| 资源预检 | `os.sched_getaffinity(0)` 检测 20 个可用 CPU；`os.cpu_count()` 为 20 |
| 默认资源写回 | `defaults.nproc = 8`（`min(8, 20)`） |
| 测试门禁 | `pytest -q`：940 passed、10 skipped；`compileall` 通过；`git diff --check` 通过 |
| 测试台账 | 950 条 pytest + 18 条 LLM 场景 = 968 条记录，见 [`tests/reports/test_case_catalog.md`](../tests/reports/test_case_catalog.md) |
| 浏览器验证 | 本地 HTTP 服务返回 200；slash menu 资源已加载。完整 Playwright E2E 需目标环境安装可导入的 Playwright Python runtime |

## 验收边界

- G-01 的受管 GROMACS smoke harness 已纳入源码和 fixture 契约；真实工具执行仍需在具备 GROMACS 的目标机显式 opt-in，并保存脱敏 evidence。
- G-03 外置 SSD WSL 的并行资源限制按环境事实处理；Willy 只做核数规范化和非阻塞警告，不把该环境限制宣称为科学链路完成。
- G-06 的服务端历史、状态机和控制审计已有本地回归；刷新、双向 `/switch` 和 fork/resume 后的视觉交互仍需目标浏览器复验。

## 记录位置

运行事实继续写入各自 `md_run/<run_id>/` 的 `manifest.json`、`events.jsonl`、`status.json`、`provenance.json` 和结构化日志；发布记录只保存版本、环境摘要、核数和脱敏测试结论，不复制密钥、原始 prompt、命令、绝对路径或私有运行目录。
