# Willy 文档台账

本台账定义当前文档的用途、责任范围和更新触发条件。文档状态只描述当前事实，不保存版本流水、环境快照或问题复盘。

| 文档 | 用途 | 责任范围 | 更新触发条件 |
|---|---|---|---|
| [`../README.md`](../README.md) | 产品入口、安装、依赖、公开边界 | 产品与前端 | 公开能力、安装方式、依赖或许可变化。 |
| [`../LICENSE.md`](../LICENSE.md) | Willy 原创内容的使用条款与第三方边界 | 开发者 | 授权或第三方处理方式变化。 |
| [`Willy.md`](Willy.md) | 架构、主流程和模块关系 | 架构 | 主流程、模块边界、运行目录或产品承诺变化。 |
| [`employees.md`](employees.md) | 协作职责和共同工程原则 | 治理 | 责任、权限或维护边界变化。 |
| [`knowledge_molecules.md`](knowledge_molecules.md) | 分子识别元数据和参数规则 | 知识库 | 分子、别名、基组、力场或默认值变化。 |
| [`knowledge_mdrun.md`](knowledge_mdrun.md) | GROMACS 诊断知识库 | 模拟 | 诊断条目、检索边界或来源字段变化。 |
| [`quantum.md`](quantum.md) | 量子输入、后端、产物与错误契约 | 量子 | 后端、步骤、输入审计、资源或产物变化。 |
| [`topology.md`](topology.md) | 参数化、组装和拓扑产物契约 | 拓扑 | 后端、manifest、组装、校验或重试变化。 |
| [`simulation.md`](simulation.md) | MD 协议、阶段许可、回滚和产物契约 | 模拟 | MD 协议、阶段、恢复、资源或后处理边界变化。 |
| [`environment_registry.md`](environment_registry.md) | 外部工具发现、预检、许可和运行环境 | 环境 | 工具、Vendor、环境变量、预检或脱敏规则变化。 |
| [`status_api.md`](status_api.md) | 前端可见状态和运行审计接口 | 前端 | 状态、事件、控制或公开字段变化。 |
| [`run_assistant.md`](run_assistant.md) | 运行助理权限、命令和历史绑定 | 前端与模拟 | 助理事实来源、控制动作、历史或 Prompt 边界变化。 |
| [`ERR_WARN_Build.md`](ERR_WARN_Build.md) | Error/Warning 输出规范 | 产品 | 输出 schema 或展示规则变化。 |
| [`naming_convention.md`](naming_convention.md) | 模块、Agent 和工具命名 | 治理 | 命名、scope、tool 或目录结构变化。 |
| [`revision.md`](revision.md) | 工程原则、同步清单和完成定义 | 架构 | 工程边界、优先级或完成定义变化。 |
| [`testing.md`](testing.md) | 测试分层、命令和发布门禁 | 6 号 | 测试范围、marker、外部验收或发布规则变化。 |
| [`project_gap_analysis.md`](project_gap_analysis.md) | 当前验收缺口和关闭条件 | 0 号、5 号 | 缺口、责任、验收范围或关闭条件变化。 |
| [`../tests/reports/test_case_catalog.md`](../tests/reports/test_case_catalog.md) | 当前测试用例台账 | 6 号 | pytest 收集结果或 LLM 场景变化；当前生成台账（936 条 pytest、18 条 LLM 场景，共 954 条记录）。 |
| [`gateway.md`](gateway.md) | 未来托管网关的安全边界 | 网关 | 重新纳入产品范围时。 |
| [`remote_execution.md`](remote_execution.md) | 未来远程执行的安全边界 | 模拟与前端 | 重新纳入产品范围时。 |

## 同步规则

1. 代码、测试、责任文档和用户可见说明必须在同一变更中保持一致。
2. 只有代码与对应测试或真实受管验收支持的能力才能写入产品文档。
3. 无法在当前目标环境证明的行为必须写入 `project_gap_analysis.md`，不能以推测补全。
4. 新增、删除或改名文档时，同步本台账和 `docs/README.md`。
5. 文档不保存版本流水、环境快照或问题复盘；需要追溯时使用 Git 与受控测试证据。
