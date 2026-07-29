# 量子层设计文档

> Willy 量子计算层接口设计文档。涵盖 Gaussian 和 ORCA 两条链路的设计理念、实现经验与可扩展方案。

---

## 1. 核心架构

### 1.1 三层模型

```
  config.json molecules 段      ← 统一配置源
         │
  ┌──────┴──────┐
  │   Agent 层  │               ← 不感知底层软件，只传参数
  └──────┬──────┘
         │
  ┌──────┴──────────────────┐
  │   Quantum Layer (本层)   │
  │                         │
  │  g16_struct_maker       │  orca_struct_maker
  │  g16_mol2_maker         │  orca_mol2_maker
  │  g16_chg_maker          │  orca_chg_maker
  └─────────────────────────┘
         │
  ┌──────┴──────┐
  │ 下游: sobtop │            ← 只消费 .mol2 + .chg，不关心中间步骤
  └─────────────┘
```

### 1.2 各 Tool 职能

| 模块 | 输入 | 输出 | 外部依赖 | 设计要点 |
|------|------|------|------|------|
| `*_struct_maker` | `.gjf` + config | `.fchk` / `.molden` | 量子化学软件 | 统一入口，后端可换 |
| `*_mol2_maker` | `.fchk` / `.molden` | `.mol2` | Multiwfn / 自研解析 | 链式转换，复用下游 |
| `*_chg_maker` | `.gjf` / `.molden` | `.chg` | RESP_noopt.sh / Multiwfn | 电荷拟合独立于优化 |

### 1.3 设计原则

- 每个 maker 只做一件事（优化 / 格式转换 / 电荷拟合）
- 同技术栈文件加 `g16_` / `orca_` 前缀，结构对称
- 下游（sobtop）只消费 `.mol2` + `.chg`，不关心中间产物
- `config.json → molecules` 为唯一配置源，不硬编码分子参数

---

## 2. 依赖

量子层依赖以下外部工具，统一由 `env_checker.py` 预检：

| 依赖 | 路径 | 用途 |
|------|------|------|
| Gaussian 16 | PATH (`g16`) | DFT 优化 + ESP 计算 |
| ORCA 6.x | PATH (`orca`) | DFT 优化（开源替代） |
| formchk | PATH（随 g16） | `.chk → .fchk` |
| Multiwfn | PATH | 坐标提取 / RESP 拟合 / `.molden → .fchk` |
| `RESP_noopt.sh` | 项目根目录 | RESP 电荷一键脚本（卢天） |
| `env_checker.py` | `src/willy/` | 14 项依赖统一预检 |

---

## 3. 遇到的问题与解决方案

### 3.1 WSL2 中 Gaussian/formchk 崩溃

- **现象**：`sched_setaffinity` 报错 Aborted
- **原因**：WSL2 不支持 CPU affinity 系统调用
- **解决方案**：运行命令前注入 `GAUSS_CDEF=0 OMP_NUM_THREADS=1`。`struct_maker.py` 中已用 `shell=True` + 内联环境变量

### 3.2 Gaussian Scratch 目录缺失

- **现象**：`g16` 报 `PGFIO-F-/OPEN/... no such file`
- **原因**：Gaussian 默认 scratch 目录不存在或无写权限
- **解决方案**：`mkdir -p /home/hush/g16/Scratch && chmod 777`，一次性修复

### 3.3 `struct_maker` 中 formchk 路径嵌套

- **现象**：`cwd=struct/` 后 `struct/Li.chk` 变为 `struct/struct/Li.chk`
- **原因**：相对路径在 `cwd` 上下文中被重复拼接
- **解决方案**：使用 `.resolve()` 转绝对路径

### 3.4 ORCA 输入格式转换

- **现象**：Gaussian `.gjf` 的 `b3lyp/6-311+g(d,p)` 格式 ORCA 不接受；`* xyz` 块中原子计数行和注释行导致解析失败
- **原因**：ORCA 和 Gaussian 使用不同的输入格式语法
- **解决方案**：
  - 基组：正则 `b3lyp/→B3LYP `，`def2TZVP→def2-TZVP`
  - 坐标：跳过原子计数行和注释行，坐标直接跟在 `* xyz charge spin` 后
  - 添加 `%pal nprocs 8 end` 和 `%maxcore 4000` 性能参数

### 3.5 ORCA molden → RESP 失败

- **现象**：Multiwfn 读 ORCA 生成的 `.molden` 做 RESP 拟合时 Fortran crash
- **原因**：ORCA 的 GTO 基组格式与 Gaussian 不兼容，Multiwfn 无法正确解析
- **解决方案**：暂走迂回路径——ORCA 优化后，仍用 Gaussian SP + Multiwfn RESP（即复用 `RESP_noopt.sh`）。`orca_chg_maker.py` 保留占位，待未来 Multiwfn 兼容后直接 molden→RESP

### 3.6 ORCA `.molden` 扩展名异常

- **现象**：`orca_2mkl -molden` 输出 `.molden.input` 而非 `.molden`
- **原因**：orca_2mkl 的默认命名行为将 input 文件名作为后缀
- **解决方案**：生成后重命名为 `.molden`

### 3.7 Multiwfn 不支持 molden → mol2 直接导出

- **现象**：Multiwfn 的 Export 菜单无 `.mol2` 选项（只有 pdb/xyz/cif/gro）
- **原因**：Multiwfn 当前版本不内置 mol2 导出功能
- **解决方案**：两步走——`.molden → .fchk`（Multiwfn 100→2→7），再复用 `g16_mol2_maker` 的 fchk 解析器 → `.mol2`

---

## 4. 可扩展方向

### 4.1 短期（已验证）

| 软件 | 类型 | 可行性 | 实现方式 |
|------|------|:---:|------|
| **xtb (GFN2-xTB)** | 半经验优化 | 高 | 10MB 单体，内置 vendor/，替代 g16 做快速预优化 |
| **xtb → .mol2** | 格式转换 | 高 | xtb 自带 `--molden` 输出，Multiwfn 转 fchk |

### 4.2 中期（需额外安装）

| 软件 | 类型 | 可行性 | 说明 |
|------|------|:---:|------|
| **Psi4** | Python 原生 DFT | 中 | pip 安装，Python API 直接调，无需 subprocess |
| **PySCF** | Python 原生 DFT | 中 | 同上，Apache 许可，商业化友好 |
| **RDKit** | 构象生成 | 高 | pip 安装，生成初始构象 + MMFF94 电荷 |

### 4.3 长期（学术免费但体量大）

| 软件 | 类型 | 可行性 | 说明 |
|------|------|:---:|------|
| **Q-Chem** | 商业 DFT | 低 | 需许可，接口与 Gaussian 接近 |
| **Molpro** | 高精度电子结构 | 低 | 学术免费，specialized use |
| **NWChem** | 大规模 DFT | 中 | 开源，适合超大体系 |

---

## 5. 对 Agent 的暴露接口

量子层对上层完全透明。Agent 只需知道：

- `config.json → molecules` 填写分子名
- 调用 `run_pipeline.py [g16|orca]` 选择后端
- 下游自动适配，不需区分软件

未来可扩展 `run_pipeline.py --backend xtb`，只需在 quantum/ 下新增 `xtb_struct_maker.py` 等文件，保持命名和接口约定即可。

---

## 6. 接口清单

| 函数 | 文件 | 签名 |
|------|------|------|
| `run_all` | g16_struct_maker.py | `(config_path, struct_dir, backup) → list[Path]` |
| `run_one` | g16_struct_maker.py | `(name, cfg, defaults, struct_dir) → Path` |
| `batch_convert` | g16_mol2_maker.py | `(struct_dir) → list[Path]` |
| `fchk_to_mol2` | g16_mol2_maker.py | `(fchk_path, output_path) → StepResult` |
| `batch_make_chg` | g16_chg_maker.py | `(struct_dir, output_dir, solvent) → list[Path]` |
| `make_chg_one` | g16_chg_maker.py | `(gjf_path, charge, spin, ...) → Path` |
| `run_all` | orca_struct_maker.py | `(config_path, struct_dir) → list[Path]` |
| `run_one` | orca_struct_maker.py | `(name, cfg, defaults, struct_dir) → Path` |
| `batch_convert` | orca_mol2_maker.py | `(struct_dir) → list[Path]` |
| `molden_to_fchk` | orca_mol2_maker.py | `(molden_path, output_path) → Path \| None` |
| `batch_make_chg` | orca_chg_maker.py | `(struct_dir, output_dir, ...) → list[Path]` |
| `run_one` | orca_chg_maker.py | `(gjf_path, charge, spin, ...) → Path` |
