# Willy 修订总计划与边界策略

> 版本：1.0  
> 日期：2026-07-31  
> 适用范围：`src/willy/`、`app.py`、`run_pipeline.py`、`docs/`、`tests/`、`config.json`、运行产物目录与 vendor 依赖。  
> 目标：给后续修订建立统一路线图，明确每个部分的职责边界，避免重构时跨层漂移、重复实现和文档失真。

---

## 一、修订总目标

未来修订围绕一个核心目标推进：

> 将 Willy 从“可运行的研究型 MVP”推进到“端到端闭环、边界清晰、可测试、可观测、可安全运行的内部自动化平台”。

当前最大的结构性问题不是单点 bug，而是三类边界不够稳定：

1. **产品承诺边界**：文档声称能完成 GROMACS EM/NVT/NPT/PROD，但主编排器目前只到 Packmol 建盒。
2. **模块职责边界**：部分执行层、toolist、orchestrator、env checker 之间的命名和契约不同步。
3. **运行安全边界**：前端、shell、进程控制、API key、运行产物管理还没有形成严格约束。

修订策略必须先稳定边界，再扩展能力。

---

## 二、责任归属

本计划由 **0 号总工程师 Codex** 维护。0 号负责判断修订优先级、裁决跨层边界、定义验收门槛，并协调 1-5 号领域工程师的改动合流。

职责关系：

| 角色 | 责任 |
|------|------|
| 0 号总工程师 | 路线图、边界裁决、质量门禁、安全治理、文档一致性 |
| 1 号量子层工程师 | `quantum/` 执行层、量子产物契约、量子错误诊断 |
| 2 号拓扑层工程师 | `topology/` 执行层、力场接口、主拓扑和 itp 修订 |
| 3 号模拟层工程师 | `simulation/` 执行层、MDP/Packmol/GROMACS 主流程 |
| 4 号前端交互工程师 | `app.py`、`frontend_api.py`、进度展示、用户交互 |
| 5 号文档架构师 | 命名规范、设计文档、知识库、索引和矛盾清单 |

当两个领域对同一边界有不同判断时，以 0 号总工程师在本文档和 `docs/employees.md` 中定义的边界为准；若边界需要调整，必须先更新文档，再改代码。

---

## 三、总体路线图

### Phase 0：基线收敛

目标：让当前项目进入可维护状态。

范围：

- 修复已确认的阻塞 bug。
- 让测试基线全绿。
- 统一当前实际能力和文档描述。
- 收紧最基本的安全风险。

必须完成：

- 修复 `tools_validate_config` 导入错误。
- 统一 `PipelineOrchestrator.ensure()` 和 `env_checker._DEPENDENCIES.needed_by` 的模块名。
- 修复 `top_assembly.build()` 中 `itp_revise` 写死 `TOPO_DIR` 的问题。
- 决定主流程状态：当前 7 步是否临时作为 setup pipeline，还是立即升级为含 GROMACS 的完整 pipeline。
- 将 Gradio 默认绑定改为本地地址，或增加远程访问认证。
- `pytest -q` 全绿。

验收标准：

```text
pytest -q
python3 -m willy.env_checker
python3 -m tests.llm_eval.run_eval
```

其中测试必须全绿；环境检查允许标注可选依赖缺失，但主线依赖必须就绪。

### Phase 1：契约稳定

目标：让各层之间只通过明确契约通信，减少隐式路径和字符串匹配。

范围：

- 统一 `StepResult` 序列化。
- 固化 `config.json` schema。
- 统一步骤编号、步骤名、状态机字段。
- 明确外部依赖注册方式。

必须完成：

- 给 `StepResult` 增加统一 `to_dict()`，去掉各 `toolist_*.py` 中重复的 `_step_to_dict()`。
- 用 JSON Schema 或 Pydantic 定义 `config.json`。
- 建立唯一的 step registry，例如 `STEP_ID`, `step_name`, `layer`, `required_deps`, `outputs`。
- 所有执行模块返回 `StepResult`，禁止裸 `raise` 穿出 pipeline 公共接口。
- `env_checker` 的 `needed_by` 只使用 step registry 中的规范名称。

验收标准：

- 任意一步失败时，orchestrator、状态机、Agent、前端展示看到的是同一个 `step_name` 和 `ErrorKind`。
- grep 不再出现重复的 `_step_to_dict`。
- 新增配置字段必须同时出现在 schema、defaults、文档和测试中。

### Phase 2：端到端 MD 闭环

目标：让主流程真正完成从结构输入到 GROMACS 生产运行。

范围：

- 接入文件收集。
- 接入 EM。
- 明确 NVT/NPT 设计。
- 接入 EQ 和 PROD。
- 调整状态机与前端进度。

必须完成：

- 在 `PipelineOrchestrator._build_steps()` 中接入 `simulation.setup.collect_files()`。
- 接入 `simulation.em.run_em()`。
- 明确是否拆分 NVT 和 NPT。如果拆分，应新增 `nvt.py` 或将 `eq.py` 明确重命名/重构。
- 接入 `simulation.eq.run_eq()`。
- 接入 `simulation.prod.run_prod()`。
- 修复 `tools_retry_prod` 构造 `extra_mdrun` 但未透传的问题。
- 更新 `PipelineStatus.total_steps`、前端进度标签、README 和设计文档。

建议最终主流程：

```text
1. quantum_struct
2. quantum_sp_mol2
3. quantum_resp
4. topology_molecule
5. topology_assembly
6. simulation_mdp
7. simulation_box
8. simulation_setup
9. simulation_em
10. simulation_nvt
11. simulation_npt
12. simulation_prod
```

如果短期不实现 NVT，则文档必须明确当前只支持 NPT EQ，不能继续声称 NVT 已接入。

验收标准：

- 一次 `run_pipeline.py --no-llm` 至少能在小体系 fixture 上跑完整 setup/MD mock 流程。
- 所有 simulation retry tools 对应的主流程步骤真实存在。
- 前端进度不再显示和实际步骤不一致的标签。

### Phase 3：Agent 修复能力增强

目标：让 Agent 的修复行为可控、可测、可升级。

范围：

- 工具权限治理。
- 重试次数治理。
- 升级信息标准化。
- LLM API 稳定性。

必须完成：

- `TOOL_META` 成为统一规范，覆盖所有 tool，并标明 `mutating`、`risk`、`layer`、`requires_confirmation`。
- 高风险工具调用前有明确策略：自动执行、用户确认、或只在 CLI 模式允许。
- DeepSeek/OpenAI client 配置 timeout 和 retry/backoff。
- `LayerAgent` 捕获 JSON 解析错误、tool 异常和 LLM 异常时写入结构化 action。
- 扩展 `tests/llm_eval`，加入“不能跨层调用不存在工具”“升级信息完整性”等场景。

验收标准：

- Mock eval 继续全通过。
- 升级信息必须包含 `layer`、`step_name`、`error_kind`、`attempts_made`、`actions_tried`、`recommendation`、`backup_plan`。
- 高风险工具不允许绕过 tool handler 直接执行 shell。

### Phase 4：前端与运行管理产品化

目标：让 Web UI 成为可安全使用的控制面板，而不是直接暴露脚本入口。

范围：

- 认证。
- 进程管理。
- 运行目录管理。
- 产物浏览。
- 离线资源。

必须完成：

- 默认 `server_name="127.0.0.1"`。
- 远程访问必须启用认证。
- 移除 `os.system`，用 `subprocess.run([...], shell=False)` 或 PID/PGID API 管理进程。
- 运行目录作为一等对象：每次 run 有 `run_id`、`run_dir`、`status.json`、日志、配置快照。
- 前端 3Dmol 使用 vendored `vendor/3Dmol-min.js` 或明确联网要求。
- 上传文件经过类型、大小和名称校验。

验收标准：

- 前端不直接拼接 shell 命令。
- 停止流水线只影响当前 run 的进程组，不误杀其他用户或其他项目进程。
- 页面展示的 run_dir、状态、错误和产物路径一致。

### Phase 5：仓库治理与发布准备

目标：让仓库适合多人协作、复现和发布。

范围：

- 运行产物管理。
- vendor 依赖说明。
- CI。
- 文档同步。

必须完成：

- 扩展 `.gitignore`，排除 `status.json`、`.md_counter`、临时 lock/pid、默认运行产物。
- 将示例数据与真实运行产物分离。
- 为 vendor 二进制补充来源、版本、license 和校验方式。
- `pyproject.toml` 补齐 runtime、test、dev dependencies。
- 加 CI：运行 unit tests、import check、文档链接检查。

验收标准：

- 新 clone 后按 README 能安装并运行基础检查。
- CI 能阻止测试红线、依赖缺失和文档索引遗漏。
- 文档目录索引和实际文件一致。

---

## 四、模块边界

### 1. Frontend 边界

文件：

- `app.py`
- `src/willy/frontend_api.py`

职责：

- UI 布局。
- 用户输入收集。
- 文件上传入口。
- 分子查看器渲染。
- 状态轮询。
- 启动/停止流水线的用户操作入口。

允许：

- 调用 `agent_config.chat()`。
- 调用 `frontend_api` 中受控 API。
- 读取状态摘要和分子展示数据。

禁止：

- 在 `app.py` 中直接访问任意项目路径。
- 在 `app.py` 中直接调用 shell。
- 在 UI 层直接修改 `config.json`。
- 在 UI 层直接 import 量子、拓扑、模拟执行模块。

修订原则：

- `app.py` 只保留 Gradio 组件和事件绑定。
- 所有文件系统、进程、路径逻辑都收敛到 `frontend_api.py`。
- `frontend_api.py` 内的危险操作必须 shell-free，并受 run_id/PID 边界限制。

### 2. Config Agent 边界

文件：

- `src/willy/agent_config.py`
- `src/willy/toolist_global.py`
- `src/willy/llm_config.py`
- `docs/knowledge.md`

职责：

- 自然语言到配置草案。
- 分子别名和化合物解析。
- `config.json` 验证和默认值合并。
- 配置阶段的轻量诊断。

允许：

- 读取 `docs/knowledge.md`。
- 扫描 `struct/*.gjf` 作为可用分子列表。
- 写入 `config.json`，但必须通过 `llm_config.apply_config()` 或统一配置 API。

禁止：

- 直接运行量子、拓扑、模拟外部程序。
- 直接修改运行目录产物。
- 在 prompt 中承诺未接入的 pipeline 能力。

修订原则：

- 配置 Agent 只产出结构化配置，不负责执行。
- `toolist_global.py` 是 LLM tool schema 和 handler，不放复杂科学计算逻辑。
- `llm_config.py` 是配置 schema/default/validation 的唯一来源。

### 3. Orchestrator 边界

文件：

- `run_pipeline.py`
- `src/willy/pipeline_orchestrator.py`
- `src/willy/pipeline_state.py`

职责：

- 获取 pipeline lock。
- 创建 run_dir。
- 按 step registry 顺序执行步骤。
- 调用依赖预检。
- 处理 `StepResult`。
- 失败时调用对应 LayerAgent。
- 写入状态机。

允许：

- import 每层公开执行函数。
- 调用 `env_checker.ensure()`。
- 调用 LayerAgent。
- 收集 artifacts。

禁止：

- 在 orchestrator 中写复杂分子/拓扑/MD 业务逻辑。
- 直接解析外部工具日志做领域判断。
- 调用执行模块的私有函数，除非该函数先升级为公开接口。
- 用硬编码字符串绕过 step registry。

修订原则：

- Orchestrator 只管“何时执行”和“失败后交给谁”，不管“具体怎么计算”。
- 步骤编号、名称、层级、依赖和产物应来自统一 step registry。
- 每次增删步骤必须同步状态机、前端标签、Agent prompt、测试和文档。

### 4. LayerAgent 边界

文件：

- `src/willy/layer_agent.py`
- `src/willy/agent_quantum.py`
- `src/willy/agent_topology.py`
- `src/willy/agent_simulation.py`

职责：

- 根据失败上下文选择工具。
- 管理重试次数。
- 记录 action。
- 判断是否升级到用户。

允许：

- 通过 tool calling 间接修改配置或重试步骤。
- 读取失败上下文、config 文本、artifacts 摘要。

禁止：

- 直接调用 shell。
- 直接写业务产物文件。
- 调用未在本层 tool list 中定义的工具。
- 跨层直接 import 其他层执行模块。

修订原则：

- Agent 的能力边界由 `toolist_{scope}.py` 决定。
- prompt 只能描述真实存在的工具和真实接入的步骤。
- 升级不是失败兜底文本，而是结构化结果。

### 5. Toolist 边界

文件：

- `src/willy/toolist_global.py`
- `src/willy/toolist_quantum.py`
- `src/willy/toolist_topology.py`
- `src/willy/toolist_simulation.py`

职责：

- 定义 LLM function calling JSON Schema。
- 将 LLM 参数转换为执行层公开函数调用。
- 将执行结果转换为 JSON。
- 维护工具元数据。

允许：

- 调用本层执行模块公开 API。
- 进行轻量参数适配和路径解析。
- 写入配置中的本层字段。

禁止：

- 承载复杂业务算法。
- 和 orchestrator 维护不同的 step_name。
- silently swallow 执行异常而不返回结构化错误。
- 重复实现通用序列化逻辑。

修订原则：

- 每个 tool handler 分支必须有单元测试。
- 每个 tool schema 必须在 prompt 中可见，且名称一致。
- Tool 返回结构必须稳定，尤其是 `_step_result` 和 `_diagnosis`。

### 6. Quantum 执行层边界

目录：

- `src/willy/quantum/`

职责：

- 结构优化。
- 单点计算。
- fchk/molden/mol2 转换。
- RESP 电荷生成。

输入：

- 显式路径。
- 分子配置。
- defaults。

输出：

- `StepResult`
- `.fchk`
- `*_opt.fchk`
- `.molden`
- `.mol2`
- `.chg`
- 日志路径。

禁止：

- 调用 LLM。
- 直接修改 `config.json`。
- 直接操作 topology 或 simulation 目录。
- 裸异常穿出公开 pipeline 函数。

修订原则：

- 外部程序失败必须转换为 `StepError`。
- 产物命名变更必须同步 toolist、orchestrator、docs 和 tests。
- G16/ORCA 后端差异只能在 quantum 层内部处理，对上层暴露统一产物契约。

### 7. Topology 执行层边界

目录：

- `src/willy/topology/`

职责：

- Sobtop/GAFF 拓扑生成。
- LigParGen/OPLS 后备。
- 主拓扑组装。
- `.itp` 修订。

输入：

- `.mol2`
- `.chg`
- `.itp`
- `.gro`
- `config.json` residues/topology 段。

输出：

- `.itp`
- `.gro`
- `topol.top`
- `StepResult`

禁止：

- 重新生成量子产物。
- 执行 GROMACS MD。
- 在传入 `topo_dir` 时写死根目录 `topo/`。

修订原则：

- 拓扑层只消费量子层产物，不回写量子层文件。
- `top_assembly` 和 `itp_revise` 必须操作同一 topo_dir。
- 混合力场、atomtype 冲突必须显式诊断，不允许静默继续。

### 8. Simulation 执行层边界

目录：

- `src/willy/simulation/`

职责：

- MDP 生成。
- Packmol 输入和盒子构建。
- MD 运行目录准备。
- GROMACS EM/EQ/PROD。
- GROMACS 日志和能量稳定性检查。

输入：

- `topol.top`
- `.itp`
- `.gro`
- `.pdb`
- `.mdp`
- `config.json` md/box/residues 段。

输出：

- `model.pdb`
- `em.gro`
- `eq.gro`
- `prod.xtc` / `prod.gro` / `prod.log`
- `.tpr`
- `.edr`
- `.xvg`
- `StepResult`

禁止：

- 生成 RESP 电荷。
- 修改 topology 参数以外的上游产物。
- 将 GROMACS step 编号和 orchestrator step 编号混用。

修订原则：

- `mdp.py` 只生成参数文件，不运行 GROMACS。
- `box.py` 只负责建盒，不负责 EM/EQ/PROD。
- `setup.py` 只收集运行输入，不做模拟。
- `em.py`、`eq.py`、`prod.py` 只运行对应阶段。

### 9. Shared Infra 边界

文件：

- `src/willy/errors.py`
- `src/willy/env_checker.py`
- `src/willy/log_parsers.py`
- `src/willy/_paths.py`

职责：

- 共享错误模型。
- 外部依赖检查。
- 日志解析。
- 项目根路径解析。

禁止：

- import 具体执行层形成反向依赖。
- 写业务产物。
- 调用 LLM。

修订原则：

- shared infra 必须低耦合。
- 新 `ErrorKind` 必须有测试和文档说明。
- 新外部依赖必须集中注册在 `env_checker.py`。

### 10. Docs 和 Tests 边界

目录：

- `docs/`
- `tests/`
- `benchmarks/`

职责：

- 文档描述真实系统能力。
- 测试保护模块契约。
- eval 衡量 LLM Agent 行为。

禁止：

- 文档继续引用已删除文件名而不标注过期。
- 测试依赖固定源码行号。
- benchmark 隐式读取真实 API key 并输出敏感信息。

修订原则：

- 改代码必须同步相关设计文档。
- 改文件名必须同步 `docs/README.md` 和矛盾清单。
- 新增 bug 复现测试后，下一步必须把它改成修复后的回归测试。

### 11. Data、Artifacts 和 Vendor 边界

目录：

- `struct/`
- `topo/`
- `md_run/`
- `vendor/`

职责：

- `struct/`：输入结构和必要示例结构。
- `topo/`：拓扑示例或默认拓扑产物。
- `md_run/`：运行产物。
- `vendor/`：第三方二进制和静态资源。

禁止：

- 将临时运行产物和长期示例数据混在一起而不区分。
- vendor 二进制无来源、版本、license 说明。
- 默认运行覆盖示例数据。

修订原则：

- 每次 run 必须写入独立 run_dir。
- 运行产物默认不进入 Git。
- 示例数据应小而稳定，真实大产物应外部存储或按需生成。

---

## 五、跨层契约

### 1. 数据流契约

标准数据流：

```text
自然语言
  -> config draft
  -> validated config.json
  -> quantum outputs
  -> topology outputs
  -> simulation setup outputs
  -> GROMACS run outputs
  -> status/artifacts
```

任何层只能消费上一层公开产物，不允许跳过契约直接猜路径。

### 2. 错误契约

所有 pipeline 公共函数必须返回：

```python
StepResult(
    step_name=...,
    step_index=...,
    success=...,
    error=StepError(...) | None,
    outputs={...},
    artifacts=[...],
)
```

禁止公开函数直接抛出：

- `RuntimeError`
- `ValueError`
- `FileNotFoundError`
- `subprocess.TimeoutExpired`

内部 helper 可以抛异常，但公开入口必须捕获并转换为 `StepResult`。

### 3. 配置契约

`config.json` 分段边界：

| 字段 | 所属边界 |
|------|------|
| `backend` | Config / Quantum |
| `molecules` | Config / Quantum |
| `residues` | Config / Topology / Simulation |
| `md` | Simulation |
| `defaults` | Quantum |
| `topology` | Topology |
| `box` | Simulation |
| `skipped_molecules` | Orchestrator / Agents |
| `skip_reasons` | Orchestrator / Agents |

任何新增字段必须说明消费者是谁。

### 4. 产物契约

产物命名应固定：

| 层 | 关键产物 |
|------|------|
| Quantum Step 1 | `{name}.fchk`, `{name}.log` |
| Quantum Step 2 | `{name}_opt.fchk`, `{name}.mol2` |
| Quantum Step 3 | `{name}.chg` |
| Topology | `{name}.itp`, `{name}.gro`, `topol.top` |
| Simulation setup | `run_dir/topol.top`, `run_dir/*.itp`, `run_dir/*.mdp`, `run_dir/model.pdb` |
| GROMACS | `em.*`, `eq.*`, `prod.*` |

产物命名变更属于破坏性变更，必须同步全仓。

---

## 六、修订流程

每次修订按以下顺序推进。

### Step 1：确定变更边界

先回答：

- 这次变更属于哪个边界？
- 会影响哪些下游消费者？
- 是否改变 step、config、artifact、tool schema 或 ErrorKind？

### Step 2：先改契约，再改实现

如果涉及跨层：

1. 更新或新增测试描述期望契约。
2. 更新共享类型或 schema。
3. 修改执行实现。
4. 修改 toolist adapter。
5. 修改 orchestrator。
6. 修改 Agent prompt。
7. 修改 docs。

### Step 3：运行最小验证

按影响范围选择：

| 影响范围 | 必跑 |
|------|------|
| config/toolist | `pytest tests/test_llm_config.py tests/test_toolist_global.py -q` |
| quantum | `pytest tests/test_log_parsers.py tests/test_toolist_quantum_topology.py -q` |
| topology | `pytest tests/test_toolist_quantum_topology.py -q` |
| simulation | `pytest tests/test_toolist_simulation.py -q` |
| orchestrator/state | `pytest tests/test_pipeline_orchestrator.py tests/test_pipeline_state.py -q` |
| Agent prompt/tool | `python3 -m tests.llm_eval.run_eval` |
| 全局 | `pytest -q` |

### Step 4：更新文档索引

新增文档必须同步：

- `docs/README.md` 快速导航
- `docs/README.md` 文档分类
- 相关设计文档

修改文件名必须同步：

- `docs/README.md`
- `docs/naming_convention.md`
- `docs/reconstruction.md`
- `README.md`

---

## 七、优先级规则

### P0：必须先做

- 修复运行时崩溃。
- 修复安全暴露。
- 修复测试基线。
- 修复文档和实际主流程明显矛盾。

### P1：随后做

- 稳定跨层契约。
- 补齐主流程闭环。
- 整理配置 schema。
- 标准化日志。

### P2：可以并行推进

- 前端体验改进。
- LLM eval 扩展。
- 文档补充。
- 仓库产物治理。

### P3：暂缓

- 新增更多分子库。
- 新增更多后端。
- 大规模 UI 重设计。
- 未证明必要的新抽象。

原则：当前阶段优先补闭环和边界，不优先扩功能。

---

## 八、变更验收定义

一个修订只有同时满足以下条件，才算完成：

- 代码实现完成。
- 单元测试或回归测试覆盖关键路径。
- 若改变外部行为，README 或 docs 已同步。
- 若改变 step/config/artifact/tool/error，相关边界文档已同步。
- `pytest -q` 或明确说明未跑原因。
- 没有引入新的未说明安全风险。
- 没有修改或回退无关用户变更。

---

## 九、近期建议执行顺序

### 第一批：稳定基线

1. 修 `tools_validate_config`。
2. 修 `env_checker` 命名和 `ensure()` 调用。
3. 修 `top_assembly` 的 `topo_dir` 传递。
4. 修脆弱测试，去掉固定行号断言。
5. 让 `pytest -q` 全绿。

### 第二批：补完整主流程

1. 建立 step registry。
2. 接入 `simulation.setup.collect_files()`。
3. 接入 `run_em()`。
4. 明确并接入 NVT/NPT。
5. 接入 `run_prod()`。
6. 更新状态机、前端进度和 README。

### 第三批：安全和运行治理

1. Gradio 默认本地绑定。
2. 移除 `os.system`。
3. 替换可替换的 `shell=True`。
4. 引入 run_id/run_dir 运行管理。
5. 清理 `.gitignore` 和运行产物策略。

### 第四批：Agent 和文档治理

1. 标准化 `TOOL_META`。
2. 扩展 LLM eval。
3. 更新 `quantum_design.md`、`topology_design.md`。
4. 新增 `simulation_design.md`。
5. 清理所有旧文件名引用。

---

## 十、与现有文档的关系

本文件是总计划和边界策略。

相关文档职责：

- `project_evaluation.md`：当前状态评估。
- `project_gap_analysis.md`：已知缺口和修复优先级。
- `naming_convention.md`：命名规范。
- `reconstruction.md`：重构时的具体检查清单。
- `quantum_design.md`：量子层设计细节。
- `topology_design.md`：拓扑层设计细节。
- `status_api.md`：状态文件和前端轮询接口。

未来新增 `simulation_design.md` 后，应与本文的 Simulation 边界保持一致。
