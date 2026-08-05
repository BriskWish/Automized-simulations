# 外部运行环境注册设计

> 维护范围：`src/willy/env_registry.py`、`src/willy/env_checker.py` 及外部软件调用适配器。
> 状态：核心实现完成；启动级全局缓存、版本兼容性判定和自动后端重规划待后续评估。
> 最后更新：2026-08-05。

## 1. 目标与边界

Willy 同时依赖项目内置程序和用户安装的外部程序。当前外部程序的解析分散在
`env_checker.py`、ORCA 工具模块及各执行适配器中，分别使用 `PATH`、软件原生
环境变量和约定目录。该差异会导致预检与实际执行的路径选择不一致。

本设计引入 **`env_registry`**：它是“外部运行环境的发现、验证和子进程环境构造”
的唯一事实来源。此名称刻意不使用 `tools_registry`，以避免与 LLM function-calling
接口 `tools_*`、各 `toolist_*.py` 模块混淆。

`env_registry` 不负责以下内容：

- 不读取或执行用户的 `.bashrc`、`.profile` 等 shell 配置文件；应用只继承启动进程的环境。
- 不解析任务级模拟参数；这些仍只来自 `config.json`。
- 不存储 API key，也不把任意 `.env` 内容传递给外部子进程。
- 不替代内置软件的固定路径和发布完整性检查。

## 2. 受管工具与配置名

项目内置软件由项目根目录推导，不能因环境变量改变位置：Sobtop、Packmol、OpenBabel。
以下外部软件由 `env_registry` 受管：

| 工具 ID | 标准覆盖变量 | 兼容读取 | 自动发现 |
|---|---|---|---|
| `g16` | `WILLY_G16_BIN` | 无 | `PATH:g16` |
| `formchk` | `WILLY_FORMCHK_BIN` | 无 | `PATH:formchk` |
| `orca` | `WILLY_ORCA_BIN`、`WILLY_ORCA_HOME` | `ORCA_DIR` | `PATH:orca` |
| `orca_2mkl` | `WILLY_ORCA_2MKL_BIN`、`WILLY_ORCA_HOME` | `ORCA_DIR` | ORCA 同目录或 `PATH:orca_2mkl` |
| `multiwfn` | `WILLY_MULTIWFN_BIN` | `MULTIWFN_BIN` | `PATH:Multiwfn` |
| `gmx` | `WILLY_GMX_BIN` | 无 | `PATH:gmx` |
| `ligpargen` | `WILLY_LIGPARGEN_BIN` | 无 | `PATH:LigParGen` |
| `boss` | `WILLY_BOSS_HOME` | `BOSSdir` | `~/boss/boss`（目录存在时） |

同一工具的路径解析优先级固定如下：

```text
启动进程中的 WILLY_* 显式覆盖
-> .env 中白名单的 WILLY_* 覆盖
-> 软件原生兼容变量（ORCA_DIR、MULTIWFN_BIN、BOSSdir）
-> 受限的约定默认目录
-> PATH 自动发现
-> unavailable
```

`.env` 只加载 `DEEPSEEK_API_KEY`、`WILLY_LLM_*`、受管工具的 `WILLY_*` 与 `WILLY_SERVER_PORT` 白名单键；
启动进程环境优先于 `.env`。`ORCA_DIR`、`BOSSdir`、`MULTIWFN_BIN` 在过渡期继续支持，
但新部署应使用 `WILLY_*` 名称。

### 2.1 LLM 服务配置

LLM 不是外部科学软件，因而不属于 `env_registry` 的工具清单，也不会出现在
`environment_report.json`。`src/willy/llm_config.py` 单独管理唯一支持的适配器：
OpenAI-compatible Chat Completions API。

| 配置项 | 用途 | 默认/兼容行为 |
|---|---|---|
| `WILLY_LLM_API_KEY` | API 密钥 | 优先于旧 `DEEPSEEK_API_KEY` |
| `WILLY_LLM_BASE_URL` | 服务根 URL | 缺省为 `https://api.deepseek.com` |
| `WILLY_LLM_MODEL` | 所用模型 | 缺省为 `deepseek-v4-pro` |

每项均遵循“启动进程环境 → 项目 `.env` → 默认值”的顺序；密钥缺失时，Config Agent、
层级修复 Agent 和运行助理均降级为无 LLM 模式。旧 `DEEPSEEK_API_KEY` 仅作读取回退，
新配置页保存时会写入三项 `WILLY_LLM_*` 值并移除旧键。Base URL 仅接受无查询参数的完整
`http(s)` 地址，因此本地 vLLM、Ollama 或 LM Studio 的兼容端点可用。

当前不会做启动时联网探测。配置页的“测试连接”是用户显式触发的一次性检查：它只使用当前表单
中的 API Key、Base URL 和 Model，不读写 `.env`，并在 12 秒上限内强制模型调用零参数
`willy_connection_check`。为避免在页面回显凭据，已保存的 API Key 不会回填；重启后再次测试时，
用户必须重新填写 Key，否则返回 `invalid_test_api_key`，不会错误地把该本地校验归因于服务端。
一次成功响应同时证明 Chat Completions、所选模型与 function calling 可用；保存动作仍只写本机
`.env`，且需要重启前端使 Agent 重载 client。

Base URL 经格式校验后按用户原值使用，绝不自动补 `/v1` 或重写路径。版本前缀是 OpenAI-compatible
服务的常见差异：部分服务要求 `/v1`，而另一些服务（包括默认 DeepSeek 地址）不要求。配置页对此
提供非强制提示；对 HTML、非 JSON 与 404 响应，界面会建议检查路径并在常见情况下追加 `/v1`。
公开结果固定为 `ok`、`code`、`message`、`suggestion` 四项；不会包含 API Key、请求头、原始响应、
URL 查询参数或完整异常。

| `code` | 用户文案 | 解决提示 |
|---|---|---|
| `invalid_url` | Base URL 格式无效 | 使用完整 http(s) 地址，不含查询参数。 |
| `invalid_test_api_key` | 本次连接测试的 API Key 为空或格式无效 | 重新粘贴 Key；测试不读取已保存的 Key。部分服务还要求 Base URL 以 `/v1` 结尾。 |
| `non_json` | 服务未返回 OpenAI API 响应 | 检查 Base URL；常见修复是在末尾追加 `/v1`。 |
| `authentication` | API Key 无效或无权访问该服务 | 重新复制 Key，确认账户与模型权限。 |
| `not_found` | 未找到 API 端点或模型 | 检查 `/v1` 路径和 Model 名称。 |
| `rate_limited` | 服务限流或余额不足 | 稍后重试，检查配额、并发和账户余额。 |
| `network` | 无法连接 LLM 服务 | 检查网络、代理、防火墙和服务可达性。 |
| `protocol` | 服务不兼容 OpenAI Chat Completions | 更换 OpenAI-compatible 端点或联系服务提供方。 |
| `tool_call_unsupported` | 当前模型不支持 function calling | 更换支持工具调用的模型；Willy 的配置与自动修复依赖该能力。 |

API key 永不写入状态、事件、manifest、日志或前端状态文本。

## 3. 公共模型

`env_registry` 提供不可变的解析结果，而非修改全局 `os.environ`：

```python
ResolvedTool(
    tool_id="gmx",
    status="available",
    executable=Path("..."),
    home=Path("..."),
    source="path",
    version=None,                # 字段已预留，当前不主动执行版本探测
    public_reason=None,
)
```

状态仅允许：`available`、`missing`、`misconfigured`、`not_executable`、
`version_incompatible`、`runtime_unavailable`。路径、环境变量值和原始命令输出是
内部诊断信息，不能写入前端事件、LLM prompt 或公开 manifest。

每个工具还可生成专用的子进程环境。例如 ORCA 在该副本中补充
`LD_LIBRARY_PATH`；LigParGen 只在该副本中设置兼容的 `BOSSdir`。不再由预检把默认值
写入全局环境。

## 4. 预检与运行时序

预检分为三层：

1. **能力发现（已实现）**：每个新 run 创建后、进入步骤前，非阻塞扫描全部外部工具并写入脱敏报告；缺少未选中的工具不使服务不可用。
2. **方案预检（已实现）**：配置与后端确定后，按该流程需要的模块检查所需工具。当前没有自动切换后端；必需工具不可用时在执行前返回结构化失败。
3. **工步预检**：进入外部命令前，再验证当前输入、附属文件、可执行权限和子进程环境。软件可发现不代表当前任务一定可运行。

预检报告包含工具 ID、脱敏状态、来源（`willy_env`、`dotenv`、`legacy_env`、`default`、`path`）
和预留版本字段。每个 run 在其目录保存 `environment_report.json`；该文件不含
绝对路径、API key、环境变量值或原始软件输出。状态机和前端只呈现操作级消息，例如
“ORCA 未配置，已选用 Gaussian”。

### 4.1 执行模块与依赖归属

`step_registry.py` 还维护 `EXECUTION_MODULE_REGISTRY`。它以稳定的执行模块 ID
（例如 `struct_g16`、`topo_opls`、`box`、`gromacs_eq`）关联所属主流程 step、外部
`tool_id` 与内置依赖 ID。一个 step 可以有多个后端执行模块，因此该表不能由前端标签
反推。

`env_checker.py` 继续提供兼容的 `check_all()`、`check_module()` 和 `ensure()`，但其
`DepResult.needed_by` 由该注册表反向生成，而不是在预检列表中重复维护字符串。新增或
迁移执行器时必须先在执行模块注册表登记依赖，再增加外部 `ToolSpec` 或内置依赖展示项。
这样不会改变既有模块 ID 或预检阻断时机，也避免工具选择和依赖报告出现漂移。

## 5. 模块职责与迁移

- `env_registry.py`：工具声明、路径解析、校验、子进程环境构造和脱敏能力报告。
- `step_registry.py`：维护主流程 step 及其执行模块、外部工具和内置依赖的归属关系。
- `env_checker.py`：保留现有 `check_all()`、`check_module()`、`ensure()` API，按执行模块
  注册表生成依赖归属并适配 `env_registry` 的报告，避免现有调用方一次性破坏。
- 量子、拓扑、模拟模块：只向 registry 请求可执行文件和运行环境，不直接调用
  `shutil.which()` 或读取软件环境变量。
- `PipelineOrchestrator`：在 run 注册后写入脱敏 `environment_report.json`；在确定步骤
  方案后执行模块级预检。
- Run Assistant：通过 `tools_get_environment_run` 只读获取脱敏能力摘要，可解释“工具不可用”，
  没有环境变量读写或任意路径读取权限；当前不会自动改用后端。

迁移期间，`env_checker` 继续是所有外部依赖检查的唯一入口；registry 是它的内部实现，
不是第二套平行检查器。

## 6. 验收与回归

必须覆盖：

- 标准变量、旧变量、默认目录和 `PATH` 的解析优先级。
- `.env` 白名单加载与进程环境覆盖关系。
- OpenAI-compatible LLM 的通用变量、DeepSeek 兼容回退、URL/模型校验和密钥脱敏。
- 表单值的一次性连通性测试：强制工具调用、12 秒上限、八类公开错误、无持久化与无敏感信息泄露。
- 缺失、路径不存在、目录误作二进制、不可执行、必要附属程序缺失等状态。
- ORCA 动态库环境和 LigParGen/BOSS 环境只作用于目标子进程。
- 必需工具缺失会阻断相应模块；未选中或存在替代链路的工具缺失不会阻断新任务。
- `environment_report.json`、状态事件和 Run Assistant 数据中不含绝对路径、密钥、环境变量值或底层日志。
- 现有 Gaussian、ORCA、Sobtop、LigParGen、GROMACS 测试保持通过；外部真实 smoke 仍是独立验收，不由替身测试替代。

已由 `tests/test_env_registry.py` 覆盖标准变量、`.env`、旧变量兼容、子进程环境和脱敏报告；
外部真实 smoke 仍是独立验收，不由替身测试替代。
