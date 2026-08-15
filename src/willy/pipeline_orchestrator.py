"""
pipeline_orchestrator.py
========================
LLM 编排的流水线执行器。

正常路径：直接执行步骤（零 LLM 开销）
失败路径：调用对应层的 LayerAgent 诊断→修复→重试

状态报告：集成 PipelineStateMachine，所有事件写入 status.json 供前端轮询。

用法:
  orchestrator = PipelineOrchestrator(backend="g16", use_llm=True)
  ok = orchestrator.run()  # True = 全流程完成
"""

from __future__ import annotations
import sys, json
import secrets
import shutil
from pathlib import Path
from typing import Optional

from willy._paths import get_project_root
from willy.config_store import copy_file, write_json
from willy.env_checker import EnvironmentDependencyError, ensure
from willy.errors import StepResult, StepError, ErrorKind, public_error_summary
from willy.layer_agent import LayerAgent
from willy.llm_config import LLMConfigError, configured_llm_client
from willy.pipeline_state import PipelineStateMachine, State
from willy.run_registry import RunRegistry, RunRegistryError
from willy.structured_log import append_structured_event
from willy.action_contract import build_default_tool_catalog
from willy.recovery_policy import default_recovery_policy
from willy.llm_budget import LLMBudget
from willy.step_registry import EM_STEP, EQ_STEP, MDP_STEP, PACKMOL_STEP, PROD_STEP, STEP_REGISTRY
from willy.quantum.input_audit import (
    apply_audited_quantum_properties,
    audit_config_quantum_inputs,
    quantum_input_contract_issues,
)

ROOT = get_project_root()
_SUPPORTED_QUANTUM_BACKENDS = frozenset({"g16", "g09", "orca"})

class PipelineOrchestrator:
    """流水线编排器 —— 执行 10 步流水线，失败时调用 LLM Agent。支持断点续跑。"""

    def __init__(self, backend: str = "g16", use_llm: bool = True,
                 resume_from: int = 0, resume_run_dir: str = "",
                 confirmed_action_id: str = "", controlled_resume_step: int = 0,
                 parent_run_id: str = "", control_action: dict[str, object] | None = None):
        self.backend = str(backend).strip().lower()
        if self.backend not in _SUPPORTED_QUANTUM_BACKENDS:
            raise ValueError("量子后端必须为 g16、g09 或 orca")
        self.use_llm = use_llm
        self.llm_client = None
        self.llm_model: str | None = None
        self._agents: dict[int, Optional[LayerAgent]] = {
            1: None, 2: None, 3: None,
        }
        self._run_dir: Optional[Path] = None
        self._run_config_path: Optional[Path] = None
        self._run_registry: Optional[RunRegistry] = None
        self._rollback_attempts: dict[int, int] = {}
        self._rollback_to_step: int | None = None
        self._rerun_step: int | None = None
        self._stop_after_step: int | None = None
        self._repair_rerun_attempts: dict[tuple[int, str], int] = {}
        self._confirmed_action_id = confirmed_action_id.strip()
        self._controlled_resume_step = int(controlled_resume_step)
        if self._controlled_resume_step and not 1 <= self._controlled_resume_step <= STEP_REGISTRY.total_steps:
            raise ValueError("受控续跑步骤无效")
        self._parent_run_id = parent_run_id.strip()
        self._control_action = dict(control_action or {})
        self._tool_catalog = build_default_tool_catalog()
        self._recovery_policy = default_recovery_policy(self._tool_catalog)
        self._llm_budget = LLMBudget.from_env() if use_llm else None

        # 断点续跑必须由调用方显式指定。绝不能从根 status.json 猜测，
        # 否则新任务会错误继承上个 run 的 done_steps 并跳过结构优化。
        self._resume_from = min(max(int(resume_from), 0), 1)
        self._resume_run_dir = Path(resume_run_dir) if resume_run_dir else None
        if self._resume_from and self._resume_run_dir is None:
            raise ValueError("断点续跑必须提供原运行目录")
        if self._resume_from > 0:
            print(f"[orchestrator] 🔄 断点续跑：Step 1 结构优化已完成，从 Step 2 开始")
            if self._resume_run_dir:
                print(f"[orchestrator]    复用运行目录: {self._resume_run_dir}")

        # A run is not a UI fact until its isolated workspace has been
        # registered.  Deferring writes prevents failed duplicate launches
        # from overwriting another run's status.json at the project root.
        self._sm = PipelineStateMachine(total_steps=STEP_REGISTRY.total_steps, defer_writes=True)
        initial_resume_from = (
            self._controlled_resume_step - 1
            if self._controlled_resume_step
            else self._resume_from
        )
        for s in range(1, initial_resume_from + 1):
            self._sm.mark_done(s)

        if use_llm:
            self._init_llm()
            self._init_agents()

    def _init_llm(self):
        try:
            self.llm_client, settings = configured_llm_client(ROOT)
        except LLMConfigError as exc:
            print(f"[orchestrator] ⚠ LLM 服务配置无效，禁用 LLM Agent: {exc}")
            self.use_llm = False
            return
        if self.llm_client is None or settings is None:
            print("[orchestrator] ⚠ 未配置 OpenAI-compatible LLM 服务，禁用 LLM Agent")
            self.use_llm = False
            return
        self.llm_model = settings.model

    def _init_agents(self):
        if not self.llm_client:
            return
        from willy.agent_quantum import QuantumAgent
        from willy.agent_topology import TopologyAgent
        from willy.agent_simulation import SimulationAgent

        self._agents[1] = QuantumAgent(
            llm_client=self.llm_client, max_retries=5,
            model=self.llm_model,
            on_action=lambda a: self._sm.add_action(a),
            on_decision=self._record_agent_decision,
            recovery_policy=self._recovery_policy, tool_catalog=self._tool_catalog,
            llm_budget=self._llm_budget,
        )
        self._agents[2] = TopologyAgent(
            llm_client=self.llm_client, max_retries=4,
            model=self.llm_model,
            on_action=lambda a: self._sm.add_action(a),
            on_decision=self._record_agent_decision,
            recovery_policy=self._recovery_policy, tool_catalog=self._tool_catalog,
            llm_budget=self._llm_budget,
        )
        self._agents[3] = SimulationAgent(
            llm_client=self.llm_client, max_retries=3,
            model=self.llm_model,
            on_action=lambda a: self._sm.add_action(a),
            on_decision=self._record_agent_decision,
            on_config_updated=self._record_simulation_config_update,
            recovery_policy=self._recovery_policy, tool_catalog=self._tool_catalog,
            llm_budget=self._llm_budget,
        )

    # ============================================================
    # 步骤构建
    # ============================================================

    def _build_steps(self, run_dir: Path) -> list[tuple]:
        from willy.quantum.struct_g16 import run_all as g16_struct
        from willy.quantum.struct_g09 import run_all as g09_struct
        from willy.quantum.struct_orca import run_all as orca_struct
        from willy.quantum.chg_resp import batch_make_chg as chg_resp
        from willy.topology.backends import dispatch_topology
        from willy.topology.top_assembly import build as build_top
        from willy.simulation.mdp import build_all as build_mdp
        from willy.simulation.box import auto_from_config, InpGenerator
        from willy.simulation._gmx_utils import build_stage_inputs
        from willy.simulation.em import run_em
        from willy.simulation.eq import run_eq
        from willy.simulation.prod import run_prod

        def _run_box(rd: str, config_path: str) -> StepResult:
            from willy.simulation.box import validate_box_preflight
            preflight = validate_box_preflight(config_path, rd)
            if not preflight.success:
                # Preflight may stop before Packmol starts; persist the same
                # bounded evidence so missing inputs are not confused with a
                # Packmol process failure and stale output cannot look valid.
                from willy.simulation.manifest import manifest_exists, record_box_execution
                evidence = preflight.extra.get("box_execution")
                if isinstance(evidence, dict) and manifest_exists(rd):
                    try:
                        record_box_execution(rd, evidence)
                    except (OSError, ValueError, RuntimeError) as exc:
                        return StepResult(
                            step_name="box", step_index=PACKMOL_STEP, success=False,
                            error=StepError(
                                ErrorKind.INPUT_CONTRACT,
                                f"无法记录建盒前置证据: {exc}",
                            ),
                        )
                return preflight
            try:
                config = auto_from_config(
                    config_path=config_path, output_dir=rd, gro_dir=rd, pdb_dir=rd,
                )
                gen = InpGenerator(config)
                result = gen.run()
                result.extra.setdefault("preflight", preflight.extra.get("preflight", {}))
                from willy.simulation.manifest import ManifestError, manifest_exists, record_box_execution
                evidence = result.extra.get("box_execution")
                if not isinstance(evidence, dict) and manifest_exists(rd):
                    raise ManifestError("Packmol 未返回执行证据")
                if isinstance(evidence, dict) and manifest_exists(rd):
                    record_box_execution(rd, evidence)
                return result
            except (OSError, ValueError, RuntimeError) as exc:
                return StepResult(
                    step_name="box", step_index=PACKMOL_STEP, success=False,
                    error=StepError(ErrorKind.INPUT_CONTRACT, f"建盒前置校验失败: {exc}"),
                )

        def _run_gromacs_stage(stage: str) -> StepResult:
            inputs = build_stage_inputs(run_dir, stage)
            kwargs = {
                "work_dir": str(run_dir),
                "mdp": str(inputs.mdp),
                "conf": str(inputs.coordinates),
                "topol": str(inputs.topol),
                "itps": [str(path) for path in inputs.itps],
                "tpr": str(inputs.tpr),
                "on_progress": on_prog,
                "on_heartbeat": lambda _snapshot: self._sm.heartbeat(),
            }
            if stage == "em":
                result = run_em(**kwargs)
            elif stage == "eq":
                result = run_eq(**kwargs)
            else:
                result = run_prod(**kwargs)
            result.target_type = "stage"
            result.target = stage
            return result

        gromacs_steps = [
            (STEP_REGISTRY.label_for(EM_STEP), lambda: _run_gromacs_stage("em"), "gromacs_em", False, 3),
            (STEP_REGISTRY.label_for(EQ_STEP), lambda: _run_gromacs_stage("eq"), "gromacs_eq", False, 3),
            (STEP_REGISTRY.label_for(PROD_STEP), lambda: _run_gromacs_stage("prod"), "gromacs_prod", False, 3),
        ]

        on_prog = lambda activity: self._sm.set_activity(**activity)
        cfg = str(self._run_config_path or (run_dir / "config.json"))

        if self.backend == "orca":
            return [
                ("ORCA 结构优化",    lambda: orca_struct(
                    config_path=cfg, struct_dir=str(run_dir), on_progress=on_prog),
                 "struct_orca", True, 1),
                ("SP + molden→mol2", lambda: _orca_sp_and_mol2(
                    cfg, on_prog, work_dir=run_dir),
                 "sp_orca", True, 1),
                ("RESP 电荷",        lambda: chg_resp(
                    struct_dir=str(run_dir), config_path=cfg, on_progress=on_prog),
                 "chg_resp", True, 1),
                ("分子拓扑参数化", lambda: dispatch_topology(
                    config_path=cfg, workspace=run_dir, on_progress=on_prog),
                 None, True, 2),
                ("主拓扑 + 修订 itp", lambda: build_top(
                    config_path=cfg, topo_dir=str(run_dir)),
                 None, False, 2),
                ("生成 mdp",          lambda: build_mdp(
                    config_path=cfg, output_dir=str(run_dir), on_progress=on_prog),
                 None, False, 3),
                ("Packmol 盒子",      lambda: _run_box(str(run_dir), cfg),
                 "box", False, 3),
            ] + gromacs_steps
        if self.backend == "g09":
            return [
                ("g09 优化 + formchk", lambda: g09_struct(
                    config_path=cfg, struct_dir=str(run_dir), on_progress=on_prog),
                 "struct_g09", True, 1),
                ("SP + mol2",           lambda: _g09_sp_and_mol2(
                    cfg, on_prog, work_dir=run_dir),
                 "sp_g09", True, 1),
                ("RESP 电荷",           lambda: chg_resp(
                    struct_dir=str(run_dir), config_path=cfg, on_progress=on_prog),
                 "chg_resp", True, 1),
                ("分子拓扑参数化",       lambda: dispatch_topology(
                    config_path=cfg, workspace=run_dir, on_progress=on_prog),
                 None, True, 2),
                ("主拓扑 + 修订 itp",    lambda: build_top(
                    config_path=cfg, topo_dir=str(run_dir)),
                 None, False, 2),
                ("生成 mdp",             lambda: build_mdp(
                    config_path=cfg, output_dir=str(run_dir), on_progress=on_prog),
                 None, False, 3),
                ("Packmol 盒子",         lambda: _run_box(str(run_dir), cfg),
                 "box", False, 3),
            ] + gromacs_steps
        return [
            ("g16 优化 + formchk", lambda: g16_struct(
                config_path=cfg, struct_dir=str(run_dir), on_progress=on_prog),
             "struct_g16", True, 1),
            ("SP + mol2",           lambda: _g16_sp_and_mol2(
                cfg, on_prog, work_dir=run_dir),
             "sp_g16", True, 1),
            ("RESP 电荷",           lambda: chg_resp(
                struct_dir=str(run_dir), config_path=cfg, on_progress=on_prog),
             "chg_resp", True, 1),
            ("分子拓扑参数化",       lambda: dispatch_topology(
                config_path=cfg, workspace=run_dir, on_progress=on_prog),
             None, True, 2),
            ("主拓扑 + 修订 itp",    lambda: build_top(
                config_path=cfg, topo_dir=str(run_dir)),
             None, False, 2),
            ("生成 mdp",             lambda: build_mdp(
                config_path=cfg, output_dir=str(run_dir), on_progress=on_prog),
             None, False, 3),
            ("Packmol 盒子",         lambda: _run_box(str(run_dir), cfg),
             "box", False, 3),
        ] + gromacs_steps

    def _prepare_run_directory(self, run_dir: Path) -> Path:
        """Create an isolated run workspace from the immutable project inputs.

        The raw source is mandatory and backend-specific: G16 consumes
        ``.gjf`` and ORCA consumes ``.inp``.  A precomputed Step 1 result is
        optional and may be copied for reuse, but can never replace the raw
        input audit or raw-source snapshot.
        """
        run_dir.mkdir(parents=True, exist_ok=True)
        config_source = ROOT / "config.json"
        config_snapshot = run_dir / "config.json"
        if not config_snapshot.exists():
            if not config_source.exists():
                raise FileNotFoundError(f"缺少项目配置文件: {config_source}")
            copy_file(config_source, config_snapshot)

        # Bind status persistence as soon as this run owns a workspace.  Any
        # following setup failure belongs to this run, never to a root status.
        self._run_dir = run_dir
        self._run_config_path = config_snapshot
        self._run_registry = RunRegistry(ROOT)
        self._run_registry.register_run(
            run_dir,
            backend=self.backend,
            total_steps=self._sm.total_steps,
            parent_run_id=self._parent_run_id or None,
        )
        from willy.env_registry import public_capabilities
        capabilities = public_capabilities()
        self._sm.bind_status_path(run_dir / "status.json")
        self._sm.bind_observer(self._record_run_status)
        self._sm.set_extra(run_id=run_dir.name)

        with config_snapshot.open() as f:
            config = json.load(f)
        from willy.workflow_config import validate_config
        configured_backend = str(config.get("backend", "g16")).strip().lower()
        if configured_backend != self.backend:
            raise ValueError(
                f"配置量子后端 {configured_backend!r} 与启动后端 {self.backend!r} 不一致"
            )
        reusable_suffix = ".fchk" if self.backend in {"g16", "g09"} else ".molden"
        raw_suffix = ".gjf" if self.backend in {"g16", "g09"} else ".inp"
        missing_quantum_inputs: list[str] = []
        for name in config.get("molecules", {}):
            source_input = ROOT / "struct" / f"{name}{raw_suffix}"
            source_intermediate = ROOT / "struct" / f"{name}{reusable_suffix}"
            target_input = run_dir / source_input.name
            target_intermediate = run_dir / source_intermediate.name
            if not target_input.exists() and source_input.is_file():
                shutil.copy2(source_input, target_input)
            if not target_intermediate.exists() and source_intermediate.is_file():
                shutil.copy2(source_intermediate, target_intermediate)
            if not target_input.is_file():
                missing_quantum_inputs.append(name)
        if missing_quantum_inputs:
            raise ValueError(
                f"{', '.join(missing_quantum_inputs)} 缺少 {raw_suffix} 原始输入"
            )

        # Run-local raw sources are authoritative for a fork too.  A parent
        # snapshot must not be revalidated against a subsequently edited root
        # struct/ directory.
        audit = audit_config_quantum_inputs(config, struct_dir=run_dir)
        if not audit.get("ok"):
            raise ValueError("量子输入审计未通过: " + "; ".join(audit.get("issues", [])))
        proposed_input_issues = quantum_input_contract_issues(config, audit)
        if proposed_input_issues:
            raise ValueError("量子输入与已确认方案不一致: " + "; ".join(proposed_input_issues))
        config = apply_audited_quantum_properties(config, audit)
        input_issues = quantum_input_contract_issues(config, audit)
        if input_issues:
            raise ValueError("量子输入契约无效: " + "; ".join(input_issues))
        from willy.remote_registry import merge_execution_md_defaults
        self._stop_after_step = None
        execution = config.get("execution")
        if isinstance(execution, dict):
            execution["md"] = merge_execution_md_defaults(execution.get("md"))
        elif execution is None:
            config["execution"] = {"md": merge_execution_md_defaults(None)}
        issues = validate_config(config)
        if issues:
            raise ValueError("配置契约无效: " + "; ".join(issues))
        if config.get("execution", {}).get("stop_after_stage") == "eq":
            self._stop_after_step = EQ_STEP
        md = config.setdefault("md", {})
        run_seed = md.get("run_seed")
        if run_seed is None:
            run_seed = secrets.randbelow(2_147_483_646) + 1
            md["run_seed"] = run_seed
        else:
            run_seed = int(run_seed)
        # Persist source-authoritative charge/spin even when the seed existed.
        write_json(config_snapshot, config)
        # Registration precedes this final snapshot write; refresh the registry
        # hash after all audited fields and the run seed are persisted.
        self._run_registry.refresh_config_fingerprint(run_dir)
        # The RunRegistry was created before copying inputs so early status
        # writes have an owner.  Refresh only its frozen input fingerprints
        # now that this run's backend-specific sources are present.
        self._run_registry.refresh_input_files(run_dir)

        for agent in self._agents.values():
            if agent is not None and hasattr(agent, "set_workspace"):
                agent.set_workspace(str(run_dir), str(config_snapshot))
        from willy.simulation.manifest import initialize_manifest
        initialize_manifest(run_dir, config_snapshot, random_seed=run_seed)
        from willy.run_provenance import create_or_refresh_provenance
        create_or_refresh_provenance(
            run_dir,
            project_root=ROOT,
            backend=self.backend,
            config_path=config_snapshot,
            random_seed=run_seed,
            capabilities=capabilities,
            llm_model=self.llm_model,
            prompt_versions={
                agent.name: agent.prompt_version
                for agent in self._agents.values()
                if agent is not None
            },
        )
        if self._control_action:
            try:
                self._run_registry.record_control_action(
                    run_dir.name,
                    action=str(self._control_action.get("action", "fork")),
                    outcome="created",
                    restart_step=self._control_action.get("restart_step"),
                    stopped_step=self._control_action.get("stopped_step"),
                    parameter_paths=self._control_action.get("parameter_paths", ()),
                    parent_run_id=self._parent_run_id or None,
                )
            except (OSError, ValueError, RunRegistryError):
                pass
        return config_snapshot

    # ============================================================
    # 执行
    # ============================================================

    def run(self, run_dir: Optional[Path] = None) -> bool:
        if self._confirmed_action_id:
            if run_dir is None:
                raise ValueError("确认后的 EQ 重跑必须提供原运行目录")
            return self._run_confirmed_eq_action(Path(run_dir).resolve())
        # ── 断点续跑：复用上次运行目录 ──
        if run_dir is None:
            if self._resume_run_dir:
                run_dir = Path(self._resume_run_dir)
                if not run_dir.exists():
                    raise ValueError(f"续跑目录不存在: {run_dir}")
            else:
                raise ValueError("必须通过 PipelineLaunch 分配 run_dir")

        run_dir = run_dir.resolve()
        try:
            if self._controlled_resume_step:
                config_path = self._bind_controlled_resume_run(run_dir)
            else:
                config_path = self._prepare_run_directory(run_dir)
        except (OSError, ValueError, json.JSONDecodeError, RunRegistryError) as exc:
            self._sm.set_error(
                public_error_summary("流水线", "运行初始化", "当前体系", ErrorKind.CONFIG_INVALID),
                ErrorKind.CONFIG_INVALID.value,
            )
            self._sm.transition(State.ABORTED)
            return False

        append_structured_event(
            run_dir,
            "run_started",
            source="orchestrator",
            layer="pipeline",
            outcome="started",
            message_code="run.started",
        )
        print(f"[orchestrator] 运行目录: {run_dir}")
        print(f"[orchestrator] 后端: {self.backend}")
        print(f"[orchestrator] LLM Agent: {'启用' if self.use_llm else '禁用'}")
        if self._resume_from > 0 or self._controlled_resume_step:
            restart_step = self._controlled_resume_step or self._resume_from + 1
            print(f"[orchestrator] 断点续跑: 从 Step {restart_step} 重新开始")

        steps = self._build_steps(run_dir)
        accumulated_artifacts: dict[str, list[str]] = {}

        self._sm.transition(State.RUNNING)

        if self._abort_if_stop_requested(run_dir):
            return False

        step_position = 1
        while step_position <= len(steps):
            if self._abort_if_stop_requested(run_dir):
                return False
            i = step_position
            label, func, dep_module, is_batch, layer_index = steps[i - 1]
            # ── 断点续跑：跳过已完成步骤 ──
            resume_from = self._controlled_resume_step - 1 if self._controlled_resume_step else self._resume_from
            if i <= resume_from:
                append_structured_event(
                    run_dir,
                    "step_skipped",
                    step=i,
                    step_name=label,
                    layer=STEP_REGISTRY.layer_for(i),
                    source="orchestrator",
                    outcome="skipped",
                    message_code="step.resume_skip",
                )
                print(f"\n  {i}/{len(steps)} {label}  ⏭ 已完成，跳过")
                step_position += 1
                continue

            self._sm.set_step(i, label, STEP_REGISTRY.layer_for(i))
            self._sm.set_activity(**self._initial_activity(i))
            append_structured_event(
                run_dir,
                "step_started",
                step=i,
                step_name=label,
                layer=STEP_REGISTRY.layer_for(i),
                source="orchestrator",
                outcome="started",
                message_code="step.started",
            )

            if dep_module:
                try:
                    ensure(dep_module)
                except EnvironmentDependencyError as exc:
                    error_kind = self._environment_error_kind(exc)
                    result = StepResult(
                        step_name=label,
                        step_index=i,
                        success=False,
                        error=StepError(
                            kind=error_kind,
                            message="当前步骤的运行环境预检未通过",
                            hint="安装或重新编译与当前系统兼容的运行工具后重新提交",
                        ),
                        extra={
                            "dependency_preflight": {
                                "module": dep_module,
                                "failures": [
                                    {"name": item.name, "status": item.status}
                                    for item in exc.report.failed()
                                ],
                                "automatic_retry_allowed": False,
                            },
                            "automatic_retry_allowed": False,
                        },
                    )
                    self._record_step_result(result, label, source="environment_preflight")
                    self._escalate_nonretryable_environment_failure(
                        result, label, run_dir, source="environment_preflight",
                    )
                    return False

            print(f"\n{'='*60}\n  {i}/{len(steps)} {label}\n{'='*60}")

            result = func()
            if self._abort_if_stop_requested(run_dir):
                return False
            if is_batch:
                for item in result:
                    self._record_step_result(item, label)
            else:
                self._record_step_result(result, label)

            if is_batch:
                ok = self._handle_batch_result(result, label, run_dir, accumulated_artifacts, layer_index, i)
            else:
                ok = self._handle_single_result(result, label, run_dir, accumulated_artifacts, layer_index, i)

            if ok:
                if self._rerun_step == i:
                    self._rerun_step = None
                    continue
                if self._rollback_to_step is not None:
                    step_position = self._rollback_to_step
                    self._rollback_to_step = None
                    continue
                completion_failure = self._completion_contract_failure(i, run_dir)
                if completion_failure is not None:
                    self._record_step_result(completion_failure, label)
                    self._set_public_error(completion_failure.error, completion_failure.target)
                    self._sm.transition(State.ABORTED)
                    print(f"[{label}] ❌ 阶段完成契约未满足，禁止进入下一步")
                    return False
                visualization_failure = self._prod_visualization_contract_failure(i, run_dir)
                if visualization_failure is not None:
                    self._record_step_result(visualization_failure, label)
                    self._set_public_error(visualization_failure.error, visualization_failure.target)
                    self._sm.transition(State.ABORTED)
                    print(f"[{label}] ❌ PROD 可视化产物契约未满足，禁止结束流程")
                    return False
                cleanup_failure = self._prod_intermediate_cleanup_contract_failure(i, run_dir)
                if cleanup_failure is not None:
                    self._record_step_result(cleanup_failure, label)
                    self._set_public_error(cleanup_failure.error, cleanup_failure.target)
                    self._sm.transition(State.ABORTED)
                    print(f"[{label}] ❌ PROD 收尾清理未完成，禁止结束流程")
                    return False
                self._sm.mark_done(i)
                if STEP_REGISTRY.stage_for(i) != "prod":
                    self._schedule_stage_visualization(run_dir, i)
                if self._stop_after_step == i:
                    self._sm.set_extra(completion_scope={
                        "mode": "through_eq",
                        "step": i,
                        "stage": "eq",
                    })
                    self._sm.transition(
                        State.DONE,
                        step=i,
                        step_label=label,
                        layer=STEP_REGISTRY.layer_for(i),
                    )
                    print(
                        f"\n{'='*60}\n"
                        f"  ✅ 已按测试范围完成至 EQ\n"
                        f"  产物: {run_dir}/\n"
                        f"{'='*60}"
                    )
                    return True
                step_position += 1
            else:
                return False

        self._sm.transition(State.DONE)
        print(f"\n{'='*60}\n  ✅ 全流程完成\n  产物: {run_dir}/\n{'='*60}")
        return True

    def _bind_controlled_resume_run(self, run_dir: Path) -> Path:
        """Bind an API-authorized same-run replay without recreating metadata."""
        config_path = run_dir / "config.json"
        registry = RunRegistry(ROOT)
        self._run_dir = run_dir
        self._run_config_path = config_path
        self._run_registry = registry
        self._sm.bind_status_path(run_dir / "status.json")
        self._sm.bind_observer(self._record_run_status)
        if not config_path.is_file():
            raise ValueError("恢复运行缺少配置快照")
        manifest = registry._read_registry_manifest(run_dir)
        if manifest.get("backend") != self.backend:
            raise ValueError("恢复运行的后端与冻结配置不一致")
        # A normal resume preserves its existing MD manifest.  An early fork
        # from the original rollout could contain only the registry section,
        # which is not a valid MD manifest for the MDP metadata writer.
        try:
            from willy.simulation.manifest import (
                ManifestError,
                initialize_manifest,
                load_manifest,
            )
            try:
                load_manifest(run_dir)
            except ManifestError:
                config = json.loads(config_path.read_text(encoding="utf-8"))
                md = config.get("md")
                raw_seed = md.get("run_seed", 1) if isinstance(md, dict) else 1
                initialize_manifest(run_dir, config_path, random_seed=int(raw_seed))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(f"恢复运行的 MD manifest 无效: {exc}") from exc
        status = registry.get_run_status(run_dir.name, reconcile=False)
        if status.get("state") != State.RETRYING.value:
            raise ValueError("恢复运行未进入受控重试状态")
        self._sm.restore_for_controlled_resume(status)
        self._sm.invalidate_for_controlled_restart(self._controlled_resume_step)
        for agent in self._agents.values():
            if agent is not None and hasattr(agent, "set_workspace"):
                agent.set_workspace(str(run_dir), str(config_path))
        return config_path

    def _bind_confirmed_action_run(self, run_dir: Path) -> Path:
        """Bind an existing waiting run without reinitializing its inputs."""
        config_path = run_dir / "config.json"
        if not config_path.is_file():
            raise ValueError("恢复运行缺少配置快照")
        registry = RunRegistry(ROOT)
        registry.register_run(run_dir, backend=self.backend, total_steps=self._sm.total_steps)
        status = registry.get_run_status(run_dir.name)
        if status.get("state") not in {
            State.AWAITING_CONFIRMATION.value,
            State.RETRYING.value,
        }:
            raise ValueError("当前运行不处于等待确认状态")
        pending = status.get("extra", {}).get("pending_action", {})
        if not isinstance(pending, dict) or pending.get("action_id") != self._confirmed_action_id:
            raise ValueError("待确认方案与当前运行不匹配")
        self._run_dir = run_dir
        self._run_config_path = config_path
        self._run_registry = registry
        self._sm.bind_status_path(run_dir / "status.json")
        self._sm.bind_observer(self._record_run_status)
        self._sm.restore_for_controlled_resume(status)
        for agent in self._agents.values():
            if agent is not None and hasattr(agent, "set_workspace"):
                agent.set_workspace(str(run_dir), str(config_path))
        return config_path

    def _run_confirmed_eq_action(self, run_dir: Path) -> bool:
        """Apply one user-approved EQ proposal and rerun EQ before PROD."""
        try:
            self._bind_confirmed_action_run(run_dir)
            from willy.simulation.pending_action import apply_pending_action, public_pending_action
            action = apply_pending_action(run_dir, self._confirmed_action_id)
            from willy.env_registry import public_capabilities
            from willy.run_provenance import create_or_refresh_provenance
            config = json.loads((run_dir / "config.json").read_text(encoding="utf-8"))
            create_or_refresh_provenance(
                run_dir,
                project_root=ROOT,
                backend=self.backend,
                config_path=run_dir / "config.json",
                random_seed=int(config.get("md", {}).get("run_seed", 1)),
                capabilities=public_capabilities(),
                llm_model=self.llm_model,
                prompt_versions={
                    agent.name: agent.prompt_version
                    for agent in self._agents.values()
                    if agent is not None
                },
            )
        except (OSError, ValueError, RunRegistryError) as exc:
            self._sm.set_error(
                public_error_summary("流水线", "确认重跑", "当前体系", ErrorKind.CONFIG_INVALID),
                ErrorKind.CONFIG_INVALID.value,
            )
            self._sm.transition(State.ABORTED)
            print(f"[orchestrator] 确认后的 EQ 重跑无法启动: {exc}")
            return False

        public_action = public_pending_action(action)
        restart_step = int(public_action["restart_step"])

        def record_execution(success: bool, summary: str, error_kind: str = "") -> None:
            if self._run_registry is None:
                return
            try:
                from willy.simulation.pending_action import pending_action_executed
                executed = pending_action_executed(
                    action,
                    success=success,
                    result_summary=summary,
                    output_keys=("eq", "prod") if success else (),
                    error_kind=error_kind,
                )
                self._record_agent_decision({
                    "decision_id": executed.decision_id,
                    "action_id": executed.decision_id,
                    "layer": executed.action.proposal.layer,
                    "step": executed.action.proposal.failed_step,
                    "policy_id": executed.action.policy_id,
                    "selected_tool": executed.action.proposal.tool_name,
                    "tool_effect": executed.action.effective_effect.value,
                    "parameter_changes": [change.field_name for change in executed.action.proposal.parameter_changes],
                    "model_id": executed.action.proposal.model_id,
                    "prompt_version": executed.action.proposal.prompt_version,
                    "restart_step": restart_step,
                    "result": "executed",
                    "success": executed.success,
                })
            except (OSError, ValueError):
                pass

        # A user approval first becomes an auditable controlled retry.  Do not
        # advertise normal execution until all affected MDPs have been rebuilt
        # and the replay is about to invoke the first scientific stage.
        self._sm.start_retry("simulation", 1, 1)
        self._sm.invalidate_for_controlled_restart(restart_step)
        self._sm.set_step(restart_step, STEP_REGISTRY.label_for(restart_step), "simulation")
        self._sm.add_adjustments(public_action.get("adjustments", []))
        self._sm.add_action(
            "用户已确认 EQ 修复方案，重新生成参数并从指定阶段重新验收"
        )
        if self._abort_if_stop_requested(run_dir):
            record_execution(False, "用户在确认后的 EQ 重跑前请求停止")
            return False

        from willy.simulation.mdp import build_all
        mdp_result = build_all(
            config_path=str(self._run_config_path),
            output_dir=str(run_dir),
            stages=("em", "eq", "prod") if restart_step == PACKMOL_STEP else ("eq", "prod"),
            on_progress=lambda activity: self._sm.set_activity(**activity),
        )
        self._record_step_result(mdp_result, "重新生成受影响 MDP", source="pending_action")
        if not mdp_result.success:
            self._set_public_error(mdp_result.error, "eq")
            self._sm.transition(State.ABORTED)
            record_execution(False, "受影响 MDP 重新生成失败", ErrorKind.CONFIG_INVALID.value)
            return False

        steps = self._build_steps(run_dir)
        artifacts: dict[str, list[str]] = {}
        self._sm.transition(State.RUNNING)
        for step_i in STEP_REGISTRY.indices_from(restart_step):
            if self._abort_if_stop_requested(run_dir):
                record_execution(False, "用户在确认后的 EQ 重跑期间请求停止")
                return False
            label, func, dep_module, _is_batch, layer_index = steps[step_i - 1]
            self._sm.set_step(step_i, label, STEP_REGISTRY.layer_for(step_i))
            self._sm.set_activity(**self._initial_activity(step_i))
            append_structured_event(
                run_dir,
                "step_started",
                step=step_i,
                step_name=label,
                layer=STEP_REGISTRY.layer_for(step_i),
                source="controlled_retry",
                outcome="started",
                message_code="step.started",
            )
            if dep_module:
                try:
                    ensure(dep_module)
                except EnvironmentDependencyError as exc:
                    error_kind = self._environment_error_kind(exc)
                    failure = StepResult(
                        step_name=label,
                        step_index=step_i,
                        success=False,
                        error=StepError(
                            kind=error_kind,
                            message="确认重跑的运行环境预检未通过",
                            hint="安装或重新编译与当前系统兼容的运行工具后重新提交",
                        ),
                        extra={
                            "dependency_preflight": {
                                "module": dep_module,
                                "failures": [
                                    {"name": item.name, "status": item.status}
                                    for item in exc.report.failed()
                                ],
                                "automatic_retry_allowed": False,
                            },
                            "automatic_retry_allowed": False,
                        },
                    )
                    self._record_step_result(failure, label, source="environment_preflight")
                    self._escalate_nonretryable_environment_failure(
                        failure, label, run_dir, source="environment_preflight",
                    )
                    record_execution(False, "确认重跑依赖不可用", error_kind.value)
                    return False
            result = func()
            if self._abort_if_stop_requested(run_dir):
                record_execution(False, "用户在确认后的 EQ 重跑期间请求停止")
                return False
            self._record_step_result(result, label)
            if not self._handle_single_result(result, label, run_dir, artifacts, layer_index, step_i):
                kind = result.error.kind.value if result.error else ErrorKind.UNKNOWN.value
                record_execution(False, "确认后的 EQ 重跑未通过后续验收", kind)
                return False
            completion_failure = self._completion_contract_failure(step_i, run_dir)
            if completion_failure is not None:
                self._record_step_result(completion_failure, label)
                self._set_public_error(completion_failure.error, completion_failure.target)
                self._sm.transition(State.ABORTED)
                kind = completion_failure.error.kind.value if completion_failure.error else ErrorKind.UNKNOWN.value
                record_execution(False, "确认后的重跑未满足阶段产物契约", kind)
                return False
            visualization_failure = self._prod_visualization_contract_failure(step_i, run_dir)
            if visualization_failure is not None:
                self._record_step_result(visualization_failure, label)
                self._set_public_error(visualization_failure.error, visualization_failure.target)
                self._sm.transition(State.ABORTED)
                kind = visualization_failure.error.kind.value if visualization_failure.error else ErrorKind.UNKNOWN.value
                record_execution(False, "确认后的重跑未生成 PROD 可视化产物", kind)
                return False
            cleanup_failure = self._prod_intermediate_cleanup_contract_failure(step_i, run_dir)
            if cleanup_failure is not None:
                self._record_step_result(cleanup_failure, label)
                self._set_public_error(cleanup_failure.error, cleanup_failure.target)
                self._sm.transition(State.ABORTED)
                kind = cleanup_failure.error.kind.value if cleanup_failure.error else ErrorKind.UNKNOWN.value
                record_execution(False, "确认后的重跑未完成 PROD 收尾清理", kind)
                return False
            self._sm.mark_done(step_i)
            if STEP_REGISTRY.stage_for(step_i) != "prod":
                self._schedule_stage_visualization(run_dir, step_i)

        self._sm.transition(State.DONE)
        record_execution(True, "确认后的 EQ 重跑及 PROD 已完成")
        print(f"[orchestrator] 已完成确认后的 EQ 重跑及 PROD: {run_dir}")
        return True

    def abort_from_signal(self) -> None:
        """Finalize a UI/terminal signal as a user stop when a run is bound."""
        if self._run_dir is None:
            return
        try:
            from willy.simulation.manifest import clear_stop_request
            clear_stop_request(self._run_dir)
        except OSError:
            pass
        self._sm.set_aborted(user_requested=True)

    def _abort_if_stop_requested(self, run_dir: Path) -> bool:
        """Consume a persisted user request before an error can enter retry logic."""
        try:
            from willy.simulation.manifest import clear_stop_request, stop_requested
            if not stop_requested(run_dir):
                return False
            clear_stop_request(run_dir)
        except OSError:
            return False
        self._sm.set_aborted(user_requested=True)
        print("[orchestrator] 已按用户请求中止当前运行")
        return True

    def _record_run_status(self, status, event_type: str) -> None:
        """Mirror state into the active run; failures are handled by the observer."""
        if self._run_registry is not None and self._run_dir is not None:
            self._run_registry.record_status(self._run_dir, status.__dict__, event_type)
            # Heartbeats remain in their dedicated ETA stream; all other
            # state/activity updates are compact run-local structured facts.
            if event_type != "runtime_heartbeat":
                extra = status.extra if isinstance(status.extra, dict) else {}
                pending = extra.get("pending_action", {})
                action_id = pending.get("action_id") if isinstance(pending, dict) else None
                append_structured_event(
                    self._run_dir,
                    "state_changed",
                    step=status.step,
                    step_name=status.step_label,
                    layer=status.layer,
                    source="state_machine",
                    outcome=status.state,
                    error_kind=status.error_kind or None,
                    action_id=action_id,
                    message_code=event_type,
                )

    def _record_agent_decision(self, trace: dict[str, object]) -> None:
        """Persist one bounded Agent decision only after a run is bound."""
        if self._run_registry is None or self._run_dir is None:
            return
        try:
            self._run_registry.append_decision_trace(self._run_dir, trace)
        except OSError:
            pass
        append_structured_event(
            self._run_dir,
            "decision_recorded",
            step=trace.get("step"),
            layer=trace.get("layer"),
            source="agent",
            outcome=trace.get("result") or ("accepted" if trace.get("success") else "proposed"),
            error_kind=trace.get("error_kind"),
            action_id=trace.get("action_id") or trace.get("decision_id"),
            policy_id=trace.get("policy_id"),
            model_id=trace.get("model_id"),
            prompt_version=trace.get("prompt_version"),
            parameter_fields=trace.get("parameter_changes"),
            message_code="decision.trace",
        )

    def _record_step_result(self, result: StepResult, label: str, source: str = "pipeline") -> None:
        """Add a read-only audit record without changing pipeline success semantics."""
        if self._run_dir is None:
            return
        # Constructing an observational record must never reject an otherwise
        # valid StepResult (for example, a legacy fixture with an unknown index).
        try:
            layer = STEP_REGISTRY.layer_for(result.step_index)
        except (AttributeError, TypeError, ValueError):
            layer = None
        try:
            duration_ms = max(0.0, float(result.duration_s) * 1000.0)
        except (AttributeError, TypeError, ValueError):
            duration_ms = None
        try:
            artifact_refs = list(result.outputs.keys()) if isinstance(result.outputs, dict) else None
        except (AttributeError, TypeError):
            artifact_refs = None
        try:
            error_kind = result.error.kind.value if result.error else None
            append_structured_event(
                self._run_dir,
                "step_result",
                step=result.step_index,
                step_name=label or result.step_name,
                layer=layer,
                source=source,
                outcome="succeeded" if result.success else "failed",
                error_kind=error_kind,
                duration_ms=duration_ms,
                artifact_refs=artifact_refs,
                message_code="step.result",
            )
        except Exception:
            pass
        if self._run_registry is None:
            return
        try:
            self._run_registry.record_step_result(
                self._run_dir,
                result,
                label=label,
                source=source,
                activity=self._sm._status.activity,
            )
        except Exception as exc:
            print(f"[orchestrator] ⚠ 运行步骤审计写入失败: {exc}")

    def _initial_activity(self, step: int) -> dict:
        """Return a public, Chinese activity placeholder before a callback fires."""
        backend_tool = {"g16": "G16", "g09": "G09", "orca": "ORCA"}[self.backend]
        activities = {
            STEP_REGISTRY.by_id("quantum_optimize").index: (backend_tool, "结构优化", "molecule", "待处理分子", 0, 0),
            STEP_REGISTRY.by_id("quantum_singlepoint_mol2").index: (backend_tool, "单点计算与 mol2 转换", "molecule", "待处理分子", 0, 0),
            STEP_REGISTRY.by_id("quantum_resp").index: ("Multiwfn", "RESP 电荷计算", "molecule", "待处理分子", 0, 0),
            STEP_REGISTRY.by_id("topology_parameterize").index: ("拓扑工具", "拓扑参数化", "molecule", "待处理分子", 0, 0),
            STEP_REGISTRY.by_id("topology_assemble").index: ("拓扑组装", "主拓扑生成", "system", "当前体系", 1, 1),
            MDP_STEP: ("MDP", "参数生成", "stage", "待生成阶段", 0, 4),
            PACKMOL_STEP: ("Packmol", "初始盒子构建", "system", "当前体系", 1, 1),
            EM_STEP: ("GROMACS", "输入预处理", "stage", "em", 0, 2),
            EQ_STEP: ("GROMACS", "输入预处理", "stage", "eq", 0, 2),
            PROD_STEP: ("GROMACS", "输入预处理", "stage", "prod", 0, 2),
        }
        tool, operation, target_type, target, current, total = activities[step]
        return {
            "tool": tool, "operation": operation,
            "target_type": target_type, "target": target,
            "current": current, "total": total,
        }

    def _set_public_error(self, error: StepError | ErrorKind | None, target: str = "") -> None:
        """Persist only a tool/action/object summary to the public state."""
        activity = self._sm._status.activity
        if not activity:
            step = self._sm._status.step
            activity = self._initial_activity(step) if step else {
                "tool": "流水线", "operation": "工序执行",
                "target_type": "system", "target": "当前体系",
                "current": 1, "total": 1,
            }
        resolved_target = target or activity["target"]
        kind = error.kind if isinstance(error, StepError) else error
        self._sm.set_error(
            public_error_summary(activity["tool"], activity["operation"], resolved_target, error),
            kind.value if isinstance(kind, ErrorKind) else "unknown",
        )

    @staticmethod
    def _environment_error_kind(exc: EnvironmentDependencyError) -> ErrorKind:
        return (
            ErrorKind.RUNTIME_UNAVAILABLE
            if exc.runtime_unavailable
            else ErrorKind.DEPENDENCY_MISSING
        )

    @staticmethod
    def _is_nonretryable_environment_error(result: StepResult) -> bool:
        return bool(result.error and result.error.kind in {
            ErrorKind.DEPENDENCY_MISSING,
            ErrorKind.DEPENDENCY_NO_EXEC,
            ErrorKind.RUNTIME_UNAVAILABLE,
        })

    def _escalate_nonretryable_environment_failure(
        self,
        result: StepResult,
        label: str,
        run_dir: Path,
        *,
        source: str,
    ) -> bool:
        """End an immutable environment failure without inventing an LLM retry."""
        error = result.error or StepError(ErrorKind.DEPENDENCY_MISSING)
        self._set_public_error(error, result.target)
        action = "检测到运行环境不可用，未执行自动参数修复"
        self._sm.add_action(action)
        recommendation = (
            "当前系统无法启动所需运行工具。自动调整模拟参数不能解决此问题；"
            "请安装或重新编译与当前系统兼容的运行工具后重新提交。"
        )
        escalation = {
            "layer": STEP_REGISTRY.layer_for(result.step_index),
            "step": result.step_name or label,
            "error_kind": error.kind.value,
            "attempts_made": 0,
            "actions_tried": [action],
            "recommendation": recommendation,
            "backup_plan": "保留当前输入和私有执行证据；修复运行环境后从失败步骤重新运行。",
        }
        self._record_agent_decision({
            "layer": STEP_REGISTRY.layer_for(result.step_index),
            "step": result.step_index,
            "error_kind": error.kind.value,
            "candidate_tools": [],
            "result": source,
            "success": False,
            "attempt": 0,
        })
        if self._run_registry is not None:
            append_structured_event(
                run_dir,
                "recovery_finalized",
                step=result.step_index,
                step_name=label,
                layer=STEP_REGISTRY.layer_for(result.step_index),
                source=source,
                outcome="blocked_environment",
                error_kind=error.kind.value,
                message_code="recovery.environment_blocked",
            )
        self._sm.set_escalated(escalation)
        print(f"[{label}] 🆘 运行环境不可用，未调用 LLM 自动修复")
        return False

    def _escalate_agent_exception(
        self,
        result: StepResult,
        label: str,
        run_dir: Path,
        agent: LayerAgent,
        exc: Exception,
    ) -> bool:
        """Finalize an Agent boundary failure instead of leaving ``retrying`` stale."""
        error = result.error or StepError(ErrorKind.UNKNOWN)
        self._set_public_error(error, result.target)
        action = "自动修复服务异常，未应用任何参数调整"
        self._sm.add_action(action)
        attempts = max(0, int(self._sm._status.retry_n))
        escalation = {
            "layer": agent.name,
            "step": result.step_name or label,
            "error_kind": error.kind.value,
            "attempts_made": attempts,
            "actions_tried": [action],
            "recommendation": (
                "自动修复服务未能完成诊断，当前模拟参数未被改写。"
                "请检查 LLM 连接或稍后重新提交此步骤。"
            ),
            "backup_plan": "保留当前输入和私有错误证据；恢复服务后从失败步骤重新运行。",
        }
        self._record_agent_decision({
            "layer": agent.name,
            "step": result.step_index,
            "error_kind": error.kind.value,
            "candidate_tools": [],
            "model_id": self.llm_model,
            "prompt_version": agent.prompt_version,
            "result": "agent_exception",
            "success": False,
            "attempt": attempts,
        })
        if self._run_registry is not None:
            append_structured_event(
                run_dir,
                "recovery_finalized",
                step=result.step_index,
                step_name=label,
                layer=STEP_REGISTRY.layer_for(result.step_index),
                source="agent_exception",
                outcome="escalated",
                error_kind=error.kind.value,
                message_code="recovery.agent_exception",
            )
        self._sm.set_escalated(escalation)
        print(f"[{label}] 🆘 自动修复服务异常（{type(exc).__name__}），已升级到用户")
        return False

    def _escalate_agent_unavailable(
        self,
        result: StepResult,
        label: str,
        run_dir: Path,
        layer_index: int,
    ) -> bool:
        """Persist a terminal result when no repair Agent is configured."""
        error = result.error or StepError(ErrorKind.UNKNOWN)
        self._set_public_error(error, result.target)
        action = "当前层没有可用自动修复 Agent，未应用任何参数调整"
        self._sm.add_action(action)
        layer = STEP_REGISTRY.layer_for(result.step_index) or f"layer_{layer_index}"
        escalation = {
            "layer": layer,
            "step": result.step_name or label,
            "error_kind": error.kind.value,
            "attempts_made": 0,
            "actions_tried": [action],
            "recommendation": "当前层没有可用自动修复服务，请人工处理失败步骤后重新运行。",
            "backup_plan": "保留当前输入和私有错误证据；修复后从失败步骤重新运行。",
        }
        self._record_agent_decision({
            "layer": layer,
            "step": result.step_index,
            "error_kind": error.kind.value,
            "candidate_tools": [],
            "result": "agent_unavailable",
            "success": False,
            "attempt": 0,
        })
        if self._run_registry is not None:
            append_structured_event(
                run_dir,
                "recovery_finalized",
                step=result.step_index,
                step_name=label,
                layer=layer,
                source="agent_unavailable",
                outcome="escalated",
                error_kind=error.kind.value,
                message_code="recovery.agent_unavailable",
            )
        self._sm.set_escalated(escalation)
        return False

    def _record_simulation_config_update(
        self,
        before: dict,
        after: dict,
        updated_fields: list[str],
    ) -> None:
        """Expose only a whitelist of applied MD configuration deltas to the UI."""
        field_specs = {
            "dt": ("时间步长", ("md", "dt"), " ps"),
            "ref_p": ("目标压力", ("md", "ref_p"), " bar"),
            "tcoupl": ("恒温器", ("md", "tcoupl"), ""),
            "tau_t": ("恒温耦合时间", ("md", "tau_t"), " ps"),
            "pcoupl": ("压强耦合器", ("md", "pcoupl"), ""),
            "constraints": ("键长约束", ("md", "constraints"), ""),
            "rcoulomb": ("静电截断半径", ("md", "rcoulomb"), " nm"),
            "rvdw": ("范德华截断半径", ("md", "rvdw"), " nm"),
            "nsteps": ("能量最小化步数", ("md", "nsteps"), ""),
            "emtol": ("最小化力阈值", ("md", "emtol"), ""),
            "emstep": ("最小化步长", ("md", "emstep"), ""),
            "lincs_iter": ("LINCS 迭代次数", ("md", "lincs_iter"), ""),
            "lincs_order": ("LINCS 阶数", ("md", "lincs_order"), ""),
            "eq_high_temperature": ("EQ 高温点", ("md", "eq", "high_temperature"), " K"),
            "eq_transition_temperature": ("EQ 过渡温度", ("md", "eq", "transition_temperature"), " K"),
            "eq_target_temperature": ("EQ 目标温度", ("md", "eq", "target_temperature"), " K"),
            "eq_tau_p": ("EQ 压强耦合时间", ("md", "eq", "tau_p"), " ps"),
            "prod_duration_ns": ("生产模拟时长", ("md", "prod", "duration_ns"), " ns"),
            "prod_temperature": ("生产模拟温度", ("md", "prod", "temperature"), " K"),
            "prod_tau_p": ("生产模拟压强耦合时间", ("md", "prod", "tau_p"), " ps"),
            "outputs.trr": ("完整精度轨迹输出", ("md", "outputs", "trr"), ""),
            "box.target_mass_density_g_cm3": ("初始质量密度", ("box", "target_mass_density_g_cm3"), " g/cm3"),
            "box.packing_number_density_nm3": ("建盒数密度", ("box", "packing_number_density_nm3"), " 分子/nm3"),
            "box.box_size": ("建盒边长", ("box", "box_size"), " A"),
            "box.tolerance": ("建盒间距", ("box", "tolerance"), " A"),
        }
        adjustments = []
        for field in updated_fields:
            spec = field_specs.get(field)
            if spec is None:
                continue
            name, path, unit = spec
            old_value = self._nested_value(before, path)
            new_value = self._nested_value(after, path)
            if old_value == new_value or old_value is None or new_value is None:
                continue
            adjustments.append({
                "name": name,
                "before": self._format_public_config_value(old_value, unit),
                "after": self._format_public_config_value(new_value, unit),
            })
        self._sm.add_adjustments(adjustments)

    @staticmethod
    def _nested_value(payload: dict, path: tuple[str, ...]):
        value = payload
        for key in path:
            if not isinstance(value, dict) or key not in value:
                return None
            value = value[key]
        return value

    @staticmethod
    def _format_public_config_value(value: object, unit: str) -> str:
        if isinstance(value, bool):
            return ("开启" if value else "关闭") + unit
        if isinstance(value, float):
            text = f"{value:g}"
        else:
            text = str(value)
        return f"{text}{unit}"

    # ============================================================
    # 结果处理
    # ============================================================

    def _handle_batch_result(self, results: list[StepResult], label: str,
                              run_dir: Path, artifacts: dict, layer_index: int,
                              step_i: int) -> bool:
        if self._abort_if_stop_requested(run_dir):
            return False
        if not results:
            message = f"{label} 未产生任何结果，检查本次运行目录中的输入文件"
            self._set_public_error(ErrorKind.FILE_NOT_FOUND)
            self._sm.transition(State.ABORTED)
            print(f"[{label}] ❌ {message}")
            return False

        ok = sum(1 for r in results if r.success)
        failed = [r for r in results if not r.success]

        for r in results:
            if r.success:
                for k, v in r.outputs.items():
                    artifacts.setdefault(k, []).append(v)

        if not failed:
            print(f"[{label}] ✅ {ok}/{len(results)} 全部成功")
            return True

        # 记录错误到状态机
        first_err = failed[0].error
        if first_err:
            self._set_public_error(first_err, failed[0].target)
        else:
            self._set_public_error(ErrorKind.UNKNOWN, failed[0].target)

        print(f"[{label}] ❌ {ok}/{len(results)} 成功, {len(failed)} 失败")
        for r in failed:
            err = r.error
            if err:
                print(f"  [{err.kind.value}] {err.message[:100]}")
                if err.hint: print(f"    → {err.hint}")

        blocked_environment = next(
            (item for item in failed if self._is_nonretryable_environment_error(item)),
            None,
        )
        if blocked_environment is not None:
            return self._escalate_nonretryable_environment_failure(
                blocked_environment, label, run_dir, source="environment_execution",
            )

        if not self.use_llm or layer_index is None:
            self._sm.transition(State.ABORTED)
            return False

        for failed_result in failed:
            if not self._invoke_agent(failed_result, label, run_dir, artifacts, layer_index):
                return False
        return True

    def _handle_single_result(self, result: StepResult, label: str,
                               run_dir: Path, artifacts: dict, layer_index: int,
                               step_i: int) -> bool:
        if self._abort_if_stop_requested(run_dir):
            return False
        if result.success:
            print(f"[{label}] ✅ 完成 ({result.duration_s:.1f}s)")
            for k, v in result.outputs.items():
                artifacts.setdefault(k, []).append(v)
            if step_i == PACKMOL_STEP:
                try:
                    from willy.simulation.manifest import box_parameters_changed, record_box_attempt
                    record_box_attempt(run_dir, result.extra.get("box_parameters", {}))
                    if not box_parameters_changed(run_dir):
                        self._set_public_error(ErrorKind.INPUT_CONTRACT)
                        self._sm.transition(State.ABORTED)
                        print("[Packmol 盒子] ❌ 回滚后盒子体积或建盒参数未发生实际变化")
                        return False
                except (OSError, ValueError) as exc:
                    self._set_public_error(ErrorKind.INPUT_CONTRACT)
                    self._sm.transition(State.ABORTED)
                    print(f"[Packmol 盒子] ❌ 无法记录建盒参数: {exc}")
                    return False
            return True

        err = result.error
        if err:
            self._set_public_error(err, result.target)
            print(f"[{label}] ❌ [{err.kind.value}] {err.message[:100]}")
            if err.hint: print(f"  → {err.hint}")
        else:
            self._set_public_error(ErrorKind.UNKNOWN, result.target)
            print(f"[{label}] ❌ 失败（无详细错误信息）")

        if self._is_nonretryable_environment_error(result):
            return self._escalate_nonretryable_environment_failure(
                result, label, run_dir, source="environment_execution",
            )

        # EQ protocol or acceptance failures are never auto-repaired.  The
        # model may diagnose and propose a bounded change, but the run remains
        # parked until the user approves that exact proposal.
        if step_i == EQ_STEP and layer_index == 3:
            return self._await_eq_user_confirmation(result, label, run_dir)

        rollback_step = result.extra.get("rollback_to_step")
        if rollback_step is not None and self._schedule_simulation_rollback(
            result, run_dir, int(rollback_step), step_i,
        ):
            return True

        if not self.use_llm or layer_index is None:
            self._sm.transition(State.ABORTED)
            return False

        return self._invoke_agent(result, label, run_dir, artifacts, layer_index)

    def _await_eq_user_confirmation(
        self,
        result: StepResult,
        label: str,
        run_dir: Path,
    ) -> bool:
        """Persist a proposal for an EQ failure and intentionally stop the runner."""
        agent = self._agents.get(3)
        if agent is None or not hasattr(agent, "propose_eq_recovery"):
            self._sm.transition(State.ABORTED)
            print(f"[{label}] ❌ 无可用 Simulation Agent，无法生成待确认方案")
            return False
        print(f"\n[{label}] 🤖 正在生成 EQ 修复方案，等待用户确认...")
        proposal = agent.propose_eq_recovery(
            result,
            str(self._run_config_path or (run_dir / "config.json")),
        )
        try:
            from willy.simulation.pending_action import (
                PendingActionError,
                create_eq_pending_action,
                public_pending_action,
            )
            requires_box_rebuild = result.extra.get("rollback_to_step") == PACKMOL_STEP
            action = create_eq_pending_action(
                run_dir,
                proposal=proposal,
                requires_box_rebuild=requires_box_rebuild,
            )
            self._sm.set_awaiting_confirmation(public_pending_action(action))
            if self._run_registry is not None:
                try:
                    from willy.simulation.pending_action import pending_action_validated
                    validated = pending_action_validated(action)
                    self._record_agent_decision({
                        "decision_id": validated.decision_id,
                        "action_id": validated.decision_id,
                        "layer": validated.proposal.layer,
                        "step": validated.proposal.failed_step,
                        "error_kind": result.error.kind.value if result.error else "unknown",
                        "policy_id": validated.policy_id,
                        "selected_tool": validated.proposal.tool_name,
                        "tool_effect": validated.declaration.effect.value,
                        "parameter_changes": [change.field_name for change in validated.proposal.parameter_changes],
                        "restart_step": action.get("restart_step"),
                        "requires_confirmation": validated.requires_confirmation,
                        "result": "awaiting_confirmation",
                    })
                except (OSError, ValueError):
                    pass
        except (OSError, ValueError, PendingActionError) as exc:
            self._sm.transition(State.ABORTED)
            print(f"[{label}] ❌ 无法生成待确认方案: {exc}")
            return False
        print(f"[{label}] ⏸ 已生成待确认 EQ 方案，等待用户回复")
        return False

    def _schedule_simulation_rollback(
        self,
        result: StepResult,
        run_dir: Path,
        target_step: int,
        failed_step: int,
    ) -> bool:
        """Return an EM/EQ failure to Packmol with bounded, audited cleanup."""
        if target_step != PACKMOL_STEP:
            return False
        attempts = self._rollback_attempts.get(failed_step, 0)
        max_attempts = self._max_box_rollbacks()
        if attempts >= max_attempts:
            self._sm.add_action(
                f"Step {failed_step} 已达到 {max_attempts} 次 Packmol 回滚上限，转入 Agent 处理"
            )
            return False

        self._rollback_attempts[failed_step] = attempts + 1
        reason = result.extra.get("rollback_reason", f"Step {failed_step} 需要重新建盒")
        multiplier = result.extra.get("box_density_multiplier")
        if multiplier is not None:
            adjustment = self._adjust_box_density(float(multiplier))
            if adjustment:
                reason = f"{reason}；{adjustment}"

        self._remove_gromacs_outputs(run_dir)
        self._sm.rollback_to(PACKMOL_STEP, STEP_REGISTRY.label_for(PACKMOL_STEP), "simulation", reason)
        self._rollback_to_step = PACKMOL_STEP
        print(f"[orchestrator] ↩ {reason}")
        return True

    def _max_box_rollbacks(self) -> int:
        try:
            config_path = self._run_config_path
            if config_path is None:
                return 1
            value = json.loads(config_path.read_text()).get("md", {}).get("max_box_rollbacks", 1)
            return max(0, int(value))
        except (OSError, ValueError, json.JSONDecodeError):
            return 1

    def _adjust_box_density(self, multiplier: float) -> str:
        """Persist a bounded simulation recovery adjustment in one run snapshot."""
        config_path = self._run_config_path
        if config_path is None:
            return ""
        try:
            config = json.loads(config_path.read_text())
            box = config.setdefault("box", {})
            uses_mass_density = "target_mass_density_g_cm3" in box
            field = "target_mass_density_g_cm3" if uses_mass_density else "packing_number_density_nm3"
            density = float(box.get(field, 1.0 if uses_mass_density else 6.0))
            new_density = round(density * multiplier, 6)
            box[field] = new_density
            if box.get("box_size") is not None:
                old_size = float(box["box_size"])
                box["box_size"] = round(old_size * (density / new_density) ** (1 / 3), 6)
            write_json(config_path, config)
            label = "初始质量密度" if uses_mass_density else "填充数密度"
            return f"已将{label}调整为原来的 {multiplier:.2f} 倍"
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            self._sm.add_action(f"无法持久化模拟建盒密度修复: {exc}")
            return ""

    @staticmethod
    def _remove_gromacs_outputs(run_dir: Path) -> None:
        """Remove only known, regenerable GROMACS artifacts inside one run."""
        names = {
            f"{stage}.{suffix}"
            for stage in ("em", "eq", "prod")
            for suffix in ("tpr", "gro", "xtc", "edr", "log", "cpt", "trr")
        }
        names.update({"density.xvg", "temp.xvg"})
        for name in names:
            path = run_dir / name
            if path.is_file():
                path.unlink()
        from willy.simulation.visualization import clear_stage_visualization_artifact
        for stage in ("em", "eq", "prod"):
            clear_stage_visualization_artifact(run_dir, stage)

    @staticmethod
    def _schedule_stage_visualization(run_dir: Path, step_index: int) -> None:
        """Create a display-only PDB after an MD stage is fully accepted."""
        stage = STEP_REGISTRY.stage_for(step_index)
        if stage not in {"em", "eq", "prod"}:
            return
        try:
            from willy.simulation.visualization import schedule_accepted_stage_visualization
            schedule_accepted_stage_visualization(run_dir, stage)
        except (OSError, ValueError):
            # The viewer is an optional consumer, never a pipeline dependency.
            return

    @staticmethod
    def _prod_visualization_contract_failure(
        step_index: int,
        run_dir: Path,
    ) -> StepResult | None:
        """Synchronously publish PROD PDB before granting pipeline completion.

        EM/EQ viewer files remain best-effort background artifacts.  PROD is
        different because it is the final stage: returning from the runner
        would terminate daemon conversion workers and could leave only a
        temporary PDB.  A successful conversion is therefore a finalization
        contract, while its failure aborts the run without marking PROD done.
        """
        if STEP_REGISTRY.stage_for(step_index) != "prod":
            return None
        try:
            from willy.simulation.visualization import convert_stage_gro_to_pdb
            result = convert_stage_gro_to_pdb(run_dir, "prod")
        except (OSError, ValueError) as exc:
            reason = str(exc) or "转换器调用失败"
            return StepResult(
                step_name="prod",
                step_index=step_index,
                success=False,
                error=StepError(
                    ErrorKind.INPUT_CONTRACT,
                    f"PROD 可视化产物验收失败: {reason}",
                ),
                target_type="stage",
                target="prod",
            )
        if result.success:
            return None
        return StepResult(
            step_name="prod",
            step_index=step_index,
            success=False,
            error=StepError(
                ErrorKind.INPUT_CONTRACT,
                f"PROD 可视化产物验收失败: {result.reason or '未知原因'}",
            ),
            target_type="stage",
            target="prod",
        )

    @staticmethod
    def _prod_intermediate_cleanup_contract_failure(
        step_index: int,
        run_dir: Path,
    ) -> StepResult | None:
        """Remove only named, regenerateable artifacts before accepting PROD.

        The cleanup is intentionally limited to direct children of the current
        run directory.  Names come from its frozen configuration so stage
        outputs (``em/eq/prod.gro``), ``.fchk`` files, topology, trajectories,
        and MD logs cannot be selected by a wildcard.
        """
        if STEP_REGISTRY.stage_for(step_index) != "prod":
            return None
        try:
            config = json.loads((run_dir / "config.json").read_text(encoding="utf-8"))
            molecules = config.get("molecules", {})
            if not isinstance(molecules, dict):
                raise ValueError("运行配置中的 molecules 无效")
            molecule_names = list(molecules)
            if any(
                not isinstance(name, str)
                or not name
                or name in {".", ".."}
                or Path(name).name != name
                or "\\" in name
                for name in molecule_names
            ):
                raise ValueError("运行配置包含无效分子名")

            paths = [
                run_dir / f"{name}{suffix}"
                for name in molecule_names
                for suffix in (".chg", ".chk", ".gro", ".log", "_opt.chk", "_opt.log")
            ]
            paths.extend(run_dir.glob("*_out.mdp"))
            for path in paths:
                if path.is_file():
                    path.unlink()
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return StepResult(
                step_name="prod",
                step_index=step_index,
                success=False,
                error=StepError(
                    ErrorKind.INPUT_CONTRACT,
                    f"PROD 收尾清理失败: {str(exc) or '未知原因'}",
                ),
                target_type="stage",
                target="prod",
            )
        return None

    def _invoke_agent(self, step_result: StepResult, label: str,
                       run_dir: Path, artifacts: dict, layer_index: int) -> bool:
        if self._abort_if_stop_requested(run_dir):
            return False
        agent = self._agents.get(layer_index)
        if agent is None:
            print(f"[{label}] ⚠ 无可用 Agent（层 {layer_index}），跳过自动修复")
            return self._escalate_agent_unavailable(step_result, label, run_dir, layer_index)

        print(f"\n[{label}] 🤖 调用 {agent.name} Agent 进行自动修复...")

        # A batch may contain several failed molecules.  The public retry
        # state must follow the item being repaired rather than retaining the
        # first failure selected by _handle_batch_result().
        self._set_public_error(step_result.error or ErrorKind.UNKNOWN, step_result.target)

        flat_artifacts: dict[str, str] = {}
        for k, v in artifacts.items():
            if isinstance(v, list) and v:
                flat_artifacts[k] = v[-1]
            elif isinstance(v, str):
                flat_artifacts[k] = v

        # 失败步骤也可能已生成可用于修复的中间产物（例如 Step 2 的
        # *_opt.fchk）。它们必须覆盖同名的历史产物，供 Agent 精确重试。
        for k, v in step_result.outputs.items():
            if v:
                flat_artifacts[k] = str(v)

        try:
            repair_result = agent.handle_failure(
                step_result=step_result,
                config_path=str(self._run_config_path or (run_dir / "config.json")),
                run_dir=str(run_dir),
                artifacts=flat_artifacts,
                state_machine=self._sm,
            )
        except Exception as exc:
            return self._escalate_agent_exception(
                step_result, label, run_dir, agent, exc,
            )

        if self._abort_if_stop_requested(run_dir):
            return False

        if repair_result.success:
            restart_step, identity_failure = self._repair_restart_step(
                step_result, repair_result, run_dir,
            )
            if identity_failure is not None:
                self._record_step_result(identity_failure, label, source=f"agent:{agent.name}")
                self._set_public_error(identity_failure.error, identity_failure.target)
                self._sm.transition(State.ABORTED)
                print(f"[{label}] ❌ Agent 修复结果不具备当前步骤完成资格")
                return False

            if restart_step is not None:
                self._record_step_result(repair_result, label, source=f"agent:{agent.name}")
                for k, v in repair_result.outputs.items():
                    artifacts.setdefault(k, []).append(v)
                return self._schedule_repair_restart(
                    step_result, repair_result, restart_step, label,
                )

            contract_failure = self._repair_contract_failure(step_result, repair_result, run_dir)
            if contract_failure is not None:
                self._record_step_result(contract_failure, label, source=f"agent:{agent.name}")
                rerun_key = (step_result.step_index, step_result.target)
                attempts = self._repair_rerun_attempts.get(rerun_key, 0)
                if attempts < 1:
                    self._repair_rerun_attempts[rerun_key] = attempts + 1
                    self._rerun_step = step_result.step_index
                    self._sm.add_action(
                        f"{step_result.target} 的修复仅补足上游产物，重跑当前步骤"
                    )
                    self._sm.transition(State.RUNNING, step=step_result.step_index)
                    print(f"[{label}] ↩ Agent 修复未满足步骤产物契约，重跑当前步骤")
                    return True

                self._set_public_error(contract_failure.error, contract_failure.target)
                self._sm.transition(State.ABORTED)
                print(f"[{label}] ❌ Agent 修复未满足步骤产物契约")
                return False

            self._record_step_result(repair_result, label, source=f"agent:{agent.name}")
            print(f"[{label}] ✅ Agent 修复成功")
            self._sm.transition(State.RUNNING, step=step_result.step_index)
            for k, v in repair_result.outputs.items():
                artifacts.setdefault(k, []).append(v)
            return True

        self._record_step_result(repair_result, label, source=f"agent:{agent.name}")

        if repair_result.escalated:
            # The original step error remains the public physical failure.
            # Agent escalation text describes remediation, not a new engine
            # outcome, and must not overwrite error type or target.
            final_error = step_result.error or repair_result.error or ErrorKind.UNKNOWN
            final_target = step_result.target or repair_result.target
            self._set_public_error(final_error, final_target)
            escalation = repair_result.extra.get("escalation", {})
            self._sm.set_escalated(escalation)
            print(f"\n[{label}] 🆘 自动修复失败，已升级到用户")
            if escalation:
                print(f"  层: {escalation.get('layer')}")
                print(f"  错误类型: {escalation.get('error_kind')}")
                print(f"  已尝试: {escalation.get('attempts_made')} 次")
                for act in escalation.get("actions_tried", []):
                    print(f"    - {act}")
        else:
            final_error = repair_result.error or step_result.error or ErrorKind.UNKNOWN
            final_target = repair_result.target or step_result.target
            self._set_public_error(final_error, final_target)
            self._sm.transition(State.ABORTED)
            print(f"[{label}] ❌ Agent 修复失败")

        return False

    def _repair_restart_step(
        self,
        failed_step: StepResult,
        repair_result: StepResult,
        run_dir: Path,
    ) -> tuple[int | None, StepResult | None]:
        """Classify a successful repair without granting downstream completion.

        A repair tool may execute the failed step itself, or it may complete a
        prerequisite such as MDP regeneration, Packmol, or EM.  In the latter
        case the original failed step must be re-executed (or the whole
        invalidated simulation suffix must be replayed).  A tool may never use
        a later step to satisfy an earlier failure.
        """
        if repair_result.step_index == failed_step.step_index:
            if (
                failed_step.target
                and repair_result.target
                and failed_step.target != repair_result.target
            ):
                return None, self._repair_identity_failure(
                    failed_step, repair_result,
                    "修复对象与失败对象不一致",
                )
            return None, None

        if repair_result.step_index > failed_step.step_index:
            return None, self._repair_identity_failure(
                failed_step, repair_result,
                "下游步骤不能替代上游失败步骤",
            )

        source_stage = STEP_REGISTRY.stage_for(repair_result.step_index)
        if source_stage is not None:
            source_failure = self._completion_contract_failure(repair_result.step_index, run_dir)
            if source_failure is not None:
                return None, source_failure

        restart_step = failed_step.step_index
        invalidated_stages = repair_result.extra.get("invalidated_stages", [])
        if failed_step.step_index >= STEP_REGISTRY.by_id("simulation_mdp").index and isinstance(invalidated_stages, list):
            invalidated_steps = [
                STEP_REGISTRY.index_for_stage(stage)
                for stage in invalidated_stages
                if STEP_REGISTRY.index_for_stage(stage) is not None
            ]
            if invalidated_steps:
                restart_step = min(invalidated_steps)
        elif failed_step.step_index >= STEP_REGISTRY.by_id("simulation_mdp").index and repair_result.step_index == PACKMOL_STEP:
            # A rebuilt Packmol box changes the EM input even if the tool did
            # not expose its invalidation list.
            restart_step = EM_STEP

        return restart_step, None

    def _schedule_repair_restart(
        self,
        failed_step: StepResult,
        repair_result: StepResult,
        restart_step: int,
        label: str,
    ) -> bool:
        """Persist an explicit rerun/rollback directive for an upstream repair."""
        rerun_key = (failed_step.step_index, failed_step.target)
        attempts = self._repair_rerun_attempts.get(rerun_key, 0)
        if attempts >= 1:
            failure = self._repair_identity_failure(
                failed_step,
                repair_result,
                "修复后仍未完成目标步骤",
            )
            self._set_public_error(failure.error, failure.target)
            self._sm.transition(State.ABORTED)
            print(f"[{label}] ❌ Agent 修复后仍未完成目标步骤")
            return False

        self._repair_rerun_attempts[rerun_key] = attempts + 1
        source = STEP_REGISTRY.label_for(repair_result.step_index)
        if restart_step < failed_step.step_index:
            target_label = STEP_REGISTRY.label_for(restart_step)
            self._sm.rollback_to(
                restart_step,
                target_label,
                STEP_REGISTRY.layer_for(restart_step),
                f"{source} 修复影响上游阶段，需重新验收后续工序",
            )
            self._rollback_to_step = restart_step
            print(f"[{label}] ↩ Agent 已修复 {source}，从 Step {restart_step} 重新验收")
            return True

        self._rerun_step = failed_step.step_index
        self._sm.add_action(f"{source} 修复完成，重跑当前步骤进行验收")
        self._sm.transition(State.RUNNING, step=failed_step.step_index)
        print(f"[{label}] ↩ Agent 修复完成，重跑当前步骤")
        return True

    @staticmethod
    def _repair_identity_failure(
        failed_step: StepResult,
        repair_result: StepResult,
        reason: str,
    ) -> StepResult:
        return StepResult(
            step_name=failed_step.step_name,
            step_index=failed_step.step_index,
            success=False,
            error=StepError(
                ErrorKind.INPUT_CONTRACT,
                f"{failed_step.step_name}: {reason}（修复工具实际执行 Step {repair_result.step_index}）",
            ),
            target_type=failed_step.target_type,
            target=failed_step.target,
        )

    @staticmethod
    def _repair_contract_failure(
        failed_step: StepResult,
        repair_result: StepResult,
        run_dir: Path,
    ) -> StepResult | None:
        """Reject an Agent success that did not produce the failed step's outputs.

        A Step 2 repair may legitimately create its missing Step 1 ``.fchk``.
        That is useful upstream progress, but it is not a successful SP/mol2
        repair until the Step 2 artifacts themselves exist in this run.
        """
        if failed_step.step_index != 2 or not failed_step.target:
            return None
        target = failed_step.target
        required = [run_dir / f"{target}_opt.fchk", run_dir / f"{target}.mol2"]
        missing = [path.name for path in required if not path.is_file()]
        if not missing:
            return None
        return StepResult(
            step_name=failed_step.step_name,
            step_index=failed_step.step_index,
            success=False,
            error=StepError(
                ErrorKind.INPUT_CONTRACT,
                f"{target}: Agent 修复未生成 Step 2 必需产物: {', '.join(missing)}",
            ),
            target_type=failed_step.target_type,
            target=target,
        )

    @staticmethod
    def _completion_contract_failure(step_index: int, run_dir: Path) -> StepResult | None:
        """Return a failure unless a completed MD step has private evidence.

        ``StepResult.success`` is an execution claim.  For EM/EQ/PROD the
        private MD manifest and non-empty stage outputs are the authority that
        grants a pipeline completion permission.
        """
        stage = STEP_REGISTRY.stage_for(step_index)
        if stage is None:
            return None

        required_status = "completed" if stage == "prod" else "accepted"
        required_outputs = ["tpr", "gro", "xtc", "edr"]
        if stage in {"eq", "prod"}:
            required_outputs.append("cpt")
        try:
            from willy.simulation.manifest import ManifestError, load_manifest
            manifest = load_manifest(run_dir)
            record = manifest.get("stages", {}).get(stage, {})
            if record.get("status") != required_status:
                raise ManifestError(
                    f"{stage.upper()} 尚未写入 {required_status} 阶段许可"
                )
            missing = [
                name for name in required_outputs
                if not (run_dir / f"{stage}.{name}").is_file()
                or (run_dir / f"{stage}.{name}").stat().st_size <= 0
            ]
            if missing:
                raise ManifestError(
                    f"{stage.upper()} 缺少非空阶段产物: {', '.join(missing)}"
                )
        except (ManifestError, OSError, ValueError) as exc:
            return StepResult(
                step_name=stage,
                step_index=step_index,
                success=False,
                error=StepError(ErrorKind.INPUT_CONTRACT, str(exc)),
                target_type="stage",
                target=stage,
            )
        return None


def _g16_sp_and_mol2(config_path: str, on_progress=None,
                     work_dir: str | Path | None = None) -> list:
    """Step 2 (G16): *_opt.fchk → *_opt.fchk + .mol2."""
    import json
    from willy.execution_resources import resolve_nproc
    from willy.quantum.singlepoint_g16 import run as sp_run
    from willy.quantum.fchk_mol2 import convert as mol2_convert

    with open(config_path) as f:
        config = json.load(f)
    molecules = config.get("molecules", {})
    defaults = config.get("defaults", {})

    workspace = Path(work_dir) if work_dir is not None else ROOT / "struct"
    results = []
    total = len(molecules)
    for i, (name, cfg) in enumerate(molecules.items(), 1):
        if on_progress:
            on_progress({
                "tool": "G16", "operation": "单点计算与 mol2 转换",
                "target_type": "molecule", "target": name,
                "current": i, "total": total,
            })
        fchk_path = str(workspace / f"{name}.fchk")
        if not Path(fchk_path).exists():
            results.append(StepResult(
                step_name="sp_mol2_g16", step_index=2, success=False,
                error=StepError(kind=ErrorKind.FILE_NOT_FOUND,
                                message=f"{name}.fchk 不存在，需先运行 Step 1 结构优化"),
                target_type="molecule", target=name))
            continue
        charge = cfg.get("charge", 0)
        spin = cfg.get("spin", 1)
        mem = cfg.get("mem") or defaults.get("mem", "5GB")
        nproc = resolve_nproc(cfg.get("nproc") or defaults.get("nproc"))
        sr = sp_run(
            fchk_path, charge=charge, spin=spin, workdir=str(workspace),
            mem=mem, nproc=nproc,
        )
        if sr.success:
            opt_fchk = sr.outputs["fchk"]
            sr2 = mol2_convert(opt_fchk)
            if sr2.success:
                sr.outputs["mol2"] = sr2.outputs["mol2"]
                sr.artifacts.extend(sr2.artifacts)
            else:
                sr.success = False
                sr.error = sr2.error
        sr.target_type = "molecule"
        sr.target = name
        results.append(sr)
    return results


def _g09_sp_and_mol2(config_path: str, on_progress=None,
                     work_dir: str | Path | None = None) -> list:
    """Step 2 (G09): *_opt.fchk → *_opt.fchk + .mol2."""
    import json
    from willy.execution_resources import resolve_nproc
    from willy.quantum.singlepoint_g09 import run as sp_run
    from willy.quantum.fchk_mol2 import convert as mol2_convert

    with open(config_path) as f:
        config = json.load(f)
    molecules = config.get("molecules", {})
    defaults = config.get("defaults", {})

    workspace = Path(work_dir) if work_dir is not None else ROOT / "struct"
    results = []
    total = len(molecules)
    for i, (name, cfg) in enumerate(molecules.items(), 1):
        if on_progress:
            on_progress({
                "tool": "G09", "operation": "单点计算与 mol2 转换",
                "target_type": "molecule", "target": name,
                "current": i, "total": total,
            })
        fchk_path = str(workspace / f"{name}.fchk")
        if not Path(fchk_path).exists():
            results.append(StepResult(
                step_name="sp_mol2_g09", step_index=2, success=False,
                error=StepError(kind=ErrorKind.FILE_NOT_FOUND,
                                message=f"{name}.fchk 不存在，需先运行 Step 1 结构优化"),
                target_type="molecule", target=name))
            continue
        charge = cfg.get("charge", 0)
        spin = cfg.get("spin", 1)
        mem = cfg.get("mem") or defaults.get("mem", "5GB")
        nproc = resolve_nproc(cfg.get("nproc") or defaults.get("nproc"))
        sr = sp_run(
            fchk_path, charge=charge, spin=spin, workdir=str(workspace),
            mem=mem, nproc=nproc,
        )
        if sr.success:
            opt_fchk = sr.outputs["fchk"]
            sr2 = mol2_convert(opt_fchk)
            if sr2.success:
                sr.outputs["mol2"] = sr2.outputs["mol2"]
                sr.artifacts.extend(sr2.artifacts)
            else:
                sr.success = False
                sr.error = sr2.error
        sr.target_type = "molecule"
        sr.target = name
        results.append(sr)
    return results


def _orca_sp_and_mol2(config_path: str, on_progress=None,
                      work_dir: str | Path | None = None) -> list:
    """Step 2 (ORCA): *_opt.molden → .mol2; retain *_opt.fchk for RESP."""
    import json
    from willy.execution_resources import memory_to_mb, resolve_nproc
    from willy.quantum.singlepoint_orca import run as sp_run
    from willy.quantum.molden_mol2 import convert as molden_to_mol2

    with open(config_path) as f:
        config = json.load(f)
    molecules = config.get("molecules", {})
    defaults = config.get("defaults", {})

    workspace = Path(work_dir) if work_dir is not None else ROOT / "struct"
    results = []
    total = len(molecules)
    for i, (name, cfg) in enumerate(molecules.items(), 1):
        if on_progress:
            on_progress({
                "tool": "ORCA", "operation": "单点计算与 mol2 转换",
                "target_type": "molecule", "target": name,
                "current": i, "total": total,
            })
        molden_path = str(workspace / f"{name}.molden")
        if not Path(molden_path).exists():
            results.append(StepResult(
                step_name="sp_orca", step_index=2, success=False,
                error=StepError(kind=ErrorKind.FILE_NOT_FOUND,
                                message=f"{name}.molden 不存在，需先运行 Step 1 结构优化"),
                target_type="molecule", target=name))
            continue
        charge = cfg.get("charge", 0)
        spin = cfg.get("spin", 1)
        nproc = resolve_nproc(cfg.get("nproc") or defaults.get("nproc"))
        mem_mb = memory_to_mb(cfg.get("mem") or defaults.get("mem", "5GB"))
        sr = sp_run(
            molden_path, charge=charge, spin=spin, workdir=str(workspace),
            nproc=nproc, mem_mb=mem_mb,
        )
        if sr.success:
            opt_molden = sr.outputs.get("molden") or str(workspace / f"{name}_opt.molden")
            sr2 = molden_to_mol2(opt_molden)
            if sr2.success:
                sr.outputs["mol2"] = sr2.outputs["mol2"]
                sr.artifacts.extend(sr2.artifacts)
            else:
                sr.success = False
                sr.error = sr2.error
        sr.target_type = "molecule"
        sr.target = name
        results.append(sr)
    return results



def run_pipeline(backend: str = "g16", use_llm: bool = True,
                 run_dir: Optional[Path] = None) -> bool:
    orch = PipelineOrchestrator(backend=backend, use_llm=use_llm)
    return orch.run(run_dir=run_dir)
