# Willy 命名规范

> 版本 1.1 · 2026-07-29 · 适用于所有 `src/willy/` 下的模块、文件、LLM tool 名称

## 实施状态

| 规范 | 状态 |
|------|:---:|
| LLM tool 名称: `tools_{操作}_{目标}_{scope/backend}` | ✅ 已完成 — 全部 32 个 tool 已重命名 |
| Tool 模块文件: `toolist_{scope}.py` | ✅ 已完成 — 4 个文件 |
| Agent 文件: `agent_{scope}.py` | ✅ 已完成 — 4 个文件（config/quantum/topology/simulation） |
| prompts 拆分到各 agent 文件 | ✅ 已完成 — `prompts.py` 已删除，每个 `agent_{scope}.py` 持有自己的 prompt |
| 执行模块命名: `{动作}_{backend}.py` | ✅ 已完成 — `struct_g16.py` / `mdp.py` / `topo_gaff.py` 等 |

**当前 agent ↔ toolist ↔ 执行模块对应关系：**

| Agent | Toolist | 执行模块 |
|------|------|------|
| `agent_config.py` | `toolist_global.py` | (不直接调用执行模块) |
| `agent_quantum.py` | `toolist_quantum.py` | `quantum/struct_{g16,orca}.py` `quantum/mol2_{g16,orca}.py` `quantum/chg_{g16,orca}.py` `quantum/resp_maker.py` |
| `agent_topology.py` | `toolist_topology.py` | `topology/topo_{gaff,opls}.py` `topology/top_assembly.py` `topology/itp_revise.py` |
| `agent_simulation.py` | `toolist_simulation.py` | `simulation/mdp.py` `simulation/box.py` `simulation/{em,eq,prod}.py` `simulation/setup.py` `simulation/_gmx_utils.py` |

---

## 一、LLM Tool 命名

### 格式

```
tools_{操作}_{目标}_{scope/backend}
```

| 段 | 含义 | 示例 |
|----|------|------|
| `tools` | 固定前缀 | `tools` |
| `{操作}` | 动词，描述动作 | `lookup` `retry` `diagnose` `modify` `skip` `validate` `resolve` `refresh` `get` `set` |
| `{目标}` | 名词，被操作的对象 | `molecule` `compound` `error` `struct` `chg` `config` `box` `mdp` `topology` `backend` |
| `{scope/backend}` | 适用范围或计算后端。全局工具省略。 | `g16` `orca` `quantum` `topology` `simulation` `global` |

### 规则

1. **全局工具（Layer 0 Config）省略第三段**，如 `tools_lookup_molecule`
2. **同层共享工具用层名作第三段**，如跨三层的 `skip_molecule` → `tools_skip_molecule_quantum` / `tools_skip_molecule_topology` / `tools_skip_molecule_simulation`
3. **后端特定工具用后端名作第三段**，如 `tools_retry_struct_g16` / `tools_retry_struct_orca`
4. **后端无关的层专属工具用层名作第三段**，如 `tools_diagnose_error_quantum`

### 完整映射表

#### Layer 0 — toolist_global（10 tools）

| 当前名称 | 新名称 | 操作 | 目标 | scope |
|------|------|------|------|------|
| `lookup_molecule` | `tools_lookup_molecule` | lookup | molecule | — |
| `resolve_compound` | `tools_resolve_compound` | resolve | compound | — |
| `lookup_md_defaults` | `tools_lookup_md_defaults` | lookup | md_defaults | — |
| `get_box_density` | `tools_get_box_density` | get | box_density | — |
| `lookup_basis_set` | `tools_lookup_basis_set` | lookup | basis_set | — |
| `refresh_structs` | `tools_refresh_structs` | refresh | structs | — |
| `diagnose_config_error` | `tools_diagnose_error_config` | diagnose | error | config |
| `validate_config` | `tools_validate_config` | validate | config | — |
| `set_quantum_backend` | `tools_set_backend_quantum` | set | backend | quantum |
| `skip_molecule` | `tools_skip_molecule_global` | skip | molecule | global |

#### Layer 1 — toolist_quantum（8 tools）

| 当前名称 | 新名称 | 操作 | 目标 | scope/backend |
|------|------|------|------|------|
| `retry_struct_maker` | `tools_retry_struct_g16` | retry | struct | g16 |
| `retry_orca_struct_maker` | `tools_retry_struct_orca` | retry | struct | orca |
| `retry_mol2_conversion` | `tools_retry_mol2_conversion` | retry | mol2 | conversion |
| `retry_chg_maker` | `tools_retry_chg_g16` | retry | chg | g16 |
| `retry_orca_chg_maker` | `tools_retry_chg_orca` | retry | chg | orca |
| `diagnose_quantum_error` | `tools_diagnose_error_quantum` | diagnose | error | quantum |
| `modify_molecule_config` | `tools_modify_config_molecule` | modify | config | molecule |
| `skip_molecule` | `tools_skip_molecule_quantum` | skip | molecule | quantum |

#### Layer 2 — toolist_topology（6 tools）

| 当前名称 | 新名称 | 操作 | 目标 | scope/backend |
|------|------|------|------|------|
| `retry_sobtop` | `tools_retry_topo_gaff` | retry | topo | gaff |
| `retry_ligpargen` | `tools_retry_topo_opls` | retry | topo | opls |
| `retry_top_assembly` | `tools_retry_top_assembly` | retry | top | assembly |
| `diagnose_topology_error` | `tools_diagnose_error_topology` | diagnose | error | topology |
| `modify_topology_config` | `tools_modify_config_topology` | modify | config | topology |
| `skip_molecule` | `tools_skip_molecule_topology` | skip | molecule | topology |

#### Layer 3 — toolist_simulation（8 tools）

| 当前名称 | 新名称 | 操作 | 目标 | scope/backend |
|------|------|------|------|------|
| `retry_mdp_generation` | `tools_retry_mdp` | retry | mdp | — |
| `retry_box_generation` | `tools_retry_box` | retry | box | — |
| `retry_em` | `tools_retry_em` | retry | em | — |
| `retry_eq` | `tools_retry_eq` | retry | eq | — |
| `retry_prod` | `tools_retry_prod` | retry | prod | — |
| `diagnose_md_error` | `tools_diagnose_error_simulation` | diagnose | error | simulation |
| `modify_md_config` | `tools_modify_config_simulation` | modify | config | simulation |
| `skip_molecule` | `tools_skip_molecule_simulation` | skip | molecule | simulation |

---

## 二、Tool 模块文件命名

### 格式

```
toolist_{scope}.py
```

| 段 | 含义 | 示例 |
|----|------|------|
| `toolist` | 固定前缀 | `toolist` |
| `{scope}` | 适用范围 | `global` `quantum` `topology` `simulation` |

### 映射表

| 当前文件名 | 新文件名 | scope |
|------|------|------|
| `knowledge_tools.py` | `toolist_global.py` | Config Agent 使用的全局工具（10 tools） |
| `quantum_tools.py` | `toolist_quantum.py` | Quantum Agent 专属工具（8 tools） |
| `topology_tools.py` | `toolist_topology.py` | Topology Agent 专属工具（6 tools） |
| `simulation_tools.py` | `toolist_simulation.py` | Simulation Agent 专属工具（8 tools） |

### 规则

1. 一个 `toolist_*.py` 文件包含两层内容：**Tool 定义**（JSON Schema 列表）+ **Tool Handler**（`handle_*_tool_call` 分发函数）
2. 按 scope 划分，不按"action vs diagnostic vs config"功能划分
3. 未来新增 scope（如 `spectroscopy`）时，新增 `toolist_spectroscopy.py`

---

## 三、Agent 文件命名

### 格式

```
agent_{scope}.py
```

| 段 | 含义 | 示例 |
|----|------|------|
| `agent` | 固定前缀 | `agent` |
| `{scope}` | 适用范围 | `config` `quantum` `topology` `simulation` |

### 映射表

| 当前文件名 | 新文件名 | scope | 说明 |
|------|------|------|------|
| `agent.py` | `agent_config.py` | config | Layer 0: NL → config.json 对话循环 |
| *(不存在)* | `agent_quantum.py` | quantum | Layer 1: 继承 `LayerAgent`，量子化学修复 |
| *(不存在)* | `agent_topology.py` | topology | Layer 2: 继承 `LayerAgent`，力场拓扑修复 |
| *(不存在)* | `agent_simulation.py` | simulation | Layer 3: 继承 `LayerAgent`，MD 模拟修复 |
| `layer_agent.py` | 保持不动 | — | Agent 基类（tool-calling 循环 + 重试 + escalation），非 agent 本身 |

### 规则

1. `agent_{scope}.py` 必须是 `LayerAgent` 的子类（或等价实现了 `handle_failure` 接口的类）
2. 每个 `agent_{scope}.py` 负责：
   - 持有本层的 `system_prompt` 常量
   - import 本层的 `toolist_{scope}` 模块获取 tools 和 handler
   - 实现层特定的 `handle_failure` 逻辑（如需重写）
3. `layer_agent.py` 作为基类/基础设施，不遵循 `agent_` 前缀规则
4. 未来新增 scope 时新增 `agent_{new_scope}.py`

### 当前 pipeline_orchestrator._init_agents 改造说明

当前三个 LayerAgent 是在 `pipeline_orchestrator._init_agents()` 中用 `LayerAgent(...)` 直接构造的。按本规范后，改为：

```python
from willy.agent_quantum import QuantumAgent
from willy.agent_topology import TopologyAgent
from willy.agent_simulation import SimulationAgent

self._agents[1] = QuantumAgent(llm_client=self.llm_client, on_action=lambda a: self._sm.add_action(a))
self._agents[2] = TopologyAgent(llm_client=self.llm_client, on_action=lambda a: self._sm.add_action(a))
self._agents[3] = SimulationAgent(llm_client=self.llm_client, on_action=lambda a: self._sm.add_action(a))
```

每个 `agent_{scope}` 子类内部持有自己的 prompt、tools、tool_handler，pipeline_orchestrator 不再直接 import prompts 或 tools。

---

## 四、目录结构总览

```
src/willy/
├── _paths.py
│
├── agent_config.py          ← Layer 0: NL→config (含 CONFIG_AGENT_PROMPT)
├── agent_quantum.py         ← Layer 1: Quantum Agent (含 QUANTUM_AGENT_PROMPT)
├── agent_topology.py        ← Layer 2: Topology Agent (含 TOPOLOGY_AGENT_PROMPT)
├── agent_simulation.py      ← Layer 3: Simulation Agent (含 SIMULATION_AGENT_PROMPT)
├── layer_agent.py           ← Agent 基类（不遵循 agent_ 命名）
│
├── toolist_global.py        ← Layer 0 工具定义 + handler
├── toolist_quantum.py       ← Layer 1 工具定义 + handler
├── toolist_topology.py      ← Layer 2 工具定义 + handler
├── toolist_simulation.py    ← Layer 3 工具定义 + handler
│
├── pipeline_orchestrator.py
├── pipeline_state.py
│
├── errors.py
├── log_parsers.py
├── llm_config.py
├── env_checker.py
├── frontend_api.py
│
├── quantum/                 ← 执行层（不变）
├── topology/                ← 执行层（不变）
└── simulation/              ← 执行层（不变）
```

核心变化：

| 维度 | 之前 | 之后 |
|------|------|------|
| prompt 归属 | `prompts.py` 集中存放，pipeline 跨层 import | 各自 `agent_{scope}.py` 内部持有 |
| tool 定义 | `*_tools.py` 分散命名 | 统一 `toolist_{scope}.py` |
| LLM tool 名 | 口语化：`retry_chg_maker` / `retry_orca_chg_maker` | 结构化：`tools_retry_chg_g16` / `tools_retry_chg_orca` |
| agent 文件 | 只有 `agent.py` + `layer_agent.py`，Layer 1-3 无独立文件 | 每层一个 `agent_{scope}.py` |
| 同名 tool 冲突 | `skip_molecule` 在 3 个文件各定义一次，名称相同 | `tools_skip_molecule_quantum/topology/simulation` 各自唯一 |

---

## 五、检查清单

新增一个 tool 或 agent 时，对照此清单：

- [ ] LLM tool name 符合 `tools_{操作}_{目标}_{scope/backend}` 格式
- [ ] tool 定义放入正确的 `toolist_{scope}.py` 文件
- [ ] tool handler 的 `if tool_name == "..."` 分支使用新名称
- [ ] 对应的 `agent_{scope}.py` prompt 中 "可用工具" 列表同步更新
- [ ] `pipeline_orchestrator._init_agents()` 中的 import 路径正确
- [ ] 不与现有 tool name 冲突（grep `tools_` 全仓检查）
