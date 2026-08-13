"""Tracks in-flight background embedding tasks - 'building' status is derived from this, never persisted.

See docs/requirement-specification/search/02-vector-search.md#index-status-is-per-documentnote-derived-by-comparing-recorded-vs-configured-model.
A process restart mid-embedding simply loses the in-flight task; the next status check honestly
reports whatever the record was before (not_built/stale), which is self-healing by construction.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)


class EmbeddingScheduler:
    """Fire-and-forget async task tracking, keyed by an opaque tuple (e.g. (provider, id, format))."""

    def __init__(self) -> None:
        self._tasks: dict[tuple, asyncio.Task] = {}

    def is_building(self, key: tuple) -> bool:
        """Whether a live background embedding task currently exists for `key`."""
        return key in self._tasks

    def schedule(self, key: tuple, coro_factory: Callable[[], Awaitable[None]]) -> None:
        """Schedule `coro_factory()` to run in the background, unless `key` is already in flight."""
        if key in self._tasks:
            return
        task = asyncio.ensure_future(self._run(key, coro_factory))
        self._tasks[key] = task

    async def _run(self, key: tuple, coro_factory: Callable[[], Awaitable[None]]) -> None:
        try:
            await coro_factory()
        except Exception:
            logger.exception("Background embedding task failed for key=%r", key)
        finally:
            self._tasks.pop(key, None)

    async def wait_all(self) -> None:
        """Wait for every currently-tracked task to finish. Test support, not used in production."""
        pending = list(self._tasks.values())
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
