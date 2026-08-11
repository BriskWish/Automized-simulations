# 网关离线验收事实记录

> 最后核验：2026-08-11
>
> 本文只记录当前仓库中已执行的离线验收及其边界，不将离线测试结果解释为真实部署或跨机器验收结果。

## 1. 记录范围

本记录对应 `tests/test_gateway_offline_acceptance.py`，并使用 pytest marker `gateway_acceptance`。测试对象是当前托管网关 ASGI 应用及其 SQLite 账本边界。

测试运行方式如下：

- 使用 `httpx.ASGITransport` 在进程内调用 FastAPI 应用。
- 使用临时 SQLite 数据库。
- 每个测试生成隔离的 Ed25519 设备密钥。
- 使用脚本化的内存 fake upstream 记录请求事件和转发载荷。
- 不绑定监听端口，不调用真实上游，不读取 `md_run/`，不启动科学计算。

fake upstream 是脚本化响应/异常序列，不是 SSE 服务。当前网关协议对 `stream=true` 返回 `streaming_not_supported`；本记录不声称实现了流式响应。

## 2. 已实际验证的边界

| 用例 | 实际断言 |
|---|---|
| `test_device_token_replay_and_revocation_are_enforced_offline` | 设备可通过签名请求换取令牌；同一 nonce 重放返回 401；设备撤销后，已有令牌访问模型列表和 Chat 请求均返回 401；撤销后的 Chat 请求没有到达 fake upstream。 |
| `test_quota_and_streaming_guard_block_fake_upstream_before_forwarding` | 日/月额度不足时 Chat 返回 429 `quota_exceeded`；`stream=true` 返回 400 `streaming_not_supported`；上述两类请求都没有到达 fake upstream。 |
| `test_timeout_keeps_then_expires_unknown_reservation_without_sleeping` | fake upstream 超时使 Chat 返回 504；超时预留仍可在用量查询中观察到；通过 monkeypatch 推进网关时钟后，下一次预留清理该未知预留并成功完成；测试没有实际等待。 |
| `test_tool_call_forwarding_and_audit_redaction_are_observable_offline` | 请求中的模型别名被映射到固定上游模型； `tools`、`tool_choice` 和 tool-call arguments 被转发并返回；fake upstream 的 500 错误对客户端只表现为 502，供应商错误内容未出现在响应或 SQLite 账本中。 |

脱敏断言还检查 SQLite 中不存在测试 prompt、tool 参数 canary、供应商错误 canary、上游 Key 或客户端 Bearer 令牌。该断言只针对本测试写入的临时 SQLite 数据库。

## 3. 实际执行结果

以下结果来自 2026-08-11 当前工作区的命令输出：

```text
python3 -m pytest -q tests/test_gateway_offline_acceptance.py
4 passed

python3 -m pytest -m gateway_acceptance -q
4 passed, 800 deselected

网关关联回归：
86 passed, 1 skipped

完整默认回归：
795 passed, 9 skipped

python3 -m compileall -q tests/test_gateway_offline_acceptance.py src/willy_gateway
通过（无输出）
```

本次快照生成时，测试台账结果为 `804 pytest + 18 LLM = 822 records`；当前目录中的台账应以重新生成的最新文件为准，四个离线验收用例登记在 K 类。

## 4. 未由本记录验证的事项

以下事项没有在本次离线验收中执行或证明：

- 两台电脑之间的真实网络链路、HTTPS/TLS 证书、反向代理和防火墙配置。
- 真实 Uvicorn 监听、真实上游 LLM 服务、真实模型别名、真实 provider tool calling 或 provider 配额行为。
- SSE 或其他流式响应转发；当前已验证的是 `stream=true` 被拒绝。
- PostgreSQL/Redis、多实例一致性、备份恢复、密钥轮换、OIDC 管理员认证和公网反自动化能力。
- HTTP 字节吞吐、延迟分位数、集中式监控和告警。
- 通过测试证明客户端请求在语义上“只用于 Willy”；网关当前只提供设备、接口、模型和额度边界。
- 另一台电脑上的安装包、配置页、设备申请和跨版本升级流程。

这些事项应使用单独、显式 opt-in 的双机或真实上游验收，不得以本文件中的 pytest 结果代替。

## 5. 相关记录

- [`gateway.md`](../../../docs/gateway.md)：网关设计、构建状态、完成度和发布门禁。
- [`testing_strategy.md`](../../../docs/testing_strategy.md)：测试分层及 `gateway_acceptance` marker。
- [`test_case_catalog.md`](../test_case_catalog.md)：T267-T270 的逐条测试登记。
