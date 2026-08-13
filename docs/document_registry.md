# Willy 文档台账与维护规范

> 本文件是项目文档用途、责任人和生命周期状态的唯一权威台账。`docs/README.md` 只负责导航；设计细节只保留在各主题文档中。最后核验：2026-08-13（初始质量密度默认值 0.7 g/cm3、统一 MD 默认时间步长 0.001 ps、显式 Packmol PBC、实际盒审计与残留输出隔离、私有 Packmol 执行证据、当前 run 隔离、统一 versioned run metadata、运行 provenance、RunStore、受管 GROMACS/量子/拓扑子进程生命周期、StepRegistry、执行模块依赖归属、完整配置外层 schema、external smoke 门禁、分段配置量子审计、服务端 DIAGNOSE/RECOVER 状态机、真实工具契约评分与脱敏 LLM 协议探针，以及当前真实恢复矩阵 16/18 的差异已同步）。

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
| [`../README.md`](../README.md) | 产品入口 | 0 号、4 号 | 当前，长期维护：明确“可识别分子”与“具备所选后端原始输入的分子”不同，G16/G09 `.gjf` / ORCA `.inp` 均在方案和启动前审计；四 profile 的十步结果仅作为历史批次记录，当前受控验收机已复核 G16+Sobtop 1/4；唯一支持形态为 GitHub 完整源码检出与可编辑安装，不发布 wheel/PyPI/独立安装包或可运行 staging。README 与根目录 `LICENSE.md` 已列出科学依赖的版本基线、上游许可状态，以及 Willy 原创代码免费使用但再分发须授权、且不向 `vendor/` 授权的边界；同时明确内置 Packmol/Multiwfn 仍须由宿主提供 `libgfortran.so.5`/`libXm.so.4`，并给出 Ubuntu/Debian 安装包。中性 OPLS 的单一 Ewald 净电荷 warning 使用 `0.15e` 受控容差；OPLS-AA 仍为隔离显式路径，离子/无氢组分需外部可验证参数。 | 2026-08-13 | 公开主流程、安装方式、依赖版本/许可、UI 入口或目录结构变化。 |
| [`../LICENSE.md`](../LICENSE.md) | 项目原创内容使用条款 | 开发者、0 号 | 当前：仅覆盖 Willy 作者拥有权利的原创代码、文档和资产；允许免费研究、教育、评估和内部使用，任何再分发须事先书面授权。明确排除 `vendor/`、外部程序和第三方文档，不以 Willy 的条款取代其上游许可。该自定义条款不是 OSI 标准开源许可证。 | 2026-08-13 | 项目作者的使用授权、再分发边界或第三方组件处理方式变化。 |
| [`README.md`](README.md) | 文档导航 | 5 号 | 当前，长期维护：导航已标明知识库元数据不替代 `struct/` 量子输入，已移除删除的锂盐参考资料，并将公开发行物边界指向根 README 与 G-00。 | 2026-08-13 | 新增、删除、重命名或重新分类任何文档。 |
| `document_registry.md` | 文档治理 | 5 号 | 当前，长期维护：已登记 run 隔离、schema-v2 `run_manifest.json` 的 section/CAS、运行 provenance、RunStore、受管进程生命周期、StepRegistry、完整配置外层 schema、external smoke 门禁、初始质量密度默认值 0.7 g/cm3、后端特异原始量子输入审计、OPLS 专属 GRO 残基字段恢复、历史 profile 与当前版本证据的区分、Ubuntu 20.04/glibc 2.31 Packmol 兼容构建，以及 G-00 发行物边界。 | 2026-08-13 | 文档状态、责任边界、事实来源或维护规则变化。 |
| [`Willy.md`](Willy.md) | 产品与架构 | 0 号、5 号 | 当前，长期维护：架构索引已反映 ORCA Molden→MOL2 键级适配和 LigParGen ITP 命名空间；Simulation Agent 新增只读 mdrun 知识条目检索；Config Agent 会审计后端原始输入，并支持待确认方案的上下文问答与增量修改；工具总数为 50。 | 2026-08-11 | 主流程、模块边界、工具清单、运行目录或产品承诺变化。 |
| [`employees.md`](employees.md) | 团队治理 | 0 号、5 号 | 当前，长期维护：0-7 号均为受限 AI 工程师，开发者是唯一真人，仅负责 AI 严格无法验证的真实环境、许可证与版本缺口；前端负责协议调整摘要和批准交互；文本中止/暂停词没有停止权限；ORCA 量子层使用用户 `.inp` 原始输入；7 号负责托管 LLM 网关与 provider 安全边界；工具总数为 50，四条最小 profile 有历史完整结果，当前版本重放由 G-03 跟踪。 | 2026-08-13 | 职责、领域文件、共同工程原则或文档所有权变化。 |
| [`knowledge.md`](knowledge.md) | 运行时知识库与诊断经验边界 | 5 号，0-3 号按领域供数 | 当前，长期维护：分子表提供识别元数据；实际可启动组分要求与选定后端匹配的 `struct/<name>.gjf`（G16/G09）或 `.inp`（ORCA），缓存中间产物不可替代原始输入审计。已清理 run 的历史诊断不再作为可复核运行证据。 | 2026-08-10 | 分子、别名、力场、基组、MD 默认值、工作流步骤或已验证诊断案例变化。 |
| [`knowledge_mdrun.md`](knowledge_mdrun.md) | GROMACS MD 诊断知识库 | 3 号、5 号 | 当前，长期维护：已建立运行时错误、`mdrun` 特性和 `.mdp` 参数审阅条目，以及数字+名称校验、2 次/失败和 3 条/次的受限检索契约。 | 2026-08-08 | 条目、来源、适用边界、检索工具、proposal 提示词或公开来源字段变化。 |
| [`quantum_design.md`](quantum_design.md) | 量子设计 | 1 号 | 当前，长期维护：ORCA 的 MOL2 不再错误复用 Gaussian FCHK 连通性字段，而是从 `*_opt.molden` 调用内置 Multiwfn 的连通性/Mayer 键级分析；FCHK 保留给 RESP。G16/G09 仍使用 FCHK 解析，原始输入审计、单原子 SP 特例和集中资源配置不变。G09 未获得授权进行可靠全链路验收，仅在方案助理欢迎气泡提示潜在运行问题。 | 2026-08-11 | 量子后端、步骤、产物、依赖、工具、错误、原始输入审计、算力配置或子进程生命周期契约变化。 |
| [`topology_design.md`](topology_design.md) | 拓扑设计 | 2 号 | 当前，长期维护：LigParGen/OPLS-AA 在 Step 5 会对 run 私有 assembly ITP 施加确定性 atomtype 命名空间，重写类型引用并将映射写入 topology manifest；源 ITP 不变，GAFF/UFF 路径不变。仅 `topo_opls.py` 会把 LigParGen 的临时 `-r` 前缀从最终 GRO 第 6--10 列恢复为配置残基名，EC/FEC/EMC GRO 均通过 GROMACS 2025 `editconf`；两条 OPLS profile 已在 `0.15e` 净电荷容差下完成完整 MD。 | 2026-08-11 | 后端、manifest、组装、产物校验、重试策略或子进程生命周期契约变化。 |
| [`simulation_design.md`](simulation_design.md) | 模拟设计 | 3 号 | 当前，长期维护：覆盖 MD 协议、EM/EQ/PROD 阶段许可、Packmol 审计、Ubuntu 20.04 兼容构建、回滚和运行恢复契约；EQ 默认 `tau_p=2 ps`，初始建盒密度为 `0.7 g/cm3`，grompp 对单一 Ewald 净电荷 warning 使用 `0.15e` 受控容差；GROMACS 仅接受显式 `WILLY_GMX_BIN`，不从 `PATH` 自动发现；四条最小 profile 有历史真实 10/10 记录，当前版本仅 G16+Sobtop 已在受控验收机复核，其他路线仍由 G-03 跟踪。后处理/分析代码仅作内部实验保留，不属于本版本公开可用范围。 | 2026-08-13 | MD 协议、后处理、算力参数、阶段 manifest、恢复、回滚、实时 ETA 或真实验收变化。 |
| [`environment_registry_design.md`](environment_registry_design.md) | 外部运行环境设计 | 0 号 | 当前，长期维护：核心 registry、执行模块依赖归属、模块预检、子进程注入，以及表单值驱动、脱敏且受限的 OpenAI-compatible LLM 连通性检查已实现。LLM 连接请求使用自动工具选择，仍严格验证指定工具调用；配置页依赖预检保持分组判定、外部工具自动发现和默认项补写，但前端只展示去重后的独立软件或模组及中文状态/来源，不显示完整链路结论或内部细节；运行期强制模块预检不变。已确定仅支持 GitHub 完整源码检出；文档记录项目条款不覆盖仓内/外部第三方授权，逐项区分已核实和待核实的版本、许可证与再分发证据，并登记 Packmol/Multiwfn 的宿主运行库前置条件。 | 2026-08-13 | 外部工具、vendor 清单、许可证/来源、LLM 或软件环境变量优先级、预检、子进程环境或脱敏运行报告变化。 |
| [`gateway.md`](gateway.md) | 托管 LLM 网关设计 | 0 号、7 号 | 后续版本参考：网关协议、额度与安全边界保留离线回归；本版本前端不提供托管模式、登记或服务检查，历史 `managed` 标记强制走本地 BYOK，手工构造的托管 client、网关/管理端启动器和托管客户端包构建器均被拒绝。网关不属于当前产品入口或发布验收。 | 2026-08-12 | 网关协议、设备认证、额度、LLM provider、部署、安全或测试边界变化。 |
| [`remote_execution_design.md`](remote_execution_design.md) | 远程 GROMACS 执行设计 | 0 号、3 号、4 号、6 号 | 后续版本参考：SSH/Slurm 远程执行不属于当前产品入口或发布验收，本文仅保留未来边界设计。 | 2026-08-12 | 未来重新纳入远程执行范围时，重新启动独立验收计划。 |
| [`status_api.md`](status_api.md) | 公共接口 | 0 号、4 号 | 当前，长期维护：新 run 使用统一 metadata，registry 是唯一公开 section，其他 section 私有；环境查询读取 provenance section 并对历史报告只读回退；pending-action 的知识来源/未验证提示字段已贯通状态机、RunRegistry、前端 API 和 UI；替代方案校验失败会返回脱敏明确原因并写入私有 `rejected_validation` 决策记录；Packmol 的 `box_executions[]` 保持私有，公共状态新增受限 `escalation` 摘要且不暴露路径、日志或命令；EQ-only 受控验收通过 `completion_scope=through_eq` 区分于完整流程完成。 | 2026-08-11 | `PipelineStatus`、事件、停止控制、公开与私有 manifest 边界、RunRegistry、ETA 快照或前端消费字段变化。 |
| [`revision_strategy.md`](revision_strategy.md) | 总体计划 | 0 号 | 执行中：Phase 1 契约、运行审计、进程生命周期、fixture 完整性和 CI 质量门禁已完成；Agent 恢复已收敛为服务端 DIAGNOSE/RECOVER 状态机，配置生成已分段审计，评分只接受实际工具契约。真实模型协议与 fake-tool 闭环仍为独立验收项；当前恢复矩阵为 16/18，`q_scf_001` 缺少恢复工具调用，`s_grompp_017` 有一次策略禁止调用。四条 G16/ORCA + Sobtop/LigParGen profile 已有历史完整十步成功证据；科学体系预测、后处理和 G09 全链路不属于本版本目标。 | 2026-08-13 | Phase 状态、环境/运行注册边界、优先级或完成定义变化。 |
| [`run_assistant_design.md`](run_assistant_design.md) | 运行计划 | 0 号、3 号、4 号 | 执行中：MD 知识库受限检索已完成并转入长期维护；运行目录冻结并以统一 metadata 登记 G16/G09 `.gjf` / ORCA `.inp` 原始输入及可复用中间产物；运行助理的环境查询读取 provenance section 并兼容历史报告；运行与方案助理复用版本化九段 Prompt 契约，运行助理逐轮绑定当前 `run_id`，不传递聊天历史、完整配置、日志尾部或产物清单；当前会话的欢迎语、状态快照、错误、待确认方案和终态升级建议均为独立气泡；独立、只读且受白名单文档检索约束的项目说明助理规划中。 | 2026-08-12 | run 数据契约、实时 ETA、性能、经验检索、权限、控制动作、Prompt 契约、项目说明助理或任一 Phase 验收变化。 |
| [`testing_strategy.md`](testing_strategy.md) | 验收计划 | 6 号 | 当前，长期维护：四条最小 profile 的只读终态、阶段许可、固定产物哈希和干净版本摘要证据契约已实现；受控验收机上的 G16 + Sobtop 已完成真实十步流程，三条剩余 profile 和标准化脱敏 JSON/JUnit 仍待人工完成。18 个本地 mock 恢复场景全部通过；2026-08-12 当前 BYOK 协议探针 3/3、配置 Agent 5/5 通过，真实恢复矩阵 16/18：`q_scf_001` 缺少恢复工具调用，`s_grompp_017` 有一次策略禁止调用。Packmol ABI 已有兼容构建和最小 smoke；浏览器运行仍待完成。本版本成熟方案以十步完成和最终无错误为准；G09、科学体系预测和后处理/分析已明确排除。 | 2026-08-13 | 测试基线、marker、ETA、smoke 证据、LLM eval、受管进程生命周期、外部运行时 ABI、非当前产品验收矩阵或真实 profile 结果变化。 |
| [`../tests/reports/test_case_catalog.md`](../tests/reports/test_case_catalog.md) | 测试用例台账 | 6 号 | 当前：由收集脚本从实际 pytest 用例生成；ORCA Molden→MOL2 键连接/Mayer 键级、OPLS ITP 命名空间、LigParGen GRO 五列残基字段、净电荷容差边界、结构化日志、Packmol ABI/终态升级回归、归档网关离线回归、配置页依赖预检、客户端部署包运行态隔离、四条 profile 的只读终态证据和用户可见 Prompt 的工程绑定/摘要边界已归入对应分类，重新生成并核验（907 条 pytest、18 条 LLM 场景，共 925 条记录）。 | 2026-08-13 | pytest 收集结果、LLM eval 场景、测试对象或测试方式变化。 |
| [`naming_convention.md`](naming_convention.md) | 命名规范 | 5 号 | 当前，长期维护：50 个 tool 的 11/10/6/14/9 分布已按实际定义复核，含 G09 镜像工具、后端原始输入审计和只读 `tools_lookup_mdrun_knowledge`；共享 `prompt_contract.py` 管理版本化九段上下文，领域 Prompt 仍归属各 `agent_{scope}.py`。 | 2026-08-12 | 新增 scope、tool 总数、toolist、agent、共享 Prompt 契约或文件命名规则变化。 |
| [`ERR_WARN_Build.md`](ERR_WARN_Build.md) | 输出规范 | 0 号、5 号 | 当前，长期维护：量子原始输入不完整和未经确认的电荷不平衡为配置阶段阻塞 Error，而非 Warning；模拟层对 LigParGen 累计舍入的单一 Ewald warning 另有 `0.15e` 受控容差，超阈值仍为 grompp 输入契约错误。 | 2026-08-11 | Error/Warning schema、前端展示或配置 Agent 输出变化。 |

> 2026-08-11 文档同步：Packmol ABI/启动预检、`runtime_unavailable` 归类、终态 `escalation` 公共摘要和实际修复次数语义已写入环境、模拟、状态 API、运行助理与测试策略文档；目标机复测仍需由开发者完成。
| [`project_gap_analysis.md`](project_gap_analysis.md) | 问题台账 | 0 号、5 号，领域负责人认领 | 当前，长期维护：以发布 `v0.4.0` 为基线，登记当前 LLM 协议和真实工具闭环缺口 G-05/G-09；已确定仅支持 GitHub 源码检出，G-00 关闭；T-03 已具备四个具名 profile 的只读脱敏证据契约，G16 + Sobtop 已在受控验收机真实完成十步，三条剩余路线及标准化归档仍开放。G-02 明确保留仓内 Sobtop、精简 Open Babel 和 3Dmol 的第三方授权证据缺口。本地 mock 和服务端契约已通过，真实 BYOK 报告按当前 16/18 口径留存。明确唯一真人开发者负责 AI 严格无法验证的真实环境矩阵；0-7 号均为受限 AI 协作角色。G09、科学体系预测、后处理/分析与离子 OPLS 均登记为本版本非目标。 | 2026-08-13 | 缺口修复、责任/严重度变化、外部依赖兼容性、稳定基线更新或发布前。 |

## 强制维护规范

以下规则适用于 0-7 号所有工程师。5 号负责台账与一致性审查，但不替代领域负责人维护自己的技术事实。

1. **同一变更同步**：改变 step、config schema、artifact、tool、ErrorKind、manifest、状态字段、运行行为或公开能力时，代码、相关测试和责任文档必须在同一变更中同步；未同步不得标记完成。
2. **领域文件由领域负责人维护**：1 号维护量子设计，2 号维护拓扑设计，3 号维护模拟/后处理设计，4 号维护前端消费接口，6 号维护测试策略，7 号维护网关设计与 provider 安全边界。5 号仅在分类、术语、索引和跨文档一致性上裁决。
3. **产品承诺受证据约束**：README、`Willy.md` 和评估报告只能陈述已由代码与对应测试或真实 smoke 支持的能力。未验收能力必须标注为计划或待验收。
4. **新增、删除和改名**：新增文档须登记本台账、索引和分类；改名须按 `revision_strategy.md` 的重构同步清单检查引用；删除文档前须处理索引、链接和需要保留的历史结论。
5. **状态更新**：每次修改主题文档时，责任人必须同时更新本台账的“最后核验”和状态。无法核验的事实标为 `待同步`，不得以猜测填补。
6. **发布门禁**：发布前由 0 号与 5 号检查本台账中不存在无责任人的 `待同步` 项，且 README、架构文档、设计文档、测试策略和缺口台账不对同一能力作出冲突陈述。

## 最小维护流程

1. 确定变更影响的文档台账条目和事实来源。
2. 先更新契约或设计说明，再完成实现、测试和用户可见说明。
3. 执行对应的最小验证命令，并将真实验收限制写入文档。
4. 更新台账状态和核验日期；新增文档再更新 `docs/README.md`。
