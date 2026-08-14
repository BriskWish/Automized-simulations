# Willy 文档索引

> 本文件是 `docs/` 的唯一导航入口。文档用途、责任人和生命周期状态以 [`document_registry.md`](document_registry.md) 为准。最后整理：2026-08-14。

## 文档架构

```text
仓库根 README.md                 产品入口与公开能力
docs/README.md                   导航、分类和阅读路径
docs/document_registry.md        权威文档台账与维护规范
docs/<主题>.md                   领域知识、设计、计划、规范或评估
tests/reports/                   自动生成的测试目录与时间戳审计证据
```

文档按首要维护目的分类；设计、规范和计划保留在 `docs/`，自动生成目录与审计快照归档到 `tests/reports/`，避免把一次性证据混入长期维护文档。状态不在本索引中重复维护；发现不一致时，先在文档台账标记，再由对应领域负责人修正事实来源。

## 快速导航

| 你想做什么 | 读这份 |
|------|------|
| 快速了解项目是什么、怎么跑 | [`README.md`](../README.md) |
| 理解整体架构、主流程和当前能力 | [`Willy.md`](Willy.md) |
| 查团队职责、文档所有权和共同约束 | [`employees.md`](employees.md) |
| 查分子参数、基组、力场、MD 参数或运行诊断证据状态 | [`knowledge.md`](knowledge.md)、[`knowledge_mdrun.md`](knowledge_mdrun.md) |
| 理解量子、拓扑、模拟/后处理或外部软件运行环境；查阅远程/Gateway 后续设计 | [`quantum_design.md`](quantum_design.md)、[`topology_design.md`](topology_design.md)、[`simulation_design.md`](simulation_design.md)、[`remote_execution_design.md`](remote_execution_design.md)、[`gateway.md`](gateway.md)、[`environment_registry_design.md`](environment_registry_design.md) |
| 给前端或调用方接入状态与运行审计 | [`status_api.md`](status_api.md) |
| 制定修订路线、确定边界和验收规则 | [`revision_strategy.md`](revision_strategy.md) |
| 建设运行助理、运行历史与受控续跑 | [`run_assistant_design.md`](run_assistant_design.md) |
| 运行回归、真实 smoke 与发布门禁 | [`testing_strategy.md`](testing_strategy.md) |
| 查询任一测试的对象、内容、方式和执行 ID | [`../tests/reports/test_case_catalog.md`](../tests/reports/test_case_catalog.md) |
| 查看一次性环境、网关和安全审计快照 | [`../tests/reports/README.md`](../tests/reports/README.md) |
| 新增或重命名 tool、toolist、agent 或模块 | [`naming_convention.md`](naming_convention.md)、[`revision_strategy.md`](revision_strategy.md) |
| 遵循 LLM Error/Warning 输出协议 | [`ERR_WARN_Build.md`](ERR_WARN_Build.md) |
| 了解项目现状、缺口和修复优先级 | [`project_gap_analysis.md`](project_gap_analysis.md) |
| 查询任意文档的用途、状态、责任人或更新条件 | [`document_registry.md`](document_registry.md) |

## 文档分类

### 产品与架构类

| 文档 | 用途 |
|------|------|
| [`Willy.md`](Willy.md) | 系统架构、端到端主流程、模块关系和产品能力说明。 |
| [`employees.md`](employees.md) | 团队职责、文档所有权、跨层接口与共同工程原则。 |

### 知识库类

| 文档 | 用途 |
|------|------|
| [`knowledge.md`](knowledge.md) | 分子识别元数据、力场/基组/MD 参数与受限诊断记录；分子表是当前 Config Agent 检索数据源，但可启动性仍以 `struct/` 中的量子输入为准。 |
| [`knowledge_mdrun.md`](knowledge_mdrun.md) | GROMACS 运行时错误、`mdrun` 特性与 `.mdp` 参数的审阅条目；仅由受限的 EQ proposal 检索工具按数字和名称读取。 |

### 设计与接口类

| 文档 | 用途 |
|------|------|
| [`quantum_design.md`](quantum_design.md) | 量子层后端、产物和错误处理契约。 |
| [`topology_design.md`](topology_design.md) | GAFF/OPLS 参数化、主拓扑和 ITP 修订契约。 |
| [`simulation_design.md`](simulation_design.md) | GROMACS 输入、EM/EQ/PROD、回滚和产物契约。 |
| [`environment_registry_design.md`](environment_registry_design.md) | 外部软件发现、环境变量优先级、预检和子进程环境的统一设计。 |
| [`remote_execution_design.md`](remote_execution_design.md) | 后续版本参考：MD 层 SSH/Slurm GROMACS 执行、profile、同步、停止和验收边界。 |
| [`gateway.md`](gateway.md) | 后续版本参考：托管 LLM 网关、设备注册、白名单、用量、双模式客户端和安全发布计划。 |
| [`status_api.md`](status_api.md) | 流水线状态、run 级审计字段和前端消费接口。 |

### 执行计划与运行类

| 文档 | 用途 |
|------|------|
| [`revision_strategy.md`](revision_strategy.md) | 总体修订路线、优先级、模块边界和完成定义。 |
| [`run_assistant_design.md`](run_assistant_design.md) | 运行助理的阶段计划、权限模型、审计契约和后续能力边界。 |
| [`testing_strategy.md`](testing_strategy.md) | 测试分层、外部 smoke、LLM eval 和发布质量门禁。 |
| [`../tests/reports/test_case_catalog.md`](../tests/reports/test_case_catalog.md) | 由 pytest 收集生成的逐条测试与 LLM mock eval 用例目录。 |

### 规范与变更类

| 文档 | 用途 |
|------|------|
| [`naming_convention.md`](naming_convention.md) | tool、toolist、agent 与执行模块的命名规范。 |
| [`ERR_WARN_Build.md`](ERR_WARN_Build.md) | LLM Error/Warning 的结构和内容规范。 |
| [`revision_strategy.md`](revision_strategy.md) | 总体修订路线及重构同步检查清单。 |

### 评估与缺口类

| 文档 | 用途 |
|------|------|
| [`project_gap_analysis.md`](project_gap_analysis.md) | 可追踪的缺口清单、严重度和建议执行顺序。 |

## 维护入口

每次涉及代码、配置、测试、产物、公共接口或产品承诺的变更，都必须遵循 [`document_registry.md`](document_registry.md) 的维护规范。改动文档时，更新其台账条目的核验日期、状态或待同步说明；新增文档时，同时登记台账、快速导航和上方分类。
