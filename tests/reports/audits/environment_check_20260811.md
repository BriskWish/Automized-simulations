# Environment Check

> 检查日期：2026-08-11
>
> 本文只记录已执行的只读检查、仓库清单状态和已标注的官方来源，不替代真实科学链路验收。

## 主机与工具

以下结果来自当前工作区执行的命令：

| 检查项 | 实际结果 |
|---|---|
| Python | `Python 3.12.3` |
| CPU/平台 | `x86_64` |
| C 库 | `glibc 2.39` |
| GROMACS | `2025.0` |
| C shell | `/usr/bin/csh` 可发现 |
| Open Babel | `/usr/bin/obabel` 可发现 |
| GCC | `/usr/bin/gcc` 可发现 |
| PATH 中的 Multiwfn | 可发现 |

Willy 的 `env_registry` 对 Multiwfn 使用仓库内固定路径：
`vendor/multiwfn/linux-x86_64/3.8-dev-2025-02-14/Multiwfn`。测试记录了外部
`WILLY_MULTIWFN_BIN`、`MULTIWFN_BIN` 和 PATH 中同名程序不会改变该解析结果。

## Python 包版本

以下版本由 `importlib.metadata.version()` 读取：

| 包 | 实际版本 |
|---|---:|
| `cryptography` | `41.0.7` |
| `gradio` | `6.20.0` |
| `py3Dmol` | `2.5.5` |
| `scikit-learn` | `1.9.0` |
| `pytest` | `9.1.1` |

`pyproject.toml` 声明 `cryptography>=42`。当前读取到的 `41.0.7` 不满足该声明。
本次检查未升级或安装共享 Python 包。

## `pip check`

执行 `python3 -m pip check` 的输出为：

```text
oslo-serialization 5.4.0 requires tzdata, which is not installed.
oslo-utils 7.1.0 requires tzdata, which is not installed.
```

## Vendor 清单

执行命令：

```bash
python3 scripts/verify_vendor_manifest.py --json
```

实际结果：

```text
integrity_ok: true
release_ready: false
```

组件状态：

| 组件 | `distribution_status` | 完整性结果 |
|---|---|---|
| Multiwfn Linux x86_64 | `release_ready` | 无完整性问题 |
| Packmol Linux runtime | `evidence_pending` | 无完整性问题 |
| Sobtop runtime | `evidence_pending` | 无完整性问题 |
| Open Babel minimal runtime | `evidence_pending` | 无完整性问题 |
| `3Dmol-min.js` | `exclude_from_release_artifact` | 无完整性问题 |

执行 `python3 scripts/verify_vendor_manifest.py --require-release-ready` 的退出码为
`1`。输出列出的状态为：

```text
distribution_evidence_pending:packmol-linux-runtime:evidence_pending
distribution_evidence_pending:sobtop-runtime:evidence_pending
distribution_evidence_pending:openbabel-minimal-runtime:evidence_pending
distribution_evidence_pending:3dmol-legacy-asset:exclude_from_release_artifact
```

清单文件为 [`vendor/manifest.json`](../vendor/manifest.json)。清单中的每个受管文件均登记
了相对路径、文件大小和 SHA-256；审计器不执行 vendor 二进制，也不写入 `md_run/`。

## 官方来源记录

以下链接仅作为清单中的上游资料：

- Packmol 官方论文 PDF 中写有 Packmol 按 GNU General Public License 分发：<https://m3g.github.io/packmol/packmol2.pdf>
- Open Babel 官方文档写有其库按 GNU GPL version 2 使用：<https://openbabel.org/docs/UseTheLibrary/intro.html>
- 3Dmol.js 官方文档写有其按 BSD 开源许可发布：<https://3dmol.org/doc/>

当前仓库的 Packmol、Sobtop、精简 Open Babel 和本地 `3Dmol-min.js` 清单记录中没有对应的
本地许可证文件路径，因此它们仍保持上述 `evidence_pending` 或
`exclude_from_release_artifact` 状态。Multiwfn 目录包含 `LICENSE.txt` 和 `NOTICE.md`。

## 回归与文件检查

执行 `python3 -m pytest -q` 的结果为：

```text
795 passed, 9 skipped
```

跳过项包括 external smoke、真实 LLM 连接、真实外部拓扑执行和浏览器 E2E 的显式 opt-in
用例。`python3 -m compileall` 对新增环境治理文件执行通过。

全仓 `git diff --check` 当前报告 `struct/TFSI.gjf` 的 CRLF/行尾空白和文件末尾空白行；
该文件问题未在本次环境治理中修改。

## 本次检查范围

- 未安装或升级 Python 包。
- 未执行 Gaussian、ORCA、Packmol、Sobtop、Multiwfn、Open Babel 或 GROMACS 科学任务。
- 未修改 `md_run/`、当前运行目录或当前运行配置。
- vendor 完整性审计和环境文件阅读均为只读操作。
