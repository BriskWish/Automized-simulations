# 运行助理开发方案

> 状态：Phase A 与性能/可靠性升级核心实现完成；EQ 失败后的提案、替代方案、明确授权和受控重跑已实现；实际建盒审计查询已实现；真实目标体系验收待补；Phase B-D 规划中
> 最后核验：2026-08-04
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

当前 `PipelineOrchestrator` 已具备单次运行工作区：为每个运行创建 `md_run/<run_id>/`，将配置快照及本次需要的 `.gjf` 或可复用 Step 1 中间产物复制进去，并使量子、拓扑和模拟步骤在同一工作区读写产物。新任务总是从 Step 1 开始，不能继承根目录 `status.json` 的完成步骤；续跑必须由受控调用方显式传入原运行目录和步骤。缺少中间产物但存在 `.gjf` 时会正常执行 Step 1，不产生前端错误；失败时仍由所属 LayerAgent 处理。

Phase A 之前的基线与目标如下：

| 项目 | 当前情况 | 运行助理目标 |
|---|---|---|
| 运行身份 | 启动后再推导 `run_dir` | 已实现启动时原子预占锁并立即返回稳定 `run_id` |
| 状态 | 根目录单一 `status.json`，只代表当前/最近运行 | 已实现每 run 的独立状态快照；根文件不作为运行事实 |
| 事件 | 无可追加的事件记录 | 已实现 `events.jsonl`：状态变化、步骤结果的公开摘要 |
| 配置来源 | 运行目录有 `config.json` 快照 | 已实现配置和量子输入（`.gjf`、`.fchk`/`.molden`）哈希、后端和产物登记；代码/依赖版本待补 |
| 用户操作 | 前端以启动/停止为主 | 先只读，后以 ActionProposal + 显式确认执行 |
| 日志与产物 | 文件存在于运行目录但缺少统一查询 API | 原始日志仅保留给开发排障，不经 UI 或 LLM 工具读取 |

初版已提供历史列表和只读运行观察。EQ 协议调整的受控重跑已落地：失败后只创建待确认动作，方案会列出允许复审的 EQ 参数；`awaiting_confirmation` 仅表示“LLM 已返回当前方案、尚未得到用户明确确认”，不能回退为未知、重试中或运行中。只有当前工程仍携带 `awaiting_confirmation` 动作时，用户提出明确的参数或阶段修正才能请求替代方案、无歧义批准语才能启动受控重跑；其他状态下的同类文本仍只读解释，不具备控制权限。替代方案会生成新的 `action_id`，原方案失效，配置在再次明确确认前保持不变；确认接口接受“确认”“同意该方案并重跑”等无歧义表达，且在成功保留启动锁后立即写入 `retrying`，随后由受控子进程流转到 `running`。启动失败时恢复原 `awaiting_confirmation` 快照。当前受限动作只允许从 Step 9 重跑 EQ，或在真空区等必须重建初始盒子的情形从 Step 7 依次重跑 Box、EM、EQ、PROD；不开放任意 run、任意路径或任意步骤的通用 resume。

## 3. 运行数据契约

每一次运行的目录为 `md_run/<run_id>/`，其中 `run_id` 必须由受控生成器产生，不能直接接受用户提供的路径。建议最小布局：

```text
md_run/
  index.json                         # 运行摘要索引，原子写入
  <run_id>/
    config.json                      # 启动时冻结的配置快照
    manifest.json                    # RunRegistry 的公开运行审计元数据和产物清单
    md_manifest.json                 # 模拟层私有的阶段许可、指纹与 checkpoint
    topology_manifest.json           # 拓扑层私有的 Step 4/5 组件计划
    status.json                      # 此运行最新状态
    events.jsonl                     # 追加式审计事件
    input/ or *.gjf, *.fchk, *.molden # 已冻结的量子输入（实现可以保持现有平铺布局）
    logs/                            # 外部程序和控制器日志
    artifacts/ or step outputs       # 计算产物（实现可逐步迁移）
```

第一版不要求移动已存在的运行产物，但必须让其公开路径由 `manifest.json` 描述；不允许 UI 或 LLM 用任意文件名扫描项目根目录。`md_manifest.json` 和 `topology_manifest.json` 分别是模拟层与拓扑层的私有执行契约，运行助理不从中推断公开状态。唯一受限例外是 `tools_get_box_parameters_run`：它只读取 `md_manifest.json.box_attempts` 的最近一条，并按固定白名单返回建盒策略、目标/实际密度、请求/实际盒矢量、角度、体积和原子数；不返回路径、命令、日志、阶段许可、checkpoint 或其他私有字段。

### 3.1 RunRegistry `manifest.json` 最小字段

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

`parent_run_id` 仅在从旧运行创建新运行（fork）时填写。恢复原运行与创建 fork 是两种不同动作：前者通常不修改配置快照，后者必须生成新的 `run_id` 和新的快照。已批准的 EQ 协议调整是受限例外：其私有动作记录保存审批前配置哈希，只有确认后才原子改写同一 run 的配置快照，并由下一个 MD 阶段将该版本归档到 `md_manifest.json` 的配置修订记录。

### 3.2 状态与事件

`status.json` 继续使用 `PipelineStatus` 的稳定字段。Phase A 的 run 级快照增加顶层 `run_id`，步骤 ID 复用既有 `step` 字段；独立 `attempt_id` 和可定位的 `error_ref` 留给后续阶段。根目录旧 `status.json` 不作为运行事实来源；未绑定 run 的启动冲突或启动失败仅写入独立 `startup_audit.json`。

每个 `events.jsonl` 记录 `timestamp`、`event_type`、`run_id`、`details`。公开 `details` 仅记录 activity、完成状态和摘要错误，不含 Agent 动作、原始日志、命令行、绝对路径或底层诊断；EQ 的等待、替代提案和确认均以受限状态事件记录，替代事件为 `pending_action_revised`。

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
| `get_box_parameters_run` | 最近一次 Packmol 的建盒策略、目标/实际质量密度、实际盒矢量和体积 | 仅固定白名单的 `md_manifest.json.box_attempts[-1]`，不推断平衡密度 |
| `get_run_environment` | 外部软件可用性和发现来源摘要 | 读取脱敏 `environment_report.json`，不返回路径或变量值 |
| `get_md_eta_run` | GROMACS 当前阶段的 ETA 或运行心跳 | 仅读取脱敏 `mdrun_eta.json`，不触发刷新；ETA 仅在 `mdrun -v` 已给出有效剩余时长或 GROMACS 2025 `will finish <ctime>` 预测时返回，其他情况只说明心跳与最近阶段产物更新 |

LLM 的系统提示必须明确：不运行 shell，不编辑源代码、配置或数据集，不执行未确认动作，不把缺失信息编造成运行结果。它只能基于工具返回的公开 `run_id`、状态、工序报告和产物摘要作答；不得请求或展示原始日志、stderr、命令行、绝对路径或 Agent 底层诊断。ETA 是 GROMACS 在 `eta_observed_at` 时报告的运行时预测，必须原样说明其观测时刻，不能把它表述为保证的结束时刻，也不能按阶段总时长、步骤或文件更新时间自行推算。工具提供 `_local` 时间字段时，助理必须优先使用，未经转换的 ISO 时间一律视为内部 UTC。唯一受控例外是等待确认的 EQ：用户明确提出替代调整时，后端将受限上下文交给 Simulation Agent 生成新 proposal；LLM 仍不能直接写配置、启动或确认动作。

### 4.1 策展经验库（Phase B 待实现）

运行助理当前没有、也不得通过任意路径读取 `docs/`。已验证案例先维护在 [`knowledge.md`](knowledge.md) 的“已验证运行诊断经验”章节；它是后续经验工具的人工审阅来源，不是当前 Phase A 的运行时输入。

Phase B 应新增只读 `tools_lookup_experience_run`。该工具只能根据当前已选 run 的公开步骤、错误类别和已公开配置，返回白名单化案例中的：适用签名、已验证事实、不可作出的结论、建议补充证据和需用户确认的下一步。不得返回文档全文、原始日志、命令行、绝对路径或未匹配案例。

经验条目至少须包含 `case_id`、适用范围、证据、可得结论、禁止外推、建议动作和维护日期。工具匹配到条目时仍必须先说明它是历史经验；当前 run 缺少必要证据时应明确“无足够证据匹配”，不能把经验改写为该 run 的诊断事实。经验工具永远只提供建议，配置修改、重试或创建派生运行仍必须走确认式控制。

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

已完成：`PipelineOrchestrator` 在创建 run 时注册 manifest、写入脱敏外部工具能力报告，并在每个状态转换时同步 run 状态和事件；GROMACS `mdrun -v` 的 ETA 与阶段产物心跳会被归约为 `mdrun_eta.json`，并每 15 秒刷新 run 内状态快照而不扩张事件流。`RunRegistry` 以只读、安全字段和本地化展示时间供运行助理查询；`frontend_api.py` 只经 `RunRegistry` 读取运行信息；`app.py` 保留欢迎语为运行助理对话框的首个气泡，再将当前 run 的工程状态及待确认调整作为其下独立、可替换的信息气泡，并将普通问答作为后续独立气泡。可用 ETA 在状态气泡中简写为“当前步骤预计结束：<本地时间>”。状态与待确认动作必须从同一个当前 `run_id` 快照读取，绝不扫描历史等待项；定时信息不会累积到浏览器对话历史或传入 LLM。浏览器内的普通对话也绑定该 run，工程切换后先重置为欢迎消息再向 LLM 传递上下文。确认停止会公开 `stopping` 与最终 `aborted`，运行助理只解释该持久化事实，不执行停止操作。为保证高影响控制不经模型推断，文本“中止”“暂停”“稍后”等只保留工程当前状态；只有独立的“中止流水线”按钮经第二次点击才调用停止入口。`agent_run.py` 和其 tool list 仍严格只读。`agent_run.py` 是按请求创建的无状态 Agent；对话历史仅保留在浏览器会话，不作为运行事实或审计记录。

`agent_run.py` 与其 tool list 保持严格只读。唯一的控制例外位于 `frontend_api.revise_pending_action`：它仅在同一 run 处于 `awaiting_confirmation` 时生成并校验替代 `pending_action`，不写 `config.json`、MDP、阶段许可或进程；替换后的动作必须由用户再次明确确认。

验收：可列出至少三个历史 run；选中旧 run 后能看到正确状态、步骤、失败原因和已登记产物；未确认或仅替换方案时不产生新进程、不改写配置；确认后先观察到 `retrying`，再观察到 `running` 和对应重跑阶段。

### Phase B：错误解释与 MD 指导

范围：按运行上下文解释 `ErrorKind`、LayerAgent 动作和 MD 结果；MD Advisor 提供参数和流程建议，但不写配置。

改造点：把各层 `StepResult`、error、日志引用和 Agent action 标准化写入事件；实现白名单化 `tools_lookup_experience_run`，将经审阅的案例与当前 run 的公开证据匹配；Config Agent 支持与运行助理不同的对话模式及多轮上下文摘要。

验收：运行助理工程状态和对话对同一错误给出相同的公开摘要；经验命中必须附带适用边界和待验证条件，未命中不得臆造案例；`step_id`、`ErrorKind` 和证据引用仅供内部 LayerAgent 处理，不能向用户暴露；Advisor 建议与实际 run 配置一致，并能说明建议的前提。

### Phase C：确认式运行控制

范围：`ActionProposal`、确认 UI、`RunController`、安全 abort/resume/fork。

已落地的最小前置能力包括现有前端的确认式停止，以及 EQ 失败后的受限 `retry_step`：按钮首击或“中止流水线”文本只进入待确认态，第二次点击或精确“确认中止”才持久化停止意图、`stopping`/`aborted` 终态和 run 事件；GROMACS 在安全点写 checkpoint。EQ 修正采用“LLM 提案 -> 用户修正 -> LLM 替代提案 -> 用户确认”流程，未确认时不改写配置、不启动进程；确认恢复固定经过 `awaiting_confirmation -> retrying -> running`。它不是 Run Assistant tool，也不开放任意 resume 或 fork。

改造点：为编排器公开不依赖 UI 的 `abort`、`resume`、`fork` 入口；进程以 PID/PGID 与进程启动身份管理；操作完成后写事件及产物失效关系。

验收：未确认 proposal 没有任何副作用；重复确认同一 `action_id` 幂等；停止操作不影响其他 run；配置变化只能创建 fork；失败的 resume 能给出结构化拒绝原因。

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
