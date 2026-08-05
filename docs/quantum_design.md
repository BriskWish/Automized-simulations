# 量子层设计文档

> Willy 量子计算层架构文档。两步法：结构优化 (DZ) → 单点能 (TZ) → mol2 + chg。
> 最后更新: 2026-08-05

---

## 1. 文件架构

```
src/willy/quantum/
├── struct_g16.py          Step 1: G16 结构优化 → .fchk
├── struct_orca.py         Step 1: ORCA 结构优化 → .molden
├── singlepoint_g16.py     Step 2a: .fchk → g16 SP(def2TZVP) → *_opt.fchk
├── singlepoint_orca.py    Step 2a: .molden → ORCA SP(def2-TZVP) → Multiwfn → *_opt.fchk
├── fchk_mol2.py           Step 2b: *_opt.fchk → .mol2 (纯 Python 解析, G16+ORCA 统一)
├── chg_resp.py            Step 3:  *_opt.fchk → Multiwfn RESP(内部ESP) → .chg (G16+ORCA 统一)
├── _orca_utils.py         ORCA 路径解析 + Multiwfn 工具 + 坐标提取/格式化
└── __init__.py
```

## 2. 两步法链路

```
Step 1: 结构优化 (DZ)
  G16:  .gjf → g16 Opt(6-311+g(d,p)) → formchk → {name}.fchk
  ORCA: .gjf → ORCA Opt(6-311+g(d,p)) → orca_2mkl → {name}.molden

Step 2a: 单点能 (TZ)
  G16:  {name}.fchk → 提取坐标 → {name}_opt.gjf → g16 SP(def2TZVP) → formchk → {name}_opt.fchk
  ORCA: {name}.molden → 提取坐标 → {name}_opt.inp → ORCA SP(def2-TZVP) → orca_2mkl
        → {name}_opt.molden → Multiwfn molden→fchk → {name}_opt.fchk

Step 2b: mol2 生成
  G16+ORCA 统一: {name}_opt.fchk → fchk_mol2.convert() → {name}.mol2

Step 3: RESP 电荷
  G16+ORCA 统一: {name}_opt.fchk → Multiwfn RESP(7→18→2→y→q) → {name}.chg
```

启动预检会优先冻结已有的 Step 1 中间产物（G16 为 `.fchk`，ORCA 为
`.molden`）到本次 `md_run/<run_id>/`；存在时 Step 1 跳过该分子。若没有
中间产物但存在 `.gjf`，这是正常新任务，会静默进入 Step 1 优化。只有两类
输入都缺失时才拒绝启动。

## 3. 各模块职能

| 模块 | 输入 | 输出 | 外部依赖 | 设计要点 |
|------|------|------|------|------|
| `struct_g16` | `.gjf` + config | `.fchk` | g16, formchk | 支持 Agent 重试覆盖基组/SCF/OPT 参数 |
| `struct_orca` | `.gjf` + config | `.molden` | ORCA, orca_2mkl | 零硬编码路径，通过 `_orca_utils` 解析 |
| `singlepoint_g16` | `.fchk` (DZ) | `*_opt.fchk` (TZ) | g16, formchk, Multiwfn | 提取坐标 → `*_opt.gjf` → SP → formchk |
| `singlepoint_orca` | `.molden` (DZ) | `*_opt.fchk` (TZ) | ORCA, orca_2mkl, Multiwfn | 内部完成 molden→fchk，保证输出统一 |
| `fchk_mol2` | `*_opt.fchk` | `.mol2` | 无 | 纯 Python fchk 解析器，零外部依赖 |
| `chg_resp` | `*_opt.fchk` | `.chg` | Multiwfn | G16+ORCA 完全统一，MultiWfn 内部 ESP |
| `_orca_utils` | — | — | — | `find_orca`, `find_multiwfn`, `extract_xyz`, `format_orca_xyz_coords`, `get_orca_env` |

## 4. 设计原则

- **两步法**: DZ 优化求速度，TZ 单点求精度。两步不可合并——DZ 优化的 fchk 不能直接做 SP（需换基组重建输入），产物同名会覆盖
- **`*_opt` 命名**: Step 2a 的 SP 产物统一 `{name}_opt.*`，防止覆盖 Step 1 产物。最终 `.mol2` / `.chg` 保持原名（Sobtop 兼容）
- **Step 2b/3 统一**: `fchk_mol2` 和 `chg_resp` 只收 `*_opt.fchk`，不感知 G16/ORCA
- **无 shell RESP 依赖**: G16 不再调用外部 RESP 脚本，SP 由 `singlepoint_g16` 完成
- **统一环境解析**: 所有外部可执行文件通过 `env_registry` 解析；`_orca_utils` 仅保留 ORCA 坐标与调用辅助
- **统一 StepResult**: 所有函数返回 `StepResult`，错误用 `ErrorKind` 枚举
- **受管外部进程**: G16、formchk、ORCA、orca_2mkl 和 Multiwfn 均通过
  `run_managed_command()` 在独立进程组中运行。当前 run 有停止请求或命令超时时，
  统一按 `SIGINT -> SIGTERM -> SIGKILL` 结束整个进程组，并在 run 私有
  `process_lifecycle.jsonl` 记录脱敏命令、原因、信号和退出码；正常完成不新增该审计记录。
  调用方继续接收 `CompletedProcess` 或 `TimeoutExpired`，并映射为既有 `StepResult` 错误契约。

## 5. 依赖

| 依赖 | 路径 | 使用者 |
|------|------|------|
| Gaussian 16 | `WILLY_G16_BIN` 或 PATH | `struct_g16`, `singlepoint_g16` |
| formchk | `WILLY_FORMCHK_BIN` 或 PATH | `struct_g16`, `singlepoint_g16` |
| ORCA 6.x | `WILLY_ORCA_HOME` / `WILLY_ORCA_BIN`、兼容 `$ORCA_DIR` 或 PATH | `struct_orca`, `singlepoint_orca` |
| orca_2mkl | `WILLY_ORCA_2MKL_BIN` / `WILLY_ORCA_HOME`、兼容 `$ORCA_DIR` 或 PATH | `struct_orca`, `singlepoint_orca` |
| Multiwfn | `WILLY_MULTIWFN_BIN`、兼容 `$MULTIWFN_BIN` 或 PATH | `singlepoint_g16`, `singlepoint_orca`, `chg_resp` |

统一由 `env_registry.py` 解析、由兼容入口 `env_checker.py` 执行模块预检。

## 6. Agent 工具接口

| 工具 | 功能 | 调用模块 |
|------|------|------|
| `tools_retry_struct_g16` | 重试 G16 结构优化 | `struct_g16.run_one()` |
| `tools_retry_struct_orca` | 重试 ORCA 结构优化 | `struct_orca.run_one()` |
| `tools_retry_mol2_conversion` | 重试 fchk→mol2 | `fchk_mol2.convert()` |
| `tools_retry_chg_g16` | 重试 RESP (从 `*_opt.fchk`) | `chg_resp.make_chg()` |
| `tools_retry_chg_orca` | 同上，等价 | `chg_resp.make_chg()` |
| `tools_diagnose_error_quantum` | 诊断 g16/ORCA 输出 | `log_parsers.diagnose_log()` |
| `tools_modify_config_molecule` | 修改 config.json | 直接写 JSON |
| `tools_skip_molecule_quantum` | 跳过无法修复的分子 | 直接写 JSON |

## 7. 产物命名

| 步骤 | G16 | ORCA |
|------|-----|------|
| Step 1 | `{name}.fchk` | `{name}.molden` |
| Step 2a 中间 | `{name}_opt.gjf`, `{name}_opt.chk` | `{name}_opt.inp`, `{name}_opt.gbw`, `{name}_opt.molden` |
| Step 2a 输出 | `{name}_opt.fchk` | `{name}_opt.fchk` |
| Step 2b | `{name}.mol2` | `{name}.mol2` |
| Step 3 | `{name}.chg` | `{name}.chg` |

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

### 8.5 Multiwfn 无 mol2 导出
- `singlepoint_orca` 内部 molden→fchk (Multiwfn)，再复用 `fchk_mol2` 解析

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

## 9. 重构历史

| 日期 | 变更 |
|------|------|
| 2026-07-29 | 文件重命名: `g16_*`/`orca_*` → `struct_g16`/`struct_orca`/`mol2_g16`/`mol2_orca`/`chg_g16`/`chg_orca` |
| 2026-07-29 | 消除重复: 新建 `_orca_utils.py`，`resp_maker` 消除硬编码 |
| 2026-07-29 | 两步法统一: SP 显式化为 Step 2，mol2+chg 都从 `*_opt.fchk` 生成 |
| 2026-07-29 | 模块拆分合并: `resp_maker` → `singlepoint_g16` + `singlepoint_orca`; `mol2_g16` + `mol2_orca` → `fchk_mol2`; `chg_g16` + `chg_orca` → `chg_resp` |
| 2026-08-01 | 运行预检支持冻结复用 Step 1 中间产物；仅 `.gjf` 时正常执行结构优化，缺少两类输入才拒绝启动。 |
| 2026-08-01 | Step 2 Agent 修复加入产物契约门禁；Step 3 拒绝不完整的配置分子集合。 |
| 2026-08-05 | 外部量子命令统一迁移到受管进程组生命周期，支持停止、超时升级和 run 内脱敏审计。 |
