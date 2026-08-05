# Willy 项目综合评估

> 评估日期：2026-08-05
> 范围：当前工作区的源码、文档、确定性测试、LLM mock eval 与已记录的小体系外部验收。
> 本报告是当前快照，不替代 [`project_gap_analysis.md`](project_gap_analysis.md) 的问题台账或各设计文档的领域契约。

## 总体结论

Willy 已具备可运行的研究型内部自动化 MVP：自然语言配置、量子计算、RESP、拓扑、Packmol、GROMACS EM/NPT EQ/PROD、运行状态和失败后分层 Agent 修复已形成 10 步主链路。它尚不是生产级科学计算平台，原因是目标体系科学验收、安全控制、可观测性和跨环境外部工具覆盖仍不充分。

## 已核验证据

| 证据 | 结果 | 含义 |
|------|------|------|
| `pytest -q` | 586 passed，8 skipped | 默认回归为确定性、隔离的测试基线；6 个 external case 与真实 LLM 连通性均显式 opt-in，另有真实 Sobtop execution 用例待验收机 fixture。 |
| `python3 -m tests.llm_eval.run_eval` | 18/18 通过，均分 86.2/100 | Mock 场景中各层 Agent 可选择受限工具并形成修复或升级结论。 |
| [`simulation_design.md`](simulation_design.md) 记录的小体系 smoke | Packmol -> EM -> EQ -> PROD 完成 | 已验证最小 GROMACS 链路和强制产物。 |
| 主编排器与状态契约 | 10 个步骤，注册表、run 内 manifest/status/events | EM、EQ、PROD 通过阶段许可串联；受控重跑、产物契约、执行模块依赖和前端状态均查询唯一注册表。 |
| 持久化与外部进程 | provenance、写前日志、受管进程组 | 中断的状态/环境/步骤审计写入可幂等重放；GROMACS、量子与拓扑外部命令统一采用信号升级和脱敏审计。 |

## 当前能力与边界

| 范围 | 状态 | 边界 |
|------|------|------|
| 量子层 | 可用主链路 | G16/ORCA 经两步法产出统一 `.mol2` 与 `.chg`；外部许可证和实际环境仍是前置条件。 |
| 拓扑层 | Sobtop 主线可用 | OPLS-AA 依赖 LigParGen/BOSS 环境，不能与 GAFF 在同一 run 混用。 |
| 模拟层 | 10 步主链路已接入 | EM -> NPT EQ -> PROD 已有契约测试；不承诺目标体系收敛，真实组合验收执行中。 |
| 后处理 | 确定性 API 已定义 | 基于完成的 PROD 产物；组分特异性分析和物性结论仍需受控扩展。 |
| 运行环境 | 核心注册与预检已接入 | `env_registry` 统一解析外部软件；版本兼容性判定与自动重规划尚待评估。 |
| 运行助理 | Phase A 已实现 | 只读观察、状态解释和产物摘要已具备；续跑、终止、派生运行等控制动作尚未开放。 |
| 前端 | 本地控制面板可用 | 默认仅本机绑定；远程部署需认证与受管进程控制。 |

## 已收敛事项

- `tools_validate_config` 的运行时导入、依赖预检命名和运行目录 ITP 修订问题已由代码和回归测试覆盖。
- 默认 Gradio 绑定已收紧为 `127.0.0.1`，`frontend_api.py` 不再通过 `os.system` 拼接清理命令。
- 文档已建立导航、台账和领域责任边界；旧文件名不再作为当前接口描述。
- 根目录不再保存共享拓扑、状态、计数器或历史 RESP/清理脚本；运行产物位于独立 `md_run/<run_id>/`，历史清理由受锁保护的保留工具显式执行。
- `config_schema.py`、`STEP_REGISTRY` 与 `EXECUTION_MODULE_REGISTRY` 分别收敛配置外形、步骤/产物契约和依赖归属；未知扩展字段与旧 MD 配置迁移保持兼容。
- 打包声明包含 `scikit-learn`，并提供 `test`/`dev` pytest extras；CI 已配置默认 regression、compileall/测试台账质量门禁和条件 self-hosted external gate，仍需远程执行验证。

## 主要风险与下一步

1. **科学与外部验收**：为目标体系建立可重放 GROMACS、量子和 OPLS smoke，以及收敛阈值、警告白名单和验收记录。
2. **安全与运行控制**：持续审计密钥处理、参数化子进程调用和远程部署；远程访问必须认证，并按 run 进程组控制停止。
3. **跨环境发布**：完成 CI 远程全绿记录，并在 self-hosted 验收机保存 external fixture、真实执行证据和版本摘要。
4. **可观测性与仓库治理**：引入结构化日志，分离运行产物与示例数据，并补齐 vendor 的来源、版本、license 和校验信息。
5. **文档门禁**：每次领域变更按 [`document_registry.md`](document_registry.md) 同步责任文档、测试证据和状态台账。

## 判断

项目适合继续作为研究型内部工具推进。发布为更广泛使用的平台之前，必须完成目标体系科学验收、安全控制、外部工具覆盖和文档台账中的发布门禁。
