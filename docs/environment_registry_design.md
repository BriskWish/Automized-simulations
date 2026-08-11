# 外部与内置运行环境注册设计

> 维护范围：`src/willy/env_registry.py`、`src/willy/env_checker.py` 及外部软件调用适配器。
> 状态：核心实现完成；启动级全局缓存、版本兼容性判定和自动后端重规划待后续评估。
> 最后更新：2026-08-11。

## 1. 目标与边界

Willy 同时依赖项目内置程序和用户安装的外部程序。当前运行程序的解析分散在
`env_checker.py`、ORCA 工具模块及各执行适配器中，分别使用固定 vendor 路径、`PATH`、软件原生
环境变量和约定目录。该差异会导致预检与实际执行的路径选择不一致。

本设计引入 **`env_registry`**：它是“运行环境的发现、验证和子进程环境构造”
的唯一事实来源，统一覆盖外部安装和内置负载。此名称刻意不使用 `tools_registry`，以避免与 LLM function-calling
接口 `tools_*`、各 `toolist_*.py` 模块混淆。

`env_registry` 不负责以下内容：

- 不读取或执行用户的 `.bashrc`、`.profile` 等 shell 配置文件；应用只继承启动进程的环境。
- 不解析任务级模拟参数；这些仍只来自 `config.json`。
- 不存储 API key，也不把任意 `.env` 内容传递给外部子进程。
- 不替代内置软件的固定路径和发布完整性检查。

## 2. 受管工具与配置名

项目内置软件由项目根目录推导，不能因环境变量改变位置：Sobtop、Packmol、OpenBabel、Multiwfn。
以下外部软件由 `env_registry` 受管：

| 工具 ID | 标准覆盖变量 | 兼容读取 | 自动发现 |
|---|---|---|---|
| `g16` | `WILLY_G16_BIN` | 无 | `PATH:g16` |
| `g09` | `WILLY_G09_BIN` | 无 | `PATH:g09` |
| `g09_formchk` | `WILLY_G09_FORMCHK_BIN` | 无 | G09 对应 `formchk` |
| `formchk` | `WILLY_FORMCHK_BIN` | 无 | `PATH:formchk` |
| `orca` | `WILLY_ORCA_BIN`、`WILLY_ORCA_HOME` | `ORCA_DIR` | `PATH:orca` |
| `orca_2mkl` | `WILLY_ORCA_2MKL_BIN`、`WILLY_ORCA_HOME` | `ORCA_DIR` | ORCA 同目录或 `PATH:orca_2mkl` |
| `multiwfn` | 无（固定内置负载） | 无 | `vendor/multiwfn/.../Multiwfn` |
| `gmx` | `WILLY_GMX_BIN` | 无 | `PATH:gmx` |
| `ligpargen` | `WILLY_LIGPARGEN_BIN` | 无 | `PATH:LigParGen`；执行时以私有兼容入口调用已配置的 `obabel` |
| `obabel` | `WILLY_OBABEL_BIN` | 无 | `PATH:obabel`；必须是含格式插件和数据文件的完整 Open Babel 安装 |
| `csh` | `WILLY_CSH_BIN` | 无 | `PATH:csh`；BOSS 运行脚本所必需 |
| `boss` | `WILLY_BOSS_HOME` | `BOSSdir` | `~/boss/boss`（目录存在时） |

同一工具的路径解析优先级固定如下：

```text
内置固定负载（Multiwfn、Sobtop、Packmol、OpenBabel）
-> 启动进程中的 WILLY_* 显式覆盖（仅适用于外部工具）
-> .env 中白名单的 WILLY_* 覆盖
-> 软件原生兼容变量（ORCA_DIR、BOSSdir）
-> 受限的约定默认目录
-> PATH 自动发现
-> unavailable
```

`.env` 只加载 `DEEPSEEK_API_KEY`、`WILLY_LLM_*`、受管外部工具的 `WILLY_*` 与
`WILLY_SERVER_PORT` 白名单键；启动进程环境优先于 `.env`。Multiwfn 不读取环境变量，
旧的 `WILLY_MULTIWFN_BIN`/`MULTIWFN_BIN` 即使存在也不会改变内置执行目标。

### 2.1 运行版本基线与兼容目标

下表是 2026-08-10 在当前开发机取得的事实快照，不等同于所有部署环境均已
验收。项目的 `pyproject.toml` 只声明直接 Python 依赖的最低版本；没有锁文件的
依赖必须在发布环境中以 constraints 或 lock 文件固定。

| 组件 | 项目声明 | 当前开发机 | 发布/验收目标 | 状态 |
|---|---|---|---|---|
| Python | `>=3.10` | 3.12.3 | x86_64 glibc Linux/WSL2 上的 3.10--3.12 | 当前基线 |
| cryptography | `>=42` | 41.0.7 | `>=42` | 当前环境漂移，发布前必须修正 |
| FastAPI | `>=0.115` | 0.140.0 | 满足声明的已锁定版本 | 当前基线 |
| httpx | `>=0.27` | 0.28.1 | 满足声明的已锁定版本 | 当前基线 |
| uvicorn | `>=0.30` | 0.51.0 | 满足声明的已锁定版本 | 当前基线 |
| Gradio | 未锁定 | 6.20.0 | 发布前锁定 | 当前基线，不构成长期承诺 |
| OpenAI SDK | 未锁定 | 2.48.0 | 发布前锁定 | 当前基线，不构成长期承诺 |
| py3Dmol | 未锁定 | 2.5.5 | 发布前锁定 | 当前基线，不构成长期承诺 |
| scikit-learn | 未锁定 | 1.9.0 | 发布前锁定 | 当前基线，不构成长期承诺 |
| pytest（test extra） | `>=8` | 9.1.1 | CI 以锁定版本运行 | 当前基线 |

| 外部组件 | 当前开发机 | 目标范围 | 证据边界 |
|---|---|---|---|
| GROMACS | 2025.0 | 计划验收 2023、2024、2025；代码理论下限为提供统一 `gmx` 前端的 5.0 | 仅 2025.0 有本机真实链路证据，其他版本不得表述为已支持 |
| Packmol | 21.2.3 | 固定当前内置二进制 | 当前 Linux x86_64/glibc 基线 |
| Sobtop | 内置 Linux x86_64 二进制 | 固定随仓库版本 | 当前 Linux x86_64/glibc 基线 |
| Open Babel | 3.1.1 | OPLS 使用完整 Open Babel 3；内置仅为精简运行时 | 完整格式插件仍由用户安装 |
| Multiwfn | 3.8(dev)，更新于 2025-02-14 | 固定 `3.8-dev-2025-02-14` 的 Linux x86_64 包 | 已接入 vendor，默认不依赖外部安装 |
| ORCA | 6.1.1 | ORCA 6.x | 外部安装、按许可证部署 |
| Gaussian 16 | 已发现，精确 revision 未探测 | 用户的合法 G16 安装 | 外部安装、按许可证部署 |
| Gaussian 09 | 当前未发现 | 用户的合法 G09 安装及同安装来源的 `formchk` | 外部安装；必须显式设置两项 G09 变量，不能混用 PATH 中的 G16 `formchk` |

当前 registry 只报告可执行性，尚未按这些版本目标做自动兼容性判定；因此版本表是
发布与验收基线，而不是运行时策略。Alpine（musl）和 ARM64 不属于当前内置二进制的
目标平台。

### 2.2 已接入的 Multiwfn vendor 负载

`vendor/multiwfn/linux-x86_64/3.8-dev-2025-02-14/` 保存了独立的最小运行负载：
`Multiwfn`、`settings.ini`、`LICENSE.txt`、`NOTICE.md` 与 `manifest.json`。完整上游
目录约 223 MB；本负载不包含示例、`MtwfnFld/` 或图形资源，核心二进制为 41.9 MB。

`settings.ini` 已清除构建机的 Gaussian、ORCA、`formchk`、`orca_2mkl` 与 DFT-D3 绝对
路径。Willy 只向 Multiwfn 传递已生成的 `.fchk` 或 `.molden`；量子程序仍由各自受管
适配器解析，因此内置负载不会隐式借用部署机器上的同名软件。

该负载已由 `env_registry` 固定解析，并在临时目录内完成当前工程实际使用的三项受控探查：
`.fch -> .xyz`、`.molden -> .fchk`、`.fch -> RESP .chg`。`settings.ini` 对该最小 RESP
样本的输出哈希未造成差异，但仍随包保留以固定默认行为。此结论只说明本机可用性，不替代
目标体系的科学验收或跨发行版验证。

`env_checker` 将 Multiwfn 作为 bundled dependency 归属到 `sp_g16`、`sp_g09`、
`sp_orca` 和 `chg_resp`；量子调用继续通过受管进程生命周期执行。Multiwfn 不再属于
外部安装前置条件，旧环境变量和 PATH 中的同名程序均被忽略，以保证跨机器结果可复现。

Sobtop 的可选 Multiwfn 菜单也固定为相对于 `vendor/sobtop/` 的内置可执行文件。当前
GAFF+UFF 主链使用已有 `.chg` 时不会调用该菜单，但它不能成为重新引入外部 PATH 依赖的
旁路。

当前接入门禁如下：

1. RESP 使用完整正常退出序列 `7 -> 18 -> 2 -> y -> 0 -> 0 -> q`，并要求
   `returncode == 0` 后才接受 `.chg`。
2. RESP、坐标导出和 molden 转 fchk 均通过 vendor 可执行文件回归，验证
   原子顺序、输出哈希、退出码和超时/停止语义。
3. 目标机必须提供 glibc、`libXm.so.4`、X11、OpenGL 等动态库；该二进制不适用于 Alpine
   或 ARM64。容器化时还必须为系统库建立 SBOM 与漏洞更新机制。

本地 `LICENSE.txt` 允许 Multiwfn 作为商业代码的免费组件分发，并要求使用结果发表时在
正文引用 2012 JCC 和 2024 JCP 论文。发行包必须保留许可证和引用通知；销售修改后的
Multiwfn 本体前必须取得原作者许可。

### 2.3 Vendor 清单与发布边界

`vendor/manifest.json` 是内置运行负载的发行清单：每个受管文件登记大小与 SHA-256，
并明确其来源、版本、许可证证据和发行状态。运行时不会读取该清单，也不会因为审计而启动
Packmol、Sobtop、Multiwfn 或 Open Babel；它只由下列只读命令在 CI 和发布复核中消费：

```bash
python3 scripts/verify_vendor_manifest.py
python3 scripts/verify_vendor_manifest.py --require-release-ready
```

前者必须始终通过，防止受管二进制和参数文件在未知条件下漂移。后者会将未补齐来源、版本或
许可证的组件视为发布阻塞。当前只有 Multiwfn 已达到 `release_ready`；Packmol、Sobtop、
精简 Open Babel 和遗留 `3Dmol-min.js` 均明确标为 `evidence_pending` 或
`exclude_from_release_artifact`，不能在正式发行前静默通过。Sobtop 示例目录也不属于受管
运行时；发布包必须在打包规则中排除它，或另行登记其来源、许可证与示例数据保留理由。

这项清单不替代外部科学软件许可证：Gaussian、ORCA、LigParGen、BOSS 与完整 Open Babel
仍由用户在合法环境中安装，且各 profile 的真实执行证据仍由 external smoke 负责。

### 2.4 当前开发机审计快照

2026-08-11 在不启动科学软件的条件下完成以下只读检查：Python `3.12.3`，Linux
`x86_64`/glibc `2.39`，GROMACS `2025.0`，`csh` 和系统 Open Babel 均可发现。registry
仍固定使用 vendor 内置 Multiwfn，不受本机 `/home/.../Multiwfn` 或 PATH 中同名程序影响。

当前 `cryptography==41.0.7`，低于 `pyproject.toml` 声明的 `cryptography>=42`，因此该开发机
不能作为正式发布环境；在真实验收运行期间不执行共享环境升级，发布前必须在干净环境安装并
记录满足约束的版本。`pip check` 另报告全局 `oslo-serialization`/`oslo-utils` 缺少
`tzdata`，这不是 Willy 的直接依赖，但说明当前解释器不是可复现的隔离发布环境。

环境基线命令应在发布候选副本中执行：

```bash
python3 -m pip check
python3 scripts/verify_vendor_manifest.py
python3 scripts/verify_vendor_manifest.py --require-release-ready
```

这些检查只读，不修改 `config.json`、`md_run/` 或 vendor 文件；`--require-release-ready`
必须在 Packmol、Sobtop、精简 Open Babel 及遗留资产的证据/排除工作完成后才允许通过。

### 2.5 LLM 服务配置

LLM 不是外部科学软件，因而不属于 `env_registry` 的工具清单，也不会出现在
run-local 的脱敏能力快照中。`src/willy/llm_config.py` 单独管理唯一支持的适配器：
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
和预留版本字段。每个新 run 将该快照固化至 `run_manifest.json` 的私有
`provenance.capabilities` section；历史 `provenance.json` 与 `environment_report.json` 仅由读取接口兼容。记录均不含绝对路径、API key、环境变量值或原始软件输出。状态机和前端只呈现操作级消息，例如
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
- `PipelineOrchestrator`：在 run 配置快照和 provenance 固化后写入脱敏能力快照；在确定步骤
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
- BOSS 无入参加载探针的启动异常、超时及负信号退出均归类为 `runtime_unavailable`；非负的参数提示退出码仅证明加载器可用，不替代真实 LigParGen fixture。
- ORCA 动态库环境和 LigParGen/BOSS 环境只作用于目标子进程。
- 必需工具缺失会阻断相应模块；未选中或存在替代链路的工具缺失不会阻断新任务。
- `run_manifest.json` 的私有 `provenance.capabilities`、历史 `provenance.json`/`environment_report.json`、状态事件和 Run Assistant 数据中不含绝对路径、密钥、环境变量值或底层日志。
- 现有 Gaussian、ORCA、Sobtop、LigParGen、GROMACS 测试保持通过；外部真实 smoke 仍是独立验收，不由替身测试替代。

已由 `tests/test_env_registry.py` 覆盖标准变量、`.env`、旧变量兼容、子进程环境和脱敏报告；
外部真实 smoke 仍是独立验收，不由替身测试替代。
