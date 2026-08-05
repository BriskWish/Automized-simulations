# Willy 测试策略与质量门禁

> 维护角色：6 号测试工程师
> 最后更新：2026-08-05
> 当前基线：2026-08-05 复核 `pytest -q` 为 586 passed、8 skipped（收集 594 条 pytest 用例）。新增覆盖包括外部 smoke 注册、验收机 required 门禁、带版本/文件集合/大小/SHA-256 校验的 fixture bundle，以及区分预检和真实执行的脱敏证据；GROMACS、量子和拓扑外部命令的 `SIGINT -> SIGTERM -> SIGKILL` 进程组生命周期审计；run provenance；RunStore 并发读改写与跨审计日志单调序列；以及唯一 StepRegistry 在编排、恢复、待确认动作、前端停止判断、阶段执行和产物审计中的一致性。外部工具和真实 LLM 网络测试仍为显式 opt-in；默认跳过不代表相应的真实科学链路已验收。当前仅 Sobtop EC case 具有已实现的真实执行测试，尚无目标验收机的成功证据；其余外部 case 仍只执行受控预检。此前的质量密度建盒、PBC/`CRYST1`、ETA、当前 run 隔离、用户确认、停止和状态越级防护回归继续保留。真实 checkpoint-first stop smoke、长程真实 MD ETA 刷新和真实远程模型端到端耗时仍须在目标体系验收机确认。

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
| 集成 | `integration` | 编排器、前端 API、run registry、后处理、模拟执行 | 默认执行，外部进程由替身控制 |
| 外部 smoke | `external` | G16、ORCA、Multiwfn、Sobtop、LigParGen/BOSS、GROMACS 的具名预检；当前 Sobtop EC 另有最小真实执行 | 仅 `--run-external` |
| LLM 连通性 | `llm_connection` | 已配置 OpenAI-compatible 端点的实际 Chat Completions 与工具调用 | 仅 `--run-llm-connection` |
| LLM 行为评估 | `llm_eval` | 诊断、工具选择、重试、升级 | 独立的 mock eval 命令 |

当前 pytest 文件在收集时按上述前三层自动标记。`tests/test_external_smoke.py` 的 case 注册分别声明工具、fixture、预期产物、超时和证据目录；外部 fixture 根目录必须包含版本化 `fixture-manifest.json`，其条目必须与声明文件集合完全一致，并逐项校验文件大小和 SHA-256。默认不具备 fixture 的 case 会跳过，验收机设置 `WILLY_EXTERNAL_SMOKE_REQUIRED=1` 后缺工具、缺 fixture 或 bundle 校验失败必须导致失败。预检证据的 `execution_attempted=false` 只能证明“就绪”；真实 Sobtop 最小集成用例另写 `execution` 证据，必须含 `EC.itp` 和 `EC.gro` 摘要才可证明本次执行。fixture bundle 格式和操作边界见 [`../tests/fixtures/external/README.md`](../tests/fixtures/external/README.md)。配置回归还覆盖工作流外层 schema 对错误嵌套 section 的拒绝、未知扩展字段的前向兼容和显式 v2 迁移；环境回归验证 `needed_by` 从执行模块注册表反查而不复制模块字符串。
真实 LLM 连通性用例也默认跳过，只有传入 `--run-llm-connection` 后才会使用已配置的本地凭据请求网络。

逐条测试对象、验证内容和方式见 [`test_case_catalog.md`](test_case_catalog.md)。该目录由仓库内脚本从实际 pytest 收集结果生成，参数化变体按独立用例编号。

## 3. 命令与门禁

```bash
# 日常确定性回归；external 用例会清晰显示为跳过。
pytest -q

# 按层定位变更影响。
pytest -m unit -q
pytest -m contract -q
pytest -m integration -q

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

仓库 CI 的 `regression` job 始终执行默认 pytest 与离线 LLM eval，`quality` job 额外执行 `compileall` 并拒绝未重新生成的 `test_case_catalog.md`。`external-smoke` 仅在手动 dispatch 勾选 `run_external_smoke` 或仓库变量 `WILLY_ENABLE_EXTERNAL_SMOKE=true` 时，才会调度到标签为 `willy-external` 的自托管验收机；它固定设置 `WILLY_EXTERNAL_SMOKE_REQUIRED=1`，将 JUnit 与脱敏证据上传为 artifact。手动 dispatch 默认选中已实现真实执行的 `sobtop_ec`，并要求 `execution` 证据；选择尚未实现执行用例的 case 时，证据门禁应失败，这表示发布证据不足而非工具已通过。

| 变更类型 | 必跑门禁 |
|------|------|
| 配置、tool、ErrorKind | `contract` + 相关模块回归 |
| 编排、状态、run 管理、前端 | `integration` + `contract` |
| 模拟协议、MDP、后处理 | `contract` + `integration`；覆盖 LLM 伪确认拒绝、首次调用升级和公开提示；目标环境补 GROMACS smoke |
| 拓扑后端 | `contract` + `integration`；目标环境补 Sobtop/OPLS smoke |
| 量子后端 | 日常回归 + 目标环境的对应量子 smoke |
| Agent prompt 或工具行为 | 相关 pytest 集 + LLM mock eval |
| LLM 配置页或连接测试 | `tests/test_llm_config.py` + `tests/test_frontend_api.py` + `tests/test_app_ui.py`；目标端点补 `--run-llm-connection` |
| 当前工程可视化 | `tests/test_frontend_api.py` + `tests/test_app_ui.py`；覆盖下拉框焦点刷新、启动锁优先/最新数字目录回退、PDB/MOL2 白名单、完整文件名和查看器渲染 |
| GROMACS 实时 ETA | `tests/test_mdrun_eta.py` + `tests/test_run_assistant.py` + 模拟执行回归；目标环境补 `mdrun -v` 真实 smoke |
| 确认停止与失活对账 | `tests/test_frontend_api.py` + `tests/test_app_ui.py` + `tests/test_pipeline_state.py` + `tests/test_pipeline_orchestrator.py`；覆盖仅按钮的服务端两次确认、失败回执可重试、文本中止/暂停词不停止；目标环境补 GROMACS checkpoint-first stop smoke |
| 量子或拓扑外部命令 | `tests/test_process_lifecycle.py` + 受影响后端契约回归；覆盖进程组 `SIGINT -> SIGTERM -> SIGKILL`、停止/超时的脱敏审计与既有 `StepResult` 错误分类；目标环境补对应真实 smoke |
| 协议调整待确认 | `tests/test_pending_action.py` + `tests/test_app_ui.py` + `tests/test_frontend_api.py` + `tests/test_pipeline_orchestrator.py`；覆盖脱敏待确认方案展示、等待状态不降级为未知、无歧义批准语、替代方案的 action ID 替换与配置不变、无回复/暂停/否决保持等待、非 `awaiting_confirmation` 状态的控制拒绝并回退只读问答，以及跨 run 或过期 action 拒绝 |
| 运行助理性能与降级 | `tests/test_run_assistant.py` + `tests/test_frontend_api.py` + `tests/test_app_ui.py` + LLM mock eval；目标环境补真实远程模型耗时 smoke |
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

最小案例规划：Sobtop 使用 `EC.mol2 + EC.chg`；GROMACS 使用预置小体系依次验证 EM、EQ、PROD 和后处理；G16/ORCA/Multiwfn 各使用单分子输入；LigParGen 在 `BOSSdir` 完整配置后执行中性小分子案例。


## 5. 当前缺口与实施顺序

| 阶段 | 工作项 | 完成定义 |
|------|------|------|
| M0 | pytest 配置、分层 marker、外部 opt-in、依赖 extra、CI、脆弱 skip 清理 | 已完成：默认回归不调用外部工具，fresh install 可运行测试 |
| M1 | 共享 fixture 工厂、步骤注册表、契约矩阵、测试数据和覆盖率基线 | 已完成 StepRegistry、注册表契约测试、fixture bundle 哈希契约、external preflight/证据框架，以及 CI 的默认回归/编译/用例台账漂移门禁；覆盖率阈值待补 |
| M2 | 将已完成的 GROMACS 小体系验收固化为 external 用例，并补量子、OPLS 的可重放 smoke | 每条主线都有版本化真实执行证据；当前仅 Sobtop EC 执行用例已实现，尚未取得验收机证据 |
| M3 | LLM 升级决策场景、夜间门禁、发布证据归档 | 发布能追溯到测试、模型行为、完整的 external 执行证据和目标体系结果 |

M0 已完成。M1-M3 按目标环境、许可证和算力条件推进。
