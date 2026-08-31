# 流水线状态接口

## 读取边界

前端和运行助理只读取当前选中 `run_id` 的公开状态。`status.json` 是最新快照，`events.jsonl` 是脱敏事件流；日志工作区可只读显示 `run_manifest.json` 的公开 registry 投影和 `events.jsonl`。私有 manifest sections、原始日志、命令、路径、密钥和 Agent 原始输出不得进入公开接口。

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
| `escalated` | 自动处理不适用、失败或需要用户决策。 |
| `done` | 已完成声明范围；完整流程才可表示 PROD 完成。 |
| `aborted` | 已停止；只能通过完整受控命令恢复或派生。 |

新工程完成 registry 注册、但尚未绑定首个 `status.json` 的可识别短暂空档，不新增状态机状态；前端将其投影为 `preparing`（中文“准备中”），并记录对应的运行助理展示事件。读取错误、损坏快照等其他 `unknown` 情形仍保持未知，不得伪装为准备中。`preparing` 仅是公开展示值，不能用于控制接口或状态迁移。

右侧进度展示当前将要或正在执行的步骤，而不是仅计数已完成步骤：准备中与第 1 步执行时为 `1/10`；已完成 9 步并进入第 10 步时为 `10/10`。`awaiting_confirmation`、`aborted`、`escalated` 等无活动步骤的状态仍显示实际已完成数量；`done` 固定为 `10/10`。

## 控制接口

- 停止必须通过按钮和服务端二次确认；聊天文本没有停止权限。
- `/resume` 校验当前 run、断点、许可、checkpoint、配置指纹和启动锁。
- `/fork` 创建独立子 run，不能复制父 run 的阶段产物、许可或聊天记录。
- `/switch` 只切换展示绑定，严格验证运行编号，并在源与目标 run 分别写入事件。
- 待确认方案必须绑定 run、动作标识和配置指纹；未确认不得改写配置或启动进程。
- `POST /api/runs/{run_id}/pending-action/confirm` 仅接受当前 `awaiting_confirmation` 动作的 `action_id`、`state_revision` 与 `config_fingerprint`；三者任一过期即拒绝，其他状态不提供重跑权限。
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
