# 分子动力学模拟知识库

> 供 AI Agent 在生成 config.json 时参考。以下信息基于当前工作流已验证的配置。

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

**添加新分子**：将 `.gjf` + `.mol2` 放入 `struct/`，在上表中添加一行，并添加映射规则。

---

## 二、力场

| 力场 | 适用 | Sobtop 选项 | JSON 配置 |
|------|------|:---:|------|
| **GAFF** | 有机分子、溶剂 | gaff=True（默认） | — |
| AMBER | 蛋白、核酸 | gaff=False | — |
| **UFF** | 金属离子、GAFF 不支持的元素 | Sobtop 自动退选 | — |

当前策略：**GAFF 优先，无法匹配的退 UFF**。混合力场下 atomtype 命名：小写=GAFF，UFF_前缀=UFF。

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

## 六、盒子密度

| 体系类型 | 密度 (分子/nm³) | 示例 |
|------|:---:|------|
| 离子液体 (纯) | 6 | LiTFSI, 100Li+100TFSI → 33Å |
| 离子 + 溶剂混合 | 5 | 含 DME/DMM/FEC |
| 溶剂 (中性有机) | 4 | 纯 EC/DMC/DME |
| 水溶液 | 3 | 蛋白+水 |

**公式**：`box = ceil(∛(N / density) × 10)` Å

### config.json 中 box 配置字段

| 字段 | 默认值 | 说明 |
|------|:---:|------|
| box.density | 6.0 | 分子填充密度 (分子/nm³)，用于自动计算盒子边长 |
| box.box_size | null | 手动指定盒子边长 (Å)，设为 null 则由密度自动计算 |
| box.tolerance | 2.0 | Packmol 分子间最小容忍距离 (Å)，过小可能导致 packing 失败 |

> `box_size` 非 null 时优先于 `density`，适合需要精确盒子尺寸的场景。

---

## 七、工作流步骤依赖

```
config.json ──────────────────────────────────────────────────┐
    │                                                          │
    ▼                                                          │
[1] struct_maker: .gjf → g16/ORCA → 结构优化 + formchk         │
[2] mol2_maker:   .fchk/.molden → .mol2                        │
[3] chg_maker:    RESP 电荷计算 → .chg                          │
[4] sobtop_interface: .mol2+.chg → Sobtop → .itp+.gro          │
[5] top_maker:    汇总 atomtype → topol.top + itp_reviser       │
[6] mdp_maker:    md 参数 → em/eq/prod.mdp                      │
[7] inp_generator: residues → Packmol 盒子 (model.pdb)         │
    │                                                          │
    ▼                                                          │
   产物统一写入 md_run/md_*/                                   │
    │                                                          │
    ▼                                                          │
   GROMACS: EM → NVT → NPT EQ → PROD (手动或脚本控制)          │
```

---

## 八、AI Agent 默认决策规则

1. **电荷/自旋**：从 molecules 表查询，未知分子询问用户
2. **基组**：<20 原子用 6-311+g(d,p)，≥20 用 6-31g(d)
3. **溶剂**：电池电解质用 acetone，水溶液用 water，不确定问用户
4. **温度**：电池模拟 298-350K，高温测试 400-500K
5. **dt**：含 Li⁺/Mg²⁺ 等高电荷离子 → 1fs；纯有机 → 2fs
6. **eq_ns**：离子液体 5-10ns，纯溶剂 1-2ns
7. **prod_ns**：用户指定；默认 10ns
8. **盒子密度**：离子液体 6/nm³；离子+溶剂 5/nm³；纯有机 4/nm³
9. **力场**：有机分子用 GAFF，金属离子自动 UFF
10. **冲突处理**：用户指定 > 知识库推荐 > 默认值
11. **用户上传分子**：`struct/` 下的自上传 `.gjf` 文件会以默认中性参数（charge=0, spin=1, GAFF 力场, b3lyp/6-311+g(d,p)）自动注册。若分子实际为离子或需特殊基组，用户需在 knowledge.md 表格中手动注册

---

