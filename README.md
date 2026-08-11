# Willy — AI 驱动的分子动力学模拟自动化

用自然语言描述化学体系，AI Agent 自动完成从量子化学计算、建盒到 GROMACS EM/EQ/PROD 的 MD 流程。

> **版本 0.2.0**：新增受控托管 LLM 网关服务器及 Willy 客户端 `managed/byok` provider，支持设备注册、短期令牌、模型白名单、额度和撤销审计。当前定位为本机/受控局域网试运行；OIDC、多实例、生产数据库、备份告警和公网部署仍未声明可用。

> 当前公开主流程为 10 步：体系准备后执行 GROMACS EM、三点式退火 EQ 和生产模拟。每个 MD 阶段至少登记 `.tpr`、`.gro`、`.xtc`、`.edr`；任一前置阶段未验收都不会进入 PROD。代码契约与模拟层测试已验证；2026-08-11 的四条真实 profile（G16/ORCA + Sobtop/LigParGen）均已完成 10/10，使用 7 ns EQ、2 ns PROD，且根目录配置在批次结束后恢复。

本版本以“受支持 profile 完成十步、最终状态无错误且产物契约通过”为成熟方案标准。G09 仅保留接口并在方案助理欢迎气泡提示未经可靠全链路验证；科学体系预测、后处理/分析和 SSH/Slurm 远程执行不属于本版本公开能力。

```
用户: "Li 80, TFSI 80, FEC 300, 350K, 20ns"
         │
         ▼
OpenAI-compatible LLM  →  config.json  →  run_pipeline.py (10 步)
         │
         ▼
   GROMACS 产物：em/eq/prod 的 .tpr + .gro + .xtc + .edr
```

## 架构

```
┌──────────────────────────────────────────────────┐
│                   Gradio Web UI                   │
│         聊天输入 → LLM 解析 → 方案确认 → 启动       │
│         3D 分子查看器 | 运行助理                    │
└────────────────────┬─────────────────────────────┘
                     │
┌────────────────────▼─────────────────────────────┐
│       Config Agent (agent_config.py)               │
│  OpenAI-compatible 服务 + toolist_global.py（11 tools）│
│     NL → config.json (molecules/residues/md)      │
└────────────────────┬─────────────────────────────┘
                     │
┌────────────────────▼─────────────────────────────┐
│              run_pipeline.py (10 步)              │
│  1. 量子结构优化 (g16/ORCA；G09 未可靠全链路验证)     │
│  2. 格式转换 (.fchk/.molden → .mol2)               │
│  3. RESP 电荷计算                                  │
│  4. 拓扑生成 (Sobtop: .mol2+.chg → .itp+.gro)      │
│  5. 主拓扑 + itp 修订                              │
│  6. MDP 参数生成 (em/eq/prod)                      │
│  7. Packmol 初始盒子构建                            │
│  8. GROMACS 能量最小化 (EM)                         │
│  9. GROMACS 三点式退火平衡 (EQ)                      │
│ 10. GROMACS 生产模拟 (PROD)                         │
└──────────────────────────────────────────────────┘
```

## 快速开始

### 环境要求

| 依赖 | 用途 | 获取方式 |
|------|------|------|
| Python ≥3.10 | 运行环境 | `apt install python3` |
| Gaussian16 / Gaussian09 | 量子化学计算（默认后端为 G16） | 需 license |
| ORCA 6.x | 量子化学计算（可选后端） | [orcaforum.kofo.mpg.de](https://orcaforum.kofo.mpg.de/) |
| formchk | Gaussian checkpoint 转换 | 分别配置对应版本的 formchk |
| Multiwfn（内置） | RESP 电荷拟合 | 随仓库提供 Linux x86_64 负载 |
| GROMACS | MD 模拟引擎 | `apt install gromacs` |
| Packmol | 初始盒子构建 | [github.com/mcubeg/packmol](https://github.com/mcubeg/packmol) |
| Sobtop | 拓扑生成 (GAFF 力场) | [sobereva.com/soft/sobtop](http://sobereva.com/soft/sobtop/) |
| Open Babel 3（仅 OPLS-AA） | LigParGen 的 SMILES/MOL2 转换 | `apt install openbabel` |
| C shell（仅 OPLS-AA） | BOSS 运行脚本 | `apt install csh` |

> 项目 `vendor/` 目录已内置 Packmol、Sobtop、Multiwfn 与精简 Open Babel 运行时。Multiwfn 不需要外部安装或 PATH 配置；OPLS-AA 的 LigParGen/BOSS 链路仍需要另行安装含格式插件和数据文件的完整 Open Babel，以及 C shell。

> OPLS-AA 仅能作为与 Sobtop/GAFF 隔离的显式参数化路径。中性有机小分子的 LigParGen/BOSS 参数化、ITP 命名空间、GRO 五列残基字段、Packmol 以及 GROMACS EM/EQ/PROD 已有真实证据；对于单一 Ewald 净电荷 warning，只有总电荷绝对值不超过 `0.15e` 时才按受控规则放行，其他 warning 或更大不平衡仍拒绝。Li+、NO3-、TFSI- 等离子或不含 H 组分不由当前 LigParGen 路径支持，必须提供可验证的外部 OPLS 参数，且不得与 Sobtop 产物混用。

### 安装

```bash
git clone <repo-url>
cd AutomizedSimulations
pip install -e .
```

### 配置 LLM

启动应用后，在“配置”页选择“自带 API Key”或“托管网关（服务器）”。前者允许填写 API Key、Base URL 和 Model；“测试连接”只使用当前表单值验证 Chat Completions 与工具调用，不会保存配置，也不会自动补 `/v1`。后者只读取部署者提供的、未提交到 GitHub 的 `managed_gateway.json`；用户点击“申请接入”时才生成本机设备密钥，由网关按服务端名额记录并等待管理员批准，不会显示或接收上游 Key、Base URL 或邀请码。保存本机 BYOK 配置或切换模式后，应用会刷新 provider，无需重启。

```bash
cp .env.example .env
# 编辑 .env，填入 API Key、Base URL 和 Model
```

外部软件可使用 `WILLY_G16_BIN`、`WILLY_G09_BIN`、`WILLY_G09_FORMCHK_BIN`、`WILLY_ORCA_HOME`、`WILLY_GMX_BIN`、
`WILLY_LIGPARGEN_BIN`、`WILLY_BOSS_HOME`、`WILLY_OBABEL_BIN`、`WILLY_CSH_BIN`
等白名单变量覆盖发现结果；详细优先级和旧变量兼容规则见
[`docs/environment_registry_design.md`](docs/environment_registry_design.md)。Sobtop 与 Packmol 为项目内置工具；OPLS-AA 的 Open Babel/C shell/BOSS 必须通过预检。

### 检查环境

```bash
python3 -m willy.env_checker
```

### 启动

```bash
python3 app.py
# 浏览器打开 http://localhost:7860
```

在聊天框输入模拟需求，例如：

```
Li 80, TFSI 80, FEC 300, 350K, 20ns
```

Agent 会给出方案确认；紧随方案回复“运行”“开始运行”或“确认运行”后，自动执行当前的 10 步 MD 流程。确认前可在配置中开启可选的全精度 `.trr` 轨迹；`.gro`、`.xtc`、`.edr` 是固定产物。

界面提供“任务”“配置”和“关于”页签；“关于”页说明项目定位、构建参与者、运行模型、Willy 的职责与后续规划，并展示项目公众号二维码。

### CLI 模式

```bash
# 确保 config.json 已配置好
python3 run_pipeline.py          # Gaussian16 后端
python3 run_pipeline.py g09      # Gaussian09 后端
python3 run_pipeline.py orca     # ORCA 后端
```

## 内置分子

| 分子 | 类型 | 电荷 | 基组 | 中文别名 |
|------|:---:|:---:|------|------|
| Li | 阳离子 | +1 | b3lyp/6-311+g(d,p) | 锂离子、锂盐、锂 |
| TFSI | 阴离子 | −1 | b3lyp/6-311+g(d,p) | 双三氟甲磺酰亚胺 |
| NO3 | 阴离子 | −1 | b3lyp/6-311+g(d,p) | 硝酸根、硝酸盐 |
| PF6 | 阴离子 | −1 | b3lyp/6-311+g(d,p) | 六氟磷酸根 |
| FEC | 溶剂 | 0 | b3lyp/6-311+g(d,p) | 氟代碳酸乙烯酯 |
| DME | 溶剂 | 0 | b3lyp/6-311+g(d,p) | 乙二醇二甲醚 |
| DMM | 溶剂 | 0 | b3lyp/6-311+g(d,p) | 二甲氧基甲烷 |
| EC | 溶剂 | 0 | b3lyp/6-311+g(d,p) | 碳酸乙烯酯 |
| EMC | 溶剂 | 0 | b3lyp/6-311+g(d,p) | 碳酸甲乙酯 |
| TTE | 溶剂 | 0 | b3lyp/6-311+g(d,p) | 含氟醚 |

LLM 支持中文别名映射：输入"锂离子"自动识别为 Li，"硝酸根"→NO3，依此类推。

上表是 Config Agent 的可识别分子元数据，不等同于当前工作区已具备可执行的量子输入。启动某个组分前，`struct/<分子>.gjf`（或可复用的对应后端中间产物）必须存在；当前实际可用文件以 `struct/` 目录为准，缺失输入会在分配 run 后被前置校验拒绝。

## 扩展分子

将 `.gjf` 文件放入 `struct/` 目录，系统自动识别为新分子（默认中性；知识库元数据默认标记为 GAFF）。这既使分子可被识别，也为新 run 提供必需的量子输入。

如需精确参数（电荷、自旋、特殊基组），在 `docs/knowledge.md` 的分子表格中新增一行即可。

## 自定义上传

在 Web UI 中点击"上传结构"按钮，支持 `.gjf`、`.mol2`、`.pdb`、`.xyz` 格式。上传后系统自动刷新分子列表。

## 目录结构

```
AutomizedSimulations/
├── app.py                  ← Gradio Web UI
├── run_pipeline.py         ← 全流程编排 (10 步)
├── config.json             ← 体系配置
├── src/willy/             ← 核心 Python 包
│   ├── agent_config.py     ← Layer 0 配置 Agent
│   ├── agent_quantum.py    ← Layer 1 量子修复 Agent
│   ├── agent_topology.py   ← Layer 2 拓扑修复 Agent
│   ├── agent_simulation.py ← Layer 3 模拟修复 Agent
│   ├── agent_run.py        ← 只读运行助理
│   ├── toolist_global.py   ← Layer 0 工具（11 个）
│   ├── toolist_quantum.py  ← Layer 1 工具（8 个）
│   ├── toolist_topology.py ← Layer 2 工具（6 个）
│   ├── toolist_simulation.py ← Layer 3 工具（14 个）
│   ├── toolist_run.py      ← 只读运行工具（9 个）
│   ├── action_contract.py  ← 工具效果与确认契约
│   ├── recovery_policy.py  ← 失败恢复白名单与重试上限
│   ├── llm_config.py / llm_budget.py ← LLM 配置与运行级预算
│   ├── config_schema.py / config_store.py ← 配置外层校验与原子写入
│   ├── env_registry.py / env_checker.py ← 工具发现与依赖预检
│   ├── pipeline_orchestrator.py ← 10 步主流程编排
│   ├── pipeline_launch.py / pipeline_state.py / step_registry.py ← 启动、状态与步骤契约
│   ├── run_registry.py / run_store.py / run_provenance.py ← run 审计、事务与溯源
│   ├── process_lifecycle.py ← 受管外部进程生命周期
│   ├── frontend_api.py      ← 前端专用后端 API
│   ├── quantum/            ← 量子化学 (g16/g09/ORCA)
│   ├── topology/           ← 拓扑生成 (Sobtop)
│   └── simulation/         ← MD 模拟 (GROMACS)
├── vendor/                 ← 内置依赖 (Packmol, Open Babel, Sobtop, Multiwfn)
├── struct/                 ← 分子结构文件 (.gjf)
├── md_run/<run_id>/         ← 隔离运行产物与审计记录
├── docs/                   ← 文档 (详见 docs/README.md 索引)
│   ├── README.md          ← 文档导航
│   ├── document_registry.md ← 文档状态与维护规范
│   ├── Willy.md           ← 架构文档
│   ├── knowledge.md        ← 分子知识库
│   ├── knowledge_mdrun.md  ← GROMACS 诊断知识库
│   ├── ERR_WARN_Build.md   ← Error/Warning 协议
│   ├── naming_convention.md ← 命名规范
│   ├── quantum_design.md   ← 量子层设计
│   ├── topology_design.md  ← 拓扑层设计
│   ├── status_api.md       ← 前端接口
│   ├── employees.md        ← 团队分工与共识
│   ├── simulation_design.md ← 模拟设计
│   ├── environment_registry_design.md ← 外部环境设计
│   ├── remote_execution_design.md ← 远程执行设计
│   ├── gateway.md           ← 托管 LLM 网关设计
│   ├── revision_strategy.md ← 修订路线与重构清单
│   ├── testing_strategy.md ← 测试与发布门禁
│   ├── run_assistant_design.md ← 运行助理计划
│   └── project_gap_analysis.md ← 问题台账
└── benchmarks/             ← LLM 评分测试
```

## 更多文档

→ **[docs/README.md](docs/README.md)** — 文档导航与分类；**[文档台账](docs/document_registry.md)** — 用途、状态、责任人与维护规范。

## 贡献与协作

GitHub 的 Contributors 图由默认分支上的提交作者自动统计。项目同时记录以下协作角色：

- **Codex（OpenAI AI 协作工程助手）**：参与架构设计、代码实现、测试、文档与发布质量检查。

Codex 没有独立 GitHub 账号，因此该致谢不伪造为 GitHub 用户贡献。新增真人贡献者时，请使用其已关联 GitHub 账号的提交邮箱提交到默认分支，或在仓库的 Settings -> Collaborators / Manage access 中邀请其作为协作者。

### 第三方组件引用与著作权

Willy 集成并编排第三方科学软件，但 Willy 作者不拥有其原始项目的著作权。研究工作如使用了 Willy 集成的 Sobtop 参数化路径及其中涉及的 Multiwfn 工作，请至少引用以下文献：

- Tian Lu, Feiwu Chen, *Multiwfn: A Multifunctional Wavefunction Analyzer*, *Journal of Computational Chemistry* **33**, 580-592 (2012). DOI: [10.1002/jcc.22885](https://doi.org/10.1002/jcc.22885)
- Tian Lu, *A comprehensive electron wavefunction analysis toolbox for chemists, Multiwfn*, *Journal of Chemical Physics* **161**, 082503 (2024). DOI: [10.1063/5.0216272](https://doi.org/10.1063/5.0216272)

研究工作如使用了 Willy 集成的 Packmol 建盒组件，请至少引用以下文献：

- L. Martinez, R. Andrade, E. G. Birgin, J. M. Martinez, *Packmol: A package for building initial configurations for molecular dynamics simulations*, *Journal of Computational Chemistry* **30**, 2157-2164 (2009). DOI: [10.1002/jcc.21224](https://doi.org/10.1002/jcc.21224)
- J. M. Martinez, L. Martinez, *Packing optimization for the automated generation of complex system's initial configurations for molecular dynamics and docking*, *Journal of Computational Chemistry* **24**, 819-825 (2003). DOI: [10.1002/jcc.10216](https://doi.org/10.1002/jcc.10216)

使用 Sobtop、Packmol 或其他第三方组件时，使用者还应遵守其各自的许可、分发和引用要求。Willy 对这些组件仅提供集成与工作流编排，不主张其原始软件、文档或学术成果的著作权。

## 许可

待定
