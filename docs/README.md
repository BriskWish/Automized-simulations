# Willy 文档索引

> 按角色和场景导航 14 份文档。最后更新：2026-07-31。

---

## 快速导航（按阅读目的）

| 你想做什么 | 读这份 |
|------|------|
| 快速了解项目是什么、怎么跑 | [`README.md`](../README.md) |
| 理解整体架构和全流程 | [`Willy.md`](Willy.md) |
| 查某个分子参数、基组、力场 | [`knowledge.md`](knowledge.md) |
| 理解 LLM 输出错误/警告格式 | [`ERR_WARN_Build.md`](ERR_WARN_Build.md) |
| 给前端接流水线状态 | [`status_api.md`](status_api.md) |
| 加一个 tool 或 agent，遵循命名规则 | [`naming_convention.md`](naming_convention.md) |
| 改量子层代码，知道要同步哪些文件 | [`reconstruction.md`](reconstruction.md) |
| 制定后续修订路线、明确模块边界和验收规则 | [`revision_strategy.md`](revision_strategy.md) |
| 理解量子层的设计和坑 | [`quantum_design.md`](quantum_design.md) |
| 理解拓扑层的设计（GAFF/OPLS-AA） | [`topology_design.md`](topology_design.md) |
| 了解 0 号总工程师职责、团队分工和共识原则 | [`employees.md`](employees.md) |
| 查当前项目的已知缺口和修bug优先级 | [`project_gap_analysis.md`](project_gap_analysis.md) |
| 看当前项目的综合评估、优缺点和完整性判断 | [`project_evaluation.md`](project_evaluation.md) |
| 锂电池电解质体系调研背景 | [`lithium-salts.md`](lithium-salts.md) (孤立文档，未被引用) |

---

## 文档分类

### 入口与架构（3 份）

| 文档 | 行数 | 受众 | 状态 |
|------|:---:|------|:---:|
| `../README.md` | 170 | 所有人 | ✅ 同步 |
| `Willy.md` | 177 | 开发者、架构师 | ⚠️ 文件名和工具数过期（见下方矛盾清单） |
| `employees.md` | 161 | 0 号总工程师、领域工程师、团队职责边界 | ⚠️ 0号已同步，领域成果旧名待修 |

### 知识库与协议（2 份）

| 文档 | 行数 | 说明 | 状态 |
|------|:---:|------|:---:|
| `knowledge.md` | 223 | 11 分子 + 力场/基组/MD 参数，Agent TF-IDF 数据源 | ✅ 同步 |
| `ERR_WARN_Build.md` | 51 | LLM 输出 Error/Warning 协议 | ✅ 同步 |

### 设计文档（2 份）

| 文档 | 行数 | 说明 | 状态 |
|------|:---:|------|:---:|
| `quantum_design.md` | 166 | 量子层 7 模块 + 7 项踩坑记录 + 扩展矩阵 | ⚠️ 文件名过期 |
| `topology_design.md` | 221 | 拓扑层 4 模块 + 5 项踩坑记录 + 力场扩展矩阵 | ⚠️ 文件名过期 |

### 规范与重构（3 份）

| 文档 | 行数 | 说明 | 状态 |
|------|:---:|------|:---:|
| `naming_convention.md` | 236 | tools/toolist/agent 三套命名规范 + 实施状态 | ✅ 同步 |
| `reconstruction.md` | 143 | 6 关联方检查清单 + 常见遗漏模式 | ⚠️ 文件名示例过期，原则有效 |
| `revision_strategy.md` | 851 | 后续修订总计划、模块边界、跨层契约和验收规则 | ✅ 同步 |

### 接口与分析（4 份）

| 文档 | 行数 | 说明 | 状态 |
|------|:---:|------|:---:|
| `status_api.md` | 118 | PipelineStatus JSON 字段 + 前端渲染伪代码 | ✅ 同步 |
| `project_gap_analysis.md` | 240 | 22 项缺口（3C/6H/8M/5L）+ 执行计划 | ✅ 同步 |
| `project_evaluation.md` | 321 | 当前项目综合评估：优点、创新点、缺点、完整性和改进优先级 | ✅ 同步 |
| `lithium-salts.md` | 303 | 锂电池盐类电解质调研 | ⚠️ 孤立，无引用 |

---

## 文档间矛盾清单

> 以下矛盾在 2026-07-29 全量交叉检查时发现。修改任一文档时需同步修正。

### 矛盾 1：设计文档中的文件名 vs 实际代码文件名

| 设计文档引用的文件名 | 实际文件名 | 影响范围 |
|------|------|------|
| `g16_struct_maker.py` | `struct_g16.py` | `quantum_design.md` §1.2, §6 |
| `g16_mol2_maker.py` | `mol2_g16.py` | `quantum_design.md` §1.2, §6 |
| `g16_chg_maker.py` | `chg_g16.py` | `quantum_design.md` §1.2, §6 |
| `orca_struct_maker.py` | `struct_orca.py` | `quantum_design.md` §1.2, §6 |
| `orca_mol2_maker.py` | `mol2_orca.py` | `quantum_design.md` §1.2, §6 |
| `orca_chg_maker.py` | `chg_orca.py` | `quantum_design.md` §1.2, §6 |
| `sobtop_interface.py` | `topo_gaff.py` | `topology_design.md` §2, `Willy.md` §一, `reconstruction.md` |
| `ligpargen_interface.py` | `topo_opls.py` | `topology_design.md` §2, `Willy.md` §一 |
| `top_maker.py` | `top_assembly.py` | `topology_design.md` §2 |
| `itp_reviser.py` | `itp_revise.py` | `topology_design.md` §2 |
| `mdp_maker.py` | `mdp.py` | `Willy.md` §一 |
| `inp_generator.py` | `box.py` | `Willy.md` §一 |
| `md_em.py` / `md_eq.py` / `md_prod.py` | `em.py` / `eq.py` / `prod.py` | `Willy.md` §一 |
| `_md_utils.py` | `_gmx_utils.py` | `Willy.md` §一 |
| `agent.py` | `agent_config.py` | `Willy.md` §一, `employees.md` |

**修正方向**：以实际文件名为准，更新所有设计文档。

### 矛盾 2：`quantum_design.md` §6 接口清单签名过期

设计文档声明的返回类型与实际代码不符：

| 函数 | 文档签名 | 实际返回类型 |
|------|------|------|
| `fchk_to_mol2` | `→ Path` | `→ StepResult` |
| `batch_convert` (mol2) | `→ list[Path]` | `→ list[StepResult]` |
| `make_chg_one` | `→ Path` | `→ StepResult` |
| `batch_make_chg` | `→ list[Path]` | `→ list[StepResult]` |
| `molden_to_fchk` | `→ Path \| None` | `→ StepResult` |
| `run_one` (struct) | `→ Path` | `→ StepResult` |

**修正方向**：以实际 `StepResult` 返回类型更新 §6。Phase 1 的 StepResult 统一化已经完成，文档未跟进。

### 矛盾 3：`topology_design.md` §6 接口清单签名过期

| 函数 | 文档签名 | 实际返回类型 |
|------|------|------|
| `make_itp_gro` | `→ dict` | `→ StepResult` |
| `batch_make_topo` | `→ list[dict]` | `→ list[StepResult]` |
| `build` | `→ Path` | `→ StepResult` |

### 矛盾 4：`Willy.md` §三 工具数量与实际不符

- 文档说 "LLM Tools (7)" — 实际 `toolist_global.py` 有 **9** 个工具
- 工具名未更新为 `tools_` 前缀（如 `lookup_molecule` → `tools_lookup_molecule`）

### 矛盾 5：`Willy.md` §一 项目结构过期

- 列出 `prompts.py` — 已删除（内容已拆分到 `agent_*.py`）
- 列出 `knowledge_tools.py` — 已更名为 `toolist_global.py`
- 列出 `quantum_tools.py` / `topology_tools.py` / `simulation_tools.py` — 已更名为 `toolist_*.py`
- 列出 `agent.py` — 已更名为 `agent_config.py`
- 列出 `progress.py` — 文件存在但未出现在当前 tree 输出中
- 缺少 `agent_quantum.py`、`agent_topology.py`、`agent_simulation.py`（新增）
- 缺少 `docs/naming_convention.md`、`docs/project_gap_analysis.md`、`docs/reconstruction.md`
- 缺少 `docs/lithium-salts.md`

### 矛盾 6：`employees.md` 中领域成果清单仍含旧文件名

| 描述 | 实际 |
|------|------|
| 0 号总工程师职责 | 已更新为 `Codex — 总工程师`，负责路线图、边界裁决、质量门禁和文档治理 |
| 1-3 号成果清单仍引用 `g16_struct_maker.py`、`sobtop_interface.py`、`mdp_maker.py` 等旧名 | 实际代码已重命名为 `struct_g16.py`、`topo_gaff.py`、`mdp.py` 等 |
| `SIMULATION_AGENT_PROMPT (in prompts.py)` | prompt 已拆入 `agent_simulation.py`，`prompts.py` 已删除 |

**修正方向**：0 号职责已同步；下一轮应由 1-3 号领域工程师把各自成果清单的旧文件名改为当前实际文件名。

### 矛盾 7：`reconstruction.md` 文件名示例过期

- 使用 `quantum/g16_struct_maker.py` 等旧名作为案例
- 检查清单原则仍然有效，但示例文件名需更新为 `quantum/struct_g16.py` 等

---

## 文档依赖图

```
README.md  ←── Willy.md  ←── knowledge.md
                │               │
                ├── ERR_WARN_Build.md
                ├── status_api.md
                ├── naming_convention.md
                ├── reconstruction.md
                ├── employees.md
                ├── project_gap_analysis.md
                │
                ├── quantum_design.md
                ├── topology_design.md
                │
                └── lithium-salts.md (孤立)
```

箭头方向 = "引用/依赖"。`lithium-salts.md` 是唯一不被任何文档引用的孤立节点。

---

## 维护规则

1. **改代码** → 同步更新受影响的 `*_design.md` 和 `Willy.md`
2. **改文件名** → 按 [`reconstruction.md`](reconstruction.md) 的 6 关联方清单逐项检查；同时更新本文档的矛盾清单
3. **加新文档** → 在本文档"快速导航"和"文档分类"各加一行
4. **发现矛盾** → 在本文档矛盾清单中记录，标注影响范围
5. **每次发版前** → 跑一遍矛盾清单逐项验证
