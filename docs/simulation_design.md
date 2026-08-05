# 模拟层设计

维护范围：`src/willy/simulation/`、`agent_simulation.py`、`toolist_simulation.py`。

模拟层在单个 run workspace 中执行 GROMACS `EM -> NPT EQ -> PROD`。它消费拓扑层交接的 `topol.top`、`.itp` 和坐标文件，不改写量子产物或拓扑参数。GROMACS 可由 `WILLY_GMX_BIN` 显式指定，未设置时从启动 Willy 的 `PATH` 发现；编排器会经 `env_checker.ensure()` 预检，实际子进程复用相同解析结果。

## 协议与配置

MD 配置为 `md.schema_version: 2`。`md.eq` 保存三点式退火和六段具名时长，默认温度为 500 / 400 / 298 K，默认时长为升温、500 K 保温、降至 400 K、400 K 保温、降至 298 K、298 K 保温各 `[2, 1, 2, 1, 2, 2]` ns，总长 10 ns。每段必须为正，EQ 总长必须在 7--100 ns；最终 298 K 保温短于验收窗口时会警告并禁止自动判定已平衡。

`md.prod` 独立配置 2--200 ns 的生产时长，温度必须等于 EQ 目标温度。`mdp.py` 将 ns 换算为 `nsteps` 后记录实际取整时长。默认使用 V-rescale 热浴、C-rescale 压浴、各向同性压力耦合和 `EnerPres` 色散校正；界面、晶体及单轴体系必须显式选择相应压力耦合类型。

旧字段 `eq_ns`、`prod_ns`、`ref_t` 和 `box.density` 不会被运行时静默解释。默认 `config.json` 已直接使用 v2。使用显式迁移可先产生独立新文件和字段映射审计：

```bash
python -m willy.simulation.protocol config.json --output config.v2.json
```

迁移不覆盖源配置；若要采用结果，`tools_migrate_md_config_simulation` 必须获得服务端用户确认授权，`adopt=true` 仅表达请求，不能由 LLM 自行确认。它先在项目本地且被 Git 忽略的 `.willy/config-migrations/` 保存迁移前备份与字段映射审计，再原子替换活动配置；像旧 5 ns EQ 这类不合规时长会明确归一到 10 ns 默认协议并记录映射。根目录只保留唯一活动 `config.json`。

完整工作流配置另有一个保守的外层结构边界：`config_schema.py` 校验顶层对象及
`defaults`、`molecules`、`residues`、`md`、`topology`、`box` 等稳定 section 的对象形状，
并在写入 defaults 前拒绝错误嵌套。科学协议、范围和跨字段约束仍只由
`simulation.protocol` 的 MD v2 校验处理。未知扩展字段保留，schema 不会自动升级旧
`eq_ns`/`prod_ns` 或 `box.density`，因此历史配置只能经上述显式迁移流程改变。

## 运行链路与文件契约

| 主流程步骤 | 模块 | 输入 | 成功时必需产物 |
|---|---|---|---|
| 6 | `mdp.py` | v2 `config.json` | `em.mdp`、`eq.mdp`、`prod.mdp`、`mdp_metadata.json` |
| 7 | `box.py` | `residues`、`topol.top`、`.itp`、坐标、盒子参数 | `model.inp`、`model.pdb`、`md_manifest.json.box_attempts[]` 的真实盒审计 |
| 8 | `em.py` | `em.mdp`、`model.pdb`、`topol.top`、`.itp` | `em.tpr`、`em.gro`、`em.xtc`、`em.edr` |
| 9 | `eq.py` | `eq.mdp`、已验收 `em.gro`、`topol.top`、`.itp` | `eq.tpr`、`eq.gro`、`eq.xtc`、`eq.edr`、`eq.cpt` |
| 10 | `prod.py` | `prod.mdp`、已验收 `eq.gro` / `eq.cpt`、`topol.top`、`.itp` | `prod.tpr`、`prod.gro`、`prod.xtc`、`prod.edr`、`prod.cpt` |
| 主流程外 | `postprocess.py` | 已完成 PROD 的 `md_manifest.json` 与匹配指纹的 `prod.tpr/.xtc/.edr/.mdp` | `analysis/<id>/analysis_manifest.json`、PBC 处理轨迹、热力学曲线、MSD |

`.tpr` 是阶段临时输入；`.gro`、`.xtc`、`.edr` 是不可关闭的成功契约。`.log` 和 checkpoint 也会登记。用户仅能在模拟前、明确确认后经 `tools_configure_outputs_simulation` 开启可选全精度 `.trr`。`tools_configure_prod_simulation` 可在生成 `prod.tpr` 前重写当前 run 的 `prod.mdp`。

每个 `mdrun` 固定携带 `-v`，执行器同时消费标准输出与标准错误。`mdrun_eta.json` 的 `observed_at` 是受管进程的当前观测心跳，`last_progress_at` / `last_progress_step` 来自本阶段 `.log/.edr/.xtc/.cpt` 的最新写入，证明阶段仍在推进；这些字段绝不能被用于按速率推算结束时间。只有 `-v` 明确输出剩余时长或 GROMACS 2025 的 `will finish Tue Aug  4 01:00:54 2026` 绝对预测时，执行器才写入 `remaining_seconds`、`eta_observed_at` 和换算后的 `estimated_end_at`；后者按 GROMACS 所在主机本地时区解析后统一存为 UTC。解析兼容秒数、紧凑单位、`HH:MM:SS` 和该 `ctime` 格式，无法识别时保持 `waiting`。所有写入均为原子替换，运行期每 15 秒刷新一次心跳；状态机同步 `status.json.updated_at`，但心跳不追加审计事件。查询入口只读取既有快照，不触发心跳或按步数计算 ETA。该文件不保存命令、路径、原始输出或底层警告；ETA 表示 GROMACS 的运行时预测，而非保证的完成时间。进程结束后，快照仅保留阶段已结束或 ETA 不可用的事实。

每次 run 固化 `config.json` 快照、MDP、随机种子、工具版本、资源信息和输入文件指纹至 `md_manifest.json`。历史 run 不跟随项目根配置变化。其 `em_accepted`、`eq_accepted` 与 `prod_completed` 是唯一阶段许可：文件存在不足以跨阶段执行。

运行身份与可复现性由三个互补的 run-local 文件承担：`provenance.json` 记录 Git 提交/dirty 标记、运行时版本、配置修订哈希、输入指纹、随机种子、脱敏工具能力和 LLM 模型/prompt 版本；公共 `manifest.json` 记录 run 身份与公开产物索引；私有 `md_manifest.json` 记录阶段许可与 checkpoint。三者均不保存 API Key、环境变量原值、绝对路径、原始 prompt 或原始工具输出。`RunStore` 使用单一 run 锁包住 MD manifest 的读改写操作；`events.jsonl`、`decision_trace.jsonl` 和 `process_lifecycle.jsonl` 使用同一 run 级单调 `sequence`，并在追加后 `fsync`。`status.json` 与状态事件、环境报告与其事件、步骤事件与公共 manifest 使用私有 `.run-transaction.json` 写前日志：先固化目标 JSON/JSONL 意图，崩溃后在下一次同 run 写入或注册时幂等补写，JSONL 通过事务标识避免重复追加。该机制不覆盖索引刷新、外部进程和科学产物，因此它是可恢复文件事务，不是跨系统数据库事务。

所有主流程步骤由 `src/willy/step_registry.py` 的 `STEP_REGISTRY` 唯一定义编号、层级、公开标签、模拟阶段、产物契约和受控重跑许可。执行器、恢复策略、待确认动作、运行审计和前端停止判断只能查询该注册表；Step 9 的受控重跑只允许回到 Step 9 或因建盒参数变化回到 Step 7。增删步骤时先改注册表，再同步文档表格与测试，禁止在调用方复制数字映射。

修复工具的成功仅表示该工具实际执行的步骤成功，`LayerAgent` 必须保留其原始 `step_name` 与 `step_index`。编排器根据该真实身份决定恢复路径：MDP 变更按 manifest 中失效的最早阶段回放；建盒后从 EM 回放；EM 修复后重跑 EQ；EQ 只有同时具备 `accepted` 许可和非空 `eq.tpr/.gro/.xtc/.edr/.cpt` 时才能标记 Step 9 完成。PROD 同样需要 `completed` 许可和完整阶段产物。任何修复工具结果都不能直接把其他失败步骤标记为完成。

同一 run 内的 manifest 按所有者严格区分：`manifest.json` 由 `RunRegistry` 管理 run 身份、冻结输入、公开产物索引和状态路径；`md_manifest.json` 只由模拟层管理阶段指纹、许可、checkpoint 与恢复；`topology_manifest.json` 只由拓扑层管理 Step 4/5 的组件计划。调用方不得以其中一个文件推导或改写另一个文件的事实。

```text
配置快照 + 输入指纹 -> MDP -> Packmol -> EM accepted -> EQ accepted -> PROD completed
                                   |              |                    |
                                   v              v                    v
                              Packmol 回滚     Packmol 回滚     指纹一致才可恢复
```

PROD 首次由 `eq.cpt` 连续启动。PROD 的恢复仅在 TPR、MDP、输入和父 checkpoint 指纹均一致时使用 `-cpi -append`；协议改动会新建阶段尝试并清理受影响产物，禁止混写不同协议的轨迹。

## 前置校验、验收与回滚

- 建盒前检查 `residues`、`topol.top [ molecules ]`、`.itp` 与坐标文件的一致性；非中性体系必须由用户确认或提供补偿离子方案。每个组分的质量从当前 run 的 `<residue>.itp` `[ atoms ]` 第 8 列求和，缺失、空段或非法质量阻止 Packmol 启动。
- 新任务的 `box.target_mass_density_g_cm3` 默认 `1.5`，并提示“初始体积将由使用默认1.5g/cm3的密度猜测”。当未指定 `box.box_size` 时，Step 7 以 `V_nm3 = M_amu * 1.66053906660e-3 / rho_g_cm3`、`L_A = 10 * cbrt(V_nm3)` 计算立方盒边长；不向上取整。显式 `box_size` 优先。历史 `packing_number_density_nm3` 仅为既有快照兼容，只有未设置目标质量密度时才使用。
- `model.inp` 始终写入与 `inside cube` 同一边长的 Packmol `pbc L L L`，不再写 `add_box_sides`。Packmol 成功后必须校验 `model.pdb` 的 `CRYST1` 长度和角度与请求周期盒一致，并记录请求/实际盒矢量、角度、体积、拓扑总质量、目标和实际初始质量密度至 `md_manifest.json.box_attempts[]`。缺少或不一致的 `CRYST1` 以输入契约错误停止；不得用坐标极值推断盒子。
- EM 解析收敛日志。未收敛时回滚至 Packmol；每次回滚必须记录并实际改变建盒参数。
- EQ 只分析最终目标温度保持段的温度、压力、密度和势能，计算分块均值、趋势与不确定度；最终结构出现宏观真空区时回滚建盒。未验收不得启动 PROD。
- PROD 验收检查正常结束、实际终止时间、轨迹帧数、EDR/checkpoint 完整性和所有强制产物非零。

错误对外归类为输入契约、引擎失败、数值不稳定、平衡未通过和恢复冲突。run 内保留原始证据，状态机只公开阶段、退火区段、已完成时间、预计产物和简化错误。执行前估算磁盘空间并记录 CPU/GPU；运行锁防止并发写入。受管 GROMACS 子进程以独立进程组启动：用户停止或超时时先发送 `SIGINT`，等待 30 秒后发送 `SIGTERM`，再等待 10 秒后发送 `SIGKILL`。停止原因、已发信号、退出码与 checkpoint 是否存在仅写入私有 `process_lifecycle.jsonl`。受控中止返回的非零退出码必须由编排器转为 `aborted`，不得触发 Simulation Agent 自动修复。

若历史 `status.json` 已错误越过一个仍为 `running` 且具备 ETA 心跳或近期产物写入证据的私有 MD 阶段，`RunRegistry` 仅撤销该阶段及下游的公开完成标记，恢复公开活动到真实阶段，并记录 `stage_status_reconciled`。该受限对账不触碰 `md_manifest.json`、GROMACS 产物、锁或进程；它不是续跑、清理或科学验收操作。

## 工具

执行：`tools_run_em_simulation`、`tools_run_eq_simulation`、`tools_run_prod_simulation`。

配置与修复：`tools_retry_mdp`、`tools_retry_box`、`tools_retry_em`、`tools_retry_eq`、`tools_retry_prod`、`tools_configure_outputs_simulation`、`tools_configure_prod_simulation`、`tools_diagnose_error_simulation`、`tools_modify_config_simulation`、`tools_migrate_md_config_simulation`。模拟层不支持“跳过分子”，因为安全排除必须同时重建组分、拓扑和 Packmol 盒子。

执行工具支持 `top_path`、`itp_paths`、`mdp_path`、`pdb_path`（或 `structure_path`）与临时 `tpr_path`，并拒绝当前 run workspace 外的路径。涉及温度、时长、压力耦合、输出精度和协议变更的工具调用必须经过服务端的用户确认授权：LLM 工具参数不包含、也不能以 `confirmed=true` 伪造授权。未获授权的请求会停止自动修复并以 `user_confirmation_required` 升级；当前 run 的配置和阶段产物不会被改写，用户审阅新方案后应启动新的 run。

## 验证

```bash
pytest -q tests/test_simulation_protocol.py tests/test_simulation_execution.py \
  tests/test_toolist_simulation.py tests/test_pipeline_orchestrator.py \
  tests/test_frontend_api.py
```

单元和模拟执行测试覆盖协议边界、配置迁移、阶段越权、checkpoint 连续性、配置变更禁止 append、质量密度公式、显式 PBC/`CRYST1` 审计、真实密度回滚参数和空产物拒绝，以及进程组停止、运行 provenance、RunStore 并发序列化/中断重放、StepRegistry、配置外层 schema 与执行模块依赖归属。2026-08-01 已在 GROMACS 2025.0 和 Packmol 21.2.3 上完成工作区内 32 原子中性 LJ 高压气相参考体系的真实 `Packmol -> EM -> EQ -> PROD -> 后处理` 冒烟。external smoke 现有具名 case、version/文件集合/大小/SHA-256 校验的 fixture bundle、required 门禁和区分预检/真实执行的脱敏证据；当前仅 Sobtop EC 具备真实执行测试实现，尚无目标验收机的成功证据。四种真实量子/拓扑组合端到端验收仍待目标环境执行。这些证据都不替代真实目标体系的科学验收或 GROMACS 警告白名单建立。
