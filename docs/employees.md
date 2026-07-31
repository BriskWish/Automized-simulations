# Willy 开发团队

> 本文档定义团队职责边界。0 号为总工程师，负责全局技术治理与业务边界裁决；1-5 号为领域工程师，负责各自模块的设计、实现和维护。

## 0. Codex — 总工程师（Architecture / Delivery / Quality）

- **定位**: 0 号总工程师不是某一个执行模块的所有者，而是 Willy 项目的技术负责人。职责是把业务目标、系统架构、跨层契约、交付节奏和质量门禁统一起来，确保 1-5 号工程师的工作可以稳定合流。
- **核心业务**:
  - **业务边界裁决**: 判断项目当前承诺什么、不承诺什么。尤其负责同步 README、架构文档和真实主流程，避免“文档声称端到端完成，但代码只到建盒”的边界失真。
  - **路线图统筹**: 维护 `docs/revision_strategy.md`，确定 Phase 0-5 的修订顺序，优先补闭环、契约、安全和测试基线，再扩展新能力。
  - **跨层契约治理**: 统一 `StepResult`、`ErrorKind`、`config.json` schema、step registry、产物命名、状态机字段和 tool 返回格式。
  - **架构审查**: 审查跨层改动是否越界，防止 orchestrator 写业务逻辑、toolist 承载复杂算法、前端直接操作 shell、执行层反向调用 LLM。
  - **质量门禁**: 定义每类变更的最小验证命令，要求测试、LLM eval、环境检查和文档同步在交付前完成。
  - **安全治理**: 负责 API key、Gradio 暴露面、`shell=True`、`os.system`、进程组控制、运行产物清理和 vendor 依赖来源的风险收敛。
  - **文档治理**: 维护 `docs/README.md`、`project_evaluation.md`、`revision_strategy.md`、`project_gap_analysis.md` 与各设计文档之间的一致性。
- **管辖文档**:
  - `docs/revision_strategy.md` — 总计划、模块边界、跨层契约和验收规则。
  - `docs/project_evaluation.md` — 当前项目综合评估。
  - `docs/project_gap_analysis.md` — 缺口和修复优先级。
  - `docs/README.md` — 文档索引和矛盾清单入口。
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
| 量子层 | 1 号 | 产物契约、错误结构化、后端隔离、依赖预检命名 | G16/ORCA 具体计算细节 |
| 拓扑层 | 2 号 | `.mol2`/`.chg` 到 `.itp`/`.gro` 的边界、top assembly 目录一致性 | Sobtop/LigParGen 交互细节 |
| 模拟层 | 3 号 | 主流程是否真正接入 EM/NVT/NPT/PROD、状态机步骤一致性 | GROMACS 参数细节 |
| 前端交互 | 4 号 | 安全暴露面、run 管理、状态展示准确性 | Gradio 组件细节 |
| 文档架构 | 5 号 | 文档是否反映真实代码、索引是否完整、旧名是否清理 | 每份设计文档的具体技术细节 |

### 0 号当前业务重点

1. **Phase 0 基线收敛**: 修复 `tools_validate_config`、依赖预检命名、`top_assembly` 目录写死、脆弱测试和 Gradio 暴露面。
2. **Phase 1 契约稳定**: 建立 step registry、统一 `StepResult.to_dict()`、固化 `config.json` schema。
3. **Phase 2 端到端闭环**: 将 setup、EM、NVT/NPT、PROD 接入主编排器，并同步前端进度与 README。
4. **Phase 3+ 治理增强**: 安全进程管理、tool 权限元数据、CI、运行产物和 vendor 治理。

## 1. Claude — 量子层接口专家

- **职责**: 量子计算层（`src/willy/quantum/`）的接口设计、实现与维护
- **成果**（对应模块 7 个文件）:
  - `g16_struct_maker.py` — Gaussian 结构优化：.gjf 解析 / route card 重写 / g16 + formchk → .fchk
  - `g16_mol2_maker.py` — .fchk 解析（Atomic numbers / IBond / RBond）→ Tripos .mol2
  - `g16_chg_maker.py` — RESP 电荷：调用 RESP_noopt.sh（Gaussian SP + Multiwfn RESP）→ .chg
  - `orca_struct_maker.py` — .gjf→.inp 转换 + ORCA opt freq → .gbw → orca_2mkl → .molden
  - `orca_mol2_maker.py` — .molden → Multiwfn → .fchk → .mol2
  - `orca_chg_maker.py` — ORCA 链路的 RESP 电荷（复用 RESP_noopt.sh）
  - `resp_maker.py` — **纯 Python ORCA-native RESP**（零 Gaussian 依赖）：ORCA SP → Multiwfn 内置 ESP → RESP fitting，无硬编码绝对路径，通过 env var + PATH 解析工具位置
  - `quantum_design.md` — 设计文档
- **原则**: 一个 maker 只做一件事、下游只消费 `.mol2` + `.chg`、统一 `config.json` 配置源
- **事实勘误**:
  - ~~6 个 maker~~ → 现为 7 个（新增 `resp_maker.py`，ORCA-native RESP 无需 shell 脚本）
  - `resp_maker.py` 与 `orca_mol2_maker.py` 各有一份 `molden_to_fchk`（Multiwfn 100→2→7）实现，应抽取为共享工具函数

## 2. Claude — 拓扑层接口专家

- **职责**: `src/willy/topology/` 下所有力场接口的设计、实现与维护
- **成果**（对应模块 4 个文件）:
  - `sobtop_interface.py` — GAFF 力场：mol2 + chg → Sobtop 交互式输入 → .itp + .gro（兼容 rc=24 非致命错误）
  - `ligpargen_interface.py` — OPLS-AA 力场：SMILES 或 mol2 → LigParGen/BOSS → .itp + .gro（支持 LBCC 电荷模型）
  - `top_maker.py` — 主拓扑组装：收集各 itp 的 [atomtypes] → 去重 → 生成 `#include` 列表 + [system] + [molecules] → topol.top
  - `itp_reviser.py` — ITP 后处理：删除 [atomtypes] 段（已迁移到主 .top）+ RESNAME 替换为残基名
  - `topology_design.md` — 设计文档
- **原则**: 统一出参 `{"itp": Path, "gro": Path}`、下游不感知力场（GAFF vs OPLS-AA）、`env_checker` 统一管理依赖
- **事实勘误**:
  - 员工文档澄清了拓扑层完整 4 模块（top_maker 和 itp_reviser 之前只是隐式提及）

## 3. Claude — 模拟层接口专家

- **职责**: `src/willy/simulation/` 下 MD 模拟全链路设计、实现，以及 Layer 3 Simulation Agent 的工具与 prompt
- **成果**（对应 7 个模块 + tools + prompt + log parser）:
  - `mdp_maker.py` — `MdpConfig` 数据类 + em/eq/prod 三阶段 .mdp 批量生成，参数从 config.json `md` 段读取，支持 `overrides` 覆盖供 Agent 重试
  - `inp_generator.py` — `InpConfig`/`Component` 数据类 + `InpGenerator`，支持 inside_cube/inside_box/fixed 三种约束，`auto_from_config()` 与 top_maker 同源读取 residues
  - `md_setup.py` — 文件收集器：自动命名运行目录 `md_{residues}_{YYYYMMDD}{counter:04d}`，复制 topol.top/.itp/.mdp/model.pdb，计数器每日重置
  - `_md_utils.py` — grompp+mdrun 标准两步封装（conf 链自动推断: model.pdb→em.gro→eq.gro），xvg 解析 + `check_last_fraction()` 收敛检查器
  - `md_em.py` — EM 执行器，解析 log 检查 Fmax 收敛
  - `md_eq.py` — NPT 平衡执行器，gmx energy 提取密度(#22)+温度(#15)，后 20% 轨迹收敛检查
  - `md_prod.py` — 产出 MD 执行器
  - `toolist_simulation.py` — Layer 3 的 **7** 个 tool 定义与 handler（retry_mdp/box/em/eq/prod + diagnose + modify_config + skip_molecule）[注: skip_molecule handler 已实现但 tool 定义在 SIMULATION_TOOLS 列表中缺失，为 bug 需修复]
  - `SIMULATION_AGENT_PROMPT` (in `prompts.py`) — EM/EQ/PROD/通用 4 大类 14 条决策规则
  - `log_parsers.py` 中 `parse_gromacs_log()` — 7 种异常模式识别
- **原则**: 所有函数返回 `StepResult`、MdpConfig 为 MD 参数唯一真相源、EM→EQ→PROD conf 链自动推断、EQ 收敛有明确数值标准（rel_change < 0.10）、盒子与拓扑同源读取、诊断返回结构化 dict
- **事实勘误**:
  - ~~8 tools~~ → 实际为 7 个 tool 定义 + skip_molecule handler（tool 定义列表中缺失 skip_molecule，为 bug）
  - ~~TOOL_META 分类元数据~~ → 代码中不存在，为规划中功能

## 4. Claude — 前端与交互工程师

- **职责**: `app.py` Gradio UI 设计、3D 分子查看器、对话交互体验
- **成果**:
  - SCAN 风格 3D 查看器（浅色球棍模型 + 按电荷分类下拉）
  - 确认按钮流程（文字确认向后兼容："好的""跑吧""ok"等关键字）
  - LLM 工具调用进度实时可见（每轮 tool_call 返回后即时更新聊天框）
  - 状态机前端集成（`PipelineStateMachine.read()` → 进度面板六态渲染，3 秒轮询）
  - Gradio 6 兼容包装（`_chat_wrapper` 适配新版 API）
  - `frontend_api.py` — 前端专用后端 API 层（340 行），封装流水线控制、分子目录、3D 查看器渲染、进度面板，`app.py` 不再直接触碰路径/shell/文件系统
  - 中止流水线双按钮（清理产物 / 保留产物）
- **原则**: 进度即时可见不黑盒、操作一键可达不绕路、视觉参考 SCAN 平台保持专业感、`app.py` 只做 UI 布局不碰后端逻辑
- **事实勘误**:
  - `app.py` 中的 3D 查看器、进度面板、分子目录逻辑已提取到 `frontend_api.py`，`app.py` 只做布局和事件绑定

## 5. Claude — 项目顾问 & 文档架构师

- **职责**: 项目架构评审、文档体系规范化、命名规范制定、技术选型分析
- **成果**:
  - **命名规范制定**: `docs/naming_convention.md` — tools/toolist/agent 三套命名体系，覆盖全部 32 个 LLM tool 名称、4 个 toolist 模块文件、agent 文件命名规则
  - **现有文档规范化**: `knowledge.md`（分子注册说明、步骤数修正、box 配置补充）、`Willy.md`（幽灵引用删除、ORCA 后端补全、vendor 细化）、`quantum_design.md`/`topology_design.md`（章节编号统一、接口清单）
  - **新建文档**: `README.md`（中文门面 + 架构图）、`.env.example`、`docs/naming_convention.md`
  - **架构分析**: 识别架构演进路径 — 从平铺 14 文件到命名规范化（toolist/agent），区分"已完成"（tool 命名+toolist 文件）、"进行中"（agent 文件拆分）、"废弃"（agents/tools/pipeline/infra 空目录蓝图）
  - **技术分析**: TF-IDF 检索链路分析、三模型对比（DeepSeek V4 Pro vs Claude Opus vs GPT-5.5）、`reasoning_content` 字段风险评估
  - **环境清理**: 临时文件清理（FEC_run.gjf / gau.*）
- **原则**: 文档与代码同步——反映真实架构而非理想设计；命名规范先于重构——统一语言降低跨层沟通成本；分析有数据支撑——落实到项目具体代码路径

---

## 共识原则

> 以下原则由 0 号总工程师维护，1-5 号领域工程师共同遵守。新增代码必须遵守；若现实代码暂未满足，应在 `project_gap_analysis.md` 或 `revision_strategy.md` 中记录差距和修复优先级。

### 数据与返回值

1. **统一返回 `StepResult`** — 所有流水线步骤函数（maker、generator、executor）必须返回 `StepResult(success, error, outputs, artifacts, duration_s)`，禁止 `print` 报错后 `sys.exit()`、禁止裸 `raise` 跨模块传播
2. **`config.json` 唯一配置源** — `molecules` / `residues` / `md` / `defaults` / `box` 五段统一从此文件读取，禁止各模块自行解析命令行参数或环境变量作为配置入口

### Agent 架构

3. **LLM on Failure Only** — 正常流水线零 LLM 调用。仅步骤失败时激活对应层的 Agent（Quantum/Topology/Simulation）进行诊断→修复→重试
4. **层间 Tool 隔离** — 每层 Agent 只能调用本层的 tools，Quantum Agent 不能调 Sobtop 工具，Topology Agent 不能调 GROMACS 工具
5. **状态机主动报告** — 任何状态变更（步骤开始/完成/失败/重试/升级/中止）必须写入 `status.json`，前端不猜状态

### 命名与文件

6. **命名规范三件套** — 见 `docs/naming_convention.md`：
   - LLM tool 名: `tools_{操作}_{目标}_{scope/backend}`
   - Tool 模块: `toolist_{scope}.py`
   - Agent 文件: `agent_{scope}.py`
7. **文件归属 = 维护归属** — 一个 `.py` 文件只属于一位成员（一个领域），不出现同一文件被多人高频修改
8. **废弃代码立即删除** — 不保留空目录（如 `agents/` `tools/` `pipeline/` `infra/`）、不保留注释掉的旧实现、不保留计划中的占位文件

### 依赖与环境

9. **`env_checker` 为依赖唯一入口** — 所有外部工具（g16/ORCA/Multiwfn/formchk/Sobtop/GROMACS/Packmol）的可执行性检查统一委托给 `env_checker.check_module()`，不在各 maker 中重复实现 `which` / `PATH` 检查
10. **下游不感知上游后端** — `.mol2` + `.chg` 是量子层与拓扑层之间的唯一接口，拓扑层不感知 g16 还是 ORCA；`.itp` + `.gro` + `topol.top` 是拓扑层与模拟层之间的唯一接口

### 错误与诊断

11. **`ErrorKind` 枚举覆盖所有已知失败模式** — 新增错误类型必须先在 `errors.py` 的 `ErrorKind` 枚举中注册，再在各层 tools 中引用
12. **诊断返回结构化 dict** — 所有 `diagnose_*` 工具返回 dict（含 `error_patterns`、`anomalies`、`hint`、`evidence_lines`），不返回裸字符串，供 Agent 做精确的条件判断
