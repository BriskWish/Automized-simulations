# Willy 项目缺口与风险台账

> 最后核验：2026-08-15，基于发布 `v0.5.0`、Python 3.10 发布门禁修复及当前验收口径。本文只记录当前可执行缺口和已分配任务；历史已解决事项列在文末，文档状态和责任人以 [`document_registry.md`](document_registry.md) 为准。

## 当前稳定基线

Willy 当前是可运行的研究型内部 MD 自动化 MVP，不是生产级科学计算平台。本轮 LLM 契约修改后，
完整离线回归为 `920 passed, 10 skipped`（收集 930 条）；离线 mock 恢复评测为 `18/18`、均分
`98.1/100`，`q_scf_001` 已满足工具契约。测试台账为
`950 pytest + 18 LLM = 968`。历史批次中四条 G16/ORCA + Sobtop/LigParGen 路线均完成 10/10；
`md__202608110001`、`005`、`010`、`011` 的终态、阶段许可和固定产物哈希已归档至
`tests/reports/baselines/external_profiles_20260814/`，归档不复制 `.xtc`、`.trr` 或原始日志。
每条记录保留 source commit 和 dirty 标记，四条 profile 的完成记录来自当前开发机；历史完成 run 与当前确定性回归共同组成验收并集；
新版本默认继承此并集，不要求为每条 profile 重跑。

本版本成熟方案的边界是：受支持 profile 从 Step 1 严格完成 Step 10，受管进程正常退出，最终状态为 `done`，阶段产物和 manifest/status 通过校验，且无未处理错误。该结论是流程完整性结论，不是科学体系预测。科学预测、后处理/分析、G09 可靠全链路和离子 OPLS 仍不属于本版本可用范围。

### 2026-08-14 目标机验收口径

新 Ubuntu 22.04 WSL 已安装 ORCA 与 GROMACS，并已完成内置 Packmol 周期盒和裸 GROMACS CUDA 最小运行。该环境现在可执行：

1. 当前检出的干净 Python 安装与默认回归；
2. 显式 `WILLY_GMX_BIN` 下的 Willy 受管 GROMACS smoke；
3. `orca_sobtop_electrolyte` 真实十步 profile。

该环境没有 G16、LigParGen/BOSS，因此只需以 ORCA + Sobtop 作为目标机的单条完整链路；GROMACS 与其他外部二进制一样，清除显式 `WILLY_GMX_BIN` 后仍应从已初始化的 `PATH` 解析；裸 `gmx` CUDA 成功只作为硬件证据，不能替代 Willy 受管调用。ORCA profile 启动前必须确认 `orca` 和同安装目录的 `orca_2mkl` 均由 registry 解析为可用。

### 2026-08-14 Ubuntu 22.04.5 WSL2 目标机回执

以下结果由开发者在全新隔离检出（提交 `6045c7b6be80`）提供；本检出未携带 `.env`、`md_run/`、`.venv` 或继承的 `WILLY_*`，随后仅在项目 `.env` 配置 GROMACS。依赖为 Python 3.10.12、GROMACS 2025.1、ORCA 6.1.1、`orca_2mkl`、Packmol 21.2.3、可执行 Sobtop、Playwright 1.62.0/Chromium 151，`pip check` 通过。

| 缺口 | 本次事实 | 结论 |
|------|------|------|
| G-01 / T-01 | 安装、`pip check`、`compileall`、显式 GROMACS/ORCA 解析通过；完整 pytest 为 `915 passed, 10 skipped, 1 failed`。唯一失败为受管停止审计测试仅期望 SIGINT，实际还记录 SIGTERM；受管外部链路未完成。 | 保持开放 |
| G-03 / T-03 | `orca_sobtop_electrolyte` 的 ORCA、`orca_2mkl`、Sobtop、Packmol、GROMACS 和原始输入审计通过；真实链路在 Step 2 量子 RESP 阶段因 Li 电荷计算失败而 `aborted`，没有 `done`、阶段许可或固定产物哈希。 | 保持开放 |
| G-06 / T-05 | fake-executor 浏览器用例 `1/1`、运行助理及 UI 契约 `89/89` 通过，公开 EQ 错误和“不自动修改”边界可见；项目切换会重置聊天历史，刷新后无按工程恢复证据，也未有独立项目设计问答验收。 | 保持开放 |
| G-07 / T-07 | 指定 Ubuntu 22.04.5 WSL2 上六类注入场景、真实 Packmol 启动、ABI/依赖缺失分类和 LLM 配置边界均通过，公开/私有脱敏与状态契约符合预期。 | 仅对该目标环境关闭 |

目标机回执列出的脱敏证据文件名为：`g01-pytest.xml`、`g06-browser-existing.xml`、`g06-contracts.xml`、`g07-local-wsl-acceptance.json`、`g03-tool-preflight.json`、`g03-input-audit.json`、`g03-execution-summary.json`。这些文件未随当前检出自动导入，故本台账只登记回执事实，不把文件存在误写成本地复核结果。

### 2026-08-15 完整运行记录补充

开发者补充确认了两类真实运行证据：

1. 远程 Ubuntu 20.04.6 验收机已完成一条受支持 profile 的 Step 1--10，最终状态为 `done`；该环境的完整原始记录由开发者保留，当前检出未导入私有运行目录。
2. 当前开发机已完整跑通四条 profile：`md__202608110001`（G16 + Sobtop）、`md__202608110005`（ORCA + Sobtop）、`md__202608110010`（G16 + LigParGen/BOSS）和 `md__202608110011`（ORCA + LigParGen/BOSS）。四条记录的脱敏 JSON/JUnit 位于 `tests/reports/baselines/external_profiles_20260814/`，共同 source commit 为 `2e0f9f235f4d9af61d8f81193f41ef55c8cf428c`。

本机版本快照为：Ubuntu 24.04.4 LTS/WSL2、Python 3.12.3、GROMACS 2025.0、Packmol 21.2.3、内置 Multiwfn `3.8(dev)-2025-02-14`；registry 基线为 ORCA 6.1.1、Open Babel 3.1.1。Gaussian 16、LigParGen、BOSS 和 Sobtop 的精确 revision 未在运行 manifest 中固化，不能据此补写具体版本。

因此，Ubuntu 20.04 已有一次完整 profile 记录；Ubuntu 22.04.5 目前仍是目标机真实 profile 在 Step 2 中止，Ubuntu 24.04 目前仍只有错误矩阵和 Packmol 启动探针，不能合并表述为三版均已完成完整十步验收。

## 当前任务配置

以下任务用于 v0.5.0 稳定版的收口和下一版准备；“非阻塞”任务不改变当前十步成熟方案声明。

| 任务 | 负责人 | 当前动作 | 完成证据 | 优先级 |
|------|------|------|------|------|
| T-01 发布环境复现 | 开发者、6 号 | Ubuntu 22.04.5 目标机已完成安装、依赖检查、编译和显式工具解析；默认回归仍有 1 个停止审计失败，受管 smoke 未完成 | 修正停止审计契约后重跑全绿，并补受管 smoke；保留 `g01-pytest.xml` 等脱敏结果 | P1 |
| T-03 单链路外部验收 | 开发者、6 号 | 目标机已启动 `orca_sobtop_electrolyte`，但 Step 2 Li RESP 失败并中止，未形成完成态证据 | 定位并修复/确认 Li RESP 前置条件后，从新 run 完成 Step 1--10，状态 `done`、阶段许可和固定产物哈希通过 | P1 |
| T-04 网关部署安全（后续版本资产） | 7 号、0 号 | 保留网关协议和部署安全检查；本版本不开放入口、不做远程部署验收 | 后续版本独立部署检查清单和失败边界；不得写入当前发布主路径 | 后续 |
| T-05 运行助理扩展 | 3 号、4 号、0 号 | 方案助理已提供只读项目说明问答；运行助理已在状态、ETA、EQ 确认和停止基础上提供显式 `/resume`、`/fork`，仍需补通用错误指导和历史保留 | 每项剩余能力有独立状态契约、mock eval 和用户可见结果 | P2 |
| T-07 公开错误与日志治理 | 0 号、3 号、4 号 | Ubuntu 22.04.5 WSL2 目标机的输入、依赖缺失、Packmol ABI、grompp、EQ、PROD 与 LLM 配置错误矩阵均通过脱敏/关联检查 | 目标范围限定为 Ubuntu 22.04.5 WSL2 时关闭；其他 Ubuntu/ABI 仍需独立复测 | P2（条件关闭） |

## 当前验收缺口

以下是稳定版本仍未完全关闭的事项；四条历史 profile 证据已归档，不再要求目标机逐条重放。

### P0/P1：发布和部署收口

| 编号 | 缺口 | 事实依据 | 关闭条件 |
|------|------|------|------|
| G-01 / T-01 | 目标机安装、依赖检查、编译和显式工具解析已通过；SIGINT 测试的 10ms 调度窗口已在本轮拓宽并保持升级链契约。当前候选新增了自生成、manifest 校验的三原子 fixture 和受管 EM/EQ/PROD harness，尚待目标机复验 | `g01-pytest.xml`、`g01-stop-escalation.json`、`g01-external.xml`（目标机回执）+ 当前停止/fixture 契约回归 | 在目标机最终提交上重跑 pytest 全绿，并运行 `g01_gromacs_minimal_acceptance` 证明 fixture、受管 execution、固定产物和脱敏 evidence 均通过 |
| G-03 / T-03 | ORCA + Sobtop 在该外置 SSD WSL 上仅 `nproc=1` 稳定，`2/4/8` 不产出 Step 2 FCHK；判定为该环境的预期资源边界。当前实现已扫描进程可用核数、写回 `min(8, 可用核数)`，显式超限值钳制并警告但不阻塞 | `g03-li-resource-matrix.json`（目标机回执）+ CPU 规范化契约回归 | 以规范化后的新 run 在该目标机完成 Step 1--10 并保存 execution JSON/JUnit；不再把并行受限本身记为 Willy 缺陷 |

### P2：产品增强和治理

| 编号 | 缺口 | 事实依据 | 关闭条件 |
|------|------|------|------|
| G-06 | 按 run 历史持久化、刷新恢复和 `/switch` 已实现；通用错误指导及目标浏览器复验仍待完成 | 运行助理已支持状态、ETA、EQ 受限方案确认、停止、`/resume`、`/fork`、严格 `/switch`；每个 run 独立保存历史，resume 保持原编号，fork 跟随子编号，switch 在源/目标写事件 | 在目标浏览器验证刷新、双向切换、fork/resume 后历史绑定且不串工程；完成剩余通用错误指导 |
| G-07（Ubuntu 22.04.5 条件关闭） | 2026-08-14 目标机六类运行错误均进入正确终态；真实 Packmol 启动、ABI/依赖缺失分类、LLM 配置错误边界和公开/私有脱敏均通过 | 仅对 Ubuntu 22.04.5 WSL2 关闭；Ubuntu 20/24 或其他 ABI 目标仍需按同一报告格式复测 |

> 2026-08-14 G-07/T-07 目标机复测：`g07-local-wsl-acceptance.json`（由目标机回执提供）。本次只关闭 Ubuntu 22.04.5 WSL2 目标；其他 ABI 目标仍需独立确认。

### Ubuntu 22.04 GPU WSL 验收清单

另一套 `Ubuntu-22.04` WSL 由专门测试者操作。该系统预期仅额外具有 GPU 加速的 GROMACS，
因此它用于验证**未预配置 Willy 时的自动发现与模拟环境**，不承担 G16/ORCA/OPLS-AA 四条
完整 profile 的验收。测试者应在实际启动 Willy 的同一终端、完整源码检出目录中依次记录：

1. Python 3.10--3.12、干净虚拟环境、`pip check`、`compileall` 与默认 `pytest -q`；不得复用现有
   `.env`、`md_run/` 或 `WILLY_*` 环境变量作为通过证据。
2. 清除 `WILLY_GMX_BIN` 后调用 `env_registry.resolve_tool("gmx")`：在保留 GROMACS 环境脚本初始化的
   `PATH` 时预期由 `path` 发现；再设置 `WILLY_GMX_BIN` 为已安装 GROMACS 的可执行文件路径，预期由
   `willy_env` 优先发现。GROMACS 不做磁盘扫描，但与其他外部二进制一样支持继承的 `PATH`；其余
   G16/formchk/ORCA 发现策略不属于本机的 GROMACS 验收范围；
   源码内的 Multiwfn、Packmol、Sobtop 文件按项目路径可见。
3. `gmx --version`：记录版本、CUDA/GPU 编译特性与实际启动命令环境；只保存脱敏文本，不记录绝对
   路径、用户信息或其他环境变量。
4. 配置页“运行依赖预检”：确认它只展示独立工具的满足/不满足和来源；显式 `WILLY_GMX_BIN`、项目
   `.env` 或继承的 `PATH` 任一可用时都应将 GROMACS 显示为满足，且预检不会覆盖已有配置。缺少未选量子后端或
   GROMACS 配置均不应禁止本地任务创建。预检对其他自动发现工具的 `.env` 写入必须作为单独的有写入测试。
5. Packmol 最小周期盒与 GROMACS GPU 最小 `grompp`/`mdrun` smoke：确认 Packmol ABI、GPU 后端和
   受管进程生命周期可用；该项只验证模拟底座，不替代量子到 Step 10 的完整流程。
6. 在不安装 G16/ORCA 的条件下启动前端：确认依赖缺失的公共文案、错误/状态气泡和停止控件不会泄露
   路径、命令、密钥或原始日志。完成后保存版本、commit、结果与脱敏截图/报告。

上述环境具备 ORCA + Sobtop 时只满足 G-03 的工具前置条件；仍须以该环境完成受管 Step 1--10
并达到 `done` 才能关闭 G-03。它不承担其余三条 profile 的重放，也不以裸 GROMACS CUDA 成功
替代 Willy 受管十步链路。

## 需人工验证的缺口

自动化测试只能证明确定性契约，不能替代真实软件、真实设备、真实用户操作和部署环境的观察。下表对应当前仍开放的人工验证；未完成时，缺口只能标记为“代码/自动化通过，人工待验”。

| 缺口 | 开发者必须执行的人工验证 | AI 协作角色 | 通过条件与证据 |
|------|------|------|------|
| G-01 / T-01 | 开发者在新 WSL 的全新检出中创建含 pip 的虚拟环境，安装测试依赖并执行依赖预检、默认回归和最小受管 smoke | 0 号制定清单，6 号生成报告 | `pip check`、compileall、默认 pytest、显式 `WILLY_GMX_BIN`/ORCA 解析和 Willy 受管 smoke 均通过；保存版本、环境摘要和脱敏结果 |
| G-03 / T-03 | 开发者在新 WSL 单独运行一个可用 profile，不调用四 profile 串行脚本 | 6 号提供 external profile 证据校验器和脱敏模板 | 该 profile 从 Step 1 到 Step 10 完成，状态 `done`，阶段许可与固定产物哈希通过；保存 execution JSON/JUnit，失败也保存固定 issue code |
| G-06 / T-05 | 开发者询问方案助理项目架构，再人为制造 EQ 错误，分别测试查询状态、等待确认、修改方案、确认重跑、暂停文本、实际中止，以及中止后的 `/resume`、有效 `/fork`、过早 `/fork`、刷新恢复和双向 `/switch` | 3 号提供状态/进程观测工具，4 号提供气泡与控件检查点 | 设计问答只引用白名单文档且不改变方案/进程；未确认不执行；修改方案产生新 action；确认后从许可步骤重跑；暂停文本不杀进程；中止按钮能结束受管进程；`/resume` 保持原 run 历史，有效 `/fork` 建立独立子 run 并跟随子历史，过早 `/fork` 回到等待；`/switch` 仅接受两种编号格式并恢复目标历史；保存状态、manifest/event、历史文件与气泡顺序记录 |
| G-07 / T-07 | 开发者逐个触发输入错误、外部依赖缺失、grompp/EQ/PROD 失败和 LLM 配置错误，观察用户文案与开发诊断是否分离 | 0 号维护错误分类，3/4 号提供各层错误样例和日志校验 | 前端只显示文件/操作级摘要和解决建议；私有日志含结构化 `run_id/step/error_kind`；无底层密钥、完整命令或原始日志泄露 |

### 人工与 AI 边界

- **开发者是唯一真人**：只有开发者可以提供私有凭据、操作真实许可证软件、跨设备部署、观察真实 UI/进程并签署人工验收。
- **0-7 号全部是 AI 协作角色**：他们可以生成测试步骤、调用受限工具、检查证据和提出“不通过”结论，但不能代替开发者签署真实环境通过。
- **6 号、5 号、7 号不是人工测试人员或管理员**：6 号负责测试框架，5 号负责文档一致性，7 号负责网关代码与部署约束；这些职责均由 AI 执行，真人操作由开发者完成。
- **普通用户不是项目验收角色**：用户行为可作为开发者人工测试场景，不能被当作第二个真人成员或授权来源。

人工验收记录至少包含：版本/commit、测试角色、日期、环境摘要、操作结果、公开错误摘要和脱敏证据路径。不得记录 API Key、管理员密钥、Authorization、完整 prompt、原始日志或私有运行目录。

### 不列为当前缺口的事项

- 历史四条 G16/ORCA + Sobtop/LigParGen profile 的十步结果：已确认来自当前开发机并以核心 JSON/JUnit 归档；另有远程 Ubuntu 20.04.6 的一条完整 profile 记录。目标机仍只需验证一条可用路线。
- `0.15e` 单一 Ewald 净电荷 warning 容差：已实现并回归；超过阈值、不可解析或伴随其他 warning 仍阻断。
- G09、科学体系预测、后处理/分析、离子 OPLS 和其他未声明力场混用：明确非目标，不纳入当前版本验收。
- 网关离线协议、设备令牌、撤销、额度和管理员控制面：作为后续版本参考保留离线回归，不属于本版本产品入口或发布验收。

## 使用规则

- 每个未解决问题必须有责任领域、事实依据、完成证据和关闭条件。
- 修复问题时，同一变更更新本台账、责任文档和测试；不能仅删除问题描述。
- 不在本文记录真实密钥、私有运行数据或未脱敏日志。安全风险只记录处理要求和验收证据。

## 本版本明确非目标

- **G09**：代码保留后端接口，但未获得授权进行可靠全链路验证。方案助理欢迎气泡必须提示“G09 未经可靠全链路验证，使用时可能出现运行问题”；不把 G09 计入本版本通过 profile。
- **科学体系预测**：本版本只验收流程是否完整执行和最终是否无错误，不对密度、结构、收敛质量或目标体系科学结论背书。
- **后处理/分析**：后处理代码和设计可保留为内部实验能力，但不列入本版本公开可用范围和发布门禁。
- **离子 OPLS 与其他力场混用**：继续受控拒绝，不作为本版本支持能力。

## 已关闭或已替代的历史项

| 原问题 | 当前结论 |
|------|------|
| Gradio 默认绑定 `0.0.0.0` | 已关闭：默认绑定为 `127.0.0.1`。 |
| `frontend_api.py` 使用 `os.system` 清理 | 已关闭：改为受控子进程与路径操作。 |
| `src/willy/` 中存在 5 处 `shell=True` | 已关闭：2026-08-02 全仓复核未发现 `shell = True`。后续子进程安全审计纳入后续部署与网关治理。 |
| 模拟层缺少设计文档 | 已关闭：`simulation_design.md` 已合并包含 PROD 后处理设计。 |
| 模拟层跳过分子工具缺失 | 已替代：该能力会破坏拓扑与建盒一致性，因此不对 Simulation Agent 公开。 |
| 设计文档普遍使用旧文件名 | 已关闭：当前设计文档已使用实际模块名；历史迁移名称只保留在命名与重构记录中。 |
| 缺少 CI 与测试 extras | 已关闭：默认 regression、compileall、mock eval 和测试台账质量门禁已进入 CI；外部 profile 的 self-hosted 重放仍由 G-03/T-03 跟踪。 |
| 缺少完整 `config.json` schema、唯一 step registry 与规范化依赖归属 | 已关闭：`config_schema.py` 负责外层结构；`simulation.protocol` 负责 MD v2 与迁移；`STEP_REGISTRY`/`EXECUTION_MODULE_REGISTRY` 统一步骤和依赖归属，并有契约回归。 |
| 新 run 隐式继承旧 `done_steps`，并使上游修复被误记为下游成功 | 已关闭：新 run 从 Step 1 开始；续跑必须显式提供原运行目录。Step 2 校验 `.fchk`/`.mol2` 契约，Step 3 拒绝不完整分子集合，防止错误延迟至 Sobtop。 |
| EQ 重试期间，上游修复工具成功被重标为 Step 9 成功，导致 PROD 越级启动 | 已关闭：LayerAgent 保留工具实际步骤身份；编排器按失效阶段回滚或重跑，Step 8--10 在 `mark_done` 前强制校验私有 manifest 许可和非空产物；RunRegistry 仅对历史状态越级做无损公开状态对账。回归覆盖 MDP/EM 上游修复、EQ 许可缺失和活跃 EQ 状态纠正。 |
| 方案确认与启动回执合并为重复助手气泡 | 已关闭：待确认方案后的文本启动回复记录为显式用户动作；启动回执不再重复方案摘要，并有前端与配置 Agent 回归测试。 |

## 发布前门禁

发布前，0 号、5 号和 6 号必须确认：高优先级项无未认领风险；相关外部 smoke 有证据；`pytest -q` 与 LLM mock eval 全绿；以及 `document_registry.md` 中没有无责任人的 `待同步` 文档。
