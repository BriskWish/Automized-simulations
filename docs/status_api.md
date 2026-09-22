# 流水线状态接口

## 溶剂库接口

- `GET /api/solvents` 返回 `candidates` 中的全量 Gaussian 内置/人工条目。
- `GET /api/solvents?query=...` 返回精确命中的 `match`；未命中时返回向量检索候选，不自动选择。
- `POST /api/solvents` 接收 `name`（可选）、`epsilon`、`epsinf`；成功返回 `ok=true` 和 `solvent`。
- 条目包含 `name`、`epsilon`、`epsinf`、`source`（builtin/manual）、`manual`；内置 `epsinf` 为 null。
- 重名、非有限值、缺参数或不满足 `epsilon >= epsinf >= 1` 返回 HTTP 400，库记录保持不变。
- 登记只修改当前安装的人工溶剂库，不启动工程、不修改已有方案、运行状态或冻结配置。

## 读取边界

前端和运行助理只读取当前选中 `run_id` 的公开状态。`status.json` 是最新快照，`events.jsonl` 是脱敏事件流；日志工作区只读显示 `run_manifest.json` 的公开 registry 投影和当前工程 `config.json` 的公开科学参数，不展示 events。私有 manifest sections、原始日志、命令、路径、密钥和 Agent 原始输出不得进入公开接口。

EQ 五段覆盖规则不新增公开状态：时间协议无效归入输入契约错误，缺段、提前结束、乱序、重复或无效数值归入平衡未通过；均不得因 GROMACS 退出码为零就显示已获 PROD 许可。规则标识、五段均值、覆盖详情与产物指纹保留在 run 的私有 EQ 验收证据中，不直接展开为公开 manifest sections。

## PipelineStatus

```json
{
  "state": "running",
  "step": 3,
  "step_label": "RESP 电荷",
  "layer": "quantum",
  "error": "操作级错误摘要",
  "error_kind": "scf_not_converged",
  "activity": {
    "tool": "ORCA",
    "operation": "结构优化",
    "target_type": "molecule",
    "target": "FEC",
    "current": 1,
    "total": 2
  },
  "done_steps": [1, 2],
  "total_steps": 10,
  "extra": {"run_id": "md__..."}
}
```

`activity` 是唯一公开工序模型。`repair` 只在已实际执行受控修复时出现；`pending_action` 只包含脱敏方案摘要和 `recovery_plan`：问题、当前步调参路径、打回前序路径、逐点证据及服务端写入的 `risk_level=high` / `manual_review_required=true`。`escalation` 只包含错误类别、已尝试动作和建议。ETA 单独来自受限 ETA 快照，不能作为完成保证。完整十步流程的 `done` 快照还会生成一个固定的公开完成交付事件，仅含相对 run 目录与 `prod.gro`、`prod.xtc`、`prod.trr`、`prod.edr` 等文件名。运行助理的 `status_event_id` 对模拟阶段纳入脱敏 ETA 令牌；等待、可用、预测变更和结束都会使前端重新读取状态气泡。

## 状态

| state | 含义 |
|---|---|
| `idle` | 当前工程没有活动流水线。 |
| `running` | 正常执行。 |
| `retrying` | 已开始实际执行受控修复。 |
| `awaiting_confirmation` | 存在需要用户明确确认的方案或恢复操作。 |
| `stopping` | 服务端已确认停止请求，等待安全退出。 |
| `escalated` | 自动推进已禁止，故障需要人工接管；不承诺所有外部进程已经退出。处理后刷新复查，通过后自动回到 `aborted`，不自动续跑。 |
| `done` | 已完成声明范围；完整流程才可表示 PROD 完成。 |
| `aborted` | 已停止；只能通过完整受控命令恢复或派生。 |

### 故障流转与刷新复查

保留八态，`idle`、`running`、`retrying`、`awaiting_confirmation`、`stopping` 及新控制操作中的 `aborted` 均有 `escalated` 故障出口；`escalated` 可记录新的关联故障。`done` 不因查询或子工程故障回退。`stopping -> aborted` 是正常停止出口，`stopping -> escalated` 是停止请求失败、退出超时等控制异常出口。正常执行及原有受控恢复迁移不变。

每次工作台/工程快照刷新（包括页面加载、手动刷新及轮询）重新查询当前工程的故障处理条件。停止请求持续 120 秒未完成时进入停止故障复查，不通过刷新发送额外终止信号。只有启动锁、MD 锁、已登记进程组、遗留 PID 与运行心跳均不再阻止复查时，才检查故障对应的处理结果：

- 环境故障重新查询该执行模块的依赖；文件/配置故障重新执行原样恢复准入，不自动接纳新的哈希。
- 停止或回收故障确认进程退出后可以解除，但不据此宣称 checkpoint 有效。
- 未知异常、科学错误等不能仅凭文件变化认定已解决。用户须点击“已完成人工处理，复查”，提交 `POST /api/runs/{run_id}/fault/complete`，携带当前 `fault_id` 和 `state_revision`；过期声明返回 409。声明不修改参数、不豁免输入或阶段许可。

复查通过，以 CAS 原子写入 `escalated -> aborted` 和 `run_fault_resolved` 事件；清空当前错误提示，但在故障记录中保留原位置、类型、关联原因及解除依据。重复刷新不重复迁移或启动进程，失败/竞争继续保持原故障。读盘失败、活进程或无法证明退出时不得假装处理完成。

公开快照可增加 `fault` 与 `fault_review`：`fault` 包含故障 ID、步骤、出错时状态、控制环节、错误类别、发生/解除时间、关联故障与解除依据；`fault_review` 是本次复查结果及能否提交人工完成声明。私有 `.fault_diagnostics.jsonl` 只记录异常类别与源码文件/函数/行号，不向前端传递堆栈或异常原文；`.fault_context.json` 保存声明绑定，`.process_owners.json` 保存独立进程组身份。后两者在 fork 中仅归档，不成为子工程的有效控制记录。

新工程完成 registry 注册、但尚未绑定首个 `status.json` 的可识别短暂空档，不新增状态机状态；前端将其投影为 `preparing`（中文“准备中”），并记录对应的运行助理展示事件。读取错误、损坏快照等其他 `unknown` 情形仍保持未知，不得伪装为准备中。`preparing` 仅是公开展示值，不能用于控制接口或状态迁移。

右侧进度展示当前将要或正在执行的步骤，而不是仅计数已完成步骤：准备中与第 1 步执行时为 `1/10`；已完成 9 步并进入第 10 步时为 `10/10`。`awaiting_confirmation`、`aborted`、`escalated` 等无活动步骤的状态仍显示实际已完成数量；`done` 固定为 `10/10`。

## 环境预检接口

配置页依赖报告新增 `runtime` 字段，包含 `ready`、`python_version`、`supported_python`、
`dependencies`（仅依赖 `name` 与 `status`）和脱敏 `error`。依赖状态限于 `available`、`missing`、
`runtime_unavailable`。已有量子、拓扑、模拟 `groups` 不变；总 `ready` 同时要求运行时和三个科学组就绪。
报告不包含解释器路径、命令、原始 stderr 或环境变量值；`written_defaults` 不登记 Python。

## 控制接口

- 普通启动、确认重跑、`/resume` 和 `/fork` 使用同一父进程 Python，不读取 PATH 中的另一个 `python3`；版本和核心依赖在 Web 启动前检查。启动锁、文件描述符传递、配置确认和步骤恢复契约保持不变。
- 停止必须通过按钮和服务端二次确认；聊天文本没有停止权限。
- 服务启动会扫描无启动锁、无新鲜 ETA/产物心跳的 transient 状态，并以 `run_aborted_after_process_exit` 审计将其 CAS 终结为 `aborted`；同一脱敏来源和阶段同步写入 registry manifest 的 `interruption_history`。该恢复从不自动启动计算。`/resume` 对当前选中 run 先执行同一受控调和，因此用户停止、流水线进程退出或服务/主机故障重启后的遗留状态，都只能经过 `aborted -> awaiting_confirmation -> retrying` 再重跑。
- `/resume` 沿用已登记 run 的稳定配置、目录与完成前缀，不继承、不改参。服务端启动前、子进程绑定和每步启动前检查哈希；变化文件须先明确声明，不补造许可。故障遗留 transient 状态仍须先通过存活证据检查。各步骤与 checkpoint 规则见 [`run_assistant.md`](run_assistant.md)。
- `POST /api/runs/{run_id}/resume` 接受当前 `state_revision`，不依赖 LLM 建议，覆盖 `aborted`、`awaiting_confirmation`、`escalated`。仅在准入通过后，`escalated` 经原有 `aborted -> awaiting_confirmation -> retrying` 路径恢复；拒绝保留原状态。原参数按钮、明确的原参数恢复指令和 `/resume` 共用此恢复实现。
- 服务端准入审计 `resume_admission_checked` 只记录 `ok`、`restart_step`、`hash_policy`，成功时增加 `checked_files`，失败时增加受限 `reason`；不公开文件路径、内容或哈希。拒绝时释放启动预留并保留原状态；子进程复查失败时终结为 `aborted`、保留完成前缀。
- `/inputs` 查询候选输入的名称、相对路径、SHA-256 和消费步骤；带声明时由 LLM 选择有界范围，服务端校验非空、格式、路径和新版本。声明回执与 `inputs_declared` 是允许展示所选相对路径/哈希的专用边界，无全局跳过开关。
- `/fork` 完整复制科学文件、配置、对话与历史证据为独立子 run，重建控制权限并失效重做范围内的旧准出；父工程不迁移状态。旧产物在新作业前归档到 `old/`。
- `/switch` 只切换展示绑定，严格验证运行编号，并在源与目标 run 分别写入事件。
- 待确认方案必须绑定 run、动作标识和配置指纹；未确认不得改写配置或启动进程。
- `POST /api/runs/{run_id}/pending-action/confirm` 接受当前 `awaiting_confirmation` 动作的 `action_id`、`state_revision` 与 `config_fingerprint`；任一过期即拒绝。确认后创建分支，响应的 `run_id` 和展示切换到子工程；重复动作按外部控制回执幂等处理，禁止写回父配置。
- `POST /api/runs/{run_id}/pending-action/revise` 接受相同三个版本绑定字段及非空 `request`。LLM 只生成建议，服务端复核启动锁和当前版本后在同一事务发布新方案、状态和事件；保持 `awaiting_confirmation`，不改配置或启动计算。成功返回同一 `run_id`、更新后的 `messages`、`snapshot` 和 `text`，确认按钮须绑定最新动作；旧绑定返回 HTTP 409，前端刷新但不自动重发。公开快照顶层 `state_revision` 用于没有待确认建议时的原参数恢复按钮。
- 普通启动和受控重跑共用启动门：子进程在启动登记、PID 记录与回收线程交接完成前不运行科学步骤。交接失败先终结并回收子进程；无法确认退出时保留启动锁，不宣称安全失败或允许重复启动。
- `unknown` 错误不得形成默认待确认重试；状态机进入 `escalated` 并记录联网检索申请/人工审核。当前版本不在运行期发起外网请求。

## 方案工作区接口

方案助理的预启动会话不属于运行状态机。前端加载且没有活动流水线时，`POST /api/proposals/default` 会恢复最近一个未正式化的方案工作区；不存在时创建 `md_run/temp__YYYYMMDDNNNN/`。`POST /api/proposals` 始终创建一个独立 `temp__`，用于本地任务栏中的“新建临时方案”。`GET /api/proposals/{proposal_id}` 可读取 `temp__`、`plan__` 或受管 `md__` 工程的受限方案会话；不返回候选配置。

首次形成待确认候选时，当前 `temp__` 原子改名为 `plan__YYYYMMDDNNNN/` 并保存 `proposal.json`。在已有 `plan__` 中继续提出新方案时，更新同一目录中的候选和会话；不会额外分配 `plan__`。在已有 `md__` 的方案助理中形成新候选时，服务端保留原工程的既有会话，再分配新的 `plan__` 并将“本轮用户请求 + 新方案回复”作为该 plan 的独立会话；两侧不复制、不互通，旧 run 的候选不会被重新确认。明确运行请求须携带该 `plan__` 标识；服务端在正常启动锁已保留、候选配置仍匹配后，才把目录原子改为分配的 `md__` 目录。注册正式 run 后，registry manifest 写入不含参数值的 `proposal_workspace_id`，`events.jsonl` 写入 `proposal_formalized`；候选配置和对话只保留在 run 本地的 `proposal.json`。目录正式化或启动失败不得伪造 `status.json` 状态转换。

本地任务删除接口为 `DELETE /api/local-items/{item_id}`。它只接受单一受管 `md__`、`plan__` 或 `temp__` 标识，且任何流水线运行中均返回冲突。删除 `plan__` 必须携带 `confirm_plan: true`，作为前端两次确认后的服务端约束；删除 run 同时移除其索引和仅用于显示的名称映射，不改写其他工程记录。

## 审计文件

| 文件 | 作用 |
|---|---|
| `run_manifest.json` | 公开 registry 与私有 provenance、topology、simulation、protocol sections。 |
| `status.json` | 当前公开状态快照。 |
| `events.jsonl` | 公开状态和控制摘要。 |
| `logs/structured.jsonl` | 私有、脱敏的步骤和进程事实。 |
| `process_lifecycle.jsonl` | 私有进程生命周期记录。 |
| `run_assistant_history.json` | 当前 run 的有界聊天与展示记录。 |
| `proposal.json` | `temp__`、`plan__` 或 `md__` 的本地有界方案会话；计划还保存候选配置，正式 run 保存正式化来源。它不由公开日志接口返回。 |

`GET /api/runs/{run_id}/logs` 只接受已登记的 `run_id`，固定返回上述 manifest 公共投影和 `events.jsonl` 内容。任一记录最多返回 1 MiB，并明确标记截断；事件流截断时保留最后部分。接口不接受任意文件名或路径。

写入使用 run 级锁和原子更新。公开接口不扫描其他 run，也不从文件时间戳推断科学阶段成功。
