# MD 后处理设计

维护范围：`src/willy/simulation/postprocess.py`。后处理是生产模拟完成后的确定性分析，不属于主流程 Step 1-10，也不由 LLM 决定分析结果。

## 输入与产物

输入限定为同一次 run 中非空的 `prod.tpr`、`prod.xtc`、`prod.edr`、`prod.mdp`。模块绝不改写这些源文件。

一次分析写入 `md_run/<run_id>/analysis/<analysis_id>/`。若该目录已有 `analysis_manifest.json`，请求直接失败，必须使用新的 `analysis_id`，避免不同参数的结果混写。

manifest 在执行前以 `running` 状态原子写入；可预期失败会更新为 `failed` 并记录错误类别、摘要和修复建议，不记录原始 GROMACS 输出。成功时状态为 `completed`。

可直接调用 API `run_postprocess(run_dir, PostprocessConfig(...))`，或运行：

```bash
python3 -m willy.simulation.postprocess md_run/<run_id> --analysis-id standard
```

| 产物 | 用途 |
|---|---|
| `analysis_manifest.json` | 输入文件指纹、参数、完整性、统计和 MSD 摘要 |
| `prod_centered.xtc` | 已剔除平衡段、居中的可视化/结构分析轨迹 |
| `prod_nojump.xtc` | 连续坐标轨迹，供 MSD 使用 |
| `thermo_*.xvg` | Temperature、Pressure、Density、Potential 的原始提取数据 |
| `msd_system.xvg` | 指定原子组的 MSD 原始数据 |

## 标准分析顺序

1. 用 `gmx check` 读取生产轨迹末帧，并以 `prod.mdp` 的 `dt * nsteps` 验证完整性。缺帧时不分析，要求先从 checkpoint 续跑。
2. 用 `gmx trjconv` 依次得到分子完整的临时轨迹、居中轨迹和 no-jump 轨迹。临时文件在成功后删除，避免三份 XTC 副本占满磁盘；PBC 处理不替代原始轨迹。
3. 按显式 `discard_time_ps` 或默认末帧时间的 20% 剔除平衡段。剔除区间覆盖整段轨迹时拒绝执行。
4. 用 `gmx energy` 提取温度、压力、密度和势能，并对保留窗口计算均值、标准差和五块分块标准误。
5. PBC 处理始终使用完整的 `System` 组，避免生成与 TPR 不匹配的子集轨迹；`gmx msd` 才使用指定的 MSD 组，并在 no-jump 轨迹上关闭再次 PBC 处理。manifest 中的扩散系数为全保留窗口线性拟合的初步值，不能替代人工确认线性区间后的物性报告。

## 边界

- 首版默认原子组为 `System`，不自动猜测离子、溶剂或配位原子组。组分特异性 RDF、配位数和 MSD 将以受控 index group/选择规则扩展。
- 长轨迹的速度相关函数、Green-Kubo 电导和黏度需要在 PROD 前选择高频输出档，不能由已有 1 ps XTC 轨迹补救。
- 运行助理可以读取 manifest 并解释结果；后处理模块和后续 Analysis Agent 均不得改写 MD 配置、原始轨迹或拓扑。
