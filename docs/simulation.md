# 模拟层

模拟层将当前 run 的已验证拓扑转为 Packmol 初始盒、GROMACS EM、三点式退火 EQ 和 PROD。它只接受当前 run 内通过契约的输入，并以阶段许可控制下游执行。

## 协议

```text
拓扑与坐标 -> MDP -> Packmol -> EM accepted -> EQ accepted -> PROD completed
```

`config.json.simulation.protocol` 定义 MD 参数。默认时间步长为 `0.001 ps`；EQ 使用高温、过渡温度和目标温度组成的三点式退火；PROD 必须从已验收的 EQ checkpoint 连续启动。协议或输入指纹变化时，受影响阶段必须重新生成，不能 append 到不匹配轨迹。

## 阶段产物与许可

| 阶段 | 许可 | 必需产物 |
|---|---|---|
| EM | `em_accepted` | `.tpr`、`.gro`、`.xtc`、`.edr` |
| EQ | `eq_accepted` | `.tpr`、`.gro`、`.xtc`、`.edr`、`.cpt` |
| PROD | `prod_completed` | `.tpr`、`.gro`、`.xtc`、`.edr`、`.cpt` |

所有产物必须来自当前受管调用、非空并满足阶段校验。文件存在不等于许可已获得。

## 建盒与预检

建盒前校验 `residues`、`topol.top`、ITP 与坐标的一致性。初始体积默认按拓扑质量和 `0.7 g/cm3` 计算，显式 `box_size` 优先。Packmol 输入必须声明周期边界，输出 PDB 的 `CRYST1` 必须与请求盒矢量一致。

Packmol、grompp 和 mdrun 失败分别保留受限类别。动态加载器、ABI 或依赖库问题为 `runtime_unavailable`，不尝试通过改动密度或 MDP 修复。`grompp` 默认拒绝 warning；仅当输出中只有 Ewald 净电荷 warning 且总电荷绝对值不超过 `0.15e` 时可受控使用一次 `-maxwarn 1`。

## EQ 与 PROD 验收

EQ 仅评估目标温度保持段：温度均值距目标温度不超过 5 K，势能归一化线性斜率不超过配置阈值。未通过时不得进入 PROD。PROD 检查正常结束、轨迹帧、EDR、checkpoint 与强制产物完整性。

错误对外归类为输入契约、引擎失败、数值不稳定、平衡未通过和恢复冲突。自动恢复只允许白名单工具和受限次数；温度、时长、压力耦合、输出精度和协议变更必须先取得用户确认。

EQ 待确认恢复方案固定包含“问题”“当前步调参重试”“打回前序流程重试”和逐点“证据”。当前步只能从第 9 步重新验收；打回前序流程只允许经证据支持后从第 7 步 Packmol 重建。两条路径均为高风险科学协议变更，必须人工审核和明确确认；EM 等低风险、无协议变更的受控重试仍按既有恢复策略执行。`ErrorKind=unknown` 不得触发默认参数调整，而是升级为联网检索申请/人工审核，保持配置和阶段许可不变。

## 运行与停止

GROMACS 由独立受管进程组执行。停止或超时按 `SIGINT -> SIGTERM -> SIGKILL` 升级，以优先保留 checkpoint。停止不是自动修复触发条件，最终状态必须为 `aborted`。私有生命周期记录保存信号和退出事实，公开状态只显示脱敏摘要。

## 后处理边界

后处理只能读取同一 run 的已完成 PROD 产物，结果写入独立 `analysis/<analysis_id>/`，不得修改 MD 输入、原始轨迹或拓扑。它不是当前公开主流程能力，也不由 LLM 生成科学结论。

## 验证

```bash
python3 -m pytest -q tests/test_simulation_protocol.py \
  tests/test_simulation_execution.py tests/test_toolist_simulation.py \
  tests/test_pipeline_orchestrator.py tests/test_frontend_api.py
```
