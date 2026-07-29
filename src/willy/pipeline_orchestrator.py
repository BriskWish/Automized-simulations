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
    """流水线编排器 —— 执行 7 步流水线，失败时调用 LLM Agent。"""

    def __init__(self, backend: str = "g16", use_llm: bool = True):
        self.backend = backend
        self.use_llm = use_llm
        self.llm_client = None
        self._agents: dict[int, Optional[LayerAgent]] = {
            1: None, 2: None, 3: None,
        }
        self._sm = PipelineStateMachine(total_steps=7)

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
        from willy.prompts import QUANTUM_AGENT_PROMPT, TOPOLOGY_AGENT_PROMPT, SIMULATION_AGENT_PROMPT
        from willy.quantum_tools import QUANTUM_TOOLS, handle_quantum_tool_call
        from willy.topology_tools import TOPOLOGY_TOOLS, handle_topology_tool_call
        from willy.simulation_tools import SIMULATION_TOOLS, handle_simulation_tool_call

        self._agents[1] = LayerAgent(
            name="quantum", system_prompt=QUANTUM_AGENT_PROMPT,
            tools=QUANTUM_TOOLS, tool_handler=handle_quantum_tool_call,
            llm_client=self.llm_client, max_retries=5,
            on_action=lambda a: self._sm.add_action(a),
        )
        self._agents[2] = LayerAgent(
            name="topology", system_prompt=TOPOLOGY_AGENT_PROMPT,
            tools=TOPOLOGY_TOOLS, tool_handler=handle_topology_tool_call,
            llm_client=self.llm_client, max_retries=4,
            on_action=lambda a: self._sm.add_action(a),
        )
        self._agents[3] = LayerAgent(
            name="simulation", system_prompt=SIMULATION_AGENT_PROMPT,
            tools=SIMULATION_TOOLS, tool_handler=handle_simulation_tool_call,
            llm_client=self.llm_client, max_retries=3,
            on_action=lambda a: self._sm.add_action(a),
        )

    # ============================================================
    # 步骤构建
    # ============================================================

    def _build_steps(self, run_dir: Path) -> list[tuple]:
        from willy.quantum.g16_struct_maker import run_all as g16_struct
        from willy.quantum.g16_mol2_maker import batch_convert as g16_mol2
        from willy.quantum.g16_chg_maker import batch_make_chg as g16_chg
        from willy.quantum.orca_struct_maker import run_all as orca_struct
        from willy.quantum.orca_mol2_maker import batch_convert as orca_mol2
        from willy.quantum.orca_chg_maker import batch_make_chg as orca_chg
        from willy.topology.sobtop_interface import batch_make_topo
        from willy.topology.top_maker import build as build_top
        from willy.simulation.mdp_maker import build_all as build_mdp
        from willy.simulation.inp_generator import auto_from_config, InpGenerator

        def _run_box(rd: str) -> StepResult:
            config = auto_from_config(output_dir=rd, gro_dir=rd, pdb_dir=rd)
            gen = InpGenerator(config)
            return gen.run()

        idx = lambda i: i  # step index 1-7

        if self.backend == "orca":
            return [
                ("ORCA 结构优化",    lambda: orca_struct(), "struct_maker", True, 1),
                ("molden→mol2",      lambda: orca_mol2(),   None, True, 1),
                ("ORCA RESP 电荷",   lambda: orca_chg(),    "chg_maker", True, 1),
                ("mol2+chg→itp+gro", lambda: batch_make_topo(output_dir=str(run_dir)), "sobtop_interface", True, 2),
                ("主拓扑 + 修订 itp", lambda: build_top(topo_dir=str(run_dir)), None, False, 2),
                ("生成 mdp",          lambda: build_mdp(output_dir=str(run_dir)), None, False, 3),
                ("Packmol 盒子",      lambda: _run_box(str(run_dir)), "inp_generator", False, 3),
            ]
        return [
            ("g16 优化 + formchk", lambda: g16_struct(), "struct_maker", True, 1),
            ("fchk→mol2",           lambda: g16_mol2(), None, True, 1),
            ("RESP 电荷",           lambda: g16_chg(),   "chg_maker", True, 1),
            ("mol2+chg→itp+gro",    lambda: batch_make_topo(output_dir=str(run_dir)), "sobtop_interface", True, 2),
            ("主拓扑 + 修订 itp",    lambda: build_top(topo_dir=str(run_dir)), None, False, 2),
            ("生成 mdp",             lambda: build_mdp(output_dir=str(run_dir)), None, False, 3),
            ("Packmol 盒子",         lambda: _run_box(str(run_dir)), "inp_generator", False, 3),
        ]

    # ============================================================
    # 执行
    # ============================================================

    def run(self, run_dir: Optional[Path] = None) -> bool:
        if run_dir is None:
            from willy.simulation.md_setup import _next_run_dir
            run_dir = _next_run_dir()
            run_dir.mkdir(parents=True, exist_ok=True)

        print(f"[orchestrator] 运行目录: {run_dir}")
        print(f"[orchestrator] 后端: {self.backend}")
        print(f"[orchestrator] LLM Agent: {'启用' if self.use_llm else '禁用'}")

        steps = self._build_steps(run_dir)
        accumulated_artifacts: dict[str, list[str]] = {}

        self._sm.transition(State.RUNNING)

        for i, (label, func, dep_module, is_batch, layer_index) in enumerate(steps, 1):
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


def run_pipeline(backend: str = "g16", use_llm: bool = True,
                 run_dir: Optional[Path] = None) -> bool:
    orch = PipelineOrchestrator(backend=backend, use_llm=use_llm)
    return orch.run(run_dir=run_dir)
