# 静态安全检查记录

> 检查日期：2026-08-11
> 检查类型：只读静态检查
> 范围：当前工作树、指定日志/fixture 范围、Git 历史的两条长格式凭据模式、源码中的监听默认值，以及当前执行环境的指定端口监听状态。

## 记录原则

- 本记录不写入 API Key、Authorization 值、Base URL、绝对路径或 Git 远程地址。
- 本记录不把模式未命中表述为“绝对不存在”。模式检查只能覆盖所列规则和可读取范围。
- 本记录不连接远程服务，不验证 DNS、TLS、代理、防火墙、账户权限或远端实际部署状态。
- 本记录不修改文件、权限、环境变量、Git 配置或运行中的服务。

## 已查询边界

| 对象 | 已执行的静态检查 | 未执行的检查 |
|---|---|---|
| 当前工作树 | 使用 <code>rg</code> 扫描隐藏文件；排除 <code>.git/</code>、<code>node_modules/</code> 和 Python 字节码。规则覆盖长 <code>sk-</code> 形式、API Key 变量赋值、Authorization/Bearer、常见 Unix/Windows 绝对路径、SSH/SFTP、私网地址和回环地址。输出只保留文件名、行号、字段名或脱敏文本。 | 不对未知密钥格式、加密内容、编码内容或二进制内容作推断。 |
| 日志与 fixture | 扫描 <code>.log</code>、<code>.out</code>、<code>.jsonl</code>、<code>.txt</code>，以及 <code>tests/fixtures/</code>、<code>tests/e2e/</code>；规则覆盖 Key、Authorization、绝对路径和 SSH/SFTP。 | 不解析科学软件输出语义，不验证第三方示例文件来源。 |
| 本地敏感配置 | 检查 <code>.env</code> 与 <code>managed_gateway.json</code> 是否存在、Unix 权限和字段名；不读取或输出字段值。 | 不验证字段值是否可用，不读取身份文件、数据库、TLS 私钥或运行时日志。 |
| Git 当前索引与历史 | 检查 <code>.env</code>、<code>managed_gateway.json</code> 是否被索引或被忽略；对所有 Git 引用使用两条长格式模式搜索：<code>sk-[A-Za-z0-9]{20,}</code> 与 <code>Bearer</code> 后至少 20 个允许字符。 | 不进行熵分析，不将任意字符串推断为凭据，也不扫描不可达对象或外部托管平台。 |
| 监听默认值和端口 | 阅读前端、网关和管理员启动代码；查询当前执行环境的 TCP <code>7860</code>、<code>8787</code>、<code>8788</code> 监听项。 | 不检查其他网络命名空间、宿主机防火墙、容器编排、反向代理或外部网络可达性。 |

## 观察结果

### 凭据、Authorization 与本地配置

1. 当前工作树的宽松 <code>sk-</code> 文本规则命中两个 <code>remote-task-unavailable</code> 相关标识符。该命中来自标识符中的子串，不是凭据值。
2. 对所有 Git 引用执行的两条长格式模式搜索均未返回提交标识。
3. Authorization/Bearer 规则命中网关请求头处理代码和测试/测试台账中的协议文本。检查输出未包含请求头值或 Bearer 值。
4. <code>.env</code> 存在，权限为 <code>600</code>。检查仅观察到其 LLM Key、Base URL 和 Model 对应字段为非空；未记录字段值。
5. <code>managed_gateway.json</code> 存在，权限为 <code>644</code>。检查到的顶层字段名为 schema 版本、profile 标识、标签、模型和 Base URL；未记录字段值。
6. Git 当前索引未列出 <code>.env</code> 或 <code>managed_gateway.json</code>；<code>git status --ignored</code> 将二者标记为已忽略。<code>.env.example</code> 位于当前索引中。

### 绝对路径、日志与 fixture

1. 测试源码、文档和部分实现代码含有绝对路径文本，用于临时目录、示例或路径解析测试。
2. 日志/fixture 范围的路径规则只命中三个 <code>vendor/sobtop/examples/</code> 下的第三方 <code>.out</code> 示例输出。每个文件含“程序开始/结束目录”形式的绝对路径文本。
3. 该次日志/fixture 检查未返回 <code>tests/fixtures/</code> 或 <code>tests/e2e/</code> 中的 Key、Authorization、绝对路径或 SSH/SFTP 命中项。

### 远程主机信息

1. SSH、私网地址和回环地址规则命中来源包括网关/远程执行源码、测试 fake service、测试文件和设计文档。
2. 本地 <code>managed_gateway.json</code> 的字段名包含 Base URL；本记录不输出其值。
3. Git 配置存在名为 <code>origin</code> 的远程项。本记录不输出其地址。

### 默认监听与当前端口

1. 前端启动调用使用 <code>server_name="127.0.0.1"</code> 且 <code>share=False</code>。
2. 托管网关设置的 <code>bind_host</code> 默认值为 <code>127.0.0.1</code>。环境变量 <code>WILLY_GATEWAY_BIND_HOST</code> 可提供另一值。
3. 管理员页面启动调用使用 <code>host="127.0.0.1"</code>。
4. 本次 <code>ss</code> 查询在当前执行环境中未返回 TCP <code>7860</code>、<code>8787</code> 或 <code>8788</code> 的监听项。

## 不作出的结论

- 不将上述静态模式未命中推断为仓库、历史、二进制文件或外部系统中不存在任何凭据。
- 不将配置文件的存在、权限或字段名推断为其值有效、无泄露风险或满足部署要求。
- 不将源码默认监听地址推断为运行时必然只绑定本机；环境变量、部署参数和运行环境可以改变实际监听行为。
- 不将当前执行环境未观察到监听项推断为其他网络命名空间、宿主机或外部机器上不存在服务。
