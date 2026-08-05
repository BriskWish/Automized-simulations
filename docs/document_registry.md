# Willy 文档台账与维护规范

> 本文件是项目文档用途、责任人和生命周期状态的唯一权威台账。`docs/README.md` 只负责导航；设计细节只保留在各主题文档中。最后核验：2026-08-05（初始质量密度默认值 1.5 g/cm3、显式 Packmol PBC、实际盒审计、当前 run 隔离、运行 provenance、RunStore、受管 GROMACS/量子/拓扑子进程生命周期、StepRegistry、执行模块依赖归属、完整配置外层 schema、external smoke 门禁、欢迎后实时信息卡、简化 ETA 与 EQ 待确认方案替换/确认状态流转已复核）。

## 状态定义

| 状态 | 含义 |
|------|------|
| `当前` | 已针对列出的事实来源核验；发生对应触发条件时必须重新核验。 |
| `长期维护` | 规则或知识库已可用，持续随项目演进更新。 |
| `执行中` | 已批准的计划尚未完成；文档必须保留已完成项和下一步。 |
| `待同步` | 已知内容与代码、测试或其他事实来源不一致；负责人必须在下一次相关改动中修正。 |
| `参考资料` | 静态背景或外部调研，不构成运行时或产品承诺。 |

状态描述的是文档和功能的当前关系，不把“文档已写完”误表述为“产品能力已完成”。

## 文档台账

| 文档 | 类别 | 责任人 | 状态 | 最后核验 | 更新触发条件 |
|------|------|------|------|------|------|
| [`../README.md`](../README.md) | 产品入口 | 0 号、4 号 | 当前，长期维护：明确“可识别分子”与“具备 run 输入的分子”不同，真实量子/拓扑组合验收仍待目标验收机。 | 2026-08-05 | 公开主流程、安装方式、UI 入口或目录结构变化。 |
| [`README.md`](README.md) | 文档导航 | 5 号 | 当前，长期维护：导航已标明知识库元数据不替代 `struct/` 量子输入。 | 2026-08-05 | 新增、删除、重命名或重新分类任何文档。 |
| `document_registry.md` | 文档治理 | 5 号 | 当前，长期维护：已登记 run 隔离、运行 provenance、RunStore、受管进程生命周期、StepRegistry、执行模块依赖归属、完整配置外层 schema、external smoke 门禁、对话内信息卡及 EQ 待确认方案替换/确认状态流转契约，以及初始质量密度默认值 1.5 g/cm3 | 2026-08-05 | 文档状态、责任边界、事实来源或维护规则变化。 |
| [`Willy.md`](Willy.md) | 产品与架构 | 0 号、5 号 | 当前，长期维护：任务页通过待确认方案后的明确文本回复启动；EQ 协议调整可先替换为新的脱敏方案、再由明确批准语请求指定步骤重跑，且恢复固定经过 `retrying` 后进入 `running`；实际中止仅限按钮服务端二次确认；建盒默认按 `1.5 g/cm3` 的拓扑质量估算并审计实际 PBC 盒矢量；总计 46 个工具，其中运行助理 9 个只读工具；可视化优先读取启动锁 run、否则按目录数字选择最新 run 的 PDB/MOL2 文件，并以完整文件名呈现 | 2026-08-05 | 主流程、模块边界、工具清单、运行目录或产品承诺变化。 |
| [`employees.md`](employees.md) | 团队治理 | 0 号、5 号 | 当前，长期维护：前端负责协议调整摘要和批准交互；文本中止/暂停词没有停止权限；工具总数随新增只读建盒审计工具更新 | 2026-08-04 | 职责、领域文件、共同工程原则或文档所有权变化。 |
| [`knowledge.md`](knowledge.md) | 运行时知识库与诊断经验边界 | 5 号，0-3 号按领域供数 | 当前，长期维护：分子表提供识别元数据；实际可启动组分仍要求 `struct/<name>.gjf` 或可复用中间产物。已清理 run 的历史诊断不再作为可复核运行证据。 | 2026-08-05 | 分子、别名、力场、基组、MD 默认值、工作流步骤或已验证诊断案例变化。 |
| [`lithium-salts.md`](lithium-salts.md) | 领域参考 | 5 号 | 参考资料 | 2026-08-01 | 接入运行时知识库、更新外部来源或决定归档时。 |
| [`quantum_design.md`](quantum_design.md) | 量子设计 | 1 号 | 当前，长期维护：所有外部量子命令使用受管进程组生命周期与 run 内脱敏审计 | 2026-08-05 | 量子后端、步骤、产物、依赖、工具、错误或子进程生命周期契约变化。 |
| [`topology_design.md`](topology_design.md) | 拓扑设计 | 2 号 | 当前，长期维护：Sobtop、LigParGen/BOSS 和 obabel 使用受管进程组生命周期与 run 内脱敏审计 | 2026-08-05 | 后端、manifest、组装、产物校验、重试策略或子进程生命周期契约变化。 |
| [`simulation_design.md`](simulation_design.md) | 模拟设计 | 3 号 | 当前，长期维护：含 `mdrun -v` ETA（兼容 GROMACS 2025 `will finish <ctime>`）、阶段产物心跳、LLM 不可伪造的协议确认边界、修复工具真实步骤身份、默认 1.5 g/cm3 的质量密度建盒/显式 PBC/实际盒审计、受管 GROMACS 进程生命周期、RunStore/provenance、StepRegistry、完整配置外层 schema 和 EM/EQ/PROD 完成门禁 | 2026-08-05 | MD 协议、主流程外后处理边界、阶段 manifest、恢复、回滚、实时 ETA 或真实验收变化。 |
| [`postprocessing_design.md`](postprocessing_design.md) | 后处理设计 | 3 号 | 当前，长期维护 | 2026-08-02 | 分析输入、产物、PBC 策略、统计方法或验收变化。 |
| [`environment_registry_design.md`](environment_registry_design.md) | 外部运行环境设计 | 0 号 | 当前，长期维护：核心注册、执行模块依赖归属、模块预检、子进程注入，以及表单值驱动、脱敏且受限的 OpenAI-compatible LLM 连通性检查、临时 Key 与 `/v1` 路径提示已实现；版本兼容性与自动重规划待评估 | 2026-08-05 | 外部工具、LLM 或软件环境变量优先级、预检、子进程环境或脱敏运行报告变化。 |
| [`status_api.md`](status_api.md) | 公共接口 | 0 号、4 号 | 当前，长期维护：含 checkpoint-first 停止、仅按钮的服务端两次确认与失败回执可重试、失活 run 对账、活动 MD 阶段状态越级纠正、EQ 协议调整的 `awaiting_confirmation`/`pending_action` 契约；确认和替代方案仅在当前 run 的 `awaiting_confirmation` 动作上可用，其他状态文本保持只读；无歧义文本确认不进入只读 LLM，启动锁保留后立即写入 `confirmation_retry_started`/`retrying`，启动失败恢复等待快照，恢复固定为 `awaiting_confirmation -> retrying -> running`；欢迎后同 run 状态与待确认动作组成原子快照并以独立气泡替换，ETA 使用简化前端文案，旧 run 不得泄漏；同 run 配置指纹校验且仅允许从 Step 9 或建盒修改所需的 Step 7 受控重跑；事件单调序列、注册表产物契约、provenance 边界，UTC 存储/本地展示的 ETA 与运行心跳字段、GROMACS 2025 本地 `ctime` ETA 转换，以及受限的实际建盒审计查询 | 2026-08-05 | `PipelineStatus`、事件、停止控制、公开与私有 manifest 边界、RunRegistry、ETA 快照或前端消费字段变化。 |
| [`revision_strategy.md`](revision_strategy.md) | 总体计划 | 0 号 | 执行中：Phase 1 已完成 StepRegistry、执行模块依赖归属、完整配置外层 schema、可恢复 RunStore 写入、provenance、进程生命周期、external fixture 完整性/证据与 CI 质量门禁；GROMACS/量子/OPLS 真实执行 fixture、成功证据和 CI 远程全绿仍待推进 | 2026-08-05 | Phase 状态、环境/运行注册边界、优先级或完成定义变化。 |
| [`run_assistant_design.md`](run_assistant_design.md) | 运行计划 | 0 号、3 号、4 号 | 执行中：Phase A 与性能/可靠性升级核心实现完成（含 ETA 与独立心跳、6 条上下文、快速路径、降级与脱敏遥测；ETA 兼容 GROMACS 2025 `will finish <ctime>`）；欢迎语后状态、待确认方案和普通回复均以独立对话气泡显示，定时卡不进入 LLM 历史，ETA 简写且全部绑定当前 run；已增加只读、白名单化的实际建盒审计查询；EQ 失败后的 LLM 方案、替代方案、仅 `awaiting_confirmation` 可用的无歧义文本授权及立即可见的 `retrying` 状态、启动失败回退以及 `awaiting_confirmation -> retrying -> running` 的 Step 9/7 受控重跑已实现；UI 的停止仍只允许服务端两次确认按钮；真实验收待补；Phase B 的策展经验检索及 Phase C 的其他 resume/retry/fork、Phase D 待执行 | 2026-08-04 | run 数据契约、实时 ETA、性能、经验检索、权限、控制动作或任一 Phase 验收变化。 |
| [`testing_strategy.md`](testing_strategy.md) | 验收计划 | 6 号 | 当前，长期维护：本地基线为 586 passed、8 skipped；质量密度建盒、ETA、用户确认、状态越级防护与当前 run 隔离回归保留，新增 external fixture 的版本/文件集合/大小/SHA-256 校验、required 门禁/执行证据、GROMACS/量子/拓扑受管进程组生命周期、provenance、可恢复 RunStore、StepRegistry、配置外层 schema 与执行模块依赖归属覆盖；真实端点仍仅显式 opt-in | 2026-08-05 | 测试基线、marker、ETA、smoke 证据、LLM eval、受管进程生命周期或发布门禁变化。 |
| [`test_case_catalog.md`](test_case_catalog.md) | 测试用例台账 | 6 号 | 当前，长期维护：由收集脚本生成，现为 594 pytest + 18 LLM = 612 条，含受管进程生命周期、可恢复 RunStore、外部 fixture 完整性/执行证据和原有 EQ 待确认、当前 run 隔离、ETA、建盒/可视化契约测试 | 2026-08-05 | pytest 收集结果、LLM eval 场景、测试对象或测试方式变化。 |
| [`naming_convention.md`](naming_convention.md) | 命名规范 | 5 号 | 当前，长期维护：46 个 tool 的 10/8/6/13/9 分布已按实际定义复核。 | 2026-08-05 | 新增 scope、tool 总数、toolist、agent 或文件命名规则变化。 |
| [`ERR_WARN_Build.md`](ERR_WARN_Build.md) | 输出规范 | 0 号、5 号 | 当前，长期维护 | 2026-08-01 | Error/Warning schema、前端展示或配置 Agent 输出变化。 |
| [`reconstruction.md`](reconstruction.md) | 变更规范 | 5 号 | 当前，长期维护：已覆盖步骤/执行模块注册、动作与恢复契约、配置持久化、run 事务/溯源和受管进程关联方。 | 2026-08-05 | 跨层关联方、运行注册、环境注册、重构流程或常见遗漏模式变化。 |
| [`project_evaluation.md`](project_evaluation.md) | 状态评估 | 0 号、5 号 | 当前快照，长期维护：确定性、mock eval、受管进程、可恢复写入与外部验收边界已复核 | 2026-08-05 | 发布前、主要 Phase 完成后或任何结论证据失效时。 |
| [`project_gap_analysis.md`](project_gap_analysis.md) | 问题台账 | 0 号、5 号，领域负责人认领 | 执行中，长期维护：配置/步骤/依赖结构化缺口已关闭；真实 fixture、external 成功证据与 CI 远程执行仍待完成 | 2026-08-05 | 缺口修复、发现新风险、严重度变化或发布前。 |

## 强制维护规范

以下规则适用于 0-6 号所有工程师。5 号负责台账与一致性审查，但不替代领域负责人维护自己的技术事实。

1. **同一变更同步**：改变 step、config schema、artifact、tool、ErrorKind、manifest、状态字段、运行行为或公开能力时，代码、相关测试和责任文档必须在同一变更中同步；未同步不得标记完成。
2. **领域文件由领域负责人维护**：1 号维护量子设计，2 号维护拓扑设计，3 号维护模拟/后处理设计，4 号维护前端消费接口，6 号维护测试策略。5 号仅在分类、术语、索引和跨文档一致性上裁决。
3. **产品承诺受证据约束**：README、`Willy.md` 和评估报告只能陈述已由代码与对应测试或真实 smoke 支持的能力。未验收能力必须标注为计划或待验收。
4. **新增、删除和改名**：新增文档须登记本台账、索引和分类；改名须按 `reconstruction.md` 检查引用；删除文档前须处理索引、链接和需要保留的历史结论。
5. **状态更新**：每次修改主题文档时，责任人必须同时更新本台账的“最后核验”和状态。无法核验的事实标为 `待同步`，不得以猜测填补。
6. **发布门禁**：发布前由 0 号与 5 号检查本台账中不存在无责任人的 `待同步` 项，且 README、架构文档、设计文档、测试策略和缺口台账不对同一能力作出冲突陈述。

## 最小维护流程

1. 确定变更影响的文档台账条目和事实来源。
2. 先更新契约或设计说明，再完成实现、测试和用户可见说明。
3. 执行对应的最小验证命令，并将真实验收限制写入文档。
4. 更新台账状态和核验日期；新增文档再更新 `docs/README.md`。
