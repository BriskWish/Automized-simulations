"""
run_pipeline.py
===============
全流程串联执行。产物统一写入 md_run/md_*_xxx/ 目录。

用法:
  python3 run_pipeline.py              # g16 后端, LLM Agent 修复
  python3 run_pipeline.py orca         # ORCA 后端, LLM Agent 修复
  python3 run_pipeline.py --no-llm     # 纯 CLI 模式 (无 LLM Agent)
  python3 run_pipeline.py --no-llm orca
"""

import sys, os, atexit
from datetime import datetime, timezone

from willy._paths import get_project_root

ROOT = get_project_root()
LOCK_FILE = ROOT / ".pipeline.lock"


def _acquire_lock() -> bool:
    """获取流水线锁，防止并发运行。"""
    if LOCK_FILE.exists():
        try:
            content = LOCK_FILE.read_text().strip().split("\n")
            pid = int(content[0])
            os.kill(pid, 0)
            print(f"❌ 流水线已在运行 (PID {pid})")
            return False
        except (OSError, ProcessLookupError, ValueError):
            print("⚠️ 检测到上次异常退出，覆盖锁文件。")
    LOCK_FILE.write_text(f"{os.getpid()}\n{datetime.now(timezone.utc).isoformat()}")
    atexit.register(lambda: LOCK_FILE.unlink(missing_ok=True))
    return True


if __name__ == "__main__":
    # ── 解析参数 ──
    args = sys.argv[1:]
    no_llm = False
    backend = "g16"
    for a in args:
        if a == "--no-llm":
            no_llm = True
        elif a in ("g16", "orca"):
            backend = a
        elif a.startswith("--"):
            print(f"未知参数: {a}")
            print("用法: python3 run_pipeline.py [--no-llm] [g16|orca]")
            sys.exit(1)
        else:
            print(f"未知参数: {a}")
            print("用法: python3 run_pipeline.py [--no-llm] [g16|orca]")
            sys.exit(1)

    if not _acquire_lock():
        sys.exit(1)

    # ── 执行 ──
    from willy.pipeline_orchestrator import PipelineOrchestrator

    try:
        orch = PipelineOrchestrator(backend=backend, use_llm=not no_llm)
        success = orch.run()
    finally:
        LOCK_FILE.unlink(missing_ok=True)

    sys.exit(0 if success else 1)
