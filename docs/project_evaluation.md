# Willy 项目综合评估

> 评估日期：2026-07-31  
> 范围：当前工作区源码、文档、测试、配置、运行产物与环境检查结果。  
> 说明：本报告基于项目现状评估，未修改或回退任何已有代码。

---

## 一、总体结论

Willy 是一个面向电解液/小分子体系的 AI 驱动分子动力学自动化原型。项目目标明确：用户用自然语言描述化学体系，系统自动生成 `config.json`，再串联量子化学、RESP 电荷、拓扑生成、Packmol 建盒，并通过前端展示进度和分子结构。

项目已经具备清晰的 MVP 形态和较高的工程投入：有分层 Agent、统一错误模型、状态机、Gradio 前端、测试集、LLM mock eval 和较完整的文档体系。但它目前更接近“研究型内部工具/可迭代原型”，还不是完整可靠的端到端生产系统。

最关键的差距是：文档和产品叙述声称会自动完成 GROMACS EM/NVT/NPT/PROD，但当前主编排器只执行到 Packmol 初始盒子构建。`em.py`、`eq.py`、`prod.py` 已存在，但未接入 `PipelineOrchestrator._build_steps()` 主流程。

---

## 二、核验结果

### 测试基线

执行：

```bash
pytest -q
```

结果：

```text
268 passed, 3 failed, 1 skipped
```

失败项主要分两类：

- 测试仍按旧模块名断言，例如 `struct_maker`，但当前依赖注册使用 `struct_g16`。
- 部分测试通过固定源码行号定位 bug，源码行号漂移后测试失败。

结论：测试数量不少，但当前不是全绿基线；部分测试设计比较脆弱。

### 环境检查

执行：

```bash
python3 -m willy.env_checker
```

结果：

```text
18/19 ready
missing: BOSSdir
```

结论：当前机器上 GAFF/Sobtop 主线依赖基本就绪；LigParGen/OPLS 后备路径因 `BOSSdir` 缺失不可用。

### LLM Agent Mock Eval

执行：

```bash
python3 -m tests.llm_eval.run_eval
```

结果：

```text
18/18 scenarios passed
mean score: 86.9/100
```

关键发现：诊断和工具选择得分较高，但升级决策偏弱，存在过早升级或升级信息不完整的问题。

---

## 三、项目优点

### 1. 架构分层清晰

项目按领域拆分为：

- Layer 0：配置 Agent，自然语言到 `config.json`
- Layer 1：量子化学，G16/ORCA、fchk/mol2、RESP
- Layer 2：拓扑层，Sobtop/GAFF、LigParGen/OPLS、top assembly
- Layer 3：模拟层，MDP、Packmol、GROMACS EM/EQ/PROD
- 前端与状态：Gradio UI、`frontend_api.py`、`pipeline_state.py`

这种分层利于定位失败、分配工具权限，也适合后续扩展新的量子后端、力场后端或模拟协议。

### 2. 有统一错误和结果模型

`StepResult`、`StepError`、`ErrorKind` 是正确的工程方向。相比直接解析字符串，结构化错误能让 Agent 根据错误类别做决策，并把 `hint`、`raw_output`、`artifacts` 等上下文带入下一轮修复。

### 3. 领域覆盖较完整

当前代码覆盖了分子模拟自动化中的多个关键外部工具：

- Gaussian 16 / ORCA
- formchk / orca_2mkl
- Multiwfn RESP
- Sobtop / GAFF
- LigParGen / OPLS-AA
- Packmol
- GROMACS
- 3Dmol 分子查看器

这说明项目不是单点脚本，而是在尝试搭建完整科学计算流水线。

### 4. 前端具备实际操作能力

Gradio UI 已包含：

- 聊天式体系输入
- 方案确认按钮
- 文件上传
- 分子下拉选择
- 3D 分子查看器
- 流水线进度轮询
- 中止流水线

这已经超过普通 CLI 原型，具备内部工具产品化的基础。

### 5. 测试和文档投入明显

项目已有 10 个测试文件、12 份文档、LLM eval harness 和 benchmark。尤其是 `docs/project_gap_analysis.md` 已经主动记录了不少安全、架构和代码质量问题，说明团队有复盘和工程治理意识。

---

## 四、创新点

### 1. 分层 LLM 修复 Agent

项目最大的差异化不是“让 LLM 生成配置”，而是失败后按层调用 Agent：

- Quantum Agent 处理 SCF 不收敛、Gaussian/ORCA 崩溃、RESP 失败等。
- Topology Agent 处理 Sobtop、LigParGen、atomtype、top assembly 问题。
- Simulation Agent 处理 Packmol、GROMACS、EM/EQ/PROD 问题。

这比传统自动化脚本更进一步：流水线不仅能跑，还尝试在失败时诊断、修复、重试和升级。

### 2. 文档知识库即运行时数据源

`docs/knowledge.md` 同时面向人和程序：

- 人可以查分子、电荷、自旋、基组、力场和别名。
- 程序用 `_MoleculeRegistry` 正则解析表格，并构建 TF-IDF 检索索引。

这种设计降低了维护分子知识库的门槛。

### 3. LLM 行为可评估

`tests/llm_eval/` 用 mock 场景衡量：

- 诊断能力
- 工具选择
- 修复质量
- 升级策略

这对 LLM Agent 项目很重要，因为它能避免“看起来会推理，但实际无法稳定修复”的黑箱状态。

---

## 五、主要缺点和风险

### 1. 主流程没有真正完成 MD 闭环

README 叙述为：

```text
GROMACS: EM -> NVT -> NPT EQ -> PROD
```

但 `PipelineOrchestrator._build_steps()` 当前只包含 7 步：

1. 结构优化
2. SP + mol2
3. RESP 电荷
4. mol2+chg -> itp+gro
5. 主拓扑 + itp 修订
6. MDP 参数生成
7. Packmol 盒子

`em.py`、`eq.py`、`prod.py` 存在，但未接入主流程。因此“自动完成 GROMACS MD 模拟”的产品承诺目前不成立。

### 2. 配置验证工具存在运行时错误

`toolist_global.py` 中 `tools_validate_config` 分支导入：

```python
from willy.llm_config import tools_validate_config
```

但 `llm_config.py` 中实际函数名是 `validate_config`。实际调用会触发 `ImportError`。这是配置 Agent 的真实功能缺陷，不只是文档问题。

### 3. 依赖预检命名不一致

编排器对部分步骤使用：

```python
ensure("struct_maker")
ensure("chg_maker")
```

但 `env_checker.py` 注册的是：

```text
struct_g16, struct_orca, sp_g16, sp_orca, chg_resp
```

结果是某些关键依赖预检会静默失效，直到外部命令执行时才暴露问题。

### 4. 拓扑修订目录写死

`top_assembly.build(topo_dir=run_dir)` 可以生成运行目录内的 `topol.top`，但后续调用：

```python
revise_all(str(TOPO_DIR))
```

这里写死为项目根目录 `topo/`，没有修订传入的 `topo_dir`。如果主流程把 `.itp` 产物写入 `md_run/...`，运行目录中的 `.itp` 可能仍保留重复 `[atomtypes]` 段。

### 5. 安全边界不足

当前存在几个高风险点：

- Gradio 默认绑定 `0.0.0.0`，会暴露到局域网。
- `frontend_api.py` 使用 `os.system("pkill ...")` 和 `os.system("rm -f ...")`。
- `agent_config.py` 用 `shell=True` 启动流水线。
- 若 `.env` 中保存真实 API key，需要按泄露风险处理并轮换。

MD 控制面板具备启动外部计算和杀进程能力，不应在无认证情况下暴露。

### 6. 包依赖声明不完整

`pyproject.toml` 只声明：

```toml
dependencies = ["gradio", "openai", "py3Dmol"]
```

但源码直接依赖：

- `scikit-learn`
- `PyYAML`（作为 JSON fallback）
- `pytest`（测试）

这会导致新环境 `pip install -e .` 后不一定可运行。

### 7. 仓库卫生和产物管理较弱

当前仓库中包含较多运行产物和第三方二进制：

- `struct/*.chk`
- `struct/*.fchk`
- `struct/*.log`
- `md_run/*`
- `vendor/sobtop/sobtop`
- OpenBabel 共享库

优点是开箱体验较好；缺点是仓库体积、许可、复现实验和版本管理边界都更复杂。`.gitignore` 也较薄，未覆盖 `.md_counter`、`status.json`、运行目录等常见产物。

---

## 六、完整性评估

| 模块 | 完整度 | 评价 |
|------|:---:|------|
| 自然语言配置 | 70% | 有工具调用和知识库，但验证工具 broken，schema 不够硬 |
| 量子层 | 75% | G16/ORCA 主线较完整，仍有裸 `raise` 和预检命名问题 |
| 拓扑层 | 70% | Sobtop 主线较可用，LigParGen 当前依赖缺失，运行目录修订有 bug |
| 模拟层 | 45% | MDP/Packmol 接入，GROMACS EM/EQ/PROD 未进主流程 |
| 前端 | 65% | 可用原型，但安全、离线资源和进程管理需要收紧 |
| 测试 | 70% | 覆盖不少，但当前不全绿，部分测试脆弱 |
| 文档 | 75% | 文档丰富，但多处文件名、工具数和流程描述过期 |

综合判断：项目完成度约 65%。作为研究型 MVP 已有较好基础；作为完整端到端自动 MD 平台仍需补关键闭环和安全工程。

---

## 七、优先改进建议

### P0：修正阻塞主流程的问题

1. 修复 `tools_validate_config` 导入错误。
2. 统一 `env_checker` 的模块名和 `PipelineOrchestrator` 的 `ensure()` 调用。
3. 修复 `top_assembly.build()` 中 `itp_revise` 写死 `TOPO_DIR` 的问题。
4. 明确主流程到底是 7 步还是 10 步，并同步状态机和文档。

### P1：补齐真正端到端 MD

1. 在主编排器中接入 `collect_files()`。
2. 接入 `run_em()`。
3. 接入 NVT/NPT 平衡逻辑；当前只有 `eq.py` 的 NPT 平衡描述，需要明确是否拆分 NVT 和 NPT。
4. 接入 `run_prod()`。
5. 让 Simulation Agent 的工具和主流程保持一致。

### P2：安全和工程化

1. Gradio 默认改为 `127.0.0.1`，远程访问加认证。
2. 替换 `os.system`，尽量使用 `subprocess.run([...], shell=False)`。
3. 给 OpenAI/DeepSeek client 增加 timeout 和 retry/backoff。
4. 补齐 `pyproject.toml` 依赖和 dev/test extras。
5. 加 CI，至少运行 `pytest -q` 和基础 import check。

### P3：仓库和文档治理

1. 将运行产物和示例数据分离。
2. 为 vendor 二进制补来源、版本、license 说明。
3. 扩展 `.gitignore`。
4. 同步更新 README、`docs/README.md`、设计文档中的旧文件名和旧工具数。
5. 新增 `docs/simulation_design.md`，与 `quantum_design.md`、`topology_design.md` 对齐。

---

## 八、最终判断

Willy 的方向和架构值得继续投入。它的核心价值不只是自动跑脚本，而是尝试把科学计算流水线变成一个可对话、可诊断、可修复、可观测的系统。

当前最大短板是“产品承诺”和“实际主流程”还没有完全对齐。只要补上 GROMACS 端到端执行、修掉配置/预检/拓扑目录问题、收紧安全边界，并让测试基线全绿，它就可以从研究原型升级为比较扎实的内部自动化平台。
