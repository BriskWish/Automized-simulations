# 重构检查清单

> 版本 1.2 · 2026-08-05 · 基于各执行层、动作契约与运行管理重构经验

重构执行层（`quantum/`、`topology/`、`simulation/`）或运行管理层（`run_registry.py`、`pipeline_launch.py`、`run_store.py`）任一文件时，必须同步检查下列关联方。前六项覆盖执行调用链，后续项覆盖步骤注册、动作授权、持久化、运行事实、环境和公开接口。

---

## 依赖图

```
                       ┌─────────────────┐
                       │  执行层 maker    │  ← 你改的地方
                       │  quantum/*.py   │
                       └───────┬─────────┘
                               │
      ┌──────────┬─────────────┼─────────────┬──────────┬────────────┐
      ▼          ▼             ▼             ▼          ▼            ▼
┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐
│ toolist  │ │orchestr  │ │agent     │ │ errors   │ │ config   │ │ env      │
│ handler  │ │_build_   │ │prompt    │ │          │ │ defaults │ │ checker  │
│ 适配器   │ │steps     │ │决策规则  │ │          │ │          │ │          │
└──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────┘
```

---

## 1. toolist handler — 工具适配器

**文件**: `src/willy/toolist_{scope}.py`

**检查**:

- [ ] maker 函数签名变了？handler 调用参数需同步
- [ ] 产物文件名变了？（如 `.fchk` → `*_opt.fchk`）handler 里的路径逻辑需更新
- [ ] 步骤合并/拆分？handler 的 `step_name` 需对齐
- [ ] 新增/删除了步骤？handler 分支需增减

**本次案例**:
- 已修复：`tools_retry_mol2_conversion` 现在接收 Step 2 的显式 `*_opt.fchk` 路径，并调用统一的纯 Python 转换器。
- 已修复：`tools_retry_chg_g16` 与 `tools_retry_chg_orca` 都接收显式 `*_opt.fchk` 路径，再调用统一的 `chg_resp` 接口。

---

## 2. orchestrator — 步骤编排

**文件**: `src/willy/pipeline_orchestrator.py`

**检查**:

- [ ] 步骤合并/拆分后 `_build_steps()` 的 lambda 更新？
- [ ] import 的 maker 模块名变了？有无死 import？
- [ ] 步骤数变了？`total_steps` 参数需同步
- [ ] `_STEP_LAYER` 映射需调整？
- [ ] `step_name` 在错误返回里是否正确？

**本次案例**:
- `mol2_g16.batch_convert` 和 `mol2_orca.batch_convert` 变成死 import（Step 2 改用 `_g16_sp_and_mol2`）
- `_g16_sp_and_mol2` 的 `step_name` 写成 `"chg_g16"`（应该是 `"sp_mol2_g16"`）
- 导入 `resp_maker` 的私有函数 `_run_orca_sp` 和 `_extract_xyz`

---

## 3. agent prompt — 决策规则

**文件**: `src/willy/agent_{scope}.py`

**检查**:

- [ ] "可用工具"列表的工具描述过时？含新/旧产物名？
- [ ] 决策规则中的步骤顺序变了？
- [ ] Few-shot 示例匹配新流程？
- [ ] 工具名是否完整（含 `tools_` 前缀）？

---

## 4. errors — 错误类型

**文件**: `src/willy/errors.py`

**检查**:

- [ ] 新增了错误类型？需加到 `ErrorKind` 枚举
- [ ] 现有的 `ErrorKind` 是否覆盖新的失败模式？
- [ ] `StepResult` 返回值结构一致（`outputs` / `artifacts` / `step_name`）？

---

## 5. config defaults — 配置默认值

**文件**: `src/willy/workflow_config.py`

**检查**:

- [ ] 新增了配置字段？`_apply_defaults()` 需补 `setdefault`
- [ ] 配置字段改名了？下游消费者需同步

---

## 6. env checker — 依赖检查

**文件**: `src/willy/env_checker.py`

**检查**:

- [ ] 新增了外部依赖？`_DEPENDENCIES` 列表需补
- [ ] 依赖改名了？`needed_by` 和 `path` 需同步
- [ ] `ensure(module)` 的参数值需更新？

`env_checker.py` 是兼容入口；新增或改名外部依赖时还必须检查 `env_registry.py` 中的工具声明、覆盖变量、解析优先级、子进程环境和脱敏报告。

## 6.1 步骤与动作契约

**文件**: `src/willy/step_registry.py`、`action_contract.py`、`recovery_policy.py`

**检查**:

- [ ] 增删步骤、修改标签、产物契约或重跑许可时，`STEP_REGISTRY` 是否仍是唯一来源？
- [ ] 新增或调整 tool 时，五个 toolist 的 JSON Schema、`TOOL_META`、`ActionToolCatalog` 和恢复策略是否同步？
- [ ] 新动作的效果等级、确认要求、失效阶段、重跑位置和 fork 限制是否有回归测试？

禁止在调用方复制步骤数字、绕过工具效果判断，或把模型输出直接当作用户授权。

## 6.2 配置与 run 持久化

**文件**: `src/willy/config_schema.py`、`config_store.py`、`run_store.py`、`run_provenance.py`

**检查**:

- [ ] 新顶层配置字段是否同时更新外层 schema、默认值/语义校验、配置快照和文档？
- [ ] 配置写入是否仍为带锁的原子替换，备份失败时是否保留旧活动配置？
- [ ] 新 run 事实是否写入正确的 run-local manifest/provenance/事务记录，且不含密钥、绝对路径、原始 prompt 或日志？

---

## 7. RunRegistry — 公开运行事实

**文件**: `src/willy/run_registry.py`、`src/willy/pipeline_state.py`

**检查**:

- [ ] `run_id`、`manifest.json`、`status.json`、`events.jsonl` 或公开字段变了吗？
- [ ] `md_run/index.json` 的摘要和 `RunRegistry` 只读接口仍与真实 run 对齐吗？
- [ ] 是否把内部的 `md_manifest.json` 或 `topology_manifest.json` 错当作公开运行审计事实？

---

## 8. Pipeline launch — 启动锁与绑定

**文件**: `src/willy/pipeline_launch.py`、`src/willy/agent_config.py`

**检查**:

- [ ] 启动预占、run 绑定、清理和失败路径仍只影响当前项目与当前运行吗？
- [ ] 未绑定 run 的冲突或失败是否只写入脱敏 `startup_audit.json`？
- [ ] 新流程是否仍从空 `done_steps` 开始，续跑是否必须显式传入受控原 run？

---

## 9. 环境与状态公开接口

**文件**: `src/willy/env_registry.py`、`src/willy/frontend_api.py`、`src/willy/toolist_run.py`

**检查**:

- [ ] 新工具、变量或环境报告是否已由 `env_registry` 解析，而不是在执行器中自行读取环境？
- [ ] `environment_report.json`、`mdrun_eta.json`、状态事件和运行助理输出是否仍不泄露路径、密钥、命令或原始日志？
- [ ] 前端与 LLM 是否继续只经 `RunRegistry` 和受限工具读取运行事实？

新增外部命令或停止行为时，还必须检查 `process_lifecycle.py`：命令是否通过受管进程组启动，停止/超时是否按 `SIGINT -> SIGTERM -> SIGKILL` 升级，私有审计是否继续脱敏且不进入公共状态。

---

## 10. 测试与责任文档

**检查**:

- [ ] 对应的单元、契约、集成或 external smoke 已按 `testing_strategy.md` 增补？
- [ ] 相关设计文档、`docs/README.md` 和 `document_registry.md` 已同步？
- [ ] 改名、步骤、产物或公开字段是否已经全仓搜索旧名和旧契约？
- [ ] 变更测试时，`scripts/generate_test_case_catalog.py` 是否重新生成且 CI 中无台账漂移？涉及外部工具时，是否新增或更新 required smoke/执行证据？

---

## 检查顺序

沿着一次正常执行的数据流走一遍：

```
config.json → orchestrator._build_steps() → maker 函数
                                            ↓
                                          产物文件
                                            ↓
                               toolist handler（重试路径）
                                            ↓
                               agent prompt（LLM 决策）
```

1. orchestrator 怎么调 maker → import 和 lambda 正确？
2. maker 返回什么 → outputs/artifacts 产物名正确？
3. toolist handler 重试时怎么调 → 参数和路径匹配新 maker？
4. prompt 怎么描述 → LLM 看到的工具和步骤描述正确？
5. error 类型是否覆盖 → 新失败模式有对应的 ErrorKind？
6. config/env 是否同步 → 新字段/新依赖有默认值？

---

## 常见遗漏模式

| 模式 | 表现 | 预防 |
|------|------|------|
| **死 import** | orchestrator import 了不再使用的模块 | 每次改 `_build_steps` 后 grep 旧 import 名 |
| **私有函数泄露** | orchestrator 调了 maker 的 `_private_func` | 对外接口用公开函数，封装逻辑 |
| **step_name 漂移** | 错误返回用了其他步骤的名字 | 每步用一个唯一 step_name，全局搜索去重 |
| **产物名变更传播断裂** | maker 输出 `*_opt.fchk`，toolist 还在找 `.fchk` | 产物名变更时全局 grep 旧名 |
| **prompt 不同步** | 工具名/步骤描述与代码不一致 | 每次改 tool 参数或步骤后检查 prompt |
| **修复身份覆盖** | 上游工具成功被重标为原失败下游步骤，状态越级进入后续阶段 | 保留工具返回的 `step_name`/`step_index`；由编排器根据实际阶段回滚或重跑，并在 MD 阶段完成前校验 manifest 许可与产物 |
