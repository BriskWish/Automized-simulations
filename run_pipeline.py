"""CLI entrypoint for one lock-bound Willy pipeline run."""

from __future__ import annotations

import sys
import os
import signal
from pathlib import Path

from willy._paths import get_project_root
from willy.pipeline_launch import (
    PipelineLaunchError,
    PipelineLockConflict,
    adopt_pipeline_launch,
    reserve_pipeline_launch,
    write_startup_audit,
)


ROOT = get_project_root()


def _parse_args(argv: list[str]) -> tuple[bool, str, Path | None, int | None, str | None, str | None]:
    no_llm = False
    backend = "g16"
    run_dir: Path | None = None
    lock_fd: int | None = None
    launch_token: str | None = None
    pending_action_id: str | None = None
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg == "--no-llm":
            no_llm = True
        elif arg in ("g16", "orca"):
            backend = arg
        elif arg in {"--run-dir", "--lock-fd", "--launch-token", "--resume-pending-action"}:
            if index + 1 >= len(argv):
                raise ValueError(f"{arg} 缺少参数")
            value = argv[index + 1]
            if arg == "--run-dir":
                run_dir = Path(value)
            elif arg == "--lock-fd":
                lock_fd = int(value)
            elif arg == "--resume-pending-action":
                pending_action_id = value
            else:
                launch_token = value
            index += 1
        elif arg.startswith("--"):
            raise ValueError(f"未知参数: {arg}")
        else:
            raise ValueError(f"未知参数: {arg}")
        index += 1
    if (lock_fd is None) != (run_dir is None) or (lock_fd is None) != (launch_token is None):
        raise ValueError("--run-dir、--lock-fd 和 --launch-token 必须同时使用")
    if pending_action_id is not None and (
        run_dir is None or lock_fd is None or launch_token is None
    ):
        raise ValueError("确认重跑必须使用受管启动锁")
    return no_llm, backend, run_dir, lock_fd, launch_token, pending_action_id


def main(argv: list[str] | None = None) -> int:
    try:
        no_llm, backend, run_dir, lock_fd, launch_token, pending_action_id = _parse_args(argv or sys.argv[1:])
    except (TypeError, ValueError) as exc:
        print(f"启动失败: {exc}")
        return 1

    reservation = None
    try:
        if lock_fd is None:
            reservation = reserve_pipeline_launch(ROOT)
            run_dir = reservation.run_dir
            reservation.mark_runner_started(os.getpid())
        else:
            reservation = adopt_pipeline_launch(ROOT, run_dir, lock_fd, launch_token)
    except PipelineLockConflict:
        write_startup_audit(ROOT, "lock_conflict")
        print("已有任务运行")
        return 1
    except PipelineLaunchError:
        write_startup_audit(ROOT, "failed")
        print("启动失败")
        return 1

    success = False
    orchestrator = None

    def _abort_on_signal(_signum, _frame) -> None:
        raise KeyboardInterrupt

    previous_handlers = {
        signal.SIGINT: signal.signal(signal.SIGINT, _abort_on_signal),
        signal.SIGTERM: signal.signal(signal.SIGTERM, _abort_on_signal),
    }
    try:
        from willy.pipeline_orchestrator import PipelineOrchestrator

        orchestrator = PipelineOrchestrator(
            backend=backend,
            use_llm=not no_llm,
            confirmed_action_id=pending_action_id or "",
        )
        success = orchestrator.run(run_dir=run_dir)
        return 0 if success else 1
    except KeyboardInterrupt:
        if orchestrator is not None:
            orchestrator.abort_from_signal()
        return 130
    finally:
        for sig, handler in previous_handlers.items():
            signal.signal(sig, handler)
        if not success and (orchestrator is None or orchestrator._run_dir is None):
            write_startup_audit(ROOT, "failed")
        reservation.release()


if __name__ == "__main__":
    sys.exit(main())
