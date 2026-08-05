# 分子动力学模拟知识库

> 供 AI Agent 在生成 `config.json` 时参考。分子识别、配置默认值和运行可启动性是不同层次的事实：本文件的表格提供识别元数据，实际执行仍以当前 `struct/` 中可复制的量子输入为准。

---

## 零、分子注册与扩展说明

本文档下方表格是 **AI Agent 分子知识库的权威数据源**。系统启动时，`_MoleculeRegistry` 会：

1. **正则解析下方表格**，提取每个分子的电荷、自旋、原子数、基组、力场、中文别名
2. **基于别名构建 TF-IDF 向量索引**，供 LLM 通过 `lookup_molecule` 工具进行语义检索
3. **扫描 `struct/` 目录下的 `.gjf` 文件**，自动注册未在表中出现的分子（默认参数：charge=0, spin=1, basis=b3lyp/6-311+g(d,p), forcefield=GAFF）

**如何扩展分子库：**

- **快速方式**：将 `.gjf` 文件放入 `struct/` 目录，系统自动识别（使用默认中性参数）
- **精确方式**：在下方表格新增一行，填写准确的电荷、自旋、基组等信息后，重启应用或调用 `refresh_structs` 工具

> ⚠️ 自动注册的分子默认 charge=0（中性），若实际为离子，请务必在表格中手动注册，否则 RESP 电荷计算和力场分配可能出错。

**输入就绪性（2026-08-05 核验）**：注册表可识别 `Li`、`TFSI`、`NO3`、`PF6`、`FEC`、`DME`、`DMM`、`EC`、`EMC`、`TTE` 与 `DMAA`，但“可识别”不代表可启动。当前 `struct/` 已有 `.gjf` 的表内组分为 `Li`、`NO3`、`PF6`、`FEC`、`EC`、`EMC`、`DMAA`；`TFSI`、`DME`、`DMM`、`TTE` 在加入配置前仍须提供对应 `.gjf`（或所选后端可复用的 Step 1 中间产物）。`struct/` 中未列入表格的文件会被自动注册为中性默认条目，使用前应补充准确元数据。

---

## 一、可用分子库

### 阳离子 (Cations)

| 分子 | 电荷 | 自旋 | 原子数 | 基组 | 力场 | 中文别名 |
|------|:---:|:---:|:---:|------|------|------|
| Li | +1 | 1 | 1 | b3lyp/6-311+g(d,p) | UFF | 锂离子、锂盐、锂、Li⁺ |

### 阴离子 (Anions)

| 分子 | 电荷 | 自旋 | 原子数 | 基组 | 力场 | 中文别名 |
|------|:---:|:---:|:---:|------|------|------|
| TFSI | -1 | 1 | 15 | b3lyp/6-311+g(d,p) | GAFF | 双三氟甲磺酰亚胺、TFSI⁻ |
| NO3 | -1 | 1 | 4 | b3lyp/6-311+g(d,p) | GAFF | 硝酸根、硝酸盐、硝酸、NO₃⁻ |
| PF6 | -1 | 1 | 7 | b3lyp/6-311+g(d,p) | GAFF | 六氟磷酸根、PF₆⁻ |

### 溶剂 (Solvents)

| 分子 | 电荷 | 自旋 | 原子数 | 基组 | 力场 | 中文别名 |
|------|:---:|:---:|:---:|------|------|------|
| FEC | 0 | 1 | 10 | b3lyp/6-311+g(d,p) | GAFF | 氟代碳酸乙烯酯、FEC 溶剂 |
| DME | 0 | 1 | 16 | b3lyp/6-311+g(d,p) | GAFF | 乙二醇二甲醚、二甲氧基乙烷 |
| DMM | 0 | 1 | 13 | b3lyp/6-311+g(d,p) | GAFF | 二甲氧基甲烷 |
| EC | 0 | 1 | 10 | b3lyp/6-311+g(d,p) | GAFF | 碳酸乙烯酯 |
| EMC | 0 | 1 | 14 | b3lyp/6-311+g(d,p) | GAFF | 碳酸甲乙酯 |
| TTE | 0 | 1 | 15 | b3lyp/6-311+g(d,p) | GAFF | 含氟醚 |
| DMAA | 0 | 1 | 12 | b3lyp/6-311+g(d,p) | GAFF | 二甲基乙酰胺、DMAC |

### JSON 映射规则（LLM 必须遵守）

用户说以下任何词 → 映射为 `molecules` 和 `residues` 的 key：
- "锂离子""锂盐""锂""Li""Li+" → **Li** (charge=1)
- "TFSI""TFSI-""双三氟甲磺酰亚胺" → **TFSI** (charge=-1)
- "硝酸根""硝酸盐""硝酸""NO3""NO3-" → **NO3** (charge=-1)
- "PF6""PF6-""六氟磷酸根" → **PF6** (charge=-1)
- "FEC""FEC溶剂""氟代碳酸乙烯酯" → **FEC** (charge=0)
- "DME""乙二醇二甲醚""二甲氧基乙烷" → **DME** (charge=0)
- "DMM""二甲氧基甲烷" → **DMM** (charge=0)
- "EC""碳酸乙烯酯" → **EC** (charge=0)
- "EMC""碳酸甲乙酯" → **EMC** (charge=0)
- "TTE""含氟醚" → **TTE** (charge=0)
- "DMAA""二甲基乙酰胺""DMAC" → **DMAA** (charge=0)

### 化合物自动拆分

以下化合物名需自动拆分为离子（计数均分或按化学式）：
- **LiTFSI** → Li + TFSI (各 1:1)
- **LiPF6** → Li + PF6 (各 1:1)
- **LiNO3** → Li + NO3 (各 1:1)
- **硝酸锂** → Li + NO3 (各 1:1)
- **六氟磷酸锂** → Li + PF6 (各 1:1)

例如: "50 LiTFSI" → residues: {"Li": 50, "TFSI": 50}
例如: "80个硝酸锂" → residues: {"Li": 80, "NO3": 80}

**添加新分子**：将 `.gjf` 放入 `struct/`，在上表中添加一行，并添加映射规则。`.mol2` 是 Step 2 的运行产物，不是新任务的必需输入。

---

## 二、力场与当前后端边界

| 后端配置 | force-field family | 当前用途 | 说明 |
|------|------|------|------|
| `sobtop` / `gaff_uff` | `gaff_uff` | 默认的小分子参数化 | Sobtop 以 GAFF 为先、UFF 补齐缺失原子类型或参数；这是一套 run 内一致的组合，不是可任意拼接的 AMBER 路径。 |
| `oplsaa` / `oplsaa` | `oplsaa` | LigParGen/BOSS OPLS-AA 参数化 | 需要外部 LigParGen 与 BOSS；同一 run 不能与 `gaff_uff` 混用。 |

运行时的唯一选择入口是 `config.json.topology`。当前不支持 `backend=amber`，也不存在 `gaff=True/False` 这一配置开关。分子表中的“力场”列仅用于识别和默认建议；最终参数化后端由整个 run 的 `topology` 段决定，详见 `topology_design.md`。

---

## 三、基组选择

| 体系 | 推荐 | 备选 |
|------|------|------|
| 有机分子 (<20 原子) | b3lyp/6-311+g(d,p) | b3lyp/def2TZVP |
| 有机分子 (>20 原子) | b3lyp/6-31g(d) | b3lyp/def2SVP |
| 离子 | b3lyp/6-311+g(d,p) | b3lyp/def2TZVP |
| 过渡金属 | b3lyp/def2TZVP | b3lyp/6-31+g(d) + ECP |

**当前默认**：`b3lyp/6-311+g(d,p)`（所有分子统一）。

---

## 四、RESP 电荷溶剂

| 关键词 | 含义 | 适用场景 |
|------|------|------|
| `gas` | 真空 | 聚合物体、中性有机分子 |
| `acetone` | 丙酮 (ε≈20.7) | 中等极性溶剂环境 |
| `water` | 水 (ε≈78.4) | 水溶液模拟 |
| `ethanol` | 乙醇 | 醇类溶剂 |

**当前默认**：`acetone`（libelectrolyte 常用）。修改：`config.json → molecules.{name}.solvent`。

---

## 五、MD 参数参考

### 通用

| 参数 | 默认值 | 说明 |
|------|:---:|------|
| dt | 0.001 ps (1 fs) | 含 Li⁺ 等高电荷密度离子建议 1fs |
| constraints | hbonds | LINCS 约束所有含 H 的键 |
| rcoulomb / rvdw | 1.0 nm | 非键截断 |
| coulombtype | PME | 长程静电 |
| DispCorr | EnerPres | 长程色散修正 |

### 热浴 (Thermostat)

| 参数 | 值 | 说明 |
|------|:---:|------|
| tcoupl | V-rescale | Bussi 速度重标定 |
| tau_t | 0.1-0.5 | τ_t=0.1 更紧，适合 NVT；τ_t=0.5 适合 NPT |
| ref_t | 298.15 K | 室温；高温模拟调至 350-400K |
| tc_grps | system | 单温度耦合组 |

### 压浴 (Barostat)

| 参数 | 值 | 说明 |
|------|:---:|------|
| pcoupl | C-rescale | 随机压浴，适合平衡 |
| pcoupltype | isotropic | 各向同性缩放 |
| tau_p | 1 (eq) / 2 (prod) | 越小耦合越紧 |
| compressibility | 8.5e-5 | 典型液体压缩系数 |

### 退火 (Annealing, eq 专用)

| 参数 | 值 | 说明 |
|------|:---:|------|
| annealing | single | 单组退火 |
| annealing_time | 0 1000 3000 4000 6000 7000 12000 | 时间节点 (ps) |
| annealing_temp | 298 500 500 400 400 298 298 | 对应温度 (K) |
| **关键** | 从 298K 起（非 0K） | 避免初始温度冲击 |

---

## 六、初始建盒密度

Step 7 的初始盒子使用质量密度，而不是分子数量估算。默认提示为“初始体积将由使用默认1.5g/cm3的密度猜测”。每个组分的分子质量从当前 run 的 `<residue>.itp` 中 `[ atoms ]` 的质量列计算；这使同一分子在不同参数化后仍以实际拓扑质量建盒。

**公式**：`V_nm3 = M_amu * 1.66053906660e-3 / rho_g_cm3`，`L_A = 10 * cbrt(V_nm3)`。

`model.inp` 以 `pbc L L L` 明确声明周期盒，`model.pdb` 的 `CRYST1` 是运行使用的实际盒矢量来源。目标质量密度只用于初始构型，不等同于 EQ 或 PROD 的平衡密度；真实边长、体积和初始质量密度会写入该 run 的 `md_manifest.json.box_attempts[]`。

### config.json 中 box 配置字段

| 字段 | 默认值 | 说明 |
|------|:---:|------|
| box.target_mass_density_g_cm3 | 1.5 | 初始目标质量密度 (g/cm3)，未设手动边长时由拓扑质量自动计算 |
| box.box_size | null | 手动指定立方盒边长 (Å)，优先于密度估算 |
| box.tolerance | 2.0 | Packmol 分子间最小容忍距离 (Å)，过小可能导致 packing 失败 |
| box.packing_number_density_nm3 | 仅历史兼容 | 旧运行快照字段；只有不存在目标质量密度时才执行 |

> 优先级：`box_size` > `target_mass_density_g_cm3` > 历史 `packing_number_density_nm3`。用户可显式覆盖初始质量密度；LLM 不得静默依据体系类型改写它。

---

## 七、工作流步骤依赖

```
config.json ──────────────────────────────────────────────────┐
    │                                                          │
    ▼                                                          │
[1] struct_g16/struct_orca: .gjf → 结构优化 → .fchk/.molden     │
[2] singlepoint_* + fchk_mol2: 单点能 → .mol2                  │
[3] chg_resp:      RESP 电荷计算 → .chg                         │
[4] topo_gaff/topo_opls: .mol2+.chg → .itp+.gro                │
[5] top_assembly + itp_revise: manifest → topol.top            │
[6] mdp:           md 参数 → em/eq/prod.mdp                    │
[7] box:           residues → Packmol 盒子 (model.pdb)         │
[8] em:            model.pdb → em.tpr/em.gro/em.xtc/em.edr     │
[9] eq:            em.gro → eq.tpr/eq.gro/eq.xtc/eq.edr        │
[10] prod:         eq.gro/eq.cpt → prod.tpr/prod.gro/prod.xtc/prod.edr │
    │                                                          │
    ▼                                                          │
   产物统一写入 md_run/<run_id>/                                │
    │                                                          │
    ▼                                                          │
   GROMACS: EM → 三点式退火 NPT EQ → PROD（由 manifest 严格串联） │
```

---

## 八、AI Agent 默认决策规则

1. **电荷/自旋**：从 molecules 表查询，未知分子询问用户
2. **基组**：<20 原子用 6-311+g(d,p)，≥20 用 6-31g(d)
3. **溶剂**：电池电解质用 acetone，水溶液用 water，不确定问用户
4. **温度**：电池模拟 298-350K，高温测试 400-500K
5. **dt**：含 Li⁺/Mg²⁺ 等高电荷离子 → 1fs；纯有机 → 2fs
6. **EQ**：默认 500/400/298 K 六段退火，总时长 10 ns；所有段为正且总长 7-100 ns
7. **PROD**：用户独立指定 2-200 ns；温度必须等于 EQ 目标温度，默认 10 ns，并只从已验收 EQ checkpoint 启动
8. **初始建盒**：默认以拓扑质量和 `1.5 g/cm3` 估算；用户指定边长或目标质量密度优先。实际 PBC 盒矢量必须由 Packmol 输出审计后再交给 GROMACS
9. **力场**：新 run 默认使用 `sobtop/gaff_uff`；需要 OPLS-AA 时显式选择 `oplsaa/oplsaa` 并满足 LigParGen/BOSS 依赖。不得在同一 run 混用两类 family。
10. **冲突处理**：用户指定 > 知识库推荐 > 默认值
11. **用户上传分子**：`struct/` 下的自上传 `.gjf` 文件会以默认中性参数（charge=0, spin=1, GAFF 力场, b3lyp/6-311+g(d,p)）自动注册。若分子实际为离子或需特殊基组，用户需在 knowledge.md 表格中手动注册

---

## 九、运行诊断证据状态

截至 2026-08-05，旧的 `md__202608010003` 工作区已按 run 保留策略清理，仓库中不再保留其原始轨迹、日志和 manifest。因此此前围绕该 run 的 EQ/NVT 数值描述不再作为“已验证运行经验”供 Agent 或人工决策引用。

当前规则是：只有当同一 run 的受控状态、manifest 与分析产物仍可读取，且结论的输入、版本和验收条件可追溯时，才可在本节登记为诊断经验。否则只能作为外部或历史参考，不能放宽 EM/EQ/PROD 阶段许可、改变默认协议或替代目标体系的科学验收。
