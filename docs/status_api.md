# 流水线状态接口文档

每次启动会先原子预占运行锁并立即分配 `run_id`。前端仅在运行助理对话框内的工程状态信息气泡展示状态；该摘要与运行助理对话通过 `RunRegistry` 读取 `md_run/<run_id>/status.json`、`run_manifest.json` 的受限 section、`events.jsonl` 及 `mdrun_eta.json`。项目根目录 `status.json` 不是运行事实来源。前端与 LLM 必须通过 `RunRegistry` 查询运行，不能扫描任意目录。步骤编号、层级、公开标签、产物契约和模拟阶段由 `step_registry.py` 的 `STEP_REGISTRY` 唯一定义；本文件的步骤表仅为当前注册表的文档快照。

新 run 使用单一的 `run_manifest.json`（schema v2）：公开 `registry` section 管理 run 身份、冻结输入和产物索引；私有 `provenance`、`topology`、`simulation`、`protocol` sections 分别保存溯源、组件计划、MD 阶段许可和 MDP 元数据。物理合并不扩大公开面，`RunRegistry` 仍只投影白名单字段。历史 `manifest.json`、`provenance.json`、`topology_manifest.json` 和 `md_manifest.json` 仅作兼容读取；已迁移终态 run 保留它们以便回退。`RunRegistry` 可在内部读取私有 simulation section，以发现公开状态已经越过一个仍在运行的阶段；这不会向前端或 LLM 返回私有内容。

公开状态表达七类信息：**工具、操作、对象、进度、摘要错误、自动修复摘要、终态升级摘要**。自动修复摘要仅可包含已经发生的重试次数和已落盘配置的白名单差异（旧值到新值）；终态升级摘要仅可含尝试次数、受限操作记录、建议和备用方案。命令行、stderr、原始日志、绝对路径、堆栈、Agent 思维过程和任意工具原始参数不得写入状态或经前端、运行助理暴露。若 Simulation Agent 提议修改协议，使用固定的 `user_confirmation_required` 错误类型升级；不公开模型参数、拟议值或授权信息。

## 读取方式

```python
from willy.run_registry import RunRegistry
status = RunRegistry().get_run_status(run_id)
```

`PipelineStateMachine` 仅在运行目录注册成功后写入 `md_run/<run_id>/status.json`；该文件和运行索引均采用原子写入。未绑定 `run_id` 的启动冲突或启动失败只写入根目录 `startup_audit.json`，其公开消息仅为“已有任务运行”或“启动失败”。

## 停止与失活对账

停止意图只支持按钮的确定性 UI 路径：首次点击“中止流水线”只把按钮切换为“确认中止”，第二次点击才由前端服务端直接调用 `stop_pipeline`。首次操作不得写运行状态、`stop.request` 或发送进程信号；停止不得依赖浏览器 `window.confirm`，也不得经 LLM 或 Run Assistant tool。运行助理文本框中的“中止”“暂停”“稍后”“确认中止”及其等效讨论语只可产生对话提示或保留等待状态，绝不能调用停止、重跑或退出工程。仅对已经由该按钮或进程信号中止的当前工程，完整的显式 `/resume` 与 `/fork` 消息由前端在调用只读运行助理前确定性处理；它们不是 LLM tool，也不会由自然语言近义表达触发。

## 用户中止后的受控续跑

当前选中的 run 处于 `aborted`（或此前被拒绝 fork 留下的、没有 `pending_action` 的 `awaiting_confirmation`）时，才允许下列完整命令：

```text
/resume
/fork md.eq.tau_p=3
/fork {"md":{"eq":{"tau_p":3}}}
```

`/resume` 不接受参数。服务端重新读取该 run 内冻结的 `config.json`，以 `done_steps` 的首个缺失步骤作为安全续跑点，在**原** `md_run/<run_id>/` 预占启动锁后按 `aborted -> awaiting_confirmation -> retrying -> running` 流转；启动失败则回到无 `pending_action` 的 `awaiting_confirmation`。它不会从项目根目录、任意路径或另一个 run 推断输入。

`/fork` 必须给出实际发生变化的现有配置路径。支持的路径仅为 `topology`（Step 4）、`box`（Step 7）、`md`（默认 Step 6，`md.eq` 为 Step 9、`md.prod` 为 Step 10）和 `execution`（Step 6）；`md.run_seed` 及其他根字段禁止修改。每项路径的首次消费步骤必须不早于上次停止步骤，否则拒绝并通过 CAS 回到无 `pending_action` 的 `awaiting_confirmation`。有效 fork 创建新的 run、冻结修改后的 `config.json`、登记 `parent_run_id`，并重放 Step 4 或 Step 6 的安全边界；父 run 保持不变。参数值只保留在子 run 配置快照中，控制审计只记录路径。

当阶段失败并等待协议调整授权时，公开 `status.json` 可带受限的 `pending_action`。单方案只含 `action_id`、`state`、失败步骤的公开标签、`restart_step` 和至多 8 项已脱敏的核心修改项（名称、旧值、新值与简短目的）；还可带至多 8 项可复审参数（名称、当前值、允许范围和简短目的），供用户提出替代要求。若诊断有多个可信原因，`options` 最多含 3 个独立选项，每项只公开 `option_id`、序号、标题、可能原因、证据摘要、重跑起点和同样脱敏的修改项。每项可额外带来源字段：`knowledge_status`（`retrieved`、`not_matched` 或 `unavailable`）、实际读取且数字/名称验证过的 `knowledge_entries`（仅 `number`、`name`）、`advice_source`（`knowledge_base` 或 `llm_unverified`）及固定的 `compatibility_notice`。未命中或不可用时，前端必须明确“LLM 未经知识库验证的推断”；不得把模型假设显示为文档事实。`selected_option_id` 仅在用户选定后出现，`selection_required=true` 时普通“确认/同意”必须被拒绝，不能默认选项。`restart_step` 只能为 9（仅重跑 EQ）或 7（修改建盒后依次重跑 Box、EM、EQ、PROD）。不得含日志、路径、命令、模型推理、原始错误或内部配置。前端将新 `action_id` 渲染为运行助理对话框末尾的独立、不可变调整事件气泡；轮询同一 `action_id` 时不重复追加。带有 `pending_action` 的 `awaiting_confirmation` 表示“LLM 已返回方案，尚未得到用户对当前 `action_id` 的明确确认”；它不能被渲染或解释成状态未知、自动重试或已在运行。无 `pending_action` 的同一状态仅用于被拒绝或启动失败的显式续跑控制，前端显示等待下一条受控命令，绝不把它交给 EQ 方案确认入口。

用户只有以精确白名单批准语（“同意”“确认重跑”“按方案执行”）回应当前待确认方案时，才可经 `frontend_api.confirm_pending_action(action_id, run_id)` 请求受控重跑；该入口必须校验 run、action 和配置指纹。多方案时，“方案1/方案一/选择方案1”仅调用 `select_pending_action_option` 固化选择并保持 `awaiting_confirmation`；“确认方案1/确认方案一”会先以同一 run 的 CAS 选择该项，再调用确认入口。未选方案的普通确认被拒绝。确认后编排器必须先持久化 `retrying`，重建受影响 MDP，再转为 `running` 并调用首个需重跑的科学阶段。未回复、暂停、稍后、否决或不匹配文本均保持 `awaiting_confirmation`，不改写配置、不发起重跑，也不终止工程。

用户明确提出替代调整时，`frontend_api.revise_pending_action(action_id, request, run_id)` 可调用 Simulation Agent 生成一个完整的新方案。该入口只接受当前同一 run 的 pending 动作；替换方案仍经字段白名单、数值范围和冻结配置指纹校验，写入新的 `action_id` 与 `pending_action_revised` 事件。原动作随即失效，`config.json`、MDP、阶段许可和进程均不变，状态继续保持 `awaiting_confirmation`，直到用户再次确认新动作。EQ 动作校验可把 `tau_p`/`taup`/`md.eq.tau_p` 归一为 `eq_tau_p`，把 `hold_target`/`hold_time` 归一为 `eq_segment.hold_target`，并接受带受限单位的数值字符串；它不接受任意路径或 PROD 字段。无法生成或校验替代方案时，保留原动作和等待状态，并返回脱敏的明确类别，例如字段无效、数值无效或越界、无可执行调整项、冻结配置变化或状态版本冲突；同一原因以 `rejected_validation` 记录在私有 `decision_trace.jsonl`，不写入公共状态事件。

建盒的真实几何不写入 `status.json` 或 `events.jsonl`。Step 7 成功时模拟层将请求/实际盒矢量、体积和初始质量密度写入私有 `run_manifest.json.sections.simulation.data.box_attempts[]`；每次前置校验或 Packmol 调用的失败阶段、返回码、产物指纹、原子数和周期盒校验另写入私有 `box_executions[]`。运行助理只能通过 `tools_get_box_parameters_run` 读取最近一条固定白名单几何字段，供“盒子/边长/初始密度”问题解释；不暴露执行证据中的路径、日志或命令。

服务端确认后，前端先经 `RunRegistry` 写入 `run_stop_requested` 事件和 `state=stopping`，再请求目标进程退出。GROMACS 阶段通过 run 内 `stop.request` 请求安全点 `SIGINT`，以保留 checkpoint；其他阶段由受管进程组退出。按钮只有收到这一持久化请求的成功回执后才能锁定为“正在安全停止”；未获得回执时必须保持可交互并显示“中止请求未送达，请重试”。`stopping` 不是引擎失败，编排器收到该请求后必须直接写 `run_aborted`，不得调用 LayerAgent 重试。

CLI 收到 `SIGINT` 或 `SIGTERM` 时也必须写 `aborted` 后释放启动锁。前端轮询只读 `status.json`，不得因暂时看不到根目录 runner 或启动锁而改写运行状态。失活对账是独立的维护动作：只对最新的 `running`/`retrying`/`stopping` run，在重新读取同一 `state_revision` 后执行 run 级存活检查；当前 `mdrun_eta.json` 的心跳、受管进程组、`eq`/`em`/`prod` 阶段近期产物或新鲜状态心跳任一成立，都必须跳过中止。不得使用全局 `pgrep` 作为任意 run 的存活证据。确认无新鲜证据后仍需用同一 revision CAS 写入 `run_aborted_after_process_exit`，并在事件中记录脱敏的检查来源、阶段、新鲜度和 revision。对账成功后才清除遗留 `stop.request`；用户明确的 `stopping` 优先级不被心跳覆盖。这避免页面刷新误杀仍在运行的 GROMACS，也不扫描或改写其他历史 run。

当一个 transient `status.json` 已把步骤或 `done_steps` 写到仍为 `running` 的 EM/EQ/PROD 之后，显式的状态修复任务才可调用 `RunRegistry.get_run_status(..., reconcile=True)`；它只有同时观察到活跃 ETA 心跳或近期阶段产物写入时，才执行一次受限状态对账：撤销该活动阶段及下游步骤的公开完成标记，显示实际阶段、清除失配的公开错误，并追加 `stage_status_reconciled`。前端和运行助理的普通查询使用 `reconcile=False`，不会写状态。对账不会更改私有阶段许可、输入、产物、锁或受管进程；`stopping` 和 `aborted` 不参与此阶段修复，以保留用户停止的优先级。

## 轮询频率

推荐 `gr.Timer(3)`（3 秒），流水线内部更新频率远低于 3 秒。每次刷新先一次性解析当前 `run_id`，再读取该 run 的状态摘要、公开错误事件与待确认动作，组成同一公共快照。快照额外公开 `status_event_id`，它只由 run、公开状态、步骤、待确认 `action_id` 和公开错误事件身份构成，不随心跳或 ETA 文本变化。前端保留欢迎气泡为第一条，并关闭连续助理消息合并；同一 `status_event_id` 的状态卡原位刷新，身份变化时旧卡封存为历史并新建当前状态卡。失败以 `run_id + state_revision` 作为事件身份，新的待确认调整以 `run_id + action_id` 作为事件身份，均作为彼此独立的历史气泡追加；重复轮询同一身份不得重复追加。状态、错误和方案在当前浏览器会话内保留，切换 run 时一并清空；它们不追加到 LLM 的普通对话上下文。可用 ETA 在状态气泡中仅显示“当前步骤预计结束：<本地时间>”，不重复显示预测观测时刻。

## PipelineStatus 字段

```json
{
  "state":         "running",        // 见下方状态表
  "step":          3,                // 当前步骤 index (1-10)，idle 时为 0
  "step_label":    "RESP 电荷",      // 当前步骤描述（兼容字段，前端不依赖它渲染）
  "layer":         "quantum",        // 当前层: "config"|"quantum"|"topology"|"simulation"
  "error":         "G16 结构优化失败：NO3\n原因：SCF 未收敛", // 摘要错误（空串=无错误）
  "error_kind":    "scf_not_converged", // 前端仅以白名单类型渲染固定状态；`user_confirmation_required` 显示待用户确认
  "repair": {
    "attempt": 2,                 // 当前自动修复尝试；0 表示仍在诊断
    "max_attempts": 3,
    "adjustments": [
      {"name": "恒温耦合时间", "before": "0.5 ps", "after": "2 ps"}
    ]
  },
  "escalation": {
    "layer": "simulation",
    "step": "Packmol 盒子",
    "error_kind": "runtime_unavailable",
    "attempts_made": 0,
    "actions_tried": ["检测到运行环境不可用，未执行自动参数修复"],
    "recommendation": "安装或重建与当前系统兼容的 Packmol 后重新提交。",
    "backup_plan": "请确认目标机的软件运行环境。"
  },
  "activity": {
    "tool":        "G16",
    "operation":   "结构优化",
    "target_type": "molecule",
    "target":      "NO3",
    "current":     1,
    "total":       4
  },
  "started_at":    "2026-07-28T12:00:00Z",  // 启动时间
  "updated_at":    "2026-07-28T12:05:30Z",  // 最后更新时间
  "total_steps":   10,               // 总步骤数
  "done_steps":    [1, 2],           // 已完成的步骤 index
  "extra":         {"run_id": "md_202607310001"}
}
```

`activity` 是唯一的公开工序模型，由 `set_activity(tool, operation, target_type, target, current, total)` 原子写入。字段严格固定，`target_type` 仅允许 `molecule`、`stage`、`system`。`repair` 仅在已经进入实际自动修复或存在已应用配置差异时出现，诊断阶段不得伪报“第 1/3 次”；其差异由配置写入成功后的白名单字段生成，最多保留 8 项。`escalation` 仅在 `escalated` 状态出现，所有文本和列表都经过白名单清洗。run 级状态额外含有顶层 `run_id`，不写入绝对运行目录。

等待用户授权时，`extra` 增加受限的 `pending_action`，而私有动作记录仅保存在同一 run 的 `pending_action.json`。公开字段示例：

```json
{
  "pending_action": {
    "action_id": "act-...",
    "state": "pending",
    "step_label": "GROMACS 三点式退火平衡",
    "restart_step": 9,
    "summary": "降低时间步长并延长末段保温后，重新验收 EQ。",
    "adjustments": [
      {"name": "时间步长", "before": "0.001 ps", "after": "0.0005 ps", "purpose": "降低高温段数值不稳定风险"}
    ]
  }
}
```

`pending_action` 不包含可执行路径、完整配置、原始错误或模型原文。前端轮询、替代方案和确认入口只能读取同一当前 `run_id` 的待确认动作，禁止扫描历史 run 查找任意 `awaiting_confirmation` 项；活动流水线优先使用启动锁登记的 `run_id`，无活动流水线时才按索引的更新时间和 `run_id` 确定当前工程。新 run 一旦成为当前工程，旧 run 的动作必须隐藏且不可被文本确认。服务端确认时以私有记录中的 run ID、动作 ID、`config.json` 指纹和允许字段重新校验，再写入配置快照和启动恢复进程。

确认和替代方案输入由前端确定性识别，不交给只读运行助理解释；它们的控制前置条件是当前工程带有 `pending_action` 的 `awaiting_confirmation`。`确认`、`同意`、`同意该方案并重跑` 等无歧义批准表达才会调用受控确认接口；修改词和受限参数词才会请求 LLM 生成替代方案。涉及拒绝、取消、暂停或等待的表达绝不触发重跑。除完整 `/resume`、`/fork` 外，其他文本在任意状态下都进入只读运行助理，包括非等待状态中的“确认”或“修改”文本；它们不能触发配置或进程操作。接口取得启动锁并再次校验后，先写入 `confirmation_retry_started` 与 `state=retrying`，再启动恢复进程，因此刷新不会显示旧的等待快照。恢复进程只接受同一动作的 `awaiting_confirmation` 或已受理 `retrying` 快照；若进程无法创建，服务端写入 `confirmation_launch_failed` 并恢复原等待快照和待确认方案。

新 run 的 `done_steps` 必须从空列表开始。只有显式指定原 run 与已验证断点的受控操作才能预置完成步骤。批量量子步骤只有配置中的每个分子都满足本步骤产物契约后才写入 `step_succeeded`；因此 Step 3 不得将部分 `*_opt.fchk` 的 RESP 结果记作整体成功。

对于 Step 8--10，`mark_done()` 还必须通过私有阶段完成门禁：EM 为 `accepted` 加 `em.tpr/.gro/.xtc/.edr`，EQ 为 `accepted` 加 `eq.tpr/.gro/.xtc/.edr/.cpt`，PROD 为 `completed` 加 `prod.tpr/.gro/.xtc/.edr/.cpt`。Agent 工具结果只可触发重跑或回滚指令，不能绕过该门禁。

## 运行审计接口

每个被 `RunRegistry` 登记的运行目录包含：

| 文件 | 用途 | 写入方式 |
|---|---|---|
| `run_manifest.json` | schema v2：公开 registry 及私有 provenance/topology/simulation/protocol section；registry 的有界 `control_history` 记录 resume/fork 的动作、结果、步骤、参数路径和父 run，不记录参数值；各 section 具备独立 revision/CAS | RunStore 原子替换 |
| `manifest.json`、`provenance.json`、`topology_manifest.json`、`md_manifest.json` | 历史 run 的兼容记录；迁移后保留为回退证据，新 run 不创建 | 仅旧 run 写入 |
| `.run-transaction.json` | 未完成 JSON/JSONL 写入包的私有、可重放意图；成功后删除 | 原子替换、恢复后删除 |
| `status.json` | 此 run 最新的 `PipelineStatus` 快照 | 原子替换 |
| `events.jsonl` | run 创建、状态变化、步骤结果的公开摘要 | 运行级单调序列单行追加、`fsync` 并加锁 |
| `logs/structured.jsonl` | 运行期间的脱敏结构化状态、步骤、决策、进程及低频心跳事件 | RunStore 单调序列单行追加、`fsync` 并加锁；写入失败不阻断计算 |
| `mdrun_eta.json` | GROMACS `mdrun -v` 的 ETA 预测，以及独立的进程/阶段产物心跳 | 原子替换 |
| `md_run/index.json` | 历史 run 的安全摘要 | 原子替换并加锁 |

`events.jsonl` 的公共字段为 `sequence`、`timestamp`、`event_type`、`run_id`、`details`；由可恢复写入包产生的记录另带不含业务信息的 `transaction_id`。`sequence` 与同一 run 的决策审计、进程生命周期记录共享单调递增序列。步骤结果的 `details` 仅包含 `step_id`、注册表产物契约 `artifact_contract`、`activity`、`success`、`error_kind` 和摘要 `error`；状态事件可额外包含与 `status.json` 相同的受限 `repair`、`pending_action` 或 `escalation` 摘要。受控续跑额外使用 `resume_accepted`、`resume_launched`、`resume_launch_failed`、`fork_accepted`、`fork_created`、`fork_launched`、`fork_launch_failed` 和 `fork_rejected`；details 只含动作、结果、停止/续跑步骤、参数路径和可校验的父 run ID。停止事件仅说明已请求停止或已中止，不携带底层信号、命令或日志。不会写入原始错误消息、`hint`、命令行、绝对路径、产物路径、日志内容或 Agent 原始工具参数。EM 收敛失败触发重建时，状态机只记录公开状态变化；EQ 真空区仅保留为私有诊断证据。

结构化内部流的 `event_code` 采用固定事件族：`run_started`、`state_changed`、`step_started`、`step_skipped`、`step_result`、`decision_recorded`、`process_started`、`process_heartbeat` 和 `process_finished`。它只保留逻辑标签和受控标识，不是原始程序日志的替代品。

`mdrun_eta.json` 不属于 `PipelineStatus`，不改变状态机字段。运行助理通过 `tools_get_md_eta_run` 读取经过校验的字段：`status`、`stage`、`process_alive`、`observed_at`、`last_progress_at`、`last_progress_step`，以及仅在 `available` 时存在的 `step`、`remaining_seconds`、`eta_observed_at`、`estimated_end_at`。`observed_at` 是当前心跳，`eta_observed_at` 才是 GROMACS 给出 ETA 的时刻；`last_progress_*` 只能证明日志或阶段产物仍在写入。GROMACS 2025 的 `will finish <ctime>` 按产生该输出的主机本地时区转换；持久化时间统一为 UTC，运行工具同时返回带 `_local` 后缀的主机本地展示值；前端和 LLM 必须优先显示该字段，不得把 UTC 原值当作本地时间。只有 `status=available` 才表示 GROMACS 已产生预测；`waiting` 表示阶段正在运行但尚无预测，`finished` 表示该阶段已结束且没有实时 ETA，`unavailable` 表示无法估算。查询仅读取既有快照，不触发心跳，也不能把 ETA 视为承诺或根据总时长、步骤、文件更新时间补算 ETA。

对批量步骤逐项重试时，`retrying` 和最终 `escalated` 的 `activity` 与摘要错误必须对应当前失败对象，不能保留该批次的首个失败对象。

## state 枚举

| state | 含义 | 前端展示 |
|-------|------|----------|
| `idle` | 无运行中的流水线 | 运行助理工程状态显示暂无运行 |
| `running` | 正常执行中 | 运行助理工程状态显示 `activity` 与运行圆环 |
| `retrying` | 正在执行实际内部修复 | 仅在已有真实修复尝试后显示 `activity`、实际第几次修复和已应用参数差异；诊断或依赖失败不得进入该状态 |
| `awaiting_confirmation` | 带 `pending_action` 时为 LLM 已生成当前 EQ 协议调整方案；无该字段时为已拒绝或启动失败的显式续跑控制 | 前者展示固定脱敏方案摘要并等待明确批准语；后者仅提示等待下一条 `/resume` 或 `/fork`。两种情况都不自动启动进程。 |
| `stopping` | 已确认停止，正在等待安全点或进程退出 | 显示“正在安全停止”；中止按钮禁用 |
| `escalated` | 自动处理未完成或不允许自动处理 | 显示摘要错误、已尝试次数、受限处理记录和建议；`error_kind=user_confirmation_required` 时明确当前 run 未改写，需确认新方案后重新启动；`runtime_unavailable` 时明确未执行参数重试 |
| `done` | 已完成声明的运行范围 | 默认全流程完成并显示全部完成；仅当 `extra.completion_scope.mode=through_eq` 时显示“已完成至 EQ”，不得暗示 PROD 已运行 |
| `aborted` | 已中止 | 显示已中止；仅完整 `/resume` 或 `/fork` 可经 CAS 进入受控等待路径 |

## 步骤 index 映射

| index | step_label | layer |
|:---:|------|------|
| 1 | 结构优化 | quantum |
| 2 | 单点计算与 mol2 转换 | quantum |
| 3 | RESP 电荷 | quantum |
| 4 | 分子拓扑参数化 | topology |
| 5 | 主拓扑生成 | topology |
| 6 | 生成 mdp | simulation |
| 7 | Packmol 盒子 | simulation |
| 8 | GROMACS 能量最小化 | simulation |
| 9 | GROMACS 三点式退火平衡 | simulation |
| 10 | GROMACS 生产模拟 | simulation |

## 前端渲染伪代码

```python
def render_progress(status):
    if status.state == "idle":
        return None  # 不显示

    activity = status.activity
    show(f"⏳ 正在使用 {activity['tool']} 进行{activity['operation']}")
    show(f"当前进度：{activity['target']}（{activity['current']}/{activity['total']}）")
    if status.state == "retrying":
        show(f"自动修复：第 {status.repair.attempt}/{status.repair.max_attempts} 次")
        for change in status.repair.adjustments:
            show(f"已调整：{change.name} {change.before} → {change.after}")
    if status.error:
        show(status.error)
```

## 状态流转图

```
IDLE ──→ RUNNING ──→ RETRYING ──→ RUNNING ──→ ... ──→ DONE
              │          │              ↑
              │          └──→ ESCALATED ─┘ (Agent 放弃)
              │
              ├──→ AWAITING_CONFIRMATION ──(替代方案)──┐
              │                 │                         │
              │                 └──(明确确认)→ RETRYING ─┘
              │
              └──→ STOPPING ──→ ABORTED (用户中止 / 进程已失活)
                                      │
                         /resume ────┼──→ AWAITING_CONFIRMATION ──→ RETRYING
                    拒绝 /fork ──────┘       (无 pending_action)
                                      \
                               有效 /fork ───→ CHILD RETRYING (父 run 不变)
```
