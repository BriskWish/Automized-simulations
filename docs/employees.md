# Willy 开发团队

## 0. Claude — 主工程师（Prompt 设计 & Tool 统筹）

- **职责**: 面向 LLM 做 prompt 设计和 tool 整体统筹，定义 agent 分层架构、tools 拆分方案、状态机与前端接口规范
- **成果**:
  - 4 层 Agent 架构（Config/Quantum/Topology/Simulation），共 25 个 function calling tools
  - `layer_agent.py` — Agent 基类（LLM tool-calling 循环、重试计数、结构化 escalation）
  - `prompts.py` — 4 个 system prompt
  - `quantum_tools.py` / `topology_tools.py` / `simulation_tools.py` — 17 个层专属 tool 定义与分发
  - `pipeline_orchestrator.py` — LLM 编排执行器（正常路径零开销，失败时调 Agent）
  - `pipeline_state.py` — 6 态状态机（IDLE/RUNNING/RETRYING/ESCALATED/DONE/ABORTED），`status.json` 供前端轮询
  - `log_parsers.py` — Gaussian/ORCA/GROMACS 输出结构化解析
  - `run_pipeline.py` — CLI 入口（`--no-llm` 向后兼容）
  - Phase 1 统一错误返回：全流水线 13 个步骤函数返回 `StepResult`，消除 print/raise/sys.exit 混乱
  - `docs/status_api.md` — 前端接口文档
- **原则**: LLM on Failure Only（正常路径零 LLM 开销）、层间 tool 隔离不越界、状态机主动报告不靠猜、所有步骤统一返回 `StepResult`

## 1. Claude — 量子层接口专家

- **职责**: 量子计算层（`src/willy/quantum/`）的接口设计、实现与维护
- **成果**: G16/ORCA 双链路（6 个 maker）、7 项踩坑修复、`quantum_design.md` 设计文档
- **原则**: 一个 maker 只做一件事、下游只消费 `.mol2` + `.chg`、统一 `config.json` 配置源

## 2. Claude — 拓扑层接口专家

- **职责**: `src/willy/topology/` 下所有力场接口的设计、实现与维护
- **成果**: `sobtop_interface.py`（GAFF）、`ligpargen_interface.py`（OPLS-AA）、`topology_design.md` 设计文档
- **原则**: 统一出参 `{"itp": Path, "gro": Path}`、下游不感知力场、env_checker 统一管理依赖

## 3. Claude — 模拟层接口专家

> 待补充

## 4. Claude — 前端与交互工程师

- **职责**: `app.py` Gradio UI 设计、3D 分子查看器、对话交互体验
- **成果**: SCAN 风格 3D 查看器（浅色球棍模型 + 分类下拉）、确认按钮流程（文字确认向后兼容）、LLM 工具调用进度实时可见、状态机前端集成（`pipeline_state.py` → `_progress_ui` 六态渲染）、Gradio 6 兼容包装
- **原则**: 进度即时可见不黑盒、操作一键可达不绕路、视觉参考 SCAN 平台保持专业感、状态机而非文件轮询

## 5. Claude — 项目顾问 & 文档架构师

- **职责**: 项目架构评审、文档体系规范化、技术选型分析与对外呈现
- **成果**:
  - 5 份文档规范化：`knowledge.md`（新增分子注册说明、修正步骤数、补充 box 配置）、`Willy.md`（删除幽灵引用、补全 ORCA 后端、细化 vendor）、`quantum_design.md` / `topology_design.md`（统一章节编号、补齐原因字段、新增接口清单）
  - 新建 `README.md`（中文 GitHub 门面，包含架构图、快速开始、分子表、目录结构）和 `.env.example`
  - RAG / Harness 技术雏形分析：识别 `knowledge_tools.py` 中完整 TF-IDF 检索链路（Ingest→Index→Retrieve→Augment→Generate），以及 `errors.py` + `run_pipeline.py` + `env_checker.py` + `progress.py` + `benchmarks/` 构成的五组件 Harness 骨架
  - DeepSeek V4 Pro vs Claude Opus vs GPT-5.5 三模型对比：聚焦 Willy 场景的理解与推理优劣势，识别 `reasoning_content` 字段潜在风险，提出路由策略建议
  - 清理临时文件（FEC_run.gjf / gau.*）
- **原则**: 文档与代码同步——文档反映真实架构而非理想设计；统一格式降低认知负担——章节编号、issue 字段、命名风格全仓一致；分析有数据支撑——benchmark 对比落实到项目具体代码路径
