# 命名规范

## 工具

工具名称使用 `tools_<verb>_<scope>`：

- `scope` 只允许 `global`、`quantum`、`topology`、`simulation`、`run`。
- 查询使用 `lookup`、`get`、`inspect`；修改使用 `configure`、`modify`、`retry`、`run`、`select`。
- 工具必须只处理所属层公开的输入和产物，不能用名称绕过权限边界。

工具模块命名为 `toolist_<scope>.py`，Agent 命名为 `agent_<scope>.py`。Run Assistant 工具必须只读；高影响控制由服务端命令入口处理。

## 文件与数据

- 运行目录：`md_run/<run_id>/`。
- 方案草稿目录：`md_run/temp__YYYYMMDDNNNN/`；只保存方案助理对话，不是 run。
- 待确认方案目录：`md_run/plan__YYYYMMDDNNNN/`；保存对话和候选配置，明确确认后才原子正式化为 `md__YYYYMMDDNNNN/`。
- 配置快照：`config.json`。
- 统一元数据：`run_manifest.json`。
- 公开状态：`status.json`、`events.jsonl`。
- 私有结构化日志：`logs/structured.jsonl`。
- 阶段文件使用 `em`、`eq`、`prod` 前缀。

名称不得包含路径分隔符、路径跳转或未受限的用户输入。残基名、文件名和 run 标识在写入前必须验证。

`temp__` 与 `plan__` 不得进入 `RunRegistry`、运行状态接口或 `/switch` 运行命令；它们仅由方案工作区 API 使用。`plan__` 正式化后保留其原始标识在正式工程的 `proposal.json`、registry manifest 与 `events.jsonl` 中，以支持追溯但不公开候选配置。正式 `md__` 可持久化方案助理会话；从该 run 产生的新候选必须分配新的 `plan__`，不得覆盖历史 run。

## 命名变更

新增或改名 tool、toolist、Agent、模块、状态或产物时，同步更新实现、测试、`docs/README.md`、`document_registry.md` 和责任领域文档。
