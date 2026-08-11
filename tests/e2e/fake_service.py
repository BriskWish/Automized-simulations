"""Launch the real Gradio UI with a fake executor in a disposable workspace."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import signal
import threading

from .fake_executor import FakeExecutor


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--port", required=True, type=int)
    args = parser.parse_args()
    args.root.mkdir(parents=True, exist_ok=True)
    os.environ["WILLY_ROOT"] = str(args.root.resolve())

    import app as ui

    fake = FakeExecutor()
    ui.frontend_api.get_run_panel_snapshot = fake.snapshot
    ui.frontend_api.latest_run_id = fake.latest_run_id
    ui.frontend_api.get_run_visualization_run_choices = fake.run_choices
    ui.frontend_api.get_run_visualization_file_choices = fake.file_choices
    ui.frontend_api.render_run_visualization_html = fake.render_structure
    ui.frontend_api.render_run_visualization_legend_html = fake.render_legend
    ui.frontend_api.confirm_pending_action = fake.confirm
    ui.stop_pipeline = fake.stop
    ui.is_pipeline_running = fake.is_running
    ui.get_latest_run_control_state = fake.control_state
    ui.chat_run_assistant = fake.answer

    ui.app.launch(
        server_name="127.0.0.1",
        server_port=args.port,
        prevent_thread_lock=True,
        quiet=True,
        show_error=True,
    )
    stopped = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_args: stopped.set())
    signal.signal(signal.SIGINT, lambda *_args: stopped.set())
    stopped.wait()
    ui.app.close()


if __name__ == "__main__":
    main()
