# 运行环境与工具注册

## 目标

环境注册表统一负责外部工具发现、来源优先级、能力预检和受限子进程环境。发现到可执行文件只表示满足启动前提，不表示整条科学流程已验收。

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

## 发现优先级

外部二进制按以下顺序解析：

1. 显式 `WILLY_*` 环境变量。
2. 项目 `.env` 中的白名单变量。
3. 启动 Willy 的进程继承的 `PATH` 或工具专属受限默认目录。

GROMACS 使用 `WILLY_GMX_BIN`；Gaussian 使用 `WILLY_G16_BIN`、`WILLY_G09_BIN` 和 `WILLY_G09_FORMCHK_BIN`；ORCA 使用 `WILLY_ORCA_HOME`；OPLS 相关工具使用 `WILLY_LIGPARGEN_BIN`、`WILLY_BOSS_HOME`、`WILLY_OBABEL_BIN` 和 `WILLY_CSH_BIN`。Packmol、Multiwfn 和 Sobtop 由完整源码检出内的项目路径解析。

预检可以将自动发现的外部工具建议写入缺失的本机 `.env` 白名单项，但不得覆盖已有 `.env`、进程环境或显式 ORCA 设置。它不写入 Vendor 路径，不输出绝对路径、环境变量值、日志或密钥。

## 预检与执行

配置页预检按量子、拓扑和模拟分组展示工具状态及来源。创建 run 后，执行模块仍必须按实际步骤重新进行强制预检。工具需要同时满足可发现、可执行、短时启动探针和该步骤的输入契约；动态加载器、ABI 或依赖库失败统一归类为 `runtime_unavailable`，不触发参数重试。

所有外部程序由受管进程组执行。停止或超时按 `SIGINT -> SIGTERM -> SIGKILL` 升级，私有审计仅记录受控阶段、退出码、信号和 checkpoint 事实。公开状态只给出脱敏的操作级建议。

## 资源边界

启动前读取当前进程可用 CPU affinity；默认 `nproc` 为 `min(8, 可用核数)`。超过可用容量的显式值会写回可用上限并提示用户，fork 与运行时快照也使用同一规范化规则。资源规范化防止无效并行配置，但不保证量子软件在任意宿主机的并行稳定性。

## LLM 配置与秘密

应用只支持用户自配 OpenAI-compatible LLM。配置页使用当前表单值测试 Chat Completions 和实际工具调用，不保存测试值，也不自动补 URL 路径。API Key、令牌、私钥和供应商原始响应不得进入 run 状态、日志、测试报告或文档。

托管网关和远程执行不属于当前产品入口；对应文档只定义未来恢复功能时必须遵守的安全边界。
