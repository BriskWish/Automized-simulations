# 外部与内置运行环境注册设计

> 维护范围：`src/willy/env_registry.py`、`src/willy/env_checker.py` 及外部软件调用适配器。
> 状态：核心实现完成；启动级全局缓存、版本兼容性判定和自动后端重规划待后续评估。
> 最后更新：2026-08-13。

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
| `gmx` | `WILLY_GMX_BIN` | 无 | 不自动发现；仅进程环境或 `.env` 中的显式值 |
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
| Packmol | 21.2.3 | Linux x86_64，glibc >= 2.29，宿主 `libgfortran.so.5` | 官方 `v21.2.3` 源码在 Ubuntu 20.04/glibc 2.31、gfortran 9.4.0 上以通用 x86-64 选项构建；当前二进制动态依赖 GCC GNU Fortran runtime。Ubuntu/Debian 用户安装 `libgfortran5`，无需配置 Packmol 路径 |
| Sobtop | 内置 Linux x86_64 二进制 | 固定随仓库版本 | 当前 Linux x86_64/glibc 基线 |
| Open Babel | 3.1.1 | OPLS 使用完整 Open Babel 3；内置仅为精简运行时 | 完整格式插件仍由用户安装 |
| Multiwfn | 3.8(dev)，更新于 2025-02-14 | 固定 `3.8-dev-2025-02-14` 的 Linux x86_64 包，宿主 `libXm.so.4` | 已接入 vendor，不需要配置路径；当前二进制依赖 Motif/X11 runtime，Ubuntu/Debian 用户安装 `libxm4` |
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

### 2.3 Vendor 清单、版本与许可边界

`vendor/manifest.json` 是内置运行负载的发行清单：每个受管文件登记大小与 SHA-256，
并明确其来源、版本、许可证证据和发行状态。运行时不会读取该清单，也不会因为审计而启动
Packmol、Sobtop、Multiwfn 或 Open Babel；它只由下列只读命令在 CI 和发布复核中消费：

```bash
python3 scripts/verify_vendor_manifest.py
python3 scripts/verify_vendor_manifest.py --require-release-ready
python3 scripts/verify_vendor_manifest.py --artifact-root <unpacked-release-root> --require-release-ready
```

前者必须始终通过，防止受管二进制和参数文件在未知条件下漂移。后者会将未补齐来源、版本或
许可证的组件视为发布阻塞。当前 Multiwfn 和 Packmol 已达到 `release_ready`；Sobtop、
精简 Open Babel 和遗留 `3Dmol-min.js` 均登记为 `exclude_from_release_artifact`，其清单内
显式列出待排除路径、仓内无法证明的原因和人工核验步骤。排除不是授权结论，也不等于发行
就绪：在实际打包器能证明这些路径没有进入工件之前，`--require-release-ready` 必须保持失败。
Sobtop 的整个目录（含示例）均在排除范围内；若后续希望随包发行，必须先单独登记其来源、
许可证与示例数据保留理由。

发布构建完成后，还必须将解压后的 staging 根目录传给
`--artifact-root --require-release-ready`。该纯文件检查验证 `release_ready` 组件所登记文件
的大小与 SHA-256、清单自身以及其余未登记文件；同时检查 `exclude_from_release_artifact`
组件的全部登记路径均不在工件中。它不解压归档、不启动程序、不替代许可证人工复核。工件
通过这一步后，待证据组件的排除才可视为已实际落实；若要把它们重新随包发行，仍需补齐其
上游来源、精确版本、许可文本与再分发依据。

受控 staging 只用于审计，不是当前项目的交付包。若需要复核 staging 文件边界，可使用：

```bash
python3 scripts/build_release_staging.py --output /安全的空目录/willy-release
python3 scripts/verify_vendor_manifest.py \
  --artifact-root /安全的空目录/willy-release \
  --require-release-ready
```

构建器只复制 Git 跟踪的非 vendor 文件，再复制清单中 `release_ready` 的 vendor 文件和同一份
`vendor/manifest.json`；源工作树和目标目录均必须干净/为空。它不执行任何二进制、不会读取 `.env` 或 `md_run/`，且在
复制后立即执行同一份 artifact audit。CI 的 quality 门禁执行这两条检查；不得把该 staging 表述为当前可运行发行物。

**当前发行形态（2026-08-13）**：开发者已确定只支持从 GitHub 完整源码检出后运行 `pip install -e .`；
不发布 wheel、PyPI 包、独立安装包或可运行 staging。普通 wheel 不含根目录资源或 `vendor/` 负载，且
现有 `get_project_root()` 只识别源码布局，因此不属于支持形态。项目原创代码的免费使用与再分发需事先授权
边界写在根 README；这不是对第三方组件的授权。仓内 Sobtop、精简 Open Babel 和 3Dmol 的来源/再分发
依据仍未完成审计，即使它们随当前源码检出可见，也不得据此推断项目拥有或授予其分发权。

外部科学软件不随 Willy 提供。许可证以 2026-08-13 的官方页面核验为准：GROMACS 是
LGPL-2.1-or-later；Packmol 21.2.3 是 MIT；Multiwfn 3.8(dev)-2025-02-14 随附许可允许免费
再分发并要求引用。Gaussian 是需签署许可的软件；ORCA 的 EULA 规定其授权不可转让和不可再许可；
BOSS 5.1 官网只说明向学术用户免费提供并要求登记下载。Open Babel 上游为 GPL-2.0，完整安装由用户提供。
LigParGen 的上游服务和 Sobtop 页面未在本项目可审计材料中给出可用于本项目再分发的许可证文本，因此保持
“外部安装/待核实”，不得以“免费”推导再分发权。相关官网链接集中在根 README；各 profile 的真实执行证据
仍由 external smoke 负责。

核验来源仅用于记录事实，不构成法律意见或对第三方条款的替代解释：

| 组件 | 官方核验来源 | 当前记录结论 |
|---|---|---|
| GROMACS | [官方许可说明](https://manual.gromacs.org/current/reference-manual/preface.html) | LGPL-2.1-or-later；用户自行安装，本项目不随附。 |
| Packmol 21.2.3 | [官方 GitHub 仓库](https://github.com/m3g/packmol)、[v21.2.3 许可](https://github.com/m3g/packmol/blob/v21.2.3/LICENSE) | MIT；本地许可证、版本、构建来源和哈希已登记。 |
| Multiwfn 3.8(dev) | [上游下载页](http://sobereva.com/multiwfn/) 与仓内 `LICENSE.txt` | 随附条款允许免费分发并要求引用；本地版本和哈希已登记。 |
| Gaussian 16/09 | [Gaussian 官方许可/价格说明](https://gaussian.com/wp-content/uploads/dl/ousa_com.pdf) | 需要签署许可；用户自行安装，不随附。 |
| ORCA 6.x | [ORCA EULA](https://orcaforum.kofo.mpg.de/app.php/privacypolicy) | 授权不可转让或再许可；用户自行下载和安装。 |
| BOSS 5.1 | [Jorgensen 组软件页](https://zarbi.chem.yale.edu/software.html) | 官网说明面向学术用户免费提供且需登记下载；不随附。 |
| LigParGen | [LigParGen 上游服务](https://zarbi.chem.yale.edu/ligpargen/index.html) | 本项目未获得可审计的再分发许可记录；仅支持用户外置安装。 |
| Open Babel | [官方许可 FAQ](https://openbabel.org/docs/Introduction/faq.html) | GPL-2.0；完整 OPLS 依赖由用户安装，仓内精简负载仍待溯源。 |
| Sobtop | [上游页面](http://sobereva.com/soft/sobtop/) | 仓内精确版本、许可和再分发依据尚未核实。 |

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
必须在 Sobtop、精简 Open Babel 和遗留资产已补齐发行证据，或发布打包器已可验证地排除其
全部登记路径后才允许通过。

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
中的 API Key、Base URL 和 Model，不读写 `.env`，并在 12 秒上限内要求模型调用零参数
`willy_connection_check`。请求使用 OpenAI-compatible 的 `tool_choice="auto"`，但服务端仍必须在响应中
收到且仅接受该指定工具调用；纯文本、其他工具或空调用均不通过。这样兼容拒绝强制
`tool_choice="required"`、但能正确自动调用工具的 reasoning 网关，不会放宽 Willy 的动作契约。为避免在页面回显凭据，已保存的 API Key 不会回填；重启后再次测试时，
用户必须重新填写 Key，否则返回 `invalid_test_api_key`，不会错误地把该本地校验归因于服务端。
一次成功响应同时证明 Chat Completions、所选模型与自动 function calling 可用；保存动作仍只写本机
`.env`，且需要重启前端使 Agent 重载 client。`tests.llm_eval.live_protocol_runner` 还提供显式 opt-in
的脱敏三段协议探针：文本和自动工具调用是产品门禁，`required` 工具选择仅记录 provider 兼容性，
不作为当前自动工具路径的失败依据。

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

预检分为四层：

1. **配置页依赖预检（已实现）**：用户显式点击“预检运行依赖”后，`dependency_preflight.py` 仍按可替代链路组完成内部判定、自动发现外置工具并补写缺失默认项；前端仅将已有结果渲染为量化结构、电荷配置、拓扑参数、初始建盒和模拟运行下的独立软件或模组清单。每项仅展示一次“满足/不满足”及“内置/已检测到外置/已配置外置”来源，不展示完整链路结论、推荐安装或内部组件细节。这是建议性结果，绝不创建 run、改变状态机或阻止本地任务启动。
2. **能力发现（已实现）**：每个新 run 创建后、进入步骤前，非阻塞扫描全部外部工具并写入脱敏报告；缺少未选中的工具不使服务不可用。
3. **方案预检（已实现）**：配置与后端确定后，按该流程需要的模块检查所需工具。当前没有自动切换后端；必需工具不可用时在执行前返回结构化失败。
4. **工步预检**：进入外部命令前，再验证当前输入、附属文件、可执行权限和子进程环境。软件可发现不代表当前任务一定可运行。

配置页预检只会对可用、由 `PATH`、兼容变量或受限默认目录自动发现的**外部**工具补写缺失的本机 `.env` `WILLY_*` 项，并以原子替换方式设为 `0600`。它不覆盖启动进程环境、已有 `.env` 的 `WILLY_*` 设置或 ORCA 相关任一显式键；内置 Vendor 路径永不写入 `.env`。写入失败只给出提示，不改变预检结论或本地任务可提交性。报告只公开状态和来源，不公开绝对路径、环境变量值、日志或密钥。

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

### 4.2 外部二进制 ABI 兼容性边界

工具被发现且具有执行权限，不代表它能在当前宿主机启动。2026-08-11 目标机真实运行
`md__202608110001` 时，随包 `vendor/packmol` 因要求 `GLIBC_2.34` 而在 `GLIBC_2.31`
宿主上以返回码 1 退出，Step 7 未生成 `model.pdb`。该事实登记在
`docs/testing_strategy.md` 的 6.12 和 `docs/project_gap_analysis.md` 的 G-01/G-07。

Packmol 已登记为受管工具，`resolve_tool("packmol")` 与 `env_checker` 会在 Step 7 前执行受控最小启动
探针（`packmol -h`）。动态加载器/ABI 签名、启动异常、超时或异常信号退出统一归类为
`runtime_unavailable`；普通参数/帮助用法的非零退出只证明加载器已启动，不能被误判为不可用。
这类失败不会进入密度、盒参数或 LLM 自动修复重试：编排器直接以零次修复升级，私有证据只保留
受限分类和返回码，公共状态仅显示文件/操作级兼容性建议。目标机仍须部署兼容构建并重新执行真实链路，
本地探针并不替代跨发行版运行证据。

2026-08-12 已以官方 `v21.2.3` 源码在 Ubuntu 20.04/glibc 2.31、gfortran 9.4.0 上重建内置
`vendor/packmol`。构建固定为 `-O3 -march=x86-64 -mtune=generic -funroll-loops`，最大 GLIBC
符号为 `GLIBC_2.29`，避免绑定构建机 CPU；源码 tarball、编译器、选项、二进制哈希和 MIT 许可证均登记在
`vendor/manifest.json`。本机与同一 Ubuntu 20.04 主机均完成 4 原子、20 A 周期盒 smoke（退出码 0、
`Success!`、4 个原子、一个 `CRYST1`）。验收机上的 Willy 检出尚未替换为该版本，因此完整端到端仍须在
更新检出后重新执行。

## 5. 模块职责与迁移

- `env_registry.py`：工具声明、路径解析、校验、子进程环境构造和脱敏能力报告。
- `dependency_preflight.py`：配置页调用的建议性分组预检；读取 registry、核验内置 Vendor，并仅补写自动发现的缺失外部 `WILLY_*` 默认项。它不创建工程、不进入状态机，也不替代执行期检查。
- `step_registry.py`：维护主流程 step 及其执行模块、外部工具和内置依赖的归属关系。
- `env_checker.py`：保留现有 `check_all()`、`check_module()`、`ensure()` API，按执行模块
  注册表生成依赖归属并适配 `env_registry` 的报告，避免现有调用方一次性破坏。
- 量子、拓扑、模拟模块：只向 registry 请求可执行文件和运行环境，不直接调用
  `shutil.which()` 或读取软件环境变量。
- `PipelineOrchestrator`：在 run 配置快照和 provenance 固化后写入脱敏能力快照；在确定步骤
  方案后执行模块级预检。
- Run Assistant：通过 `tools_get_environment_run` 只读获取脱敏能力摘要，可解释“工具不可用”，
  没有环境变量读写或任意路径读取权限；当前不会自动改用后端。

执行期的外部依赖强制检查仍以 `env_checker` 为唯一入口；`env_registry` 是它的内部实现。
配置页的 `dependency_preflight` 仅用于部署前发现和默认配置补全，不能作为执行许可，也不构成第二套
运行时阻断逻辑。

## 6. 验收与回归

必须覆盖：

- 标准变量、旧变量、默认目录和 `PATH` 的解析优先级。
- `.env` 白名单加载与进程环境覆盖关系。
- OpenAI-compatible LLM 的通用变量、DeepSeek 兼容回退、URL/模型校验和密钥脱敏。
- 表单值的一次性连通性测试：强制工具调用、12 秒上限、八类公开错误、无持久化与无敏感信息泄露。
- 配置页依赖预检：分组的替代链路判定、内置 Vendor 的存在/可执行性、自动发现的 `WILLY_*` 默认项写入、已有设置不覆盖、写入失败降级，以及失败结果不影响本地任务启动。
- 缺失、路径不存在、目录误作二进制、不可执行、必要附属程序缺失等状态。
- BOSS 无入参加载探针的启动异常、超时及负信号退出均归类为 `runtime_unavailable`；非负的参数提示退出码仅证明加载器可用，不替代真实 LigParGen fixture。
- ORCA 动态库环境和 LigParGen/BOSS 环境只作用于目标子进程。
- 必需工具缺失会阻断相应模块；未选中或存在替代链路的工具缺失不会阻断新任务。
- `run_manifest.json` 的私有 `provenance.capabilities`、历史 `provenance.json`/`environment_report.json`、状态事件和 Run Assistant 数据中不含绝对路径、密钥、环境变量值或底层日志。
- 现有 Gaussian、ORCA、Sobtop、LigParGen、GROMACS 测试保持通过；外部真实 smoke 仍是独立验收，不由替身测试替代。

已由 `tests/test_env_registry.py` 覆盖标准变量、`.env`、旧变量兼容、子进程环境和脱敏报告；
外部真实 smoke 仍是独立验收，不由替身测试替代。
