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
import os, sys, json
from pathlib import Path
from typing import Optional

from willy._paths import get_project_root
from willy.env_checker import ensure
from willy.errors import StepResult, StepError, ErrorKind
from willy.layer_agent import LayerAgent
from willy.pipeline_state import PipelineStateMachine, State

ROOT = get_project_root()

# 步骤 index → 层名映射
_STEP_LAYER = {1: "quantum", 2: "quantum", 3: "quantum",
               4: "topology", 5: "topology", 6: "simulation", 7: "simulation"}


class PipelineOrchestrator:
    """流水线编排器 —— 执行 7 步流水线，失败时调用 LLM Agent。支持断点续跑。"""

    def __init__(self, backend: str = "g16", use_llm: bool = True,
                 resume_from: int = -1, resume_run_dir: str = ""):
        self.backend = backend
        self.use_llm = use_llm
        self.llm_client = None
        self._agents: dict[int, Optional[LayerAgent]] = {
            1: None, 2: None, 3: None,
        }
        self._skipped_molecules: set = set()

        # ── 断点续跑：读取上次进度 ──
        if resume_from >= 0:
            self._resume_from = min(resume_from, 1)
            self._resume_run_dir = resume_run_dir
        else:
            prev = PipelineStateMachine.read()
            self._resume_from = max(prev.done_steps) if prev.done_steps else 0
            self._resume_run_dir = prev.extra.get("run_dir") if prev.extra else None
            self._resume_from = min(self._resume_from, 1)
        if self._resume_from > 0:
            print(f"[orchestrator] 🔄 断点续跑：Step 1 结构优化已完成，从 Step 2 开始")
            if self._resume_run_dir:
                print(f"[orchestrator]    复用运行目录: {self._resume_run_dir}")

        self._sm = PipelineStateMachine(total_steps=7)
        for s in range(1, self._resume_from + 1):
            self._sm.mark_done(s)

        if use_llm:
            self._init_llm()
            self._init_agents()

    def _init_llm(self):
        api_key = os.environ.get("DEEPSEEK_API_KEY", "")
        if not api_key:
            env_file = ROOT / ".env"
            if env_file.exists():
                for line in env_file.read_text().split("\n"):
                    if line.startswith("DEEPSEEK_API_KEY="):
                        api_key = line.split("=", 1)[1].strip().strip('"').strip("'")
        if not api_key:
            print("[orchestrator] ⚠ DEEPSEEK_API_KEY 未设置，禁用 LLM Agent")
            self.use_llm = False
            return
        from openai import OpenAI
        self.llm_client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com")

    def _init_agents(self):
        if not self.llm_client:
            return
        from willy.agent_quantum import QuantumAgent
        from willy.agent_topology import TopologyAgent
        from willy.agent_simulation import SimulationAgent

        self._agents[1] = QuantumAgent(
            llm_client=self.llm_client, max_retries=5,
            on_action=lambda a: self._sm.add_action(a),
        )
        self._agents[2] = TopologyAgent(
            llm_client=self.llm_client, max_retries=4,
            on_action=lambda a: self._sm.add_action(a),
        )
        self._agents[3] = SimulationAgent(
            llm_client=self.llm_client, max_retries=3,
            on_action=lambda a: self._sm.add_action(a),
        )

    # ============================================================
    # 步骤构建
    # ============================================================

    def _build_steps(self, run_dir: Path) -> list[tuple]:
        from willy.quantum.struct_g16 import run_all as g16_struct
        from willy.quantum.struct_orca import run_all as orca_struct
        from willy.quantum.fchk_mol2 import batch_convert as fchk_mol2
        from willy.quantum.chg_resp import batch_make_chg as chg_resp
        from willy.topology.topo_gaff import batch_make_topo
        from willy.topology.top_assembly import build as build_top
        from willy.simulation.mdp import build_all as build_mdp
        from willy.simulation.box import auto_from_config, InpGenerator

        def _run_box(rd: str) -> StepResult:
            config = auto_from_config(output_dir=rd, gro_dir=rd, pdb_dir=rd)
            gen = InpGenerator(config)
            return gen.run()

        on_prog = lambda msg: self._sm.set_detail(msg)
        cfg = str(ROOT / "config.json")

        if self.backend == "orca":
            return [
                ("ORCA 结构优化",    lambda: orca_struct(on_progress=on_prog),
                 "struct_orca", True, 1),
                ("SP + molden→mol2", lambda: _orca_sp_and_mol2(cfg, on_prog),
                 "sp_orca", True, 1),
                ("RESP 电荷",        lambda: chg_resp(on_progress=on_prog),
                 "chg_resp", True, 1),
                ("mol2+chg→itp+gro", lambda: batch_make_topo(output_dir=str(run_dir)),
                 "topo_gaff", True, 2),
                ("主拓扑 + 修订 itp", lambda: build_top(topo_dir=str(run_dir)),
                 None, False, 2),
                ("生成 mdp",          lambda: build_mdp(output_dir=str(run_dir)),
                 None, False, 3),
                ("Packmol 盒子",      lambda: _run_box(str(run_dir)),
                 "box", False, 3),
            ]
        return [
            ("g16 优化 + formchk", lambda: g16_struct(on_progress=on_prog),
             "struct_g16", True, 1),
            ("SP + mol2",           lambda: _g16_sp_and_mol2(cfg, on_prog),
             "sp_g16", True, 1),
            ("RESP 电荷",           lambda: chg_resp(on_progress=on_prog),
             "chg_resp", True, 1),
            ("mol2+chg→itp+gro",    lambda: batch_make_topo(output_dir=str(run_dir)),
             "topo_gaff", True, 2),
            ("主拓扑 + 修订 itp",    lambda: build_top(topo_dir=str(run_dir)),
             None, False, 2),
            ("生成 mdp",             lambda: build_mdp(output_dir=str(run_dir)),
             None, False, 3),
            ("Packmol 盒子",         lambda: _run_box(str(run_dir)),
             "box", False, 3),
        ]

    # ============================================================
    # 执行
    # ============================================================

    def run(self, run_dir: Optional[Path] = None) -> bool:
        # ── 断点续跑：复用上次运行目录 ──
        if run_dir is None:
            if self._resume_run_dir:
                run_dir = Path(self._resume_run_dir)
                if not run_dir.exists():
                    print(f"[orchestrator] ⚠ 续跑目录 {run_dir} 不存在，创建新目录")
                    from willy.simulation.setup import _next_run_dir
                    run_dir = _next_run_dir()
                    run_dir.mkdir(parents=True, exist_ok=True)
                    self._resume_from = 0  # 目录没了，不能续跑
            else:
                from willy.simulation.setup import _next_run_dir
                run_dir = _next_run_dir()
                run_dir.mkdir(parents=True, exist_ok=True)

        # ── 记录 run_dir 到状态机 extra ──
        self._sm._status.extra = {"run_dir": str(run_dir)}
        self._sm._write()

        print(f"[orchestrator] 运行目录: {run_dir}")
        print(f"[orchestrator] 后端: {self.backend}")
        print(f"[orchestrator] LLM Agent: {'启用' if self.use_llm else '禁用'}")
        if self._resume_from > 0:
            print(f"[orchestrator] 断点续跑: 仅跳过 Step 1 结构优化，Step 2+ 全部重跑")

        # 读取 skipped_molecules
        config_path = ROOT / "config.json"
        if config_path.exists():
            try:
                with open(config_path) as f:
                    cfg = json.load(f)
                skipped = cfg.get("skipped_molecules", [])
                if skipped:
                    self._skipped_molecules = set(skipped)
                    reasons = cfg.get("skip_reasons", {})
                    for name in skipped:
                        reason = reasons.get(name, "未指定")
                        print(f"[orchestrator] ⚠ 跳过的分子: {name}（原因: {reason}）")
            except (json.JSONDecodeError, OSError):
                pass

        steps = self._build_steps(run_dir)
        accumulated_artifacts: dict[str, list[str]] = {}

        self._sm.transition(State.RUNNING)

        for i, (label, func, dep_module, is_batch, layer_index) in enumerate(steps, 1):
            # ── 断点续跑：跳过已完成步骤 ──
            if i <= self._resume_from:
                print(f"\n  {i}/7 {label}  ⏭ 已完成，跳过")
                continue

            if dep_module:
                try:
                    ensure(dep_module)
                except RuntimeError as e:
                    self._sm.set_error(str(e), "dependency_missing")
                    self._sm.transition(State.ABORTED)
                    return False

            self._sm.set_step(i, label, _STEP_LAYER.get(i, ""))

            print(f"\n{'='*60}\n  {i}/7 {label}\n{'='*60}")

            result = func()

            if is_batch:
                ok = self._handle_batch_result(result, label, run_dir, accumulated_artifacts, layer_index, i)
            else:
                ok = self._handle_single_result(result, label, run_dir, accumulated_artifacts, layer_index, i)

            if ok:
                self._sm.mark_done(i)
            else:
                return False

        self._sm.transition(State.DONE)
        print(f"\n{'='*60}\n  ✅ 全流程完成\n  产物: {run_dir}/\n{'='*60}")
        return True

    # ============================================================
    # 结果处理
    # ============================================================

    def _handle_batch_result(self, results: list[StepResult], label: str,
                              run_dir: Path, artifacts: dict, layer_index: int,
                              step_i: int) -> bool:
        ok = sum(1 for r in results if r.success)
        failed = [r for r in results if not r.success]

        for r in results:
            if r.success:
                for k, v in r.outputs.items():
                    artifacts.setdefault(k, []).append(v)

        if not failed:
            print(f"[{label}] ✅ {ok}/{len(results)} 全部成功")
            return True

        # 过滤 skipped_molecules：将其从失败列表中移除
        if self._skipped_molecules:
            real_failed = []
            for r in failed:
                # 尝试从 error message / outputs 中匹配分子名
                is_skipped = False
                for mol_name in self._skipped_molecules:
                    err_msg = r.error.message if r.error else ""
                    if mol_name in err_msg or mol_name in str(r.outputs):
                        is_skipped = True
                        print(f"[{label}] ⏭ 跳过 {mol_name}（在 skip 列表中）")
                        # 将跳过的分子产物仍收集起来（如果有部分输出的话）
                        for k, v in r.outputs.items():
                            artifacts.setdefault(k, []).append(v)
                        break
                if not is_skipped:
                    real_failed.append(r)
            if not real_failed:
                print(f"[{label}] ✅ {ok}/{len(results)} 成功（{len(failed) - len(real_failed)} 个已跳过）")
                return True
            failed = real_failed

        # 记录错误到状态机
        first_err = failed[0].error
        if first_err:
            self._sm.set_error(first_err.message[:200], first_err.kind.value)

        print(f"[{label}] ❌ {ok}/{len(results)} 成功, {len(failed)} 失败")
        for r in failed:
            err = r.error
            if err:
                print(f"  [{err.kind.value}] {err.message[:100]}")
                if err.hint: print(f"    → {err.hint}")

        if not self.use_llm or layer_index is None:
            self._sm.transition(State.ABORTED)
            return False

        return self._invoke_agent(failed[0], label, run_dir, artifacts, layer_index)

    def _handle_single_result(self, result: StepResult, label: str,
                               run_dir: Path, artifacts: dict, layer_index: int,
                               step_i: int) -> bool:
        if result.success:
            print(f"[{label}] ✅ 完成 ({result.duration_s:.1f}s)")
            for k, v in result.outputs.items():
                artifacts.setdefault(k, []).append(v)
            return True

        err = result.error
        if err:
            self._sm.set_error(err.message[:200], err.kind.value)
            print(f"[{label}] ❌ [{err.kind.value}] {err.message[:100]}")
            if err.hint: print(f"  → {err.hint}")
        else:
            self._sm.set_error("未知错误")
            print(f"[{label}] ❌ 失败（无详细错误信息）")

        if not self.use_llm or layer_index is None:
            self._sm.transition(State.ABORTED)
            return False

        return self._invoke_agent(result, label, run_dir, artifacts, layer_index)

    def _invoke_agent(self, step_result: StepResult, label: str,
                       run_dir: Path, artifacts: dict, layer_index: int) -> bool:
        agent = self._agents.get(layer_index)
        if agent is None:
            print(f"[{label}] ⚠ 无可用 Agent（层 {layer_index}），跳过自动修复")
            self._sm.transition(State.ABORTED)
            return False

        print(f"\n[{label}] 🤖 调用 {agent.name} Agent 进行自动修复...")

        # 进入 RETRYING
        self._sm.start_retry(agent.name, 0, agent.max_retries)

        flat_artifacts: dict[str, str] = {}
        for k, v in artifacts.items():
            if isinstance(v, list) and v:
                flat_artifacts[k] = v[-1]
            elif isinstance(v, str):
                flat_artifacts[k] = v

        repair_result = agent.handle_failure(
            step_result=step_result,
            config_path=str(ROOT / "config.json"),
            run_dir=str(run_dir),
            artifacts=flat_artifacts,
            state_machine=self._sm,
        )

        if repair_result.success:
            print(f"[{label}] ✅ Agent 修复成功")
            self._sm.transition(State.RUNNING, step=step_result.step_index)
            for k, v in repair_result.outputs.items():
                artifacts.setdefault(k, []).append(v)
            return True

        if repair_result.escalated:
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
            self._sm.transition(State.ABORTED)
            print(f"[{label}] ❌ Agent 修复失败")

        return False


def _g16_sp_and_mol2(config_path: str, on_progress) -> list:
    """Step 2 (G16): *_opt.fchk → *_opt.fchk + .mol2."""
    import json
    from willy.quantum.singlepoint_g16 import run as sp_run
    from willy.quantum.fchk_mol2 import convert as mol2_convert

    with open(config_path) as f:
        molecules = json.load(f).get("molecules", {})

    results = []
    total = len(molecules)
    for i, (name, cfg) in enumerate(molecules.items(), 1):
        if on_progress:
            on_progress(f"分子 {i}/{total}: {name}")
        fchk_path = str(ROOT / "struct" / f"{name}.fchk")
        if not Path(fchk_path).exists():
            results.append(StepResult(
                step_name="sp_mol2_g16", step_index=2, success=False,
                error=StepError(kind=ErrorKind.FILE_NOT_FOUND,
                                message=f"{name}.fchk 不存在，需先运行 Step 1 结构优化")))
            continue
        charge = cfg.get("charge", 0)
        spin = cfg.get("spin", 1)
        sr = sp_run(fchk_path, charge=charge, spin=spin)
        if sr.success:
            opt_fchk = sr.outputs["fchk"]
            sr2 = mol2_convert(opt_fchk)
            sr.outputs["mol2"] = sr2.outputs.get("mol2", "")
            sr.artifacts.extend(sr2.artifacts)
        results.append(sr)
    return results


def _orca_sp_and_mol2(config_path: str, on_progress) -> list:
    """Step 2 (ORCA): *_opt.molden → *_opt.fchk → .mol2."""
    import json
    from willy.quantum.singlepoint_orca import run as sp_run
    from willy.quantum.fchk_mol2 import convert as mol2_convert

    with open(config_path) as f:
        molecules = json.load(f).get("molecules", {})

    results = []
    total = len(molecules)
    for i, (name, cfg) in enumerate(molecules.items(), 1):
        if on_progress:
            on_progress(f"分子 {i}/{total}: {name}")
        molden_path = str(ROOT / "struct" / f"{name}.molden")
        if not Path(molden_path).exists():
            results.append(StepResult(
                step_name="sp_orca", step_index=2, success=False,
                error=StepError(kind=ErrorKind.FILE_NOT_FOUND,
                                message=f"{name}.molden 不存在，需先运行 Step 1 结构优化")))
            continue
        charge = cfg.get("charge", 0)
        spin = cfg.get("spin", 1)
        sr = sp_run(molden_path, charge=charge, spin=spin)
        if sr.success:
            opt_fchk = sr.outputs["fchk"]
            sr2 = mol2_convert(opt_fchk)
            sr.outputs["mol2"] = sr2.outputs.get("mol2", "")
            sr.artifacts.extend(sr2.artifacts)
        results.append(sr)
    return results



def run_pipeline(backend: str = "g16", use_llm: bool = True,
                 run_dir: Optional[Path] = None) -> bool:
    orch = PipelineOrchestrator(backend=backend, use_llm=use_llm)
    return orch.run(run_dir=run_dir)
