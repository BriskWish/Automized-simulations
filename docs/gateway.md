# 托管 LLM 网关设计（0.2.0）

> 维护范围：独立 LLM Gateway、Willy 客户端的托管/自带 Key 选择、设备注册、用量账本、管理员控制面，以及与 `src/willy/llm_config.py`、`frontend_api.py`、`app.py` 的集成边界。
>
> 状态：执行中。已实现局域网试运行切片：固定上游/模型白名单、非流式转发、服务端计数的 Ed25519 自助申请、前 50 名默认授权的自动批准、10 分钟令牌、nonce 防重放、撤销检查、SQLite 预留/结算账本、非回环 TLS 强制配置、回环管理员页面，以及 Willy 的 `managed/byok` provider。管理员 OIDC、PostgreSQL/Redis、多实例、备份告警、真实跨机器 TLS/upstream smoke 和密钥轮换工作流尚未实现。当前 Gradio 仍仅绑定 `127.0.0.1`。
>
> 最后更新：2026-08-12。

构建完成度、运行证据与双机验收顺序在本文第 11 节统一维护。

## 1. 目标与边界

网关的目标是在不分发上游供应商密钥的前提下，让部署在其他电脑上的 Willy Agent 使用受控的托管 LLM，同时保留用户使用自己的 API Key 直连模型服务的路径。

```text
托管模式：Willy 客户端 -> HTTPS LLM Gateway -> 运营者的上游 LLM 账户
自带 Key：Willy 客户端 -> 用户自己的 OpenAI-compatible 服务
```

网关只代理受限的 LLM 请求，不启动或停止 GROMACS，不读取客户端文件，不执行 shell，不访问 Willy 的运行目录，也不接收远程工作流控制命令。工具调用仍由每台客户端本地的动作契约和工具处理器执行。

不在首期实现：通用 HTTP 代理、任意上游 URL、供应商密钥分发、共享万能 API Key、跨用户 run 目录、网关执行 function tool、对公网暴露 Gradio，或无限制、无配额的自动注册批准。

## 2. 双模式客户端

| 模式 | 凭据来源 | 请求目的地 | 配置页 | 用量归属 |
|---|---|---|---|---|
| `managed` | 设备注册后签发的短期托管令牌 | 固定的 LLM Gateway | 只读授权、模型和额度状态；不显示可编辑 Key、URL 或模型 | 网关按设备和用户记账 |
| `byok` | 用户本机环境变量、凭据库或受限 `.env` | 用户自己的模型服务 | 保留 Key、Base URL、Model 和本地连通性检查 | 用户自行承担服务商用量 |

两种模式必须互斥。托管模式下，浏览器表单、本地 `config.json`、LLM prompt 和普通用户环境变量都不能覆盖网关地址、模型映射、额度或安全策略。BYOK 模式不得向托管网关上传 Key、Base URL、完整 prompt 或用量。

`llm_config.py` 已按模式解析 provider。托管模式从安装目录的部署描述读取固定网关、通过本机身份文件签名换取短期令牌，并在每次 OpenAI-compatible 调用前按需刷新令牌；方案助理刷新本进程 provider，`run_pipeline.py` 子进程独立重新解析同一 provider。不能只修改前端表单。

### 2.1 托管网关部署描述

部署者随安装包在项目根目录提供 `managed_gateway.json`，客户端只读该文件，不提供 URL、模型或上游 Key 的浏览器输入。当前 schema：

```json
{
  "schema_version": 1,
  "profile_id": "home-gateway",
  "label": "Home gateway",
  "base_url": "https://gateway.example.test",
  "model": "willy-default"
}
```

`base_url` 必须是无路径、无查询参数的 HTTPS 地址；只有 `localhost`/loopback 的开发测试允许 HTTP。描述文件不是服务端授权凭据，客户端篡改不会获得运营者的上游 Key、模型权限或额度；服务端白名单仍是唯一授权事实源。

`managed_gateway.json` 是具体部署资产，不提交到 GitHub；它由客户端部署包构建器写入安装包根目录。仓库只提交可审计的网关/客户端源代码和无凭据文档。`WILLY_GATEWAY_*` 环境变量、`.env`、SQLite 数据库、管理员秘密、上游 Key、TLS 私钥/证书、用户身份文件和运行时日志都只能留在部署机器，并已列入 `.gitignore` 防止误提交。当前仓库中的 `managed_gateway.json` 若指向 `127.0.0.1`，只适用于网关和 Willy 在同一台电脑上的测试，不能复制给其他电脑。

发布者使用真实服务器 profile 构建客户端部署包：

```bash
python3 scripts/build_managed_client_bundle.py \
  --profile /secure/deployment/managed_gateway.json \
  --output /secure/releases/willy-client
```

构建器只接受位于源码树外的非 loopback HTTPS profile，并排除所有隐藏本地目录（仅保留无凭据的 `.env.example`）、虚拟环境、日志、决策追踪、进程/锁文件、数据库、设备身份、密钥、证书和运行目录；输出包根目录只写入规范化的 `managed_gateway.json`。目标机不需要再手动创建或编辑该文件。

发布顺序固定为：先通过测试、提交并推送源码；再在干净的同一提交检出中构建客户端目录；最后压缩、校验并以 GitHub Release 附件或受控下载方式分发。部署 profile 和生成目录均不得提交到 Git。示例：

```bash
tar -C /secure/releases -czf /secure/releases/willy-client-0.2.0.tar.gz willy-client
sha256sum /secure/releases/willy-client-0.2.0.tar.gz
```

## 3. 威胁模型与安全原则

需要防范客户端提取文件和内存、访问令牌泄露或重放、设备复制、恶意改写模型或上游 URL、免费额度滥用、网关被当作任意代理、上游异常泄露内部细节，以及托管 prompt 的隐私风险。

1. 上游供应商 Key 只保存在网关秘密管理或服务端环境变量，永不下发。
2. 每台设备使用独立密钥对和可撤销身份；机器码只能作展示信息，不能作为安全绑定依据。
3. 访问令牌短期有效，须由设备私钥签名换取；令牌泄露后的有效窗口受限。
4. 网关只支持明确声明的模型别名和 API 路由，拒绝任意 URL、任意请求转发和未知模型。
5. 管理员身份与设备身份完全分离；设备令牌不能批准、撤销或查看其他设备。
6. 默认不保存原始 prompt、响应、请求头、Authorization、密钥或工具参数；审计记录保持脱敏。
7. 托管模式必须明确告知用户：LLM prompt 会经过运营者控制的网关。

## 4. 设备注册与认证

### 4.1 首次注册

1. 用户在 Willy 配置页点击“确认接入”后，客户端生成或读取本机 Ed25519 密钥对并提交一次申请；当前实现将私钥存入用户级私有身份文件，父目录 `0700`、文件 `0600`；系统凭据库适配属于后续强化。私钥不写入项目 `.env`、浏览器、网关数据库或日志。
2. 客户端提交公钥、版本和设备标签；网关不回传邀请码或其他可复用凭据。
3. SQLite 在同一 `BEGIN IMMEDIATE` 事务中检查公钥是否已存在，再检查活跃 `pending + approved` 设备数是否达到 `WILLY_GATEWAY_REGISTRATION_LIMIT`（默认 `500`）。同一公钥重复申请返回原设备记录，不占用第二个名额。
4. 新申请在同一事务中统计历史唯一注册设备。默认前 `50` 个唯一申请直接成为 `approved`，授予一个模型别名（优先 `willy-default`，否则字典序首个别名）、每日 `1,000,000` Token、每月 `10,000,000` Token、并发 `2` 和每分钟 `20` 次请求。自动批准窗口由注册审计永久计数，已有手动注册、撤销和过期均不让后来设备补位；同一公钥重复申请不重复占名额。
5. 自动名额用尽后的新申请创建无调用权限的 `pending` 设备记录，默认 `86400` 秒后过期并释放普通接入名额。相同公钥可重新申请以续期；过期申请不能被批准。管理员仍可在独立控制面批准或调整任一设备的有效期、模型许可、每日/月 token 配额、并发数和速率。

从旧版邀请码登记升级时，网关或管理员控制面首次启动会为历史 `pending` 记录补上当前配置的截止时间；已批准和已撤销记录不受影响。

设备公钥是一次本机身份安装的稳定标识，不是不可伪造的物理机器证明。删除身份文件、重装系统或虚拟机均可产生新密钥；MAC、磁盘序列号等不能作为安全身份或名额依据。自动批准只适合受控的小范围试运行，不能识别恶意新密钥或替代管理员审核。入口还按请求来源作进程内节流，默认每来源每小时 `10` 次；公网部署必须在受信任反向代理处另设真实来源限流和反自动化控制。

### 4.2 短期令牌

客户端向 `POST /api/v1/device/token` 发送包含时间戳、nonce、方法、路径和请求体摘要的私钥签名。网关验证公钥、设备状态、时间窗口和 nonce 未重放后，签发 10 分钟有效的访问令牌。首版访问令牌仍是短期 bearer；每请求设备绑定签名留待后续强化。

令牌至少包含 `device_id`、许可模型别名、过期时间、令牌 ID 和公钥指纹。网关维护撤销列表；设备撤销、额度暂停或密钥轮换后，旧令牌不得继续调用。私钥丢失时只能重新注册并重新批准。

## 5. 网关接口

首期只实现 Willy 当前需要的非流式 OpenAI-compatible 请求。流式转发及流式 usage 结算属于后续阶段。

| 接口 | 权限 | 用途 |
|---|---|---|
| `GET /healthz` | 无凭据或内网 | 返回服务存活，不代表上游额度可用。 |
| `POST /api/v1/registrations` | 无凭据，自助申请限额 | 提交设备公钥，原子创建或返回 `approved`（自动名额内）/`pending` 设备。 |
| `POST /api/v1/device/token` | 设备签名 | 获取短期访问令牌。 |
| `GET /v1/models` | 托管令牌 | 返回设备允许的模型别名。 |
| `POST /v1/chat/completions` | 托管令牌 | 转发 Chat Completions 并保留 tool calls。 |
| `GET /api/v1/me/usage` | 托管令牌 | 查看本设备脱敏用量。 |
| `/api/v1/admin/*` | 独立管理员认证 | 审批、撤销、额度、模型许可和汇总。 |

`/v1/chat/completions` 只接受模型别名，例如 `willy-default`。网关将别名映射为服务端真实模型，并限制消息大小、`max_tokens`、工具定义数量、单设备并发、速率和超时。网关不执行 tool，只透明返回模型的文本和 tool calls。

每个请求生成 `request_id`。错误响应不得包含上游响应体、供应商 Key、内部堆栈、真实模型部署名或网络拓扑。

## 6. 用量、额度与审计

请求转发前按 prompt 估算值和 `max_tokens` 预留额度；超过日/月 token、金额、并发或速率限制时不调用上游。收到上游 `usage` 后结算真实 input/output/total tokens，并释放预留差额。当前 SQLite 实现使用 `BEGIN IMMEDIATE` 保证单机开发切片的原子性；生产切换 PostgreSQL 前不得宣称可公网运行，Redis 只能用于限流/nonce 辅助，不能替代账本事实源。

最小数据模型：

| 实体 | 关键字段 |
|---|---|
| `devices` | 设备 ID、公钥指纹、状态、标签、申请截止、创建/最后活动、撤销时间 |
| `device_grants` | 设备 ID、模型许可、有效期、日/月 token、并发和速率限制 |
| `usage_requests` | 请求 ID、设备、模型别名、token、状态、延迟、时间、上游关联 ID |
| `usage_daily` | 设备/模型/日期聚合、已结算与已预留 token |
| `audit_events` | 注册、批准、额度变更、撤销、轮换和管理员操作 |
| `provider_configs` | 模型别名、真实模型引用、上游秘密引用、启用状态 |

默认不保存 prompt、响应、工具参数和精确 IP。临时故障排查内容必须显式启用、加密、设置最短保留期并写入管理员审计。

## 7. 部署边界

推荐拓扑：

```text
公网 HTTPS 反向代理
        -> Gateway 应用（FastAPI/ASGI）
        -> PostgreSQL 用量账本 + Redis 限流/nonce
        -> 固定上游模型服务
```

网关与 Willy Gradio 必须是不同服务、不同端口和不同权限主体。网关要求 TLS、请求体限制、连接/超时限制、入口限流、健康检查、备份、密钥轮换和告警；管理员控制面使用独立强认证。

个人电脑可用于内测，但公网稳定运行需要公网地址、TLS、持续在线、备份和应急撤销能力，优先使用受管 VPS。不能把当前只绑定 `127.0.0.1` 的 Gradio 直接改成公网中转站。

### 7.1 当前局域网试运行方式

服务入口为 `python3 -m willy_gateway`，默认只监听 `127.0.0.1:8787`。部署启动前必须由受限部署配置提供 `WILLY_GATEWAY_DATABASE`、`WILLY_GATEWAY_TOKEN_SECRET`（至少 32 字节）、`WILLY_GATEWAY_UPSTREAM_BASE_URL`、`WILLY_GATEWAY_UPSTREAM_API_KEY` 和 `WILLY_GATEWAY_MODEL_ALIASES`（JSON 别名映射）。自助申请配置可选：`WILLY_GATEWAY_REGISTRATION_LIMIT`（默认 `500`）、`WILLY_GATEWAY_AUTO_APPROVE_LIMIT`（默认 `50`；设为 `0` 关闭自动批准）、`WILLY_GATEWAY_DEFAULT_DAILY_TOKEN_LIMIT`（默认 `1000000`）、`WILLY_GATEWAY_DEFAULT_MONTHLY_TOKEN_LIMIT`（默认 `10000000`）、`WILLY_GATEWAY_DEFAULT_MAX_CONCURRENCY`（默认 `2`）、`WILLY_GATEWAY_DEFAULT_REQUESTS_PER_MINUTE`（默认 `20`）、`WILLY_GATEWAY_PENDING_REGISTRATION_TTL_S`（默认 `86400`）、`WILLY_GATEWAY_REGISTRATION_REQUESTS_PER_WINDOW`（默认 `10`）和 `WILLY_GATEWAY_REGISTRATION_RATE_WINDOW_S`（默认 `3600`）。这些值不应写入仓库、客户端 `.env` 或浏览器表单。

要接受其他电脑，设置 `WILLY_GATEWAY_BIND_HOST` 和 `WILLY_GATEWAY_BIND_PORT`，并使 profile 中的端口与实际监听端口一致。任何非 loopback 监听都必须同时设置 `WILLY_GATEWAY_TLS_CERTFILE` 和 `WILLY_GATEWAY_TLS_KEYFILE`，否则进程会在启动前拒绝配置；客户端的 `managed_gateway.json` 必须使用对应 HTTPS 地址。客户端网关请求默认不继承 `HTTP_PROXY`/`HTTPS_PROXY` 环境代理；如部署网络必须经过代理，应由部署者增加明确的受控代理配置。证书信任、路由、防火墙和真实 LAN 连通性必须由部署者在目标网络验收，不能由 fake-upstream 测试代替。

管理员页面以独立进程启动：`python3 -m willy_gateway.admin`。它固定监听 `127.0.0.1:${WILLY_GATEWAY_ADMIN_PORT:-8788}`，且要求至少 32 字节的 `WILLY_GATEWAY_ADMIN_SECRET`；页面地址是 `http://127.0.0.1:8788/`。该页面仅展示设备指纹、状态、接入名额占用、自动批准已用/剩余、待审批截止、今日结算/预留 token、当前并发和近一分钟请求数，可批准/调整 grant 或撤销设备；不返回私钥、访问令牌、prompt、上游 Key 或请求体。不要将管理端口转发到 LAN/公网，也不要在浏览器外持久化管理员秘密。

当前管理员认证是“回环监听 + 部署秘密”的内测措施，不等同于 OIDC。OIDC、PostgreSQL/Redis、TLS 真实验收、备份、监控与恢复演练完成前，不得将服务暴露到公网或将自助申请当作抗批量滥用的公开注册系统。

## 8. Willy 客户端集成

新增 provider 边界：

```text
resolve_settings()   -> 脱敏端点、模型和调用凭据
create_client()      -> OpenAI-compatible 客户端（实际工作流调用）
connection_check()   -> 设备令牌交换 + /api/v1/me/usage 状态检查
usage_snapshot()     -> 托管模式的本设备用量与剩余额度
```

`managed` 从本机受保护的设备身份获取短期令牌并使用固定网关 URL；`byok` 继续使用用户本机配置。Config Agent、LayerAgent、Run Assistant 和 `PipelineOrchestrator` 都通过该边界创建 client，禁止保留读取全局 Key 的旁路。

配置页：

- 托管模式显示安装包携带的部署描述、粗粒度本机设备状态、“确认接入”和“检查托管服务”；只有点击“确认接入”才提交本机公钥；不接受表单 URL、Key 或邀请码，且不写入 `.env`。
- BYOK 模式允许编辑自己的 Key、Base URL、Model，并显示请求不会经过托管网关。
- 模式切换不修改已有 run 的 provenance；新 run 只记录脱敏 provider mode 和模型标识。

网关能确保上游 Key 不下发、设备只使用受限模型别名和配额，并记录设备归属；它不能证明用户控制的电脑上某一 prompt 在语义上“只用于 Willy 项目”。同一用户权限下的其他程序可能复用本机设备身份。若需要将调用强绑定到服务器任务，必须由服务端为每个 project/run 签发短期任务许可，并让网关验证该许可；这属于后续的中心化任务调度设计。

## 9. 实施阶段与验收

### Phase 0：协议和安全设计

固化接口、认证、数据保留、错误码、模型别名、隐私提示、自助申请名额和额度单位；为托管/BYOK 写出配置 schema 与迁移规则。

验收：没有实现时不得在 README、UI 或安装包中宣称托管服务可用。

### Phase 1：网关核心转发（私有开发切片已实现）

实现健康检查、固定上游、模型别名、非流式 `chat/completions`、tool call 透传、脱敏错误、请求限制、超时和 request ID。

验收：不执行 tool、不访问任意 URL、不输出上游密钥；`tests/test_gateway.py` 的 fake upstream、模型别名、请求限制、tool call 透传和错误边界通过。该切片仅适用于本机/内网，不是公网部署凭据。

### Phase 2：设备与额度（局域网运营控制面已实现）

已实现服务端名额计数的公钥自助申请、前 50 名自动批准和默认 grant、待审批过期释放、回环管理员页面的批准/撤销/grant 操作、10 分钟令牌、签名验证、nonce 防重放、设备撤销、SQLite 事务账本和模型/并发/速率配额。管理员页面每 5 秒刷新接入名额、自动批准名额、各设备的今日 token、当前并发和近一分钟请求数。尚未实现 PostgreSQL/Redis 适配、管理员 OIDC、密钥轮换工作流和多实例部署。

验收：复制令牌、重放、过期、撤销、越权模型、超额和高并发均被拒绝；当前测试覆盖重放、撤销、白名单、额度、管理员秘密隔离和 grant 生命周期。OIDC 与真实 LAN/TLS 验收仍待实现。

### Phase 3：Willy 双模式接入（局域网切片已实现）

已实现 `managed/byok` provider，托管模式隐藏可编辑服务商表单，BYOK 保持本地路径；方案助理刷新本进程 provider，动态 OpenAI facade 在每次调用前按需刷新短期令牌，子进程独立重新解析 provider。

验收：托管调用无需供应商 Key；BYOK 不触达网关；当前测试覆盖客户端只发送公钥、私有身份文件权限、签名令牌交换、缓存/刷新、表单隔离与公开错误。真实跨机器调用、用量页和 run provenance 的 provider 字段仍待实现。

### Phase 4：小范围试运行

在小范围宣传中保持 `500` 或更低的自助申请上限、最多 `50` 个自动批准名额、保守模型、日 token、并发和请求体限制；完成 TLS、备份、告警、轮换和故障演练，再决定是否扩大注册范围。

## 10. 测试与发布门禁

默认使用 fake upstream、临时数据库和隔离密钥，禁止默认访问真实 LLM。至少覆盖：

1. 注册、批准、拒绝、撤销、轮换和令牌过期。
2. 签名、nonce 重放、请求体篡改、模型越权和管理员越权。
3. 额度预留/结算、并发竞争、日切换、限流和上游失败。
4. tool call 透传、错误脱敏、非 JSON、超时和请求大小限制。
5. Willy 的 `managed/byok` 隔离、配置页冻结、子进程 token 刷新、用量展示和 run 脱敏。
6. 真实上游 function-calling smoke、TLS 和撤销生效验证。

`tests/test_gateway_offline_acceptance.py` 是独立的离线验收入口，使用 ASGI 内存 transport 和脚本化 fake upstream 验证设备令牌、撤销、额度、超时 unknown 预留、tool call 透传和 SQLite 脱敏审计；它不创建监听 socket、不调用真实上游、不读取模拟运行目录。运行命令为 `python3 -m pytest -q tests/test_gateway_offline_acceptance.py`。当前协议明确拒绝 `stream=true`，该用例验证拒绝边界；真实 SSE 支持属于单独的协议改造。

认证、额度、撤销、脱敏审计和真实 tool-calling smoke 全部通过前，该能力只能标为“开发中”，不得作为公开产品承诺或发放不受控访问凭据。

## 11. 构建与验收快照

当前实现达到单机/受控局域网试运行完整度：网关、管理员控制面、Willy 客户端、安装包 profile 与确认接入后的设备申请、前 50 名自动批准、设备令牌、模型白名单、额度账本和撤销链路均有代码与离线测试。网关关联回归和完整默认回归的实际结果以本轮测试记录为准。

当前仍未完成真实双机 TLS、真实上游、反向代理限流、备份恢复、管理员强认证、多实例账本、密钥轮换和 Uvicorn 监听 smoke。离线验收使用临时 SQLite、ASGI 内存 transport 与脚本化 fake upstream，不创建监听 socket、不调用真实上游，不能替代生产验收。

### 11.1 2026-08-11 本机健康检查

本机部署描述 `managed_gateway.json` 指向 `http://127.0.0.1:8789`。通过绕过环境代理的只读请求
访问 `/healthz`，返回 HTTP 200 和 `{"status":"ok"}`；未读取或记录设备私钥、访问令牌、上游
API Key 或管理员秘密。未绕过代理的同一请求曾返回 502，确认为代理路径结果，不能作为网关本身的失败证据。

该记录只证明本机监听和健康端点可用，不构成真实双机 TLS、设备注册、短期令牌交换、上游
tool-calling 或远程 Willy 验收。远程验收机目前没有 `managed_gateway.json`，且本机 loopback 地址
不能从远程机器使用；这些条件仍属于真实双机网关验收缺口。

发布顺序固定为：先完成协议/安全契约，再完成核心转发与设备额度，随后完成 Willy 双模式接入，最后进行小范围双机试运行。认证、额度、撤销、脱敏审计和真实 tool-calling smoke 未全部通过前，状态只能标为“开发中”。
