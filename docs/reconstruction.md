# 重构检查清单

> 版本 1.0 · 2026-07-29 · 基于 Quantum 层重构经验

重构引擎层（`quantum/`、`topology/`、`simulation/`）任一文件时，必须同步检查以下 **6 个关联方**。

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
- `tools_retry_mol2_conversion` 不知道新产物 `*_opt.fchk`，reparse 路径找到 `Li.fchk` 但找不到 `Li_opt.fchk`
- `tools_retry_chg_g16` 仍传 `gjf_path` 给 maker，而 maker 已改为读 `*_opt.fchk`（传参兼容但语义不对）

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

**文件**: `src/willy/llm_config.py`

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
