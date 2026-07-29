# Willy — AI 驱动的 MD 模拟自动化

## 一、项目结构

```
AutomizedSimulations/
├── app.py                   ← Gradio UI (Willy Agent)
├── run_pipeline.py          ← 全流程编排 7 步 (产物→md_run/md_*/)
├── config.json              ← 体系唯一配置源
├── knowledge.md             ← 11 分子 + 力场/基组/MD 参数知识库
├── ERR_WARN_Build.md        ← Error/Warning 协议规范
├── README.md                ← 项目门面
├── .env.example             ← API key 配置模板
├── .env                     ← DeepSeek API key (不纳入版本控制)
├── clean.sh                 ← 清理脚本 (保留 struct/, 清空 md_run/)
├── RESP_noopt.sh            ← RESP 电荷脚本
├── prompt.txt               ← 每次 LLM 调用的 prompt 存档
│
├── vendor/  (~6.8 MB)       ← 内置依赖
│   ├── packmol              ← Packmol 二进制文件
│   ├── obabel.bin           ← OpenBabel CLI
│   ├── libopenbabel.so.7    ← OpenBabel 共享库
│   ├── libcoordgen.so.3     ← OpenBabel 依赖库 (coordgen)
│   ├── 3Dmol-min.js         ← 前端 3D 分子可视化
│   └── sobtop/              ← Sobtop 拓扑生成器
│       ├── sobtop, atomtype ← 可执行文件
│       ├── sobtop.ini       ← 配置文件
│       ├── LJ_param.dat     ← GAFF LJ 参数
│       └── bonded_param.dat ← GAFF 键合参数
│
├── struct/                  ← 量子计算输入/产物 (gjf/fchk/mol2/chg)
├── md_run/md_*/             ← 流水线运行目录 (topo/mdp/box/GROMACS)
│
├── benchmarks/
│   ├── llm_score.py         ← LLM 评分 (多用例并行测试)
│   └── precheck_examples.jsonl
│
├── src/willy/
│   ├── _paths.py            ← get_project_root()
│   ├── env_checker.py       ← 14 项依赖预检
│   ├── progress.py          ← 文件系统扫描进度报告
│   ├── errors.py            ← ErrorKind 枚举
│   ├── agent.py             ← NL→config→run (LLM 交互核心)
│   ├── llm_config.py        ← config.json 验证/写入/默认值
│   ├── knowledge_tools.py   ← 7 tools + TF-IDF 向量检索 + system prompt
│   ├── quantum/             ← struct_maker, mol2_maker, chg_maker (g16 + ORCA)
│   ├── topology/            ← sobtop_interface, top_maker, itp_reviser
│   └── simulation/          ← mdp_maker, inp_generator, md_setup, md_em/eq/prod, _md_utils
│
├── docs/
│   ├── Willy.md            ← 本文档
│   ├── knowledge.md         ← 分子知识库 (Agent 数据源)
│   └── ERR_WARN_Build.md    ← Error/Warning 协议
│
└── pyproject.toml           ← pip install -e .
```

## 二、全流程 Workflow

```
用户 NL → Willy Agent (DeepSeek v4-pro)
              │
    ┌─────────┴──────────┐
    │ 7 tools + system prompt
    │ lookup_molecule, resolve_compound, lookup_md_defaults,
    │ get_box_density, lookup_basis_set, refresh_structs, diagnose_error
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
    │ 1. struct_maker    │  .gjf → g16/ORCA → 结构优化 + formchk
    │ 2. mol2_maker      │  .fchk/.molden → .mol2
    │ 3. chg_maker       │  RESP 电荷 → .chg
    │ 4. sobtop_iface    │  .mol2+.chg → Sobtop → .itp+.gro
    │ 5. top_maker       │  → topol.top + itp_reviser
    │ 6. mdp_maker       │  → em/eq/prod.mdp
    │ 7. inp_generator   │  Packmol → model.pdb
    └─────────┬──────────┘
              │
    ┌─────────▼──────────┐
    │ GROMACS MD         │  EM → NVT(100ps) → NPT EQ → PROD(手动)
    └────────────────────┘
```

量子化学后端支持 **Gaussian16**（默认）和 **ORCA**：
- `python3 run_pipeline.py` → 使用 g16，产物 `.fchk`
- `python3 run_pipeline.py orca` → 使用 ORCA，产物 `.molden`

## 三、LLM Tools (7)

| Tool | 功能 |
|------|------|
| `lookup_molecule` | TF-IDF 向量检索 charge/spin/basis/别名 |
| `resolve_compound` | LiTFSI→Li+TFSI, 硝酸锂→Li+NO3 |
| `lookup_md_defaults` | 默认温度/时间/步长/热浴/压浴 |
| `get_box_density` | 盒子密度 (ionic 6, mix 5, organic 4) |
| `lookup_basis_set` | 按原子数推荐基组 |
| `refresh_structs` | 重载 registry (上传新分子后) |
| `diagnose_error` | 错误诊断 |

向量检索: sklearn TF-IDF char n-gram, knowledge.md 表格→自动提取, struct/*.gjf→自动注册。

## 四、Error/Warning 协议

见 `ERR_WARN_Build.md`。LLM 输出统一 JSON:
```json
{"error": null, "warnings": [], "molecules": {...}, "residues": {...}, "md": {...}, "defaults": {...}}
```
Error type: invalid_molecule / invalid_value / ambiguous (阻塞)
Warning type: charge_imbalance / compute_heavy (非阻塞)

## 五、盒子密度

`box = ceil(∛(N / 6.0) × 10)` Å → 展示为 nm

## 六、运行方式

```bash
# 环境检查
python3 -m willy.env_checker           # 检查所有依赖

# Web UI
python3 app.py                          # 启动 Gradio UI → http://localhost:7860

# CLI 流水线
python3 run_pipeline.py                 # g16 后端 (默认)
python3 run_pipeline.py orca            # ORCA 后端

# 工具
bash clean.sh                           # 保留 struct/, 清空 md_run/
python3 benchmarks/llm_score.py         # LLM 评分测试
```
