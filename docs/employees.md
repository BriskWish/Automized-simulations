# Willy 开发团队

> 本文档定义 AI 工程师团队的职责边界。0-7 号全部是受限 AI 工程师，负责各自模块的设计、实现、测试和文档维护；他们不是拥有无限权限的自主代理。项目只引入一个真人角色“开发者”，用于执行 AI 严格无法替代的真实环境操作和版本缺口验收。

## 真人角色：开发者（唯一真人）

- **定位**：开发者是项目的唯一真人协作者，不属于 0-7 号 AI 工程师编号体系，也不在运行时作为 Agent 被调用。
- **进入条件**：只有在缺口明确标注“需开发者人工验证”且自动化证据无法覆盖时，才需要开发者介入。
- **职责**：提供或确认本机软件、许可证、GPU/网络、真实 LLM 端点和受控凭据；执行真实外部工具和跨设备部署；观察前端交互和进程行为；对版本缺口作出人工通过/不通过结论。
- **权限**：可以在明确授权范围内运行外部软件、输入私有配置、批准测试命令、停止自己启动的受管进程以及签署发布验收；不得把私钥、API Key、管理员密钥或原始运行数据提交到仓库或交给不需要它的 AI 工具。
- **边界**：开发者不绕过状态机直接修改 run 状态，不以主观观察替代产物/manifest 契约，不把一次人工成功扩大为未测试 profile 的普遍支持。
- **证据**：人工验收必须记录版本/commit、日期、环境摘要、操作结果和脱敏证据路径；不得记录密钥、Authorization、完整 prompt、原始日志或私有运行目录。

## AI 工程师共同权限边界

- 0-7 号只能使用各自层声明的工具、文件范围和动作契约；跨层操作必须通过编排器和已登记接口。
- AI 可以读取和修改工作区内获授权的源代码、测试和文档，但不能自行获取、猜测或输出开发者的私有凭据。
- AI 不能把工具退出码、mock 结果或预检成功表述为真实软件/真实部署已验收；需要真人介入的缺口必须升级给开发者。
- AI 不能自行扩大监听地址、访问未批准的远程主机、执行任意 shell、批准网关设备或宣称版本发布；这些动作必须由确定性策略和开发者授权共同约束。
- 6 号测试工程师、5 号文档架构师和 7 号网关工程师均为 AI 角色；他们可以生成检查清单、测试报告和建议，但不能替代开发者的人工签署。

## 0. Codex — 总工程师（Architecture / Delivery / Quality）

- **定位**: 0 号总工程师不是某一个执行模块的所有者，而是 Willy 项目的技术负责人。职责是把业务目标、系统架构、跨层契约、交付节奏和质量门禁统一起来，确保 1-7 号工程师的工作可以稳定合流。
- **核心业务**:
  - **业务边界裁决**: 判断项目当前承诺什么、不承诺什么。尤其负责同步 README、架构文档和真实主流程，避免“文档声称端到端完成，但代码只到建盒”的边界失真。
  - **路线图统筹**: 维护 `docs/revision_strategy.md`，确定 Phase 0-5 的修订顺序，优先补闭环、契约、安全和测试基线，再扩展新能力。
  - **跨层契约治理**: 统一 `StepResult`、`ErrorKind`、`config.json` schema、step registry、产物命名、状态机字段和 tool 返回格式。
  - **架构审查**: 审查跨层改动是否越界，防止 orchestrator 写业务逻辑、toolist 承载复杂算法、前端直接操作 shell、执行层反向调用 LLM。
  - **质量门禁**: 定义每类变更的最小验证命令，要求测试、LLM eval、环境检查和文档同步在交付前完成。
  - **安全治理**: 负责 API key、Gradio 暴露面、参数化子进程调用、进程组控制、运行产物清理和 vendor 依赖来源的风险收敛。
  - **文档治理**: 维护 `docs/README.md`、`revision_strategy.md`、`project_gap_analysis.md` 与各设计文档之间的一致性。
- **管辖文档**:
  - `docs/revision_strategy.md` — 总计划、模块边界、跨层契约和验收规则。
  - `docs/project_gap_analysis.md` — 缺口和修复优先级。
  - `docs/README.md` — 文档导航与分类入口。
  - `docs/document_registry.md` — 文档用途、状态、责任人与维护规范的权威台账。
  - `docs/employees.md` — 团队职责边界。
- **交付原则**:
  - 先收敛边界，再扩展功能。
  - 正常路径保持零 LLM 开销，LLM 只在失败诊断和修复中介入。
  - 所有公共 pipeline 步骤必须返回结构化 `StepResult`。
  - 每次改 step/config/artifact/tool/error，都必须同步 tests、prompt、docs 和状态展示。
  - 未经验证的产品能力不得写入 README 的主路径承诺。
- **决策权**:
  - 有权冻结跨层重构，要求先补测试或文档。
  - 有权要求领域工程师把越界逻辑退回正确层。
  - 有权把“新增功能”降级为 P2/P3，优先处理 P0/P1 的闭环、安全和稳定性问题。
  - 有权定义 release gate：测试未全绿、主线依赖未说明、文档与代码矛盾未记录时不得视为完成。

### 0 号总工程师与各领域工程师的接口

| 领域 | 负责人 | 0 号关注点 | 不直接接管 |
|------|------|------|------|
| 量子层 | 1 号 | 产物契约、错误结构化、后端隔离、依赖预检命名 | G16/G09/ORCA 具体计算细节 |
| 拓扑层 | 2 号 | `.mol2`/`.chg` 到 `.itp`/`.gro` 的边界、top assembly 目录一致性 | Sobtop/LigParGen 交互细节 |
| 模拟层 | 3 号 | v2 协议、EM/EQ/PROD 阶段许可、状态机步骤一致性 | GROMACS 参数细节 |
| 前端交互 | 4 号 | 安全暴露面、run 管理、状态展示准确性 | Gradio 组件细节 |
| 文档架构 | 5 号 | 文档是否反映真实代码、索引是否完整、旧名是否清理 | 每份设计文档的具体技术细节 |
| 测试与验收 | 6 号 | 测试策略、回归基线、真实案例验收与发布质量门禁 | 领域算法与业务协议实现 |
| LLM 网关 | 7 号 | 托管网关协议、设备认证、额度/审计、provider 隔离与部署安全 | 上游供应商运营、Willy 本地工作流执行 |

### 0 号当前业务重点

1. **Phase 0 基线收敛**: 已完成配置验证、依赖预检命名、`top_assembly` 目录、脆弱测试和 Gradio 默认暴露面的修复。
2. **Phase 1 契约稳定**: `StepResult.to_dict()`、完整配置外层 schema、唯一步骤/执行模块注册、run 事务与 provenance 已落地；公共接口中仍禁止以裸异常替代 `StepResult`。
3. **Phase 2 端到端闭环**: 10 步主流程及 EM/NPT EQ/PROD 已接入；2026-08-11 的四条真实量子/拓扑 profile（G16/ORCA + Sobtop/LigParGen）均已完成 10/10，中性 OPLS 的单一 Ewald 净电荷 warning 按 `0.15e` 受控容差放行并已回归。本版本以十步完成且最终无错误为验收标准，不进行目标体系科学预测。
4. **Phase 3+ 治理增强**: 安全进程管理、tool 权限元数据、CI、运行产物和 vendor 治理持续推进。

## 1. Claude — 量子层接口专家

- **职责**: 量子计算层（`src/willy/quantum/`）的接口设计、实现与维护
- **成果**（对应量子模块 10 个文件）:
  - `struct_g16.py` — Gaussian 结构优化：.gjf 解析 / route card 重写 / g16 + formchk → .fchk
  - `struct_g09.py` — G09 独立镜像链路：.gjf 解析 / route card 重写 / g09 + G09 formchk → .fchk
  - `struct_orca.py` — 以用户 `.inp` 原始输入执行 ORCA opt freq → orca_2mkl → .molden
  - `singlepoint_g16.py` — 从优化后的 .fchk 提取坐标，执行 def2-TZVP 单点能 → `*_opt.fchk`
  - `singlepoint_g09.py` — G09 独立镜像：从优化后的 .fchk 提取坐标，执行 def2-TZVP 单点能 → `*_opt.fchk`
  - `singlepoint_orca.py` — 从 .molden 提取坐标，执行 def2-TZVP 单点能并转换 → `*_opt.fchk`
  - `fchk_mol2.py` — 统一解析 `*_opt.fchk`（Atomic numbers / IBond / RBond）→ Tripos .mol2，无外部依赖
  - `chg_resp.py` — 统一的 Multiwfn RESP：`*_opt.fchk` → .chg，G16/G09 与 ORCA 共用
  - `_orca_utils.py` — ORCA / Multiwfn 路径解析、运行环境、坐标提取和格式化共享工具
  - `quantum_design.md` — 设计文档
- **原则**: 一个 maker 只做一件事、下游只消费 `.mol2` + `.chg`、统一 `config.json` 配置源
- **事实勘误**:
  - 量子层现采用两步法：结构优化后显式运行单点能，再以统一的 `*_opt.fchk` 生成 `.mol2` 和 `.chg`。
  - 已移除的 shell RESP 辅助脚本、旧的 `g16_*` / `orca_*` maker 和 `resp_maker.py` 均不在当前量子层接口中；路径解析与坐标提取由 `_orca_utils.py` 统一处理。

## 2. Claude — 拓扑层接口专家

- **职责**: `src/willy/topology/` 下所有力场接口的设计、实现与维护
- **成果**（执行模块与交接辅助组件）:
  - `topo_gaff.py` — Sobtop GAFF/UFF 力场：mol2 + chg → .itp + .gro（兼容经过产物校验的 rc=24）
  - `topo_opls.py` — LigParGen/BOSS OPLS-AA 力场：SMILES 或 mol2 → .itp + .gro
  - `top_assembly.py` — 基于 `topology_manifest.json` 组装 atomtype、`#include`、`[system]`、`[molecules]` 和 `topol.top`
  - `itp_revise.py` — 为主拓扑消费副本移除 `[atomtypes]` 并修订残基名
  - `topology_design.md` — 设计文档
- **原则**: 公共执行接口返回 `StepResult`；下游只消费经过 manifest 校验的产物，不感知 GAFF/OPLS-AA 后端；`env_checker` 统一管理依赖

## 3. Claude — 模拟层接口专家

- **职责**: `src/willy/simulation/` 下 MD 模拟全链路设计、实现，以及 Layer 3 Simulation Agent 的工具与 prompt
- **成果**（执行模块 + tools + prompt + log parser）:
  - `protocol.py` — `md.schema_version: 2` 校验、六段 EQ 退火与旧字段显式迁移
  - `manifest.py` — 阶段验收许可、输入/MDP 指纹、checkpoint 恢复、运行锁与安全停止请求
  - `mdp.py` — `MdpConfig` 数据类 + em/eq/prod 三阶段 .mdp 批量生成；固定 `.xtc/.edr` 产物，可选 `.trr` 由 `md.outputs.trr` 控制
  - `box.py` — `InpConfig`/`Component` 数据类 + `InpGenerator`，支持 inside_cube/inside_box/fixed 三种约束，`auto_from_config()` 与拓扑层同源读取 residues
  - `_gmx_utils.py` — 输入契约校验 + grompp/mdrun 标准两步封装（model.pdb→em.gro→eq.gro），并强制 `.tpr/.gro/.xtc/.edr` 产物
  - `em.py` — EM 执行器，解析 log 检查 Fmax 收敛；失败可回滚 Packmol 建盒
  - `eq.py` — 三点式退火平衡执行器，检验最终目标温度保持段和最终 `.gro` 宏观真空区
  - `prod.py` — 生产 MD 执行器；必须由已验收 `eq.cpt` 连续启动，`prod.tpr` 前允许独立重写 prod.mdp
  - `postprocess.py` / `mdrun_eta.py` / `pending_action.py` — 生产后确定性分析、运行心跳/ETA 和 run-local EQ 方案确认
  - `toolist_simulation.py` — Layer 3 的 **14** 个 JSON Schema tool 定义与 handler，含显式旧配置迁移、输出配置、PROD MDP 配置和只读 GROMACS 知识检索
  - `SIMULATION_AGENT_PROMPT` (in `agent_simulation.py`) — EM/EQ/PROD/通用决策规则
  - `log_parsers.py` 中 `parse_gromacs_log()` — 7 种异常模式识别
- **原则**: 所有函数返回 `StepResult`、v2 配置和 run 内 manifest 为协议真相源、EM→EQ→PROD 只能消费已验收父阶段、盒子与拓扑同源读取、诊断返回结构化 dict
- **事实勘误**:
  - Simulation Agent 对外公开 14 个 JSON Schema；时长、温度、压力耦合和输出精度变更需要模拟前确认，GROMACS 知识检索保持只读并受两次/三条预算限制。

## 4. Codex — 前端与交互工程师

- **职责**: `app.py` Gradio UI 设计、3D 分子查看器、对话交互体验
- **成果**:
  - SCAN 风格 3D 查看器（浅色球棍模型 + 按电荷分类下拉）
  - 方案文本确认流程（仅紧随待确认方案的明确启动回复可运行；支持“运行”“开始运行”“确认运行”等词汇）
  - LLM 工具调用进度实时可见（每轮 tool_call 返回后即时更新聊天框）
  - 状态机前端集成（运行助理工程状态 8 态渲染，3 秒轮询）
  - Gradio 6 兼容包装（`_chat_wrapper` 适配新版 API）
  - `frontend_api.py` — 前端专用后端 API 层，封装流水线控制、分子目录、3D 查看器渲染、运行助理工程状态，`app.py` 不再直接触碰路径/shell/文件系统
  - 中止流水线服务端二次确认：仅按钮首击进入待确认，二次点击收到停止请求回执后才锁定；GROMACS 阶段请求安全停止，文本中止/暂停词不具备停止权限
  - 协议调整确认展示：消费公开的脱敏 `pending_action`，方案同时列出允许复审的参数；用户提出明确修正后由 LLM 生成替代方案并刷新 `action_id`，精确批准文本才请求服务端确认重跑；无回复、暂停或否决保持等待
- **原则**: 进度即时可见不黑盒、操作一键可达不绕路、视觉参考 SCAN 平台保持专业感、`app.py` 只做 UI 布局不碰后端逻辑
- **事实勘误**:
  - `app.py` 中的 3D 查看器、运行助理工程状态、分子目录逻辑已提取到 `frontend_api.py`，`app.py` 只做布局和事件绑定

## 5. Codex — 项目顾问 & 文档架构师

- **职责**: 项目架构评审、文档体系规范化、命名规范制定、技术选型分析
- **成果**:
  - **命名规范制定**: `docs/naming_convention.md` — tools/toolist/agent 三套命名体系，覆盖 50 个 LLM tool（11/10/6/14/9，含 9 个只读 run tool）、5 个 toolist 模块和 5 个 agent 文件
  - **现有文档规范化**: `knowledge.md`（分子注册说明、步骤数修正、box 配置补充）、`Willy.md`（幽灵引用删除、ORCA 后端补全、vendor 细化）、`quantum_design.md`/`topology_design.md`（章节编号统一、接口清单）
  - **新建文档**: `README.md`（中文门面 + 架构图）、`.env.example`、`docs/naming_convention.md`
  - **文档台账与治理**: `docs/document_registry.md` 记录全部文档的用途、责任人、状态与更新触发条件；所有领域工程师在同一变更中维护所负责的技术事实
  - **架构分析**: 识别架构演进路径 — 从平铺模块到命名规范化（toolist/agent）；工具模块和 agent 文件拆分均已完成
  - **技术分析**: TF-IDF 检索链路分析、三模型对比（DeepSeek V4 Pro vs Claude Opus vs GPT-5.5）、`reasoning_content` 字段风险评估
  - **环境清理**: 临时文件清理（FEC_run.gjf / gau.*）
- **原则**: 文档与代码同步——反映真实架构而非理想设计；命名规范先于重构——统一语言降低跨层沟通成本；分析有数据支撑——落实到项目具体代码路径

---

## 6. Codex — 测试工程师（Test / Verification / Release Gate）

- **职责**: 统筹全项目的测试、验收与质量门禁，维护跨层回归基线，确保变更可验证、问题可复现、交付可追溯。
- **范围**:
  - **测试策略**: 维护单元、跨层契约、编排器集成、前端/API、LLM 评估及真实外部工具冒烟测试的分层计划。
  - **回归统筹**: 维护全量 `pytest -q` 回归基线；为每类变更定义最小测试集，防止替身测试掩盖真实命令、文件或产物问题。
  - **真实案例验收**: 维护匿名、可重放的最小真实案例，覆盖量子、拓扑、EM、EQ、PROD 与后处理；记录依赖版本、输入指纹和验收结果。
  - **契约验证**: 覆盖配置迁移、`StepResult`/`ErrorKind`、manifest/status/events、产物完整性、检查点与重启、PBC 和分析结果等跨层约定。
  - **测试数据治理**: 管理 fixture、黄金结果、测试日志和生命周期；默认测试不得意外调用高成本外部计算。
  - **质量门禁**: 验收前确认相关测试通过、关键回归已覆盖，且涉及真实工具的变更完成相应冒烟验证。
- **所有权**: 负责 `tests/`、测试 fixture、测试脚本与验收基线；不替代领域工程师实现业务逻辑，发现问题后以可复现测试和验收结论反馈给对应负责人。
- **原则**: 测试描述已观察到的契约；替身测试与真实冒烟测试并行；测试、实现和文档必须同步更新。

## 7. Codex — LLM 网关工程师（Gateway / Provider Security）

- **职责**: 独立托管 LLM Gateway 及 Willy 客户端 `managed`/`byok` provider 边界的设计、实现与维护。
- **范围**:
  - Gateway HTTP/ASGI 服务、固定模型别名和 OpenAI-compatible 非流式转发；网关不执行 tool、不访问 run 目录、不启动或停止 GROMACS。
  - 设备 Ed25519 注册、短期令牌、签名/nonce 防重放、撤销与密钥轮换；管理员身份与设备身份分离。
  - 用量预留/结算、额度/并发/速率限制、脱敏审计、错误与数据保留策略。
  - Willy provider 解析、托管模式配置只读边界、BYOK 本地直连隔离，以及方案助理和 pipeline 子进程的一致配置来源。
  - fake upstream、隔离密钥、认证/额度/脱敏和真实 tool-calling smoke 的网关发布门禁；公网部署必须有 TLS、秘密管理、备份、告警和撤销演练。
- **管辖文档**:
  - `docs/gateway.md` — 网关协议、威胁模型、阶段计划和验收门禁。
  - `docs/environment_registry_design.md` — 仅在 LLM provider 环境解析边界交叉时协同维护，不能绕过环境注册唯一来源。
  - `docs/status_api.md` — 仅在托管模式公开状态、用量和 provenance 字段交叉时协同维护。
- **接口边界**:
  - 0 号负责产品承诺、跨层裁决和发布批准；7 号负责网关技术事实，不把未验收托管服务写入 README 主路径。
  - 4 号负责前端布局与交互，7 号提供脱敏 provider/status API；前端不能直接读取令牌或修改托管策略。
  - 6 号负责测试台账与质量门禁，7 号负责网关契约和 fake upstream 测试实现；真实外部凭据只在显式 opt-in smoke 使用。
- **交付原则**: 先固化协议、密钥生命周期、数据保留、错误码和双模式 schema，再实现核心转发、设备/额度和客户端接入；任何阶段不得退化为任意代理、共享万能 Key 或远程工作流控制面。

---

## 共识原则

> 以下原则由 0 号总工程师维护，1-7 号领域工程师共同遵守。新增代码必须遵守；若现实代码暂未满足，应在 `project_gap_analysis.md` 或 `revision_strategy.md` 中记录差距和修复优先级。

### 数据与返回值

1. **统一返回 `StepResult`** — 所有流水线步骤函数（maker、generator、executor）必须返回 `StepResult(success, error, outputs, artifacts, duration_s)`，禁止 `print` 报错后 `sys.exit()`、禁止裸 `raise` 跨模块传播
2. **`config.json` 唯一配置源** — `molecules` / `residues` / `md` / `defaults` / `box` 五段统一从此文件读取，禁止各模块自行解析命令行参数或环境变量作为配置入口

### Agent 架构

3. **LLM on Failure Only** — 正常流水线零 LLM 调用。仅步骤失败时激活对应层的 Agent（Quantum/Topology/Simulation）进行诊断→修复→重试
4. **层间 Tool 隔离** — 每层 Agent 只能调用本层的 tools，Quantum Agent 不能调 Sobtop 工具，Topology Agent 不能调 GROMACS 工具
5. **状态机主动报告** — 任何状态变更（步骤开始/完成/失败/重试/升级/中止）必须写入所属 `md_run/<run_id>/status.json`，前端不猜状态

### 命名与文件

6. **命名规范三件套** — 见 `docs/naming_convention.md`：
   - LLM tool 名: `tools_{操作}_{目标}_{scope/backend}`
   - Tool 模块: `toolist_{scope}.py`
   - Agent 文件: `agent_{scope}.py`
7. **文件归属 = 维护归属** — 一个 `.py` 文件只属于一位成员（一个领域），不出现同一文件被多人高频修改
8. **废弃代码立即删除** — 不保留空目录（如 `agents/` `tools/` `pipeline/` `infra/`）、不保留注释掉的旧实现、不保留计划中的占位文件

### 依赖与环境

9. **环境注册唯一来源** — `env_registry.py` 统一声明并解析外部工具及子进程环境；`env_checker.py` 仅保留 `check_module()` 等兼容预检入口。maker 不得自行重复 `which` / `PATH` 或环境变量解析。
10. **下游不感知上游后端** — `.mol2` + `.chg` 是量子层与拓扑层之间的唯一接口，拓扑层不感知 g16、g09 还是 ORCA；`.itp` + `.gro` + `topol.top` 是拓扑层与模拟层之间的唯一接口

### 错误与诊断

11. **`ErrorKind` 枚举覆盖所有已知失败模式** — 新增错误类型必须先在 `errors.py` 的 `ErrorKind` 枚举中注册，再在各层 tools 中引用
12. **诊断返回结构化 dict** — 所有 `diagnose_*` 工具返回 dict（含 `error_patterns`、`anomalies`、`hint`、`evidence_lines`），不返回裸字符串，供 Agent 做精确的条件判断

### 文档治理

13. **同一变更同步文档** — 任何工程师修改自己领域的 step、config、artifact、tool、ErrorKind、公开接口、运行行为或产品承诺时，必须在同一变更中同步责任文档、测试和 [`document_registry.md`](document_registry.md)；5 号负责一致性审查，不替代领域负责人维护技术事实。
