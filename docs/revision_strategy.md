# Willy 修订总计划与边界策略

> 版本：1.1
> 日期：2026-08-05
> 适用范围：`src/willy/`、`app.py`、`run_pipeline.py`、`docs/`、`tests/`、`config.json`、运行产物目录与 vendor 依赖。  
> 目标：给后续修订建立统一路线图，明确每个部分的职责边界，避免重构时跨层漂移、重复实现和文档失真。

---

## 一、修订总目标

未来修订围绕一个核心目标推进：

> 将 Willy 从“可运行的研究型 MVP”推进到“端到端闭环、边界清晰、可测试、可观测、可安全运行的内部自动化平台”。

当前最大的结构性问题不是单点 bug，而是三类边界不够稳定：

1. **产品承诺边界**：主编排器与四条已承诺 profile 均有历史 EM/NPT EQ/PROD 十步成功记录；当前版本绑定的外部验收仅复核 G16+Sobtop 1/4，剩余重放由 G-03/T-03 跟踪。文档只承诺已验证路线的最终无错误和产物契约，不延伸为科学体系预测。
2. **模块职责边界**：部分执行层、toolist、orchestrator、运行注册与环境注册之间的命名和契约仍需以唯一事实来源收敛。
3. **运行安全边界**：前端、参数化子进程、进程控制、API key、运行产物管理还没有形成完整发布约束。

修订策略必须先稳定边界，再扩展能力。

---

## 二、责任归属

本计划由 **0 号总工程师 Codex** 维护。0 号负责判断修订优先级、裁决跨层边界、定义验收门槛，并协调 1-7 号领域工程师的改动合流。

职责关系：

| 角色 | 责任 |
|------|------|
| 0 号总工程师 | 路线图、边界裁决、质量门禁、安全治理、文档一致性 |
| 1 号量子层工程师 | `quantum/` 执行层、量子产物契约、量子错误诊断 |
| 2 号拓扑层工程师 | `topology/` 执行层、力场接口、主拓扑和 itp 修订 |
| 3 号模拟层工程师 | `simulation/` 执行层、MDP/Packmol/GROMACS 主流程 |
| 4 号前端交互工程师 | `app.py`、`frontend_api.py`、进度展示、用户交互 |
| 5 号文档架构师 | 命名规范、设计文档、知识库、索引和文档台账 |
| 6 号测试工程师 | 测试策略、回归基线、外部 smoke 与发布质量门禁 |
| 7 号 LLM 网关工程师 | 独立托管网关、provider 边界、设备认证、额度审计与部署安全 |

当两个领域对同一边界有不同判断时，以 0 号总工程师在本文档和 `docs/employees.md` 中定义的边界为准；若边界需要调整，必须先更新文档，再改代码。当前架构已经引入 `env_registry.py`（环境事实来源）、`run_registry.py`（公开运行事实来源）和 `pipeline_launch.py`（启动锁与 run 绑定）；它们的接口分别以 `environment_registry_design.md`、`status_api.md` 和 `run_assistant_design.md` 为准。

---

## 三、总体路线图

### Phase 0：基线收敛

目标：让当前项目进入可维护状态。

范围：

- 修复已确认的阻塞 bug。
- 让测试基线全绿。
- 统一当前实际能力和文档描述。
- 收紧最基本的安全风险。

已完成（2026-07-31）：

- 修复 `tools_validate_config` 导入错误。
- 统一 `PipelineOrchestrator.ensure()` 和 `env_checker._DEPENDENCIES.needed_by` 的模块名。
- 修复 `top_assembly.build()` 中 ITP 修订回退到共享拓扑目录的问题。
- 将 Gradio 默认绑定收紧为本地地址，并移除 `frontend_api.py` 中的 `os.system` 调用。
- 明确阶段输入、必需产物和 run 内回滚边界；GROMACS 执行阶段进入 Phase 2 验收。
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

已完成（2026-07-31）：

- 给 `StepResult` 增加统一 `to_dict()`，去掉各 `toolist_*.py` 中重复的 `_step_to_dict()`。

已完成（2026-08-05）：

- 建立 `step_registry.py` 作为步骤编号、层级、产物契约、模拟阶段与受控重跑许可的唯一来源；编排器、恢复策略、待确认动作、前端停止判断、执行器和步骤审计均已接入。
- 在同一注册表中建立执行模块与依赖归属：`EXECUTION_MODULE_REGISTRY` 将稳定模块 ID 映射到主流程 step、外部工具和内置依赖；`env_checker` 的 `needed_by` 由其生成，不再在预检清单中重复维护。
- 建立 `config_schema.py` 作为完整工作流配置的外层结构校验边界；MD v2 科学协议与显式迁移继续由 `simulation.protocol` 负责，未知扩展字段保持兼容。
- 建立 `RunStore` run-local 事务锁和跨事件/决策/生命周期记录的单调序列；MD manifest 读改写不再暴露并发丢失更新。
- 新增 `provenance.json` 与受管 GROMACS 进程组停止升级，保留脱敏、可复现的运行事实而不暴露密钥、路径或原始 prompt。
- 建立具名 external smoke 注册、required 门禁和证据框架；真实工具 fixture 仍按目标环境验收。

仍需完成：

- 所有执行模块返回 `StepResult`，禁止裸 `raise` 穿出 pipeline 公共接口。

验收标准：

- 任意一步失败时，orchestrator、状态机、Agent、前端展示看到的是同一个 `step_name` 和 `ErrorKind`。
- 已验证 `toolist_*.py` 中不再出现重复的 `_step_to_dict`。
- 新增配置字段必须同时出现在 schema、defaults、文档和测试中。

### Phase 2：端到端 MD 闭环（本版本已完成）

目标：让主流程真正完成从结构输入到 GROMACS 生产运行。本版本不纳入科学体系预测、后处理/分析或远程执行。

范围：

- 接入 EM。
- 接入 EQ 和 PROD。
- 调整状态机与前端进度。

已完成：

- `PipelineOrchestrator._build_steps()` 已接入 `simulation.em.run_em()`、`simulation.eq.run_eq()` 和 `simulation.prod.run_prod()`。
- 当前 run workspace 已在步骤 1-7 原位生成并消费 `topol.top`、`.itp`、`.mdp` 和 `model.pdb`，不再额外复制到旧式 setup 目录。
- 三个阶段在执行前校验输入，在成功时强制登记 `.tpr/.gro/.xtc/.edr`；可选 `.trr` 由模拟前配置控制。
- EM 收敛失败可有限次回滚至 Packmol 建盒；EQ 通过前不得进入 PROD。EQ 的真空区与密度证据保留给诊断，但不自动触发回滚。

建议最终主流程：

```text
1. quantum_struct
2. quantum_sp_mol2
3. quantum_resp
4. topology_molecule
5. topology_assembly
6. simulation_mdp
7. simulation_box
8. simulation_em
9. simulation_eq_npt
10. simulation_prod
```

验收标准：

- 已在 32 原子中性 LJ 小体系完成 Packmol -> EM -> EQ -> PROD 真实 smoke。
- 目标体系必须另行完成收敛阈值、GROMACS 警告白名单和科学验收，不能以小体系 smoke 替代。
- 所有 simulation retry tools 对应的主流程步骤真实存在。
- 前端进度不再显示和实际步骤不一致的标签。

### Phase 3：Agent 修复能力增强（核心契约已完成，真实端点验收待执行）

目标：让 Agent 的修复行为可控、可测、可升级。

范围：

- 工具权限治理。
- 重试次数治理。
- 升级信息标准化。
- LLM API 稳定性。

已完成：

- `action_contract.py` 从五个 toolist 的 JSON Schema 与 `TOOL_META` 构建唯一的 50 项工具目录，声明 `read_only`、`retry_safe`、`requires_confirmation`、`requires_fork`、`destructive` 等效果等级；Config Agent 的 `tools_inspect_quantum_inputs` 与模拟层的 `tools_lookup_mdrun_knowledge` 均为只读、低风险工具。
- `recovery_policy.py` 按 layer、错误类型、步骤和工具效果裁决重试上限、确认要求与 fork 限制；模型不能通过参数提升权限。
- `LayerAgent` 以服务端状态机固定 `DIAGNOSE -> RECOVER`：诊断阶段只公开一个只读工具，恢复阶段只公开一个本层、当前错误和步骤允许的 `retry_safe` 工具，并以 `tool_choice="required"` 强制该唯一动作。服务端仍将其校验为该唯一工具名，并校验参数、层级和策略；文本、空调用、多工具、跨层工具和非法参数均终态升级。
- 确认、派生 run、输入契约、运行依赖和锁冲突在模型调用前由策略终态化；EQ/PROD 的协议变更维持 `awaiting_confirmation`，不消耗修复次数。LLM 传输/协议失败写入受限决策且不调用 `start_retry`。`llm_budget.py` 继续限制单 run 调用次数、累计时长、单次超时和连续失败熔断。
- Config Agent 已分为语义提取、服务端规范化、只暴露量子审计工具并以具名 `tool_choice` 固定执行的审计、无工具严格 JSON 与服务端校验五段；审计缺失不再以追加文本提醒后继续。
- 评分器只接受真实观察到的工具名、参数 schema、顺序、层级和策略白名单，取消以 LLM 调用次数或重试次数代理工具得分。静态矩阵覆盖全部 `ErrorKind`、13 类配置和 20 类协议条件；18 个离线 mock 场景覆盖跨层工具隔离、恢复身份、升级和确认边界。

仍需完成：

- 使用当前 BYOK 配置先运行脱敏文本/自动工具协议探针，再运行 fake executor 的真实模型评测；2026-08-12 协议探针 3/3、配置 Agent 5/5 通过，恢复矩阵 16/18：`q_scf_001` 因模型在恢复阶段未调用允许的修复工具而由服务端安全升级，`s_grompp_017` 的 4 次调用中有 1 次为策略禁止调用，只有 3 次同时通过参数 schema 和策略白名单。该验证属于产品增强，必须显式 opt-in，不能由默认回归代替；G-09 在稳定性修复或端点兼容性明确前保持开放。
- 随实际修复策略扩展，持续将高影响动作收敛到可审计的确认或派生 run 流程。

验收标准：

- Mock eval 继续全通过。
- 升级信息必须包含 `layer`、`step_name`、`error_kind`、`attempts_made`、`actions_tried`、`recommendation`、`backup_plan`。
- 高风险工具不允许绕过 tool handler 直接执行 shell。

### Phase 4：前端与运行管理产品化（核心控制面已完成）

目标：让 Web UI 成为可安全使用的控制面板，而不是直接暴露脚本入口。

范围：

- 认证。
- 进程管理。
- 运行目录管理。
- 产物浏览。
- 离线资源。

已完成：

- Web UI 固定监听 `127.0.0.1` 且 `share=False`；当前没有远程公开控制面，任何未来远程暴露都必须先设计认证和授权。
- 启动锁、run 预留、run-local 配置快照、状态、公开 manifest、provenance、事务写前日志与受管进程组生命周期均已落地。
- 前端通过受限 API 读取 run 和可视化文件；停止只走服务端二次确认按钮，EQ 调整只走冻结动作与文本确认。
- 3Dmol 与结构上传类型白名单已在本地 UI 中接入。

仍需完成：

- 真实目标环境中的停止、续跑与 ETA 长时间行为验收。
- 若产品开放远程访问，补充认证、并发用户隔离、上传大小限制与部署边界测试。

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

已完成：

- `.gitignore` 忽略运行产物、启动锁、临时状态与项目本地迁移审计；run 编号从已分配目录推导，不再依赖根目录计数文件。
- `scripts/prune_runs.py` 默认仅预览，显式 `--apply` 后按保留数量删除历史 run、同步索引，并在启动锁活跃时拒绝执行。
- `.github/workflows/tests.yml` 已执行 Python 3.10/3.12 的确定性回归、LLM mock eval、编译检查和生成的测试用例台账一致性检查；external smoke 只在 self-hosted 验收机显式启用。

仍需完成：

- 已建立 `vendor/manifest.json` 与只读 SHA-256 校验；继续将示例数据与真实运行产物分离，并为尚未 `release_ready` 的 vendor 二进制补齐来源、版本、license 和校验方式。
- 增加独立的文档链接检查与目标环境 external smoke 成功证据；CI 配置存在不等于真实外部链路已验收。

验收标准：

- 新 clone 后按 README 能安装并运行基础检查。
- CI 能阻止确定性测试红线、编译失败和生成台账漂移；外部依赖与文档链接仍需补专门门禁。
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
- `src/willy/workflow_config.py`
- `src/willy/llm_config.py`
- `docs/knowledge.md`

职责：

- 自然语言到配置草案。
- 分子别名和化合物解析。
- `config.json` 验证和默认值合并。
- 配置阶段的轻量诊断。

允许：

- 读取 `docs/knowledge.md`。
- 扫描 `struct/*.gjf` 与 `struct/*.inp` 作为可用分子列表；方案生成时必须按所选后端以受限解析器审计原始输入，而不是以 registry 默认电荷代替文件头。
- 写入 `config.json`，但必须通过 `workflow_config.apply_config()` 或统一配置 API。
- 在 `awaiting_confirmation` 期间接收方案问题和增量修改：将经过指纹校验的冻结配置、摘要及最近对话绑定到 LLM 请求；问题只读回答，修改只生成新的待确认方案，且服务端重新执行量子输入审计。只有明确声明新项目/新体系/重新提交时才使旧方案失效。

禁止：

- 直接运行量子、拓扑、模拟外部程序。
- 直接修改运行目录产物。
- 在 prompt 中承诺未接入的 pipeline 能力。

修订原则：

- 配置 Agent 只产出结构化配置，不负责执行。
- `toolist_global.py` 是 LLM tool schema 和 handler，不放复杂科学计算逻辑。
- `config_schema.py` 是工作流配置外层结构的唯一来源；`workflow_config.py` 负责默认值、
  跨字段语义与写入入口；`simulation.protocol` 是 MD v2 科学协议和显式迁移的唯一来源。
- `llm_config.py` 是 OpenAI-compatible endpoint、model、credential 解析和 client 构造的唯一来源。

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
- 在传入 `topo_dir` 时回退到项目根目录的共享拓扑目录。

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
- 运行目录仅由 `pipeline_launch.py` 分配；量子、拓扑和模拟产物始终在同一 run workspace 内交接，不再维护额外文件收集器。
- `em.py`、`eq.py`、`prod.py` 只运行对应阶段。

### 9. Shared Infra 边界

文件：

- `src/willy/errors.py`
- `src/willy/env_registry.py`
- `src/willy/env_checker.py`
- `src/willy/log_parsers.py`
- `src/willy/_paths.py`
- `src/willy/run_registry.py`
- `src/willy/pipeline_launch.py`

职责：

- 共享错误模型。
- 外部工具发现、子进程环境构造和兼容预检。
- 日志解析。
- 项目根路径解析。
- 公开运行事实、启动锁与 run 绑定。

禁止：

- import 具体执行层形成反向依赖。
- 写业务产物。
- 调用 LLM。

修订原则：

- shared infra 必须低耦合。
- 新 `ErrorKind` 必须有测试和文档说明。
- 新外部依赖必须集中注册在 `env_registry.py`；`env_checker.py` 只保留兼容的模块级预检入口。
- 运行身份、公开状态和历史索引只能由 `RunRegistry` 管理；阶段许可仍由模拟层的 `md_manifest.json` 管理。

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

- 改代码必须同步相关设计文档和 `docs/document_registry.md`。
- 改文件名必须同步 `docs/README.md`、`docs/document_registry.md` 和全部受影响引用。
- 新增 bug 复现测试后，下一步必须把它改成修复后的回归测试。

### 11. Data、Artifacts 和 Vendor 边界

目录：

- `struct/`
- `md_run/`
- `vendor/`

职责：

- `struct/`：输入结构和必要示例结构。
- `md_run/`：每个 run 的拓扑、模拟、状态和审计产物。
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
| config/toolist | `pytest tests/test_workflow_config.py tests/test_llm_config.py tests/test_toolist_global.py -q` |
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
- `docs/document_registry.md` 文档台账
- 相关设计文档

修改文件名必须同步：

- `docs/README.md`
- `docs/document_registry.md`
- `docs/naming_convention.md`
- `docs/revision_strategy.md` 的“重构同步检查清单”
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

1. 已完成：修 `tools_validate_config`。
2. 已完成：修 `env_checker` 命名和 `ensure()` 调用。
3. 已完成：修 `top_assembly` 的 `topo_dir` 传递。
4. 已完成：修脆弱测试，改为验证可观察行为。
5. 已完成：让 `pytest -q` 全绿。

### 第二批：补完整主流程

1. 建立 step registry。
2. 以真实小体系验收 EM/NPT/PROD 和 PATH 中的 GROMACS 版本。
3. 固化外部 smoke 与 EQ/PROD 真实验收证据。
4. 验证 EQ 温度/势能验收、非阻塞真空区观测与 PROD 参数确认。

### 第三批：安全和运行治理

1. Gradio 默认本地绑定。
2. 移除 `os.system`。
3. 持续审计参数化子进程调用和进程边界。
4. 引入 run_id/run_dir 运行管理。
5. 清理 `.gitignore` 和运行产物策略。

### 第四批：Agent 和文档治理

1. 标准化 `TOOL_META`。
2. 扩展 LLM eval。
3. 维护量子、拓扑、模拟、后处理、环境注册与状态接口设计文档。
4. 维护运行助理、测试策略和文档台账的验收证据。
5. 清理所有旧文件名和已完成迁移的未来式描述。

---

## 十、与现有文档的关系

本文件是总计划和边界策略。

相关文档职责：

- `project_gap_analysis.md`：已知缺口和修复优先级。
- `naming_convention.md`：命名规范。
- `quantum_design.md`：量子层设计细节。
- `topology_design.md`：拓扑层设计细节。
- `simulation_design.md`：模拟协议、阶段许可、恢复契约和 PROD 后处理契约。
- `environment_registry_design.md`：外部软件发现、预检与子进程环境。
- `status_api.md`：状态文件和前端轮询接口。
- `run_assistant_design.md`：运行审计、只读查询和后续控制计划。

各领域设计文档是可执行契约的细节来源；本文只维护跨层边界、优先级和完成定义。

## 十一、重构同步检查清单

重构 `quantum/`、`topology/`、`simulation/` 或运行管理模块时，必须沿以下顺序核对：

1. maker、handler、orchestrator 的函数签名、步骤身份和产物命名是否一致。
2. `STEP_REGISTRY`、`EXECUTION_MODULE_REGISTRY`、`ErrorKind`、`ActionToolCatalog` 和恢复策略是否同步。
3. 配置 schema、默认值、迁移、run-local manifest/provenance、状态事件和公开 API 是否同步。
4. 外部依赖是否只经 `env_registry` 注册，子进程生命周期、超时、信号升级和脱敏审计是否保持。
5. Agent prompt、tool schema、测试、`docs/README.md`、本台账及全仓旧名引用是否已更新。
6. 完成最小回归、台账生成和 `git diff --check`；新增 bug 必须转为可重复的回归测试。
