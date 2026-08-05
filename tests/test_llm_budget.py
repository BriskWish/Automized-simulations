import pytest

from willy.llm_budget import LLMBudget, LLMBudgetExceeded, LLMCircuitOpen


def test_budget_limits_calls_and_reports_state():
    budget = LLMBudget(max_calls=1, max_elapsed_s=100, call_timeout_s=2, circuit_failure_limit=2)
    budget.before_call()
    with pytest.raises(LLMBudgetExceeded):
        budget.before_call()
    snapshot = budget.snapshot()
    assert snapshot["calls"] == 1
    assert snapshot["call_timeout_s"] == 2


def test_circuit_breaker_opens_after_consecutive_failures():
    budget = LLMBudget(max_calls=5, max_elapsed_s=100, circuit_failure_limit=2)
    budget.before_call(); budget.record_failure()
    budget.before_call(); budget.record_failure()
    with pytest.raises(LLMCircuitOpen):
        budget.before_call()
