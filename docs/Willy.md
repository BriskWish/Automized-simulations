# Willy — AI 驱动的 MD 模拟自动化

## 一、项目结构

```
AutomizedSimulations/
├── app.py                   ← Gradio UI (Willy Agent)
├── run_pipeline.py          ← CLI 入口：全流程编排 7 步 (产物→md_run/md_*/)
├── config.json              ← 体系唯一配置源
├── .env / .env.example      ← DeepSeek API key 配置
├── clean.sh                 ← 清理脚本 (保留 struct/, 清空 md_run/)
├── RESP_noopt.sh            ← RESP 电荷脚本

├── vendor/  (~6.8 MB)       ← 内置依赖
│   ├── packmol              ← Packmol 二进制
│   ├── obabel.bin           ← OpenBabel CLI
│   ├── libopenbabel.so.7    ← OpenBabel 共享库
│   ├── libcoordgen.so.3     ← coordgen 依赖库
│   ├── 3Dmol-min.js         ← 前端 3D 分子可视化
│   └── sobtop/              ← Sobtop 拓扑生成器 (GAFF)

├── struct/                  ← 量子计算输入/产物 (gjf/fchk/mol2/chg/molden)
├── md_run/md_*/             ← 流水线运行目录 (topo/mdp/box/GROMACS 产物)

├── benchmarks/
│   ├── llm_score.py         ← LLM 评分 (多用例并行测试)
│   └── precheck_examples.jsonl

├── src/willy/               ← 核心 Python 包
│   ├── _paths.py            ← get_project_root()
│   │
│   ├── agent_config.py      ← Layer 0: Config Agent (NL→config)
│   ├── agent_quantum.py     ← Layer 1: Quantum Agent (含 QUANTUM_AGENT_PROMPT)
│   ├── agent_topology.py    ← Layer 2: Topology Agent (含 TOPOLOGY_AGENT_PROMPT)
│   ├── agent_simulation.py  ← Layer 3: Simulation Agent (含 SIMULATION_AGENT_PROMPT)
│   ├── layer_agent.py       ← Agent 基类 (tool-calling 循环 + retry + escalation)
│   │
│   ├── toolist_global.py    ← Layer 0 工具定义 (10 tools) + handler
│   ├── toolist_quantum.py   ← Layer 1 工具定义 (8 tools) + handler
│   ├── toolist_topology.py  ← Layer 2 工具定义 (6 tools) + handler
│   ├── toolist_simulation.py← Layer 3 工具定义 (8 tools) + handler
│   │
│   ├── pipeline_orchestrator.py ← 7 步执行 + 失败→Agent 分发
│   ├── pipeline_state.py        ← 6 态状态机 (status.json 原子写入)
│   │
│   ├── errors.py            ← ErrorKind 枚举 + StepResult + RetryContext
│   ├── log_parsers.py       ← Gaussian/ORCA/GROMACS 输出结构化解析
│   ├── llm_config.py        ← config.json 验证/写入/默认值合并
│   ├── env_checker.py       ← 14 项外部依赖预检
│   ├── frontend_api.py      ← 前端专用后端 API (流水线控制/分子目录/3D 查看器/进度面板)
│   │
│   ├── quantum/             ← Layer 1 执行层: 量子化学计算
│   │   ├── struct_g16.py        ← Gaussian 结构优化 (gjf→g16→fchk)
│   │   ├── struct_orca.py       ← ORCA 结构优化 (gjf→inp→orca→molden)
│   │   ├── fchk_mol2.py         ← fchk→mol2 转换 (解析+Tripos 格式)
│   │   ├── singlepoint_g16.py   ← Gaussian 单点能 + ESP 计算
│   │   ├── singlepoint_orca.py  ← ORCA 单点能 (molden→SP→molden)
│   │   ├── chg_resp.py          ← RESP 电荷拟合 (Multiwfn 内置 ESP, ORCA-native)
│   │   └── _orca_utils.py       ← ORCA 路径解析工具 (共享)
│   │
│   ├── topology/            ← Layer 2 执行层: 力场拓扑生成
│   │   ├── topo_gaff.py         ← Sobtop GAFF (mol2+chg→itp+gro)
│   │   ├── topo_opls.py         ← LigParGen OPLS-AA (SMILES/mol2→itp+gro)
│   │   ├── top_assembly.py      ← 主拓扑组装 (atomtype 去重 + #include + topol.top)
│   │   └── itp_revise.py        ← ITP 后处理 (删除 [atomtypes] + RESNAME 替换)
│   │
│   └── simulation/          ← Layer 3 执行层: GROMACS MD 模拟
│       ├── mdp.py               ← MDP 参数生成 (em/eq/prod)
│       ├── box.py               ← Packmol 盒子构建 + auto_from_config
│       ├── setup.py             ← 文件收集器 (自动命名运行目录)
│       ├── em.py                ← 能量最小化执行 + 收敛检查
│       ├── eq.py                ← NPT 平衡执行 + 密度/温度收敛检查
│       ├── prod.py              ← 产出 MD 执行
│       └── _gmx_utils.py        ← grompp+mdrun 封装 + xvg 解析 + 收敛检查器

├── docs/                   ← 文档 (详见 docs/README.md 索引)
│   ├── Willy.md            ← 本文档
│   ├── knowledge.md         ← 分子知识库 (Agent TF-IDF 数据源)
│   ├── ERR_WARN_Build.md    ← Error/Warning 协议
│   ├── naming_convention.md ← 命名规范 (tools/toolist/agent)
│   ├── reconstruction.md    ← 重构检查清单 (6 关联方)
│   ├── quantum_design.md    ← 量子层设计文档
│   ├── topology_design.md   ← 拓扑层设计文档
│   ├── status_api.md        ← 前端接口文档
│   ├── employees.md         ← 团队分工与共识原则
│   ├── project_gap_analysis.md ← 项目完整性分析 (22 项发现)
│   └── lithium-salts.md     ← 锂盐体系调研

└── pyproject.toml           ← pip install -e .
```

---

## 二、全流程 Workflow

```
用户 NL → Willy Config Agent (DeepSeek v4-pro)
              │
    ┌─────────┴──────────┐
    │ 9 tools: tools_lookup_molecule, tools_resolve_compound,
    │ tools_lookup_md_defaults, tools_get_box_density,
    │ tools_lookup_basis_set, tools_refresh_structs,
    │ tools_diagnose_error_config, tools_validate_config,
    │ tools_set_backend_quantum
    └─────────┬──────────┘
              │
    ┌─────────▼──────────┐
    │ Error/Warning 协议  │  ERR_WARN_Build.md
    │  Error: invalid_molecule/ambiguous/invalid_value
    │  Warning: charge_imbalance/compute_heavy
    └─────────┬──────────┘
              │
    ┌─────────▼──────────┐
    │ run_pipeline.py    │  7 步, 产物→md_run/md_*/
    │ 1. struct_*        │  .gjf → g16/ORCA → 结构优化 + fchk/molden
    │ 2. fchk_mol2       │  .fchk/.molden → .mol2
    │ 3. singlepoint_*   │  Gaussian/ORCA 单点能 + ESP → .molden/.fchk
    │    + chg_resp       │  RESP 电荷拟合 → .chg
    │ 4. topo_*          │  .mol2+.chg → Sobtop/LigParGen → .itp+.gro
    │ 5. top_assembly    │  汇总 atomtype → topol.top + itp_revise
    │ 6. mdp             │  config.md → em/eq/prod.mdp
    │ 7. box             │  residues → Packmol → model.pdb
    └─────────┬──────────┘
              │
    ┌─────────▼──────────┐
    │ GROMACS MD         │  EM → NVT(100ps) → NPT EQ → PROD
    └────────────────────┘
```

量子化学后端支持 **Gaussian16**（默认）和 **ORCA**：
- `python3 run_pipeline.py` → 使用 g16
- `python3 run_pipeline.py orca` → 使用 ORCA

失败路径：对应层的 Agent 自动诊断→修复→重试（LLM on Failure Only）。

---

## 三、LLM Tools

### Layer 0 — Config Agent (10 tools)

| Tool | 功能 |
|------|------|
| `tools_lookup_molecule` | TF-IDF 向量检索 charge/spin/basis/别名 |
| `tools_resolve_compound` | LiTFSI→Li+TFSI, 硝酸锂→Li+NO3 |
| `tools_lookup_md_defaults` | 默认温度/时间/步长/热浴/压浴 |
| `tools_get_box_density` | 盒子密度 (ionic 6, mix 5, organic 4) |
| `tools_lookup_basis_set` | 按原子数推荐基组 |
| `tools_refresh_structs` | 重载 registry (上传新分子后) |
| `tools_diagnose_error_config` | 配置阶段错误诊断 |
| `tools_validate_config` | 验证草稿 config.json |
| `tools_set_backend_quantum` | 切换 g16/orca 后端 |
| `tools_skip_molecule_global` | 将分子加入跳过列表 |

### Layer 1 — Quantum Agent (8 tools)

`tools_retry_struct_g16` / `tools_retry_struct_orca` / `tools_retry_mol2_conversion` / `tools_retry_chg_g16` / `tools_retry_chg_orca` / `tools_diagnose_error_quantum` / `tools_modify_config_molecule` / `tools_skip_molecule_quantum`

### Layer 2 — Topology Agent (6 tools)

`tools_retry_topo_gaff` / `tools_retry_topo_opls` / `tools_retry_top_assembly` / `tools_diagnose_error_topology` / `tools_modify_config_topology` / `tools_skip_molecule_topology`

### Layer 3 — Simulation Agent (8 tools)

`tools_retry_mdp` / `tools_retry_box` / `tools_retry_em` / `tools_retry_eq` / `tools_retry_prod` / `tools_diagnose_error_simulation` / `tools_modify_config_simulation` / `tools_skip_molecule_simulation`

**总计: 32 tools**。命名规范见 `naming_convention.md`。

---

## 四、Error/Warning 协议

见 `ERR_WARN_Build.md`。LLM 输出统一 JSON：
```json
{"error": null, "warnings": [], "backend": "g16", "molecules": {...}, "residues": {...}, "md": {...}, "defaults": {...}}
```
Error type: invalid_molecule / invalid_value / ambiguous (阻塞)
Warning type: charge_imbalance / compute_heavy (非阻塞)

---

## 五、盒子密度

`box = ceil(∛(N / 6.0) × 10)` Å → 展示为 nm

---

## 六、运行方式

```bash
# 环境检查
python3 -m willy.env_checker

# Web UI
python3 app.py                          # → http://localhost:7860

# CLI 流水线
python3 run_pipeline.py                 # g16 后端 (默认)
python3 run_pipeline.py orca            # ORCA 后端

# 工具
bash clean.sh                           # 保留 struct/, 清空 md_run/
python3 benchmarks/llm_score.py         # LLM 评分测试
```

---

## 七、命名规范与架构演进

当前架构是本书面规范的三次迭代结果：

1. **初始**：14 个 `.py` 文件平铺在 `src/willy/` 根目录
2. **命名规范化**：统一为三套命名体系 — `tools_{op}_{target}_{scope}` (LLM tool 名) / `toolist_{scope}.py` (工具模块) / `agent_{scope}.py` (Agent 文件)。详见 `naming_convention.md`
3. **prompts 内聚**：`prompts.py` 拆分，每个 `agent_{scope}.py` 持有自己的 system prompt，`pipeline_orchestrator` 直接实例化 `QuantumAgent()`/`TopologyAgent()`/`SimulationAgent()`

**重构经验**见 `reconstruction.md`（6 关联方检查清单）。**已知缺口**见 `project_gap_analysis.md`（22 项发现）。

---

→ **[docs/README.md](README.md)** — 文档索引（按阅读目的导航 + 12 份文档分类 + 矛盾清单）
