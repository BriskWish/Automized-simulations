# 远程 GROMACS 执行设计

> 维护范围：`src/willy/remote_execution.py`、`src/willy/remote_registry.py`、`src/willy/simulation/` 的 GROMACS 适配、`frontend_api.py`、`app.py`、运行状态与远程执行测试。
>
> 状态：后续版本规划。本版本明确不加入 SSH、Slurm 或其他远程 GROMACS 执行；前端远程任务页保持“暂不支持”，全部输入冻结且不会将远程意图传入方案助理。本文件只保留未来版本的边界设计，不属于当前版本验收缺口或可用能力。
>
> 最后更新：2026-08-09。

## 1. 目标、边界与非目标

Willy 的量子、拓扑和 Packmol 仍在本地运行。远程模式只将 **MD 模拟层中的 GROMACS 调用**移交到受控的 SSH 目标：`gmx editconf`、`grompp`、`mdrun` 以及依赖 GROMACS 的后处理命令。远端不是新的科学工作流层，也不是 LLM 可任意操作的 shell。

| 范围 | 位置 | 说明 |
|---|---|---|
| Step 1--5 | 本地 | 量子、RESP、拓扑和组装不进入远程目标。 |
| Step 6 | 本地 | MDP 和冻结配置在本地生成。 |
| Step 7 | 本地 Packmol + 远程 `gmx editconf` | Packmol 保持项目内置；其输出作为远程 GROMACS 输入同步。 |
| Step 8--10 | 远程 | EM、EQ、PROD 的 `grompp`/`mdrun` 运行在选定 profile。 |
| 主流程外后处理 | 远程 | `check`、`trjconv`、`energy`、`msd` 与主阶段使用同一远端 GROMACS，避免版本漂移。 |

不在本阶段实现：远程量子/拓扑软件、LLM 任意 SSH tool、密码保存、静默接受 SSH host key、跨 profile 自动调度、自动删除远端 run、或绕开本地阶段验收的 scheduler 依赖链。

## 2. 权限模型

远程执行是确定性执行器能力，不新增 `remote_simulation Agent`。现有 Agent 的边界如下：

| 调用方 | 允许能力 | 禁止能力 |
|---|---|---|
| Config Agent | 读取可用 profile 的脱敏能力；在用户选择后写入 profile ID。 | 写 host、用户名、端口、密钥、远端路径、shell 片段、队列账户或 GROMACS 命令。 |
| Simulation Agent | 基于已标准化的阶段错误提出 MD 参数修复方案。 | 直接启动、停止、传输或修改远端资源。 |
| Run Assistant | 只读读取远端连接/队列/阶段/ETA/同步摘要。 | 任意 SSH、取消作业、上传文件、读取原始远端日志。 |
| Orchestrator / Remote executor | 经用户确认的流水线实际执行、同步和停止。 | 接收 LLM 提供的任意命令或路径。 |

远端错误必须转换为受限 `ErrorKind` 和脱敏事实，例如 `remote_connection_failed`、`remote_host_key_mismatch`、`remote_sync_failed`、`remote_disk_insufficient`、`remote_gromacs_unavailable`、`remote_job_cancelled` 和 `remote_job_timeout`。公开状态不得暴露主机名、远端绝对路径、命令行、SSH 错误原文或认证信息。

## 3. 配置与密钥边界

### 3.1 冻结的工作流配置

`config.json` 增加明确的 `execution` section。它仅记录可审计的执行选择：

```json
{
  "execution": {
    "md": {
      "backend": "local",
      "profile": null,
      "retain_remote_run": true
    }
  }
}
```

`backend` 仅允许 `local`、`ssh`、`slurm`；远程模式必须带非空的受注册表允许的 `profile`。该 section 与其 profile ID 随 run 快照固化；实际 host 和目录只能进入私有的运行记录，公共 manifest、状态、事件和 LLM 事实中只保留 profile ID、启动器种类、脱敏能力和阶段/尝试 ID。

### 3.2 本机私有 profile 注册表

配置档案位于 `~/.config/willy/remote_profiles.json`（可由 `WILLY_REMOTE_PROFILES_FILE` 覆盖），其父目录和文件必须分别是 `0700`、`0600`。不存在时，远程模式不可用但本地模式不受影响。

```json
{
  "schema_version": 1,
  "profiles": {
    "lab_gpu": {
      "ssh_host_alias": "lab-gpu",
      "remote_run_root": "/scratch/willy",
      "transfer": {"method": "rsync", "partial": true},
      "gromacs": {
        "command": "/opt/gromacs/2025.0/bin/gmx",
        "setup_script": "/opt/gromacs/2025.0/bin/GMXRC",
        "gpu_policy": "auto"
      },
      "launcher": {"kind": "direct"}
    }
  }
}
```

profile 使用 `~/.ssh/config` 中已经配置且受信任的 alias，并默认经 `ssh-agent` 认证。注册表不保存密码、API Key、私钥内容或可由 LLM 修改的任意 shell 片段。`ssh_host_alias`、远程目录和命令均视作私有诊断信息。

### 3.3 Slurm profile 扩展

`launcher.kind="slurm"` 增加只允许的结构化资源字段：

```json
{
  "launcher": {
    "kind": "slurm",
    "partition": "gpu",
    "account": "project_a",
    "qos": "normal",
    "gpus": 1,
    "cpus": 10,
    "memory_mb": 32000,
    "walltime": "24:00:00"
  }
}
```

profile 中的资源请求必须经本地 schema 校验并由用户在远程任务栏选择；LLM 不生成 `#SBATCH`、`sbatch` 参数或 shell 脚本。未来 PBS 等调度器使用独立 launcher 适配器，不能将命令字符串混入 Slurm profile。

## 4. 远程工作站执行链路

本地 run 目录始终是状态、配置、阶段验收和可复现审计的权威来源。远端工作目录按 `<remote_run_root>/<run_id>` 隔离，且每个阶段尝试拥有受控的 attempt ID。

1. 启动前解析 `execution.md` 并加载 profile；检查 SSH 非交互连接、严格 host key、远端目录可写、磁盘余量、`gmx --version`、GROMACS GPU 能力与 `rsync`。
2. 本地完成 MDP 和 Packmol；在 `gmx editconf` 前仅同步本阶段的声明输入、配置快照和必要拓扑文件。
3. 远端 wrapper 用固定参数执行 GROMACS，在独立进程组中保存 PID/PGID、阶段/attempt ID 和输出日志。
4. 本地按需拉取远端日志末尾并复用既有 ETA 解析；不以文件修改时间估算 ETA。断开查询连接不等同阶段失败。
5. 阶段退出后，下载声明的 `.tpr/.gro/.xtc/.edr/.log/.cpt`，用大小和哈希校验后才写入本地阶段 manifest 并验收。
6. 本地验收成功后才允许提交下一阶段。远端存在文件或 Slurm 已成功均不能绕过 `em_accepted`、`eq_accepted` 与 `prod_completed`。
7. 用户明确中止时，直连模式先向远端进程组发 `SIGINT` 以请求 checkpoint，再按现有宽限规则升级 `SIGTERM`/`SIGKILL`；停止结果写入本地生命周期审计。

默认不在运行期间同步大型 `.xtc/.trr`。阶段完成时完整同步必需产物；正在运行的 ETA 只读取受限日志尾部。远端保留策略由 `retain_remote_run` 明确指定，清理属于后续、经确认的治理动作。

## 5. 集群与 Slurm 方案

集群模式通过 SSH 登录节点提交任务，禁止在登录节点直接运行 `gmx mdrun`。每个 EM、EQ、PROD 阶段是一个独立的 scheduler 作业：

```text
本地编排器 -> SSH 预检/同步 -> sbatch stage wrapper
             <- job_id / squeue / sacct
本地阶段验收 <- 同步输出 <- 计算节点共享文件系统
```

作业状态映射：`PENDING -> queued`、`RUNNING -> running`、`COMPLETED -> 阶段产物同步和验收`、`CANCELLED -> aborted`、`TIMEOUT/FAILED -> 标准化失败`。排队时间不能伪装为 GROMACS ETA；仅在作业进入运行并有 `mdrun -v` 输出时展示步骤 ETA。

不使用预先串联的 `afterok` 自动提交整个 EM/EQ/PROD 链，因为 EQ 的科学验收和待确认修复必须阻塞 PROD。作业成功后由本地编排器下载产物、执行门禁，再提交下一阶段。取消使用 `scancel <job_id>`；在运行作业上先请求可写 checkpoint 的优雅停止，具体信号策略由受控 wrapper 固化。

集群 profile 必须额外验证共享远端根目录、用户配额、`sbatch/squeue/sacct/scancel` 可用性、计算节点可见的 GROMACS 环境和 GPU request；`/scratch` 只可在同步完成、本地验收并经保留策略允许后清理。

## 6. 前端与 LLM 上下文

“本地任务”是当前版本唯一可提交的入口；其方案助理固定写入 `execution.md.backend=local`。相邻的“远程任务”页保留为功能预告，但 SSH/Slurm 模式和 profile 输入均为禁用控件，显示“本版本暂不支持远程执行”，不会读取 profile、连接远端、提交任务，也不会向方案助理传递远程选择。它不显示或回填密码、私钥、绝对远端路径、完整 host、命令或原始日志。

远程模式的配置方案确认必须展示“本地前序步骤 + 远程 MD 层”的边界和所选 profile 名称。Config Agent 注入的事实仅为 `remote_mode`、profile ID、启动器类型和脱敏预检结果；Simulation Agent 只接收已归类的阶段错误；Run Assistant 的新增只读事实是远端连接、同步、scheduler/job 状态和远端 GROMACS ETA。

远程选择在前端重新开放前不可生成方案或冻结配置；本版本不存在从远程意图静默回退为本机执行的路径。后续重新开放时，仍须保持“远程执行器未接入则拒绝确认且不启动任何进程”的门禁。

## 7. 实施计划与验收

### Phase 1：数据契约与 profile 预检

- 新增 `remote_registry.py`，严格解析私有 profile、权限、SSH alias、直连/Slurm schema 和脱敏 capability。
- 扩展 `config_schema.py` / `workflow_config.py`，明确 `execution.md`，默认 `local`，并冻结到 run 配置。
- 增加 `remote_execution.py` 的不可变 command/transfer/状态数据模型、预检接口和标准化错误。
- 增加 remote profile 的前端只读 API；profile 选择和向 Config Agent 传递受限上下文延后至远程执行器接入时恢复。当前 UI 固定本地执行，并明确展示远程不可用。

验收：无 profile 不影响现有本地 run；非法 profile、宽松权限、未知 profile、非受信任 host policy、非结构化 Slurm 资源、生成方案后切换 profile 均被拒绝且不启动子进程；Phase 2 前的远程确认不可静默回退本机；所有公开输出无密钥、host、路径和命令。

### Phase 2：SSH 直连执行器

- 从 `_gmx_utils.py` 抽象本地/SSH GROMACS 执行路径，保持 `run_gmx()` 和阶段产物契约向调用方兼容。
- 以固定 argv 和远端 wrapper 完成同步、启动、查询 ETA、下载、哈希校验、中止和断线重连。
- 扩展 `md_manifest.json` 私有执行记录与 `process_lifecycle.jsonl`，公共状态仅展示操作级事实。
- 使 `box.py`、`postprocess.py`、`visualization.py` 经同一执行器调用远端 GROMACS。

验收：fixture SSH transport 覆盖输入上传、阶段输出回传、哈希不一致、远端断连、进程存活、SIGINT checkpoint、停止升级和本地阶段门禁；真实工作站 external smoke 跑通至少一个小体系 EM/EQ/PROD。

### Phase 3：恢复远程任务栏与运行助理

- 在 SSH 执行器验收后，解除“远程任务”页的冻结，新增模式/profile 选择、连接测试和脱敏能力卡。
- 运行助理新增远端只读状态/ETA/sync 事实；快速路径不调用 LLM。
- 所有远程任务卡、状态卡和错误卡独立成气泡，避免与欢迎语或旧 run 信息混合。

验收：切换 run 或远程 profile 不串状态；LLM 上下文明确边界且没有 SSH 细节；文本“中止”仍无控制权限，只有现有确定性中止流程可以停止远端任务。

### Phase 4：Slurm launcher

- 以独立 `SlurmLauncher` 实现 `sbatch/squeue/sacct/scancel`，生成受模板与 schema 限定的 job script。
- 映射 scheduler 状态、作业 ID、队列与运行 ETA，并保留本地阶段验收后的串行提交。
- 支持 scheduler 断线后的重新查询与受控停止。

验收：fake Slurm 覆盖 queued/running/completed/failed/cancelled/timeout、重复轮询、重复取消和恢复；目标集群 external smoke 留存作业与阶段产物证据。

## 8. 测试与发布门禁

新增单元/契约测试至少覆盖 profile schema、文件权限、脱敏、命令 argv、同步清单、哈希、错误映射、远程状态转换、SSH 停止和 Slurm 映射。所有测试默认使用 fake transport，不访问真实网络。真实 SSH/Slurm 测试必须标记 `external`，由显式 profile 和用户授权触发。

SSH/Slurm 不属于当前版本发布门禁。未来版本若重新启用该方向，必须完成本文件定义的 external smoke、远程中止 checkpoint 和端到端 EM/EQ/PROD 证据后，才可单独声明远程能力可用。
