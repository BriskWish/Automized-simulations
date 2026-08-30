# 命名规范

## 工具

工具名称使用 `tools_<verb>_<scope>`：

- `scope` 只允许 `global`、`quantum`、`topology`、`simulation`、`run`。
- 查询使用 `lookup`、`get`、`inspect`；修改使用 `configure`、`modify`、`retry`、`run`、`select`。
- 工具必须只处理所属层公开的输入和产物，不能用名称绕过权限边界。

工具模块命名为 `toolist_<scope>.py`，Agent 命名为 `agent_<scope>.py`。Run Assistant 工具必须只读；高影响控制由服务端命令入口处理。

## 文件与数据

- 运行目录：`md_run/<run_id>/`。
- 配置快照：`config.json`。
- 统一元数据：`run_manifest.json`。
- 公开状态：`status.json`、`events.jsonl`。
- 私有结构化日志：`logs/structured.jsonl`。
- 阶段文件使用 `em`、`eq`、`prod` 前缀。

名称不得包含路径分隔符、路径跳转或未受限的用户输入。残基名、文件名和 run 标识在写入前必须验证。

## 命名变更

新增或改名 tool、toolist、Agent、模块、状态或产物时，同步更新实现、测试、`docs/README.md`、`document_registry.md` 和责任领域文档。
