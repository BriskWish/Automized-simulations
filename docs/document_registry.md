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
| [`quantum.md`](quantum.md) | 量子输入、电荷审计、Gaussian 溶剂库、后端、产物与错误契约 | 量子 | 后端、步骤、溶剂登记与快照、输入审计、电荷门禁、资源或产物变化。 |
| [`topology.md`](topology.md) | 参数化、组装和拓扑产物契约 | 拓扑 | 后端、manifest、组装、校验或重试变化。 |
| [`simulation.md`](simulation.md) | MD 协议、电荷舍入容差、非有限值与五段覆盖验收、证据许可、辅助命令超时重试、回滚和产物契约 | 模拟 | MD 协议、电荷容差、数值有效性、窗口与覆盖要求、阶段、执行时限与尝试次数、恢复、资源或后处理边界变化。 |
| [`environment_registry.md`](environment_registry.md) | Python 运行时与父解释器契约、外部工具发现、预检和许可 | 环境 | Python 支持范围、启动检查、工具、Vendor、环境变量或脱敏规则变化。 |
| [`status_api.md`](status_api.md) | 前端可见状态、环境预检和运行审计接口 | 前端 | 状态、运行时报告、事件、控制或公开字段变化。 |
| [`run_assistant.md`](run_assistant.md) | 故障位置与人工处理复查、报错/暂停恢复、最新建议确认、状态简图与独立分支 | 前端与模拟 | 助理事实来源、故障出口、刷新复查、控制动作、版本绑定、输入准入与上下文继承边界变化。 |
| [`ERR_WARN_Build.md`](ERR_WARN_Build.md) | Error/Warning 输出规范与电荷门禁边界 | 产品 | 输出 schema、阻塞与容差边界或展示规则变化。 |
| [`naming_convention.md`](naming_convention.md) | 模块、Agent 和工具命名 | 治理 | 命名、scope、tool 或目录结构变化。 |
| [`revision.md`](revision.md) | 工程原则、未实施规划（启动更新提示与数据保护）、同步清单和完成定义 | 架构 | 工程边界、规划范围、优先级或完成定义变化。 |
| [`testing.md`](testing.md) | 测试分层、命令和发布门禁 | 6 号 | 测试范围、marker、外部验收或发布规则变化。 |
| [`project_gap_analysis.md`](project_gap_analysis.md) | 当前验收缺口和关闭条件 | 0 号、5 号 | 缺口、责任、验收范围或关闭条件变化。 |
| [`g07_python_matrix_20260908.json`](../tests/reports/audits/g07_python_matrix_20260908.json) | 本机三版本独立安装、真实 Web 重启、无科学计算续跑及错误矩阵证据 | 6 号 | 解释器、验收范围、实现散列或回归结果变化；核心冒烟与扩大回归结论必须分别记录。 |
| [`g07_python_matrix_20260910.json`](../tests/reports/audits/g07_python_matrix_20260910.json) | 本机三版本默认全量回归、真实冒烟、前端构建及完整快照一致性复验 | 6 号 | 源码、产物、解释器或回归结果变化；记录未覆盖变更，不以快照通过替代当前版本放行。 |
| [`g09_eq_coverage_20260906.json`](../tests/reports/audits/g09_eq_coverage_20260906.json) | EQ 覆盖门禁的真实产物隔离复验与回归脱敏证据 | 6 号 | 新验收规则的受管复验、来源产物与实现散列、许可结果或回归证据变化。 |
| [`../tests/reports/test_case_catalog.md`](../tests/reports/test_case_catalog.md) | 当前测试用例台账 | 6 号 | pytest 收集结果或 LLM 场景变化；当前生成台账（1968 条 pytest、18 条 LLM 场景，共 1986 条记录）。 |
| [`gateway.md`](gateway.md) | 未来托管网关的安全边界 | 网关 | 重新纳入产品范围时。 |
| [`remote_execution.md`](remote_execution.md) | 未来远程执行的安全边界 | 模拟与前端 | 重新纳入产品范围时。 |

## 同步规则

Gaussian Generic/Read 的本机 EC 优化证据：
[`gaussian_smd_generic_20260921.json`](../tests/reports/audits/gaussian_smd_generic_20260921.json)，
由 `tests/tools/g16_smd_generic_acceptance.py` 生成；责任为量子与测试。只声明输入接受和收敛，
不将二参数介电近似视作完整 SMD 参数化验证。

1. 代码、测试、责任文档和用户可见说明必须在同一变更中保持一致。
2. 只有代码与对应测试或真实受管验收支持的能力才能写入产品文档。
3. 无法在当前目标环境证明的行为必须写入 `project_gap_analysis.md`，不能以推测补全。
4. 新增、删除或改名文档时，同步本台账和 `docs/README.md`。
5. 文档不保存版本流水、环境快照或问题复盘；需要追溯时使用 Git 与受控测试证据。
6. 未实施规划集中写入 `revision.md` 并明确状态、范围与验收条件，相关缺口登记于 `project_gap_analysis.md`；不得表述为当前产品能力。
