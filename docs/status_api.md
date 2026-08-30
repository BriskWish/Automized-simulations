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

`activity` 是唯一公开工序模型。`repair` 只在已实际执行受控修复时出现；`pending_action` 只包含脱敏方案摘要；`escalation` 只包含错误类别、已尝试动作和建议。ETA 单独来自受限 ETA 快照，不能作为完成保证。

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

## 控制接口

- 停止必须通过按钮和服务端二次确认；聊天文本没有停止权限。
- `/resume` 校验当前 run、断点、许可、checkpoint、配置指纹和启动锁。
- `/fork` 创建独立子 run，不能复制父 run 的阶段产物、许可或聊天记录。
- `/switch` 只切换展示绑定，严格验证运行编号，并在源与目标 run 分别写入事件。
- 待确认方案必须绑定 run、动作标识和配置指纹；未确认不得改写配置或启动进程。

## 审计文件

| 文件 | 作用 |
|---|---|
| `run_manifest.json` | 公开 registry 与私有 provenance、topology、simulation、protocol sections。 |
| `status.json` | 当前公开状态快照。 |
| `events.jsonl` | 公开状态和控制摘要。 |
| `logs/structured.jsonl` | 私有、脱敏的步骤和进程事实。 |
| `process_lifecycle.jsonl` | 私有进程生命周期记录。 |
| `run_assistant_history.json` | 当前 run 的有界聊天与展示记录。 |

`GET /api/runs/{run_id}/logs` 只接受已登记的 `run_id`，固定返回上述 manifest 公共投影和 `events.jsonl` 内容。任一记录最多返回 1 MiB，并明确标记截断；事件流截断时保留最后部分。接口不接受任意文件名或路径。

写入使用 run 级锁和原子更新。公开接口不扫描其他 run，也不从文件时间戳推断科学阶段成功。
