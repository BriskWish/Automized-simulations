# Willy — AI 驱动的 MD 模拟自动化

## 一、项目结构

```
AutomizedSimulations/
├── app.py                   ← Gradio UI (Willy Agent)
├── run_pipeline.py          ← CLI 入口：全流程编排 10 步 (产物→md_run/<run_id>/)
├── config.json              ← 体系唯一配置源
├── .env / .env.example      ← API key 与白名单外部软件覆盖配置
├── scripts/
│   └── prune_runs.py        ← 历史 run 保留工具（默认只预览）

├── tests/tools/
│   └── generate_test_case_catalog.py ← 由 pytest 收集结果生成测试台账

├── vendor/  (~95 MB，2026-08-11) ← 内置依赖
│   ├── packmol              ← Packmol 二进制
│   ├── obabel.bin           ← OpenBabel CLI
│   ├── libopenbabel.so.7    ← OpenBabel 共享库
│   ├── libcoordgen.so.3     ← coordgen 依赖库
│   ├── 3Dmol-min.js         ← 前端 3D 分子可视化
│   └── sobtop/              ← Sobtop 拓扑生成器 (GAFF)

├── struct/                  ← 量子输入 `.gjf` 与可复用 Step 1 中间产物
├── md_run/<run_id>/         ← 隔离运行目录 (状态、manifest、事件和计算产物)

├── benchmarks/
│   ├── llm_score.py         ← LLM 评分 (多用例并行测试)
│   └── precheck_examples.jsonl

├── src/willy/               ← 核心 Python 包
│   ├── _paths.py            ← get_project_root()
│   │
│   ├── agent_config.py      ← Layer 0: Config Agent (NL→config)
│   ├── agent_quantum.py     ← Layer 1: Quantum Agent (含 QUANTUM_AGENT_PROMPT)
│   ├── agent_topology.py    ← Layer 2: Topology Agent (含 TOPOLOGY_AGENT_PROMPT)
│   ├── agent_simulation.py  ← Layer 3: Simulation Agent (含 SIMULATION_AGENT_PROMPT)
│   ├── agent_run.py         ← Run Assistant（只读运行解释）
│   ├── layer_agent.py       ← Agent 基类 (tool-calling 循环 + retry + escalation)
│   │
│   ├── toolist_global.py    ← Layer 0 工具定义 (11 tools) + handler
│   ├── toolist_quantum.py   ← Layer 1 工具定义 (8 tools) + handler
│   ├── toolist_topology.py  ← Layer 2 工具定义 (6 tools) + handler
│   ├── toolist_simulation.py← Layer 3 工具定义 (14 tools) + handler
│   ├── toolist_run.py       ← Run Assistant 只读工具 (9 tools) + handler
│   │
│   ├── pipeline_orchestrator.py ← 10 步执行 + 失败→Agent 分发
│   ├── pipeline_launch.py       ← 启动锁预占、run 绑定与启动审计
│   ├── pipeline_state.py        ← 8 态状态机（状态、待确认方案、公开重试次数和配置调整原子写入）
│   ├── run_registry.py          ← run manifest/status/event/index 的受控访问
│   ├── step_registry.py         ← 步骤、产物契约与执行模块依赖归属
│   ├── action_contract.py        ← 工具效果、确认和审计动作契约
│   ├── recovery_policy.py        ← 按层/错误/步骤的恢复白名单
│   ├── config_schema.py / config_store.py ← 配置外层校验与原子持久化
│   ├── run_store.py / run_provenance.py ← run 事务与可复现性记录
│   ├── process_lifecycle.py      ← 受管子进程停止升级与脱敏审计
│   ├── llm_budget.py             ← 每 run 的 LLM 次数、时长与熔断边界
│   │
│   ├── errors.py            ← ErrorKind 枚举 + StepResult + RetryContext
│   ├── log_parsers.py       ← Gaussian/ORCA/GROMACS 输出结构化解析
│   ├── llm_config.py        ← OpenAI-compatible LLM 配置与 client 构造
│   ├── workflow_config.py   ← config.json 验证/写入/默认值合并
│   ├── env_registry.py      ← 外部软件发现、标准变量与子进程环境
│   ├── env_checker.py       ← 兼容的模块级外部依赖预检入口
│   ├── frontend_api.py      ← 前端专用后端 API (流水线控制/分子目录/3D 查看器/运行助理状态)
│   │
│   ├── quantum/             ← Layer 1 执行层: 量子化学计算
    │   │   ├── struct_g16.py        ← Gaussian 16 结构优化 (gjf→g16→fchk)
    │   │   ├── struct_g09.py        ← Gaussian 09 结构优化 (gjf→g09→fchk)
│   │   ├── struct_orca.py       ← ORCA 结构优化 (gjf→inp→orca→molden)
│   │   ├── fchk_mol2.py         ← G16/G09 fchk→mol2 转换 (解析+Tripos 格式)
│   │   ├── molden_mol2.py       ← ORCA molden→mol2 (Multiwfn 连通性/Mayer 键级)
    │   │   ├── singlepoint_g16.py   ← Gaussian 16 单点能 + ESP 计算
    │   │   ├── singlepoint_g09.py   ← Gaussian 09 单点能 + ESP 计算
│   │   ├── singlepoint_orca.py  ← ORCA 单点能 (molden→SP→molden)
│   │   ├── chg_resp.py          ← RESP 电荷拟合 (Multiwfn 内置 ESP, ORCA-native)
│   │   └── _orca_utils.py       ← ORCA 路径解析工具 (共享)
│   │
│   ├── topology/            ← Layer 2 执行层: 力场拓扑生成
│   │   ├── backends.py          ← 已注册后端调度与 force-field family 校验
│   │   ├── manifest.py / validation.py ← 组件交接与产物契约
│   │   ├── topo_gaff.py         ← Sobtop GAFF (mol2+chg→itp+gro)
│   │   ├── topo_opls.py         ← LigParGen OPLS-AA (SMILES/mol2→itp+gro)
│   │   ├── top_assembly.py      ← 主拓扑组装 (atomtype 去重/命名空间 + #include + topol.top)
│   │   ├── itp_namespace.py     ← LigParGen ITP atomtype 命名空间重写
│   │   └── itp_revise.py        ← ITP 后处理 (删除 [atomtypes] + RESNAME 替换)
│   │
│   └── simulation/          ← Layer 3 执行层: GROMACS MD 模拟
│       ├── protocol.py          ← v2 协议校验与显式旧配置迁移
│       ├── manifest.py          ← 阶段许可、指纹、恢复与运行锁
│       ├── mdp.py               ← MDP 参数生成 (em/eq/prod)
│       ├── box.py               ← Packmol 盒子构建 + auto_from_config
│       ├── em.py                ← 能量最小化执行 + 收敛检查
│       ├── eq.py                ← 三点式退火平衡 + 最终保持段验收
│       ├── prod.py              ← 产出 MD 执行
│       ├── postprocess.py        ← PROD 后确定性后处理与分析产物
│       ├── mdrun_eta.py          ← `mdrun -v` ETA 快照的受控解析与写入
│       ├── pending_action.py     ← EQ 方案替换、确认与 run-local 应用
│       └── _gmx_utils.py        ← grompp+mdrun 封装 + xvg 解析 + 收敛检查器

├── docs/                   ← 文档 (详见 docs/README.md 索引)
│   ├── README.md            ← 唯一导航入口与分类
│   ├── document_registry.md ← 文档用途、状态、责任人和维护规范
│   ├── Willy.md             ← 本文档
│   ├── *_design.md          ← 量子、拓扑、模拟与后处理设计契约
│   ├── status_api.md        ← 状态与运行审计接口
│   ├── revision_strategy.md ← 总体执行计划
│   ├── testing_strategy.md  ← 测试与发布门禁
│   └── project_*.md         ← 项目评估与问题台账

└── pyproject.toml           ← pip install -e .
```

---

## 二、全流程 Workflow

```
用户 NL → Willy Config Agent (OpenAI-compatible LLM)
              │
    ┌─────────┴──────────┐
    │ 11 tools: tools_lookup_molecule, tools_resolve_compound,
    │ tools_lookup_md_defaults, tools_get_box_density,
    │ tools_lookup_basis_set, tools_refresh_structs,
    │ tools_diagnose_error_config, tools_validate_config,
    │ tools_set_backend_quantum, tools_skip_molecule_global,
    │ tools_inspect_quantum_inputs
    └─────────┬──────────┘
              │
    ┌─────────▼──────────┐
    │ Error/Warning 协议  │  ERR_WARN_Build.md
    │  Error: invalid_molecule/ambiguous/invalid_value/invalid_quantum_input/charge_imbalance
    │  Warning: compute_heavy
    └─────────┬──────────┘
              │
    ┌─────────▼──────────┐
    │ run_pipeline.py    │  10 步, 产物→md_run/<run_id>/
    │ 1. struct_*        │  .gjf → g16/g09/ORCA → 结构优化 + fchk/molden
    │ 2. singlepoint_* + │  G16/G09: *_opt.fchk → .mol2；ORCA: *_opt.molden → .mol2
    │    molden/fchk_mol2 │
    │ 3. chg_resp         │  *_opt.fchk → RESP 电荷 → .chg
    │ 4. topo_*          │  .mol2+.chg → Sobtop/LigParGen → .itp+.gro
    │ 5. top_assembly    │  汇总/命名空间 atomtype → topol.top + itp_revise
    │ 6. mdp             │  config.json → em/eq/prod.mdp
    │ 7. box             │  residues → Packmol → model.pdb
    │ 8. em              │  model.pdb → em.tpr/em.gro/em.xtc/em.edr
    │ 9. eq              │  em.gro → eq.tpr/eq.gro/eq.xtc/eq.edr
    │10. prod            │  eq.gro/eq.cpt → prod.tpr/prod.gro/prod.xtc/prod.edr
    └─────────┬──────────┘
              │
    ┌─────────▼──────────┐
    │ GROMACS MD         │  EM → 三点式退火 NPT EQ → PROD
    └────────────────────┘
```

量子化学后端支持 **Gaussian16**（默认）、**Gaussian09** 和 **ORCA**：
- `python3 run_pipeline.py` → 使用 g16
- `python3 run_pipeline.py g09` → 使用 g09
- `python3 run_pipeline.py orca` → 使用 ORCA

失败路径：对应层的 Agent 自动诊断→修复→重试（LLM on Failure Only）。

方案摘要会明确要求用户以文本回复确认。只有最新的待确认方案紧随其后时，回复“运行”“开始运行”“确认运行”或等效的短确认语才会启动流水线；普通讨论、否定语和没有待确认方案时的“运行”不会启动。待确认期间，配置助理按确认、增量修改、方案问题和明确新体系四类意图处理：增量修改会把冻结配置、方案摘要和最近对话一并提交给 LLM，重新审计后生成完整的新方案；方案问题只读回答并保留原方案；明确新体系才会使旧方案失效。任何修改都不会直接写入 `config.json` 或启动工序，必须再次确认。启动回执保留该用户回复与精简结果，方案摘要只在生成方案时展示一次。

运行中的“中止流水线”采用服务端两次确认：首次点击只显示“确认中止”，只有第二次点击且后端已写入停止请求后，界面才显示“正在安全停止”。运行助理文本框中的“中止”“暂停”“稍后”或“确认中止”只作为讨论或等待指令，不能终止工程。

EQ 等阶段失败后，运行助理会展示由服务端提供的脱敏协议调整摘要和重跑位置，并以 `awaiting_confirmation` 表示“LLM 已返回方案、等待当前方案的明确确认”，而非状态未知。若公开证据指向多个可能原因，LLM 会给出最多三个互斥方案，分别列出原因、证据和修改项；用户可回复“方案1/方案一”选择，或回复“确认方案1/确认方案一”直接选择并确认。多方案未选定前，“同意/确认重跑”不会默认执行。用户可提出明确的替代参数或阶段要求；LLM 会生成新的受限方案，旧方案立即失效，且仍须再次确认。确认后的受控恢复固定经过 `retrying`、受影响 MDP 重建，再进入 `running` 执行指定步骤；未回复、暂停或否决始终保留工程和失败现场，等待进一步决定。

可视化区使用固定的 3Dmol 球棍画布，并提供两个并排选择器：左栏“运行目录”列出 `md_run/` 下的合法
run，持有启动锁的工程优先，其余按目录名中的数字降序排列；右栏“结构文件”只枚举左栏所选
`md_run/<run_id>/` 内的 `.pdb`、`.mol2` 文件。初次打开时默认选择左栏首项，因此新提交工程仍会自动
可见；用户也可以切换到历史 run。根目录文件以完整文件名（如 `model.pdb`）显示，子目录文件以 run
相对路径显示以避免重名。任意项目路径、非白名单文件和绝对路径均不进入浏览器。

配置页接受 OpenAI-compatible API Key、Base URL 与 Model。“测试连接”不会保存配置；它以当前
表单值发送一次受 12 秒限制的强制 function-calling 请求，只有收到指定工具调用时才显示
“连接与工具调用可用”。已保存的 Key 不会回填到浏览器，因此重新测试时必须再次填写；缺少该表单
值会得到明确提示。Base URL 保持用户填写的路径，不会自动补 `/v1`；部分兼容服务需要版本前缀，
HTML、非 JSON 或 404 响应会给出检查 `/v1` 路径的建议。保存后仍须重启前端，让各 Agent 重载
LLM client。

---

## 三、LLM Tools

### Layer 0 — Config Agent (11 tools)

| Tool | 功能 |
|------|------|
| `tools_lookup_molecule` | TF-IDF 向量检索 charge/spin/basis/别名 |
| `tools_resolve_compound` | LiTFSI→Li+TFSI, 硝酸锂→Li+NO3 |
| `tools_lookup_md_defaults` | 默认温度/时间/步长/热浴/压浴 |
| `tools_get_box_density` | 初始建盒目标质量密度默认值（0.7 g/cm3）与拓扑质量计算说明 |
| `tools_lookup_basis_set` | 按原子数推荐基组 |
| `tools_refresh_structs` | 重载 registry (上传新分子后) |
| `tools_diagnose_error_config` | 配置阶段错误诊断 |
| `tools_validate_config` | 验证草稿 config.json |
| `tools_set_backend_quantum` | 切换 g16/g09/orca 后端 |
| `tools_skip_molecule_global` | 将分子加入跳过列表 |
| `tools_inspect_quantum_inputs` | 审计所选后端原始输入的电荷、自旋和结构完整性 |

### Layer 1 — Quantum Agent (8 tools)

`tools_retry_struct_g16` / `tools_retry_struct_g09` / `tools_retry_struct_orca` / `tools_retry_mol2_conversion` / `tools_retry_chg_g16` / `tools_retry_chg_g09` / `tools_retry_chg_orca` / `tools_diagnose_error_quantum` / `tools_modify_config_molecule` / `tools_skip_molecule_quantum`

### Layer 2 — Topology Agent (6 tools)

`tools_retry_topo_gaff` / `tools_retry_topo_opls` / `tools_retry_top_assembly` / `tools_diagnose_error_topology` / `tools_modify_config_topology` / `tools_skip_molecule_topology`

### Layer 3 — Simulation Agent (14 tools)

`tools_run_em_simulation` / `tools_run_eq_simulation` / `tools_run_prod_simulation` / `tools_retry_mdp` / `tools_retry_box` / `tools_retry_em` / `tools_retry_eq` / `tools_retry_prod` / `tools_configure_outputs_simulation` / `tools_configure_prod_simulation` / `tools_diagnose_error_simulation` / `tools_modify_config_simulation` / `tools_migrate_md_config_simulation` / `tools_lookup_mdrun_knowledge`

模拟层可诊断并提出协议调整，但不能以工具参数确认或执行温度、时长、耦合、时间步或输出精度变更。EQ 失败会保留当前 run 并进入 `awaiting_confirmation`；用户可以请求 LLM 用受限字段生成替代方案，但该操作只替换待确认动作，不改写配置或启动工序。用户确认后，服务端仅对该 run 的当前待确认动作校验配置指纹并受控重跑：EQ 参数修改从 Step 9 开始，建盒修改从 Step 7 开始，状态先进入 `retrying` 再转为 `running`。未确认时不会改写配置或启动任何工序。

`tools_lookup_mdrun_knowledge` 是唯一供 EQ proposal 使用的知识库只读工具；按 `number + name` 校验，最多 2 次/实际失败、3 条/次。公开方案会标示实际命中的条目、版本兼容提醒，或“LLM 未经知识库验证的推断”。

### Run Assistant（9 个只读工具）

`tools_list_runs` / `tools_get_status_run` / `tools_get_report_step` / `tools_list_artifacts_run` / `tools_explain_error_run` / `tools_get_config_run` / `tools_get_box_parameters_run` / `tools_get_environment_run` / `tools_get_md_eta_run`

**总计：50 个工具**，其中 Layer 0-3 为 41 个流水线工具，Run Assistant 为 9 个只读工具。命名规范见 `naming_convention.md`。

---

## 四、Error/Warning 协议

见 `ERR_WARN_Build.md`。LLM 输出统一 JSON：
```json
{"error": null, "warnings": [], "backend": "g16", "molecules": {...}, "residues": {...}, "md": {...}, "defaults": {...}}
```
Error type: invalid_molecule / invalid_value / ambiguous / invalid_quantum_input / charge_imbalance (阻塞)
Warning type: compute_heavy (非阻塞)

---

## 五、初始建盒

默认提示为“初始体积将由使用默认0.7g/cm3的密度猜测”。Step 7 从当前 `.itp` 的 `[ atoms ]` 质量计算总质量，再计算 `L_A = 10 * cbrt(M_amu * 1.66053906660e-3 / rho_g_cm3)`；Packmol 以显式 `pbc L L L` 写入实际周期盒。运行助理可只读查询已审计的实际盒矢量、体积和初始质量密度。

---

## 六、运行方式

```bash
# 环境检查
python3 -m willy.env_checker

# Web UI
python3 app.py                          # → http://localhost:7860

# CLI 流水线
python3 run_pipeline.py                 # g16 后端 (默认)
python3 run_pipeline.py g09             # G09 后端
python3 run_pipeline.py orca            # ORCA 后端

# 运行产物保留（默认仅预览；显式确认后删除旧 run）
python3 scripts/prune_runs.py --keep 1
python3 scripts/prune_runs.py --keep 1 --apply
python3 benchmarks/llm_score.py         # LLM 评分测试
```

---

## 七、命名规范与架构演进

当前架构是本书面规范的三次迭代结果：

1. **初始**：14 个 `.py` 文件平铺在 `src/willy/` 根目录
2. **命名规范化**：统一为三套命名体系 — `tools_{op}_{target}_{scope}` (LLM tool 名) / `toolist_{scope}.py` (工具模块) / `agent_{scope}.py` (Agent 文件)。详见 `naming_convention.md`
3. **prompts 内聚**：`prompts.py` 拆分，每个 `agent_{scope}.py` 持有自己的 system prompt，`pipeline_orchestrator` 直接实例化 `QuantumAgent()`/`TopologyAgent()`/`SimulationAgent()`

**重构经验**见 `revision_strategy.md` 的“重构同步检查清单”（含步骤/动作契约、配置持久化和运行治理在内的关联方检查清单）。文档用途、状态和更新责任见 `document_registry.md`。

---

→ **[docs/README.md](README.md)** — 文档导航与分类；**[document_registry.md](document_registry.md)** — 文档状态与维护规范。
