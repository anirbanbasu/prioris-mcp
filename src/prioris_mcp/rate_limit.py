"""Per-provider outbound request serialisation and adaptive rate-limit backoff.

See docs/requirement-specification/04-non-functional-requirements.md for the design this
implements.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)


class RateLimitedError(Exception):
    """Raised by a queued operation to signal an HTTP 429 (rate limit) response from the source."""


class ProviderUnavailableError(Exception):
    """Raised by a queued operation to signal a timeout, connection error, or 5xx from the source."""


class ProviderRequestQueue[T]:
    """Serialise outbound requests to a single source with spacing and 429 backoff.

    Requests are spaced at least `base_spacing_seconds`
    apart, so concurrent tool invocations against that provider never race the source's rate
    limit (see
    docs/requirement-specification/04-non-functional-requirements.md#rate-limiting-must-serialise-not-just-gate).

    On a `RateLimitedError`, retries the same call with a doubling wait starting at
    `base_spacing_seconds` (3s, 6s, 12s, 24s, 48s, ... for the documented 3-second base),
    bounded by `max_total_backoff_seconds` for that one call; once the doubling sequence
    would exceed the remaining budget, `RateLimitedError` propagates to the caller instead of
    retrying further (see
    docs/requirement-specification/04-non-functional-requirements.md#rate-limit-breaches-are-handled-inside-the-providers-queue-not-by-the-caller).
    This backoff state is local to a single `execute()` call - a later, unrelated call always
    starts its own sequence fresh from `base_spacing_seconds`.

    A `ProviderUnavailableError` is never retried - it propagates on the first occurrence (see
    docs/requirement-specification/04-non-functional-requirements.md#provider_unavailable-failures-are-not-retried).
    """

    def __init__(self, base_spacing_seconds: float, max_total_backoff_seconds: float) -> None:
        self._base_spacing_seconds = base_spacing_seconds
        self._max_total_backoff_seconds = max_total_backoff_seconds
        self._lock = asyncio.Lock()
        self._last_request_started_at: float | None = None

    async def execute(self, operation: Callable[[], Awaitable[T]]) -> T:
        """Run `operation` through the queue, applying spacing and 429 backoff."""
        async with self._lock:
            wait_seconds = self._base_spacing_seconds
            elapsed_backoff_seconds = 0.0
            while True:
                await self._wait_for_spacing()
                self._last_request_started_at = time.monotonic()
                try:
                    return await operation()
                except RateLimitedError:
                    if elapsed_backoff_seconds + wait_seconds > self._max_total_backoff_seconds:
                        logger.warning(
                            "Rate-limit backoff budget of %.1fs exhausted; giving up.",
                            self._max_total_backoff_seconds,
                        )
                        raise
                    logger.info("Rate limited; backing off for %.1fs before retrying.", wait_seconds)
                    await asyncio.sleep(wait_seconds)
                    elapsed_backoff_seconds += wait_seconds
                    wait_seconds *= 2

    async def _wait_for_spacing(self) -> None:
        if self._last_request_started_at is None:
            return
        remaining = self._base_spacing_seconds - (time.monotonic() - self._last_request_started_at)
        if remaining > 0:
            await asyncio.sleep(remaining)
