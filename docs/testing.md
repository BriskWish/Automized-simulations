# Willy 测试与质量门禁

## 目标

测试分为确定性代码契约、受控 LLM 行为、真实外部工具和人工浏览器验收。低层通过不替代高层证据；真实工具可启动也不替代受管十步流程。

当前测试台账为 `937 pytest + 18 LLM = 955`。

## 测试层级

| 层级 | 范围 | 通过标准 |
|---|---|---|
| 单元与契约 | schema、状态机、文件契约、权限、脱敏、资源边界 | 断言可重复，且不依赖本机科学软件。 |
| 前端与浏览器 | 方案确认、运行状态、停止、`/resume`、`/fork`、`/switch` | 用户可见状态与服务端 run 事实一致，不触发越权操作。 |
| LLM | 工具调用、字段校验、错误分类和受控恢复 | 模型只能调用白名单工具；高影响动作必须取得明确确认。 |
| 外部工具 | GROMACS、量子、拓扑、Packmol | 必须通过 Willy 的预检、受管子进程和产物校验。 |
| 人工验收 | 目标机、真实浏览器和真实凭据环境 | 保存脱敏结论，不以截图或裸命令替代受管证据。 |

## 常用命令

```bash
# 默认离线回归
python3 -m pytest -q

# 文档和测试台账一致性
python3 -m pytest tests/test_documentation_consistency.py -q

# React 生产构建与浏览器工作台回归
cd frontend
npm run build
npm run visual-check
cd ..

# 目标机外部验收：缺工具或 fixture 必须失败，而非跳过
WILLY_EXTERNAL_SMOKE_REQUIRED=1 python3 -m pytest -m external --run-external -q

# 最小受管 GROMACS 流程：三原子体系，仅验证流程和产物契约
PYTHONPATH=src python3 -m tests.tools.g01_gromacs_minimal_acceptance \
  --fixture-root test-results/g01-gromacs-fixture \
  --initialize-fixture \
  --evidence-dir test-results/external-smoke \
  --output test-results/g01-gromacs-minimal.json

# 已配置 LLM 的真实连通性与工具调用探针
python3 -m pytest --run-llm-connection -q tests/test_frontend_api.py
```

## 外部验收规则

外部 smoke 必须明确 opt-in，并在目标机缺少依赖、fixture 或浏览器运行时时失败。受管执行证据至少包含：环境摘要、执行范围、阶段结果、固定产物哈希、公开错误摘要和 evidence 校验结论。证据不得包含密钥、完整命令、绝对路径、原始日志或私有运行目录。

G-01 的最小 fixture 使用受管 EM/EQ/PROD 验证 GROMACS 调用、阶段许可、产物与 evidence；它不代表真实体系的科学结果。G-03 需要一条受支持 profile 完整完成十步；G-06 需要目标浏览器验证；G-07 需要按实际目标环境执行错误矩阵。

## 发布门禁

发布前必须满足：

1. 默认回归、编译检查和文档一致性检查通过。
2. 测试目录由当前收集结果重新生成并与台账一致。
3. 与改动范围对应的外部 smoke 或人工验收已完成，未完成项保留在 `project_gap_analysis.md`。
4. 公开文档、架构文档、接口文档和缺口台账对同一能力没有冲突承诺。
