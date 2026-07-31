"""Transient-failure handling.

The retry logic is the one part of the error path that is easy to get subtly
wrong in a way nothing notices: retrying a permanent error just makes it slow,
and not retrying a quota error loses a turn that would have worked. Both
directions are pinned here.

No test sleeps - `call_with_retry` takes its sleep function as an argument
precisely so the suite stays fast.
"""

from __future__ import annotations

import pytest

from supplychain.llm import LLMNotConfiguredError
from supplychain.resilience import (
    MAX_BACKOFF_SECONDS,
    call_with_retry,
    describe_failure,
    is_transient,
    suggested_delay,
)


class Recorder:
    """Stands in for time.sleep and remembers what it was asked to wait."""

    def __init__(self) -> None:
        self.waits: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.waits.append(seconds)


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "message",
    [
        "429 RESOURCE_EXHAUSTED: quota exceeded for gemini-3.1-flash-lite",
        "503 UNAVAILABLE: The service is currently unavailable",
        "504 DEADLINE_EXCEEDED",
        "500 INTERNAL error, please try again",
        "Rate limit reached for this project",
    ],
)
def test_transient_failures_are_worth_retrying(message):
    assert is_transient(RuntimeError(message))


@pytest.mark.parametrize(
    "message",
    [
        "400 INVALID_ARGUMENT: request contains an invalid field",
        "403 PERMISSION_DENIED",
        "401 UNAUTHENTICATED: API_KEY_INVALID",
        "404 NOT_FOUND: model does not exist",
    ],
)
def test_permanent_failures_are_not_retried(message):
    assert not is_transient(RuntimeError(message))


def test_a_missing_key_is_never_transient():
    """Otherwise every no-key test in this suite would sit through a back-off."""
    assert not is_transient(LLMNotConfiguredError("no key"))


def test_an_unrecognised_error_is_treated_as_permanent():
    """Retry only what is positively identified as worth retrying."""
    assert not is_transient(ValueError("something else entirely"))


def test_classification_by_exception_type_not_just_message():
    class ResourceExhausted(Exception):
        pass

    assert is_transient(ResourceExhausted("no detail in the message"))


def test_a_permanent_marker_beats_a_transient_one():
    """"400 INVALID_ARGUMENT" contains "400"; it is still not retryable."""
    assert not is_transient(RuntimeError("400 INVALID_ARGUMENT (not a 429)"))


# ---------------------------------------------------------------------------
# Back-off
# ---------------------------------------------------------------------------


def test_the_servers_suggested_delay_wins():
    exc = RuntimeError("429 RESOURCE_EXHAUSTED. Please retry in 27.4s")
    # Plus a second of headroom - the quota window is wall-clock.
    assert suggested_delay(exc, attempt=0, base=5.0) == pytest.approx(28.4)


def test_a_retry_delay_field_is_understood():
    exc = RuntimeError("quota exceeded, retryDelay: 30s")
    assert suggested_delay(exc, attempt=3, base=5.0) == pytest.approx(31.0)


def test_back_off_is_exponential_without_advice():
    exc = RuntimeError("503 UNAVAILABLE")
    delays = [suggested_delay(exc, attempt=i, base=2.0) for i in range(4)]
    assert delays == [2.0, 4.0, 8.0, 16.0]


def test_back_off_is_capped():
    exc = RuntimeError("503 UNAVAILABLE")
    assert suggested_delay(exc, attempt=20, base=5.0) == MAX_BACKOFF_SECONDS
    assert suggested_delay(RuntimeError("retry in 9999s"), 0, 5.0) == MAX_BACKOFF_SECONDS


# ---------------------------------------------------------------------------
# The retry loop
# ---------------------------------------------------------------------------


def test_a_working_call_is_not_retried():
    sleep = Recorder()
    calls = []

    result = call_with_retry(lambda: calls.append(1) or "ok", sleep=sleep)

    assert result == "ok"
    assert len(calls) == 1
    assert sleep.waits == []


def test_a_quota_error_is_waited_out():
    sleep = Recorder()
    attempts = []

    def flaky():
        attempts.append(1)
        if len(attempts) < 3:
            raise RuntimeError("429 RESOURCE_EXHAUSTED")
        return "recovered"

    result = call_with_retry(flaky, retries=3, base_delay=1.0, sleep=sleep)

    assert result == "recovered"
    assert len(attempts) == 3
    assert sleep.waits == [1.0, 2.0]


def test_it_gives_up_and_re_raises_the_last_error():
    sleep = Recorder()

    def always_throttled():
        raise RuntimeError("429 RESOURCE_EXHAUSTED")

    with pytest.raises(RuntimeError, match="RESOURCE_EXHAUSTED"):
        call_with_retry(always_throttled, retries=2, base_delay=1.0, sleep=sleep)

    assert len(sleep.waits) == 2  # two waits, three attempts


def test_a_permanent_error_fails_on_the_first_attempt():
    sleep = Recorder()
    attempts = []

    def bad_request():
        attempts.append(1)
        raise RuntimeError("400 INVALID_ARGUMENT")

    with pytest.raises(RuntimeError):
        call_with_retry(bad_request, retries=5, base_delay=1.0, sleep=sleep)

    assert len(attempts) == 1
    assert sleep.waits == []


def test_retries_can_be_switched_off():
    sleep = Recorder()
    attempts = []

    def throttled():
        attempts.append(1)
        raise RuntimeError("429 RESOURCE_EXHAUSTED")

    with pytest.raises(RuntimeError):
        call_with_retry(throttled, retries=0, sleep=sleep)

    assert len(attempts) == 1
    assert sleep.waits == []


def test_the_retry_callback_reports_what_is_happening():
    seen = []
    call_count = []

    def flaky():
        call_count.append(1)
        if len(call_count) < 2:
            raise RuntimeError("503 UNAVAILABLE")
        return "ok"

    call_with_retry(
        flaky,
        label="shipment agent",
        retries=2,
        base_delay=3.0,
        sleep=Recorder(),
        on_retry=lambda label, attempt, delay, exc: seen.append((label, attempt, delay)),
    )

    assert seen == [("shipment agent", 1, 3.0)]


def test_retry_defaults_come_from_settings(monkeypatch):
    from supplychain import config, resilience

    monkeypatch.setenv("SUPPLYCHAIN_LLM_MAX_RETRIES", "1")
    monkeypatch.setenv("SUPPLYCHAIN_LLM_RETRY_BASE_DELAY", "7")
    config.reload_settings()
    try:
        sleep = Recorder()
        with pytest.raises(RuntimeError):
            resilience.call_with_retry(
                lambda: (_ for _ in ()).throw(RuntimeError("429")), sleep=sleep
            )
        assert sleep.waits == [7.0]
    finally:
        config.reload_settings()


# ---------------------------------------------------------------------------
# Operator-facing wording
# ---------------------------------------------------------------------------


def test_failures_are_described_without_a_stack_trace():
    assert "rate limit" in describe_failure(RuntimeError("429 RESOURCE_EXHAUSTED"))
    assert "unavailable" in describe_failure(RuntimeError("503 UNAVAILABLE"))
    assert "not configured" in describe_failure(LLMNotConfiguredError("no key"))
    assert "ValueError" in describe_failure(ValueError("boom"))


def test_a_description_never_leaks_the_raw_message():
    """Quota errors carry the project id and model name; neither belongs in chat."""
    raw = "429 RESOURCE_EXHAUSTED: project 1234567890 quota for secret-model"
    described = describe_failure(RuntimeError(raw))
    assert "1234567890" not in described
    assert "secret-model" not in described
