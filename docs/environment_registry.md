# 运行环境与工具注册

## 目标

环境注册表统一负责外部工具发现、来源优先级、能力预检和受限子进程环境。`python_runtime.py` 负责 Web 与受管 Python 子进程的解释器和导入检查；两类检查都不表示整条科学流程已验收。

## 支持范围

| 组件 | 用途 | 当前要求 |
|---|---|---|
| Python | 应用与测试运行时 | 3.10--3.12 |
| GROMACS | EM、EQ、PROD | 用户安装的 2023--2025 兼容版本 |
| Gaussian 16 | G16 量子路径 | 用户合法安装，含同安装的 `formchk` |
| Gaussian 09 | 保留接口 | 用户合法安装；不列入可靠全链路承诺 |
| ORCA | ORCA 量子路径 | 用户安装的 6.x，含 `orca_2mkl` |
| Packmol | 初始建盒 | 项目 Vendor 负载，宿主需满足 ABI 和运行时依赖 |
| Multiwfn | RESP、格式转换 | 项目 Vendor 负载，宿主需提供所需图形运行时库 |
| Sobtop | GAFF/UFF 拓扑 | 完整源码检出中的 Vendor 负载；使用者自行遵守上游条件 |
| LigParGen/BOSS/Open Babel/C shell | OPLS-AA 路径 | 用户自行安装完整依赖 |

第三方组件的授权、下载、再分发和引用要求由上游决定；Willy 不拥有也不授予这些组件的权利。当前唯一支持的交付方式是完整源码检出后可编辑安装。

## 项目隔离安装

完整源码检出使用 `python3 scripts/bootstrap.py` 建立项目私有运行时。该脚本仅写入检出根目录的
`.venv/`、`.willy/`、`frontend/node_modules/` 与 `frontend/dist/`：Python 包通过 `.venv` 中的
`pip install --editable` 安装；Node 22 运行时下载并校验后位于 `.willy/toolchains/`；pip/npm 缓存、
临时 HOME 和 npm prefix 同样受限于 `.willy/`。脚本不修改调用者的 Python、Node、PATH、shell 配置、
用户级包缓存或系统包管理器。默认 Node 版本可由 `WILLY_BOOTSTRAP_NODE_VERSION` 覆盖，覆盖值只作用于
该检出中的 `.willy/toolchains/`。

内置 Packmol 与 Multiwfn 不是静态链接负载，仍由宿主动态加载器提供 ABI 库。脚本以 `ldd` 只读检查二者；
发现 `libgfortran.so.5` 或 `libXm.so.4` 缺失时，Ubuntu/Debian 的人工修复建议为
`sudo apt install -y libgfortran5 libxm4`。该建议不是脚本行为：不得由 Willy 自动调用 `sudo`、
`apt` 或其他系统包管理器，也不得自动替换 `libc`。缺库会使标准引导以非零状态结束，虽然项目内 Python
环境与前端构建已完成；执行科学步骤前必须由宿主所有者完成相应系统修复。

## 发现优先级

### Python 运行时

Web 支持 Python 3.10--3.12，与安装元数据的 `>=3.10,<3.13` 和引导脚本共用同一版本边界；
不要求 Anaconda，也不绑定 Python 3.11。用户选择系统、venv 或 conda 等环境，安装依赖后启动应用。
Web 在加载工作台前执行强制检查：当前进程依赖须能导入，同一解释器的独立子进程也须能导入核心依赖和
当前源码检出的 Willy。后者不继承 Web 临时加入的 `sys.path`，不能以工作台自身可导入来掩盖未完成的可编辑安装。

`app.py --check-runtime` 只检查，不启动 Web；未安装工程时可直接执行 `src/willy/python_runtime.py`。
探针不访问 LLM 或运行科学工具，不安装软件、不修改环境变量、不扫描 Anaconda 私有目录，子进程检查设有超时。
公开报告只包含 Python 版本、支持范围、依赖名称和脱敏状态，不包含解释器路径、原始异常或环境变量值。
依赖版本约束由安装器按 `pyproject.toml` 解析；导入可用性检查不替代 `pip check` 或受管十步验收。

普通启动、确认调参重跑、`/resume` 与 `/fork` 共用 `pipeline_launch.pipeline_command()`，使用父进程
`sys.executable` 原值和流水线入口绝对路径。不得再按 PATH 查找 `python3`，也不得解析虚拟环境解释器符号链接后
改用底层系统解释器。LigParGen 的私有 Open Babel 兼容包装器同样沿用父 Python；外部 LigParGen 本身仍按科学工具配置解析。
解释器不是自动发现后写入 `.env` 的科学工具默认项，项目不提供 `WILLY_PYTHON_BIN` 隐式覆盖。

### Python 隔离冒烟

`tests/tools/g07_python_matrix_acceptance.py` 以独立源码副本和 venv 检查 Python 3.10、3.11、3.12。
解释器和依赖须经人工授权准备；验证脚本不自动下载、替换系统 Python 或使用开发环境的 site-packages。
副本不包含真实 `md_run/`、根配置、实际 dotenv、用户上传结构、用户虚拟环境或符号链接；公开 `.env.example` 模板可保留，HOME、缓存和临时目录另行隔离。

验收包含 `pip check`、编译、实际依赖导入、空依赖环境的启动提示、回环地址 Web 服务的两次独立启动，
以及重启后通过真实 `/resume` 入口创建受管子进程。续跑使用明确的无科学计算 runner，只验证父解释器、
venv、启动门、锁交接和持久化工程绑定；故意放置的 PATH 替身不得被调用。
Packmol 只做真实启动探测，其他引擎与 LLM 错误采用受控注入；不代表真实模拟、LLM 连通性或其他宿主平台验收。

当前本机证据见 [`g07_python_matrix_20260910.json`](../tests/reports/audits/g07_python_matrix_20260910.json)，环境沿用 2026-09-08 的独立安装而非重新安装。
`--suite full` 执行默认全量回归；报告区分 `g07_smoke_passed` 与包含回归的 `passed`，不能将核心冒烟通过表述为全部回归通过；G-07 的发布目标复验边界仍保留。
两者都只适用于三版本共同使用、且验收前后未改变的冻结源码快照。`current_worktree_covered` 和
`source_changes_after_snapshot` 单独标识后续工作树变更，不能用旧快照的通过结论为未复验的新代码背书。
本次四轮快照均通过，但执行期间工作树持续变化，故 `current_version_accepted=false`；须待源码和构建产物稳定后重新冻结复验。

### 科学软件

外部二进制按以下顺序解析：

1. 显式 `WILLY_*` 环境变量。
2. 项目 `.env` 中的白名单变量。
3. 启动 Willy 的进程继承的 `PATH` 或工具专属受限默认目录。

GROMACS 使用 `WILLY_GMX_BIN`；Gaussian 使用 `WILLY_G16_BIN`、`WILLY_G09_BIN` 和 `WILLY_G09_FORMCHK_BIN`；ORCA 使用 `WILLY_ORCA_HOME`；OPLS 相关工具使用 `WILLY_LIGPARGEN_BIN`、`WILLY_BOSS_HOME`、`WILLY_OBABEL_BIN` 和 `WILLY_CSH_BIN`。Packmol、Multiwfn 和 Sobtop 由完整源码检出内的项目路径解析。

预检可以将自动发现的外部工具建议写入缺失的本机 `.env` 白名单项，但不得覆盖已有 `.env`、进程环境或显式 ORCA 设置。它不写入 Vendor 路径，不输出绝对路径、环境变量值、日志或密钥。

## 预检与执行

配置页预检先展示 Python 运行时，再按量子、拓扑和模拟分组展示工具状态及来源；总就绪结果同时要求 Python 检查通过。配置页报告仍是建议性检查，不改变任务状态或直接授予启动权限；它不会保存 Python 路径或改写 PATH。Web 启动前的 Python 检查则是强制入口条件。创建 run 后，执行模块仍必须按实际步骤重新进行强制科学依赖预检。工具需要同时满足可发现、可执行、短时启动探针和该步骤的输入契约；动态加载器、ABI 或依赖库失败统一归类为 `runtime_unavailable`，不触发参数重试。

外部计算程序由受管进程组执行。`mdrun` 和量子/拓扑程序的停止或超时按 `SIGINT -> SIGTERM -> SIGKILL` 升级。非 `mdrun` 的 GROMACS 辅助命令单次执行最多 20 秒，仅超时可重试，总计最多 3 次（含首次）；辅助命令超时或停止时直接终止进程组，另有每次最多 2 秒的有界回收，回收失败禁止再次启动。版本/依赖探针沿用更短时限。完整规则见 `simulation.md`。私有审计仅记录受控阶段、退出码、信号和 checkpoint 事实，公开状态只给出脱敏的操作级建议。

## 资源边界

启动前读取当前进程可用 CPU affinity；默认 `nproc` 为 `min(8, 可用核数)`。超过可用容量的显式值会写回可用上限并提示用户，fork 与运行时快照也使用同一规范化规则。资源规范化防止无效并行配置，但不保证量子软件在任意宿主机的并行稳定性。

## LLM 配置与秘密

应用只支持用户自配 OpenAI-compatible LLM。配置页使用当前表单值测试 Chat Completions 和实际工具调用，不保存测试值，也不自动补 URL 路径。API Key、令牌、私钥和供应商原始响应不得进入 run 状态、日志、测试报告或文档。

托管网关和远程执行不属于当前产品入口；对应文档只定义未来恢复功能时必须遵守的安全边界。
