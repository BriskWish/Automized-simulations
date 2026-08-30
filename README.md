# Willy — AI 驱动的小分子/电解液分子动力学模拟自动化

用自然语言描述化学体系，AI Agent 自动完成从量子化学计算、建盒到 GROMACS EM/EQ/PROD 的 MD 流程。

> 当前公开能力为本机 MD 执行和用户自配 OpenAI-compatible LLM。运行助理支持受控 `/resume`、`/fork`、`/switch` 与按工程保存的对话记录；CPU 资源预检会规范化 `nproc`。远程执行与托管网关不属于当前产品入口。

> 当前公开主流程为 10 步：体系准备后执行 GROMACS EM、三点式退火 EQ 和生产模拟。每个 MD 阶段至少登记 `.tpr`、`.gro`、`.xtc`、`.edr`；任一前置阶段未验收都不会进入 PROD。成熟方案必须在目标机以受支持 profile 完成十步，并通过受控 LLM 错误处理验收。

本版本以“受支持 profile 完成十步、最终状态无错误且产物契约通过”为成熟方案标准。G09 仅保留接口并在方案助理欢迎气泡提示未经可靠全链路验证；科学体系预测和后处理/分析不属于本版本公开能力。

> **发行边界**：本项目唯一支持的交付形态是 GitHub 上的完整源码检出后进行可编辑安装，不发布 wheel、PyPI 包、独立二进制安装包或受控 staging 作为可运行产品。`vendor/` 中的第三方文件不因 Willy 的公开源码使用条款而获得授权；使用者必须遵守各上游项目的许可、下载和再分发条件。受控 staging 只用于文件与许可审计，不是可运行发行物。

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
│          FastAPI + React Assistant UI              │
│    方案助理 | 运行助理 | 可视化 | 审计日志            │
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

### 环境要求与版本基线

| 依赖 | 当前项目版本基线 | 用途 | 获取方式与许可状态 |
|------|------|------|------|
| Python | 3.10--3.12 | 运行环境 | 系统、conda/mamba 或受控环境提供；系统 Python 3.8 不受支持 |
| Node.js | 20 LTS 或更高 | 构建 React 前端 | 仅在源码检出中执行一次 `npm ci && npm run build`；运行时由 `app.py` 提供已构建静态资源 |
| Gaussian 16 | 合法安装，Revision 不自动探测 | 默认量子化学后端 | 用户自行安装；Gaussian 的签署许可软件，不随 Willy 提供或再分发 |
| Gaussian 09 | 合法安装，Revision 不自动探测 | 保留后端接口 | 用户自行安装；同样属于 Gaussian 许可软件，且本版本不作可靠全链路验收 |
| formchk | 与所选 G16/G09 同一安装 | Gaussian checkpoint 转换 | 使用同一 Gaussian 合法安装中的程序，不可混用版本 |
| ORCA | 6.1.1；兼容目标 6.x | 可选量子化学后端 | 用户按 [ORCA EULA](https://orcaforum.kofo.mpg.de/app.php/privacypolicy) 下载并安装；不随 Willy 提供或再分发 |
| Multiwfn（内置） | 项目 Vendor 负载 | RESP 电荷拟合、Molden/FCHK 转换 | `vendor/` 最小 Linux x86_64 负载；随附许可证和引用要求 |
| GROMACS | 开发/真实证据 2025.0；兼容目标 2023--2025 | MD 模拟引擎 | 用户通过系统包或官方构建安装；GROMACS 为 LGPL-2.1-or-later，不随 Willy 提供 |
| Packmol（内置） | 21.2.3，glibc >= 2.29 | 初始盒子构建 | `vendor/` 的 Linux x86_64 通用构建；MIT，保留 `PACKMOL_LICENSE.txt` |
| Sobtop | 仓内 Linux x86_64 负载 | GAFF/UFF 拓扑生成 | 上游来源为 [Sobtop](http://sobereva.com/soft/sobtop/)；本项目不声明其著作权或代替上游条款，使用者须自行核实使用条件 |
| LigParGen | 2.1 兼容适配 | OPLS-AA 参数化 | 用户自行安装命令行版本；上游 [LigParGen](https://zarbi.chem.yale.edu/ligpargen/index.html) 未在本项目记录中提供可审计再分发许可 |
| BOSS | 5.1 | LigParGen 的 OPLS/CM1A 后端 | 用户按 [Jorgensen 组下载流程](https://zarbi.chem.yale.edu/software.html) 获取；官网说明仅向学术用户免费提供，不随 Willy 提供或再分发 |
| Open Babel | 完整安装 3.1.1；上游当前 3.2.0 | LigParGen 的 SMILES/MOL2 转换 | 用户自行安装完整 Open Babel；上游采用 GPL-2.0，仓内精简运行时版本/构建来源未核实，不作为 OPLS 依赖替代品 |
| C shell | 由操作系统提供 | BOSS 运行脚本 | 用户通过发行版包管理器安装；遵从相应发行版的许可 |

> 完整源码检出当前含 Packmol、Sobtop、Multiwfn 与精简 Open Babel 运行负载；上游入口分别为 Packmol、[Sobtop](http://sobereva.com/soft/sobtop/)、Multiwfn、[Open Babel](https://openbabel.org/) 和 [3Dmol.js](https://3dmol.org/)。这些第三方内容不属于 Willy 作者声明拥有权利的内容，也不构成向使用者授予的第三方授权；受控 staging 仍按 `vendor/manifest.json` 排除未纳入发行工件的负载。Multiwfn 不需要外部安装或 PATH 配置；OPLS-AA 的 LigParGen/BOSS 链路仍需要用户另行安装 LigParGen、BOSS、含格式插件和数据文件的完整 Open Babel，以及 C shell。

> OPLS-AA 仅能作为与 Sobtop/GAFF 隔离的显式参数化路径。中性有机小分子的 LigParGen/BOSS 参数化、ITP 命名空间、GRO 五列残基字段、Packmol 以及 GROMACS EM/EQ/PROD 已有真实证据；对于单一 Ewald 净电荷 warning，只有总电荷绝对值不超过 `0.15e` 时才按受控规则放行，其他 warning 或更大不平衡仍拒绝。Li+、NO3-、TFSI- 等离子或不含 H 组分不由当前 LigParGen 路径支持，必须提供可验证的外部 OPLS 参数，且不得与 Sobtop 产物混用。

### 下载与安装

```bash
sudo apt update
sudo apt install -y libgfortran5 libxm4     #内置Packmol Multiwfn所必需的依赖组件
git clone https://github.com/BriskWish/Automized-simulations.git Willy
cd Willy
python3.11 -m venv .venv  # 或任何 Python 3.10--3.12 解释器
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
cd frontend
npm ci
npm run build
cd ..
python -V  # 必须为 Python 3.10--3.12
```

Ubuntu 20.04 的系统 `python3` 通常是 3.8，不能直接安装本项目。请先通过系统包、机构模块、
conda/mamba 或其他受控方式取得 Python 3.10--3.12，再以上述解释器创建独立虚拟环境；不要以
`--ignore-requires-python` 强行安装，也不要修改项目的 Python 下限。

安装完成后无需设置`WILLY_*` 变量，也无需将 Packmol 或 Multiwfn 加入 `PATH`。应先完成系统依赖安装再启动相应工作流。

### 配置 LLM

启动应用后，在“配置”页填写用户自有的 API Key、Base URL 和 Model。“测试连接”只使用当前表单值验证 Chat Completions 与实际工具调用，不会保存配置，也不会自动补 `/v1`；该探针的 `max_tokens` 固定为 128，以避免推理型模型在极低输出预算下被误判为不支持工具调用。保存后应用会刷新本地 provider，无需重启。托管网关配置入口在本版本冻结。

方案助理仍要求模型返回实际 function call。原始量子输入审计对 provider 使用自动工具选择以兼容拒绝固定 `tool_choice` 的 OpenAI-compatible 服务；服务端只接受唯一的 `tools_inspect_quantum_inputs` 调用，并校验其后端和组分参数与已归一化方案完全一致。HTTP 400/404/405/422、鉴权和限流错误会立即显示为对应配置问题，不会无意义地连续重试三次；网络/超时等瞬态错误仍最多重试三次。

```bash
cp .env.example .env
# 编辑 .env，填入 API Key、Base URL 和 Model
```

配置页的“运行依赖预检”会在创建工程前按量子、拓扑和模拟三组展示内置 Vendor 与外部依赖的满足状态；每组任一完整可替代链路可用即通过。它会将从 `PATH` 或受限默认目录发现的可用外部软件写入缺失的 `WILLY_*` 默认项，不覆盖已有进程环境或 `.env` 设置，也不会阻止本地任务启动。GROMACS 与其他外部二进制使用相同的优先级：显式 `WILLY_GMX_BIN` 优先，其次是项目 `.env`，最后从启动进程继承的 `PATH` 自动发现；通过 `.bashrc`/GROMACS 环境脚本初始化后再启动 Willy 即可继承这些变量。实际运行仍按任务选定的后端和步骤进行强制预检。

外部软件可使用 `WILLY_G16_BIN`、`WILLY_G09_BIN`、`WILLY_G09_FORMCHK_BIN`、`WILLY_ORCA_HOME`、`WILLY_GMX_BIN`、
`WILLY_LIGPARGEN_BIN`、`WILLY_BOSS_HOME`、`WILLY_OBABEL_BIN`、`WILLY_CSH_BIN`
等白名单变量覆盖发现结果；详细优先级和旧变量兼容规则见
[`docs/environment_registry.md`](docs/environment_registry.md)。在完整源码检出中，Sobtop 与 Packmol 由项目路径解析；OPLS-AA 的 Open Babel/C shell/BOSS 必须通过预检。公开发行包的 Sobtop 路径尚未确定，不能假定普通安装包中存在它。

### 命令行检查环境

```bash
python3 -m willy.env_checker
```

该命令列出逐模块的严格执行依赖；它不写入配置。日常首次部署优先使用配置页的“预检运行依赖”。

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
Agent默认的MD路径为梯度退火模拟，暂不支持核心更改。当前为298K -> 500K -> 500K -> 400K -> 400K -> 298K -> 298K 各步骤分别为2 1 2 1 2 2 ns.可以在方案配置时修改温度点和退火步骤时长。
界面提供本地任务、配置、新手指南和关于页；任务工作区包含方案助理、运行助理、可视化和日志。日志按当前工程展示公开 manifest registry 投影及 `events.jsonl`，不展示私有记录、命令、绝对路径或密钥。

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
...

LLM 支持中文别名映射：输入"锂离子"自动识别为 Li，"硝酸根"→NO3，依此类推。

上表是 Config Agent 的可识别分子元数据，不等同于当前工作区已具备可执行的量子输入。启动某个组分前，`struct/<分子>.gjf` 或 `struct/<分子>.inp`（或可复用的对应后端中间产物）必须存在；当前实际可用文件以 `struct/` 目录为准，缺失输入会在分配 run 后被前置校验拒绝。

## 上传结构

在方案助理中上传可审计的 `.gjf` 或 `.inp` 原始量子输入。上传时会验证有限坐标、文件内电荷和自旋，并以核心文件名登记；`Li+`、`Ca2+` 等离子文件名会分别规范为 `Li`、`Ca`，而 `NO3-` 保留为 `NO3`。同一核心文件名已存在时拒绝覆盖，并提示相关结构名称。

Willy 只保留坐标、电荷和自旋，生成规范化输入。原文件的计算方法、基组、`mem`、`nproc` 和其他执行指令不会继承；基组与资源配置由候选方案、启动前审计和 CPU 容量规则统一决定。成功后，核心文件名会进入分子知识库，供后续方案查询。用户上传文件及其本地登记属于运行数据，不随 GitHub 源码版本发布。

## 目录结构

```
Willy/
├── app.py                  ← FastAPI 入口，提供 React 工作台与受限 API
├── frontend/               ← React/assistant-ui 源码与生产构建配置
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
│   ├── env_registry.py / env_checker.py / dependency_preflight.py ← 工具发现、执行期预检与配置页建议性预检
│   ├── pipeline_orchestrator.py ← 10 步主流程编排
│   ├── pipeline_launch.py / pipeline_state.py / step_registry.py ← 启动、状态与步骤契约
│   ├── run_registry.py / run_store.py / run_provenance.py ← run 审计、事务与溯源
│   ├── process_lifecycle.py ← 受管外部进程生命周期
│   ├── frontend_api.py      ← 前端专用后端 API
│   ├── quantum/            ← 量子化学 (g16/g09/ORCA)
│   ├── topology/           ← 拓扑生成 (Sobtop)
│   └── simulation/         ← MD 模拟 (GROMACS)
├── vendor/                 ← 内置依赖 (Packmol, Open Babel, Sobtop, Multiwfn)
├── struct/                 ← 内置与本地上传的量子原始输入（.gjf/.inp）
├── md_run/<run_id>/         ← 隔离运行产物与审计记录
├── docs/                   ← 文档 (详见 docs/README.md 索引)
│   ├── README.md          ← 文档导航
│   ├── document_registry.md ← 文档状态与维护规范
│   ├── Willy.md           ← 架构文档
│   ├── knowledge_molecules.md        ← 分子知识库
│   ├── knowledge_mdrun.md  ← GROMACS 诊断知识库
│   ├── ERR_WARN_Build.md   ← Error/Warning 协议
│   ├── naming_convention.md ← 命名规范
│   ├── quantum.md   ← 量子层设计
│   ├── topology.md  ← 拓扑层设计
│   ├── status_api.md       ← 前端接口
│   ├── employees.md        ← 团队分工与共识
│   ├── simulation.md ← 模拟设计
│   ├── environment_registry.md ← 外部环境设计
│   ├── remote_execution.md ← 后续版本远程执行参考
│   ├── gateway.md           ← 后续版本网关参考
│   ├── revision.md ← 修订路线与重构清单
│   ├── testing.md ← 测试与发布门禁
│   ├── run_assistant.md ← 运行助理计划
│   └── project_gap_analysis.md ← 问题台账
└── benchmarks/             ← LLM 评分测试
```

## 更多文档

→ **[docs/README.md](docs/README.md)** — 文档导航与分类；**[文档台账](docs/document_registry.md)** — 用途、状态、责任人与维护规范。

## 协作

- **Codex（OpenAI AI 协作工程助手）**：参与架构设计、代码实现、测试、文档与发布质量检查。

### 第三方组件引用与著作权

Willy 集成并编排第三方科学软件，但 Willy 作者不拥有其原始项目的著作权。研究工作如使用了 Willy 集成的 Sobtop 参数化路径、 Multiwfn 电荷生成等内容，请至少引用以下文献/网页：

- Tian Lu, Sobtop, Version [当前版本], http://sobereva.com/soft/Sobtop (accessed on 日 月 年)　注：此处的时期是你最后访问本页面的日期
- Tian Lu, Feiwu Chen, *Multiwfn: A Multifunctional Wavefunction Analyzer*, *Journal of Computational Chemistry* **33**, 580-592 (2012). DOI: [10.1002/jcc.22885](https://doi.org/10.1002/jcc.22885)
- Tian Lu, *A comprehensive electron wavefunction analysis toolbox for chemists, Multiwfn*, *Journal of Chemical Physics* **161**, 082503 (2024). DOI: [10.1063/5.0216272](https://doi.org/10.1063/5.0216272)

研究工作如使用了 Willy 集成的 Packmol 建盒组件，请至少引用以下文献：

- L. Martinez, R. Andrade, E. G. Birgin, J. M. Martinez, *Packmol: A package for building initial configurations for molecular dynamics simulations*, *Journal of Computational Chemistry* **30**, 2157-2164 (2009). DOI: [10.1002/jcc.21224](https://doi.org/10.1002/jcc.21224)
- J. M. Martinez, L. Martinez, *Packing optimization for the automated generation of complex system's initial configurations for molecular dynamics and docking*, *Journal of Computational Chemistry* **24**, 819-825 (2003). DOI: [10.1002/jcc.10216](https://doi.org/10.1002/jcc.10216)

使用 Sobtop、Packmol 或其他第三方组件时，使用者还应遵守其各自的许可、分发和引用要求。Willy 对这些组件仅提供集成与工作流编排，不主张其原始软件、文档或学术成果的著作权。

## 源码使用与第三方许可

Willy 的原创源代码以公开源码形式免费提供，可用于任何合法用途，包括研究、学习、评估和内部工作流。任何对 Willy 原创代码的**再分发**，包括重新发布源码或修改版、制作安装包/镜像、或将其并入其他产品，须事先取得项目作者授权。完整条款见 [`LICENSE.md`](LICENSE.md)。本声明不向使用者授予 `vendor/`、外部软件、其文档或学术成果的任何权利；各第三方组件仍完全适用其上游许可证、下载条件和引用要求。

该条款是项目作者的自定义源码使用声明，并非 OSI 定义的标准开源许可证：标准开源许可证要求允许自由再分发。GitHub 公开仓库仅使其他 GitHub 用户可以查看和在 GitHub 服务内 fork；它不替代第三方软件的再分发许可。第三方许可证、版本来源与待核实项见 [`docs/environment_registry.md`](docs/environment_registry.md)。
