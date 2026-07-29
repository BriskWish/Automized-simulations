# 流水线状态接口文档

前端通过读取 `status.json` 获取流水线实时状态。

## 读取方式

```python
from willy.pipeline_state import PipelineStateMachine
status = PipelineStateMachine.read()  # → PipelineStatus
```

或直接读文件：

```python
import json
status = json.load(open("status.json"))
```

文件路径：项目根目录 `status.json`（原子写入，不会读到半截数据）。

## 轮询频率

推荐 `gr.Timer(3)`（3 秒），流水线内部更新频率远低于 3 秒。

## PipelineStatus 字段

```json
{
  "state":         "running",        // 见下方状态表
  "step":          3,                // 当前步骤 index (1-7)，idle 时为 0
  "step_label":    "RESP 电荷",      // 当前步骤描述
  "layer":         "quantum",        // 当前层: "config"|"quantum"|"topology"|"simulation"
  "error":         "TFSI: SCF不收敛", // 最近错误消息（空串=无错误）
  "error_kind":    "scf_not_converged", // ErrorKind 枚举值
  "agent":         "quantum",        // Agent 名（仅 retrying 时有值）
  "retry_n":       2,                // 当前重试次数（0=未在重试）
  "retry_max":     5,                // 最大重试次数
  "actions":       ["换基组 6-31g(d)","加 scf=xqc"],  // Agent 动作记录
  "escalation":    {...},            // escalation 详情（仅 escalated 时有值）
  "started_at":    "2026-07-28T12:00:00Z",  // 启动时间
  "updated_at":    "2026-07-28T12:05:30Z",  // 最后更新时间
  "total_steps":   7,                // 总步骤数
  "done_steps":    [1, 2]            // 已完成的步骤 index
}
```

## state 枚举

| state | 含义 | 前端展示 |
|-------|------|----------|
| `idle` | 无运行中的流水线 | 隐藏进度面板 |
| `running` | 正常执行中 | 显示当前步骤 + ⏳ |
| `retrying` | Agent 正在修复 | 显示 🔄 + Agent 名 + 动作列表 |
| `escalated` | Agent 放弃，需人工 | 显示 🆘 + escalation 详情 |
| `done` | 全流程完成 | 显示 ✅ 全部完成 |
| `aborted` | 已中止 | 显示 ⏹ 已中止 |

## 步骤 index 映射

| index | step_label | layer |
|:---:|------|------|
| 1 | g16/ORCA 结构优化 | quantum |
| 2 | fchk/molden→mol2 | quantum |
| 3 | RESP 电荷 | quantum |
| 4 | mol2+chg→itp+gro | topology |
| 5 | 主拓扑 + 修订 itp | topology |
| 6 | 生成 mdp | simulation |
| 7 | Packmol 盒子 | simulation |

## escalation 对象（仅 state=escalated）

```json
{
  "layer":         "quantum",
  "step":          "struct_maker",
  "error_kind":    "scf_not_converged",
  "attempts_made": 3,
  "actions_tried": [ "换基组为 6-31g(d)", "加了 scf=xqc", "降 nproc 到 4" ],
  "last_raw_output": "...",
  "recommendation": "需要人工检查初始几何",
  "backup_plan":    "用 ORCA 后端 + def2-SVP 基组尝试"
}
```

## 前端渲染伪代码

```python
def render_progress(status):
    if status.state == "idle":
        return None  # 不显示

    for i in range(1, 8):
        if i in status.done_steps:
            icon = "✅"
        elif i == status.step:
            icon = "🔄" if status.state == "retrying" else "⏳"
        else:
            icon = "⬚"

    if status.state == "retrying":
        show(f"🤖 {status.agent} Agent 修复中 ({status.retry_n}/{status.retry_max})")
        for action in status.actions:
            show(f"  · {action}")

    if status.state == "escalated":
        show("🆘 自动修复失败")
        show(f"建议: {status.escalation['recommendation']}")
```

## 状态流转图

```
IDLE ──→ RUNNING ──→ RETRYING ──→ RUNNING ──→ ... ──→ DONE
              │          │              ↑
              │          └──→ ESCALATED ─┘ (Agent 放弃)
              │
              └──→ ABORTED (用户中止 / 配置错误)
```
