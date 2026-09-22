# Willy 测试与质量门禁

## 目标

SMD 的代码验收覆盖内置/人工库隔离、并发登记、默认编号、精确拒重、数值边界、
HTTP 查询/登记、方案快照与 ORCA 拒绝、优化/单点 SCRF 替换及多行 route。
`python3 -m tests.tools.g16_smd_generic_acceptance` 是显式真实 G16 验收：在独立临时目录
使用单核、1 GB、300 秒上限执行 EC 优化，记录正常终止、收敛与参数读入证据；不进入默认 pytest。
该验收只证明 Generic/Read 二参数写法可执行，不证明完整 SMD 参数化精度。
前端 `node frontend/scripts/solvent-check.mjs` 使用接口替身验证溶剂库交互，不替代真实服务验收。

受限沙盒中若 `TestClient` 卡在 `anyio` portal，应先用空 FastAPI 最小复现与线程栈定位，
在获准的普通进程环境重跑；不要将事件循环唤醒受限误判为业务死循环或通过跳过测试掩盖。
诊断与全量命令应设置总时限，并启用 `-o faulthandler_timeout=30`，避免无限等待。

测试分为确定性代码契约、受控 LLM 行为、真实外部工具和人工浏览器验收。低层通过不替代高层证据；真实工具可启动也不替代受管十步流程。

Python 运行时回归覆盖支持范围与安装元数据一致性、Web 启动前缺依赖提示、父子进程导入和源码检出一致性、
超时及脱敏、虚拟环境符号链接与空格路径，以及 PATH 指向其他 Python 时的实际受控子进程启动。
普通启动、确认重跑、`/resume`、`/fork` 和 LigParGen 私有兼容包装器均须保持父解释器；测试不得运行科学计算。
版本边界的参数化检查不替代目标机 Python 3.10、3.11、3.12 的独立安装与运行验收。

本机三版本真实安装与冒烟由 `tests/tools/g07_python_matrix_acceptance.py` 和
`tests/tools/g07_python_runtime_probe.py` 执行；必须使用隔离源码副本、独立 venv、HOME 和临时目录。
实际 Web 服务只绑定回环地址，两次独立启动后均通过 `/resume` 创建无科学计算的受管 runner，核对解释器、
venv、启动门和锁交接；缺依赖场景使用真正未安装依赖的空 venv，不用修改版本号或导入替身冒充。
错误矩阵明确核对预期状态和脱敏，含输入、缺依赖、ABI、Packmol、grompp、EQ、PROD，并单独检查无效 LLM 配置。
扩大回归失败必须保留失败用例与计数，不因 G-07 核心冒烟通过而豁免或隐藏；历史结果以受控报告为准。

GROMACS 辅助执行器回归覆盖 20 秒上限、较短时限、最多三次超时尝试、输入重放、用户停止、非零退出不重试、子进程回收失败阻断、一次性结束审计和 `mdrun` 不受辅助限制。真实进程回归只启动短时 Python 替身，不运行科学软件。

EQ 非有限值回归覆盖 `NaN`、`+Inf`、`-Inf` 配置、保持段元数据、能量时间与数值，以及统计溢出。
温度、势能、密度或压力的已提取序列出现非有限值时，EQ 必须失败且不得通过 PROD 前置许可；
可选诊断项缺失仍不阻断；成功提取后，时间证据同样不得异常。

EQ 最后 1 ns 的五段覆盖回归核对不同 EQ 时长和输出频率、边界唯一归属、全采样加权统计及固定窗口配置；
缺段、内部缺点、提前结束、短片段、五个瞬时点、乱序、重复、偏离网格、目标保持段不足、MDP/元数据不一致、
不完整旧许可和验收产物指纹变化均不得授予 PROD 许可。五段统计不新增分段温度/势能阈值，仍执行完整窗口的既有判据。

电荷容差回归覆盖 `grompp` 单一 Ewald 净电荷 warning 的 `±0.15 e` 两个端点、区间内正负残差、
正负超界与额外 warning；文档一致性检查同时约束阈值单位及配置阶段 `charge_imbalance` 的独立边界。
受控 `-maxwarn 1` 不修改原始电荷，不绕过用户确认或 MD 阶段许可；替身回归不替代真实体系验收。

原样续跑回归覆盖 Step 1--10 的输入清单、Gaussian/ORCA 输入差异、拓扑组分映射与组装副本、
上游许可和产物非空的独立门禁，以及同大小/不同大小修改均被哈希门拒绝、参数变化必须分支。
还覆盖失效路径、连续完成前缀、MD 锁、待确认动作、旧停止请求清理、新停止请求保留、启动失败回退，
以及服务端检查后文件被清空时子进程保持完成前缀并终结重试。全部使用本地 fixture 与执行替身，不运行科学计算。

步骤契约回归覆盖逐步校验、旧产物先归档、空跑成功不得借用旧输出、归档中断恢复/冲突保护与收尾归档。
新输入回归覆盖 LLM 文件/步骤白名单、非空与格式、决策后版本变化、单步骤豁免及无 LLM 时拒绝。
分支回归覆盖上下文完整复制、父工程字节不变、内部链接重绑定、外部链接拒绝、归档输入恢复及嵌套分支来源。
启动门回归使用真实短时 Python 子进程，证明 PID/登记/线程/交接失败不会遗留执行中的科学作业。

报错恢复回归覆盖三种可恢复状态的原参数入口、无 LLM 建议恢复、稳定配置与合法状态路径，
以及修改方案后按钮换绑、连续两次修改后仅最新参数进入子工程、旧 action/revision 拒绝和父工程不变。
服务端与界面接口还覆盖问句/否定不执行、修订失败保留原方案、启动互斥、并发确认失效与写盘中断恢复。
这些替身回归不替代真实浏览器和真实 LLM 验收。

故障复查回归覆盖七种非完成状态的升级出口、Step 1--10 未知异常的位置记录、停止失败关联原因、
人工完成声明绑定、环境/原哈希恢复后的自动更新、旧版本和并发故障保护、活进程/MD 锁/启动锁/心跳阻断，
以及进程组独立于流水线进程的存活登记。只复查和迁移，不启动科学步骤；已完成工程与父工程控制权限不回退或继承。

前端恢复控件的本地替身检查：`node frontend/scripts/recovery-check.mjs`。十一组检查覆盖三种可恢复状态、
双击互斥、修订失败保留草稿、最新方案确认、409 刷新、多选保护、跨工程迟到响应和人工完成声明只回到 `aborted`；另运行
`npm --prefix frontend run build` 验证打包。替身检查不计入 pytest 台账，也不冒充浏览器验收。

当前测试台账为 `1968 pytest + 18 LLM = 1986`。

## 测试层级

| 层级 | 范围 | 通过标准 |
|---|---|---|
| 单元与契约 | schema、状态机、文件契约、权限、脱敏、资源边界 | 断言可重复，且不依赖本机科学软件。 |
| 前端与浏览器 | `temp__`/`plan__` 方案恢复、方案确认、运行状态、停止、故障后状态调和、`/resume`、`/fork`、`/switch` | 用户可见状态与服务端 run 事实一致，不触发越权操作。 |
| LLM | 工具调用、字段校验、错误分类和受控恢复 | 模型只能调用白名单工具；高影响动作必须取得明确确认。 |
| 外部工具 | GROMACS、量子、拓扑、Packmol | 必须通过 Willy 的预检、受管子进程和产物校验。 |
| 人工验收 | 目标机、真实浏览器和真实凭据环境 | 保存脱敏结论，不以截图或裸命令替代受管证据。 |

## 常用命令

```bash
# 默认离线回归
python3 -m pytest -q

# 项目隔离安装与前端构建（首次下载项目私有 Node 22）
python3 scripts/bootstrap.py

# 只读检查内置 Packmol/Multiwfn 的宿主动态库，不安装系统包
python3 scripts/bootstrap.py --check-system-libs

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

G-07 三版本工具 `python3 -m tests.tools.g07_python_matrix_acceptance` 可用 `--suite full` 执行默认全量 pytest（不启用外部科学工具、真实 LLM 或浏览器 opt-in），省略时仍执行核心相关子集。每版须使用独立安装的解释器和同一冻结源快照；快照包括测试、前端、Vendor 清单负载及 Git 版本化结构样例，不复制实际 run、用户配置、上传结构或 Python 环境。报告同时记录完整源文件散列、跳过原因、测试后快照完整性和工作树变更，不能以少数入口文件未变代替完整版本覆盖。

[`g07_python_matrix_20260910.json`](../tests/reports/audits/g07_python_matrix_20260910.json) 保存本机三版本完整复验：复用 2026-09-08 的独立环境，最后共同快照含 1475 项 pytest，每版 1465 通过、10 跳过，无失败；跳过项为 8 项外部科学工具、1 项真实 LLM 和 1 项浏览器。真实 Web 重启、空依赖提示、受管续跑及受控错误矩阵通过；独立前端构建和 10 组恢复控件替身检查通过，生成产物与最终快照一致。四轮复验期间仍有并发代码与测试变更，报告因此保留 `current_worktree_covered=false`、`current_version_accepted=false` 和未覆盖文件清单，不能将冻结快照通过称为当前完整版本获准放行。当前台账包含后续新增测试，不代表它们已在这次矩阵中执行。

首次三版本环境准备及较早子集结果保留于 [`g07_python_matrix_20260908.json`](../tests/reports/audits/g07_python_matrix_20260908.json)；两份证据均不关闭其他宿主平台、真实 LLM、浏览器或完整科学流程的验收要求。

G-09 的 [`g09_eq_coverage_20260906.json`](../tests/reports/audits/g09_eq_coverage_20260906.json) 由 `tests/tools/g09_eq_coverage_acceptance.py` 对真实 EQ 产物的隔离副本调用受管 GROMACS 能量提取和当前验收代码生成，记录正常/提前结束的许可结果、实现与产物散列、回归摘要及原产物未变核验。它证明本次覆盖门禁，不是新执行 mdrun、PROD 或十步链路的证据，不更新历史 run 的许可。

## 发布门禁

发布前必须满足：

1. 默认回归、编译检查和文档一致性检查通过。
2. 测试目录由当前收集结果重新生成并与台账一致。
3. 与改动范围对应的外部 smoke 或人工验收已完成，未完成项保留在 `project_gap_analysis.md`。
4. 公开文档、架构文档、接口文档和缺口台账对同一能力没有冲突承诺。
