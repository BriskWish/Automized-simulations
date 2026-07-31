# 量子层设计文档

> Willy 量子计算层架构文档。两步法：结构优化 (DZ) → 单点能 (TZ) → mol2 + chg。
> 最后更新: 2026-07-29

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
- **零 RESP_noopt.sh**: G16 不再调用 shell 脚本，SP 由 `singlepoint_g16` 完成
- **零硬编码路径**: 全部通过 `_orca_utils` + 环境变量 + `shutil.which` 解析
- **统一 StepResult**: 所有函数返回 `StepResult`，错误用 `ErrorKind` 枚举

## 5. 依赖

| 依赖 | 路径 | 使用者 |
|------|------|------|
| Gaussian 16 | PATH (`g16`) | `struct_g16`, `singlepoint_g16` |
| formchk | PATH | `struct_g16`, `singlepoint_g16` |
| ORCA 6.x | PATH 或 `$ORCA_DIR` | `struct_orca`, `singlepoint_orca` |
| orca_2mkl | 同上 | `struct_orca`, `singlepoint_orca` |
| Multiwfn | PATH 或 `$MULTIWFN_BIN` | `singlepoint_g16`, `singlepoint_orca`, `chg_resp` |

统一由 `env_checker.py` 预检。

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

### 8.6 RESP_noopt.sh 依赖（已消除）
- 旧: G16 chg 走 bash 脚本 RESP_noopt.sh
- 新: `singlepoint_g16` 完成 SP，`chg_resp` 完成 RESP，全 Python

### 8.7 G16 chg 用初始坐标而非优化坐标（已修复）
- 旧: `chg_g16` 把 `.gjf`（初始几何）传给 RESP_noopt.sh
- 新: `singlepoint_g16` 从 Step 1 的 `.fchk`（优化后几何）提取坐标做 SP

### 8.8 硬编码 ORCA 路径（已消除）
- 旧: `struct_orca` 硬编码 `/home/hush/ORCA/...`
- 新: `_orca_utils.find_orca()` → `$ORCA_DIR` 环境变量 → `shutil.which`

## 9. 重构历史

| 日期 | 变更 |
|------|------|
| 2026-07-29 | 文件重命名: `g16_*`/`orca_*` → `struct_g16`/`struct_orca`/`mol2_g16`/`mol2_orca`/`chg_g16`/`chg_orca` |
| 2026-07-29 | 消除重复: 新建 `_orca_utils.py`，`resp_maker` 消除硬编码 |
| 2026-07-29 | 两步法统一: SP 显式化为 Step 2，mol2+chg 都从 `*_opt.fchk` 生成 |
| 2026-07-29 | 模块拆分合并: `resp_maker` → `singlepoint_g16` + `singlepoint_orca`; `mol2_g16` + `mol2_orca` → `fchk_mol2`; `chg_g16` + `chg_orca` → `chg_resp` |
