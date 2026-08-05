# Willy — AI 驱动的分子动力学模拟自动化

用自然语言描述化学体系，AI Agent 自动完成从量子化学计算、建盒到 GROMACS EM/EQ/PROD 的 MD 流程。

> 当前公开主流程为 10 步：体系准备后执行 GROMACS EM、三点式退火 EQ 和生产模拟。每个 MD 阶段至少登记 `.tpr`、`.gro`、`.xtc`、`.edr`；任一前置阶段未验收都不会进入 PROD。代码契约与模拟层测试已验证；四种量子/拓扑组合的真实端到端验收仍待在目标验收机执行。

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
│  OpenAI-compatible 服务 + toolist_global.py（10 tools）│
│     NL → config.json (molecules/residues/md)      │
└────────────────────┬─────────────────────────────┘
                     │
┌────────────────────▼─────────────────────────────┐
│              run_pipeline.py (10 步)              │
│  1. 量子结构优化 (g16/ORCA)                        │
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
| Gaussian16 | 量子化学计算（默认后端） | 需 license |
| ORCA 6.x | 量子化学计算（可选后端） | [orcaforum.kofo.mpg.de](https://orcaforum.kofo.mpg.de/) |
| formchk | Gaussian checkpoint 转换 | 随 Gaussian 安装 |
| Multiwfn | RESP 电荷拟合 | [sobereva.com/multiwfn](http://sobereva.com/multiwfn/) |
| GROMACS | MD 模拟引擎 | `apt install gromacs` |
| Packmol | 初始盒子构建 | [github.com/mcubeg/packmol](https://github.com/mcubeg/packmol) |
| Sobtop | 拓扑生成 (GAFF 力场) | [sobereva.com/soft/sobtop](http://sobereva.com/soft/sobtop/) |

> 项目 `vendor/` 目录已内置 Packmol、OpenBabel、Sobtop，无需额外下载。

### 安装

```bash
git clone <repo-url>
cd AutomizedSimulations
pip install -e .
```

### 配置 API Key

启动应用后，也可以在左侧“配置”页签填写 API Key、Base URL 和 Model。该页会列出常见服务商、端点和模型格式提示；“测试连接”只使用当前表单值验证 Chat Completions 与工具调用，不会保存配置，也不会自动补 `/v1`。密钥仅在点击保存后写入本机 `.env`，保存后请重启应用。

```bash
cp .env.example .env
# 编辑 .env，填入 API Key、Base URL 和 Model
```

外部软件可使用 `WILLY_G16_BIN`、`WILLY_ORCA_HOME`、`WILLY_GMX_BIN`、
`WILLY_LIGPARGEN_BIN`、`WILLY_BOSS_HOME` 等白名单变量覆盖发现结果；详细优先级和
旧变量兼容规则见 [`docs/environment_registry_design.md`](docs/environment_registry_design.md)。Sobtop、Packmol、OpenBabel 为项目内置工具，无需配置环境变量。

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
| DMAA | 溶剂 | 0 | b3lyp/6-311+g(d,p) | 二甲基乙酰胺 |

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
│   ├── toolist_global.py   ← Layer 0 工具（10 个）
│   ├── toolist_quantum.py  ← Layer 1 工具（8 个）
│   ├── toolist_topology.py ← Layer 2 工具（6 个）
│   ├── toolist_simulation.py ← Layer 3 工具（13 个）
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
│   ├── quantum/            ← 量子化学 (g16/ORCA)
│   ├── topology/           ← 拓扑生成 (Sobtop)
│   └── simulation/         ← MD 模拟 (GROMACS)
├── vendor/                 ← 内置依赖 (Packmol, OpenBabel, Sobtop)
├── struct/                 ← 分子结构文件 (.gjf)
├── md_run/<run_id>/         ← 隔离运行产物与审计记录
├── docs/                   ← 文档 (详见 docs/README.md 索引)
│   ├── README.md          ← 文档导航
│   ├── document_registry.md ← 文档状态与维护规范
│   ├── Willy.md           ← 架构文档
│   ├── knowledge.md        ← 分子知识库
│   ├── ERR_WARN_Build.md   ← Error/Warning 协议
│   ├── naming_convention.md ← 命名规范
│   ├── reconstruction.md   ← 重构检查清单
│   ├── quantum_design.md   ← 量子层设计
│   ├── topology_design.md  ← 拓扑层设计
│   ├── status_api.md       ← 前端接口
│   ├── employees.md        ← 团队分工与共识
│   ├── simulation_design.md ← 模拟设计
│   ├── postprocessing_design.md ← 后处理设计
│   ├── testing_strategy.md ← 测试与发布门禁
│   ├── run_assistant_design.md ← 运行助理计划
│   ├── project_gap_analysis.md ← 问题台账
│   └── lithium-salts.md    ← 锂盐体系调研
└── benchmarks/             ← LLM 评分测试
```

## 更多文档

→ **[docs/README.md](docs/README.md)** — 文档导航与分类；**[文档台账](docs/document_registry.md)** — 用途、状态、责任人与维护规范。

## 许可

待定
