# Willy 架构

Willy 是本机部署的受限分子动力学工作流编排器。LLM 负责解析需求、提出候选和解释受控事实；确定性代码负责输入校验、状态、权限、文件、外部程序和科学计算。

## 主流程

```text
自然语言需求 -> temp__/plan__ 方案工作区 -> 待确认 config.json -> run 分配与预检
  -> 量子结构优化
  -> 单点与 MOL2
  -> RESP 电荷
  -> 分子拓扑
  -> 主拓扑
  -> MDP
  -> Packmol 建盒
  -> GROMACS EM -> EQ -> PROD
```

每个 run 使用独立 `md_run/<run_id>/` 工作区。`temp__` 和 `plan__` 是运行前的方案目录，不被登记为 run；只有确认后才随启动锁正式化为 `md__`。阶段只能消费已验证并登记的上游产物：EM、EQ、PROD 的许可分别由已验收的阶段结果控制，文件存在不构成跨阶段许可。

## 分层

| 层 | 组件 | 职责 |
|---|---|---|
| 前端 | `app.py`、`frontend/`（FastAPI + React/assistant-ui） | 方案确认、状态展示、受控停止、运行切换、可视化和当前工程审计日志。 |
| 配置 | `agent_config.py`、`toolist_global.py`、`structure_uploads.py` | 分子识别、上传规范化、输入审计、候选配置和启动前校验。 |
| 量子 | `quantum/` | G16、G09、ORCA 输入和量子产物。 |
| 拓扑 | `topology/` | Sobtop/GAFF-UFF 或 LigParGen/BOSS OPLS-AA 参数化与组装。 |
| 模拟 | `simulation/` | MDP、Packmol、GROMACS 和阶段验收。 |
| 运行 | `run_registry.py`、`run_store.py`、`pipeline_state.py` | run 隔离、公开状态、事务、审计和恢复约束。 |

Agent 只能调用所属层的白名单 function-calling 工具。运行助理只读；`/resume`、`/fork`、`/switch` 和停止由服务端确定性控制层处理。

## 运行事实

`run_manifest.json` 保存公开 registry 与私有 provenance、topology、simulation、protocol sections；正式化方案会在 registry 中留下不含参数值的 `proposal_workspace_id`。`status.json` 是当前公开状态快照；`events.jsonl` 提供脱敏事件；`run_assistant_history.json` 只保存所属 run 的有界运行助理对话；`proposal.json` 留存每个 `temp__`、`plan__` 与正式 run 的有界方案助理对话，计划才含候选配置。日志工作区和运行助理仅显示 manifest 的公开 registry 投影和 `events.jsonl`；私有 sections、原始日志、命令、路径、候选配置和密钥不得进入这些公开界面或报告。

## 产品边界

当前入口只支持本机执行和用户自配 OpenAI-compatible LLM。远程执行、托管网关、科学性质预测、G09 可靠全链路、离子 OPLS 和公开后处理能力不属于当前范围。
