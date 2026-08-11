# 量子层设计文档

> Willy 量子计算层架构文档。两步法：结构优化 (DZ) → 单点能 (TZ) → mol2 + chg。
> 最后更新: 2026-08-11

本版本已对 G16、ORCA 量子输入及其已登记 profile 完成十步链路验收。G09 接口仍保留，
但当前未获得授权进行可靠全链路验证，不计入本版本通过 profile。方案助理欢迎气泡必须
明确提示：“G09 未经可靠全链路验证，使用时可能出现运行问题。”

---

## 1. 文件架构

```
src/willy/quantum/
├── struct_g16.py          Step 1: G16 结构优化 → .fchk
├── struct_g09.py          Step 1: G09 结构优化 → .fchk（独立镜像链路）
├── struct_orca.py         Step 1: ORCA 结构优化 → .molden
├── singlepoint_g16.py     Step 2a: .fchk → g16 SP(def2TZVP) → *_opt.fchk
├── singlepoint_g09.py     Step 2a: .fchk → g09 SP(def2TZVP) → *_opt.fchk（独立镜像链路）
├── singlepoint_orca.py    Step 2a: .molden → ORCA SP(def2-TZVP) → Multiwfn → *_opt.fchk
├── fchk_mol2.py           Step 2b: *_opt.fchk → .mol2（G16/G09）
├── molden_mol2.py         Step 2b: ORCA *_opt.molden → .mol2（Multiwfn 键连接/Mayer 键级）
├── chg_resp.py            Step 3:  *_opt.fchk → Multiwfn RESP(内部ESP) → .chg (G16+G09+ORCA 统一)
├── _orca_utils.py         ORCA 路径解析 + Multiwfn 工具 + 坐标提取/格式化
├── input_audit.py         G16/G09/ORCA 原始输入的受限解析、完整性与电荷审计
└── __init__.py
```

## 2. 两步法链路

```
Step 1: 结构优化 (DZ)
  G16:  .gjf → g16 Opt(6-311+g(d,p)) → formchk → {name}.fchk
  G09:  .gjf → g09 Opt(6-311+g(d,p)) → G09 formchk → {name}.fchk
  ORCA: .inp → ORCA Opt(用户输入关键词) → orca_2mkl → {name}.molden
        单原子内嵌 xyz 的 Opt 无优化自由度，私有临时输入将 Opt 改为 SP，
        生成 .gbw 后仍经 orca_2mkl 进入相同的 {name}.molden 契约；原始 .inp 不改写

Step 2a: 单点能 (TZ)
  G16:  {name}.fchk → 提取坐标 → {name}_opt.gjf → g16 SP(def2TZVP) → formchk → {name}_opt.fchk
  G09:  {name}.fchk → 提取坐标 → {name}_opt.gjf → g09 SP(def2TZVP) → G09 formchk → {name}_opt.fchk
  ORCA: {name}.molden → 提取坐标 → {name}_opt.inp → ORCA SP(def2-TZVP) → orca_2mkl
        → {name}_opt.molden → Multiwfn molden→fchk → {name}_opt.fchk

Step 2b: mol2 生成
  G16/G09: {name}_opt.fchk → fchk_mol2.convert() → {name}.mol2
  ORCA: {name}_opt.molden → 内置 Multiwfn 连通性 + Mayer 键级分析
        → molden_mol2.convert() → {name}.mol2
        同名 {name}_opt.fchk 保留给下一步 RESP，不承担键连接传递。

Step 3: RESP 电荷
  G16+G09+ORCA 统一: {name}_opt.fchk → 内置 Multiwfn RESP(7→18→2→y→0→0→q) → {name}.chg
```

启动预检会优先冻结已有的 Step 1 中间产物（G16/G09 为 `.fchk`，ORCA 为
`.molden`）到本次 `md_run/<run_id>/`；存在时 Step 1 跳过该分子。若没有
中间产物但存在所选后端对应的 `.gjf`（G16/G09）或 `.inp`（ORCA），这是正常新任务，
会静默进入 Step 1 优化。只有原始输入缺失时才拒绝启动。

### 2.1 算力配置

`config.json.defaults.nproc` 是量子与本地 GROMACS 主计算的 CPU 默认值，默认 `8`；
`molecules.<name>.nproc` 可以覆盖单个分子的 G16/G09/ORCA 结构优化和单点。
`defaults.mem` 与分子级 `mem` 同样传给 Gaussian；ORCA 运行副本将其换算为
每核的 `%maxcore`，并与 `%pal nprocs ... end` 一起写入。原始 `.gjf/.inp`
始终不改写，资源指令只出现在私有运行输入中。

## 3. 各模块职能

| 模块 | 输入 | 输出 | 外部依赖 | 设计要点 |
|------|------|------|------|------|
| `struct_g16` | `.gjf` + config | `.fchk` | g16, formchk | 支持 Agent 重试覆盖基组/SCF/OPT 参数 |
| `struct_g09` | `.gjf` + config | `.fchk` | g09, g09_formchk | G09 独立镜像；支持 Agent 重试覆盖基组/SCF/OPT 参数 |
| `struct_orca` | `.inp` 原始输入 | `.molden` | ORCA, orca_2mkl | 保留用户 ORCA 关键词；不从 `.gjf` 转写；单原子 `Opt` 私有降为 `SP` 以产出后续所需波函数 |
| `singlepoint_g16` | `.fchk` (DZ) | `*_opt.fchk` (TZ) | g16, formchk, 内置 Multiwfn | 提取坐标 → `*_opt.gjf` → SP → formchk |
| `singlepoint_g09` | `.fchk` (DZ) | `*_opt.fchk` (TZ) | g09, g09_formchk, 内置 Multiwfn | G09 独立镜像；提取坐标 → `*_opt.gjf` → SP → formchk |
| `singlepoint_orca` | `.molden` (DZ) | `*_opt.molden`、`*_opt.fchk` (TZ) | ORCA, orca_2mkl, 内置 Multiwfn | Molden 保留键连接分析来源；FCHK 仅供 RESP |
| `fchk_mol2` | `*_opt.fchk` | `.mol2` | 无 | G16/G09 的纯 Python FCHK 解析器 |
| `molden_mol2` | ORCA `*_opt.molden` | `.mol2` | 内置 Multiwfn | 解析原子顺序，以连通性筛选键、Mayer 键级确定单/芳香/双/三键；单原子允许零键 |
| `chg_resp` | `*_opt.fchk` | `.chg` | 内置 Multiwfn | G16+G09+ORCA 完全统一，MultiWfn 内部 ESP |
| `_orca_utils` | — | — | 内置 Multiwfn | `find_orca`, `find_multiwfn`, `extract_xyz`, `format_orca_xyz_coords`, `get_orca_env` |

## 4. 设计原则

- **两步法**: DZ 优化求速度，TZ 单点求精度。两步不可合并——DZ 优化的 fchk 不能直接做 SP（需换基组重建输入），产物同名会覆盖
- **`*_opt` 命名**: Step 2a 的 SP 产物统一 `{name}_opt.*`，防止覆盖 Step 1 产物。最终 `.mol2` / `.chg` 保持原名（Sobtop 兼容）
- **ORCA 键连接与 RESP 分离**: ORCA 的 FCHK 不含 Gaussian `MxBond/NBond/IBond/RBond`；因此 `molden_mol2` 只消费 `*_opt.molden`，`chg_resp` 仍消费 `*_opt.fchk`
- **无 shell RESP 依赖**: G16 不再调用外部 RESP 脚本，SP 由 `singlepoint_g16` 完成
- **统一环境解析**: 外部可执行文件与内置 Multiwfn 均通过 `env_registry` 解析；后者固定项目
  vendor 路径，不接受环境变量或 PATH 覆盖
- **统一 StepResult**: 所有函数返回 `StepResult`，错误用 `ErrorKind` 枚举
- **资源配置集中**: 量子步骤和本地 GROMACS 读取同一 `defaults.nproc`，避免单点或
  MD 阶段悄悄回退到另一套核数；实际命令仍由各后端生成私有运行参数
- **受管科学进程**: G16、G16 formchk、G09、G09 formchk、ORCA、orca_2mkl 和内置 Multiwfn 均通过
  `run_managed_command()` 在独立进程组中运行。当前 run 有停止请求或命令超时时，
  统一按 `SIGINT -> SIGTERM -> SIGKILL` 结束整个进程组，并在 run 私有
  `process_lifecycle.jsonl` 记录脱敏命令、原因、信号和退出码；正常完成不新增该审计记录。
  调用方继续接收 `CompletedProcess` 或 `TimeoutExpired`，并映射为既有 `StepResult` 错误契约。

## 5. 依赖

| 依赖 | 路径 | 使用者 |
|------|------|------|
| Gaussian 16 | `WILLY_G16_BIN` 或 PATH | `struct_g16`, `singlepoint_g16` |
| Gaussian 16 formchk | `WILLY_FORMCHK_BIN` 或 PATH | `struct_g16`, `singlepoint_g16` |
| Gaussian 09 | `WILLY_G09_BIN` 或 PATH | `struct_g09`, `singlepoint_g09` |
| Gaussian 09 formchk | `WILLY_G09_FORMCHK_BIN` 或 PATH | `struct_g09`, `singlepoint_g09` |
| ORCA 6.x | `WILLY_ORCA_HOME` / `WILLY_ORCA_BIN`、兼容 `$ORCA_DIR` 或 PATH | `struct_orca`, `singlepoint_orca` |
| orca_2mkl | `WILLY_ORCA_2MKL_BIN` / `WILLY_ORCA_HOME`、兼容 `$ORCA_DIR` 或 PATH | `struct_orca`, `singlepoint_orca` |
| Multiwfn | `vendor/multiwfn/linux-x86_64/3.8-dev-2025-02-14/Multiwfn`（固定内置） | `singlepoint_g16`, `singlepoint_g09`, `singlepoint_orca`, `chg_resp` |

统一由 `env_registry.py` 解析、由兼容入口 `env_checker.py` 执行模块预检。Multiwfn
不读取外部环境变量或 PATH，安装包自带负载是唯一执行目标。

G09 与 G16 使用独立的工具 ID、环境变量和执行模块；请将
`WILLY_G09_BIN` 与 `WILLY_G09_FORMCHK_BIN` 指向同一套 Gaussian 09 安装。
当前主机未提供 G09 可执行文件和许可证。本版本不执行 G09 可靠全链路验收；缺少依赖时
按环境预检受控拒绝，并由方案助理欢迎气泡提示其可能出现运行问题。

## 6. Agent 工具接口

| 工具 | 功能 | 调用模块 |
|------|------|------|
| `tools_retry_struct_g16` | 重试 G16 结构优化 | `struct_g16.run_one()` |
| `tools_retry_struct_g09` | 重试 G09 结构优化 | `struct_g09.run_one()` |
| `tools_retry_struct_orca` | 重试 ORCA 结构优化 | `struct_orca.run_one()` |
| `tools_retry_mol2_conversion` | 重试 mol2 转换 | `molden_path` 走 `molden_mol2.convert()`；`fchk_path` 走 `fchk_mol2.convert()` |
| `tools_retry_chg_g16` | 重试 RESP (从 `*_opt.fchk`) | `chg_resp.make_chg()` |
| `tools_retry_chg_g09` | 重试 G09 RESP (从 `*_opt.fchk`) | `chg_resp.make_chg()` |
| `tools_retry_chg_orca` | 同上，等价 | `chg_resp.make_chg()` |
| `tools_diagnose_error_quantum` | 诊断 G16/G09/ORCA 输出 | `log_parsers.diagnose_log()` |
| `tools_modify_config_molecule` | 修改 config.json | 直接写 JSON |
| `tools_skip_molecule_quantum` | 跳过无法修复的分子 | 直接写 JSON |

## 7. 产物命名

| 步骤 | G16 | G09 | ORCA |
|------|-----|-----|------|
| Step 1 | `{name}.fchk` | `{name}.fchk` | `{name}.molden` |
| Step 2a 中间 | `{name}_opt.gjf`, `{name}_opt.chk` | `{name}_opt.gjf`, `{name}_opt.chk` | `{name}_opt.inp`, `{name}_opt.gbw`, `{name}_opt.molden` |
| Step 2a 输出 | `{name}_opt.fchk` | `{name}_opt.fchk` | `{name}_opt.fchk` |
| Step 2b | `{name}.mol2` | `{name}.mol2` | `{name}.mol2` |
| Step 3 | `{name}.chg` | `{name}.chg` | `{name}.chg` |

## 8. 已解决问题

### 8.1 WSL2 中 Gaussian/formchk 崩溃
- 注入 `GAUSS_CDEF=0 OMP_NUM_THREADS=1`

### 8.2 ORCA 输入格式转换
- 正则: `b3lyp/→B3LYP `, `def2TZVP→def2-TZVP`
- 跳过 .gjf 原子计数行和注释行

### 8.3 ORCA molden → RESP（已解决）
- 旧: ORCA molden GTO 格式 Multiwfn 不兼容
- 新: `singlepoint_orca` 内部完成 molden→fchk (Multiwfn 100→2→7)，再统一走 RESP

### 8.4 ORCA `.molden.input` 扩展名
- `orca_2mkl` 输出 `.molden.input`，需重命名为 `.molden`

### 8.5 ORCA FCHK 缺少键连接（已修复）
- 原问题：Multiwfn 从 ORCA Molden 导出的 FCHK 不含 Gaussian `MxBond/NBond/IBond/RBond`，不能由 `fchk_mol2` 解析。
- 新路径：`molden_mol2` 保留 ORCA Molden 原子顺序，调用内置 Multiwfn 的连通性和 Mayer 键级菜单生成 MOL2；多原子没有任何连通性时明确失败，单原子允许零键。
- 边界：MOL2 的键类型来自 Mayer 键级区间映射；FCHK 仍由同一次单点计算生成并只用于 RESP。

### 8.6 历史 shell RESP 依赖（已移除）
- 旧: G16 chg 走外部 bash RESP 脚本
- 新: `singlepoint_g16` 完成 SP，`chg_resp` 完成 RESP，全 Python

### 8.7 G16 chg 用初始坐标而非优化坐标（已修复）
- 旧: `chg_g16` 把 `.gjf`（初始几何）传给外部 RESP 脚本
- 新: `singlepoint_g16` 从 Step 1 的 `.fchk`（优化后几何）提取坐标做 SP

### 8.8 硬编码 ORCA 路径（已消除）
- 旧: `struct_orca` 硬编码 `/home/hush/ORCA/...`
- 新: `_orca_utils.find_orca()` → `$ORCA_DIR` 环境变量 → `shutil.which`

### 8.9 上游修复被误判为 Step 2 完成（已修复）
- 问题: Agent 为缺少 Step 1 中间产物的分子补出 `{name}.fchk` 后，编排器曾直接将
  Step 2 标记完成，导致缺少 `{name}_opt.fchk`、`.mol2` 的分子进入下游。
- 改进: Step 2 的 Agent 成功结果必须在当前 run 内同时满足 `{name}_opt.fchk` 与
  `{name}.mol2`。若只补足了上游 `.fchk`，编排器只重跑当前 Step 2，不标记完成。
- RESP 完整性: Step 3 按配置快照逐一要求 `*_opt.fchk`；任一分子缺失即返回
  `file_not_found`，禁止将部分分子处理成功视作整个步骤成功。

### 8.10 ORCA 单原子 `Opt` 正常结束但缺少 `.gbw`（已修复）
- 现象：带电单原子（例如 `Li+`）的 ORCA `Opt` 会因无几何自由度正常结束，却不写
  优化波函数；旧执行器把缺少 `.gbw` 误报为 `orca_crash`。
- 改进：只对已审计的内嵌 `* xyz` 单原子输入，在当前 run 写入私有
  `{name}__single_atom_sp.inp`，仅将 `!` 关键词行的 `Opt` 改为 `SP`。成功后把其 `.gbw`
  归一为 `{name}.gbw`，再生成 `{name}.molden`；临时输入及同前缀副产物在所有退出路径清理。
- 边界：`xyzfile` 外链输入不尝试猜测原子数；多原子输入与没有 `Opt` 的单原子输入保持原样。

## 9. 重构历史

| 日期 | 变更 |
|------|------|
| 2026-07-29 | 文件重命名: `g16_*`/`orca_*` → `struct_g16`/`struct_orca`/`mol2_g16`/`mol2_orca`/`chg_g16`/`chg_orca` |
| 2026-07-29 | 消除重复: 新建 `_orca_utils.py`，`resp_maker` 消除硬编码 |
| 2026-07-29 | 两步法统一: SP 显式化为 Step 2，mol2+chg 都从 `*_opt.fchk` 生成 |
| 2026-07-29 | 模块拆分合并: `resp_maker` → `singlepoint_g16` + `singlepoint_orca`; `mol2_g16` + `mol2_orca` → `fchk_mol2`; `chg_g16` + `chg_orca` → `chg_resp` |
| 2026-08-11 | ORCA 的 MOL2 转换从公共 FCHK 解析器拆出为 `molden_mol2`；以内置 Multiwfn 连通性和 Mayer 键级保留键参数，FCHK 继续仅供 RESP。 |
| 2026-08-01 | 运行预检支持冻结复用 Step 1 中间产物；当时仅 `.gjf` 时正常执行结构优化，缺少两类输入才拒绝启动。 |
| 2026-08-01 | Step 2 Agent 修复加入产物契约门禁；Step 3 拒绝不完整的配置分子集合。 |
| 2026-08-05 | 外部量子命令统一迁移到受管进程组生命周期，支持停止、超时升级和 run 内脱敏审计。 |

## 10. 后端特异原始输入审计（已完成）

### 10.1 目标与权威来源

用户上传结构后，原始量子输入文件中的电荷与自旋多重度是该分子的配置权威来源；Config Agent 不得把新上传、未登记分子默认解释为中性。`docs/knowledge.md`、TF-IDF 匹配和用户自然语言仅用于名称识别与补充建议，不能覆盖已解析的输入头。

Config Agent 在产生可确认方案前必须调用只读 `tools_inspect_quantum_inputs`，并由服务端重复审计以防模型漏调工具。审计工具只返回结构化摘要，不返回原始文件内容、绝对路径或可执行命令。对每个组分返回后端、输入状态、原子数、电荷、多重度和格式问题；按用户给定数目计算总净电荷。

### 10.2 后端输入契约

| 后端 | Config Agent 与新 run 的原始输入 | 最小解析契约 | 不满足时 |
|---|---|---|---|
| `g16` / `g09` | `struct/<name>.gjf` | route card、charge/multiplicity 行、至少一个有限坐标 | 阻止方案确认和启动 |
| `orca` | `struct/<name>.inp` | 非空 `!` 关键词行、内嵌 `* xyz <charge> <multiplicity>` 坐标块、闭合 `*`、至少一个有限坐标、`Opt` 关键词 | 阻止方案确认和启动 |

ORCA 新任务不再依赖将 `.gjf` 静默转写成 `.inp`。`struct_orca` 消费已审计的 `.inp` 并保留用户提供的计算关键字；G16/G09 各自消费 `.gjf`。已有 `.fchk`/`.molden` 仍可作为 Step 1 可复用产物，但不能取代新配置方案的原始输入审计，因为它们不能提供可审计的上传头部语义。

### 10.3 方案和启动门禁

1. 模型选择后端、解析分子名和数目后，必须调用审计工具；提示词中的电荷/多重度仅能采用工具返回值。
2. 服务端对模型 JSON 再次调用相同审计器，并把 `molecules.<name>.charge/spin` 归一为已解析数值；模型给出的冲突值不可成为运行配置。
3. 缺文件、格式错误、坐标不完整、后端输入类型不匹配或未能得到电荷/多重度时，不创建可确认方案。总净电荷不为零时，只返回明确的电荷不平衡提示；除非用户另行声明受支持的补偿/非中性策略，否则不能启动。
4. `start_pipeline()` 和 `PipelineOrchestrator._prepare_run_directory()` 在写入或复制输入前再次审计同一后端、同一组分、同一数目。二次审计失败不得创建有效 run，也不得用旧的 `config.json` 电荷兜底。

实现位置：`quantum/input_audit.py` 负责只读解析和契约比较；`toolist_global.py` 暴露审计工具；`agent_config.py` 要求模型调工具并冻结审计值；`pipeline_orchestrator.py` 复制对应后缀的原始文件；`struct_orca.py` 只执行原始 `.inp`。

### 10.4 验收

- 新上传的带电 `.gjf` 或 `.inp` 会在方案中使用文件内电荷和多重度，不会降为 `0/1`。
- G16/G09 选择 `.gjf`、ORCA 选择 `.inp`；交叉后缀、`xyzfile` 外链、缺失 route/关键词、缺失坐标块或非有限坐标均有结构化拒绝结果。
- 电荷平衡以解析文件的电荷和 `residues` 数目计算；LLM 输出的错误电荷不能绕过验证。
- 方案生成后替换或删除输入文件时，启动前复核拒绝该方案；实际 run 只冻结通过复核的后端对应文件。
