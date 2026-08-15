# 运行助理开发方案

> 状态：Phase A 与性能/可靠性升级核心实现完成；EQ 失败后的提案、替代方案、明确授权和受控重跑已实现；用户中止后的显式 `/resume` 与 `/fork`、按 run 历史持久化和严格 `/switch` 已实现；实际建盒审计查询已实现。当前版本的成熟方案只验收受支持 profile 十步完成且最终无错误；科学体系预测、后处理/分析和远程执行不在本版本范围内。项目说明与架构问答已并入方案助理的只读子模式；Phase B-D 的其余增强仍为后续工作。
> 最后核验：2026-08-14
> 责任边界：0 号负责跨层契约和验收；3 号负责运行控制与模拟执行接口；4 号负责前端交互；各层工程师负责本层失败报告的准确性。

## 1. 决策与目标

运行助理（Run Assistant）是面向用户的**运行观察、解释与受控操作入口**。它不是第二个编排器，也不替代现有的 Quantum/Topology/Simulation LayerAgent。

职责分离如下：

```text
用户问题
   |
   +-- MD Advisor：流程、参数与结果解读建议，只读
   |
   +-- Run Assistant：某次运行的状态、日志、产物、失败解释和动作提案
                           |
                           +-- 用户确认后的动作
                                      |
                                      v
                         PipelineOrchestrator：确定性地执行/停止/续跑/创建新运行
                                      |
                                      v
                         LayerAgent：仅在所属步骤失败时诊断并调用本层工具修复
```

核心原则：LLM 负责理解、解释和提出建议；确定性程序负责实际执行；改变计算配置或产物的操作必须经过用户确认并可审计。

## 2. 当前基线与差距

当前 `PipelineOrchestrator` 已具备单次运行工作区：为每个运行创建 `md_run/<run_id>/`，将配置快照及与后端匹配的原始输入（G16/G09 `.gjf`、ORCA `.inp`）复制进去，并可额外复制 Step 1 中间产物。原始输入在方案和启动时均会审计；中间产物不能替代它。新任务总是从 Step 1 开始，不能继承根目录 `status.json` 的完成步骤；续跑必须由受控调用方显式传入原运行目录和步骤。缺少中间产物但原始输入完整时会正常执行 Step 1，不产生前端错误；失败时仍由所属 LayerAgent 处理。

Phase A 之前的基线与目标如下：

| 项目 | 当前情况 | 运行助理目标 |
|---|---|---|
| 运行身份 | 启动后再推导 `run_dir` | 已实现启动时原子预占锁并立即返回稳定 `run_id` |
| 状态 | 根目录单一 `status.json`，只代表当前/最近运行 | 已实现每 run 的独立状态快照；根文件不作为运行事实 |
| 事件 | 无可追加的事件记录 | 已实现 `events.jsonl`：状态变化、步骤结果的公开摘要 |
| 配置来源 | 运行目录有 `config.json` 快照 | 已实现配置和量子输入（`.gjf`/`.inp`、`.fchk`/`.molden`）哈希、后端和产物登记；代码/依赖版本待补 |
| 用户操作 | 前端以启动/停止为主 | 先只读，后以 ActionProposal + 显式确认执行 |
| 日志与产物 | 文件存在于运行目录但缺少统一查询 API | 原始日志仅保留给开发排障，不经 UI 或 LLM 工具读取 |

初版已提供历史列表和只读运行观察。EQ 协议调整的受控重跑已落地：失败后只创建待确认动作，方案会列出允许复审的 EQ 参数；带 `pending_action` 的 `awaiting_confirmation` 表示“LLM 已返回当前方案、尚未得到用户明确确认”，不能回退为未知、重试中或运行中。诊断证据同时支持多个可能原因时，动作可含最多 3 个相互独立的候选方案；每项公开可能原因、证据摘要、核心修改和重跑起点。用户回复“方案1/方案一/选择方案1”只选择该项并保持等待；回复“确认方案1/确认方案一”才同时选择并授权执行。未选择时的“确认”不启动任何进程。只有当前工程仍携带 `pending_action` 的 `awaiting_confirmation` 时，用户提出明确的参数或阶段修正才能请求替代方案、无歧义批准语才能启动受控重跑；其他状态下的同类文本仍只读解释，不具备控制权限。替代方案会生成新的 `action_id`，原方案失效，配置在再次明确确认前保持不变；确认接口在成功保留启动锁后立即写入 `retrying`，随后由受控子进程流转到 `running`。启动失败时恢复原 `awaiting_confirmation` 快照。当前受限动作只允许从 Step 9 重跑 EQ；真空区和密度只作为诊断证据，不会强制回到 Step 7 重建盒子。不开放任意 run、任意路径或任意步骤的通用 resume。替代方案若未通过后端校验，运行助理将显示脱敏的具体原因而非统一失败文案，原动作保持可见、可继续修改或确认；拒绝原因来自服务器的字段/范围/快照校验，不是 LLM 对运行结果的解释。

G06 对中止 run 增加了严格受限的例外，而非任意 resume：只有完整 `/resume` 或 `/fork` 会在调用只读 Agent 前进入受控分支。`/resume` 重新读取原 run 的冻结 `config.json`，在原目录从首个未完成步骤恢复；`/fork path=value` 或 `/fork {JSON}` 继续以确定性路径直接校验。完整 `/fork` 后的自然语言可调用无工具 LLM 生成候选字段和值，例如“重跑 EQ 段，tau_p 设置为 1”只能映射为 `md.eq.tau_p=1`；服务端重新校验字段归属、停止步骤、配置 schema 和指纹后，写入独立 `pending_fork`，用户回复“确认 fork/同意 fork”才创建带 `parent_run_id` 的子 run，并从 Step 4 或 Step 6 的安全边界重放。子 run 在进入 `retrying` 前必须初始化自己的私有 simulation/protocol manifest；它只复制边界之前的科学输入，绝不继承父 run 的 MDP、建盒、阶段许可、checkpoint、轨迹、配置修订或浏览器历史。LLM 不拥有文件、状态机或进程权限。普通文本和不完整命令仍只读。带 `pending_action` 的 `awaiting_confirmation` 仍专用于 EQ 方案；过早、未知或未变化的 fork 参数被拒绝后，父 run 通过 CAS 回到无 `pending_action` 的 `awaiting_confirmation`，只等待下一条显式命令。

运行助理输入框仅在内容以 `/` 开头时显示本地 slash menu，提供 `/resume`、`/fork` 与 `/switch` 的补全、鼠标选择和方向键/Enter 选择；选择只回填命令文本，绝不提交或启动进程。`/switch` 仅接受 `/switch md__YYYYMMDDHHMM` 或 `/switch YYYYMMDDHHMM`，其余数量或格式的参数全部拒绝；目标必须是 registry 中已存在的 run。菜单没有常驻布局空间，普通问答、其他面板及控制权限不受影响；提交后仍由服务端的命令解析、状态和启动锁校验裁决。

## 3. 运行数据契约

每一次运行的目录为 `md_run/<run_id>/`，其中 `run_id` 必须由受控生成器产生，不能直接接受用户提供的路径。建议最小布局：

```text
md_run/
  index.json                         # 运行摘要索引，原子写入
  <run_id>/
    config.json                      # 启动时冻结的配置快照
    run_manifest.json                # v2：registry 公开 section 与四个私有 section
    manifest.json                    # 仅历史 run 的兼容公开 manifest
    md_manifest.json                 # 仅历史 run 的兼容 MD manifest
    topology_manifest.json           # 仅历史 run 的兼容拓扑 manifest
    status.json                      # 此运行最新状态
    events.jsonl                     # 追加式审计事件
    logs/structured.jsonl             # 持续追加的脱敏结构化运行事件
    input/ or *.gjf, *.inp, *.fchk, *.molden # 已冻结的量子输入（实现可以保持现有平铺布局）
    logs/                            # 外部程序和控制器日志
    artifacts/ or step outputs       # 计算产物（实现可逐步迁移）
```

新 run 不移动既有科学产物，但所有低频 metadata 写入 `run_manifest.json`。不允许 UI 或 LLM 用任意文件名扫描项目根目录。`registry` 是唯一公共投影；`provenance`、`topology`、`simulation`、`protocol` 均是私有 section，由各自层级按 section revision/CAS 管理，运行助理不从中推断公开状态。历史拆分 manifest 只读兼容，终态迁移默认保留旧文件。唯一受限例外是 `tools_get_box_parameters_run`：它只读取 simulation section 的 `box_attempts` 最近一条，并按固定白名单返回建盒策略、目标/实际密度、实际盒矢量、角度、体积和原子数；不返回路径、命令、日志、阶段许可、checkpoint 或其他私有字段。

### 3.1 RunRegistry `registry` section 最小字段

```json
{
  "schema_version": 1,
  "run_id": "20260731T101530Z-a1b2c3",
  "created_at": "2026-07-31T10:15:30Z",
  "parent_run_id": null,
  "mode": "new",
  "config_sha256": "...",
  "input_files": [{"path": "MOL.gjf", "sha256": "..."}],
  "dependencies": [],
  "artifacts": [],
  "status_path": "status.json"
}
```

`parent_run_id` 仅在从旧运行创建新运行（fork）时填写。恢复原运行与创建 fork 是两种不同动作：前者不修改配置快照，后者必须生成新的 `run_id` 和新的快照。registry 还维护最近 32 条 `control_history`，每项只记录动作、结果、停止/续跑步骤、参数路径与父 run ID；参数值始终以对应 run 的冻结 `config.json` 为唯一来源。已批准的 EQ 协议调整是受限例外：其私有动作记录保存审批前配置哈希，只有确认后才原子改写同一 run 的配置快照，并由下一个 MD 阶段将该版本归档到 simulation section 的配置修订记录。

### 3.2 状态与事件

`status.json` 继续使用 `PipelineStatus` 的稳定字段。Phase A 的 run 级快照增加顶层 `run_id`，步骤 ID 复用既有 `step` 字段；独立 `attempt_id` 和可定位的 `error_ref` 留给后续阶段。根目录旧 `status.json` 不作为运行事实来源；未绑定 run 的启动冲突或启动失败仅写入独立 `startup_audit.json`。

每个 `events.jsonl` 记录 `timestamp`、`event_type`、`run_id`、`details`。公开 `details` 仅记录 activity、完成状态和摘要错误，不含 Agent 动作、原始日志、命令行、绝对路径或底层诊断；EQ 的等待、替代提案和确认均以受限状态事件记录，替代事件为 `pending_action_revised`。受控恢复使用 `resume_*` 与 `fork_*` 事件族，details 仅含动作结果、停止/续跑步骤、参数路径和父 run ID。

`logs/structured.jsonl` 是 run-local 的内部执行流，不替代公开 `events.jsonl`、`decision_trace.jsonl` 或 `process_lifecycle.jsonl`。每条记录含 `schema_version`、UTC `timestamp`、共享 `sequence`、`event_code`、`level`、`run_id`，并按需记录步骤/层、结果、错误类别、动作/策略/模型/Prompt 标识、受控参数字段、耗时和逻辑产物引用。编排器与 GROMACS 运行期间追加步骤、状态和进程事件；mdrun 心跳按现有低频周期追加。路径、命令、原始输出、完整提示词和密钥类字段统一脱敏，写入失败不改变步骤结果。

写入约束：状态和索引采用原子替换；事件采用单行追加并加锁；任何写入失败不得阻断原始计算步骤，但必须记录为可见的运行管理告警。

## 4. 助理接口与权限

第一版只提供只读工具。工具实现位于新的 `toolist_run.py`，并只接受已校验的 `run_id`：

| 工具 | 返回内容 | 限制 |
|---|---|---|
| `list_runs` | 历史运行摘要、时间、状态、父运行 | 分页，不返回绝对敏感路径 |
| `get_run_status` | 某运行的工具、操作、对象、进度和失败摘要 | 读取该 run 的 `status.json` |
| `get_step_report` | 一个步骤的公开工序、完成状态和摘要错误 | 数据来自公开 status/event，而非猜测 |
| `list_run_artifacts` | 已注册产物及类型、大小、产生步骤 | 仅 manifest 已登记条目 |
| `explain_run_error` | 当前公开工序进度和摘要错误 | 只读，不触发重试 |
| `get_run_config` | 当前运行冻结配置 | 读取该 run 的 `config.json` 快照 |
| `get_box_parameters_run` | 最近一次 Packmol 的建盒策略、目标/实际质量密度、实际盒矢量和体积 | 仅固定白名单的 simulation section `box_attempts[-1]`，不推断平衡密度 |
| `get_run_environment` | 外部软件可用性和发现来源摘要 | 读取脱敏 provenance section 的 capabilities；历史 run 回退 `provenance.json`/`environment_report.json`，不返回路径或变量值 |
| `get_md_eta_run` | GROMACS 当前阶段的 ETA 或运行心跳 | 仅读取脱敏 `mdrun_eta.json`，不触发刷新；ETA 仅在 `mdrun -v` 已给出有效剩余时长或 GROMACS 2025 `will finish <ctime>` 预测时返回，其他情况只说明心跳与最近阶段产物更新 |

LLM 的系统提示必须明确：不运行 shell，不编辑源代码、配置或数据集，不执行未确认动作，不把缺失信息编造成运行结果。它只能基于工具返回的公开 `run_id`、状态、工序报告和产物摘要作答；不得请求或展示原始日志、stderr、命令行、绝对路径或 Agent 底层诊断。ETA 是 GROMACS 在 `eta_observed_at` 时报告的运行时预测，必须原样说明其观测时刻，不能把它表述为保证的结束时刻，也不能按阶段总时长、步骤或文件更新时间自行推算。工具提供 `_local` 时间字段时，助理必须优先使用，未经转换的 ISO 时间一律视为内部 UTC。唯一受控例外是等待确认的 EQ：用户明确提出替代调整时，后端将受限上下文交给 Simulation Agent 生成新 proposal；LLM 仍不能直接写配置、启动或确认动作。

### 4.1 统一 Prompt 契约（已实现）

用户可见助理的动态 Prompt 由 [`src/willy/prompt_contract.py`](../src/willy/prompt_contract.py) 统一生成，当前版本为 `willy-prompt-contract-v1`。每次调用模型都必须附带以下九段、由服务端构造的结构化上下文：

1. 任务类型
2. 当前层与步骤
3. 不可修改事实
4. 已验证证据
5. 未验证假设
6. 允许动作
7. 禁止动作
8. 剩余预算
9. 期望输出格式

运行助理在每轮模型调用都将服务端选中的 `当前工程编号` 写入“不可修改事实”，并在再次 tool-call 前刷新剩余模型轮次和总时限。初始上下文仅选取状态、公开错误、ETA、建盒与环境的白名单摘要；**不**直接注入完整 `config.json`、日志尾部、绝对路径、产物清单或浏览器聊天历史。模型后续调用 `get_config`、`list_artifacts` 等只读工具时，返回内容同样会归约为配置字段摘要、数量或可用性统计，避免二轮 Prompt 重新扩张或泄露私有运行数据。

配置方案助理复用同一契约。方案生成和明确修改阶段只保存会话级“待确认方案概要”，供下一轮增量修改使用；它不是运行配置冻结，也不会写入根目录 `config.json`。用户提出修改后，服务端以当前待确认候选为基线重新生成，并再次执行量子输入审计、workflow schema 校验和执行边界校验；全部通过后才覆写待确认候选。只有用户明确确认开始运行后，`start_pipeline()` 才重复审计、取得启动锁并写入最终运行配置，随后由运行器复制 run-local 快照。共享模块定义权限、证据、预算与输出边界；`agent_config.py`、`agent_run.py` 各自保留领域规则和实际 tool 白名单。当前实现不把模型输出当作状态机或动作契约的授权依据。

### 4.2 MD 运行知识库（本轮已实现，维护契约）

本轮为 **Simulation Agent 的 EQ 失败 proposal** 建立受限的 GROMACS 运行知识检索；它不是 Run Assistant 的通用文档读取能力，运行助理及其 9 个只读工具仍不得读取任意 `docs/` 路径。知识源固定为 [`knowledge_mdrun.md`](knowledge_mdrun.md)，初始覆盖 GROMACS User Guide 的三类经人工整理条目：运行时错误、`mdrun` 运行特性、`.mdp` 参数。条目保存来源 URL、章节、适用条件、受限事实和兼容性提醒，但不保存原始页面全文或可执行命令。

本轮已按以下顺序实现并通过 5 个专门测试及完整回归；下列条目同时是后续维护的验收契约：

1. 已建立受版本控制的 Markdown 条目格式和解析器，只公开稳定的 `number + name` 索引；所有内容由本地受控解析器读取，运行期间不联网抓取文档。
2. 已接入只读 `tools_lookup_mdrun_knowledge`。模型只能用条目数字和条目名称成对请求；处理器验证二者匹配、单次至多 3 条，返回白名单字段而不返回 Markdown 原文、路径、日志或命令。
3. 已在每个 proposal 调用内限制 2 次知识工具调用；一次调用结束后新的真实失败由新的 proposal 调用重新计数。重复提问、同一等待方案的渲染或用户替代要求不会在一次调用内部增加预算。除该工具外，proposal 阶段不暴露任何可写或执行工具。
4. 已将错误类型、阶段、脱敏证据、冻结配置和标记为“未验证”的 Agent 假设注入 proposal prompt；不预选原因或条目。模型自行从索引中判断是否检索，再生成最多 3 个相互独立、待用户确认的方案。
5. 已为每个候选方案写入受限来源元数据：`knowledge_status`、实际读取并验证过的 `knowledge_entries`、`advice_source` 和版本兼容提示。没有命中、知识源不可用或模型未检索时，前端明确显示“LLM 未经知识库验证的推断”，不会伪装为文档结论。
6. 已覆盖索引/名称校验、三条上限、两轮预算、工具不可写、虚假条目剥离、命中/未命中公开字段、pending-action 选择/替换/确认门禁及 UI 来源文案；2026-08-11 当前完整回归为 815 passed、9 skipped（收集 824 条）。

知识条目只是诊断参考，不替代当前 run 的验收事实、ErrorKind、阶段许可或人工确认。文档来源的参数示例可能与实际安装的 GROMACS 版本不完全适配；命中时仍须展示兼容性提醒，用户确认后才可应用受限参数修改。

## 5. 受控操作模型

第二版才开放运行操作，且 LLM 永远不能直接启动进程。它只能产生 `ActionProposal`：

```json
{
  "action_id": "act-...",
  "run_id": "...",
  "kind": "resume | retry_step | abort | fork",
  "target_step_id": "optional",
  "config_diff": {},
  "impact": "will rerun topology_molecule and downstream steps",
  "risk": "existing run is unchanged; a new run will be created",
  "expires_at": "2026-07-31T10:30:00Z"
}
```

前端必须展示影响范围、配置差异和风险。仅当用户以 `action_id` 明确确认后，`RunController` 才校验 proposal 未过期、运行状态允许且权限满足，然后调用 `PipelineOrchestrator` 的确定性公开 API。

动作语义必须固定：

| 动作 | 是否修改原运行 | 适用条件 |
|---|---|---|
| `abort` | 仅终止该 run 的受管进程组并写事件 | 运行中 |
| `resume` | 是，但配置快照不可改变 | 可恢复步骤和产物都已验证 |
| `retry_step` | 是，仅重新执行目标步骤及失效的下游步骤 | 依赖图能证明输入完整 |
| `fork` | 否，创建子运行 | 配置、输入、力场、后端或参数需要改变 |

用户重新上传文件、变更结构或修改配置时一律走 `fork`，不可覆盖旧运行输入或快照。运行目录和 manifest 永久保留旧版本，除非由独立的保留策略清理。

## 6. 分阶段实施计划

### Phase A：可观察性 MVP（核心实现完成）

范围：`RunRegistry`、每运行状态/manifest/event、只读查询工具和基本前端运行列表。

建议新增模块：

```text
src/willy/run_registry.py       # run_id、索引、manifest、事件的唯一写入口
src/willy/agent_run.py          # 运行问题的只读对话编排
src/willy/toolist_run.py        # 只读 LLM tool schema 与 handler
```

已完成：`PipelineOrchestrator` 在创建 run 时注册 manifest、写入脱敏外部工具能力报告，并在每个状态转换时同步 run 状态和事件；GROMACS `mdrun -v` 的 ETA 与阶段产物心跳会被归约为 `mdrun_eta.json`，并每 15 秒刷新 run 内状态快照而不扩张事件流。`RunRegistry` 以只读、安全字段和本地化展示时间供运行助理查询；`frontend_api.py` 只经 `RunRegistry` 解析运行身份。`app.py` 将欢迎语固定为对话框的第一个独立气泡，并关闭 Gradio 对连续助理消息的视觉合并。每个 run 的有界显示历史原子保存为 `run_assistant_history.json`；刷新页面或用 `/switch` 切换工程时只加载目标 run 的记录，不扫描或合并其他工程。当前状态卡由 `status_event_id` 绑定 run、状态、步骤、待确认动作和公开错误事件；同一身份内 ETA、心跳或进度文字只原位刷新。身份改变时，旧状态卡封存为历史，随后新建当前状态卡；公开错误以 `run_id + state_revision`、待确认调整以 `run_id + action_id` 各自作为独立、去重的事件气泡。可用 ETA 在状态气泡中简写为“当前步骤预计结束：<本地时间>”。状态、公开错误与待确认动作必须从同一个选中 `run_id` 快照读取；显示事件不会作为运行事实，也不传入 LLM 工具。`/resume` 保持原 run 绑定，成功 `/fork` 自动跟随新的子 run；`/switch` 在源、目标 `events.jsonl` 分别写切出/切入事件。确认停止会公开 `stopping` 与最终 `aborted`，运行助理只解释该持久化事实，不执行停止操作。`agent_run.py` 和其 tool list 仍严格只读。

`agent_run.py` 与其 tool list 保持严格只读。控制例外均位于 `frontend_api`：`revise_pending_action` 仅在同一 run 带 `pending_action` 的 `awaiting_confirmation` 时生成并校验替代方案；`run_assistant_control_command` 只接受完整 `/resume`、`/fork` 与 `/switch`，在调用只读 Agent 前处理。前两者重新校验状态 revision、启动锁、冻结配置、断点和参数归属；`/switch` 只改变当前展示绑定，不修改状态机、配置或进程。持久化浏览器历史可以保留给界面显示，但服务端运行事实始终优先。

验收：可列出至少三个历史 run；刷新及 `/switch` 后恢复该 run 独立历史且不串工程；选中旧 run 后能看到正确状态、步骤、失败原因和已登记产物；未确认或仅替换方案时不产生新进程、不改写配置；确认后先观察到 `retrying`，再观察到 `running` 和对应重跑阶段；中止 run 的 `/resume` 必须在原目录从安全步骤恢复，`/fork` 必须创建独立子 run 和 `parent_run_id`，过早参数必须被拒绝并返回等待状态。

### Phase B：错误解释与 MD 指导

范围：按运行上下文解释 `ErrorKind`、LayerAgent 动作和 MD 结果；MD Advisor 提供参数和流程建议，但不写配置。

改造点：把各层 `StepResult`、error、日志引用和 Agent action 标准化写入事件；实现白名单化 `tools_lookup_experience_run`，将经审阅的案例与当前 run 的公开证据匹配；Config Agent 支持与运行助理不同的对话模式及多轮上下文摘要。

验收：运行助理工程状态和对话对同一错误给出相同的公开摘要；经验命中必须附带适用边界和待验证条件，未命中不得臆造案例；`step_id`、`ErrorKind` 和证据引用仅供内部 LayerAgent 处理，不能向用户暴露；Advisor 建议与实际 run 配置一致，并能说明建议的前提。

### Phase B.1：项目说明与架构问答（已实现）

不新增 Project Assistant。现有方案助理先对带有项目架构、状态机、manifest、工具/权限边界或已实现能力标记的提问进行一次无工具 LLM 意图判断；普通模拟配置、分子和协议参数请求仍进入原有生成路径。识别为项目设计问答后，方案助理只读取服务端固定白名单中的 `README.md`、`docs/Willy.md`、`docs/status_api.md`、`docs/run_assistant_design.md` 和 `docs/document_registry.md`，按 Markdown 标题截取有限片段后再由 LLM 回答。

该子模式不读取源码、运行目录、环境变量、密钥、原始日志或任意用户指定路径，不提供 tool schema，也不会生成 config JSON、修改待确认方案、写文件、启动/停止/重跑/分叉进程。回答必须区分“已实现事实”和“规划事项”，引用文档名称与章节；没有命中、文档不可读或需要源码级证据时，应明确说明无法确认。对话气泡可保留在浏览器会话中，但不得产生持久化写入或改变已有 `pending_plan`。

验收覆盖：架构问题命中白名单片段并经两轮 LLM（意图、回答）处理；普通配置请求在分类为 `plan_request` 后完整沿用既有语义归一化、输入审计和严格 JSON 流程；设计问答没有工具调用、进程调用或待确认方案副作用。

### Phase C：确认式运行控制

范围：`ActionProposal`、确认 UI、`RunController`、安全 abort/resume/fork。

已落地的能力包括现有前端的确认式停止、EQ 失败后的受限 `retry_step`，以及用户中止后的显式续跑。按钮首击或“中止流水线”文本只进入待确认态，第二次点击或精确“确认中止”才持久化停止意图、`stopping`/`aborted` 和 run 事件；GROMACS 在安全点写 checkpoint。EQ 修正采用“LLM 提案 -> 用户修正 -> LLM 替代提案 -> 用户确认”流程，未确认时不改写配置、不启动进程；确认恢复固定经过 `awaiting_confirmation -> retrying -> running`。G06 的 `/resume`、`/fork` 不是 Run Assistant tool：它们只在完整标识出现时走前端确定性入口，受状态 CAS、启动锁、停止步骤和配置字段归属共同约束。

实现：`run_control.py` 解析命令并校验参数；`frontend_api` 预占锁、转移状态、创建 fork 快照并启动锁绑定的 CLI；编排器在原 run 或子 run 中恢复并使续跑边界后的完成标记失效。操作写入 registry 的 `control_history` 和 `events.jsonl`，进程继续以 PID/PGID 与启动身份管理。

验收：未确认 proposal 没有任何副作用；重复确认同一 `action_id` 幂等；停止操作不影响其他 run；`/resume` 的原配置和原 run 目录不变且按安全步骤恢复；配置变化只能创建 fork；过早 fork 参数回到等待状态；每个接受、拒绝和启动结果均有 manifest/event 审计。

### Phase D：历史检索与保留治理

范围：运行筛选、可比较的运行摘要、产物归档/清理策略和权限审计。

验收：清理操作有明确保留期限、预览及审计；不会删除正在运行或仍被子运行引用的目录。

## 7. 测试、质量门禁与后续评估

每个 Phase 完成后由 0 号依据以下问题评估，而不是只按界面是否出现来验收：

| 维度 | 必须证明的事实 |
|---|---|
| 正确性 | run、状态、事件、清单间的 `run_id`、步骤和产物引用一致 |
| 可复现性 | 可从 manifest 找到输入哈希、配置哈希、代码版本和依赖摘要 |
| 隔离性 | 两个并发 run 不共享可写状态、日志或子进程组 |
| 安全性 | `run_id`/日志路径无法越界；LLM 与 UI 未确认时无写入/进程副作用；日志不泄露密钥 |
| 可解释性 | 每个失败都有 `ErrorKind`、证据引用、已尝试动作和明确下一步 |
| 回归 | 现有 pipeline、LayerAgent、前端状态测试继续通过，新增 API 有单元测试和集成测试 |

最低测试集：

1. 新建 run 写入正确的 config 快照、manifest 和首条事件。
2. 并发或交错两个 run 时，读取和写入不串号。
3. 给出 `../`、绝对路径、未登记日志名时，所有查询均被拒绝。
4. LLM 只读问答无法调用任何写入工具。
5. proposal 未确认、已过期、重复确认、目标运行状态不匹配时均没有意外副作用。
6. `fork` 后父 run 文件哈希不变；子 run 有独立 `run_id`、快照和 `parent_run_id`。
7. `abort` 只终止目标 run 的 PID/PGID，并写入审计事件。

交付时需提供：接口文档更新、数据迁移说明、测试命令和结果、已知限制，以及一份真实或 fixture run 的 manifest/event 示例。未完成以上证据时，该 Phase 不进入下一阶段。

## 8. 非目标与边界

- 不让运行助理直接修改 Willy 源代码。代码可靠性讨论及代码修复属于独立的 Engineering Review/Change Agent，必须走代码审查、测试和部署流程。
- 不让 LLM 替代 `PipelineOrchestrator` 决定正常步骤顺序，或替代 LayerAgent 执行领域修复。
- 不允许 GAFF/UFF 与 OPLS-AA 在同一次运行中混用；运行助理只能报告当前 force-field family，并在用户提出变更时建议 fork。
- 不在 Phase A/B 引入自动重试、自动恢复或后台清理。自动化必须在 Phase C 有明确权限、影响范围和回滚语义后再讨论。

## 9. 开工顺序与依赖

先完成 Phase A 的数据契约和只读 API，再开始前端对话和任何控制动作。实施顺序为：

1. 0 号确认 `run_id`、manifest、事件和状态字段的 schema，并补充 `status_api.md`。已完成。
2. 3 号将编排器状态写入 `RunRegistry`，并为 resume/进程控制预留公开接口，但不改变默认执行流程。前半部分已完成；控制接口留给 Phase C。
3. 4 号在只读 API 稳定后接入运行列表、详情和聊天界面。已完成。
4. 各层工程师补足 `StepResult` 的错误、日志和产物证据，以支持 Phase B 的可靠解释。
5. 0 号完成 Phase A 评估后，才批准 Phase B/C 开发。

这样可以先把“用户看见的运行事实”稳定下来，再增加语言理解和控制能力，避免对话层成为未受约束的第二套执行系统。

## 10. 性能与可靠性升级（核心实现完成）

本节解决运行助理的响应慢问题，不扩大它的读写权限，也不改变流水线、状态机或 LayerAgent 的职责。升级后的原则是“确定性事实优先，LLM 解释补充”：状态、ETA、错误摘要、产物和环境等已有运行事实不应等待远程模型推理。

| 项目 | 方案 | 状态 | 验收 |
|---|---|---|---|
| 请求耗时观测 | 服务端记录脱敏的路由、运行 ID、上下文大小、LLM 轮数、本地读取与总耗时；不记录用户全文、提示词、路径或原始日志。 | 已完成 | 能定位慢在本地读取、模型往返或资源竞争。 |
| 确定性快速路径 | 对“当前状态/工序、ETA、错误摘要、产物、环境”等明确查询直接通过 `RunRegistry` 格式化回答，不请求 LLM。 | 已完成 | 即使 LLM 不可用，也能在本地返回这些事实。 |
| 单轮事实预取 | 复杂问题先按问题关键词预取有限的脱敏运行事实，再优先进行一次 LLM 解释；只有事实不足时才允许追加只读工具调用。 | 已完成 | 常见解释问题只发生一次模型请求；模型仍可在事实不足时使用只读工具。 |
| 上下文压缩 | 历史从最近 12 条改为最近 **6 条**，每条最多 1,800 个字符；运行事实每次重新从 `RunRegistry` 读取，不信任历史。 | 已完成 | 长对话不线性放大模型输入，且不会串用其他 run。 |
| 超时与降级 | 单次模型请求最多 25 秒、整次回答最多 45 秒、最多 3 轮模型调用；超时或异常时返回已预取的本地事实，不阻塞状态查询。 | 已完成 | LLM 网络异常时用户仍得到运行摘要。 |
| 前端阶段反馈 | 在本地读取和 LLM 解释期间显示阶段性状态；快速路径直接完成，不伪造流式 LLM 内容。 | 已完成 | 用户不会面对无响应气泡。 |
| ETA 与心跳语义 | 保留 `available/waiting/finished/unavailable`；将 GROMACS ETA 观测与进程/阶段产物心跳分离，UTC 存储与本地展示分离。旧 run 或新功能加载前启动的进程不追溯伪造 ETA。 | 已完成 | 不把旧预测、步骤、总时长或文件更新时间表述为实时结束时间。 |
| 受控操作 | 通用 `ActionProposal -> 用户确认 -> RunController` 仍属于 Phase C；当前仅实现 EQ 的受限 proposal 替换/确认和按钮停止。 | 部分完成 | LLM 不直接写配置或进程；确认恢复固定经过 `awaiting_confirmation -> retrying -> running`。 |

本轮实现已完成快速路径、上下文压缩、前端反馈、有限事实预取、轮次/超时限制与脱敏遥测，并以确定性测试和 LLM mock eval 验收。真实长程 MD 的 ETA 刷新，以及真实远程模型下的端到端响应耗时，仍需在目标体系 smoke 中独立确认。
