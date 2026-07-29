# Willy — AI 驱动的分子动力学模拟自动化

用自然语言描述你的化学体系，AI Agent 自动完成从量子化学计算到 GROMACS MD 模拟的全流程。

```
用户: "Li 80, TFSI 80, FEC 300, 350K, 20ns"
         │
         ▼
   DeepSeek LLM  →  config.json  →  run_pipeline.py (7 步)
         │
         ▼
   GROMACS: EM → NVT → NPT EQ → PROD
```

## 架构

```
┌──────────────────────────────────────────────────┐
│                   Gradio Web UI                   │
│         聊天输入 → LLM 解析 → 方案确认 → 启动       │
│         3D 分子查看器 | 流水线进度监控              │
└────────────────────┬─────────────────────────────┘
                     │
┌────────────────────▼─────────────────────────────┐
│                 LLM Agent (agent.py)              │
│     DeepSeek v4-pro + 7 function calling tools    │
│     NL → config.json (molecules/residues/md)      │
└────────────────────┬─────────────────────────────┘
                     │
┌────────────────────▼─────────────────────────────┐
│              run_pipeline.py (7 步)               │
│  1. 量子结构优化 (g16/ORCA)                        │
│  2. 格式转换 (.fchk/.molden → .mol2)               │
│  3. RESP 电荷计算                                  │
│  4. 拓扑生成 (Sobtop: .mol2+.chg → .itp+.gro)      │
│  5. 主拓扑 + itp 修订                              │
│  6. MDP 参数生成 (em/eq/prod)                      │
│  7. Packmol 初始盒子构建                            │
└────────────────────┬─────────────────────────────┘
                     │
┌────────────────────▼─────────────────────────────┐
│               GROMACS MD 模拟                      │
│    能量最小化 → NVT 平衡 → NPT 平衡 → 产出采样      │
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

```bash
cp .env.example .env
# 编辑 .env，填入你的 DeepSeek API Key
```

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

Agent 会给出方案确认，确认后自动执行全流程。

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

## 扩展分子

将 `.gjf` 文件放入 `struct/` 目录，系统自动识别为新分子（默认中性、GAFF 力场）。

如需精确参数（电荷、自旋、特殊基组），在 `docs/knowledge.md` 的分子表格中新增一行即可。

## 自定义上传

在 Web UI 中点击"上传结构"按钮，支持 `.gjf`、`.mol2`、`.pdb`、`.xyz` 格式。上传后系统自动刷新分子列表。

## 目录结构

```
AutomizedSimulations/
├── app.py                  ← Gradio Web UI
├── run_pipeline.py         ← 全流程编排 (7 步)
├── config.json             ← 体系配置
├── src/willy/             ← 核心 Python 包
│   ├── agent.py            ← LLM 交互核心
│   ├── knowledge_tools.py  ← 7 个 function calling 工具
│   ├── llm_config.py       ← 配置验证/写入
│   ├── env_checker.py      ← 依赖预检
│   ├── progress.py         ← 流水线进度
│   ├── quantum/            ← 量子化学 (g16/ORCA)
│   ├── topology/           ← 拓扑生成 (Sobtop)
│   └── simulation/         ← MD 模拟 (GROMACS)
├── vendor/                 ← 内置依赖 (Packmol, OpenBabel, Sobtop)
├── struct/                 ← 分子结构文件 (.gjf)
├── md_run/md_*/            ← 模拟产物
├── docs/                   ← 文档
│   ├── Willy.md           ← 架构文档
│   ├── knowledge.md        ← 分子知识库
│   └── ERR_WARN_Build.md   ← Error/Warning 协议
└── benchmarks/             ← LLM 评分测试
```

## 更多文档

- [架构设计文档](docs/Willy.md)
- [分子知识库](docs/knowledge.md)
- [Error/Warning 协议](docs/ERR_WARN_Build.md)

## 许可

待定
