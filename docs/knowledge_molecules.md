# 分子知识库

本文件是 Config Agent 的分子识别数据源。表格用于名称、别名、默认电荷、自旋、基组和力场建议；可执行性仍取决于所选后端的原始量子输入和启动前审计。

## 一、注册规则

`_MoleculeRegistry` 解析下方表格、上传登记和 `struct/` 中的原始输入。新分子应同时提供：

1. 与后端匹配的原始输入：G16/G09 使用 `.gjf`，ORCA 使用 `.inp`。
2. 文件内正确的电荷、自旋和有限坐标。
3. 表格中的规范名称与别名；带电写法只作别名，不作文件 key。

自动发现的文件不会推断电荷、自旋或基组。文件内电荷和自旋高于知识库默认值；不一致时阻止方案确认和运行启动。

用户上传只接受可审计的 `.gjf` 或 `.inp`。上传通道从文件中验证坐标、电荷和自旋后，生成以核心文件名命名的规范化原始输入；原文件中的方法、资源和其他指令不继承，基组、`mem` 与 `nproc` 由候选方案和确认启动时的默认资源规则控制。`.mol2`、`.pdb`、`.xyz` 没有统一可验证的自旋语义，不能作为量子原始输入直接登记。

## 二、分子表

### 阳离子

| 分子 | 电荷 | 自旋 | 原子数 | 基组 | 力场 | 中文别名 |
|------|:---:|:---:|:---:|------|------|------|
| Li | +1 | 1 | 1 | b3lyp/6-311+g(d,p) | UFF | 锂离子、锂盐、锂、Li+ |
| Na | +1 | 1 | 1 | b3lyp/6-311+g(d,p) | UFF | 钠离子、钠、Na+ |

### 阴离子

| 分子 | 电荷 | 自旋 | 原子数 | 基组 | 力场 | 中文别名 |
|------|:---:|:---:|:---:|------|------|------|
| TFSI | -1 | 1 | 15 | b3lyp/6-311+g(d,p) | GAFF | 双三氟甲磺酰亚胺、TFSI- |
| NO3 | -1 | 1 | 4 | b3lyp/6-311+g(d,p) | GAFF | 硝酸根、硝酸盐、硝酸、NO3- |
| PF6 | -1 | 1 | 7 | b3lyp/6-311+g(d,p) | GAFF | 六氟磷酸根、PF6- |
| AsF6 | -1 | 1 | 7 | b3lyp/6-311+g(d,p) | GAFF | 六氟砷酸根、AsF6- |
| BCN4 | -1 | 1 | 9 | b3lyp/6-311+g(d,p) | GAFF | 四氰基硼酸根、B(CN)4- |
| BF4 | -1 | 1 | 5 | b3lyp/6-311+g(d,p) | GAFF | 四氟硼酸根、BF4- |
| BOB | -1 | 1 | 13 | b3lyp/6-311+g(d,p) | GAFF | 双草酸硼酸根、BOB- |
| CF3SO3 | -1 | 1 | 8 | b3lyp/6-311+g(d,p) | GAFF | 三氟甲基磺酸根、三氟甲磺酸根、OTf-、CF3SO3- |
| ClO4 | -1 | 1 | 5 | b3lyp/6-311+g(d,p) | GAFF | 高氯酸根、ClO4- |
| DFOB | -1 | 1 | 9 | b3lyp/6-311+g(d,p) | GAFF | 二氟草酸硼酸根、DFOB- |
| FSI | -1 | 1 | 9 | b3lyp/6-311+g(d,p) | GAFF | 双氟磺酰亚胺、FSI- |
| FTFSI | -1 | 2 | 13 | b3lyp/6-311+g(d,p) | GAFF | 氟磺酰三氟甲磺酰亚胺根、FTFSI- |
| PO2F2 | -1 | 1 | 5 | b3lyp/6-311+g(d,p) | GAFF | 二氟磷酸根、PO2F2- |
| TFOP | -1 | 1 | 11 | b3lyp/6-311+g(d,p) | GAFF | 四氟草酸磷酸根、TFOP- |
| TFSM | -1 | 1 | 22 | b3lyp/6-311+g(d,p) | GAFF | 三三氟甲磺酰甲基负离子、TFSM- |

### 溶剂

| 分子 | 电荷 | 自旋 | 原子数 | 基组 | 力场 | 中文别名 |
|------|:---:|:---:|:---:|------|------|------|
| FEC | 0 | 1 | 10 | b3lyp/6-311+g(d,p) | GAFF | 氟代碳酸乙烯酯、FEC溶剂 |
| DME | 0 | 1 | 16 | b3lyp/6-311+g(d,p) | GAFF | 乙二醇二甲醚、二甲氧基乙烷 |
| DMM | 0 | 1 | 13 | b3lyp/6-311+g(d,p) | GAFF | 二甲氧基甲烷 |
| EC | 0 | 1 | 10 | b3lyp/6-311+g(d,p) | GAFF | 碳酸乙烯酯 |
| EMC | 0 | 1 | 15 | b3lyp/6-311+g(d,p) | GAFF | 碳酸甲乙酯 |
| TTE | 0 | 1 | 15 | b3lyp/6-311+g(d,p) | GAFF | 含氟醚 |
| ADN | 0 | 1 | 16 | b3lyp/6-311+g(d,p) | GAFF | 己二腈、ADN |
| AN | 0 | 1 | 6 | b3lyp/6-311+g(d,p) | GAFF | 乙腈、AN |
| BC | 0 | 1 | 16 | b3lyp/6-311+g(d,p) | GAFF | 碳酸丁烯酯、BC |
| BTFE | 0 | 1 | 15 | b3lyp/6-311+g(d,p) | GAFF | 双2,2,2三氟乙基醚、BTFE |
| DEC | 0 | 1 | 18 | b3lyp/6-311+g(d,p) | GAFF | 碳酸二乙酯、DEC |
| DFEA | 0 | 1 | 14 | b3lyp/6-311+g(d,p) | GAFF | 二氟乙酸乙酯、DFEA |
| DFEC | 0 | 1 | 18 | b3lyp/6-311+g(d,p) | GAFF | 二氟碳酸乙烯酯、DFEC |
| DGM | 0 | 1 | 23 | b3lyp/6-311+g(d,p) | GAFF | 二乙二醇二甲醚、DGM、diglyme |
| DMC | 0 | 1 | 12 | b3lyp/6-311+g(d,p) | GAFF | 碳酸二甲酯、DMC |
| DOL | 0 | 1 | 11 | b3lyp/6-311+g(d,p) | GAFF | 1,3二氧戊环、DOL |
| DXA | 0 | 1 | 14 | b3lyp/6-311+g(d,p) | GAFF | 二氧六环、DXA、1,4二氧六环 |
| EA | 0 | 1 | 14 | b3lyp/6-311+g(d,p) | GAFF | 乙酸乙酯、EA |
| EB | 0 | 1 | 20 | b3lyp/6-311+g(d,p) | GAFF | 丁酸乙酯、EB |
| EP | 0 | 1 | 17 | b3lyp/6-311+g(d,p) | GAFF | 丙酸乙酯、EP |
| FEMC | 0 | 1 | 15 | b3lyp/6-311+g(d,p) | GAFF | 氟代碳酸甲乙酯、FEMC |
| GVL | 0 | 1 | 15 | b3lyp/6-311+g(d,p) | GAFF | 戊内酯、GVL |
| MA | 0 | 1 | 11 | b3lyp/6-311+g(d,p) | GAFF | 乙酸甲酯、MA |
| MB | 0 | 1 | 17 | b3lyp/6-311+g(d,p) | GAFF | 丁酸甲酯、MB |
| MPC | 0 | 1 | 18 | b3lyp/6-311+g(d,p) | GAFF | 碳酸甲丙酯、MPC |
| MP | 0 | 1 | 14 | b3lyp/6-311+g(d,p) | GAFF | 丙酸甲酯、MP |
| PA | 0 | 1 | 17 | b3lyp/6-311+g(d,p) | GAFF | 乙酸丙酯、PA |
| PC | 0 | 1 | 13 | b3lyp/6-311+g(d,p) | GAFF | 碳酸丙烯酯、PC |
| PFPN | 0 | 1 | 19 | b3lyp/6-311+g(d,p) | GAFF | 乙氧基五氟环三磷腈、PFPN |
| PP | 0 | 1 | 20 | b3lyp/6-311+g(d,p) | GAFF | 丙酸丙酯、PP |
| SN | 0 | 1 | 10 | b3lyp/6-311+g(d,p) | GAFF | 丁二腈、SN |
| T3GM | 0 | 1 | 30 | b3lyp/6-311+g(d,p) | GAFF | 三乙二醇二甲醚、T3GM、triglyme |
| T4GM | 0 | 1 | 37 | b3lyp/6-311+g(d,p) | GAFF | 四乙二醇二甲醚、T4GM、tetraglyme |
| TEP | 0 | 1 | 26 | b3lyp/6-311+g(d,p) | GAFF | 磷酸三乙酯、TEP |
| THF | 0 | 1 | 13 | b3lyp/6-311+g(d,p) | GAFF | 四氢呋喃、THF |
| TMC | 0 | 1 | 13 | b3lyp/6-311+g(d,p) | GAFF | 三亚甲基碳酸酯、TMC |
| TMP | 0 | 1 | 17 | b3lyp/6-311+g(d,p) | GAFF | 磷酸三甲酯、TMP |

## 三、盐与别名规则

- `LiTFSI` -> `Li` + `TFSI`，比例 1:1。
- `LiPF6` -> `Li` + `PF6`，比例 1:1。
- `LiNO3`、硝酸锂 -> `Li` + `NO3`，比例 1:1。
- 六氟磷酸锂 -> `Li` + `PF6`，比例 1:1。

用户明确给出的分子数优先于默认比例。未知、歧义或电荷不平衡的描述必须向用户确认，不能自行补齐组分。

## 四、参数化边界

| 后端 | 配置 | 说明 |
|---|---|---|
| Sobtop | `sobtop/gaff_uff` | 默认小分子路径。 |
| OPLS-AA | `oplsaa/oplsaa` | 需 LigParGen、BOSS、完整 Open Babel 与 C shell；不能与 GAFF/UFF 混用。 |

分子表的力场列仅用于识别和默认建议。最终后端由 `config.json.topology` 决定，详见 `topology.md`。

## 五、默认计算规则

| 项目 | 默认规则 |
|---|---|
| 基组 | 默认 `b3lyp/6-311+g(d,p)`；大分子或特殊体系由用户明确指定后重新审计。 |
| RESP 溶剂 | 默认 `acetone`；可选 `gas`、`water`、`ethanol`。 |
| 时间步长 | `0.001 ps`；更大步长是独立协议变更。 |
| 初始建盒 | 按实际拓扑质量和 `0.7 g/cm3` 估算；显式 `box_size` 优先。 |
| 温度协议 | 默认三点式退火 EQ，生产阶段温度必须等于 EQ 目标温度。 |

## 六、工作流依赖

```text
原始量子输入 -> 优化 -> 单点/MOL2 -> RESP CHG -> ITP/GRO
  -> topol.top -> MDP -> Packmol -> EM -> EQ -> PROD
```

每一步都依赖当前 run 的已验证上游产物。阶段许可和产物契约由确定性代码维护，知识库不绕过这些门禁。

## 七、盒子规则

未指定边长时，使用：

```text
V_nm3 = M_amu * 1.66053906660e-3 / rho_g_cm3
L_A = 10 * cbrt(V_nm3)
```

`model.inp` 必须声明 `pbc L L L`，输出 `model.pdb` 的 `CRYST1` 是实际盒矢量来源。目标质量密度只用于初始构型，不等同于平衡或生产阶段的体系性质。

## 八、Agent 决策顺序

用户明确要求 > 通过输入审计的原始文件 > 知识库规则 > 系统默认值。任何冲突、未知输入或不支持的力场组合都必须阻止自动启动并请求澄清。

## 九、诊断使用边界

知识库可用于解释名称、参数和受限建议，不能作为外部工具成功、科学收敛、产物有效或目标体系性质的证据。运行诊断必须依据当前 run 的公开状态和已验证产物；GROMACS 错误条目见 `knowledge_mdrun.md`。

## 十、已上传结构

该区段由上传通道维护，只暴露可执行结构的核心文件名。文件格式、电荷和自旋由 `struct/.willy_uploaded_structures.json` 记录，每次启动仍以原始量子输入审计为准。

<!-- WILLY_UPLOADED_STRUCTURES_START -->
| 核心文件名 |
|---|
| BMA |
<!-- WILLY_UPLOADED_STRUCTURES_END -->
