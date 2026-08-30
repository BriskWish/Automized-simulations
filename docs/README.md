# Willy 文档

`docs/README.md` 是导航入口；文档用途、责任人和更新规则以 `document_registry.md` 为准。文档只描述当前能力、当前契约和当前验收条件；一次性证据由 `tests/reports/` 管理。

## 快速导航

| 需要了解的内容 | 文档 |
|---|---|
| 产品定位、安装、依赖和使用方式 | [`README.md`](../README.md) |
| 系统架构和十步流程 | [`Willy.md`](Willy.md) |
| 当前分子、参数与默认规则 | [`knowledge_molecules.md`](knowledge_molecules.md) |
| GROMACS 诊断条目 | [`knowledge_mdrun.md`](knowledge_mdrun.md) |
| 量子、拓扑、模拟和运行环境契约 | [`quantum.md`](quantum.md)、[`topology.md`](topology.md)、[`simulation.md`](simulation.md)、[`environment_registry.md`](environment_registry.md) |
| 状态接口、运行助理与 Error/Warning 规则 | [`status_api.md`](status_api.md)、[`run_assistant.md`](run_assistant.md)、[`ERR_WARN_Build.md`](ERR_WARN_Build.md) |
| 命名、工程路线和测试门禁 | [`naming_convention.md`](naming_convention.md)、[`revision.md`](revision.md)、[`testing.md`](testing.md) |
| 当前验收缺口 | [`project_gap_analysis.md`](project_gap_analysis.md) |
| 文档责任和同步规则 | [`document_registry.md`](document_registry.md) |
| 后续版本的网关或远程执行边界 | [`gateway.md`](gateway.md)、[`remote_execution.md`](remote_execution.md) |

## 分类

- **产品与治理**：根 `README.md`、`Willy.md`、`employees.md`、`document_registry.md`。
- **领域契约**：`quantum.md`、`topology.md`、`simulation.md`、`environment_registry.md`、`status_api.md`。
- **知识与交互**：`knowledge_molecules.md`、`knowledge_mdrun.md`、`run_assistant.md`、`ERR_WARN_Build.md`。
- **工程与验收**：`revision.md`、`testing.md`、`project_gap_analysis.md`、`naming_convention.md`。
- **非当前入口**：`gateway.md`、`remote_execution.md` 只保留未来恢复功能所需的边界。

## 维护规则

改变步骤、配置、产物、工具、状态字段、外部依赖、公开能力或验收结论时，必须同步责任文档、测试和 `document_registry.md`。新增、删除或重命名文档时，同时更新本索引。
