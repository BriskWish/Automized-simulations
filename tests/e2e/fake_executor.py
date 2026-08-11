"""Deterministic executor substitute used by the browser acceptance harness."""

from __future__ import annotations


class FakeExecutor:
    """Expose only the public UI contract; it never starts scientific tools."""

    def __init__(self) -> None:
        self.current_run = "project-alpha"
        self.confirmed = False
        self.stop_requested = False

    def snapshot(self) -> dict[str, object]:
        waiting = not self.confirmed
        state = "awaiting_confirmation" if waiting else ("stopping" if self.stop_requested else "running")
        action = None
        if waiting:
            action = {
                "action_id": "fake-eq-repair",
                "run_id": self.current_run,
                "status": "awaiting_confirmation",
                "state_revision": 1,
                "config_fingerprint": "f" * 64,
                "summary": "EQ 验收未通过，需要确认降低时间步长。",
                "restart_step": 9,
                "step_label": "平衡阶段",
                "adjustments": [{"name": "dt", "before": "0.002 ps", "after": "0.001 ps", "purpose": "提高稳定性"}],
            }
        summary = {
            "awaiting_confirmation": "### 工程状态\n\n等待确认 EQ 调整方案。",
            "running": "### 工程状态\n\n正在执行 EQ 阶段。",
            "stopping": "### 工程状态\n\n正在安全停止并等待 checkpoint。",
        }[state]
        return {
            "run_id": self.current_run,
            "summary": summary,
            "live_summary": summary,
            "pending_action": action,
            "error_event": (
                {
                    "event_id": f"{self.current_run}:error:1",
                    "content": "### 公开错误\n\nEQ 验收未通过，工程未自动修改。",
                }
                if waiting else None
            ),
            "status_event_id": f"{self.current_run}:status:{state}",
            "timeline_events": True,
        }

    def is_running(self) -> bool:
        return not self.stop_requested

    def control_state(self) -> str | None:
        return "stopping" if self.stop_requested else None

    def stop(self, clean: bool = False) -> str:
        del clean
        self.stop_requested = True
        return "已请求安全停止。"

    def confirm(self, action_id: str, run_id: str, **_kwargs) -> str:
        if action_id != "fake-eq-repair" or run_id != self.current_run:
            return "当前待确认方案已失效。"
        self.confirmed = True
        return "已确认，正在重跑。"

    def latest_run_id(self) -> str:
        return self.current_run

    def run_choices(self) -> list[str]:
        return ["project-alpha", "project-beta"]

    def file_choices(self, run_id: str | None) -> list[str]:
        if run_id not in self.run_choices():
            return []
        return [f"{run_id}.pdb"]

    def render_structure(self, filename: str | None, *, run_id: str | None = None) -> str:
        return f"<div data-e2e-run='{run_id or ''}'>结构：{filename or '无'}</div>"

    def render_legend(self, filename: str | None, *, run_id: str | None = None) -> str:
        return f"<div>图例：{run_id or ''} / {filename or '无'}</div>"

    def answer(self, _run_id: str | None, _message: str, _history=None) -> str:
        return "fake executor 已读取公开运行状态。"
