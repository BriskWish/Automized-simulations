# 模拟层设计

维护范围：`src/willy/simulation/`、`agent_simulation.py`、`toolist_simulation.py`。

模拟层在单个 run workspace 中执行 GROMACS `EM -> NPT EQ -> PROD`。它消费拓扑层交接的 `topol.top`、`.itp` 和坐标文件，不改写量子产物或拓扑参数。GROMACS 仅由 `WILLY_GMX_BIN` 显式指定（进程环境优先，其次项目 `.env`）；本版本不从 `PATH`、系统扫描或 GROMACS 原生环境变量发现。编排器会经 `env_checker.ensure()` 预检，实际子进程复用相同解析结果。

本版本的模拟层验收标准是：四条已归档历史 profile 与后续确定性回归组成验收并集，目标机任选一条受支持 profile 严格完成 Step 1--10、所有阶段正常退出、最终状态为 `done`，且 manifest/status 与阶段产物契约通过。后处理/分析实现仅作内部实验保留，不属于本版本公开可用范围；不依据短链路推断科学体系性质。

## 协议与配置

MD 配置为 `md.schema_version: 2`。`md.eq` 保存三点式退火和六段具名时长，默认温度为 500 / 400 / 298 K，默认时长为升温、500 K 保温、降至 400 K、400 K 保温、降至 298 K、298 K 保温各 `[2, 1, 2, 1, 2, 2]` ns，总长 10 ns；EQ 默认 `tau_p = 2 ps`。每段必须为正，EQ 总长必须在 7--100 ns；最终 298 K 保温短于验收窗口时会警告并禁止自动判定已平衡。

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

受控验收任务可显式设置 `execution.stop_after_stage: "eq"`。该字段仅接受 `"eq"`
或省略/`null`，默认不设置，因此常规任务仍严格执行 `EM -> EQ -> PROD`。声明该范围的
run 仍需通过 Step 9 的完整 EQ 产物与验收门禁；成功后不创建、不启动或声明完成 Step 10，
而是在公开状态的 `extra.completion_scope` 写入 `{mode: "through_eq", step: 9, stage: "eq"}`
并以 `done_steps=1..9` 结束。它只适用于真实链路验收或受控测试，不是跳过 PROD 的通用用户操作。

## 运行链路与文件契约

| 主流程步骤 | 模块 | 输入 | 成功时必需产物 |
|---|---|---|---|
| 6 | `mdp.py` | v2 `config.json` | `em.mdp`、`eq.mdp`、`prod.mdp`、`run_manifest.json.protocol.mdp` |
| 7 | `box.py` | `residues`、`topol.top`、`.itp`、坐标、盒子参数 | `model.inp`、`model.pdb`、`run_manifest.json.simulation.box_attempts[]` 的真实几何审计，以及 `box_executions[]` 的私有执行证据 |
| 8 | `em.py` | `em.mdp`、`model.pdb`、`topol.top`、`.itp` | `em.tpr`、`em.gro`、`em.xtc`、`em.edr` |
| 9 | `eq.py` | `eq.mdp`、已验收 `em.gro`、`topol.top`、`.itp` | `eq.tpr`、`eq.gro`、`eq.xtc`、`eq.edr`、`eq.cpt` |
| 10 | `prod.py` | `prod.mdp`、已验收 `eq.gro` / `eq.cpt`、`topol.top`、`.itp` | `prod.tpr`、`prod.gro`、`prod.xtc`、`prod.edr`、`prod.cpt` |
| 主流程外（内部实验） | `postprocess.py` | 已完成 PROD 的私有 simulation section 与匹配指纹的 `prod.tpr/.xtc/.edr/.mdp` | `analysis/<id>/analysis_manifest.json`、PBC 处理轨迹、热力学曲线、MSD；不作为本版本公开产物 |

`.tpr` 是阶段临时输入；`.gro`、`.xtc`、`.edr` 是不可关闭的成功契约。`.log` 和 checkpoint 也会登记。用户仅能在模拟前、明确确认后经 `tools_configure_outputs_simulation` 开启可选全精度 `.trr`。`tools_configure_prod_simulation` 可在生成 `prod.tpr` 前重写当前 run 的 `prod.mdp`。

EM、EQ、PROD 通过阶段主契约并由编排器标记完成后，会在后台以 `gmx editconf` 将对应 `.gro` 原子化生成 `visualization/em.pdb`、`visualization/eq.pdb`、`visualization/prod.pdb`，供前端 3D 查看器读取。它们是派生产物，不写入阶段验收契约、不阻塞下游步骤；转换失败仅使该阶段的可视化不可用。每次阶段重跑会先清除旧 PDB，源 `.gro` 指纹或阶段令牌变化时后台任务不得发布过期结构。

每个 `mdrun` 固定携带 `-v`，并从 run-local `config.json.defaults.nproc` 读取总线程数（默认 8），生成 `-nt N`；调用方若显式传入 `-nt/-ntmpi/-ntomp`，则保留该受控覆盖。执行器同时消费标准输出与标准错误。`mdrun_eta.json` 的 `observed_at` 是受管进程的当前观测心跳，`last_progress_at` / `last_progress_step` 来自本阶段 `.log/.edr/.xtc/.cpt` 的最新写入，证明阶段仍在推进；这些字段绝不能被用于按速率推算结束时间。只有 `-v` 明确输出剩余时长或 GROMACS 2025 的 `will finish Tue Aug  4 01:00:54 2026` 绝对预测时，执行器才写入 `remaining_seconds`、`eta_observed_at` 和换算后的 `estimated_end_at`；后者按 GROMACS 所在主机本地时区解析后统一存为 UTC。解析兼容秒数、紧凑单位、`HH:MM:SS` 和该 `ctime` 格式，无法识别时保持 `waiting`。所有写入均为原子替换，运行期每 15 秒刷新一次心跳；状态机同步 `status.json.updated_at`，但心跳不追加审计事件。查询入口只读取既有快照，不触发心跳或按步数计算 ETA。该文件不保存命令、路径、原始输出或底层警告；ETA 表示 GROMACS 的运行时预测，而非保证的完成时间。进程结束后，快照仅保留阶段已结束或 ETA 不可用的事实。

每次新 run 固化 `config.json` 快照、MDP、随机种子、工具版本、资源信息和输入文件指纹至 `run_manifest.json` 的私有 simulation/protocol sections。生成的步数、实际时长、退火节点、协议控制项和 MDP warning 位于 `sections.protocol.data.mdp`；正常 pipeline 不再创建独立 `mdp_metadata.json`。仅脱离 pipeline、尚未初始化 run metadata 的独立 MDP 生成保留该旧 sidecar，读取端先读统一记录、再兼容旧文件。历史 run 不跟随项目根配置变化。其 `em_accepted`、`eq_accepted` 与 `prod_completed` 是唯一阶段许可：文件存在不足以跨阶段执行。运行期间的结构化执行事件追加至 `logs/structured.jsonl`，只记录受控步骤/进程事实和低频心跳，不改变阶段许可。

运行身份与可复现性由一个分段的 `run_manifest.json` 承担：公开 registry section 记录 run 身份与公开产物索引；私有 provenance section 记录 Git 提交/dirty 标记、运行时版本、配置修订哈希、输入指纹、随机种子、脱敏工具能力和 LLM 模型/prompt 版本；私有 simulation/protocol sections 记录阶段许可、checkpoint 与 MDP 协议元数据；topology section 记录组件计划。每 section 有 revision，写入以 section CAS 拒绝陈旧更新。新 run 不再写与 provenance capabilities 重复的 `environment_report.json`，`RunRegistry` 仅为历史 run 保留读取回退。统一记录不保存 API Key、环境变量原值、绝对路径、原始 prompt 或原始工具输出。`RunStore` 使用单一 run 锁包住 metadata 的读改写操作；`events.jsonl`、`decision_trace.jsonl` 和 `process_lifecycle.jsonl` 使用同一 run 级单调 `sequence`，并在追加后 `fsync`。`status.json` 与状态事件、步骤事件和 registry section 使用私有 `.run-transaction.json` 写前日志：先固化目标 JSON/JSONL 意图，崩溃后在下一次同 run 写入或注册时幂等补写，JSONL 通过事务标识避免重复追加。该机制不覆盖索引刷新、外部进程和科学产物，因此它是可恢复文件事务，不是跨系统数据库事务。

所有主流程步骤由 `src/willy/step_registry.py` 的 `STEP_REGISTRY` 唯一定义编号、层级、公开标签、模拟阶段、产物契约和受控重跑许可。执行器、恢复策略、待确认动作、运行审计和前端停止判断只能查询该注册表；Step 9 的受控重跑只允许回到 Step 9 或因建盒参数变化回到 Step 7。增删步骤时先改注册表，再同步文档表格与测试，禁止在调用方复制数字映射。

修复工具的成功仅表示该工具实际执行的步骤成功，`LayerAgent` 必须保留其原始 `step_name` 与 `step_index`。编排器根据该真实身份决定恢复路径：MDP 变更按 manifest 中失效的最早阶段回放；建盒后从 EM 回放；EM 修复后重跑 EQ；EQ 只有同时具备 `accepted` 许可和非空 `eq.tpr/.gro/.xtc/.edr/.cpt` 时才能标记 Step 9 完成。PROD 同样需要 `completed` 许可和完整阶段产物。任何修复工具结果都不能直接把其他失败步骤标记为完成。

EQ 失败的诊断允许返回一个或多个候选修复方案。仅在公开证据支持多个可能原因时，Simulation Agent 才返回最多 3 个彼此独立的选项；每项都必须携带脱敏的可能原因、证据摘要、受限参数差异和重跑起点，并且都以同一冻结 `config.json` 为基线独立校验，禁止把方案一的改动累积到方案二。多方案动作在用户选择前没有 `selected_option_id`，不能通过启动校验或改写配置；只有“方案1/方案一/选择方案1”等文本选定后，才允许“确认方案1/确认方案一”或后续明确确认进入 `retrying`。单方案保留原有明确确认路径。选择、确认、替代方案和实际应用都必须重新校验 `action_id`、状态版本和配置指纹。

替代 EQ 方案只接受受控字段。为避免自然语言与内部 schema 的机械差异，校验器仅在该 EQ 动作边界归一少量无歧义别名：`tau_p`、`taup`、`md.eq.tau_p` 和压强耦合中文名称映射为 `eq_tau_p`；`hold_target`、`hold_time`、最终保温段中文名称映射为 `eq_segment.hold_target`；数值字符串可带 `ps`、`ns` 或 `g/cm3` 单位。该映射不会接受任意 dotted path，也不会触及 PROD 参数。未知字段、非数值、越界值、无实际变化、无有效调整项和配置指纹/状态冲突都会拒绝替代方案，原动作与 `awaiting_confirmation` 保持不变。前端只显示脱敏拒绝原因；服务端另在 `decision_trace.jsonl` 记录 `rejected_validation` 和同一公开原因，不保存模型原文。

EQ proposal 的 GROMACS 知识检索固定使用 [`knowledge_mdrun.md`](knowledge_mdrun.md) 的受控条目，不允许在运行中联网或读取任意文档。提示词只获得 `ErrorKind`、阶段、脱敏验收/执行证据、冻结配置和明确标为未验证的 Agent 假设，以及 `number + name` 条目索引；模型不得把预设原因当作知识库结论。只读 `tools_lookup_mdrun_knowledge` 要求数字与名称同时匹配，单次最多读取 3 条、每个实际失败诊断周期最多调用 2 次；第二次真实失败才重新计数。候选方案只能把实际读取且服务端验证的条目写入 `knowledge_entries`。没有命中、知识源不可用或模型未检索时，必须以 `advice_source=llm_unverified` 和对应状态公开“LLM 未经知识库验证的推断”；命中条目也必须公开 GROMACS 版本兼容提醒。该元数据不参与配置写入或阶段许可，仍须经现有方案选择与用户确认门禁。

同一 run 内的 metadata 按 section 所有者严格区分：`RunRegistry` 仅管理公开 registry section；模拟层仅管理私有 simulation/protocol sections；拓扑层仅管理私有 topology section；provenance writer 仅管理 provenance section。调用方不得以一个 section 推导或改写另一个 section 的事实。历史拆分 manifest 仅为兼容读取，终态迁移默认非破坏性并保留旧文件。

```text
配置快照 + 输入指纹 -> MDP -> Packmol -> EM accepted -> EQ accepted -> PROD completed
                                   |              |                    |
                                   v              v                    v
                              Packmol 回滚     Packmol 回滚     指纹一致才可恢复
```

PROD 首次由 `eq.cpt` 连续启动。PROD 的恢复仅在 TPR、MDP、输入和父 checkpoint 指纹均一致时使用 `-cpi -append`；协议改动会新建阶段尝试并清理受影响产物，禁止混写不同协议的轨迹。

## 前置校验、验收与回滚

- 建盒前检查 `residues`、`topol.top [ molecules ]`、`.itp` 与坐标文件的一致性；非中性体系必须由用户确认或提供补偿离子方案。每个组分的质量从当前 run 的 `<residue>.itp` `[ atoms ]` 第 8 列求和，缺失、空段或非法质量阻止 Packmol 启动。
- 新任务的 `box.target_mass_density_g_cm3` 默认 `0.7`，并提示“初始体积将由使用默认0.7g/cm3的密度猜测”。当未指定 `box.box_size` 时，Step 7 以 `V_nm3 = M_amu * 1.66053906660e-3 / rho_g_cm3`、`L_A = 10 * cbrt(V_nm3)` 计算立方盒边长；不向上取整。显式 `box_size` 优先。历史 `packing_number_density_nm3` 仅为既有快照兼容，只有未设置目标质量密度时才使用。
- `model.inp` 始终写入与 `inside cube` 同一边长的 Packmol `pbc L L L`，不再写 `add_box_sides`。每次 Packmol 调用前会移除当前 run 内残留的 `model.pdb`，因此旧产物不能证明本次成功。成功后必须校验 `model.pdb` 的 `CRYST1` 长度和角度与请求周期盒一致，并记录请求/实际盒矢量、角度、体积、拓扑总质量、目标和实际初始质量密度至私有 simulation section 的 `box_attempts[]`。每次前置校验或进程调用另写入 `box_executions[]`：只含失败阶段、返回码、输入/输出指纹、原子数和周期盒事实，不含命令行或原始日志。Packmol 在模块预检和真实调用前均运行受控最小启动探针；动态加载器或 ABI 失败归类为 `runtime_unavailable`，不进入 `tools_retry_box`、密度或容差重试。公共状态只给出兼容性处理建议，私有证据保留受限失败类别和返回码。自动 `tools_retry_box` 还受服务端证据门控：在 Step 7 中只有原子数、周期盒解析或周期盒不一致证据可提出重建，模型不得以普通前置/进程失败泛化重建盒子。缺少或不一致的 `CRYST1` 以输入契约错误停止；不得用坐标极值推断盒子。
- EM 解析收敛日志。未收敛时回滚至 Packmol；每次回滚必须记录并实际改变建盒参数。
- `grompp` 默认拒绝 warning。唯一的受控例外是 GROMACS Ewald 净电荷 warning：执行器解析完整输出中的体系总电荷，且仅在只有这一条 warning、`abs(total_charge) <= 0.15e` 时内部重试一次 `-maxwarn 1`。该容差用于覆盖 LigParGen 四位小数电荷累计舍入，不做电荷归一化；超过阈值、缺少可解析电荷或存在其他 warning 均保持输入契约失败，并在私有步骤证据记录策略名称、数值和阈值。
- EQ 只分析最终目标温度保持段。阻塞验收仅包括：温度均值距目标温度不超过 5 K，以及势能的最小二乘归一化线性斜率不超过 `md.eq.acceptance.max_potential_relative_slope_per_ns`（默认 `0.01`，即 1%/ns）。压力、密度和宏观真空区仍提取并写入私有阶段证据，供诊断与人工复核，但不阻塞 PROD；不再使用密度/压力的分块趋势、z-score 或真空区作为 EQ 通过条件。未验收不得启动 PROD。
- PROD 验收检查正常结束、实际终止时间、轨迹帧数、EDR/checkpoint 完整性和所有强制产物非零。

错误对外归类为输入契约、引擎失败、数值不稳定、平衡未通过和恢复冲突。run 内保留原始证据，状态机只公开阶段、退火区段、已完成时间、预计产物和简化错误。执行前估算磁盘空间并记录 CPU/GPU；运行锁防止并发写入。受管 GROMACS 子进程以独立进程组启动：用户停止或超时时先发送 `SIGINT`，等待 30 秒后发送 `SIGTERM`，再等待 10 秒后发送 `SIGKILL`。停止原因、已发信号、退出码与 checkpoint 是否存在仅写入私有 `process_lifecycle.jsonl`。受控中止返回的非零退出码必须由编排器转为 `aborted`，不得触发 Simulation Agent 自动修复。

若历史 `status.json` 已错误越过一个仍为 `running` 且具备 ETA 心跳或近期产物写入证据的私有 MD 阶段，`RunRegistry` 仅撤销该阶段及下游的公开完成标记，恢复公开活动到真实阶段，并记录 `stage_status_reconciled`。该受限对账不触碰 simulation section、GROMACS 产物、锁或进程；它不是续跑、清理或科学验收操作。

## 工具

执行：`tools_run_em_simulation`、`tools_run_eq_simulation`、`tools_run_prod_simulation`。

配置与修复：`tools_retry_mdp`、`tools_retry_box`、`tools_retry_em`、`tools_retry_eq`、`tools_retry_prod`、`tools_configure_outputs_simulation`、`tools_configure_prod_simulation`、`tools_diagnose_error_simulation`、`tools_modify_config_simulation`、`tools_migrate_md_config_simulation`。只读诊断参考：`tools_lookup_mdrun_knowledge`，仅供 EQ proposal 在其两轮、三条/轮的预算内查询。模拟层不支持“跳过分子”，因为安全排除必须同时重建组分、拓扑和 Packmol 盒子。

执行工具支持 `top_path`、`itp_paths`、`mdp_path`、`pdb_path`（或 `structure_path`）与临时 `tpr_path`，并拒绝当前 run workspace 外的路径。涉及温度、时长、压力耦合、输出精度和协议变更的工具调用必须经过服务端的用户确认授权：LLM 工具参数不包含、也不能以 `confirmed=true` 伪造授权。未获授权的请求会停止自动修复并以 `user_confirmation_required` 升级；当前 run 的配置和阶段产物不会被改写，用户审阅新方案后应启动新的 run。

## PROD 后处理

后处理是生产模拟完成后的确定性分析，不属于主流程 Step 1-10，也不由 LLM 决定分析结果。输入限定为同一次 run 中非空的 `prod.tpr`、`prod.xtc`、`prod.edr`、`prod.mdp`；源文件不可改写。结果写入 `md_run/<run_id>/analysis/<analysis_id>/`，已有 `analysis_manifest.json` 时必须使用新的 `analysis_id`。

标准顺序为：检查轨迹完整性；生成分子完整、居中和 no-jump 临时轨迹；按显式 `discard_time_ps` 或默认末帧时间的 20% 剔除平衡段；提取温度、压力、密度和势能统计；在 no-jump 轨迹上计算 MSD。成功结果必须包含 `analysis_manifest.json`、处理后的 XTC、`thermo_*.xvg` 和 `msd_system.xvg`。缺帧、剔除区间覆盖整段轨迹或输入来自其他 run 时拒绝分析。

首版默认只分析 `System` 组，不自动猜测离子、溶剂或配位原子组；组分特异性 RDF、配位数和 MSD 后续以受控 index group 扩展。运行助理可以读取 manifest 并解释结果，但后处理不得改写 MD 配置、原始轨迹或拓扑。

## 验证

```bash
pytest -q tests/test_simulation_protocol.py tests/test_simulation_execution.py \
  tests/test_toolist_simulation.py tests/test_pipeline_orchestrator.py \
  tests/test_frontend_api.py
```

单元和模拟执行测试覆盖协议边界、配置迁移、阶段越权、checkpoint 连续性、配置变更禁止 append、质量密度公式、显式 PBC/`CRYST1` 审计、真实密度回滚参数、残留 Packmol 输出隔离、前置/进程/PBC 结构化执行证据和空产物拒绝，以及进程组停止、运行 provenance、RunStore 并发序列化/中断重放、StepRegistry、配置外层 schema、执行模块依赖归属和 `0.15e` 净电荷容差边界。2026-08-01 已在 GROMACS 2025.0 和 Packmol 21.2.3 上完成工作区内 32 原子中性 LJ 参考体系的十步冒烟。2026-08-11 批次 `acceptance_batch_20260811174708.json` 又以 `EC 200 / FEC 200 / EMC 200`、7 ns EQ、2 ns PROD 串行完成 G16+LigParGen/BOSS 与 ORCA+LigParGen/BOSS；四条主 profile 的核心 JSON 已归档为验收并集。本版本不据此声明科学体系预测、后处理/分析或离子 OPLS 能力。
