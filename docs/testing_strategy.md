# Willy 测试策略与质量门禁

> 维护角色：6 号测试工程师
> 最后更新：2026-08-11
> 当前基线：2026-08-11 完整 `pytest -q` 为 806 passed、9 skipped（收集 815 条 pytest 用例；测试台账另含 18 条离线 LLM 场景，共 833 条记录）。本轮新增托管网关的服务端名额自助申请、前 50 名自动批准、默认 grant、重复公钥去重、待审批过期释放、来源节流、固定上游、设备签名/nonce、撤销、过期/篡改令牌、白名单、额度账本、超时预留、回环管理员控制面、客户端私有身份文件、令牌刷新、设备状态/剩余 Token 查询和客户端到实际 ASGI 网关的协议契约；同时覆盖 schema-v2 `run_manifest.json` 的 section CAS、新旧格式读取、终态非破坏性迁移、环境能力和 MDP 协议 metadata 收敛、当前版本远程任务页的本地固定/输入冻结边界，以及可视化元素图例、PDB 原子前缀解析、氢原子保留和图例/画面统一色表。模拟验收同时覆盖温度均值、势能归一化线性斜率、真空区/密度非阻塞行为、净电荷容差边界，以及 Packmol 残留输出隔离、前置/进程结构化证据和证据门控，并新增 OPLS 专属的 LigParGen GRO 五列残基字段恢复和结构化日志回归；外部工具和真实 LLM 网络测试仍为显式 opt-in，默认跳过不代表相应的真实十步 profile 已被默认测试替代。

## 1. 目标与原则

测试验证已经观察到的跨层契约，而不是源码实现细节。默认回归必须快速、确定、隔离，不得意外启动高成本计算；真实工具 smoke 与 LLM 在线评测必须显式启用并保留验收证据。模拟 Agent 的协议调整测试还必须证明模型即使提交 `confirmed=true` 也不能改写配置，并在首次工具调用后升级到用户确认。

- 变更 step、config、artifact、tool、ErrorKind、manifest 或状态事件时，必须补相应契约测试。
- `skip` 仅用于明确不可用的外部前置条件；产品行为不满足预期时必须失败，已知缺口仅可使用 `xfail(strict=True)`。
- 测试数据使用匿名最小输入和黄金摘要，不提交大型轨迹、私有配置或真实运行目录。
- 测试、实现和相关设计文档一起变更。

## 2. 测试分层

| 层级 | pytest marker | 覆盖内容 | 执行方式 |
|------|------|------|------|
| 单元 | `unit` | ErrorKind、解析器、状态机、环境注册 | 默认执行 |
| 契约 | `contract` | config v2、tool schema、产物、manifest、检查点、拓扑组装 | 默认执行 |
| 集成 | `integration` | 编排器、前端 API、run registry、后处理、模拟执行、托管网关 ASGI/账本/本机控制面/客户端协议 | 默认执行，外部进程由替身控制 |
| 网关离线验收 | `gateway_acceptance` | 设备令牌、撤销、Token 额度、超时 unknown 预留清理、tool call 透传、SQLite 脱敏审计与流式拒绝 | 默认执行；可单独选择；仅临时 SQLite、ASGI 内存 transport 与脚本化 fake upstream |
| 外部 smoke | `external` | G16、ORCA、Sobtop、LigParGen/BOSS/Open Babel/C shell、GROMACS 的具名预检，以及内置 Multiwfn 的真实进程验收；G09 仅保留环境契约预检和用户提示，不执行本版本可靠全链路 smoke | 仅 `--run-external` |
| LLM 连通性 | `llm_connection` | 已配置 OpenAI-compatible 端点的实际 Chat Completions 与工具调用 | 仅 `--run-llm-connection` |
| LLM 行为评估 | `llm_eval` | 诊断、工具选择、重试、升级 | 独立的 mock eval 命令 |

当前 pytest 文件在收集时按上述前三层自动标记。网关集成测试使用临时 SQLite、隔离 Ed25519 密钥和 fake upstream，不读取真实上游或网络；`tests/test_gateway_offline_acceptance.py` 另以 ASGI 内存 transport 与脚本化 fake upstream 执行离线验收，不绑定端口、不读取 `md_run/` 或项目配置、不启动科学计算。它验证 token/revoke/quota/timeout/tool call/脱敏审计，并确认本版本的 `stream=true` 明确被拒绝，而非伪造未实现的 SSE。另有客户端到实际 FastAPI ASGI 网关的自助申请/签名令牌交换契约。管理员页测试验证管理员秘密隔离、自助名额、申请过期、批准/撤销和账本中的并发、预留/结算 token 汇总；所有网关测试不得将 prompt、Authorization 或上游 Key 写入账本或响应。`tests/test_external_smoke.py` 的 case 注册分别声明工具、fixture、预期产物、超时和证据目录；外部 fixture 根目录必须包含版本化 `fixture-manifest.json`，其条目必须与声明文件集合完全一致，并逐项校验文件大小和 SHA-256。默认不具备 fixture 的 case 会跳过，验收机设置 `WILLY_EXTERNAL_SMOKE_REQUIRED=1` 后缺工具、缺 fixture 或 bundle 校验失败必须导致失败。预检证据的 `execution_attempted=false` 只能证明“就绪”；真实 Sobtop 最小集成用例另写 `execution` 证据，必须含 `EC.itp` 和 `EC.gro` 摘要才可证明本次执行。fixture bundle 格式和操作边界见 [`../tests/fixtures/external/README.md`](../tests/fixtures/external/README.md)。配置回归还覆盖工作流外层 schema 对错误嵌套 section 的拒绝、未知扩展字段的前向兼容和显式 v2 迁移；环境回归验证 `needed_by` 从执行模块注册表反查而不复制模块字符串。
真实 LLM 连通性用例也默认跳过，只有传入 `--run-llm-connection` 后才会使用已配置的本地凭据请求网络。

真实组合验收可使用受控 `execution.stop_after_stage="eq"`：测试必须证明 Step 9 已通过其真实产物/验收门禁、`done_steps` 恰为 1--9、`completion_scope=through_eq`，并证明没有启动 Step 10。该范围不替代完整 PROD 验收；每个 profile 的失败需要保留独立 run 目录、公开错误摘要和私有执行证据后继续下一个串行 profile。

逐条测试对象、验证内容和方式见 [`tests/reports/test_case_catalog.md`](../tests/reports/test_case_catalog.md)。该目录由仓库内脚本从实际 pytest 收集结果生成，参数化变体按独立用例编号。

## 3. 命令与门禁

```bash
# 日常确定性回归；external 用例会清晰显示为跳过。
pytest -q

# 按层定位变更影响。
pytest -m unit -q
pytest -m contract -q
pytest -m integration -q

# 网关离线验收：不创建 socket，不调用真实上游，也不触碰模拟运行目录。
pytest -m gateway_acceptance -q

# 真实外部工具验收，只在目标环境执行。
pytest -m external --run-external -q

# 目标验收机将缺少的工具或 fixture 视为失败，而非跳过。
WILLY_EXTERNAL_SMOKE_REQUIRED=1 pytest -m external --run-external -q

# 在不运行任何科学计算的前提下，校验一个外部 fixture bundle。
python3 -m willy.external_smoke \
  --fixture-root /secure/willy-smoke-fixtures \
  --cases sobtop_ec \
  --required

# 校验已归档的执行证据确实来自执行而不是仅预检。
python3 -m willy.external_smoke \
  --evidence-dir test-results/external-smoke \
  --cases sobtop_ec \
  --phase execution \
  --require-execution

# 实际验证已配置 LLM 的 Chat Completions 与工具调用；不会写入 .env。
pytest --run-llm-connection -q tests/test_frontend_api.py::test_real_llm_connection_is_explicitly_opt_in

# Agent prompt、tool schema 或 LayerAgent 行为变更时执行。
python3 -m tests.llm_eval.run_eval
```

仓库 CI 的 `regression` job 始终执行默认 pytest 与离线 LLM eval，`quality` job 额外执行 `compileall` 并拒绝未重新生成的 `tests/reports/test_case_catalog.md`。`external-smoke` 仅在手动 dispatch 勾选 `run_external_smoke` 或仓库变量 `WILLY_ENABLE_EXTERNAL_SMOKE=true` 时，才会调度到标签为 `willy-external` 的自托管验收机；它固定设置 `WILLY_EXTERNAL_SMOKE_REQUIRED=1`，将 JUnit 与脱敏证据上传为 artifact。手动 dispatch 默认选中已实现真实执行的 `sobtop_ec`，并要求 `execution` 证据；选择尚未实现执行用例的 case 时，证据门禁应失败，这表示发布证据不足而非工具已通过。

| 变更类型 | 必跑门禁 |
|------|------|
| 配置、tool、ErrorKind | `contract` + 相关模块回归 |
| 编排、状态、run 管理、前端 | `integration` + `contract` |
| 模拟协议、MDP、后处理 | `contract` + `integration`；覆盖 LLM 伪确认拒绝、首次调用升级和公开提示；目标环境补 GROMACS smoke |
| 拓扑后端 | `contract` + `integration`；目标环境补 Sobtop/OPLS smoke |
| 量子后端 | 日常回归 + 目标环境的对应量子 smoke |
| Agent prompt 或工具行为 | 相关 pytest 集 + LLM mock eval |
| LLM 配置页或连接测试 | `tests/test_llm_config.py` + `tests/test_frontend_api.py` + `tests/test_app_ui.py`；目标端点补 `--run-llm-connection` |
| 当前工程可视化 | `tests/test_frontend_api.py` + `tests/test_app_ui.py`；覆盖运行目录/结构文件双选择器、目录切换后的文件隔离、启动锁优先/最新数字目录回退、PDB/MOL2 白名单、完整文件名和查看器渲染 |
| GROMACS 实时 ETA | `tests/test_mdrun_eta.py` + `tests/test_run_assistant.py` + 模拟执行回归；目标环境补 `mdrun -v` 真实 smoke |
| 确认停止与失活对账 | `tests/test_frontend_api.py` + `tests/test_app_ui.py` + `tests/test_pipeline_state.py` + `tests/test_pipeline_launch.py` + `tests/test_pipeline_orchestrator.py`；覆盖仅按钮的服务端两次确认、失败回执可重试、文本中止/暂停词不停止、前端轮询只读、GROMACS ETA/阶段产物存活证据、进程组仍存活时不误中止、revision 竞争放弃写入和失活事件审计；目标环境补 GROMACS checkpoint-first stop smoke |
| 量子或拓扑外部命令 | `tests/test_process_lifecycle.py` + 受影响后端契约回归；覆盖进程组 `SIGINT -> SIGTERM -> SIGKILL`、停止/超时的脱敏审计与既有 `StepResult` 错误分类；目标环境补对应真实 smoke |
| 协议调整待确认 | `tests/test_pending_action.py` + `tests/test_app_ui.py` + `tests/test_frontend_api.py` + `tests/test_pipeline_orchestrator.py`；覆盖脱敏待确认方案展示、等待状态不降级为未知、无歧义批准语、替代方案的 action ID 替换与配置不变、无回复/暂停/否决保持等待、非 `awaiting_confirmation` 状态的控制拒绝并回退只读问答，以及跨 run 或过期 action 拒绝 |
| 运行助理性能、降级与状态时间线 | `tests/test_run_assistant.py` + `tests/test_frontend_api.py` + `tests/test_app_ui.py` + LLM mock eval；覆盖独立气泡、关闭连续消息合并、同一状态原位刷新、状态转换封存、错误/方案去重、确认后新状态卡与 LLM 上下文隔离；目标环境补真实远程模型耗时 smoke |
| 外部环境解析、预检或部署变量 | `tests/test_env_registry.py` + `tests/test_env_checker.py` + 受影响模块回归 |
| 发布 | 全量 pytest、相关 LLM eval、质量契约（编译与测试用例台账漂移检查）、相关 external preflight/真实执行证据、验收记录 |

本地编排回归必须覆盖：新 run 不继承旧 `done_steps`；Step 2 Agent 只补上游产物时会重跑 Step 2；Step 3 对配置中任一缺失的 `*_opt.fchk` 失败，而不是以部分结果继续进入拓扑层；修复工具必须保留实际步骤身份；EQ 失败必须生成脱敏待确认方案，未授权不得修改配置、重跑或启动 PROD；替代方案必须生成新 action ID、使旧动作失效、保留 `awaiting_confirmation` 且不改写配置；批准当前动作后必须按 `awaiting_confirmation -> retrying -> running` 流转，并只能从其受限的 Step 9 或 Step 7 重跑，且成功的 EQ 许可与 checkpoint 仍是 PROD 的前置条件；历史公开状态越过仍在运行的 EQ 时只能修正状态，不得改写模拟文件。

## 4. Smoke 实现规范

Smoke 只确认最小真实链路可启动并生成可消费产物，不替代科学精度、收敛质量或生产规模验收。

模拟协议的契约测试必须覆盖 `EM -> EQ -> PROD` 的阶段许可、EQ/PROD 的 checkpoint 连续性，以及上游协议变更时 PROD 的失效。真实 smoke 只有在三个阶段均结束时，才可称为完整 MD 链路验收。

1. 用 `@pytest.mark.external` 标注，并由 `--run-external` 显式启用。
2. 在测试前检查依赖与许可证；开发机可 skip，指定验收机的缺失依赖必须导致门禁失败。外部输入只能从 `WILLY_EXTERNAL_SMOKE_FIXTURES` 所指的只读 bundle 读取，且 bundle manifest 的 schema、文件集合、大小与 SHA-256 必须匹配。
3. 通过 `tmp_path` 创建唯一 run workspace，复制最小匿名输入；禁止写入项目正式运行目录或 vendor 输出目录。
4. 外部科学工具 smoke 禁止调用 LLM 和网络服务，设置命令超时、CPU/GPU 和磁盘限制。LLM 连通性测试是独立的显式 opt-in，用单次、12 秒受限请求验证已配置端点，且不记录或输出密钥、请求头、原始响应和完整异常。
5. 每个 external case 先写 `preflight` 证据。该记录只能表示工具/fixture 是否就绪；真实执行必须另写 `execution` 证据，并记录已声明产物的相对路径、大小和 SHA-256。不得以预检成功或只有退出码 0 声称科学链路通过。
6. 成功后清理临时 workspace；失败时保留脱敏的失败类别、fixture 指纹、能力摘要和 JUnit/证据 JSON。fixture 原文、许可证、原始日志、完整命令行和密钥不得上传为 CI artifact。

最小案例规划：Sobtop 使用 `EC.mol2 + EC.chg`；GROMACS 使用预置小体系依次验证 EM、EQ、PROD；G16/ORCA 各使用单分子输入；G09 仅保留环境契约和用户提示，不纳入本版本可靠全链路验收；内置 Multiwfn 使用固定版本的单分子 fchk；LigParGen 在完整 `BOSSdir`、Open Babel 与 C shell 配置后执行中性小分子案例。后处理/分析、科学体系预测和 SSH/Slurm 不属于本版本公开验收范围。


## 5. 当前缺口与实施顺序

| 阶段 | 工作项 | 完成定义 |
|------|------|------|
| M0 | pytest 配置、分层 marker、外部 opt-in、依赖 extra、CI、脆弱 skip 清理 | 已完成：默认回归不调用外部工具，fresh install 可运行测试 |
| M1 | 共享 fixture 工厂、步骤注册表、契约矩阵、测试数据和覆盖率基线 | 已完成 StepRegistry、注册表契约测试、fixture bundle 哈希契约、external preflight/证据框架，以及 CI 的默认回归/编译/用例台账漂移门禁；覆盖率阈值待补 |
| M2 | 将已完成的四条十步真实 profile 固化为 external 用例和发布证据 | 当前四条 profile 均已有独立真实执行报告；CI self-hosted 固化属于后续治理增强 |
| M3 | LLM 升级决策场景、夜间门禁、发布证据归档 | 发布能追溯到测试、模型行为、完整的 external 执行证据和目标体系结果 |

M0 已完成。M1-M3 按目标环境、许可证和算力条件推进。

## 6. 本版本完整验收计划（不含网关）

本节定义本地 Willy 工作流的发布候选验收，不新增、修改或验收网关、SSH/Slurm 远程执行、后处理/分析或科学体系预测。当前成熟方案只要求受支持 profile 严格完成十步并以最终无错误状态结束。G09 仅保留接口和前端未验证提示，不计入本版本 profile。

“完整”指每项公开能力同时具有确定性回归、至少一个失败边界、适用时的真实执行证据和用户可见结果验证。本版本主流程的通过条件是十步完成且最终无错误，不生成科学体系结论。0 号负责总门禁，6 号维护矩阵和证据，1/2/3/4 号分别提供量子、拓扑、模拟和前端的 fixture、阈值及失败场景。

| 验收域 | 必须覆盖的事实 | 自动化证据 | 真实验收证据与通过条件 |
|---|---|---|---|
| A. 发布快照与可重复安装 | 干净提交、无运行锁/缓存/密钥进入版本库；测试环境可从空环境安装 | `git diff --check`、`python3 -m compileall -q app.py src/willy`、`pip install -e '.[test]'`、`pytest --collect-only -q`、测试台账生成检查 | 记录 commit SHA、Python/OS 版本与测试收集数；任何未忽略的运行锁、密钥、产物或格式错误均阻断候选发布 |
| B. 配置与输入审计 | schema v2、默认值、量子后端特异原始输入、电荷/自旋和电荷平衡；新任务不继承旧 run | 单元/契约测试覆盖有效输入、缺 `.gjf`/`.inp`、错误后端回退、缺失 `.fchk` 可从 Step 1 重建、配平阻断和 config 原子快照 | 每个外部 profile 使用其真实原始输入；G16 只读取 `.gjf`、ORCA 只读取 `.inp`，错误输入必须在启动前给出公开摘要且不创建计算进程 |
| C. 十步编排与持久化 | StepRegistry 顺序、产物许可、`run_manifest.json` section/CAS、状态事件、provenance、停止进程组 | 编排/manifest/状态机集成测试；覆盖上游修复不越级标记 EQ/PROD、过期 action/CAS 冲突、重启后读取和历史兼容 | 每个真实执行 run 保存脱敏配置哈希、软件版本、阶段产物相对路径和 SHA-256；EM/EQ/PROD 三阶段均结束且许可链完整才算全链路成功 |
| D. 后端组合 | 所有已承诺组合都能成功或在前置条件缺失时明确拒绝，不存在静默回退 | 对 G16/ORCA、Sobtop/OPLS-AA、GAFF/UFF 与 OPLS-AA 混用拒绝、LigParGen/BOSS 不可用分别设契约回归 | 在授权机运行四个最小 profile：G16+Sobtop、ORCA+Sobtop、G16+LigParGen/BOSS、ORCA+LigParGen/BOSS；OPLS-AA profile 仅选中性分子，离子或力场混用必须验证为受控拒绝 |
| E. GROMACS 十步协议 | Packmol/PBC 审计、残留输出隔离、EM -> EQ -> PROD、checkpoint 连续性、最终无错误和阶段产物完整 | MDP/建盒/阶段许可/产物契约测试；密度和真空区仅保留非阻塞诊断 | 每个已承诺 profile 均须真实完成 Step 1--10，进程正常退出，状态 `done`，manifest/status 和 `.tpr/.gro/.xtc/.edr` 通过校验；不推断科学收敛或目标体系性质 |
| F. 错误恢复与控制 | 错误公开摘要、知识检索来源标记、多方案选择、`awaiting_confirmation -> retrying -> running`、停止实际终止受管进程 | mock LayerAgent/LLM、pending-action、状态机和进程生命周期测试；覆盖未确认/暂停文本无副作用、选择方案但未确认仍等待、确认后只从许可步骤重跑、失败回退等待状态 | 人工触发一个可控 EQ 失败和一个子进程停止场景；前端必须显示最新错误/方案气泡，PROD 不得在 EQ 未验收时启动，停止后目标 PGID 不再存活 |
| G. 真实 LLM 兼容 | 已配置 OpenAI-compatible 服务能完成连接测试、工具调用、超时/格式错误降级，且不泄露凭据 | LLM 配置、错误提示、tool schema 和 mock eval；mock eval 覆盖无效工具参数、越层调用、重复确认与模型超时 | 以 `--run-llm-connection` 单次验证用户配置的端点；再对真实模型运行最小配置生成和受控错误解释。请求、响应、Key、Authorization 和原始 prompt 均不写入证据 |
| H. 前端端到端 | 方案生成、文本确认、状态卡、错误/方案独立气泡、停止二次确认、运行目录/结构文件选择和冻结远程页 | 现有 Gradio 配置与 API 集成回归；新增浏览器级 smoke 覆盖首屏、方案确认、等待确认、停止和工程切换 | 浏览器 smoke 使用临时本地服务和 fake 外部执行器，不调用真实科学软件；验证可见文案、控件状态和气泡顺序，不检查模型自然语言措辞 |

### 6.1 外部执行矩阵与证据分级

外部测试在本版本只保留十步链路证据；科学体系验收不纳入门禁：

1. **链路 smoke**：使用版本化、最小匿名 fixture，在唯一临时 `run_dir` 执行完整十步；可使用经记录的低成本测试体系，但不得跳过 EM、EQ 或 PROD。通过条件是所有外部进程退出成功、预期 artifact/manifest/status 完整、阶段许可连续、没有未处理的 GROMACS fatal error。
2. **科学体系预测**：明确不执行、不出具结论。密度、结构、势能等只能作为运行诊断，不能写入本版本“通过”结论。

每次 external case 必须先以 `WILLY_EXTERNAL_SMOKE_REQUIRED=1` 运行预检，再保存独立 `execution` 证据。证据只保留 case ID、commit SHA、fixture/config 哈希、软件版本、CPU/GPU 摘要、阶段结果、公开错误和已登记产物哈希；不得保留原始轨迹、命令行、绝对路径、许可证内容、密钥或原始日志。

### 6.2 执行顺序与发布判定

1. **R0，本地候选**：完成 A-C、F 的确定性测试，运行全量 `pytest -q`、`compileall`、mock eval、格式/台账检查；冻结待发布 commit。
2. **R1，外部后端**：在授权验收机依次执行 D 的四个最小 profile，并对每个 profile 保存 preflight 与 execution 证据。某一已承诺 profile 缺软件、许可证、fixture 或成功记录时，候选版本不得宣称支持该 profile。
3. **R2，模拟协议**：执行 E 的每个已承诺 profile 十步完整链路；失败时保留脱敏证据并回归至 F 的受控恢复用例。
4. **R3，产品增强（非本版本主流程门禁）**：真实 LLM、浏览器 E2E、通用运行助理功能可独立验收，但不改变十步成熟方案结论。
5. **R4，发布复核**：0、5、6 号确认 A-F、四条 profile 证据、文档台账和发布 commit 一致；不得把 G09、科学预测、后处理或 SSH/Slurm 写入本版本可用能力。

新增或修改测试时，6 号必须在 `tests/reports/test_case_catalog.md` 中保留 A-F 对应的可追溯 case；G/H 属于后续产品增强时再单独登记。未覆盖的公开能力应在本节或 `project_gap_analysis.md` 标为缺口，不得以默认 pytest 全绿替代已承诺 profile 的真实十步证据。

### 6.3 2026-08-10 本机执行快照

以下内容是历史审计快照，保留当时的测试数字和失败状态；当前版本结论以 6.7 及文档开头的最新基线为准。

本轮已完成外部依赖标准化与实查：使用隔离的最小 EC 样本，Gaussian 16 优化、formchk、内置 Multiwfn RESP、fchk-to-mol2、Sobtop 真实拓扑，以及 ORCA EC 结构优化均已成功；内置 Multiwfn 已以 vendor 二进制实际完成 fchk→xyz 与 fchk→RESP `.chg`，退出码均为 0。LigParGen 2.1 的 Open Babel 3、NetworkX 3 与 pandas 3 兼容均由私有子进程入口覆盖；安装 i386 运行时、`csh` 和完整 Open Babel 后，官方 BOSS 5.1 已以中性乙醇完成 CM1A 与 LBCC 的真实 `.itp/.gro` 最小执行。该结果只验证 OPLS 参数化后端，不得替代量子至 MD 的完整 profile 验收。

同日的受控四 profile 试行使用 `50 Li / 30 NO3 / 20 TFSI / 200 EC / 200 DME`，固定
EQ 为 `1/1/1/1/1/2 ns`，并通过 `execution.stop_after_stage=eq` 禁止 PROD。G16+Sobtop
完成 EM 与 EQ，最终温度均值 `298.79 K`、势能相对斜率 `3.88e-4/ns`；该结果只证明
此体系至 EQ 的链路。ORCA+Sobtop 当时被单原子 Li 的 `.gbw` 误判阻断，修复后已有
独立真实 Li SP→molden 证据，但尚未重跑完整 ORCA profile。G16+LigParGen/BOSS 到
Step 4 发现 Li、NO3、TFSI 受 OPLS 后端限制，ORCA+LigParGen/BOSS 因相同 ORCA
旧判定停在 Step 1；二者均不构成失败后可宣称支持的组合。完整十步和已修复 ORCA
组合 profile 仍属于 R1/R2 未完成项。

当前完整默认回归为 `799 passed, 9 skipped`；`compileall`、测试台账生成（`808 pytest + 18 LLM`）和 18 个 mock LLM eval 场景均通过，mock eval 均分为 `86.2/100`。通过的最终隔离发布批次见 [`tests/reports/baselines/regression-20260811-r5/batch_report.json`](../tests/reports/baselines/regression-20260811-r5/batch_report.json)，其中仅记录版本、配置 SHA-256、阶段结论、JUnit 计数摘要和产物哈希，不含原始日志或密钥。内置 Multiwfn 的固定解析、fchk→xyz、RESP `.chg`、非零退出映射、配置哈希和 Sobtop 可选菜单不回退 PATH 均纳入回归；本次还覆盖 LigParGen 临时前缀从最终 GRO 五列残基字段恢复。首个临时快照因 vendor 文件在副本创建期间发生完整性漂移而失败，已保留为 [`regression-20260811/batch_report.json`](../tests/reports/baselines/regression-20260811/batch_report.json) 审计记录；重新复制一致快照后通过。托管服务检查已改为设备令牌交换和 `/api/v1/me/usage` 查询，直接返回连接状态及本日/本月剩余 Token，不再调用 Chat Completions；前 50 个历史唯一注册设备可由服务端原子自动批准并收到默认 grant，管理员页显示自动批准窗口。直接 BYOK 连接测试仍为显式 opt-in，未纳入默认发布结论。Playwright/Chromium 使用 `/tmp` 隔离运行时完成了 1 条浏览器 E2E：fake executor + 临时 Gradio 服务验证首屏、公开错误气泡、待确认、确认、等待、停止二次确认和工程切换；该测试不访问 `md_run/`。

托管网关的客户端、配置页和 ASGI 接口现已收敛为服务端限额的自助申请：不再导入或接收邀请码，重复公钥不重复占位，待审批申请过期后释放名额。完整 pytest、应用导入和测试台账生成恢复为本轮门禁。该离线覆盖不构成真实跨机器 TLS、上游服务或公网反自动化验收。本段是 2026-08-10 的历史快照；后续串行四 profile 的 R2 证据和新缺口见 6.4。

### 6.4 2026-08-11 串行四 profile 真实验收

本轮使用同一批次运行器 `scripts/run_serial_acceptance_profiles.py`，四条 profile 串行执行；
自动修复 LLM 关闭，单条失败后保留 run 目录并立即进入下一条。四条配置均使用 EQ
六段 `1/1/1/1/1/2 ns`，PROD `2 ns`，本地 GROMACS，未设置
`execution.stop_after_stage`。批次报告为
`md_run/acceptance_batch_20260811001217.json`，原始受管输出为同名 `.log`。

| Profile | Run | 结果 | 阻塞位置与公开证据 |
|---|---|---|---|
| G16 + Sobtop | `md__202608110001` | **通过** | 10/10 完成，EM、7 ns EQ 和 2 ns PROD 均结束；`status.json` 为 `done`，`done_steps=[1..10]`。结构检查约每 5 分钟，EQ 约每 20 分钟读取；EQ 中间温度从约 `337.2 K` 降至 `326.7 K`，未出现 fatal error。 |
| ORCA + Sobtop | `md__202608110002` | 历史阻塞；修复后待重跑 | 历史批次 Step 2：5 个 ORCA `*_opt.fchk` 均生成，但 `fchk→mol2` 均报缺少 `MxBond` 字段；`done_steps=[1]`，未进入 RESP、拓扑或 MD。 |
| G16 + LigParGen/BOSS | `md__202608110003` | 历史阻塞；修复后待重跑 | 历史批次 Step 5：3 个分子均成功生成 LigParGen `.itp/.gro`，但 `FEC.itp` 与其他 ITP 对 `opls_806` 的参数定义冲突，未进入 MD。 |
| ORCA + LigParGen/BOSS | `md__202608110004` | 历史阻塞；修复后待重跑 | 历史批次 Step 2：EC/FEC/EMC 的 ORCA `*_opt.fchk` 均因缺少 `MxBond` 无法转为 `.mol2`，未进入 LigParGen 或 MD。 |

历史批次只形成一条完整十步真实证据；不能据此宣称 ORCA 或 OPLS-AA 组合已经支持。
四个 run 的错误、事件和中间产物均保留在各自目录，根目录 `config.json` 已在批次结束后恢复。

2026-08-11：上述两个阻塞点已完成代码级修复并纳入回归。ORCA 的 MOL2 改由
`*_opt.molden` 经内置 Multiwfn 连通性与 Mayer 键级生成，真实 EC 与 Li 样本分别验证了
键连接和单原子零键产物；LigParGen ITP 在 Step 5 的组装副本中使用确定性 atomtype 命名空间，
冲突 fixture 已能生成主拓扑且源 ITP 保持不变。该历史批次当时的状态为 1/4 完整通过、
3/4 修复后待重跑；随后续跑结果见 6.5。

2026-08-11 的续跑批次起，结构优化阶段的公开状态采样间隔改为 1 分钟；EQ 保持每 20 分钟。
该调整只改变验收观测频率，不改变运行配置或模拟协议。

### 6.5 2026-08-11 修复后续跑结果

在上述代码修复后，按同一配方串行重跑剩余三条 profile；批次报告为
`md_run/acceptance_batch_20260811152550.json`，受管输出为同名 `.log`。自动修复 LLM
关闭，单条失败后继续下一条，根目录配置已在结束时恢复。

| Profile | Run | 结果 | 证据与边界 |
|---|---|---|---|
| ORCA + Sobtop | `md__202608110005` | **通过** | 10/10 完成；ORCA Molden→MOL2、RESP、Sobtop、Packmol、EM、7 ns EQ 和 2 ns PROD 均成功，`status.json` 为 `done`。EQ 用时约 2063 s，PROD 约 586 s。 |
| G16 + LigParGen/BOSS | `md__202608110006` | 修复前失败并跳过 | Step 1--6 完成；LigParGen 三分子参数化和新的 ITP atomtype 命名空间组装均成功，Step 7 的 `gmx editconf` 前置转换失败，未启动 MD。 |
| ORCA + LigParGen/BOSS | `md__202608110007` | 修复前失败并跳过 | Step 1--6 完成；ORCA Molden→MOL2、RESP、LigParGen 三分子参数化和 ITP 命名空间组装均成功，Step 7 的同一 `gmx editconf` 前置转换失败，未启动 MD。 |

因此四 profile 当前仍形成 2/4 完整通过、2/4 在修复前的 Packmol 前置失败。根因是 LigParGen
将唯一 `-r` 临时前缀写入 GRO 的五列残基名字段；现已在 OPLS 专属输出适配中恢复该字段，并对
`md__202608110006/EC.gro` 的修复副本以 GROMACS 2025 `gmx editconf` 实测通过（10 atoms）；
同一失败工程的 `FEC.gro`（10 atoms）与 `EMC.gro`（15 atoms）也分别恢复并通过 `editconf`，且
最终 GRO 中无临时 UUID 残留。ORCA Molden 适配和 LigParGen ITP 命名空间也已获得真实组合链路证据；OPLS-AA 的完整 MD 仍未
验收，下一步是重新执行两条十步 profile。该批次的监测事件字段为空，
因为旧运行器实例在 `step` 字段修复前已加载；不能把它当作已完成的心跳证据。后续批次使用
修复后的 `step` 读取逻辑，结构优化每 1 分钟、EQ 每 20 分钟采样。

### 6.6 2026-08-11 OPLS GRO 修复后的两条完整 profile

按同一 `EC 200 / FEC 200 / EMC 200` 配方重跑两个历史失败的 OPLS profile；批次报告为
`md_run/acceptance_batch_20260811170615.json`。自动修复 LLM 关闭，G16 profile 结束后串行进入
ORCA profile，根目录 `config.json` 已恢复。

| Profile | Run | 结果 | 证据与边界 |
|---|---|---|---|
| G16 + LigParGen/BOSS | `md__202608110008` | 失败（Step 8） | Step 1--7 全部完成；GRO 临时前缀恢复、ITP 命名空间和 Packmol 均通过。`grompp` 因体系净电荷 warning 在默认 `-maxwarn=0` 门禁下停止，未启动 EM。 |
| ORCA + LigParGen/BOSS | `md__202608110009` | 失败（Step 8） | Step 1--7 全部完成；同一 `grompp` warning 门禁停止，未启动 EM。 |

该 warning 来自 LigParGen 四位小数部分电荷的累计舍入：单分子残余约 `0.0001--0.0002 e`，
200 个分子后 G16 体系总残余约 `0.08 e`、ORCA 体系约 `0.06 e`。这不是 GRO 或 atomtype
命名空间错误，也不能在未登记白名单前静默加入 `-maxwarn`。下一步应先决定“电荷归一化”或
基于真实体系证据登记该 warning 的受控白名单，再重跑两条 profile；在此之前不应宣称 OPLS
已完成 EM/EQ/PROD 验收。该历史结论由下一节批次关闭。

### 6.7 2026-08-11 净电荷容差后的 OPLS 完整验收

按用户确认的规则，grompp 不再把 `abs(total_charge) <= 0.15e` 的单一 Ewald 净电荷 warning
作为阻塞证据。执行器只对这一种可解析 warning 内部使用一次 `-maxwarn 1`；超过 `0.15e`、
电荷无法解析或同时存在其他 warning 仍失败。随后以相同 `EC 200 / FEC 200 / EMC 200` 配方、
六段 `[1, 1, 1, 1, 1, 2] ns` EQ（总计 7 ns）和 2 ns PROD 从 Step 1 串行重跑，LLM 自动修复关闭，
根目录配置在结束时恢复。批次报告为 `md_run/acceptance_batch_20260811174708.json`。

| Profile | Run | 结果 | 证据 |
|---|---|---|---|
| G16 + LigParGen/BOSS | `md__202608110010` | 通过 | Step 1--10 完成；EM、EQ、PROD 的 `.tpr/.gro/.xtc/.edr` 均非空，状态 `done`，批次 `exit_code=0`。 |
| ORCA + LigParGen/BOSS | `md__202608110011` | 通过 | Step 1--10 完成；EM、EQ、PROD 的 `.tpr/.gro/.xtc/.edr` 均非空，状态 `done`，批次 `exit_code=0`。 |

这两条证据关闭了此前的 Step 8 grompp 阻塞。它们只覆盖中性 EC/FEC/EMC 组合；离子 OPLS、G09
可靠全链路、科学体系预测和后处理/分析均不属于本版本公开能力。该段仅作为历史修复记录。
