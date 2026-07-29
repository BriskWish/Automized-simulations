# 拓扑层设计文档

> 力场参数化接口的架构设计、依赖策略与可扩展性分析。拓扑层的唯一职责是将分子结构转换为 GROMACS 拓扑文件。

---

## 1. 设计理念

### 1.1 核心契约：Input → .itp + .gro

上下游通过文件契约解耦：

```
量子层（Step 1-3）               拓扑层（Step 4）               MD 准备层（Step 5-7）
──────────────────              ──────────────                 ──────────────────
.mol2 + .chg ──────────→  [力场参数化引擎] ──────────→  .itp + .gro
  or SMILES                      ↑                             top_maker 读取 itp
                              本层                            mdp_maker 不感知力场
```

**Step 5 (`top_maker.py`) 和 Step 6 (`mdp_maker.py`) 不关心 .itp 来自哪个力场**——它们只解析 `[atoms]`、`[bonds]` 等标准 GROMACS 段。这是整个架构的核心假设，也是多力场支持的基础。

### 1.2 统一出参

所有力场后端必须返回相同结构：

```python
{"itp": Path, "gro": Path}   # → 写入 output_dir
```

`.top` 文件由 Step 5 统一生成，各后端不自行构建主拓扑。

### 1.3 双轨策略

| 路径 | 力场 | 电荷来源 | 输入 |
|------|------|----------|------|
| sobtop (GAFF) | General AMBER Force Field | 外部 RESP (.chg) | .mol2 + .chg |
| LigParGen (OPLS-AA) | OPLS-AA/CM1A(-LBCC) | 内置 CM1A 计算 | SMILES 或 .mol2 |

两条路径的差异仅限于 Step 4 内部，Step 5-7 无感。

---

## 2. 各模块职能

```
src/willy/topology/
├── sobtop_interface.py       ← GAFF/AMBER 路径
├── ligpargen_interface.py    ← OPLS-AA 路径（新增）
├── top_maker.py              ← 主拓扑组装（力场无关）
└── itp_reviser.py            ← itp 修订（力场无关）
```

### 2.1 sobtop_interface.py

| 项 | 内容 |
|---|---|
| 入参 | `TopMakerInput`：mol2、chg、gaff（True=GAFF / False=AMBER）、hessian（可选）、output_name |
| 后端 | `vendor/sobtop/sobtop`（交互式 stdin 控制） |
| 产物 | .itp（GAFF atomtype：c3/h1/os/o...）、.gro |
| 特点 | 已内置，零外部依赖 |

### 2.2 ligpargen_interface.py

| 项 | 内容 |
|---|---|
| 入参 | `LigParGenInput`：smiles（推荐）或 mol2（自动提取 SMILES）、output_name、net_charge、lbcc、opt_steps |
| 后端 | `LigParGen` CLI → BOSS 引擎 |
| 产物 | .itp（OPLS-AA atomtype：opls_800/801...）、.gro |
| 特点 | 外部依赖 BOSS，内置 CM1A 电荷无需 .chg |

### 2.3 top_maker.py

- 扫描 `topo/*.itp` → 提取 `[atomtypes]` → 去重 → 写入 `topol.top`
- 生成 `#include` 列表、`[system]`、`[molecules]`
- 调用 `itp_reviser` 清理 itp

### 2.4 itp_reviser.py

- 删除 itp 中的 `[atomtypes]` 段（已迁移到主拓扑）
- 将 `[atoms]` 中 RESNAME 替换为残基名

---

## 3. 依赖架构

### 3.1 分类策略

```
内置（vendor/）                    外部（用户自行安装）
─────────────                     ──────────────────
sobtop          ✅ 已内置          Gaussian 16     商业软件
obabel          ✅ 已内置          ORCA            开源但需编译
packmol         ✅ 已内置          Multiwfn        可分发非开源
atomtype        ✅ 已内置          BOSS            学术免费，需申请
                                  LigParGen        MIT开源，pip安装
```

### 3.2 LigParGen 的依赖链

```
ligpargen (pip) ──→ BOSS ($BOSSdir) ──→ 32 位 glibc
     ↑                    ↑                    ↑
  MIT 开源            学术免费             系统库
  自动安装            需手动下载            WSL2 需 multiarch
```

### 3.3 env_checker 注册模式

```python
# 内置工具：kind="file_exec"，路径在 vendor/ 下
DepResult(name="sobtop", kind="file_exec", path=str(SOBTOP_DIR / "sobtop"),
          needed_by=["sobtop_interface"])

# 外部工具：kind="binary" 或 kind="envvar"
DepResult(name="LigParGen", kind="binary", path="LigParGen",
          needed_by=["ligpargen_interface"])
DepResult(name="BOSSdir", kind="envvar", path="BOSSdir",
          needed_by=["ligpargen_interface"])
```

新增的 `kind="envvar"` 类型用于检查环境变量是否已设置且指向存在的目录。

---

## 4. 遇到的问题与解决方式

### 4.1 LigParGen 硬编码输出到 /tmp/

- **现象**：`Converter.py` 在运行时执行 `os.chdir('/tmp/')`，产物全部写入 `/tmp/{resname}.*`，无法自定义输出路径
- **原因**：LigParGen 上游代码硬编码了 `/tmp/` 作为工作目录
- **解决方案**：在 `ligpargen_interface.py` 中封装取回逻辑——运行结束后从 `/tmp/` 将 .itp 和 .gro 复制到目标 `output_dir`，再清理 `/tmp/` 残留文件。对外部调用者透明

### 4.2 LigParGen 不支持 MOL2 输入

- **现象**：LigParGen CLI 只接受 MOL、PDB、SMILES 三种格式，不接受 MOL2。而现有 pipeline Step 2 产出的是 .mol2
- **原因**：LigParGen 上游未实现 MOL2 解析器
- **解决方案**：利用已 vendored 的 `obabel` 从 .mol2 提取 SMILES（`obabel input.mol2 -osmi`），再将 SMILES 传给 LigParGen。SMILES 是 LigParGen 的原生输入，且 OPLS-AA 的 CM1A 电荷不依赖 Gaussian 优化结构，所以这一步没有信息损失

### 4.3 BOSS 是 32 位程序，WSL2 是 64 位

- **现象**：BOSS 编译为 32 位 Linux ELF，直接在 64 位系统上运行报 `exec format error`
- **原因**：BOSS 仅发布 32 位编译版本，与 64 位系统 ABI 不兼容
- **解决方案**：BOSS 作为外部依赖，由用户负责环境配置。env_checker 检测 `$BOSSdir` 并提示用户安装 32 位兼容库（`dpkg --add-architecture i386 && apt install libc6:i386`）。WSL2 完全支持 multiarch，不构成阻塞

### 4.4 GAFF 和 OPLS-AA 使用不同的 atomtype 命名

- **现象**：GAFF 用 `c3/h1/os/o/f`，OPLS-AA 用 `opls_800/801...`。如果混用会导致 GROMACS 报错
- **原因**：两个力场独立发展，atomtype 命名体系完全不同
- **解决方案**：`top_maker.py` 的 atomtype 去重逻辑按 atomtype 名称去重，天然隔离不同力场。用户不会在一次模拟中同时使用两个力场（Step 4 是排他选择），所以不会发生冲突

### 4.5 LigParGen 的 LBCC 只适用于中性分子

- **现象**：`-l` 标志启用 1.14*CM1A-LBCC 电荷，仅适用于净电荷=0 的分子。带电分子需自动回退到 CM1A
- **原因**：LBCC（Localized Bond Charge Correction）算法的理论前提是中性体系
- **解决方案**：在 `make_itp_gro_opls()` 中做运行时校验——若 `lbcc=True` 且 `net_charge ≠ 0`，自动降级为 CM1A 并打印警告

---

## 5. 可扩展的拓扑层内容

### 5.1 力场扩展矩阵

| 力场 | 后端工具 | 依赖性质 | 扩展难度 | 备注 |
|------|----------|----------|:---:|------|
| **GAFF** | sobtop | 已内置 | — | 当前主力 |
| **GAFF2** | sobtop（gaff=False→AMBER） | 已内置 | ★☆☆ | 改一个参数即可 |
| **OPLS-AA** | LigParGen + BOSS | 外部 | ★★☆ | 本接口已实现 |
| **CGenFF** | cgenff + CHARMM | 外部 | ★★☆ | 需处理 CHARMM atomtype（CGxxx） |
| **OpenFF** | openff-toolkit | pip 安装 | ★☆☆ | 纯 Python，MIT，最易内置 |
| **AMBER ff14SB/19SB** | tleap/antechamber | conda 安装 | ★★★ | Ambertools 生态 |
| **CHARMM36** | psfgen/CHARMM-GUI | 外部+Web | ★★★ | atomtype 较多，需精细映射 |
| **ReaxFF** | reaxff 参数文件 | 文献查找 | ★★★★ | 反应力场，无标准生成器 |
| **Martini 3** | martinize2 + 文献 | pip 安装 | ★★★ | CG 力场，映射逻辑复杂 |

### 5.2 扩展只需实现的接口

```python
# 任何新力场只需遵循此契约
@dataclass
class NewFFInput:
    # ... 力场特定的入参 ...
    output_name: str

def make_itp_gro_newff(inp: NewFFInput, output_dir: str) -> dict[str, Path]:
    """
    返回 {"itp": Path, "gro": Path}
    .top 由 top_maker 统一生成，无需在此处理
    """
    # 1. 检查环境/依赖
    # 2. 调用后端
    # 3. 取回 .itp + .gro
    # 4. 清理临时文件
    ...

def batch_make_topo_newff(mol_dir, output_dir) -> list[dict[str, Path]]:
    """批量版本，接口与现有 batch_make_topo 对齐"""
    ...
```

### 5.3 推荐的下一步扩展

1. **OpenFF**（纯 pip，无外部程序依赖，最适合 Agent 内置）
2. **GAFF2**（sobtop 原生支持，改 `gaff=False` 即可）

---

## 6. 接口清单

| 函数 | 文件 | 签名 |
|------|------|------|
| `make_itp_gro` | sobtop_interface.py | `(TopMakerInput, output_dir) → dict` |
| `batch_make_topo` | sobtop_interface.py | `(mol2_dir, chg_dir, output_dir) → list[dict]` |
| `make_itp_gro_opls` | ligpargen_interface.py | `(LigParGenInput, output_dir) → dict` |
| `batch_make_topo_opls` | ligpargen_interface.py | `(mol2_dir, output_dir, net_charge, lbcc) → list[dict]` |
| `generate_top` | top_maker.py | `(TopConfig, ...) → Path` |
| `build` | top_maker.py | `(config_path, topo_dir, output_path) → Path` |
| `revise_itp` | itp_reviser.py | `(itp_path) → bool` |
| `revise_all` | itp_reviser.py | `(itp_dir) → int` |
| `check_sobtop_ready` | sobtop_interface.py | `() → list[str]` |
| `check_ligpargen_ready` | ligpargen_interface.py | `() → list[str]` |
