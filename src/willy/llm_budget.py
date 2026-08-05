"""Run-local LLM call budget, timeout and circuit-breaker state."""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
from time import monotonic
import os


class LLMBudgetExceeded(RuntimeError):
    """The run has no remaining LLM budget."""


class LLMCircuitOpen(RuntimeError):
    """Repeated LLM failures opened the run circuit breaker."""


@dataclass
class LLMBudget:
    max_calls: int = 24
    max_elapsed_s: float = 1800.0
    call_timeout_s: float = 120.0
    circuit_failure_limit: int = 3
    started_at: float = field(default_factory=monotonic)
    calls: int = 0
    consecutive_failures: int = 0
    _lock: Lock = field(default_factory=Lock, repr=False)

    @classmethod
    def from_env(cls) -> "LLMBudget":
        def positive(name: str, default: float, integer: bool = False) -> float | int:
            raw = os.environ.get(name, "").strip()
            try:
                value = int(raw) if integer else float(raw)
            except ValueError:
                return int(default) if integer else default
            return value if value > 0 else int(default) if integer else default
        return cls(
            max_calls=int(positive("WILLY_LLM_MAX_CALLS", 24, True)),
            max_elapsed_s=float(positive("WILLY_LLM_MAX_SECONDS", 1800.0)),
            call_timeout_s=float(positive("WILLY_LLM_CALL_TIMEOUT", 120.0)),
            circuit_failure_limit=int(positive("WILLY_LLM_CIRCUIT_FAILURES", 3, True)),
        )

    def before_call(self) -> None:
        with self._lock:
            if self.consecutive_failures >= self.circuit_failure_limit:
                raise LLMCircuitOpen("LLM 连续失败，已触发运行级熔断")
            if self.calls >= self.max_calls:
                raise LLMBudgetExceeded("LLM 调用次数已达到本次运行上限")
            if monotonic() - self.started_at >= self.max_elapsed_s:
                raise LLMBudgetExceeded("LLM 总耗时已达到本次运行上限")
            self.calls += 1

    def record_success(self) -> None:
        with self._lock:
            self.consecutive_failures = 0

    def record_failure(self) -> None:
        with self._lock:
            self.consecutive_failures += 1

    def snapshot(self) -> dict[str, int | float | bool]:
        with self._lock:
            elapsed = monotonic() - self.started_at
            return {
                "calls": self.calls,
                "max_calls": self.max_calls,
                "elapsed_s": round(elapsed, 3),
                "max_elapsed_s": self.max_elapsed_s,
                "call_timeout_s": self.call_timeout_s,
                "consecutive_failures": self.consecutive_failures,
                "circuit_open": self.consecutive_failures >= self.circuit_failure_limit,
            }
