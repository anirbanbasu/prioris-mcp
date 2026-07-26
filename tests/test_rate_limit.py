import asyncio
import time
from itertools import pairwise

import pytest

from prioris_mcp.rate_limit import ProviderRequestQueue, ProviderUnavailableError, RateLimitedError


class TestProviderRequestQueueSpacing:
    """Dedicated test class for ProviderRequestQueue's request spacing behaviour."""

    def test_concurrent_calls_are_serialised_and_spaced(self):
        async def scenario():
            queue = ProviderRequestQueue(base_spacing_seconds=0.05, max_total_backoff_seconds=1.0)
            start_times: list[float] = []

            async def operation() -> str:
                start_times.append(time.monotonic())
                return "ok"

            await asyncio.gather(*(queue.execute(operation) for _ in range(3)))
            return start_times

        start_times = asyncio.run(scenario())
        assert len(start_times) == 3
        gaps = [b - a for a, b in pairwise(start_times)]
        assert all(gap >= 0.04 for gap in gaps), gaps

    def test_first_call_on_a_fresh_queue_does_not_wait(self):
        async def scenario():
            queue = ProviderRequestQueue(base_spacing_seconds=1.0, max_total_backoff_seconds=5.0)
            started = time.monotonic()

            async def operation() -> str:
                return "ok"

            await queue.execute(operation)
            return time.monotonic() - started

        assert asyncio.run(scenario()) < 0.5


class TestProviderRequestQueueBackoff:
    """Dedicated test class for ProviderRequestQueue's rate-limit backoff behaviour."""

    def test_retries_on_rate_limited_then_succeeds(self):
        async def scenario():
            queue = ProviderRequestQueue(base_spacing_seconds=0.02, max_total_backoff_seconds=1.0)
            attempts = 0

            async def operation() -> str:
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise RateLimitedError
                return "ok"

            result = await queue.execute(operation)
            return result, attempts

        result, attempts = asyncio.run(scenario())
        assert result == "ok"
        assert attempts == 2

    def test_gives_up_once_backoff_budget_is_exhausted(self):
        async def scenario():
            queue = ProviderRequestQueue(base_spacing_seconds=0.05, max_total_backoff_seconds=0.3)
            attempts = 0

            async def operation() -> str:
                nonlocal attempts
                attempts += 1
                raise RateLimitedError

            with pytest.raises(RateLimitedError):
                await queue.execute(operation)
            return attempts

        # waits: 0.05 (elapsed 0.05), 0.1 (elapsed 0.15), then 0.15+0.2=0.35 > 0.3 -> give up.
        # Three attempts are made in total (the third's failure is what triggers giving up).
        assert asyncio.run(scenario()) == 3

    def test_provider_unavailable_is_not_retried(self):
        async def scenario():
            queue = ProviderRequestQueue(base_spacing_seconds=0.05, max_total_backoff_seconds=1.0)
            attempts = 0

            async def operation() -> str:
                nonlocal attempts
                attempts += 1
                raise ProviderUnavailableError

            with pytest.raises(ProviderUnavailableError):
                await queue.execute(operation)
            return attempts

        assert asyncio.run(scenario()) == 1

    def test_a_later_call_still_starts_its_own_backoff_from_the_base_spacing(self):
        # Per this plan's "Backoff sequence scope" design decision: a call that follows one
        # which was rate-limited does not inherit an elevated wait - it starts fresh.
        #
        # A count-only assertion (both calls take exactly 2 attempts) would not catch a
        # regression where backoff state was incorrectly persisted as instance state instead
        # of being local to each `execute()` call: both calls would still succeed on their
        # 2nd attempt, but the second call's actual wait before that 2nd attempt would be
        # elevated (a further-doubled value) rather than reset to `base_spacing_seconds`. So
        # this test measures the actual wait duration between each call's failing 1st attempt
        # and its succeeding 2nd attempt, for both calls independently.
        base_spacing_seconds = 0.1

        async def scenario():
            queue = ProviderRequestQueue(base_spacing_seconds=base_spacing_seconds, max_total_backoff_seconds=5.0)
            attempts_by_call: list[int] = []
            retry_wait_seconds_by_call: list[float] = []

            async def make_operation():
                attempts = 0
                failed_at = 0.0

                async def operation() -> str:
                    nonlocal attempts, failed_at
                    attempts += 1
                    if attempts == 1:
                        failed_at = time.monotonic()
                        raise RateLimitedError
                    attempts_by_call.append(attempts)
                    retry_wait_seconds_by_call.append(time.monotonic() - failed_at)
                    return "ok"

                return operation

            await queue.execute(await make_operation())
            await queue.execute(await make_operation())
            return attempts_by_call, retry_wait_seconds_by_call

        attempts_by_call, retry_wait_seconds_by_call = asyncio.run(scenario())
        # Both calls take exactly 2 attempts (fail once, then succeed).
        assert attempts_by_call == [2, 2]
        # And, crucially, each call's single retry wait is close to base_spacing_seconds on
        # its own - not a multiple of it, which is what a persisted/elevated backoff state
        # would produce for the second call (two elevated-but-equal waits would still pass a
        # "both gaps are similar" check, so each is compared against the base independently).
        for retry_wait_seconds in retry_wait_seconds_by_call:
            assert base_spacing_seconds <= retry_wait_seconds < base_spacing_seconds * 1.75, retry_wait_seconds_by_call
