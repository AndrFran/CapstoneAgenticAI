"""Transient-failure handling for model calls.

Every agent in this graph is one network call away from a bad afternoon. The
Gemini free tier allows 15 requests a minute and a single multi-agent turn is
several, so ``429 RESOURCE_EXHAUSTED`` is not an edge case here - it is the
normal failure. Overloaded backends (``503``) and deadline overruns behave the
same way: wait, try again, and it works.

Two rules this module encodes:

**Retry only what a retry can fix.** A missing API key, a malformed request or
a permission error will fail identically on the third attempt, so retrying them
just makes the user wait three times as long for the same error. Everything is
non-retryable unless it is positively identified as transient.

**Honour the server's own advice.** Google returns a suggested delay with a
quota error. Sleeping for that long is both faster and politer than a blind
exponential back-off, which is used only when no advice is given.

Deliberately provider-agnostic: it matches on status names and codes rather
than importing Google's exception classes, so swapping the provider (see
``llm.MODEL_PROVIDER``) does not silently turn retries off.

Owner: Team Member 4 (cross-agent error handling).
"""

from __future__ import annotations

import re
import time
from typing import Any, Callable, TypeVar

from .config import get_settings
from .llm import LLMNotConfiguredError

T = TypeVar("T")

# Checked first: a retry cannot fix any of these, and pretending otherwise
# turns a clear error into a slow clear error.
PERMANENT_MARKERS = (
    "INVALID_ARGUMENT",
    "PERMISSION_DENIED",
    "UNAUTHENTICATED",
    "NOT_FOUND",
    "FAILED_PRECONDITION",
    "API_KEY_INVALID",
    "400",
    "401",
    "403",
    "404",
)

# Worth waiting for.
TRANSIENT_MARKERS = (
    "RESOURCE_EXHAUSTED",
    "UNAVAILABLE",
    "DEADLINE_EXCEEDED",
    "INTERNAL",
    "ABORTED",
    "QUOTA",
    "RATE LIMIT",
    "OVERLOADED",
    "429",
    "500",
    "502",
    "503",
    "504",
)

TRANSIENT_TYPES = (
    "ResourceExhausted",
    "ServiceUnavailable",
    "DeadlineExceeded",
    "InternalServerError",
    "TooManyRequests",
    "Aborted",
    "ConnectionError",
    "Timeout",
    "TimeoutError",
    "ReadTimeout",
)

# "Please retry in 27.4s", "retryDelay: 30s", "retry-after: 12"
_DELAY_PATTERNS = (
    re.compile(r"retry[ _-]?(?:in|after|delay)[\"']?[:\s]+([0-9.]+)", re.I),
    re.compile(r"([0-9.]+)s?\s*(?:seconds?)?\s*before retrying", re.I),
)

MAX_BACKOFF_SECONDS = 60.0


def is_transient(exc: BaseException) -> bool:
    """Whether waiting and trying again could plausibly succeed."""
    # An unconfigured key is the single most common failure in this project and
    # it is emphatically not transient - retrying it would make every test that
    # exercises the no-key path sleep.
    if isinstance(exc, LLMNotConfiguredError):
        return False

    name = type(exc).__name__
    if any(marker in name for marker in TRANSIENT_TYPES):
        return True

    text = str(exc).upper()
    if any(marker in text for marker in PERMANENT_MARKERS):
        return False
    return any(marker in text for marker in TRANSIENT_MARKERS)


def suggested_delay(exc: BaseException, attempt: int, base: float) -> float:
    """How long to wait: the server's advice if given, else exponential."""
    text = str(exc)
    for pattern in _DELAY_PATTERNS:
        match = pattern.search(text)
        if match:
            try:
                # A second of headroom: the quota window is wall-clock, and
                # coming back a hair early just burns another attempt.
                return min(MAX_BACKOFF_SECONDS, float(match.group(1)) + 1.0)
            except ValueError:
                break
    return min(MAX_BACKOFF_SECONDS, base * (2**attempt))


def call_with_retry(
    call: Callable[[], T],
    *,
    label: str = "model call",
    retries: int | None = None,
    base_delay: float | None = None,
    sleep: Callable[[float], Any] = time.sleep,
    on_retry: Callable[[str, int, float, BaseException], Any] | None = None,
) -> T:
    """Run ``call``, waiting out transient failures.

    Args:
        call: The zero-argument thing to run.
        label: What is being attempted, for the retry callback.
        retries: Extra attempts after the first. Defaults to settings.
        base_delay: Seconds for the first back-off. Defaults to settings.
        sleep: Injected so tests do not actually wait.
        on_retry: ``(label, attempt, delay, exc)`` before each wait.

    Raises:
        The last exception, once the attempts are used up or the failure is
        not transient.
    """
    settings = get_settings()
    attempts = (settings.llm_max_retries if retries is None else retries) + 1
    delay_base = settings.llm_retry_base_delay if base_delay is None else base_delay

    last: BaseException | None = None
    for attempt in range(max(1, attempts)):
        try:
            return call()
        except Exception as exc:  # noqa: BLE001 - re-raised below
            last = exc
            if not is_transient(exc) or attempt == attempts - 1:
                raise
            delay = suggested_delay(exc, attempt, delay_base)
            if on_retry:
                on_retry(label, attempt + 1, delay, exc)
            sleep(delay)

    raise last  # pragma: no cover - the loop either returns or raises


def describe_failure(exc: BaseException) -> str:
    """A short, operator-readable reason. Never leaks a stack trace."""
    if isinstance(exc, LLMNotConfiguredError):
        return "the language model is not configured"
    if is_transient(exc):
        text = str(exc).upper()
        if "RESOURCE_EXHAUSTED" in text or "429" in text or "QUOTA" in text:
            return "the model API rate limit was reached"
        return "the model API was temporarily unavailable"
    return f"an unexpected {type(exc).__name__}"
